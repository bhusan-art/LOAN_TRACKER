# --- 1. build the React app (frontend/ -> loan_tracker/web/spa) -------------------------
FROM node:22-slim AS web
WORKDIR /build/frontend
COPY frontend/package.json frontend/package-lock.json ./
RUN npm ci --no-audit --no-fund
COPY frontend/ ./
RUN npm run build

# --- 2. the Python app, serving the built files -----------------------------------------
FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PIP_NO_CACHE_DIR=1
WORKDIR /app

COPY pyproject.toml ./
COPY loan_tracker ./loan_tracker
COPY --from=web /build/loan_tracker/web/spa ./loan_tracker/web/spa
RUN pip install . && useradd --create-home --uid 1000 app \
    && mkdir -p /data/documents && chown app:app /data/documents && chmod 700 /data/documents
# encrypted documents live here (a named volume in docker-compose.yml)
ENV DOCUMENT_DIR=/data/documents

USER app
EXPOSE 8000
# migrate on start, then serve; --proxy-headers so HTTPS from the reverse proxy is respected
CMD ["sh", "-c", "python -m loan_tracker.cli migrate && uvicorn loan_tracker.web.app:app --host 0.0.0.0 --port 8000 --proxy-headers --forwarded-allow-ips='*'"]
