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


# ---- test case for #7795: gzip missing trailer (CRC/ISIZE), but body is decodable. ----
class TestGzipMissingTrailerDecoding:
    # we have three test cases:
    #  1. Frozen synthetic hex: pre-syntesized broken data (guard for future change of gzip lib.)
    #  2. Dynamic synthetic hex: dynamicaly syntesized broken data (for long/multiple blocks)
    #  3. Guard rails: do not get overly permissive on actually corrupted data
    #   a) flip LSB of CRC
    #   b) wrong length (ISIZE field)
    #   c) last byte of trailer missing
    # [NOTE]
    #   Though all three pattern of case-3 should detect data errror, but unfortunatly
    #  zlib cannot detect (3-c). We can accept this case, but mark thils as XFAIL for future.

    @pytest.mark.xfail(strict=True, reason="#7795: gzip missing trailer (frozen synthetic)")
    def test_decode_gzip_missing_trailer_frozen(self):
        gz = bytes.fromhex(FROZEN_GZ_HEX)
        out = encoding.decode_gzip(gz)
        assert out == FROZEN_PAYLOAD

    @pytest.mark.xfail(strict=True, reason="#7795: gzip missing trailer (dynamic)")
    def test_decode_gzip_missing_trailer_dynamic(self):
        payload = b"TRUNCATED-DYNAMIC-" + b"A"*2048
        # splits directive makes multiple chunks so we get multiple blocks
        gz = _gzip_truncated_no_trailer(payload, splits=3)
        out = encoding.decode_gzip(gz)
        assert out == payload

    def test_decode_gzip_crc_byte_flip(self):
        good = gzip.compress(b"X"*64)
        bad = bytearray(good)
        bad[-5] ^= 0xFF  # damage CRC32
        with pytest.raises(Exception):
            encoding.decode_gzip(bytes(bad))

    def test_decode_gzip_isize_byte_flip(self):
        good = gzip.compress(b"HELLO")
        bad = bytearray(good)
        bad[-1] ^= 0xFF  # damage ISIZE LSB
        with pytest.raises(Exception):
            encoding.decode_gzip(bytes(bad))

    @pytest.mark.xfail(strict=True, reason="last-byte-missing may be accepted by zlib")
    def test_decode_gzip_last_byte_missing(self):
        good = gzip.compress(b"HELLO")
        encoding.decode_gzip(good[:-1])


# test-local helpers / constants
FROZEN_PAYLOAD = b'TRUNCATED-FROZEN'
FROZEN_GZ_HEX = (
    "1f8b08008eac996802ff0a090af573760c7175d1750bf28f72f503000000ffff"
)

def _gzip_truncated_no_trailer(payload: bytes, splits: int = 1) -> bytes:
    """
    Build a truncated gzip stream by flushing between chunks so the stream ends
    without a gzip trailer. Commonly leaves 00 00 ff ff markers near boundaries.
    """
    buf = io.BytesIO()
    gz = gzip.GzipFile(fileobj=buf, mode="wb")

    n = max(1, splits)
    step = max(1, len(payload) // n)
    for i in range(0, len(payload), step):
        gz.write(payload[i:i+step])
        gz.flush(zlib.Z_SYNC_FLUSH)  # force a block boundary; we do not finish the stream
    data = buf.getvalue()  # read before close to avoid writing the trailer
    gz.close()
    return data


# Local-only helper to print FROZEN_GZ_HEX and sanity-check stdlib gzip failure.
# Run this file and paste the printed block into the frozen constants section above.
#   $ uv run python test/mitmproxy/net/test_encoding.py "payload text"
if __name__ == "__main__":
    import sys

    payload = sys.argv[1].encode("utf-8") if len(sys.argv) > 1 else FROZEN_PAYLOAD
    gz = _gzip_truncated_no_trailer(payload)
    h = gz.hex()

    try:
        gzip.GzipFile(fileobj=io.BytesIO(gz)).read()
    except EOFError:
        print("gzip: expected EOFError detected (OK)")
        print("# -- COPY FROM HERE --")
        print(f"FROZEN_PAYLOAD = {payload!r}")
        print("FROZEN_GZ_HEX = (")
        for i in range(0, len(h), 80):
            print(f'    "{h[i:i+80]}"')
        print(")")
        print("# -- TO HERE --")
    else:
        print("gzip: unexpected success (NG)")
        sys.exit(1)