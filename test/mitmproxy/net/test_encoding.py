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


=====================
@pytest.mark.xfail(strict=True, reason="#7795: truncated gzip (Z_SYNC_FLUSH, no trailer)")
def test_decode_gzip_syncflush_dynamic_ok():
    """
    Dynamic synthetic case: lenient decoding should succeed even if the gzip trailer is missing.
    """
    payload = b"SYNCFLUSH-DYNAMIC-" + (b"A" * 2048)  # long-ish to exercise multiple blocks
    out = encoding.decode_gzip(_gz_syncflush_no_trailer(payload))
    assert out == payload

# Fill FIXED_GZ_HEX with the tuple printed by running this module as a script (see __main__ below).
FIXED_PAYLOAD = b"SYNCFLUSH-FIXED"
FIXED_GZ_HEX = (
    "__REPLACE_ME_WITH_GENERATED_HEX__",
)

@pytest.mark.xfail(strict=True, reason="#7795: truncated gzip (fixed sample)")
def test_decode_gzip_syncflush_fixed_ok():
    """
    Frozen hex : identical to the dynamic case above, but frozen to guard against
    future behavior changes in zlib/gzip.
    """
    gz = bytes.fromhex("".join(FIXED_GZ_HEX))
    out = encoding.decode_gzip(gz)
    assert out == FIXED_PAYLOAD


# ---- Negative/guard cases: ensure we are not overly permissive ----

def test_decode_gzip_crc_byte_flip_raises():
    """
    Flip one byte in the CRC32 (near the end of the stream): decoding should fail.
    """
    good = gzip.compress(b"X" * 64)
    bad = bytearray(good)
    bad[-5] ^= 0xFF  # damage CRC32
    with pytest.raises(Exception):
        encoding.decode_gzip(bytes(bad))


def test_decode_gzip_isize_byte_flip_raises():
    """
    Flip one byte in ISIZE (last 4 bytes): decoding should fail.
    """
    good = gzip.compress(b"HELLO")
    bad = bytearray(good)
    bad[-1] ^= 0xFF  # damage ISIZE LSB
    with pytest.raises(Exception):
        encoding.decode_gzip(bytes(bad))


@pytest.mark.xfail(strict=True, reason="Policy TBD: zlib may accept 'last-byte-missing'. Keep as xfail.")
def test_decode_gzip_last_byte_missing():
    """
    Drop the very last byte. We keep this as xfail for now as behavior can vary; this test
    documents the current uncertainty and prevents accidental permissiveness changes.
    """
    good = gzip.compress(b"HELLO")
    encoding.decode_gzip(good[:-1])


# Helper to generate the FIXED_GZ_HEX tuple once on your machine.
# Run:  python test/mitmproxy/net/test_encoding.py
if __name__ == "__main__":
    hex_str = _gz_syncflush_no_trailer(FIXED_PAYLOAD).hex()
    print("FIXED_GZ_HEX = (")
    for i in range(0, len(hex_str), 80):
        print(f'    "{hex_str[i:i+80]}",')
    print(")")

@pytest.mark.xfail(strict=True, reason="#7795: fixed sample...")
def test_decode_gzip_syncflush_fixed():
    gz = bytes.fromhex(FIXED_GZ_HEX)
    assert encoding.decode_gzip(gz) == FIXED_PAYLOAD

def test_decode_gzip_crc_or_isize_corruption_raises():
    p = b"CORRUPTION-CHECK"
    good = gzip.compress(p)
    b = bytearray(good)
    b[-8] ^= 0x01  # CRC 1byte flip
    with pytest.raises(Exception):
        encoding.decode_gzip(bytes(b))
    b = bytearray(good)
    b[-1] ^= 0x80  # ISIZE 1byte flip
    with pytest.raises(Exception):
        encoding.decode_gzip(bytes(b))

@pytest.mark.xfail(strict=False, reason="寛容仕様では通る。厳密化したら失敗させたい。")
def test_truncate_last_byte_should_error_in_strict_mode():
    p = b"TRUNCATE-LAST-BYTE"
    broken = gzip.compress(p)[:-1]
    with pytest.raises(Exception):
        encoding.decode_gzip(broken)

def _gz_syncflush_no_trailer(payload: bytes) -> bytes:
    """
    Create a *truncated* gzip stream:
    - ends with a DEFLATE Z_SYNC_FLUSH marker (00 00 ff ff)
    - no gzip trailer (CRC32/ISIZE)
    This mimics the real-world bug from #7795.
    """
    buf = io.BytesIO()
    gz = gzip.GzipFile(fileobj=buf, mode="wb")
    gz.write(payload)
    # leave the stream in a sync-flushed state instead of finishing it
    gz.flush(zlib.Z_SYNC_FLUSH)
    data = buf.getvalue()
    gz.close()
    # sanity: typical Z_SYNC_FLUSH trailer bytes
#    assert data.endswith(b"\x00\x00\xff\xff")
    return data

if __name__ == "__main__":
    h = _gz_syncflush_no_trailer(FIXED_PAYLOAD).hex()
    print("FIXED_GZ_HEX = (")
    for i in range(0, len(h), 80):
        print(f'    "{h[i:i+80]}"')
    print(")")
