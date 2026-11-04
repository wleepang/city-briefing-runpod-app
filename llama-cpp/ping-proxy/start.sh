#!/bin/sh
# nginx takes port 8080; llama-server moves to 8081 (the options given last win over any passed in).
nginx
exec /app/llama-server "$@" --host 127.0.0.1 --port 8081
