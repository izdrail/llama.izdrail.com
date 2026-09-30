#!/bin/sh
set -eu

# Defaults match the Dockerfile ENV; compose.yaml can override them.
API_KEY_ARG=""
if [ -n "${LLAMA_ARG_API_KEY:-}" ]; then
  API_KEY_ARG="--api-key ${LLAMA_ARG_API_KEY}"
fi

# shellcheck disable=SC2086
exec /app/llama-server \
  $API_KEY_ARG \
  --host 0.0.0.0 \
  --port 8080 \
  --hf-repo "${LLAMA_CPP_HF_REPO:-unsloth/Qwen3-4B-GGUF}" \
  --hf-file "${LLAMA_CPP_HF_FILE:-Qwen3-4B-Q4_K_M.gguf}" \
  --alias "${LLAMA_CPP_MODEL_ALIAS:-qwen3-4b}" \
  --ctx-size "${LLAMA_CPP_CONTEXT_SIZE:-4096}" \
  --parallel "${LLAMA_CPP_PARALLEL:-1}" \
  --n-gpu-layers "${LLAMA_CPP_GPU_LAYERS:-999}" \
  --sleep-idle-seconds "${LLAMA_CPP_SLEEP_IDLE_SECONDS:-60}"
