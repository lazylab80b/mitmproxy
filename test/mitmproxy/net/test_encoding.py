import gzip
import io
import zlib
from unittest import mock

import pytest

from mitmproxy.net import encoding


@pytest.mark.parametrize(
    "encoder",
    [
        "identity",
        "none",
    ],
)
def test_identity(encoder):
    assert b"string" == encoding.decode(b"string", encoder)
    assert b"string" == encoding.encode(b"string", encoder)
    with pytest.raises(ValueError):
        encoding.encode(b"string", "nonexistent encoding")


@pytest.mark.parametrize(
    "encoder",
    [
        "gzip",
        "GZIP",
        "br",
        "deflate",
        "zstd",
    ],
)
def test_encoders(encoder):
    """
    This test is for testing byte->byte encoding/decoding
    """
    assert encoding.decode(None, encoder) is None
    assert encoding.encode(None, encoder) is None

    assert b"" == encoding.decode(b"", encoder)

    assert b"string" == encoding.decode(encoding.encode(b"string", encoder), encoder)

    with pytest.raises(TypeError):
        encoding.encode("string", encoder)

    with pytest.raises(TypeError):
        encoding.decode("string", encoder)
    with pytest.raises(ValueError):
        encoding.decode(b"foobar", encoder)


@pytest.mark.parametrize("encoder", ["utf8", "latin-1"])
def test_encoders_strings(encoder):
    """
    This test is for testing byte->str decoding
    and str->byte encoding
    """
    assert "" == encoding.decode(b"", encoder)

    assert "string" == encoding.decode(encoding.encode("string", encoder), encoder)

    with pytest.raises(TypeError):
        encoding.encode(b"string", encoder)

    with pytest.raises(TypeError):
        encoding.decode("foobar", encoder)


def test_cache():
    decode_gzip = mock.MagicMock()
    decode_gzip.return_value = b"decoded"
    encode_gzip = mock.MagicMock()
    encode_gzip.return_value = b"encoded"

    with mock.patch.dict(encoding.custom_decode, gzip=decode_gzip):
        with mock.patch.dict(encoding.custom_encode, gzip=encode_gzip):
            assert encoding.decode(b"encoded", "gzip") == b"decoded"
            assert decode_gzip.call_count == 1

            # should be cached
            assert encoding.decode(b"encoded", "gzip") == b"decoded"
            assert decode_gzip.call_count == 1

            # the other way around as well
            assert encoding.encode(b"decoded", "gzip") == b"encoded"
            assert encode_gzip.call_count == 0

            # different encoding
            decode_gzip.return_value = b"bar"
            assert encoding.encode(b"decoded", "deflate") != b"decoded"
            assert encode_gzip.call_count == 0

            # This is not in the cache anymore
            assert encoding.encode(b"decoded", "gzip") == b"encoded"
            assert encode_gzip.call_count == 1


def test_zstd():
    FRAME_SIZE = 1024

    # Create payload of 1024b
    test_content = "a" * FRAME_SIZE

    # Compress it, will result a single frame
    single_frame = encoding.encode_zstd(test_content.encode())

    # Concat compressed frame, it'll result two frames, total size of 2048b payload
    two_frames = single_frame + single_frame

    # Uncompressed single frame should have the size of FRAME_SIZE
    assert len(encoding.decode_zstd(single_frame)) == FRAME_SIZE

    # Uncompressed two frames should have the size of FRAME_SIZE * 2
    assert len(encoding.decode_zstd(two_frames)) == FRAME_SIZE * 2

# ---------------------------------------------------------------------------
# Regression for #7795: gzip stream ends with Z_SYNC_FLUSH (00 00 ff ff),
# gzip trailer (CRC32/ISIZE) is missing. We want lenient decode behavior.
# ---------------------------------------------------------------------------

