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

# Per-model presets (e.g. embedding mode for nomic-embed-text).
PRESET_ARG=""
if [ -f "${LLAMA_ARG_MODELS_PRESET:-/models/models-preset.ini}" ]; then
  PRESET_ARG="--models-preset ${LLAMA_ARG_MODELS_PRESET:-/models/models-preset.ini}"
fi

# GPU layers: unset means auto-fit each model to free VRAM at load time
# (the 4GB P2000 is shared with Ollama, so a fixed layer count OOMs the
# larger models). Set LLAMA_CPP_GPU_LAYERS to pin instead.
GPU_LAYERS_ARG=""
if [ -n "${LLAMA_CPP_GPU_LAYERS:-}" ]; then
  GPU_LAYERS_ARG="--n-gpu-layers ${LLAMA_CPP_GPU_LAYERS}"
fi

# shellcheck disable=SC2086
exec /app/llama-server \
  $API_KEY_ARG \
  $PRESET_ARG \
  $GPU_LAYERS_ARG \
  --host 0.0.0.0 \
  --port 8080 \
  --models-dir "${LLAMA_ARG_MODELS_DIR:-/models}" \
  --models-max "${LLAMA_ARG_MODELS_MAX:-1}" \
  --ctx-size "${LLAMA_CPP_CONTEXT_SIZE:-4096}" \
  --parallel "${LLAMA_CPP_PARALLEL:-1}" \
  --sleep-idle-seconds "${LLAMA_CPP_SLEEP_IDLE_SECONDS:-60}" \
  --jinja
