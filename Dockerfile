# ============================================================
# llama.cpp GPU Server - Optimized for Quadro P2000 (4GB Pascal)
# ============================================================
#
# Thin wrapper around the upstream llama.cpp server image. It pins
# the model defaults the old Ollama image served so the Coolify
# service keeps starting with no command override.

FROM ghcr.io/ggml-org/llama.cpp:server-cuda

# ============================================================
# Model defaults (override at runtime via environment)
# ============================================================
# Same Laravel-tuned GGUF the Ollama image pulled.
ENV LLAMA_CPP_HF_REPO=laravelcompany/laravelmail
ENV LLAMA_CPP_HF_FILE=llama-3.2-3b-instruct.Q4_K_M.gguf
ENV LLAMA_CPP_MODEL_ALIAS=laravelmail

# 4096 gives room for ~2GB models to run 100% on the P2000's 4GB VRAM.
ENV LLAMA_CPP_CONTEXT_SIZE=4096

# Release idle VRAM quickly, mirroring the old OLLAMA_KEEP_ALIVE=60s.
ENV LLAMA_CPP_SLEEP_IDLE_SECONDS=60

# Only needed for a private or gated Hugging Face model.
ENV HF_TOKEN=

EXPOSE 8080

# ============================================================
# The GGUF downloads on first container start, not during image
# build, into the persistent llama-cpp-cache volume. This keeps
# CI image builds independent of transient Hugging Face redirects.
# ============================================================
COPY start-llamacpp.sh /usr/local/bin/start-llamacpp
RUN chmod +x /usr/local/bin/start-llamacpp

ENTRYPOINT ["/usr/local/bin/start-llamacpp"]
