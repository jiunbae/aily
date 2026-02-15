(() => {
  "use strict";

  class AilyWS {
    constructor(opts = {}) {
      this.url = opts.url || AilyWS.defaultUrl();

      this._ws = null;
      this._status = "disconnected"; // connected | reconnecting | disconnected | connecting
      this._listeners = new Map(); // eventType -> Set<fn>
      this._wildcard = new Set(); // Set<fn>

      this._backoffMs = 1000;
      this._maxBackoffMs = 30000;
      this._reconnectTimer = null;
      this._pingTimer = null;

      this._subscribedSessions = [];
      this._lastHeartbeatAt = null;
      this._intentionalClose = false;
    }

    static defaultUrl() {
      const proto = window.location.protocol === "https:" ? "wss" : "ws";
      return `${proto}://${window.location.host}/ws`;
    }

    get status() {
      return this._status;
    }

    get lastHeartbeatAt() {
      return this._lastHeartbeatAt;
    }

    on(eventType, callback) {
      if (typeof callback !== "function") return;
      if (eventType === "*") {
        this._wildcard.add(callback);
        return;
      }
      if (!this._listeners.has(eventType)) {
        this._listeners.set(eventType, new Set());
      }
      this._listeners.get(eventType).add(callback);
    }

    off(eventType, callback) {
      if (eventType === "*") {
        this._wildcard.delete(callback);
        return;
      }
      const set = this._listeners.get(eventType);
      if (!set) return;
      set.delete(callback);
    }

    connect() {
      if (this._ws && (this._ws.readyState === WebSocket.OPEN || this._ws.readyState === WebSocket.CONNECTING)) {
        return;
      }
      this._intentionalClose = false;
      this._setStatus(this._status === "reconnecting" ? "reconnecting" : "connecting");

      // Clean up any prior timers.
      this._clearReconnectTimer();
      this._stopPing();

      try {
        this._ws = new WebSocket(this.url);
      } catch (e) {
        this._scheduleReconnect("ws_ctor_failed");
        return;
      }

      this._ws.addEventListener("open", () => {
        this._backoffMs = 1000;
        this._setStatus("connected");
        this._startPing();
        if (this._subscribedSessions.length > 0) {
          this.subscribe(this._subscribedSessions);
        }
      });

      this._ws.addEventListener("message", (evt) => {
        this._handleMessage(evt.data);
      });

      this._ws.addEventListener("close", () => {
        this._ws = null;
        this._stopPing();
        if (this._intentionalClose) {
          this._setStatus("disconnected");
          return;
        }
        this._scheduleReconnect("ws_closed");
      });

      this._ws.addEventListener("error", () => {
        // Most browsers also fire "close" after "error". We still schedule here
        // in case a close doesn't happen.
        this._scheduleReconnect("ws_error");
      });
    }

    disconnect() {
      this._intentionalClose = true;
      this._clearReconnectTimer();
      this._stopPing();
      if (this._ws) {
        try {
          this._ws.close(1000, "client_disconnect");
        } catch {
          // ignore
        }
        this._ws = null;
      }
      this._setStatus("disconnected");
    }

    reconnect() {
      this._backoffMs = 1000;
      this.disconnect();
      this._intentionalClose = false;
      this.connect();
    }

    send(obj) {
      if (!this._ws || this._ws.readyState !== WebSocket.OPEN) return false;
      try {
        this._ws.send(JSON.stringify(obj));
        return true;
      } catch {
        return false;
      }
    }

    subscribe(sessions = []) {
      this._subscribedSessions = Array.isArray(sessions) ? sessions.filter(Boolean) : [];
      this.send({ type: "subscribe", sessions: this._subscribedSessions });
    }

    _emit(eventType, event) {
      const specific = this._listeners.get(eventType);
      if (specific) {
        for (const fn of specific) {
          try {
            fn(event);
          } catch {
            // swallow listener errors
          }
        }
      }
      for (const fn of this._wildcard) {
        try {
          fn(eventType, event);
        } catch {
          // swallow listener errors
        }
      }
    }

    _setStatus(next) {
      if (this._status === next) return;
      this._status = next;
      this._emit("connection.status", { status: next, ts: Date.now() });
    }

    _handleMessage(raw) {
      let msg;
      try {
        msg = JSON.parse(raw);
      } catch {
        return;
      }
      if (!msg || typeof msg.type !== "string") return;

      const type = msg.type;
      if (type === "pong") {
        this._lastHeartbeatAt = Date.now();
        this._emit("pong", msg);
        return;
      }

      if (type === "heartbeat" || type === "system.heartbeat") {
        this._lastHeartbeatAt = Date.now();
      }

      this._emit(type, msg);
    }

    _scheduleReconnect(reason) {
      if (this._intentionalClose) return;
      if (this._reconnectTimer) return;

      this._setStatus("reconnecting");

      const base = this._backoffMs;
      const jitter = Math.floor(Math.random() * Math.min(500, base * 0.2));
      const delay = Math.min(this._maxBackoffMs, base + jitter);

      this._reconnectTimer = window.setTimeout(() => {
        this._reconnectTimer = null;

        if (navigator.onLine === false) {
          // Avoid hammering reconnect while offline.
          this._scheduleReconnect("offline");
          return;
        }

        this.connect();
      }, delay);

      this._backoffMs = Math.min(this._maxBackoffMs, Math.floor(this._backoffMs * 1.8));
      this._emit("connection.retry_scheduled", { reason, delayMs: delay, ts: Date.now() });
    }

    _clearReconnectTimer() {
      if (!this._reconnectTimer) return;
      window.clearTimeout(this._reconnectTimer);
      this._reconnectTimer = null;
    }

    _startPing() {
      this._stopPing();
      this._pingTimer = window.setInterval(() => {
        if (this._status !== "connected") return;
        this.send({ type: "ping" });
      }, 25000);
    }

    _stopPing() {
      if (!this._pingTimer) return;
      window.clearInterval(this._pingTimer);
      this._pingTimer = null;
    }
  }

  window.AilyWS = AilyWS;
  window.ailyWS = new AilyWS();

  // Connect ASAP, but let the page finish parsing first.
  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", () => window.ailyWS.connect(), { once: true });
  } else {
    window.ailyWS.connect();
  }
})();

