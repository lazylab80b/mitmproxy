#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Summarize gzip/zlib behavior across runs.

- 既定: 標準出力に表示。--write <path> 指定時のみファイル保存。
- 同一 Python でも zlib runtime が違えば別列（列キー=(python, zlib_runtime, gzip_kind)）。
- 各表は「python 行」→「zlib(runtime) 行」→「zlib(build) 行」→区切り線→ケース行。
- セル幅を列ごとに揃えてパディング（生Markdownでも整列）。
- セルは OK / 具体的エラー理由 (EOFError, BadGzipFile, error) を表示。
"""

from __future__ import annotations
import sys, re, argparse
from pathlib import Path
from typing import Dict, List, Tuple, Any

HEADER_RE = re.compile(
    r"^Python\s+(?P<py>[\d\.]+)\s*\|\s*gzip\s+(?P<gzip>\w+)\s*\|\s*zlib\s+build=(?P<zb>[\d\.]+)\s+runtime=(?P<zr>[\d\.]+)"
)
ROW_RE = re.compile(
    r"^\|\s*(?P<case>[^|]+?)\s*\|\s*(?P<in>\d+)\s*\|\s*(?P<expect>\d+)\s*\|\s*(?P<gzip>OK\([^)]+\)|ERR\([^)]+\))\s*\|\s*(?P<zlib>OK\([^)]+\)|ERR\([^)]+\))\s*\|"
)

def parse_file(path: Path) -> Dict[str, Any]:
    meta = {"python": None, "gzip_kind": None, "zlib_build": None, "zlib_runtime": None}
    cases: Dict[str, Dict[str, str]] = {}
    in_table = False

    for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        if not meta["python"]:
            m = HEADER_RE.match(line.strip())
            if m:
                meta["python"] = m.group("py")
                meta["gzip_kind"] = m.group("gzip")
                meta["zlib_build"] = m.group("zb")
                meta["zlib_runtime"] = m.group("zr")
                continue
        if line.strip().startswith("### BEGIN RESULTS"):
            in_table = True
            continue
        if line.strip().startswith("### END RESULTS"):
            in_table = False
            continue
        if not in_table:
            continue
        m = ROW_RE.match(line)
        if m:
            case = m.group("case").strip()
            g = m.group("gzip")
            z = m.group("zlib")
            def norm(cell: str) -> str:
                if cell.startswith("OK("):
                    return "OK"
                if cell.startswith("ERR(") and cell.endswith(")"):
                    return cell[4:-1] or "error"
                return cell
            cases[case] = {"stdlib": norm(g), "zlib": norm(z)}
    return {"meta": meta, "cases": cases, "path": str(path)}

def ver_tuple(s: str) -> Tuple[int, ...]:
    try:
        return tuple(int(x) for x in s.split("."))
    except Exception:
        return (0,)

# ---------- 表整形（固定幅パディング） ----------
def col_widths(rows: List[List[str]]) -> List[int]:
    if not rows:
        return []
    n = max(len(r) for r in rows)
    w = [0]*n
    for r in rows:
        for i, cell in enumerate(r):
            w[i] = max(w[i], len(cell))
    return w

def fmt_row_pad(row: List[str], widths: List[int]) -> str:
    cells = []
    for i, cell in enumerate(row):
        pad = widths[i] - len(cell)
        cells.append(cell + (" " * pad))
    return "| " + " | ".join(cells) + " |"

def fmt_sep_pad(widths: List[int]) -> str:
    return "| " + " | ".join("-" * max(3, w) for w in widths) + " |"
# --------------------------------------------------

def build_summary(runs: List[Dict[str, Any]]) -> str:
    # 列キー: (python, zlib_runtime, gzip_kind)
    colkeys: List[Tuple[str, str, str]] = []
    meta_by_col: Dict[Tuple[str, str, str], Dict[str, str]] = {}
    cases_union: List[str] = []

    for r in runs:
        m = r["meta"]
        py = m["python"] or "unknown"
        zr = m["zlib_runtime"] or "unknown"
        gk = m["gzip_kind"] or "unknown"
        key = (py, zr, gk)
        if key not in meta_by_col:
            meta_by_col[key] = {
                "python": py,
                "gzip_kind": gk,
                "zlib_build": m["zlib_build"] or "unknown",
                "zlib_runtime": zr,
            }
            colkeys.append(key)
        for c in r["cases"].keys():
            if c not in cases_union:
                cases_union.append(c)

    colkeys.sort(key=lambda k: (ver_tuple(k[0]), ver_tuple(k[1]), k[2]))
    py_cols = [meta_by_col[k]["python"] for k in colkeys]
    zr_cols = [meta_by_col[k]["zlib_runtime"] for k in colkeys]
    zb_cols = [meta_by_col[k]["zlib_build"] for k in colkeys]

    out_lines: List[str] = []

    # == Runtime versions ==
    out_lines.append("== Runtime versions ==")
    rv_rows = [
        ["Python", "gzip", "zlib(build)", "zlib(runtime)"],
    ]
    for k in colkeys:
        md = meta_by_col[k]
        rv_rows.append([md["python"], md["gzip_kind"], md["zlib_build"], md["zlib_runtime"]])
    w = col_widths(rv_rows)
    out_lines.append(fmt_row_pad(rv_rows[0], w))
    out_lines.append(fmt_sep_pad(w))
    for r in rv_rows[1:]:
        out_lines.append(fmt_row_pad(r, w))
    out_lines.append("")

    def build_matrix(title: str, which: str) -> None:
        out_lines.append(f"== {title} (OK or error reason by Python) ==")
        # ヘッダ3行 + 区切り + ケース行
        rows: List[List[str]] = []
        rows.append(["python"] + py_cols)
        rows.append(["zlib(runtime)"] + zr_cols)
        rows.append(["zlib(build)"] + zb_cols)
        # ダミーで区切り計算に含めないため、後で sep を挿入
        for case in cases_union:
            row = [case]
            for k in colkeys:
                val = ""
                for r in runs:
                    m = r["meta"]
                    if (m["python"] or "unknown", m["zlib_runtime"] or "unknown", m["gzip_kind"] or "unknown") != k:
                        continue
                    d = r["cases"].get(case)
                    if d:
                        v = d["stdlib" if which == "stdlib" else "zlib"]
                        val = v
                row.append(val or "")
            rows.append(row)

        widths = col_widths(rows)
        # ヘッダ3行
        out_lines.append(fmt_row_pad(rows[0], widths))
        out_lines.append(fmt_row_pad(rows[1], widths))
        out_lines.append(fmt_row_pad(rows[2], widths))
        # 区切り線
        out_lines.append(fmt_sep_pad(widths))
        # ケース
        for r in rows[3:]:
            out_lines.append(fmt_row_pad(r, widths))
        out_lines.append("")

    build_matrix("stdlib.gzip", which="stdlib")
    build_matrix("zlib.recover", which="zlib")

    # mismatches
    out_lines.append("-- Recoverable mismatches (stdlib.gzip=ERR & zlib.recover=OK) --")
    labels = [f'{meta_by_col[k]["python"]} (zr={meta_by_col[k]["zlib_runtime"]})' for k in colkeys]
    for case in cases_union:
        hit: List[str] = []
        for i, k in enumerate(colkeys):
            std = rec = None
            for r in runs:
                m = r["meta"]
                if (m["python"] or "unknown", m["zlib_runtime"] or "unknown", m["gzip_kind"] or "unknown") != k:
                    continue
                d = r["cases"].get(case)
                if d:
                    std = d["stdlib"]
                    rec = d["zlib"]
            if std and rec and std != "OK" and rec == "OK":
                hit.append(labels[i])
        if hit:
            out_lines.append(f"{case}: " + ", ".join(hit))
    out_lines.append("")
    return "\n".join(out_lines)

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--write", metavar="PATH", help="Write summary to PATH as well.")
    ap.add_argument("paths", nargs="*", help="Input log files (glob-expanded by shell).")
    args = ap.parse_args()

    paths = [Path(p) for p in args.paths] if args.paths else sorted(Path("logs").glob("out_*.txt"))
    runs = [parse_file(p) for p in paths if p.exists()]
    if not runs:
        print("No inputs.", file=sys.stderr)
        sys.exit(2)

    summary = build_summary(runs)
    print(summary)  # 既定は標準出力
    if args.write:
        Path(args.write).write_text(summary, encoding="utf-8")
        print(f"[summarize_results] wrote: {args.write}", file=sys.stderr)

if __name__ == "__main__":
    main()