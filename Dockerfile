FROM python:3.12-slim

RUN apt-get update \
    && apt-get install -y --no-install-recommends openssh-client \
    && rm -rf /var/lib/apt/lists/*

RUN useradd -m -u 1000 app

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY agent-bridge.py slack-bridge.py ./

USER 1000

# BRIDGE_MODE: "discord" or "slack"
ENV BRIDGE_MODE=discord
ENV AGENT_BRIDGE_ENV=/app/.notify-env

CMD ["sh", "-c", "if [ \"$BRIDGE_MODE\" = 'slack' ]; then exec python3 slack-bridge.py; else exec python3 agent-bridge.py; fi"]
