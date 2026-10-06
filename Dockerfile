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
# CPU-only PyTorch keeps the image ~2 GB smaller than the default CUDA wheels
RUN pip install torch --index-url https://download.pytorch.org/whl/cpu
COPY requirements.txt .
RUN pip install -r requirements.txt

# Bake the embedding model into the image so the first upload is fast
RUN python -c "from sentence_transformers import SentenceTransformer; SentenceTransformer('intfloat/multilingual-e5-small')"

COPY . .
EXPOSE 8501
VOLUME ["/app/data"]
HEALTHCHECK CMD python -c "import urllib.request; urllib.request.urlopen('http://localhost:8501/_stcore/health')"
CMD ["streamlit", "run", "app.py", "--server.address=0.0.0.0", "--server.port=8501"]
