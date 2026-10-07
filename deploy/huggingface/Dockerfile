# StudyRAG live demo on a free Hugging Face Space (CPU basic: 2 vCPU, 16 GB RAM).
# The LLM comes from Groq's free API (GROQ_API_KEY is a Space *secret*); embeddings and the
# re-ranker run on the CPU inside the container. Built by deploy/push_to_space.py.
FROM python:3.11-slim

# DejaVu Sans has Arabic glyphs (PDF exports)
RUN apt-get update && apt-get install -y --no-install-recommends fonts-dejavu-core \
    && rm -rf /var/lib/apt/lists/*

# Spaces run the container as user 1000
RUN useradd -m -u 1000 user
USER user
ENV HOME=/home/user PATH=/home/user/.local/bin:$PATH PYTHONUNBUFFERED=1 PIP_NO_CACHE_DIR=1 \
    MODEL_CACHE=/home/user/models HF_HOME=/home/user/models \
    CHROMA_DIR=/home/user/data/chroma DB_PATH=/tmp/studyrag/progress.db \
    DEMO_MODE=1 LLM_PROVIDER=groq RETRIEVAL_MODE=hybrid+rerank
WORKDIR /home/user/app

# CPU-only PyTorch (~2 GB smaller than the CUDA build)
RUN pip install --user torch --index-url https://download.pytorch.org/whl/cpu
COPY --chown=user requirements.txt .
RUN pip install --user -r requirements.txt

# Bake both models into the image so the first visitor doesn't wait for a ~900 MB download
RUN python -c "from sentence_transformers import SentenceTransformer, CrossEncoder; \
SentenceTransformer('intfloat/multilingual-e5-small'); CrossEncoder('cross-encoder/mmarco-mMiniLMv2-L12-H384-v1')"

COPY --chown=user . .
# Index the sample lectures at build time, so the demo opens ready to use
RUN python -c "from samples.make_benchmark_corpus import corpus; from core.ingest import ingest_file; \
[ingest_file(p.read_bytes(), p.name) for p in corpus()]"

EXPOSE 8501
CMD ["streamlit", "run", "app.py", "--server.address=0.0.0.0", "--server.port=8501", "--server.headless=true"]
