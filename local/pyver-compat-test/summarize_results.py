##### summarize_results.py (simplified)

from __future__ import annotations
import argparse
import re
import sys
from pathlib import Path
from typing import Dict, List, Tuple

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

def index_runs_by_col(runs: List[Dict]) -> Dict[Tuple[str, str], Dict]:
    return {(r["runtime"]["python"], r["runtime"]["zlib_runtime"]): r for r in runs}

def render_summary_table(title: str, engine: str, runs: List[Dict]) -> str:
    cols = collect_columns(runs)
    idx = index_runs_by_col(runs)

    header1 = ["python"] + [py for (py, zr) in cols]
    header2 = ["zlib(runtime)"] + [zr for (py, zr) in cols]

    body_rows: List[List[str]] = []
    for case in CASE_ORDER:
        row = [case]
        for (py, zr) in cols:
            cell = idx.get((py, zr), {}).get("cases", {}).get(
                "stdlib" if engine == "stdlib" else "zlib", {}
            ).get(case, "")
            row.append(cell)
        body_rows.append(row)

    rows = [header1, header2] + body_rows
    widths = compute_widths(rows)
    lines = [f"== {title} (OK or error reason by Python) ==",
             fmt_row(header1, widths),
             fmt_row(header2, widths),
             fmt_sep(widths)]
    lines.extend(fmt_row(r, widths) for r in body_rows)
    return "\n".join(lines)

def render_mismatches(runs: List[Dict]) -> str:
    cols = collect_columns(runs)
    idx = index_runs_by_col(runs)

    def label(py: str, zr: str) -> str:
        return f"{py} (zr={zr})"

    lines = ["-- Recoverable mismatches (stdlib.gzip=ERR & zlib.recover=OK) --"]
    for case in CASE_ORDER:
        spots = []
        for (py, zr) in cols:
            r = idx.get((py, zr))
            if not r:
                continue
            stdlib_res = r["cases"]["stdlib"].get(case, "")
            zlib_res = r["cases"]["zlib"].get(case, "")
            if stdlib_res and stdlib_res != "OK" and zlib_res == "OK":
                spots.append(label(py, zr))
        if spots:
            lines.append(f"{case}: " + ", ".join(spots))
    return "\n".join(lines)

def main():
    ap = argparse.ArgumentParser(description="Summarize gzip/zlib behavior across runtimes.")
    ap.add_argument("paths", nargs="+", help="log files like logs/out_*.txt")
    ap.add_argument("--md", action="store_true", help="also write summary.md")
    args = ap.parse_args()

    paths = [Path(p) for p in args.paths]
    runs = [parse_file(p) for p in paths]

    parts = [
        render_runtime_table(runs),
        "",
        render_summary_table("stdlib.gzip", "stdlib", runs),
        "",
        render_summary_table("zlib.recover", "zlib", runs),
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