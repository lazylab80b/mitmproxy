#!/usr/bin/env python3
from __future__ import annotations
import argparse
import re
import sys
from pathlib import Path
from typing import Dict, List, Tuple
from collections import defaultdict

# 表示順（test_gzip_variants.py の出力と対応）
CASE_ORDER = [
    "truncated(short)",
    "truncated(long)",
    "crc_flip",
    "isize_flip",
    "last_byte_missing",
]

def versplit(s: str) -> Tuple[int, ...]:
    try:
        return tuple(int(x) for x in re.findall(r"\d+", s)) or (0,)
    except Exception:
        return (0,)

def parse_file(path: str) -> Dict:
    p = Path(path)
    text = p.read_text(encoding="utf-8", errors="replace")

    # --- runtime line ---
    py = zr = zb = gz = "unknown"
    for line in text.splitlines():
        if line.startswith("## ENV "):
            parts = dict(kv.split("=", 1) for kv in line.split()[2:] if "=" in kv)
            py = parts.get("PY", py)
            zr = parts.get("ZLIB_RUNTIME", parts.get("ZLIB_RUNTIME_VERSION", zr))
            zb = parts.get("ZLIB_BUILD", parts.get("ZLIB_VERSION", zb))
            gz = parts.get("GZIP", gz)
            break

    verdicts: Dict[str, Dict[str, str]] = {"gzip": {}, "zlib": {}}

    # --- results block ---
    lines = text.splitlines()
    try:
        i0 = lines.index("### BEGIN RESULTS") + 1
        i1 = lines.index("### END RESULTS")
    except ValueError:
        # ブロックが無くても必ず verdicts を返す
        return {
            "runtime": {"python": py, "gzip": gz, "zlib_build": zb, "zlib_runtime": zr},
            "python": py,
            "zlib_runtime": zr,
            "verdicts": verdicts,
        }

    block = lines[i0:i1]
    header = next((ln for ln in block if ln.strip().startswith("|") and "case" in ln.lower()), None)
    if header:
        cols = [c.strip().lower() for c in header.strip().strip("|").split("|")]

        # 列名の別名を正規化（ログは stdlib.gzip/zlib.recover、集計は gzip/zlib）
        alias = {
            "stdlib.gzip": "gzip",
            "gzip stdlib": "gzip",
            "gzip": "gzip",
            "zlib.recover": "zlib",
            "zlib": "zlib",
        }
        col_idx: Dict[str, int] = {}
        for i, name in enumerate(cols):
            if name in alias:
                col_idx[alias[name]] = i

        def conv(cell: str) -> str:
            s = cell.strip()
            if not s:
                return ""
            if s.startswith("OK"):
                return "OK"
            if s.startswith("ERR(") and s.endswith(")"):
                return s[4:-1]  # ERR(Name) -> Name（例: EOFError, BadGzipFile）
            if s.startswith("ERR"):
                return "ERR"
            return s  # 例: -3, -5, "error" など

        for ln in block:
            if not (ln.strip().startswith("|") and not set(ln.strip()) <= {"|", "-", " "}):
                continue
            parts = [c.strip() for c in ln.strip().strip("|").split("|")]
            if len(parts) < 2 or parts[0].lower() in {"case", "python", "zlib(runtime)"}:
                continue
            case = parts[0]
            if "gzip" in col_idx and col_idx["gzip"] < len(parts):
                verdicts["gzip"][case] = conv(parts[col_idx["gzip"]])
            if "zlib" in col_idx and col_idx["zlib"] < len(parts):
                verdicts["zlib"][case] = conv(parts[col_idx["zlib"]])

    return {
        "runtime": {"python": py, "gzip": gz, "zlib_build": zb, "zlib_runtime": zr},
        "python": py,
        "zlib_runtime": zr,
        "verdicts": verdicts,
    }

def compute_widths(rows: List[List[str]]) -> List[int]:
    w = [0] * max(len(r) for r in rows)
    for r in rows:
        for i, c in enumerate(r):
            w[i] = max(w[i], len(c))
    return w

def fmt_row(cells: List[str], widths: List[int]) -> str:
    return "| " + " | ".join(c.ljust(widths[i]) for i, c in enumerate(cells)) + " |"

