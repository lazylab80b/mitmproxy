import io
import sys
import gzip
import zlib
import binascii
from typing import Optional

def gzip_truncated_no_trailer(payload: bytes, splits: int = 3) -> bytes:
    buf = io.BytesIO()
    gz = gzip.GzipFile(fileobj=buf, mode="wb", mtime=0)
    step = max(1, len(payload) // max(1, splits))
    for i in range(0, len(payload), step):
        gz.write(payload[i:i+step])
        gz.flush(zlib.Z_SYNC_FLUSH)
    data = buf.getvalue()
    gz.close()
    return data

def stdlib_gzip_read(data: bytes):
    with gzip.GzipFile(fileobj=io.BytesIO(data)) as f:
        return f.read()

def zlib_recover_gzip(data: bytes) -> bytes:
    decomp = zlib.decompressobj(16 + zlib.MAX_WBITS)
    return decomp.decompress(data)

def flip_crc_byte(gz_bytes: bytes) -> bytes:
    b = bytearray(gz_bytes)
    b[-5] ^= 0xFF
    return bytes(b)

def flip_isize_byte(gz_bytes: bytes) -> bytes:
    b = bytearray(gz_bytes)
    b[-1] ^= 0xFF
    return bytes(b)

def fmt_status(ok: bool, out_len: Optional[int], expect_len: int, err_name: Optional[str]) -> str:
    if ok:
        return f"OK({out_len}/{expect_len})"
    else:
        return f"ERR({err_name})"

def main():
    py = sys.version.split()[0]
    print(f"Python {py} | zlib {zlib.ZLIB_VERSION}")

    payload_short = b"HELLO WORLD!"
    payload_long  = (b"SYNCFLUSH-" * 1024)  # 10KB超・複数ブロック狙い
    cases = [
        ("truncated(short)", gzip_truncated_no_trailer(payload_short, splits=2), len(payload_short)),
        ("truncated(long)",  gzip_truncated_no_trailer(payload_long,  splits=5), len(payload_long)),
    ]
    gz_ok = gzip.compress(b"A"*256, mtime=0)
    cases += [
        ("crc_flip",           flip_crc_byte(gz_ok),       256),
        ("isize_flip",         flip_isize_byte(gz_ok),     256),
        ("last_byte_missing",  gz_ok[:-1],                 256),
    ]

    rows = []
    g_ok = z_ok = mismatches = 0

    print("### BEGIN RESULTS")
    print("| case               | in  | expect | stdlib.gzip           | zlib.recover        |")
    print("|--------------------|-----:|-------:|-----------------------|---------------------|")

    for name, data, expect_len in cases:
        # stdlib gzip
        g_status = ""
        try:
            gout = stdlib_gzip_read(data)
            g_status = fmt_status(True, len(gout), expect_len, None)
            g_ok += 1
        except Exception as e:
            g_status = fmt_status(False, None, expect_len, type(e).__name__)

        # zlib recover
        z_status = ""
        try:
            zout = zlib_recover_gzip(data)
            z_status = fmt_status(True, len(zout), expect_len, None)
            z_ok += 1
        except Exception as e:
            z_status = fmt_status(False, None, expect_len, type(e).__name__)

        if g_status.startswith("ERR(") and z_status.startswith("OK("):
            mismatches += 1

        print(f"| {name:18} | {len(data):3d} | {expect_len:6d} | {g_status:21} | {z_status:19} |")

    print("### END RESULTS")
    total = len(cases)
    print(f"SUMMARY: stdlib.gzip OK {g_ok}/{total} | zlib.recover OK {z_ok}/{total} | mismatches (gzip→ERR & zlib→OK): {mismatches}")

if __name__ == "__main__":
    main()