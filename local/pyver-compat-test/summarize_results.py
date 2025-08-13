#!/usr/bin/env python3
"""
Summarize results from test_gzip_variants.py runs.

Parses blocks between:
  ### BEGIN RESULTS
  ### END RESULTS

Accepts banner lines like:
  "Python 3.13.6 | gzip stdlib | zlib build=1.2.13 runtime=1.2.13"
or legacy:
  "Python 3.13.6 | zlib 1.2.13"

Usage:
  python3 summarize_results.py logs/out_*.txt
  python3 summarize_results.py --md SUMMARY.md logs/out_*.txt
  python3 summarize_results.py --stdout-only logs/out_*.txt
"""

from __future__ import annotations
import argparse
import re
from pathlib import Path
from typing import Dict, List, Tuple

BEG = "### BEGIN RESULTS"
END = "### END RESULTS"

# permissive pickers
RE_PY   = re.compile(r"\bPython\s+(\d+\.\d+\.\d+)\b", re.I)
RE_GZIP_ANY = re.compile(r"\bgzip\s+([A-Za-z0-9._-]+)\b", re.I)  # e.g. "stdlib" or "1.10"
RE_ZLIB_RUNTIME = re.compile(r"\bzlib\b.*\bruntime=(\d+\.\d+(?:\.\d+)?)", re.I)
RE_ZLIB_BUILD   = re.compile(r"\bzlib\b.*\bbuild=(\d+\.\d+(?:\.\d+)?)", re.I)
RE_ZLIB_SIMPLE  = re.compile(r"\bzlib\s+(\d+\.\d+(?:\.\d+)?)\b", re.I)

# ENV fallback:
# "## ENV PY=3.13.6 GZIP=stdlib ZLIB_BUILD=1.2.13 ZLIB_RUNTIME=1.2.13"
RE_ENV_PY   = re.compile(r"\bPY=(\d+\.\d+\.\d+)\b")
RE_ENV_GZIP = re.compile(r"\bGZIP=([A-Za-z0-9._-]+)\b")
RE_ENV_ZB   = re.compile(r"\bZLIB_BUILD=(\d+\.\d+(?:\.\d+)?)\b")
RE_ENV_ZR   = re.compile(r"\bZLIB_RUNTIME=(\d+\.\d+(?:\.\d+)?)\b")

def parse_versions(text: str) -> tuple[str, str, str, str]:
    py = gz = zb = zr = "unknown"
    for line in text.splitlines():
        if py == "unknown":
            m = RE_PY.search(line)
            if m:
                py = m.group(1)

        if gz == "unknown":
            m = RE_GZIP_ANY.search(line)
            if m:
                gz = m.group(1)

        # prefer explicit runtime/build when present
        if zr == "unknown":
            m = RE_ZLIB_RUNTIME.search(line)
            if m:
                zr = m.group(1)
        if zb == "unknown":
            m = RE_ZLIB_BUILD.search(line)
            if m:
                zb = m.group(1)

        # legacy simple "zlib 1.2.13"
        if zr == "unknown" and zb == "unknown":
            m = RE_ZLIB_SIMPLE.search(line)
            if m:
                zr = zb = m.group(1)

        # ENV fallback line (if banner missing)
        if line.startswith("## ENV"):
            if py == "unknown":
                m = RE_ENV_PY.search(line)
                if m:
                    py = m.group(1)
            if gz == "unknown":
                m = RE_ENV_GZIP.search(line)
                if m:
                    gz = m.group(1)
            if zb == "unknown":
                m = RE_ENV_ZB.search(line)
                if m:
                    zb = m.group(1)
            if zr == "unknown":
                m = RE_ENV_ZR.search(line)
                if m:
                    zr = m.group(1)

    # if one of zlib build/runtime is still unknown but the other known, mirror it
    if zb == "unknown" and zr != "unknown":
        zb = zr
    if zr == "unknown" and zb != "unknown":
        zr = zb
    return py, gz, zb, zr

def parse_file(p: Path) -> dict | None:
    text = p.read_text(encoding="utf-8", errors="replace")
    py_ver, gz_id, zl_build, zl_runtime = parse_versions(text)

    lines = text.splitlines()
    try:
        i1 = lines.index(BEG)
        i2 = lines.index(END, i1 + 1)
    except ValueError:
        print(f"[warn] {p}: results block not found ({BEG}/{END}).")
        return None

    block = lines[i1 + 1 : i2]

    header: List[str] = []
    rows: List[List[str]] = []
    for ln in block:
        s = ln.strip()
        if not s.startswith("|"):
            continue
        cells = [c.strip() for c in s.strip("|").split("|")]
        if cells and all(set(c) <= {"-", ":"} for c in cells):
            continue
        if not header:
            header = cells
        else:
            rows.append(cells)

    if not header or not rows:
        print(f"[warn] {p}: could not parse header/rows.")
        return None

    dec_cols: List[int] = []
    dec_names: List[str] = []
    for idx, name in enumerate(header):
        low = name.lower()
        if low in ("case", "in", "expect"):
            continue
        dec_cols.append(idx)
        dec_names.append(name)

    if not dec_cols:
        print(f"[warn] {p}: no decoder columns found in header: {header}")
        return None

    cases: Dict[str, Dict[str, str]] = {}
    for r in rows:
        if not r:
            continue
        case = r[0]
        cases[case] = {}
        for name, idx in zip(dec_names, dec_cols):
            cell = r[idx] if idx < len(r) else ""
            status = "OK" if "OK" in cell else ("ERR" if "ERR" in cell else "")
            cases[case][name] = status

    return {
        "file": str(p),
        "python": py_ver,
        "gzip": gz_id,
        "zlib_build": zl_build,
        "zlib_runtime": zl_runtime,
        "decoders": dec_names,
        "cases": cases,
    }