def fmt_sep(widths: List[int]) -> str:
    return "| " + " | ".join("-" * max(3, widths[i]) for i in range(len(widths))) + " |"

def render_runtime_table(runs: List[Dict]) -> str:
    seen = set()
    uniq = []
    for r in runs:
        rt = r["runtime"]
        key = (rt.get("python"), rt.get("zlib_runtime"))  # ← runtime から取る
        if key not in seen:
            uniq.append(r)
            seen.add(key)

    runs_sorted = sorted(
        uniq,
        key=lambda r: (
            versplit(r["runtime"]["python"]),
            versplit(r["runtime"]["zlib_runtime"]),
        ),
    )
    rows = [["Python", "gzip", "zlib(build)", "zlib(runtime)"]]
    for r in runs_sorted:
        rt = r["runtime"]
        rows.append([rt["python"], rt["gzip"], rt["zlib_build"], rt["zlib_runtime"]])
    widths = compute_widths(rows)
    out = ["## Runtime versions", fmt_row(rows[0], widths), fmt_sep(widths)]
    out.extend(fmt_row(r, widths) for r in rows[1:])
    return "\n".join(out)

def collect_columns(runs: List[Dict]) -> List[Tuple[str, str]]:
    """
    列IDとして (python, zlib_runtime) を採用。
    見出し1段目は python、2段目は zlib(runtime) を表示。
    """
    cols = {(r["runtime"]["python"], r["runtime"]["zlib_runtime"]) for r in runs}
    return sorted(cols, key=lambda x: (versplit(x[0]), versplit(x[1])))

def make_verdict_maps(runs: List[Dict]) -> Tuple[Dict[str, Dict[Tuple[str, str], str]],
                                                 Dict[str, Dict[Tuple[str, str], str]]]:
    """
    各ケース×各( python, zlib_runtime ) の判定表を作る。
    return: (verdict_stdlib, verdict_zlib)
            どちらも {case: {(py, zr): "OK" or 理由}} の形。
    """
    verdict_stdlib: Dict[str, Dict[Tuple[str, str], str]] = defaultdict(dict)
    verdict_zlib: Dict[str, Dict[Tuple[str, str], str]] = defaultdict(dict)

    for r in runs:
        py = r["runtime"]["python"]
        zr = r["runtime"]["zlib_runtime"]
        key = (py, zr)
        cases = r.get("cases", {})
        for case in CASE_ORDER:
            std = cases.get("stdlib", {}).get(case)
            zl  = cases.get("zlib", {}).get(case)
            if std is not None:
                verdict_stdlib[case][key] = std
            if zl is not None:
                verdict_zlib[case][key] = zl
    return verdict_stdlib, verdict_zlib

def _collect_col_pairs(runs: List[Dict[str, dict]]) -> list[tuple[str, str]]:
    """ユニークな (python, zlib_runtime) の並びを昇順で返す。"""
    pairs = {(r["runtime"]["python"], r["runtime"]["zlib_runtime"]) for r in runs}
    return sorted(pairs, key=lambda t: (versplit(t[0]), versplit(t[1])))

def _build_verdict_index(runs: List[Dict], which: str) -> dict[str, dict[str, str]]:
    """
    which='gzip' | 'zlib'
    case -> "py / zr" -> verdict に引けるインデックスを作る。
    """
    out: dict[str, dict[str, str]] = defaultdict(dict)
    for r in runs:
        py = r["runtime"]["python"]
        zr = r["runtime"]["zlib_runtime"]
        col = f"{py} / {zr}"
        for case, v in r["verdicts"][which].items():
            out[case][col] = v
    return out

