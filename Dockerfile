FROM python:3.11-slim

ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PIP_NO_CACHE_DIR=1 \
    HF_HOME=/app/.hf \
    CHROMA_DIR=/app/data/chroma \
    # Ollama running on the host machine (Docker Desktop). Override with -e OLLAMA_HOST=...
    OLLAMA_HOST=http://host.docker.internal:11434

# DejaVu Sans has Arabic glyphs -> used by the PDF quiz export
RUN apt-get update && apt-get install -y --no-install-recommends fonts-dejavu-core \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY requirements.txt .
RUN pip install -r requirements.txt   # default int8 ONNX backend: no PyTorch

COPY . .
# Bake the embedding model and the re-ranker (int8 ONNX, ~230 MB) into the image so the first question is fast
RUN python -c "from core import ingest, retriever; ingest.get_embedder(); retriever._load_reranker()"
EXPOSE 8501
VOLUME ["/app/data"]
HEALTHCHECK CMD python -c "import urllib.request; urllib.request.urlopen('http://localhost:8501/_stcore/health')"
CMD ["streamlit", "run", "app.py", "--server.address=0.0.0.0", "--server.port=8501"]
