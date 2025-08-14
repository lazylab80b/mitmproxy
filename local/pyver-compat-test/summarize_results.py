##### summarize_results.py (simplified)

from __future__ import annotations
import argparse
import re
import sys
from pathlib import Path
from typing import Dict, List, Tuple
from collections import defaultdict

# 表示順
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

def parse_file(p: Path) -> Dict:
    """
    Parse one log file produced by test_gzip_variants.py
    Expected header (ENV line only):
      ## ENV PY=3.13.6 GZIP=stdlib ZLIB_BUILD=1.2.13 ZLIB_RUNTIME=1.2.13 PLATFORM=...
    And results block between:
      ### BEGIN RESULTS ... ### END RESULTS
    """
    text = p.read_text(encoding="utf-8", errors="replace")

    # ランタイム情報（ENV 行のみ対応）
    runtime = {
        "python": "unknown",
        "gzip": "unknown",
        "zlib_build": "unknown",
        "zlib_runtime": "unknown",
    }
    m = re.search(
        r"^##\s*ENV\s+PY=([\d.]+)\s+GZIP=(\w+)\s+ZLIB_BUILD=([\d.]+)\s+ZLIB_RUNTIME=([\d.]+)",
        text, re.MULTILINE,
    )
    if m:
        runtime["python"], runtime["gzip"], runtime["zlib_build"], runtime["zlib_runtime"] = m.groups()

    def normalize(cell: str) -> str:
        cell = cell.strip()
        if cell.startswith("OK("):
            return "OK"
        if cell.startswith("ERR(") and cell.endswith(")"):
            inner = cell[4:-1].strip()
            return inner or "error"
        return "OK" if cell == "OK" else cell

    # テーブル抽出
    cases_stdlib: Dict[str, str] = {}
    cases_zlib: Dict[str, str] = {}
    in_table = False
    for line in text.splitlines():
        s = line.strip()
        if s.startswith("### BEGIN RESULTS"):
            in_table = True
            continue
        if s.startswith("### END RESULTS"):
            in_table = False
            continue
        if not in_table or not s.startswith("|"):
            continue

        cells = [c.strip() for c in s.strip("|").split("|")]
        # ヘッダ/セパレータ行スキップ
        if not cells or cells[0] in {"case", "---"} or set(cells[0]) == {"-"}:
            continue

        # 期待列: case | in | expect | stdlib.gzip | zlib.recover
        if len(cells) >= 5:
            case = cells[0]
            stdlib_cell = cells[3]
            zlib_cell = cells[4]
            if case in CASE_ORDER:
                cases_stdlib[case] = normalize(stdlib_cell)
                cases_zlib[case] = normalize(zlib_cell)

    return {
        "runtime": runtime,
        "cases": {"stdlib": cases_stdlib, "zlib": cases_zlib},
        "file": str(p),
    }

def compute_widths(rows: List[List[str]]) -> List[int]:
    w = [0] * max(len(r) for r in rows)
    for r in rows:
        for i, c in enumerate(r):
            if len(c) > w[i]:
                w[i] = len(c)
    return w

def fmt_row(cells: List[str], widths: List[int]) -> str:
    return "| " + " | ".join(c.ljust(widths[i]) for i, c in enumerate(cells)) + " |"

def fmt_sep(widths: List[int]) -> str:
    return "| " + " | ".join("-" * max(3, widths[i]) for i in range(len(widths))) + " |"

def render_runtime_table(runs: List[Dict]) -> str:
    runs_sorted = sorted(
        runs,
        key=lambda r: (versplit(r["runtime"]["python"]), versplit(r["runtime"]["zlib_runtime"]))
    )
    rows = [["Python", "gzip", "zlib(build)", "zlib(runtime)"]]
    for r in runs_sorted:
        rt = r["runtime"]
        rows.append([rt["python"], rt["gzip"], rt["zlib_build"], rt["zlib_runtime"]])
    widths = compute_widths(rows)
    out = ["== Runtime versions ==", fmt_row(rows[0], widths), fmt_sep(widths)]
    out.extend(fmt_row(r, widths) for r in rows[1:])
    return "\n".join(out)

def collect_columns(runs: List[Dict]) -> List[Tuple[str, str]]:
    cols = {(r["runtime"]["python"], r["runtime"]["zlib_runtime"]) for r in runs}
    return sorted(cols, key=lambda x: (versplit(x[0]), versplit(x[1])))

def _normalize_verdict(v: str | None) -> str | None:
    """
    ログ中のセル表記を「OK」or「理由文字列」に正規化する。
    例: "OK(12/12)" -> "OK", "ERR(EOFError)" -> "EOFError", "error" -> "error"
    """
    if v is None:
        return None
    s = str(v).strip()
    if s.startswith("OK"):
        return "OK"
    if s.startswith("ERR(") and s.endswith(")"):
        return s[4:-1]
    return s  # "error" などはそのまま

