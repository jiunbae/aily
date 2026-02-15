FROM python:3.12-slim

RUN apt-get update \
    && apt-get install -y --no-install-recommends openssh-client \
    && rm -rf /var/lib/apt/lists/*

RUN useradd -m -u 1000 app

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY agent-bridge.py slack-bridge.py ./
COPY dashboard/ ./dashboard/

RUN mkdir -p /app/data && chown app:app /app/data

USER 1000

# BRIDGE_MODE: "discord", "slack", or "dashboard"
ENV BRIDGE_MODE=discord
ENV AGENT_BRIDGE_ENV=/app/.notify-env
ENV PYTHONUNBUFFERED=1

EXPOSE 8080

CMD ["sh", "-c", \
  "if [ \"$BRIDGE_MODE\" = 'slack' ]; then exec python3 slack-bridge.py; \
   elif [ \"$BRIDGE_MODE\" = 'dashboard' ]; then exec python3 -m dashboard; \
   else exec python3 agent-bridge.py; fi"]