@pytest.mark.xfail(strict=True, reason="#7795: truncated gzip (Z_SYNC_FLUSH, no trailer)")
def test_decode_gzip_syncflush_truncated_trailer_decodes():
    """
    Dynamic synthetic case:
    - Build a truncated gzip stream that ends with Z_SYNC_FLUSH and has no trailer.
    - Use multiple chunks to exercise multiple DEFLATE blocks.
    """
    payload = b"SYNCFLUSH-DYNAMIC-" + (b"A" * 1024) + (b"B" * 1024) + (b"C" * 1024)
    gz = _gz_syncflush_no_trailer_multi(payload, splits=4)
    out = encoding.decode_gzip(gz)
    assert out == payload


# ---- Frozen synthetic hex (not issue-derived) ----
# Fill FROZEN_GZ_HEX by running this file as a script (see __main__ below).
FROZEN_PAYLOAD = b"SYNCFLUSH-FROZEN"
FROZEN_GZ_HEX = (
    "__REPLACE_ME_WITH_GENERATED_HEX__"
)

@pytest.mark.xfail(strict=True, reason="#7795: truncated gzip (frozen synthetic)")
def test_decode_gzip_syncflush_frozen_decodes():
    """Frozen synthetic hex to guard against future library behavior changes."""
    gz = bytes.fromhex(FROZEN_GZ_HEX)
    out = encoding.decode_gzip(gz)
    assert out == FROZEN_PAYLOAD


# ---- Negative/guard cases: ensure we are not overly permissive ----

def test_decode_gzip_crc_byte_flip_raises():
    """Flip one byte in the CRC32 area: decoding should fail."""
    good = gzip.compress(b"X" * 64)
    bad = bytearray(good)
    bad[-5] ^= 0xFF  # damage CRC32
    with pytest.raises(Exception):
        encoding.decode_gzip(bytes(bad))


def test_decode_gzip_isize_byte_flip_raises():
    """Flip one byte in ISIZE (last 4 bytes): decoding should fail."""
    good = gzip.compress(b"HELLO")
    bad = bytearray(good)
    bad[-1] ^= 0xFF  # damage ISIZE LSB
    with pytest.raises(Exception):
        encoding.decode_gzip(bytes(bad))


@pytest.mark.xfail(strict=True, reason="Policy TBD: last-byte-missing may be accepted by zlib")
def test_decode_gzip_last_byte_missing_policy_tbd():
    """Drop the very last byte; keep as xfail to document current uncertainty."""
    good = gzip.compress(b"HELLO")
    encoding.decode_gzip(good[:-1])


# ---- Helpers (test-local) ----

def _gz_syncflush_no_trailer_multi(payload: bytes, splits: int = 3) -> bytes:
    """
    Build a truncated gzip stream with multiple DEFLATE blocks by inserting
    Z_SYNC_FLUSH between chunks. No gzip trailer is written (BFINAL stays 0).
    """
    buf = io.BytesIO()
    gz = gzip.GzipFile(fileobj=buf, mode="wb")

    n = max(1, splits)
    step = max(1, len(payload) // n)
    for i in range(0, len(payload), step):
        gz.write(payload[i : i + step])
        gz.flush(zlib.Z_SYNC_FLUSH)  # forces a block boundary; emits 00 00 ff ff

    data = buf.getvalue()  # capture before closing to avoid writing the trailer
    gz.close()

    # sanity: we should see at least `n` sync-flush markers (impl-dependent but typical for zlib)
    assert data.count(b"\x00\x00\xff\xff") >= n
    return data


# Local-only generator:
# - Emit FROZEN_GZ_HEX as adjacent string literals (copy/paste into the const above).
# - Show that stdlib gzip fails with EOFError on this truncated stream.
# Run:  python test/mitmproxy/net/test_encoding.py
if __name__ == "__main__":
    gz = _gz_syncflush_no_trailer_multi(FROZEN_PAYLOAD, splits=3)
    h = gz.hex()
    print("FROZEN_GZ_HEX = (")
    for i in range(0, len(h), 80):
        print(f'    "{h[i:i+80]}"')
    print(")")

    import io as _io, gzip as _gzip
    try:
        _gzip.GzipFile(fileobj=_io.BytesIO(gz)).read()
        print("gzip: unexpected success")
    except EOFError:
        print("gzip: EOFError (expected)")
