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

# Copy application code. bridge_core/multiplexer/session_limit are imported by
# the bridges (from bridge_core import ...) AND by the dashboard
# (dashboard/ssh.py: from multiplexer import ...), so they must be in the image
# for every BRIDGE_MODE — including the default "dashboard" mode.
COPY agent-bridge.py slack-bridge.py bridge_core.py multiplexer.py session_limit.py ./
COPY dashboard/ ./dashboard/
COPY docker-entrypoint.sh ./
RUN chmod +x docker-entrypoint.sh

RUN mkdir -p /app/data && chown app:app /app/data

USER app

HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
    CMD python3 -c "import os, urllib.request; mode=os.environ.get('BRIDGE_MODE', 'dashboard'); urllib.request.urlopen('http://localhost:8080/healthz') if mode in ('dashboard', 'all') else None"

EXPOSE 8080

ENV PYTHONUNBUFFERED=1
ENV BRIDGE_MODE=dashboard
ENV DASHBOARD_HOST=0.0.0.0

ENTRYPOINT ["/app/docker-entrypoint.sh"]
