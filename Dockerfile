# AI-DSSW Armoured Battle Console — one image for Linux, Windows (Docker Desktop / WSL 2) and macOS.
# Build and run everything with:  docker compose up -d   →  http://localhost:8000
FROM python:3.11-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    DATA_DIR=/data \
    DEBUG=0 \
    PORT=8000

WORKDIR /app

COPY requirements.txt .
RUN pip install -r requirements.txt

COPY . .
# Windows checkouts may turn LF into CRLF, which breaks a shell script inside Linux
RUN sed -i 's/\r$//' docker/entrypoint.sh && chmod +x docker/entrypoint.sh \
    && mkdir -p /data

EXPOSE 8000
VOLUME ["/data"]
ENTRYPOINT ["docker/entrypoint.sh"]
