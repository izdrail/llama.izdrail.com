#!/bin/sh
set -eu

# Router mode: llama-server starts with no model, auto-discovers every GGUF
# in --models-dir, and loads one on demand (picked via the "model" field on
# /v1/* or the web UI dropdown). --models-max 1 mirrors Ollama behavior on
# the shared 4GB P2000: one resident model, swapped on switch.

API_KEY_ARG=""
if [ -n "${LLAMA_ARG_API_KEY:-}" ]; then
  API_KEY_ARG="--api-key ${LLAMA_ARG_API_KEY}"
fi

# shellcheck disable=SC2086
exec /app/llama-server \
  $API_KEY_ARG \
  --host 0.0.0.0 \
  --port 8080 \
  --models-dir "${LLAMA_ARG_MODELS_DIR:-/models}" \
  --models-max "${LLAMA_ARG_MODELS_MAX:-1}" \
  --ctx-size "${LLAMA_CPP_CONTEXT_SIZE:-4096}" \
  --parallel "${LLAMA_CPP_PARALLEL:-1}" \
  --n-gpu-layers "${LLAMA_CPP_GPU_LAYERS:-10}" \
  --sleep-idle-seconds "${LLAMA_CPP_SLEEP_IDLE_SECONDS:-60}" \
  --jinja
