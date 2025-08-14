#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Cross-version probe for how stdlib gzip vs. zlib behave on various gzip edge cases.

It prints:
  - an environment header (Python/zlib build/runtime),
  - a machine-readable ENV line,
  - then a markdown table between "### BEGIN RESULTS" ... "### END RESULTS".

The companion script summarize_results.py consumes the logs from multiple runs
(e.g. different Python images) and renders a compact comparison matrix.
"""
from __future__ import annotations

import binascii
import gzip
import io
import platform
import struct
import re
import zlib
from typing import Optional, Tuple


# ---------------------------------------------------------------------------
# helpers to craft gzip streams
# ---------------------------------------------------------------------------

def _gzip_ok(payload: bytes) -> bytes:
    """Return a well-formed gzip member for payload (mtime=0 for determinism)."""
    bio = io.BytesIO()
    with gzip.GzipFile(fileobj=bio, mode="wb", mtime=0) as f:
        f.write(payload)
    return bio.getvalue()


def _gzip_truncated_no_trailer(payload: bytes, splits: int = 1) -> bytes:
    """
    Build a gzip stream and *omit* the trailer by reading before close.
    We also do Z_SYNC_FLUSH between chunks to induce multiple deflate blocks.
    """
    buf = io.BytesIO()
    gz = gzip.GzipFile(fileobj=buf, mode="wb", mtime=0)
    n = max(1, int(splits))
    step = max(1, len(payload) // n)
    for i in range(0, len(payload), step):
        chunk = payload[i:i + step]
        gz.write(chunk)
        # force a block boundary; *do not* finish the stream
        gz.flush(zlib.Z_SYNC_FLUSH)
    data = buf.getvalue()  # grab bytes *before* closing -> no trailer in output
    gz.close()
    return data


def _flip_crc_byte(gz: bytes, which_from_end: int = 5) -> bytes:
    """
    Flip a CRC32 byte in the gzip trailer. Default flips the 3rd CRC byte
    (counted from the end of the whole gzip member).
    """
    ba = bytearray(gz)
    if len(ba) < 8:
        return bytes(ba)
    ba[-which_from_end] ^= 0xFF
    return bytes(ba)


def _flip_isize_lsb(gz: bytes) -> bytes:
    """Flip the ISIZE LSB (last byte of the gzip member)."""
    ba = bytearray(gz)
    if not ba:
        return bytes(ba)
    ba[-1] ^= 0xFF
    return bytes(ba)


def _missing_last_byte(gz: bytes) -> bytes:
    """Return gzip member with the very last byte cut off."""
    if not gz:
        return gz
    return gz[:-1]


# ---------------------------------------------------------------------------
# engines under test
# ---------------------------------------------------------------------------

def run_stdlib_gzip(gz_bytes: bytes) -> Tuple[bool, Optional[int], Optional[str]]:
    """
    Try stdlib gzip to read the whole member. Returns (ok, out_len, err_name)
    where err_name is None on success.
    """
    try:
        out = gzip.GzipFile(fileobj=io.BytesIO(gz_bytes)).read()
        return True, len(out), None
    except Exception as e:  # EOFError, gzip.BadGzipFile, OSError, ...
        return False, None, type(e).__name__


def run_zlib_recover(gz_bytes: bytes) -> Tuple[bool, Optional[int], Optional[str]]:
    """
    Recover via zlib *gzip mode* but deliberately *not* validating at the end.
    We stream-decompress and simply return any produced output; we do not call
    .flush() and we don't force trailer checks. This means:
      - truncated/missing trailer -> usually OK with output produced,
      - CRC/ISIZE flipped -> zlib typically raises at end-of-stream parsing.
    """
    try:
        d = zlib.decompressobj(wbits=16 + zlib.MAX_WBITS)  # gzip wrapper
        out = d.decompress(gz_bytes)
        # DO NOT call d.flush() here; we intentionally avoid final validations.
        return True, len(out), None
    except zlib.error as e:
        s = str(e)
        # 例: "Error -5 while decompressing data" から -5 を抜く
        m = re.search(r"Error\s+(-?\d+)", s)
        short = m.group(1) if m else (s.split(":", 1)[0] or s)[:12]
        return False, None, short  # 例: "-5" や "error" 相当の短い理由を返す
    except Exception as e:  # zlib.error, etc.
        return False, None, "error"


# ---------------------------------------------------------------------------
# pretty print
# ---------------------------------------------------------------------------

def fmt_status(ok: bool, out_len: Optional[int], expect_len: int, err_name: Optional[str]) -> str:
    if ok:
        return f"OK({out_len}/{expect_len})"
    else:
        return f"ERR({err_name})"


def print_table(rows):
    print("### BEGIN RESULTS")
    print("| case               |  in | expect | stdlib.gzip           | zlib.recover        |")
    print("|--------------------|----:|-------:|-----------------------|---------------------|")
    for r in rows:
        print(f"| {r['case']:<18} | {r['in']:>3} | {r['expect']:>6} | {r['g_std']:<21} | {r['g_rec']:<19} |")
    print("### END RESULTS")


def main() -> None:
    # Environment header (human + machine-readable)
    plat = f"{platform.platform()}"
    zlib_build = getattr(zlib, "ZLIB_VERSION", "?")
    zlib_rt = getattr(zlib, "ZLIB_RUNTIME_VERSION", zlib_build)
    pyver = platform.python_version()
    print(f"## ENV PY={pyver} GZIP=stdlib ZLIB_BUILD={zlib_build} ZLIB_RUNTIME={zlib_rt} PLATFORM={plat}")

    # Cases
    short_payload = b"HELLO WORLD!"  # 12 bytes
    long_payload = b"A" * 10_240

    cases = [
        ("truncated(short)", _gzip_truncated_no_trailer(short_payload, splits=2), len(short_payload)),
        ("truncated(long)", _gzip_truncated_no_trailer(long_payload, splits=3), len(long_payload)),
        ("crc_flip", _flip_crc_byte(_gzip_ok(b"Z" * 256)), 256),
        ("isize_flip", _flip_isize_lsb(_gzip_ok(b"Z" * 256)), 256),
        ("last_byte_missing", _missing_last_byte(_gzip_ok(b"Z" * 256)), 256),
    ]

    rows = []
    ok_std = ok_rec = 0
    mismatches = []

    for name, gz_bytes, expect_len in cases:
        ok1, out_len1, err1 = run_stdlib_gzip(gz_bytes)
        ok2, out_len2, err2 = run_zlib_recover(gz_bytes)

        rows.append({
            "case": name,
            "in": len(gz_bytes),
            "expect": expect_len,
            "g_std": fmt_status(ok1, out_len1, expect_len, err1),
            "g_rec": fmt_status(ok2, out_len2, expect_len, err2),
        })
        ok_std += int(ok1)
        ok_rec += int(ok2)
        if (not ok1) and ok2:
            mismatches.append(name)

    print_table(rows)
    print(f"SUMMARY: stdlib.gzip OK {ok_std}/{len(cases)} | zlib.recover OK {ok_rec}/{len(cases)} | mismatches (gzip→ERR & zlib→OK): {len(mismatches)}")
    if mismatches:
        print("  " + ", ".join(mismatches))


if __name__ == "__main__":
    main()