#!/usr/bin/env python3
import argparse
import io
import os
import re
from pathlib import Path
from typing import Dict, List, Tuple

Row = Dict[str, str]  # case -> "OK"/"ERR"
ImplResults = Dict[str, Row]  # version -> {case: status}

BEGIN = "### BEGIN RESULTS"
END = "### END RESULTS"

def parse_version_from_header(lines: List[str]) -> str:
    for ln in lines[:5]:
        # e.g. "Python 3.8.20 | zlib 1.2.13"
        m = re.search(r"Python\s+(\d+\.\d+\.\d+)", ln)
        if m:
            return m.group(1)
    # fallback: unknown
    return "?"

def parse_table(lines: List[str]) -> Tuple[List[str], List[List[str]]]:
    """Return header + rows (cell strings) inside BEGIN..END block."""
    in_table = False
    raw_rows: List[List[str]] = []
    header: List[str] = []
    for ln in lines:
        if ln.strip() == BEGIN:
            in_table = True
            continue
        if ln.strip() == END:
            break
        if not in_table:
            continue
        if not ln.startswith("|"):
            continue
        cells = [c.strip() for c in ln.strip().strip("|").split("|")]
        if not cells or cells[0] in ("case", "") or set(cells[0]) == {"-"}:
            # ヘッダ行や区切り線はスキップ
            if cells and cells[0] == "case":
                header = cells
            continue
        raw_rows.append(cells)
    if not header and raw_rows:
        # 先頭行がヘッダの可能性（保険）
        header = raw_rows.pop(0)
    return header, raw_rows

def status_from_cell(cell: str) -> str:
    # e.g. "OK(12/12)" or "ERR(EOFError)"; そのままOK/ERRに正規化
    return "OK" if cell.strip().startswith("OK") else "ERR"

def parse_file(path: str) -> Tuple[str, ImplResults, ImplResults]:
    with open(path, "r", encoding="utf-8", errors="ignore") as f:
        lines = f.readlines()
    ver = parse_version_from_header(lines)
    header, rows = parse_table(lines)
    # 期待する列: case | in | expect | stdlib.gzip | zlib.recover
    col_idx = {name: i for i, name in enumerate(header)}
    def get(col: str, r: List[str]) -> str:
        return r[col_idx[col]] if col in col_idx and col_idx[col] < len(r) else ""

    gzip_res: ImplResults = {ver: {}}
    zlib_res: ImplResults = {ver: {}}

    for r in rows:
        case = get("case", r)
        if not case:
            continue
        gzip_res[ver][case] = status_from_cell(get("stdlib.gzip", r))
        zlib_res[ver][case] = status_from_cell(get("zlib.recover", r))
    return ver, gzip_res, zlib_res

def merge_results(all_results: List[ImplResults]) -> ImplResults:
    merged: ImplResults = {}
    for res in all_results:
        for ver, row in res.items():
            merged.setdefault(ver, {}).update(row)
    return merged

def ordered_versions(impl: ImplResults, preferred: List[str]) -> List[str]:
    # 入力順（preferred）に並べ、未知は末尾
    have = set(impl.keys())
    order = [v for v in preferred if v in have]
    tail = [v for v in impl.keys() if v not in have or v not in order]
    return order + sorted([v for v in tail if v not in order])

def all_cases(impls: List[ImplResults]) -> List[str]:
    s = set()
    for impl in impls:
        for row in impl.values():
            s.update(row.keys())
    return sorted(s)

def render_table(title: str, impl: ImplResults, versions: List[str], cases: List[str]) -> str:
    out = io.StringIO()
    out.write(f"== {title} (OK/ERR by Python) ==\n")
    # ヘッダ
    out.write("| case               | " + " | ".join(f"{v:>7}" for v in versions) + " |\n")
    out.write("|--------------------|" + "|".join(["---------"] * len(versions)) + "|\n")
    # 行
    for c in cases:
        cells = []
        for v in versions:
            status = impl.get(v, {}).get(c, "")
            cells.append(f"{status:>7}")
        out.write(f"| {c:18} | " + " | ".join(cells) + " |\n")
    out.write("\n")
    return out.getvalue()

def render_mismatches(gzip: ImplResults, zlib: ImplResults, versions: List[str], cases: List[str]) -> str:
    out = io.StringIO()
    out.write("-- Recoverable mismatches (gzip=ERR & zlib=OK) --\n")
    any_case = False
    for c in cases:
        vs = [v for v in versions if gzip.get(v, {}).get(c) == "ERR" and zlib.get(v, {}).get(c) == "OK"]
        if vs:
            any_case = True
            out.write(f"{c}: " + ", ".join(vs) + "\n")
    if not any_case:
        out.write("None\n")
    return out.getvalue()

def main() -> None:
    ap = argparse.ArgumentParser(description="Summarize per-version gzip/zlib results into Markdown.")
    ap.add_argument("--md", dest="md_out", help="Write Markdown to this file")
    ap.add_argument("inputs", nargs="+", help="Input result files (e.g., logs/out_*.txt)")
    args = ap.parse_args()

    versions_in_order: List[str] = []
    gzip_all: List[ImplResults] = []
    zlib_all: List[ImplResults] = []

    for p in args.inputs:
        ver, gz_res, zl_res = parse_file(p)
        versions_in_order.append(ver)
        gzip_all.append(gz_res)
        zlib_all.append(zl_res)

    gzip_merged = merge_results(gzip_all)
    zlib_merged = merge_results(zlib_all)
    versions = ordered_versions(gzip_merged, versions_in_order)
    cases = all_cases([gzip_merged, zlib_merged])

    md = io.StringIO()
    md.write(render_table("stdlib.gzip", gzip_merged, versions, cases))
    md.write(render_table("zlib.recover", zlib_merged, versions, cases))
    md.write(render_mismatches(gzip_merged, zlib_merged, versions, cases))

    out_text = md.getvalue()
    print(out_text)

    if args.md_out:
        out_path = Path(args.md_out)
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_text(out_text, encoding="utf-8")
        # 終了メッセージはstderrに
        print(f"[summarize_results] wrote: {out_path}", file=io.TextIOWrapper(getattr(os, "dup", lambda x: 2)(2)))

if __name__ == "__main__":
    main()