def render_verdict_table(
    title: str,
    verdicts: Dict[str, Dict[str, str]],
    runs: List[Dict],
    md_mode: bool = False,
) -> str:
    """
    列キー（データ側）は常に "py / zr" で統一し、ヘッダ表示だけ md_mode で
    1行( python/zlib ) / 2行( python / zlib(runtime) ) を出し分ける。
    """
    pairs = _collect_col_pairs(runs)
    col_keys = [f"{py} / {zr}" for (py, zr) in pairs]

    # まず全行を1つの rows に積む（幅計算を正しくするため）
    rows: list[list[str]] = []
    if md_mode:
        rows.append(["python/zlib"] + col_keys)
    else:
        rows.append(["python"] + [py for (py, zr) in pairs])
        rows.append(["zlib(runtime)"] + [zr for (py, zr) in pairs])
    rows.append(["------"] + ["------"] * len(col_keys))
    for case in CASE_ORDER:
        rows.append([case] + [verdicts.get(case, {}).get(k, "") for k in col_keys])

    widths = compute_widths(rows)
    out = [title, fmt_row(rows[0], widths)]
    if not md_mode:
        out.append(fmt_row(rows[1], widths))
    out.append(fmt_sep(widths))
    start = 2 if md_mode else 3
    out.extend(fmt_row(r, widths) for r in rows[start:])
    return "\n".join(out)

# ---------- “環境差”の検出（新） ----------

def render_env_variation(runs: List[Dict]) -> str:
    """
    各ケースごとに、環境（python,zlib_runtime）で結果がブレるかを検出。
    ・gzip と zlib を別々に評価
    ・全環境で同一なら 'none'
    ・異なる値が混ざっていれば差分のみを列挙
    """
    cols = collect_columns(runs)
    verdict_stdlib, verdict_zlib = make_verdict_maps(runs)

    lines: List[str] = []
    lines.append("## Environment variation (per case; differences only)")
    for case in CASE_ORDER:
        # stdlib 側
        std_map = verdict_stdlib.get(case, {})
        std_values = {std_map.get(k, "") for k in cols if k in std_map}
        # zlib 側
        zl_map = verdict_zlib.get(case, {})
        zl_values  = {zl_map.get(k, "") for k in cols if k in zl_map}

        # すべて埋まっていない列は無視（安全側）
        out_parts: List[str] = []

        def summarize_variation(tag: str, vmap: Dict[Tuple[str,str], str], vset: set[str]) -> None:
            if len([v for v in vset if v != ""]) <= 1:
                return  # 変動なし
            # 基準は最頻値（同数なら最初の値）
            nonempty = [vmap.get(k, "") for k in cols if vmap.get(k, "") != ""]
            baseline = max(set(nonempty), key=lambda v: nonempty.count(v)) if nonempty else ""
            diffs = []
            for (py, zr) in cols:
                v = vmap.get((py, zr), "")
                if v and v != baseline:
                    diffs.append(f"{py}(zr={zr})->{v}")
            if diffs:
                out_parts.append(f"{tag} varies: " + ", ".join(diffs))

        summarize_variation("gzip", std_map, std_values)
        summarize_variation("zlib", zl_map, zl_values)

        if out_parts:
            lines.append(f"- {case}: " + " | ".join(out_parts))
        else:
            lines.append(f"- {case}: none")
    return "\n".join(lines)

# ---------- main ----------

def main():
    ap = argparse.ArgumentParser(description="Summarize gzip/zlib behavior across runtimes.")
    ap.add_argument("paths", nargs="+", help="log files like logs/out_*.txt")
    ap.add_argument("--md", action="store_true", help="also write summary.md")
    ap.add_argument("--stdout-only", action="store_true", help="print to stdout only (no file)")
    args = ap.parse_args()

    paths = [Path(p) for p in args.paths]
    runs = [parse_file(p) for p in paths]
    md_mode = bool(args.md)
    verdict_stdlib = _build_verdict_index(runs, "gzip")
    verdict_zlib = _build_verdict_index(runs, "zlib")
    parts = [
        render_runtime_table(runs),
        "",
        render_verdict_table("## gzip (OK or error reason by Python)", verdict_stdlib, runs, md_mode=md_mode),
        "",
        render_verdict_table("## zlib (OK or error reason by Python)", verdict_zlib, runs, md_mode=md_mode),
        "",
        "**Legend (zlib error codes)**",
        "- `-3`: Z_DATA_ERROR (corrupt data / invalid checksum/length)",
        "- `-5`: Z_BUF_ERROR (incomplete stream / needs more input)",
        "",
        render_env_variation(runs),
    ]
    out = "\n".join(parts)
    print(out)

    if args.md and not args.stdout_only:
        Path("summary.md").write_text(out + "\n", encoding="utf-8")
        print("[summarize_results] wrote: summary.md", file=sys.stderr)

if __name__ == "__main__":
    main()