#!/usr/bin/env sh
set -eu
LIB=""
case "${ZLIB_VARIANT:-system}" in
  zlib-1.2.11) LIB="/opt/zlib-1.2.11/lib" ;;
  zlib-1.2.13) LIB="/opt/zlib-1.2.13/lib" ;;
  zlib-1.3.1)  LIB="/opt/zlib-1.3.1/lib" ;;
  zlib-ng)     LIB="/opt/zlib-ng/lib" ;;
  system)      LIB="" ;;
  *) echo "Unknown ZLIB_VARIANT=$ZLIB_VARIANT" >&2; exit 2 ;;
esac
[ -n "$LIB" ] && export LD_LIBRARY_PATH="$LIB${LD_LIBRARY_PATH:+:$LD_LIBRARY_PATH}"
exec python /app/test_gzip_variants.py