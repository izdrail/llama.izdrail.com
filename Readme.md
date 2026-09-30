# llama.izdrail.com

GPU language-model server running **llama.cpp** (OpenAI-compatible API) on port `8080`, configured for the Quadro P2000's 4GB VRAM. The model cache is a persistent Docker volume, so redeploying does not download the model again.

Forked from [intelligence.izdrail.com](https://github.com/izdrail/intelligence.izdrail.com) with the Ollama runtime removed - llama.cpp is the only engine here. The original repo (serving `ai.izdrail.com`) stays on Ollama, untouched.

## Requirements

- Docker Engine with the Compose plugin
- NVIDIA Container Toolkit (`docker run --gpus all ...` must work)
- `curl` for health checks

## Start

```bash
cp .env.example .env
make run
```

## API

llama.cpp exposes an OpenAI-compatible API at `http://HOST:8080/v1`:

```bash
curl http://localhost:8080/v1/chat/completions \
  -H 'Content-Type: application/json' \
  -d '{
    "model": "qwen3-4b",
    "messages": [{"role": "user", "content": "Explain Laravel queues briefly."}],
    "stream": false
  }'
```

Point an OpenAI-compatible client at:

```text
http://HOST:8080/v1
```

Health check: `curl http://localhost:8080/health`. Model list: `curl http://localhost:8080/v1/models`.

## Web UIs

- `https://llama.izdrail.com/` serves llama-server's built-in chat web UI (proxied through the tracker).
- `https://llama.izdrail.com/track` serves the tracker query-log UI (login required).

**Endpoint mapping from Ollama:** llama.cpp does not serve the Ollama-native API. Callers must switch:

| Ollama | llama.cpp |
| --- | --- |
| `POST /api/chat` | `POST /v1/chat/completions` |
| `POST /api/generate` | `POST /v1/completions` |
| `POST /api/embed` / `/api/embeddings` | `POST /v1/embeddings` |
| `GET /api/tags` | `GET /v1/models` |

Clients already using Ollama's OpenAI-compatible `/v1` surface work unchanged, except the model name is now the alias `laravelmail`.

The default model is **Qwen3-4B** (`unsloth/Qwen3-4B-GGUF`, `Qwen3-4B-Q4_K_M.gguf`, ~2.5GB - fits the P2000's 4GB VRAM with room for the KV cache at ctx 4096), served under the alias `qwen3-4b`. The first start downloads it into the persistent `llama-cpp-cache` volume. Swapping models is a three-variable change (`LLAMA_CPP_HF_REPO` / `LLAMA_CPP_HF_FILE` / `LLAMA_CPP_MODEL_ALIAS`); `.env.example` has the Laravel-tuned `laravelcompany/laravelmail` GGUF as a ready alternative. llama-server loads one model per container; run a second container on another port if two models are needed at once.

## Configuration

Copy `.env.example` to `.env` and change the port or Hugging Face model if needed. For a private model, set `HF_TOKEN` in `.env` rather than committing it.

Important settings:

| Variable | Default | Purpose |
| --- | --- | --- |
| `LLAMA_CPP_PORT` | `8080` | Host port |
| `LLAMA_CPP_HF_REPO` | `unsloth/Qwen3-4B-GGUF` | Hugging Face repository |
| `LLAMA_CPP_HF_FILE` | `Qwen3-4B-Q4_K_M.gguf` | GGUF file |
| `LLAMA_CPP_MODEL_ALIAS` | `qwen3-4b` | Model name exposed by the API |
| `LLAMA_CPP_CONTEXT_SIZE` | `4096` | Context window |
| `LLAMA_CPP_SLEEP_IDLE_SECONDS` | `60` | Release idle VRAM after N seconds |

## Operations

```bash
make build          # build the llama.cpp wrapper image
make run            # start the server
make status         # show the container
make logs           # follow logs
make validate       # validate Compose configuration
make test           # health check
make test-models    # list served models
make stop           # stop the server
```

### Model downloads

The image does not download multi-gigabyte model files while being built. On first container start llama-server downloads the configured GGUF into the persistent `llama-cpp-cache` volume; later starts reuse that cache. Override the model with `LLAMA_CPP_HF_REPO` / `LLAMA_CPP_HF_FILE` / `LLAMA_CPP_MODEL_ALIAS` if needed. This keeps CI image builds independent of transient Hugging Face CDN redirects.
