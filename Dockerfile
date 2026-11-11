# --- Stage 1: build the dashboard -------------------------------------------------
FROM node:22-alpine AS frontend
WORKDIR /build
COPY frontend/package.json frontend/package-lock.json ./
RUN npm ci --no-fund --no-audit
COPY frontend/ ./
RUN npm run build

# --- Stage 2: runtime ---------------------------------------------------------------
FROM python:3.13-slim AS runtime

ENV PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    HOST=0.0.0.0 \
    PORT=8000 \
    IPMON_FRONTEND_DIST=/app/frontend-dist

WORKDIR /app
COPY pyproject.toml ./
COPY src ./src
RUN pip install .

COPY scripts ./scripts
COPY --from=frontend /build/dist ./frontend-dist

# Run as an unprivileged user. ICMP works without root because docker-compose.yml
# sets net.ipv4.ping_group_range, which allows unprivileged ICMP sockets.
RUN useradd --system --uid 10001 --no-create-home ipmon
USER ipmon

EXPOSE 8000
HEALTHCHECK --interval=30s --timeout=5s --start-period=40s --retries=3 \
  CMD python -c "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8000/health', timeout=4).status == 200 else 1)"

CMD ["python", "-m", "ipmon"]
