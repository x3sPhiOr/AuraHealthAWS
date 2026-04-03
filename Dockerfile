# ── Aura Health — production image for AWS App Runner ─────────────────────────
#
# Build:  docker build -t aura-health .
# Run:    docker run -p 8000:8000 --env-file .env aura-health
#
# The embedding model (all-MiniLM-L6-v2, ~90 MB) and spaCy language model are
# baked into the image at build time so there is no cold-start download delay.
# ──────────────────────────────────────────────────────────────────────────────

FROM python:3.11-slim

# ── System dependencies ───────────────────────────────────────────────────────
# build-essential: needed by some Python C extensions (faiss-cpu, tokenizers)
# curl:            useful for health-check scripting / debugging
RUN apt-get update && \
    apt-get install -y --no-install-recommends \
        build-essential \
        curl \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# ── Python dependencies ───────────────────────────────────────────────────────
# Copy requirements first to maximise layer cache reuse.
COPY requirements-prod.txt .
RUN pip install --no-cache-dir --upgrade pip && \
    pip install --no-cache-dir -r requirements-prod.txt

# ── Pre-bake NLP models ───────────────────────────────────────────────────────
# Download at build time so container starts without a network fetch.
# sentence-transformers (~90 MB) — used for FAISS embeddings
RUN python -c "from sentence_transformers import SentenceTransformer; SentenceTransformer('sentence-transformers/all-MiniLM-L6-v2')"

# spaCy en_core_web_lg — required by Presidio AnalyzerEngine
RUN python -m spacy download en_core_web_lg

# ── Application source ────────────────────────────────────────────────────────
# Copy governance modules and FDA data directory before app.py so that
# changes to app.py alone don't invalidate those (usually stable) layers.
COPY governance/ governance/
COPY data/       data/
COPY app_auth.py .
COPY app.py      .

# Create writable output directories (audit logs, SOAP exports)
# For persistent storage across restarts, mount these as EFS volumes in App Runner.
RUN mkdir -p aura_outputs audit_logs

# ── Runtime configuration ─────────────────────────────────────────────────────
# All secrets must be injected as environment variables — never committed to the image.
# Required at runtime:
#   AWS_DEFAULT_REGION         (e.g. ap-southeast-1)
#   BEDROCK_MODEL              (e.g. anthropic.claude-haiku-4-5-20251001-v1:0)
#   AWS credentials            provided automatically by App Runner instance role
#
# Optional:
#   BEDROCK_INFERENCE_PROFILE_ID
#   ANTHROPIC_API_KEY          (fallback if Bedrock is unavailable)
#   HF_API_TOKEN               (for Med42 second-opinion tier)
#   KB_ENABLE_PUBMED / KB_ENABLE_CDC / KB_ENABLE_OPENFDA  (default: false)

ENV PORT=8000

EXPOSE 8000

# App Runner health check polls GET /health — must return HTTP 200 within
# the configured healthcheck grace period (set to 120s in apprunner.yaml).
HEALTHCHECK --interval=30s --timeout=10s --start-period=120s --retries=3 \
    CMD curl -f http://localhost:8000/health || exit 1

# ── Entrypoint ────────────────────────────────────────────────────────────────
CMD ["python", "app.py"]
