# syntax=docker/dockerfile:1
# 使いたい Python ベースイメージを引数で切り替え（例: 3.8-slim, 3.13-slim）
ARG PY_VER=3.13-slim
FROM python:${PY_VER}

WORKDIR /app
COPY test_gzip_variants.py /app/test_gzip_variants.py

# 追加パッケージは不要（stdlibのみ）
CMD ["python", "/app/test_gzip_variants.py"]