# StudyRAG demo as a Hugging Face Docker Space.
# NOTE (Oct 2026): Hugging Face now requires a PRO subscription for Docker/Gradio Spaces on CPU hardware,
# so the free deployment uses Streamlit Community Cloud instead (see README). This file is kept for
# anyone with PRO. The LLM comes from Groq (GROQ_API_KEY is a Space *secret*).
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
    DEMO_MODE=1 LLM_PROVIDER=groq MODEL_BACKEND=onnx RETRIEVAL_MODE=hybrid+rerank
WORKDIR /home/user/app

COPY --chown=user requirements.txt .
RUN pip install --user -r requirements.txt

COPY --chown=user . .
# Bake the int8 ONNX models and the sample-lecture index into the image
RUN python -c "from core import ingest, retriever; ingest.get_embedder(); retriever._load_reranker()" && \
    python -c "from samples.make_benchmark_corpus import corpus; from core.ingest import ingest_file; \
[ingest_file(p.read_bytes(), p.name) for p in corpus()]"

EXPOSE 8501
CMD ["streamlit", "run", "app.py", "--server.address=0.0.0.0", "--server.port=8501", "--server.headless=true"]
