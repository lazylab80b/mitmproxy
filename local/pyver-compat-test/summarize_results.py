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

def parse_file(p: Path) -> Dict:
    """
    Parse one log file produced by test_gzip_variants.py
    Expected header (ENV line):
      ## ENV PY=3.13.6 GZIP=stdlib ZLIB_BUILD=1.2.13 ZLIB_RUNTIME=1.2.13 PLATFORM=...
    Table block:
      ### BEGIN RESULTS ... ### END RESULTS
    """
    text = p.read_text(encoding="utf-8", errors="replace")

    # ランタイム情報（ENV）
    runtime = {
        "python": "unknown",
        "gzip": "unknown",
        "zlib_build": "unknown",
        "zlib_runtime": "unknown",
        "platform": "unknown",
    }
    m = re.search(
        r"^##\s*ENV\s+PY=([\d.]+)\s+GZIP=(\w+)\s+ZLIB_BUILD=([\d.]+)\s+ZLIB_RUNTIME=([\d.]+)\s+PLATFORM=(.+)$",
        text, re.MULTILINE,
    )
    if m:
        runtime["python"], runtime["gzip"], runtime["zlib_build"], runtime["zlib_runtime"], runtime["platform"] = m.groups()

    def normalize(cell: str) -> str:
        cell = cell.strip()
        if cell.startswith("OK("):
            return "OK"
        if cell.startswith("ERR(") and cell.endswith(")"):
            inner = cell[4:-1].strip()
            return inner or "error"
        return "OK" if cell == "OK" else cell

    # テーブル抽出
    stdlib: Dict[str, str] = {}
    zlibrec: Dict[str, str] = {}
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
            if case in CASE_ORDER:
                stdlib[case] = normalize(cells[3])
                zlibrec[case] = normalize(cells[4])

    return {
        "runtime": runtime,
        "cases": {"stdlib": stdlib, "zlib": zlibrec},
        "file": str(p),
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

def render_verdict_table(title: str,
                         verdict_map: Dict[str, Dict[Tuple[str, str], str]],
                         runs: List[Dict]) -> str:
    cols = collect_columns(runs)
    # 見出し（2段）
    head_py = ["python"] + [py for (py, _zr) in cols]
    head_zr = ["zlib(runtime)"] + [_zr for (_py, _zr) in cols]
    # セパレータ用
    sep = ["-" * 6] * len(head_py)

    # 本体
    body: List[List[str]] = []
    for case in CASE_ORDER:
        row = [case]
        for key in cols:
            row.append(verdict_map.get(case, {}).get(key, ""))
        body.append(row)

    rows = [head_py, head_zr, sep] + body
    widths = compute_widths(rows)
    out = [title, fmt_row(head_py, widths), fmt_row(head_zr, widths), fmt_row(sep, widths)]
    out.extend(fmt_row(r, widths) for r in body)
    return "\n".join(out)

def render_mismatches(runs: List[Dict]) -> str:
    """
    各ケースごとに、"stdlib.gzip!=OK かつ zlib.recover=OK" の環境を列挙。
    常に出力し、無ければ 'none' を出す。
    """
    cols = collect_columns(runs)
    verdict_stdlib, verdict_zlib = make_verdict_maps(runs)

    lines: List[str] = []
    lines.append("\n-- Recoverable mismatches (stdlib.gzip!=OK & zlib.recover=OK) --")
    for case in CASE_ORDER:
        envs: List[str] = []
        for (py, zr) in cols:
            std = verdict_stdlib.get(case, {}).get((py, zr))
            zl  = verdict_zlib.get(case, {}).get((py, zr))
            if std is None or zl is None:
                continue
            if std != "OK" and zl == "OK":
                envs.append(f"{py} (zr={zr})")
        lines.append(f"{case}: " + (", ".join(envs) if envs else "none"))
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