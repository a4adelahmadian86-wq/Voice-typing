FROM python:3.10-slim-bookworm

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    HF_HOME=/models/huggingface \
    COQUI_HOME=/models/coqui \
    PORT=8000

RUN apt-get update && apt-get install -y --no-install-recommends \
    ffmpeg espeak-ng libsndfile1 build-essential gcc g++ \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY requirements.txt .
RUN pip install --upgrade pip setuptools wheel && pip install -r requirements.txt

COPY app ./app
RUN useradd --create-home --uid 10001 farast && mkdir -p /models && chown -R farast:farast /app /models
USER farast

EXPOSE 8000
CMD ["sh", "-c", "uvicorn app.main:app --host 0.0.0.0 --port ${PORT} --workers 1"]
