# The upstream llama.cpp server plus a small Runpod queue worker that forwards jobs to it.
FROM ghcr.io/ggml-org/llama.cpp:server-cuda13
RUN apt-get update && apt-get install -y --no-install-recommends python3-venv \
    && rm -rf /var/lib/apt/lists/* \
    && python3 -m venv /opt/venv \
    && /opt/venv/bin/pip install --no-cache-dir runpod
COPY worker /worker
ENTRYPOINT ["/opt/venv/bin/python", "/worker/main.py"]
