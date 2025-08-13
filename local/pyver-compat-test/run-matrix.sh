#!/usr/bin/env bash
set -euo pipefail

# 使い方:
#   MODE=zlib PY_VER=3.13-slim ZLIB_VERSIONS="1.2.8 1.2.11 1.2.13 1.3.1" ./run-matrix.sh
#   MODE=python PY_VERSIONS="3.8-slim 3.9-slim ..." ./run-matrix.sh
#   MODE=both PY_VERSIONS="..." ZLIB_VERSIONS="..." ./run-matrix.sh

MODE="${MODE:-zlib}"
PY_VER_DEFAULT="${PY_VER:-3.13-slim}"
PY_VERSIONS="${PY_VERSIONS:-3.8-slim 3.9-slim 3.10-slim 3.11-slim 3.12-slim 3.13-slim}"
ZLIB_VERSIONS="${ZLIB_VERSIONS:-stdlib 1.2.8 1.2.11 1.2.13 1.3.1}"  # "stdlib" or space-separated versions

mkdir -p logs

build_and_run () {
  local py="$1"; local zv="$2"
  local tag="gztest_${py//[^A-Za-z0-9]/_}_z_${zv//[^A-Za-z0-9]/_}"
  local out="logs/out_${py}_z_${zv}.txt"
  echo "==> Build PY=${py} ZV=${zv} (tag: ${tag})"
  docker build --build-arg PY_VER="${py}" --build-arg ZV="${zv}" -t "${tag}" .
  echo "==> Run ${tag} | tee ${out}"
  docker run --rm "${tag}" | tee "${out}"
  echo
}

case "${MODE}" in
  zlib)
    for zv in ${ZLIB_VERSIONS}; do
      build_and_run "${PY_VER_DEFAULT}" "${zv}"
    done
    ;;
  python)
    for py in ${PY_VERSIONS}; do
      build_and_run "${py}" "stdlib"
    done
    ;;
  both)
    for py in ${PY_VERSIONS}; do
      for zv in ${ZLIB_VERSIONS}; do
        build_and_run "${py}" "${zv}"
      done
    done
    ;;
  *)
    echo "Unknown MODE=${MODE} (use zlib|python|both)" >&2; exit 1;;
esac

# 解析（必要なら）
if command -v python3 >/dev/null 2>&1; then
  python3 summarize_results.py  logs/out_*.txt || true
fi