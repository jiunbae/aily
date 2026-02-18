# Stage 1: Install dependencies
FROM python:3.12-slim AS builder

WORKDIR /build
COPY requirements.txt .
RUN pip install --no-cache-dir --prefix=/install -r requirements.txt

# Stage 2: Runtime
FROM python:3.12-slim

RUN apt-get update \
    && apt-get install -y --no-install-recommends openssh-client \
    && rm -rf /var/lib/apt/lists/*

RUN useradd -m -u 1000 app

WORKDIR /app

# Copy only installed packages from builder
COPY --from=builder /install /usr/local

# Copy application code
COPY agent-bridge.py slack-bridge.py ./
COPY dashboard/ ./dashboard/

RUN mkdir -p /app/data && chown app:app /app/data

USER app

HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
    CMD python3 -c "import urllib.request; urllib.request.urlopen('http://localhost:8080/healthz')" || exit 1

EXPOSE 8080

ENV PYTHONUNBUFFERED=1

CMD ["python3", "-m", "dashboard"]