def vtuple(v: str) -> Tuple[int, int, int]:
    try:
        a, b, c = v.split(".")
        return int(a), int(b), int(c)
    except Exception:
        return (0, 0, 0)

def build_table(runs: List[dict], decoder: str) -> str:
    py_cols = sorted({r["python"] for r in runs}, key=vtuple)
    all_cases = sorted({c for r in runs for c in r["cases"].keys()})
    headers = ["case"] + py_cols

    w0 = max(len("case"), max((len(c) for c in all_cases), default=4))
    widths = [w0] + [max(len(py), 6) for py in py_cols]

    def fmt_row(cells: List[str]) -> str:
        return "| " + " | ".join(c.ljust(w) for c, w in zip(cells, widths)) + " |"

    def fmt_sep() -> str:
        return "|-" + "-|-".join("-" * w for w in widths) + "-|"

    status: Dict[Tuple[str, str], str] = {}
    for r in runs:
        py = r["python"]
        for case, decs in r["cases"].items():
            s = decs.get(decoder, "")
            if s:
                status[(case, py)] = s

    lines = [fmt_row(headers), fmt_sep()]
    for case in all_cases:
        row = [case] + [f"{status.get((case, py), ''):>3}" for py in py_cols]
        lines.append(fmt_row(row))
    return "\n".join(lines)

def build_versions_table(runs: List[dict]) -> str:
    py_cols = sorted({r["python"] for r in runs}, key=vtuple)
    by_py: Dict[str, dict] = {}
    for r in runs:
        by_py.setdefault(r["python"], r)

    lines = [
        "== Runtime versions ==",
        "| Python  | gzip      | zlib(build) | zlib(runtime) |",
        "|---------|-----------|-------------|---------------|",
    ]
    for py in py_cols:
        gz = by_py[py].get("gzip", "unknown")
        zb = by_py[py].get("zlib_build", "unknown")
        zr = by_py[py].get("zlib_runtime", "unknown")
        lines.append(f"| {py:<7} | {gz:<9} | {zb:<11} | {zr:<13} |")
    return "\n".join(lines)

def build_mismatches(runs: List[dict], a: str, b: str) -> str:
    all_cases = sorted({c for r in runs for c in r["cases"].keys()})
    by_case: Dict[str, List[str]] = {c: [] for c in all_cases}
    for r in runs:
        py = r["python"]
        for case, decs in r["cases"].items():
            if decs.get(a) == "ERR" and decs.get(b) == "OK":
                by_case[case].append(py)

    out = ["-- Recoverable mismatches ({}=ERR & {}=OK) --".format(a, b)]
    for case in all_cases:
        pys = by_case.get(case, [])
        if pys:
            out.append(f"{case}: " + ", ".join(sorted(pys, key=vtuple)))
    if len(out) == 1:
        out.append("none")
    return "\n".join(out)

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--md", dest="md_path", default="SUMMARY.md",
                    help="Write markdown summary here (default: SUMMARY.md). Use '-' to skip writing.")
    ap.add_argument("--stdout-only", action="store_true",
                    help="Print summary to stdout only (no file write).")
    ap.add_argument("paths", nargs="+", help="Result files (e.g., logs/out_*.txt)")
    args = ap.parse_args()

    runs = []
    for p in map(Path, args.paths):
        try:
            r = parse_file(p)
            if r:
                runs.append(r)
        except Exception as e:
            print(f"[warn] {p}: parse error: {e}")

    if not runs:
        print("[error] No results parsed. Do your inputs contain the BEGIN/END block and a pipe-table?")
        return

    decoders: List[str] = []
    seen = set()
    for r in runs:
        for d in r["decoders"]:
            if d not in seen:
                seen.add(d)
                decoders.append(d)

    out_parts: List[str] = [build_versions_table(runs), ""]

    for dec in decoders:
        out_parts.append(f"== {dec} (OK/ERR by Python) ==")
        out_parts.append(build_table(runs, dec))
        out_parts.append("")

    if len(decoders) >= 2:
        out_parts.append(build_mismatches(runs, decoders[0], decoders[1]))
        out_parts.append("")

    out = "\n".join(out_parts).rstrip() + "\n"
    print(out, end="")

    if not args.stdout_only and args.md_path != "-":
        Path(args.md_path).write_text(out, encoding="utf-8")
        print(f"[summarize_results] wrote: {args.md_path}")

if __name__ == "__main__":
    main()