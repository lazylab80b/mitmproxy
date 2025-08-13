#!/usr/bin/env bash
set -euo pipefail

VERSIONS=(
  3.8-slim
  3.9-slim
  3.10-slim
  3.11-slim
  3.12-slim
  3.13-slim
)

mkdir -p logs

for V in "${VERSIONS[@]}"; do
  TAG="gztest_${V//[^A-Za-z0-9]/_}"
  OUT="logs/out_${V}.txt"
  echo "==> Build ${V} (tag: ${TAG})"
  docker build --build-arg PY_VER="${V}" -t "${TAG}" .

  echo "==> Run ${V} | tee ${OUT}"
  docker run --rm "${TAG}" | tee "${OUT}"
  echo
done

if command -v python3 >/dev/null 2>&1; then PY=python3; else PY=python; fi
"$PY" summarize_results.py --md logs/summary.md logs/out_*.txt
