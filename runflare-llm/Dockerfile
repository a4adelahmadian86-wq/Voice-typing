FROM ghcr.io/ggml-org/llama.cpp:server

ENV PORT=8000 \
    LLAMA_MODEL_REPO=ggml-org/Qwen3-1.7B-GGUF \
    LLAMA_MODEL_QUANT=Q4_K_M \
    LLAMA_CTX_SIZE=2048 \
    LLAMA_THREADS=1

# Override the upstream image entrypoint so Runflare environment variables can be expanded.
ENTRYPOINT ["/bin/sh", "-c"]
CMD ["exec /app/llama-server -hf \"${LLAMA_MODEL_REPO}:${LLAMA_MODEL_QUANT}\" --host 0.0.0.0 --port \"${PORT:-8000}\" --ctx-size \"${LLAMA_CTX_SIZE:-2048}\" --threads \"${LLAMA_THREADS:-1}\" --threads-batch \"${LLAMA_THREADS:-1}\" --parallel 1 --n-predict 160 --api-key \"${LLAMA_API_KEY:?Set LLAMA_API_KEY in Runflare}\" --reasoning off --no-webui"]