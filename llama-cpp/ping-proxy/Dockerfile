# The upstream llama.cpp server plus a small nginx in front, so Runpod's load balancer finds the /ping route it expects.
FROM ghcr.io/ggml-org/llama.cpp:server-cuda13
RUN apt-get update && apt-get install -y --no-install-recommends nginx && rm -rf /var/lib/apt/lists/*
COPY nginx.conf /etc/nginx/nginx.conf
COPY start.sh /start.sh
RUN chmod +x /start.sh
ENTRYPOINT ["/start.sh"]