def make_verdict_maps(runs: List[Dict]) -> Tuple[Dict[str, Dict[str, str]], Dict[str, Dict[str, str]]]:
    """
    各ケース×各Python版の判定表を作る。
    return: (verdict_stdlib, verdict_zlib)
            どちらも {case: {python_version: "OK" or 理由}} の形。
    """
    verdict_stdlib: Dict[str, Dict[str, str]] = defaultdict(dict)
    verdict_zlib: Dict[str, Dict[str, str]] = defaultdict(dict)

    for r in runs:
        py = r.get("python", "unknown")
        # パーサが格納しているキーに幅を持たせて拾う
        rows = r.get("results") or r.get("cases") or {}
        for case, row in rows.items():
            # 列名の揺れに対応
            std = row.get("stdlib") or row.get("stdlib.gzip") or row.get("gzip") or row.get("stdlib_gzip")
            zl  = row.get("zlib")   or row.get("zlib.recover") or row.get("recover") or row.get("zlib_recover")
            std_n = _normalize_verdict(std)
            zl_n  = _normalize_verdict(zl)
            if std_n is not None:
                verdict_stdlib[case][py] = std_n
            if zl_n is not None:
                verdict_zlib[case][py] = zl_n

    return verdict_stdlib, verdict_zlib

def _zr_for(py: str, runs: list[dict]) -> str:
    """指定 Python 版に対応する zlib(runtime) を取得"""
    return next((r.get("zlib_runtime", "?") for r in runs if r.get("python") == py), "?")

def render_verdict_table(title: str, verdict_map: dict[str, dict[str, str]], runs: list[dict]) -> str:
    """
    verdict_map を使って比較表を出力。
    1行目: python 見出し
    2行目: zlib(runtime)
    罫線
    以後: CASE_ORDER の順で各ケースの行
    """
    py_cols = collect_columns(runs)

    def fmt_row(cells: list[str]) -> str:
        # 各セルの最小幅をそろえるシンプル整形（すでに使っているやつがあればそれでOK）
        widths = [max(len(str(c)), 6) for c in cells]
        return "| " + " | ".join(f"{str(c):<{w}}" for c, w in zip(cells, widths)) + " |"

    lines: list[str] = []
    lines.append(title)
    # 見出し2段
    lines.append(fmt_row(["python"] + py_cols))
    lines.append(fmt_row(["zlib(runtime)"] + [_zr_for(py, runs) for py in py_cols]))
    # セパレータ
    lines.append(fmt_row(["-" * 6] * (len(py_cols) + 1)))
    # 本体
    for case in CASE_ORDER:
        row = [case]
        for py in py_cols:
            cell = verdict_map.get(case, {}).get(py, "")
            row.append(cell)
        lines.append(fmt_row(row))
    return "\n".join(lines)

def render_mismatches(runs: List[Dict]) -> str:
    """
    各ケースごとに、"stdlib.gzip!=OK かつ zlib.recover=OK" の環境を列挙。
    1つも無ければ 'none' を出す。セクションは常に出力。
    """
    py_cols = collect_columns(runs)
    verdict_stdlib, verdict_zlib = make_verdict_maps(runs)

    lines: List[str] = []
    lines.append("\n-- Recoverable mismatches (stdlib.gzip!=OK & zlib.recover=OK) --")
    for case in CASE_ORDER:
        envs: List[str] = []
        for py in py_cols:
            std = verdict_stdlib.get(case, {}).get(py)
            zl  = verdict_zlib.get(case, {}).get(py)
            if std is None or zl is None:
                continue
            if std != "OK" and zl == "OK":
                zr = next((rr.get("zlib_runtime", "?") for rr in runs if rr.get("python") == py), "?")
                envs.append(f"{py} (zr={zr})")
        if envs:
            lines.append(f"{case}: " + ", ".join(envs))
        else:
            lines.append(f"{case}: none")
    return "\n".join(lines)

def main():
    ap = argparse.ArgumentParser(description="Summarize gzip/zlib behavior across runtimes.")
    ap.add_argument("paths", nargs="+", help="log files like logs/out_*.txt")
    ap.add_argument("--md", action="store_true", help="also write summary.md")
    args = ap.parse_args()

    paths = [Path(p) for p in args.paths]
    runs = [parse_file(p) for p in paths]
    verdict_stdlib, verdict_zlib = make_verdict_maps(runs)
    parts = [
        render_runtime_table(runs),
        "",
        render_verdict_table("== stdlib.gzip (OK or error reason by Python) ==", verdict_stdlib, runs),
        "",
        render_verdict_table("== zlib.recover (OK or error reason by Python) ==", verdict_zlib, runs),
        "",
        render_mismatches(runs),
    ]
    out = "\n".join(parts)

    print(out)
    if args.md:
        Path("summary.md").write_text(out + "\n", encoding="utf-8")
        print("[summarize_results] wrote: summary.md", file=sys.stderr)

if __name__ == "__main__":
    main()