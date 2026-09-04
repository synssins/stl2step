# Build context is the stl2step repo root; this file lives at web/Dockerfile.
#   docker compose build   (compose sets context + dockerfile)
#   docker build -f web/Dockerfile .
# ---------- stage 1: build the stl2step CLI ----------
FROM ubuntu:24.04 AS builder
ENV DEBIAN_FRONTEND=noninteractive
RUN apt-get update && apt-get install -y --no-install-recommends \
        build-essential cmake patch \
        libocct-foundation-dev libocct-modeling-data-dev \
        libocct-modeling-algorithms-dev libocct-data-exchange-dev \
        libtbb-dev \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /src/stl2step
COPY CMakeLists.txt CMakePresets.json ./
COPY cmake/ cmake/
COPY include/ include/
COPY src/ src/
COPY web/occt-link.patch /src/
# Upstream CMake omits TKPrim/TKBO; Ubuntu's OCCT does not pull them transitively.
RUN patch -p1 < /src/occt-link.patch \
    && cmake -S . -B build -DCMAKE_BUILD_TYPE=Release -DSTL2STEP_BUILD_TESTS=OFF \
    && cmake --build build -j"$(nproc)" \
    && strip build/stl2step

# ---------- stage 2: runtime ----------
FROM ubuntu:24.04
ENV DEBIAN_FRONTEND=noninteractive PYTHONUNBUFFERED=1 PIP_NO_CACHE_DIR=1
RUN apt-get update && apt-get install -y --no-install-recommends \
        python3 python3-pip \
        libocct-foundation-7.6t64 libocct-modeling-data-7.6t64 \
        libocct-modeling-algorithms-7.6t64 libocct-data-exchange-7.6t64 \
        libtbb12 libtbbmalloc2 \
    && rm -rf /var/lib/apt/lists/* \
    && useradd -r -u 1000 -d /app -s /usr/sbin/nologin app

COPY --from=builder /src/stl2step/build/stl2step /usr/local/bin/stl2step
COPY web/app/requirements.txt /app/requirements.txt
RUN pip3 install --break-system-packages -r /app/requirements.txt
COPY web/app/ /app/
RUN mkdir -p /data && chown -R app:app /app /data

USER app
WORKDIR /app
ENV STL2STEP_BIN=/usr/local/bin/stl2step \
    DATA_DIR=/data \
    MAX_UPLOAD_MB=200 \
    JOB_TIMEOUT_SEC=600 \
    CONCURRENCY=1 \
    RETAIN_DAYS=30
EXPOSE 8080
HEALTHCHECK --interval=30s --timeout=5s CMD python3 -c "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8080/api/health').status==200 else 1)"
CMD ["python3", "-m", "uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8080"]
