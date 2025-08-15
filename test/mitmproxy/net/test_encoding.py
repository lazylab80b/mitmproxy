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


class TestGzipMissingTrailer:
    """
    Tests for #7795: gzip missing trailer (CRC32/ISIZE) but body is decodable.
    """
    # test-local helpers / constants
    FROZEN_PAYLOAD = b'TRUNCATED-FROZEN'
    FROZEN_GZ_HEX = (
        "1f8b08000000000002ff0a090af573760c7175d1750bf28f72f503000000ffff"
    )

    @staticmethod
    def _gz_missing_trailer(payload: bytes, splits: int = 1) -> bytes:
        """
        Return a gzip stream missing the final CRC/ISIZE trailer by Z_SYNC_FLUSH.
        """
        buf = io.BytesIO()
        gz = gzip.GzipFile(fileobj=buf, mode="wb", mtime=0)

        n = max(1, splits)
        step = max(1, len(payload) // n)
        for i in range(0, len(payload), step):
            gz.write(payload[i:i+step])
            gz.flush(zlib.Z_SYNC_FLUSH)  # force a block boundary; do not finish the stream
        data = buf.getvalue()  # read before close to avoid writing the trailer
        gz.close()
        return data

    @staticmethod
    def _flip_crc(b: bytes) -> bytes:
        x = bytearray(b); x[-5] ^= 0xFF; return bytes(x)
    
    @staticmethod
    def _flip_isize(b: bytes) -> bytes:
        x = bytearray(b); x[-1] ^= 0xFF; return bytes(x)

    @staticmethod
    def _drop_last_byte(b: bytes) -> bytes:
        return b[:-1]

    def test_gzip_trailer_corrupt_raises(self):
        """Test decoding corrupted gzip data raises"""
        gz = gzip.compress(b"HELLO")
        corruptors = [
            ("crc_flip", self._flip_crc),
            ("isize_flip", self._flip_isize),
            ("last_byte_missing", self._drop_last_byte),
        ]
        for name, fn in corruptors:
            corrupted = fn(gz)
            with pytest.raises(Exception):
                encoding.decode_gzip(corrupted)

    @pytest.mark.xfail(strict=True, reason="#7795: gzip missing trailer should be decodable (pre-fix)")
    def test_gzip_missing_trailer_decodes(self):
        """Test decoding missing trailer(Z_SYNC_FLUSH patter) leniently"""
        cases = [
            ("frozen",  self.FROZEN_PAYLOAD, bytes.fromhex(self.FROZEN_GZ_HEX)),
            ("dynamic", b"TRUNCATED-DYNAMIC-" + b"A"*2048, None),
        ]
        for name, payload, corrupted in cases:
            if corrupted is None:
                corrupted = self._gz_missing_trailer(payload, splits=3)
            out = encoding.decode_gzip(corrupted)
            assert out == payload

# Local-only helper to print FROZEN_GZ_HEX and sanity-check stdlib gzip failure.
# Run this file and paste the printed block into the frozen constants section above.
#   $ uv run python test/mitmproxy/net/test_encoding.py "payload text"
if __name__ == "__main__":
    import sys

    payload = sys.argv[1].encode("utf-8") if len(sys.argv) > 1 else TestGzipMissingTrailer.FROZEN_PAYLOAD
    gz = TestGzipMissingTrailer._gz_missing_trailer(payload)
    h = gz.hex()

    try:
        gzip.GzipFile(fileobj=io.BytesIO(gz)).read()
    except EOFError:
        print("gzip: expected EOFError detected (OK)")
        print()
        IND = "    "
        HEX_PER_LINE = 64
        print(f"{IND}# -- COPY FROM HERE --")
        print(f"{IND}FROZEN_PAYLOAD = {payload!r}")
        print(f"{IND}FROZEN_GZ_HEX = (")
        for i in range(0, len(h), HEX_PER_LINE):
            print(f'{IND}    "{h[i:i+HEX_PER_LINE]}"')
        print(f"{IND})")
        print(f"{IND}# -- TO HERE --")
        print()
    else:
        print("gzip: unexpected success (NG)")
        sys.exit(1)