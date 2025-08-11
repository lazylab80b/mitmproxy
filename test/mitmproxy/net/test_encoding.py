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
    @pytest.mark.xfail(strict=True, reason="#7795: gzip missing trailer (dynamic)")
    def test_decode_gzip_missing_trailer_dynamic_decodes(self):
        # dynamic synthetic: multiple chunks so we get multiple blocks
        payload = b"TRUNCATED-DYNAMIC-" + b"A"*1024 + b"B"*1024 + b"C"*1024
        gz = _gzip_truncated_no_trailer(payload, splits=4)
        out = encoding.decode_gzip(gz)
        assert out == payload

    # ---- Frozen synthetic hex (not issue-derived). Fill by running __main__ below. ----
    @pytest.mark.xfail(strict=True, reason="#7795: gzip missing trailer (frozen synthetic)")
    def test_decode_gzip_missing_trailer_frozen_decodes(self):
        gz = bytes.fromhex(FROZEN_GZ_HEX)
        out = encoding.decode_gzip(gz)
        assert out == FROZEN_PAYLOAD

    # ---- Guard rails: do not get overly permissive on actually corrupted data ----
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

    @pytest.mark.xfail(strict=True, reason="Policy TBD: last-byte-missing may be accepted by zlib")
    def test_decode_gzip_last_byte_missing(self):
        good = gzip.compress(b"HELLO")
        encoding.decode_gzip(good[:-1])


# test-local helpers / constants (kept nearby for readability)

FROZEN_PAYLOAD = b"TRUNCATED-FROZEN"
FROZEN_GZ_HEX = (
    "__REPLACE_ME_WITH_GENERATED_HEX__"
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
    assert data  # sanity
    return data


# Local-only helper to print FROZEN_GZ_HEX and sanity-check stdlib gzip failure.
# Run: uv run test/mitmproxy/net/test_encoding.py
# copy outputed strings into constant definition section of this file.
if __name__ == "__main__":
    gz = _gzip_truncated_no_trailer(FROZEN_PAYLOAD)
    h = gz.hex()
    print("FROZEN_GZ_HEX = (")
    for i in range(0, len(h), 80):
        print(f'    "{h[i:i+80]}"')
    print(")")

    try:
        gzip.GzipFile(fileobj=io.BytesIO(gz)).read()
        print("gzip: unexpected success (NG)")
    except EOFError:
        print("gzip: expected EOFError (OK)")