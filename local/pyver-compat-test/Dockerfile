# --- build args ---
ARG PY_VER=3.13-slim
ARG ZV=stdlib   # "stdlib" or a specific zlib version like 1.2.11 / 1.2.13 / 1.3.1

# --- zlib builder stage (may be no-op if ZV=stdlib) ---
FROM python:${PY_VER} AS zlibbuild
ARG ZV
SHELL ["/bin/bash", "-euxo", "pipefail", "-c"]

# Always create an output stash so COPY never fails even if empty.
ENV ZLIB_OUT=/opt/zlib-out
RUN mkdir -p "${ZLIB_OUT}"

# If ZV != stdlib, build that zlib and stash built artifacts into ZLIB_OUT.
RUN if [[ "${ZV}" != "stdlib" ]]; then \
      # Safety: single version only
      if [[ "${ZV}" == *" "* ]]; then \
        echo "ERROR: ZV must be a single version (e.g. 1.2.11), not a space-separated list." >&2; \
        exit 2; \
      fi; \
      apt-get update && \
      apt-get install -y --no-install-recommends build-essential ca-certificates curl && \
      rm -rf /var/lib/apt/lists/* && \
      mkdir -p /tmp/z && \
      ( \
        curl -fsSL "https://zlib.net/fossils/zlib-${ZV}.tar.gz" -o /tmp/z.tgz || \
        curl -fsSL "https://zlib.net/zlib-${ZV}.tar.gz" -o /tmp/z.tgz \
      ) && \
      tar -xzf /tmp/z.tgz -C /tmp/z --strip-components=1 && \
      cd /tmp/z && ./configure --prefix=/usr/local && make -j"$(nproc)" && make install && \
      # Stash built libs for the final image:
      cp -a /usr/local/lib/libz.so* "${ZLIB_OUT}/" 2>/dev/null || true; \
    fi

# --- final runtime stage ---
FROM python:${PY_VER}
ARG ZV
WORKDIR /app

# Overlay custom zlib (if any). This directory exists even if empty.
COPY --from=zlibbuild /opt/zlib-out/ /usr/local/lib/
ENV LD_LIBRARY_PATH=/usr/local/lib:${LD_LIBRARY_PATH}
RUN ldconfig || true

# Your test script(s)
COPY test_gzip_variants.py /app/test_gzip_variants.py
CMD ["python", "/app/test_gzip_variants.py"]