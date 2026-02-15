(() => {
  "use strict";

  // -----------------------------
  // Utilities
  // -----------------------------

  function debounce(fn, waitMs) {
    let t = null;
    return function (...args) {
      const ctx = this;
      if (t) window.clearTimeout(t);
      t = window.setTimeout(() => fn.apply(ctx, args), waitMs);
    };
  }

  function escapeHtml(input) {
    const s = String(input ?? "");
    return s
      .replaceAll("&", "&amp;")
      .replaceAll("<", "&lt;")
      .replaceAll(">", "&gt;")
      .replaceAll('"', "&quot;")
      .replaceAll("'", "&#39;");
  }

  function safeUrl(url) {
    const u = String(url ?? "").trim();
    if (!u) return null;
    if (u.startsWith("http://") || u.startsWith("https://")) return u;
    return null;
  }

  function formatTime(ts) {
    if (!ts) return "";
    const d = new Date(ts);
    if (Number.isNaN(d.getTime())) return "";
    // ISO, but a bit more readable.
    return d.toISOString().replace("T", " ").replace("Z", " UTC");
  }

  function timeAgo(ts) {
    if (!ts) return "-";
    const d = new Date(ts);
    const t = d.getTime();
    if (Number.isNaN(t)) return "-";

    const now = Date.now();
    const deltaSec = Math.floor((now - t) / 1000);

    if (deltaSec < 5) return "just now";
    if (deltaSec < 60) return `${deltaSec}s ago`;

    const deltaMin = Math.floor(deltaSec / 60);
    if (deltaMin < 60) return `${deltaMin}m ago`;

    const deltaHr = Math.floor(deltaMin / 60);
    if (deltaHr < 24) return `${deltaHr}h ago`;

    const deltaDay = Math.floor(deltaHr / 24);
    if (deltaDay === 1) return "yesterday";
    if (deltaDay < 7) return `${deltaDay}d ago`;

    const deltaWk = Math.floor(deltaDay / 7);
    if (deltaWk < 5) return `${deltaWk}w ago`;

    return d.toLocaleDateString();
  }

  function normalizeStatus(status) {
    const s = String(status ?? "").toLowerCase().trim();
    if (!s) return "unknown";
    if (s === "waiting_input") return "waiting";
    if (s === "closed") return "archived";
    if (s === "dead") return "error";
    if (s === "orphan") return "orphaned";
    return s;
  }

  function statusMeta(statusRaw) {
    const status = normalizeStatus(statusRaw);
    switch (status) {
      case "active":
        return { label: "Active", badgeClass: "badge-active", dotClass: "status-dot--active pulse-active", color: "var(--status-active)" };
      case "waiting":
        return { label: "Waiting", badgeClass: "badge-waiting", dotClass: "status-dot--waiting pulse-attention", color: "var(--status-waiting)" };
      case "idle":
        return { label: "Idle", badgeClass: "badge-idle", dotClass: "status-dot--idle", color: "var(--status-idle)" };
      case "archived":
        return { label: "Archived", badgeClass: "badge-archived", dotClass: "status-dot--archived", color: "var(--status-archived)" };
      case "orphaned":
        return { label: "Orphaned", badgeClass: "badge-orphaned", dotClass: "status-dot--orphaned", color: "var(--status-orphaned)" };
      case "error":
      case "unreachable":
        return { label: "Error", badgeClass: "badge-error", dotClass: "status-dot--error pulse-error", color: "var(--status-error)" };
      default:
        return { label: status.charAt(0).toUpperCase() + status.slice(1), badgeClass: "badge-archived", dotClass: "status-dot--archived", color: "var(--status-archived)" };
    }
  }

  function agentLabel(agentType) {
    const a = String(agentType ?? "").toLowerCase();
    if (!a) return "";
    if (a.includes("claude")) return "Claude Code";
    if (a.includes("codex")) return "Codex";
    if (a.includes("gemini")) return "Gemini";
    return agentType;
  }

  function agentColor(agentType) {
    const a = String(agentType ?? "").toLowerCase();
    if (a.includes("claude")) return "var(--agent-claude)";
    if (a.includes("codex")) return "var(--agent-codex)";
    if (a.includes("gemini")) return "var(--agent-gemini)";
    return "var(--text-muted)";
  }

  async function apiJson(path, opts = {}) {
    const options = { ...opts };
    options.headers = {
      Accept: "application/json",
      ...(options.headers || {}),
    };

    if (options.body && typeof options.body !== "string") {
      options.headers["Content-Type"] = "application/json";
      options.body = JSON.stringify(options.body);
    }

    const res = await fetch(path, options);
    const contentType = res.headers.get("content-type") || "";
    const isJson = contentType.includes("application/json");
    const data = isJson ? await res.json().catch(() => null) : await res.text().catch(() => "");

    if (!res.ok) {
      const msg =
        (data && data.error && data.error.message) ||
        (typeof data === "string" ? data : "") ||
        `Request failed (${res.status})`;
      const err = new Error(msg);
      err.status = res.status;
      err.data = data;
      throw err;
    }
    return data;
  }

  // Minimal, safe markdown-ish renderer:
  // - Escapes HTML first
  // - Supports fenced code blocks, inline code, bold/italic, links, lists, blockquotes
  function renderMarkdown(input) {
    const raw = String(input ?? "");
    if (!raw) return "";

    // Extract fenced code blocks first.
    const codeBlocks = [];
    const withoutCode = raw.replace(/```([a-zA-Z0-9_-]+)?\n([\s\S]*?)```/g, (_m, lang, code) => {
      const idx = codeBlocks.length;
      codeBlocks.push({ lang: (lang || "").trim(), code: code ?? "" });
      return `@@CODEBLOCK_${idx}@@`;
    });

    // Escape everything else.
    let s = escapeHtml(withoutCode);

    // Blockquotes (very small subset): lines starting with >.
    s = s.replace(/(^|\n)&gt;[ \t]?(.+)(?=\n|$)/g, (_m, p1, body) => {
      return `${p1}<blockquote>${body}</blockquote>`;
    });

    // Bold/italic
    s = s.replace(/\*\*([^*]+)\*\*/g, "<strong>$1</strong>");
    s = s.replace(/(^|[^*])\*([^*\n]+)\*/g, "$1<em>$2</em>");

    // Inline code
    s = s.replace(/`([^`]+)`/g, (_m, code) => `<code class="inline-code">${code}</code>`);

    // Markdown links [text](url)
    s = s.replace(/\[([^\]]+)\]\(([^)]+)\)/g, (_m, text, url) => {
      const safe = safeUrl(url);
      if (!safe) return text;
      // `safe` is already HTML-escaped because we escaped the whole string earlier.
      return `<a href="${safe}" target="_blank" rel="noopener noreferrer">${text}</a>`;
    });

    // Auto-link raw URLs.
    s = s.replace(/(^|\s)(https?:\/\/[^\s<]+)/g, (_m, prefix, url) => {
      const safe = safeUrl(url);
      if (!safe) return `${prefix}${url}`;
      // `safe` is already HTML-escaped because we escaped the whole string earlier.
      return `${prefix}<a href="${safe}" target="_blank" rel="noopener noreferrer">${safe}</a>`;
    });

    // Lists (basic): group consecutive -/*/1. lines.
    const lines = s.split("\n");
    const out = [];
    let inUl = false;
    let inOl = false;
    for (const line of lines) {
      const ulMatch = line.match(/^\s*[-*]\s+(.+)$/);
      const olMatch = line.match(/^\s*\d+\.\s+(.+)$/);

      if (ulMatch) {
        if (inOl) {
          out.push("</ol>");
          inOl = false;
        }
        if (!inUl) {
          out.push("<ul>");
          inUl = true;
        }
        out.push(`<li>${ulMatch[1]}</li>`);
        continue;
      }
      if (olMatch) {
        if (inUl) {
          out.push("</ul>");
          inUl = false;
        }
        if (!inOl) {
          out.push("<ol>");
          inOl = true;
        }
        out.push(`<li>${olMatch[1]}</li>`);
        continue;
      }

      if (inUl) {
        out.push("</ul>");
        inUl = false;
      }
      if (inOl) {
        out.push("</ol>");
        inOl = false;
      }

      // Preserve blank lines as spacing.
      if (line.trim() === "") {
        out.push("");
      } else if (line.startsWith("<blockquote>")) {
        out.push(line);
      } else {
        out.push(`<p>${line}</p>`);
      }
    }
    if (inUl) out.push("</ul>");
    if (inOl) out.push("</ol>");

    s = out.join("\n");

    // Re-insert code blocks as <pre><code>.
    s = s.replace(/@@CODEBLOCK_(\d+)@@/g, (_m, idxStr) => {
      const idx = Number(idxStr);
      const block = codeBlocks[idx];
      if (!block) return "";

      const lang = (block.lang || "").toLowerCase();
      const label = lang ? `<span class="codeblock-btn" style="pointer-events:none">${escapeHtml(lang)}</span>` : "";
      const codeEsc = escapeHtml(block.code);

      const className = lang ? `language-${escapeHtml(lang)}` : "";
      return (
        `<pre>` +
          `<div class="codeblock-toolbar">` +
            `${label}` +
            `<button type="button" class="codeblock-btn" data-copy-code>Copy</button>` +
          `</div>` +
          `<code class="${className}">${codeEsc}</code>` +
        `</pre>`
      );
    });

    return s;
  }

  function highlightAllSafe() {
    if (!window.hljs || typeof window.hljs.highlightAll !== "function") return;
    try {
      window.hljs.highlightAll();
    } catch {
      // ignore
    }
  }

  function copyNearestCode(btn) {
    const pre = btn.closest("pre");
    if (!pre) return;
    const code = pre.querySelector("code");
    if (!code) return;
    const text = code.textContent || "";
    navigator.clipboard?.writeText(text).then(
      () => {
        const prev = btn.textContent;
        btn.textContent = "Copied";
        window.setTimeout(() => (btn.textContent = prev), 900);
      },
      () => {
        // Fallback: select text
        const range = document.createRange();
        range.selectNodeContents(code);
        const sel = window.getSelection();
        sel.removeAllRanges();
        sel.addRange(range);
        const prev = btn.textContent;
        btn.textContent = "Select";
        window.setTimeout(() => (btn.textContent = prev), 900);
      }
    );
  }

  document.addEventListener("click", (e) => {
    const btn = e.target.closest?.("[data-copy-code]");
    if (!btn) return;
    e.preventDefault();
    copyNearestCode(btn);
  });

  // Expose utilities for Alpine templates (and debugging).
  window.ailyUtils = {
    timeAgo,
    formatTime,
    renderMarkdown,
    statusMeta,
    agentLabel,
    agentColor,
  };

  // -----------------------------
  // Alpine global wiring
  // -----------------------------

  function setupGlobalStores(Alpine) {
    Alpine.store("ws", {
      status: "disconnected",
      lastHeartbeatAt: null,
      sessionCount: 0,
    });

    Alpine.store("sidebar", {
      sessions: [],
      loading: true,
      lastLoadedAt: null,
    });

    if (window.ailyWS) {
      Alpine.store("ws").status = window.ailyWS.status || Alpine.store("ws").status;
      Alpine.store("ws").lastHeartbeatAt = window.ailyWS.lastHeartbeatAt || null;
    }

    const refreshSidebarSessions = async () => {
      try {
        Alpine.store("sidebar").loading = true;
        const data = await apiJson("/api/sessions?limit=50&sort=-last_activity");
        const sessions = (data && data.sessions) || [];
        Alpine.store("sidebar").sessions = sessions;
        Alpine.store("sidebar").lastLoadedAt = Date.now();

        const activeCount = sessions.filter((s) => normalizeStatus(s.status) === "active").length;
        Alpine.store("ws").sessionCount = activeCount;
      } catch {
        // keep previous sidebar list
      } finally {
        Alpine.store("sidebar").loading = false;
      }
    };

    const refreshSidebarSessionsDebounced = debounce(refreshSidebarSessions, 400);

    // WS status -> store
    if (window.ailyWS) {
      window.ailyWS.on("connection.status", (evt) => {
        Alpine.store("ws").status = evt.status || "disconnected";
      });
      window.ailyWS.on("heartbeat", () => {
        Alpine.store("ws").lastHeartbeatAt = Date.now();
      });
      window.ailyWS.on("system.heartbeat", () => {
        Alpine.store("ws").lastHeartbeatAt = Date.now();
      });
      window.ailyWS.on("session.created", () => refreshSidebarSessionsDebounced());
      window.ailyWS.on("session.updated", () => refreshSidebarSessionsDebounced());
      window.ailyWS.on("session.deleted", () => refreshSidebarSessionsDebounced());
      window.ailyWS.on("message.new", () => refreshSidebarSessionsDebounced());
    }

    // Initial sidebar load.
    refreshSidebarSessionsDebounced();
  }

  // -----------------------------
  // Alpine components
  // -----------------------------

  function wsStatus() {
    return {
      get status() {
        return this.$store.ws.status;
      },
      get sessionCount() {
        return this.$store.ws.sessionCount || 0;
      },
      reconnectNow() {
        window.ailyWS?.reconnect?.();
      },
    };
  }

  // Optional helper used by base.html quick list.
  function sidebarSessions() {
    return {
      get sessions() {
        return this.$store.sidebar.sessions || [];
      },
      get activeSessions() {
        return this.sessions
          .filter((s) => ["waiting", "error", "orphaned", "active"].includes(normalizeStatus(s.status)))
          .slice(0, 20);
      },
      get idleSessions() {
        return this.sessions
          .filter((s) => normalizeStatus(s.status) === "idle")
          .slice(0, 20);
      },
    };
  }

  function dashboardHome() {
    return {
      loading: true,
      stats: null,
      sessions: [],
      activity: [],

      showNewSessionModal: false,
      creating: false,
      createError: "",
      newSession: { name: "", host: "" },
      isValidSessionName: false,

      timeAgo,
      formatTime,

      init() {
        this.refresh();
        this._wireWs();
      },

      async refresh() {
        this.loading = true;
        this.createError = "";
        try {
          const [stats, sessions] = await Promise.all([
            apiJson("/api/stats"),
            apiJson("/api/sessions?limit=60&sort=-last_activity"),
          ]);
          this.stats = stats || null;
          this.sessions = (sessions && sessions.sessions) || [];
        } catch (e) {
          this.createError = e?.message || "Failed to load dashboard data";
        } finally {
          this.loading = false;
        }
      },

      get activeCount() {
        return this.stats?.sessions?.active ?? 0;
      },

      get waitingCount() {
        // Backend may or may not support waiting; fallback to scan sessions.
        const fromStats = this.stats?.sessions?.waiting ?? this.stats?.sessions?.waiting_input ?? null;
        if (typeof fromStats === "number") return fromStats;
        return this.sessions.filter((s) => normalizeStatus(s.status) === "waiting").length;
      },

      get idleCount() {
        return this.stats?.sessions?.idle ?? this.sessions.filter((s) => normalizeStatus(s.status) === "idle").length;
      },

      get messages24h() {
        return this.stats?.messages?.last_24h ?? 0;
      },

      get attentionSessions() {
        const ranked = (s) => {
          const st = normalizeStatus(s.status);
          if (st === "error") return 0;
          if (st === "waiting") return 1;
          return 9;
        };
        return this.sessions
          .filter((s) => ["waiting", "error", "orphaned", "unreachable"].includes(normalizeStatus(s.status)))
          .sort((a, b) => ranked(a) - ranked(b))
          .slice(0, 4);
      },

      get activeSessions() {
        const rank = (s) => {
          const st = normalizeStatus(s.status);
          if (st === "waiting") return 0;
          if (st === "active") return 1;
          if (st === "idle") return 2;
          if (st === "error") return 3;
          return 4;
        };
        return [...this.sessions]
          .filter((s) => normalizeStatus(s.status) !== "archived")
          .sort((a, b) => {
            const ra = rank(a);
            const rb = rank(b);
            if (ra !== rb) return ra - rb;
            const ta = new Date(a.last_activity || a.updated_at || 0).getTime();
            const tb = new Date(b.last_activity || b.updated_at || 0).getTime();
            return tb - ta;
          });
      },

      statusColor(status) {
        return statusMeta(status).color;
      },

      agentLabel,
      agentColor,

      activityDotColor(item) {
        const type = String(item?.type || "");
        if (type === "message.new") return "var(--accent-primary)";
        if (type === "session.created") return "var(--status-active)";
        if (type === "session.deleted") return "var(--status-archived)";
        if (type === "session.updated") {
          const st = item?.payload?.status;
          return statusMeta(st).color;
        }
        return "var(--text-muted)";
      },

      activityText(item) {
        const type = String(item?.type || "");
        if (type === "message.new") {
          const role = String(item?.payload?.role || "").toLowerCase();
          const who = role === "user" ? "User" : role === "assistant" ? "AI" : role ? role : "Message";
          const content = String(item?.payload?.content || "").trim().replace(/\s+/g, " ");
          return `${who}: ${content.slice(0, 90) || "..."}`;
        }
        if (type === "session.created") return "Session started";
        if (type === "session.deleted") return "Session closed";
        if (type === "session.updated") {
          const st = statusMeta(item?.payload?.status).label;
          return `Session updated: ${st}`;
        }
        return type || "Event";
      },

      validateSessionName() {
        const name = String(this.newSession.name || "").trim();
        this.isValidSessionName = /^[a-zA-Z0-9_-]{1,64}$/.test(name);
      },

      async createSession() {
        this.validateSessionName();
        if (!this.isValidSessionName) return;
        if (this.creating) return;

        this.creating = true;
        this.createError = "";
        try {
          const body = { name: this.newSession.name.trim(), host: this.newSession.host.trim() };
          const data = await apiJson("/api/sessions", { method: "POST", body });
          const session = data?.session;
          this.showNewSessionModal = false;
          this.newSession = { name: "", host: "" };
          this.isValidSessionName = false;
          if (session?.name) {
            window.location.href = `/sessions/${encodeURIComponent(session.name)}`;
          } else {
            await this.refresh();
          }
        } catch (e) {
          this.createError = e?.message || "Failed to create session";
        } finally {
          this.creating = false;
        }
      },

      _pushActivityItem(item) {
        this.activity.unshift(item);
        if (this.activity.length > 30) this.activity.length = 30;
      },

      _wireWs() {
        if (!window.ailyWS) return;

        const addEvent = (type, payload, ts) => {
          const sessionName = payload?.name || payload?.session_name || payload?.session?.name || "";
          const when = ts ? new Date(ts * 1000) : new Date();
          const meta = {
            id: `${Date.now()}_${Math.random().toString(16).slice(2)}`,
            type,
            sessionName,
            payload,
            timestamp: when.toISOString(),
          };
          this._pushActivityItem(meta);
        };

        window.ailyWS.on("session.created", (evt) => {
          const s = evt?.payload;
          if (s?.name) this._upsertSession(s);
          addEvent("session.created", s, evt?.timestamp);
          this._refreshStatsSoon();
        });

        window.ailyWS.on("session.updated", (evt) => {
          const s = evt?.payload;
          if (s?.name) this._upsertSession(s);
          addEvent("session.updated", s, evt?.timestamp);
          this._refreshStatsSoon();
        });

        window.ailyWS.on("session.deleted", (evt) => {
          const s = evt?.payload;
          const name = s?.name || s?.session_name;
          if (name) this.sessions = this.sessions.filter((x) => x.name !== name);
          addEvent("session.deleted", s, evt?.timestamp);
          this._refreshStatsSoon();
        });

        window.ailyWS.on("message.new", (evt) => {
          const m = evt?.payload;
          addEvent("message.new", m, evt?.timestamp);
          this._touchSessionFromMessage(m);
          this._refreshStatsSoon();
        });
      },

      _upsertSession(session) {
        const idx = this.sessions.findIndex((s) => s.name === session.name);
        if (idx >= 0) {
          this.sessions.splice(idx, 1, { ...this.sessions[idx], ...session });
        } else {
          this.sessions.unshift(session);
        }
      },

      _touchSessionFromMessage(message) {
        if (!message) return;
        const byName = message.session_name || message.name;
        const byId = message.session_id;

        let idx = -1;
        if (byName) {
          idx = this.sessions.findIndex((s) => s.name === byName);
        } else if (byId != null) {
          idx = this.sessions.findIndex((s) => s.id === byId);
        }
        if (idx < 0) return;

        const s = { ...this.sessions[idx] };
        s.updated_at = message.timestamp || s.updated_at;
        if (typeof message.content === "string" && message.content.trim()) {
          s.last_message_preview = message.content.trim().slice(0, 140);
        }
        this.sessions.splice(idx, 1, s);
      },

      _refreshStatsSoon: debounce(async function () {
        try {
          this.stats = await apiJson("/api/stats");
        } catch {
          // ignore
        }
      }, 600),
    };
  }

  function sessionList() {
    return {
      loading: true,
      sessions: [],

      q: "",
      status: "all",
      sortKey: "last_activity",
      sortDir: "desc",

      showNewSessionModal: false,
      creating: false,
      createError: "",
      newSession: { name: "", host: "" },
      isValidSessionName: false,

      timeAgo,
      formatTime,

      init() {
        this.refresh();
        this._wireWs();
      },

      async refresh() {
        this.loading = true;
        try {
          const data = await apiJson("/api/sessions?limit=200&sort=-last_activity");
          this.sessions = (data && data.sessions) || [];
        } catch (e) {
          this.createError = e?.message || "Failed to load sessions";
        } finally {
          this.loading = false;
        }
      },

      get filtered() {
        const q = String(this.q || "").trim().toLowerCase();
        const status = String(this.status || "all").toLowerCase();
        let list = this.sessions;

        if (status !== "all") {
          list = list.filter((s) => normalizeStatus(s.status) === status);
        }

        if (q) {
          list = list.filter((s) => {
            return (
              String(s.name || "").toLowerCase().includes(q) ||
              String(s.host || "").toLowerCase().includes(q) ||
              String(s.agent_type || "").toLowerCase().includes(q)
            );
          });
        }

        const dir = this.sortDir === "asc" ? 1 : -1;
        const key = this.sortKey;
        const toNumTime = (v) => {
          if (!v) return 0;
          const t = new Date(v).getTime();
          return Number.isNaN(t) ? 0 : t;
        };
        list = [...list].sort((a, b) => {
          if (key === "name") return dir * String(a.name || "").localeCompare(String(b.name || ""));
          if (key === "host") return dir * String(a.host || "").localeCompare(String(b.host || ""));
          if (key === "status") return dir * normalizeStatus(a.status).localeCompare(normalizeStatus(b.status));
          if (key === "messages") return dir * ((a.message_count || a.messages || 0) - (b.message_count || b.messages || 0));
          // default: last activity
          return dir * (toNumTime(a.last_activity || a.updated_at) - toNumTime(b.last_activity || b.updated_at));
        });

        return list;
      },

      setSort(key) {
        if (this.sortKey === key) {
          this.sortDir = this.sortDir === "asc" ? "desc" : "asc";
        } else {
          this.sortKey = key;
          this.sortDir = key === "name" ? "asc" : "desc";
        }
      },

      sortIndicator(key) {
        if (this.sortKey !== key) return "";
        return this.sortDir === "asc" ? "^" : "v";
      },

      statusBadgeClass(status) {
        return statusMeta(status).badgeClass;
      },

      statusLabel(status) {
        return statusMeta(status).label;
      },

      validateSessionName() {
        const name = String(this.newSession.name || "").trim();
        this.isValidSessionName = /^[a-zA-Z0-9_-]{1,64}$/.test(name);
      },

      async createSession() {
        this.validateSessionName();
        if (!this.isValidSessionName) return;
        if (this.creating) return;

        this.creating = true;
        this.createError = "";
        try {
          const body = { name: this.newSession.name.trim(), host: this.newSession.host.trim() };
          const data = await apiJson("/api/sessions", { method: "POST", body });
          const session = data?.session;
          this.showNewSessionModal = false;
          this.newSession = { name: "", host: "" };
          this.isValidSessionName = false;
          if (session?.name) {
            window.location.href = `/sessions/${encodeURIComponent(session.name)}`;
          } else {
            await this.refresh();
          }
        } catch (e) {
          this.createError = e?.message || "Failed to create session";
        } finally {
          this.creating = false;
        }
      },

      async killSession(name) {
        const sessionName = String(name || "");
        if (!sessionName) return;
        const ok = window.confirm(`Kill session '${sessionName}'? This will archive threads where possible.`);
        if (!ok) return;

        try {
          await apiJson(`/api/sessions/${encodeURIComponent(sessionName)}`, { method: "DELETE" });
          this.sessions = this.sessions.filter((s) => s.name !== sessionName);
        } catch (e) {
          window.alert(e?.message || "Failed to kill session");
        }
      },

      _wireWs() {
        if (!window.ailyWS) return;
        window.ailyWS.on("session.created", (evt) => {
          const s = evt?.payload;
          if (s?.name) this._upsertSession(s);
        });
        window.ailyWS.on("session.updated", (evt) => {
          const s = evt?.payload;
          if (s?.name) this._upsertSession(s);
        });
        window.ailyWS.on("session.deleted", (evt) => {
          const s = evt?.payload;
          const name = s?.name || s?.session_name;
          if (!name) return;
          this.sessions = this.sessions.filter((x) => x.name !== name);
        });
      },

      _upsertSession(session) {
        const idx = this.sessions.findIndex((s) => s.name === session.name);
        if (idx >= 0) {
          this.sessions.splice(idx, 1, { ...this.sessions[idx], ...session });
        } else {
          this.sessions.unshift(session);
        }
      },
    };
  }

  function sessionDetail(sessionName) {
    return {
      sessionName,
      loading: true,
      session: null,
      messages: [],
      error: "",

      sending: false,
      draft: "",

      // Scroll state
      isAtBottom: true,
      unread: 0,

      timeAgo,
      formatTime,
      renderMarkdown,
      agentLabel,
      agentColor,
      statusMeta,

      _durationTimer: null,

      init() {
        this.load();
        this._wireWs();
      },

      async load() {
        this.loading = true;
        this.error = "";
        try {
          const [sessionResp, msgResp] = await Promise.all([
            apiJson(`/api/sessions/${encodeURIComponent(this.sessionName)}`),
            apiJson(`/api/sessions/${encodeURIComponent(this.sessionName)}/messages?limit=200&offset=0`),
          ]);
          this.session = sessionResp?.session || null;
          this.messages = msgResp?.messages || [];

          this.$nextTick(() => {
            this._setupScrollTracking();
            this.scrollToBottom(true);
            highlightAllSafe();
            this._startDurationTicker();
          });
        } catch (e) {
          this.error = e?.message || "Failed to load session";
        } finally {
          this.loading = false;
        }
      },

      get headerBadges() {
        const s = this.session;
        if (!s) return [];
        const badges = [];
        badges.push({ kind: "host", label: s.host || "unknown" });
        if (s.agent_type) badges.push({ kind: "agent", label: agentLabel(s.agent_type), color: agentColor(s.agent_type) });
        badges.push({ kind: "status", ...statusMeta(s.status) });
        return badges;
      },

      get renderedItems() {
        // Inject date separators.
        const items = [];
        let lastDay = "";
        for (const m of this.messages) {
          const ts = m.timestamp || m.created_at || m.updated_at;
          const d = ts ? new Date(ts) : null;
          const day = d && !Number.isNaN(d.getTime()) ? d.toISOString().slice(0, 10) : "";
          if (day && day !== lastDay) {
            items.push({ kind: "date", day });
            lastDay = day;
          }
          items.push({ kind: "msg", msg: m });
        }
        return items;
      },

      messageKind(m) {
        const role = String(m?.role || "").toLowerCase();
        if (role === "user") return "user";
        if (role === "system") return "system";
        if (role === "tool") return "ai";
        return "ai";
      },

      messageSourceLabel(m) {
        const src = String(m?.source || "").toLowerCase();
        if (!src) return "";
        if (src === "jsonl") return "jsonl";
        if (src === "discord") return "discord";
        if (src === "slack") return "slack";
        if (src === "tmux") return "tmux";
        if (src === "hook") return "hook";
        return src;
      },

      dateLabel(day) {
        try {
          const d = new Date(`${day}T00:00:00Z`);
          return d.toLocaleDateString(undefined, { year: "numeric", month: "long", day: "numeric" });
        } catch {
          return day;
        }
      },

      scrollToBottom(resetUnread = false) {
        const el = this.$refs.msgScroll;
        if (!el) return;
        el.scrollTop = el.scrollHeight;
        this.isAtBottom = true;
        if (resetUnread) this.unread = 0;
      },

      onScroll() {
        const el = this.$refs.msgScroll;
        if (!el) return;
        const distance = el.scrollHeight - (el.scrollTop + el.clientHeight);
        const atBottom = distance < 100;
        this.isAtBottom = atBottom;
        if (atBottom) this.unread = 0;
      },

      _setupScrollTracking() {
        const el = this.$refs.msgScroll;
        if (!el) return;
        el.removeEventListener("scroll", this._onScrollBound);
        this._onScrollBound = this.onScroll.bind(this);
        el.addEventListener("scroll", this._onScrollBound, { passive: true });
        this.onScroll();
      },

      _wireWs() {
        if (!window.ailyWS) return;
        // Server-side filtering.
        window.ailyWS.subscribe([this.sessionName]);

        window.ailyWS.on("session.updated", (evt) => {
          const s = evt?.payload;
          if (s?.name === this.sessionName) this.session = { ...(this.session || {}), ...s };
        });

        window.ailyWS.on("message.new", (evt) => {
          const m = evt?.payload;
          if (!m) return;
          if (!this._belongsToThisSession(m)) return;
          this._appendMessage(m);
        });
      },

      _belongsToThisSession(message) {
        const byName = message.session_name || message.name;
        if (byName && String(byName) === String(this.sessionName)) return true;
        const byId = message.session_id;
        if (byId != null && this.session?.id != null && Number(byId) === Number(this.session.id)) return true;
        return false;
      },

      _appendMessage(m) {
        // Dedup by external_id if present, else by id.
        const ext = m.external_id || m.source_id;
        if (ext && this.messages.some((x) => (x.external_id || x.source_id) === ext)) return;
        if (m.id != null && this.messages.some((x) => x.id === m.id)) return;

        this.messages.push(m);

        // Auto-scroll only if user is at bottom.
        this.$nextTick(() => {
          highlightAllSafe();
          if (this.isAtBottom) {
            this.scrollToBottom(true);
          } else {
            this.unread += 1;
          }
        });
      },

      async sendMessage() {
        const text = String(this.draft || "").trim();
        if (!text) return;
        if (this.sending) return;

        this.sending = true;
        this.error = "";
        try {
          await apiJson(`/api/sessions/${encodeURIComponent(this.sessionName)}/send`, {
            method: "POST",
            body: { message: text },
          });

          // Optimistic echo (will be replaced/duplicated by ingested message; dedup best-effort).
          const nowIso = new Date().toISOString();
          this._appendMessage({
            id: `local_${Date.now()}`,
            role: "user",
            content: text,
            source: "tmux",
            timestamp: nowIso,
            session_name: this.sessionName,
          });

          this.draft = "";
          this.$nextTick(() => this._autosizeTextarea());
        } catch (e) {
          this.error = e?.message || "Failed to send message";
        } finally {
          this.sending = false;
        }
      },

      async killSession() {
        const ok = window.confirm(`Kill session '${this.sessionName}'? This will archive threads where possible.`);
        if (!ok) return;
        try {
          await apiJson(`/api/sessions/${encodeURIComponent(this.sessionName)}`, { method: "DELETE" });
          window.location.href = "/sessions";
        } catch (e) {
          this.error = e?.message || "Failed to kill session";
        }
      },

      onDraftInput() {
        this._autosizeTextarea();
      },

      _autosizeTextarea() {
        const el = this.$refs.draft;
        if (!el) return;
        el.style.height = "auto";
        const max = 120; // ~5 lines
        el.style.height = `${Math.min(max, el.scrollHeight)}px`;
      },

      onDraftKeydown(e) {
        if (e.key === "Enter" && !e.shiftKey) {
          e.preventDefault();
          this.sendMessage();
        }
      },

      _startDurationTicker() {
        if (this._durationTimer) window.clearInterval(this._durationTimer);
        if (!this.session?.created_at) return;
        this._durationTimer = window.setInterval(() => {
          // Trigger Alpine re-render by touching a reactive field.
          if (this.session) this.session._tick = Date.now();
        }, 1000);
      },

      durationText() {
        const created = this.session?.created_at;
        if (!created) return "-";
        const t0 = new Date(created).getTime();
        if (Number.isNaN(t0)) return "-";
        const t1 = Date.now();
        const sec = Math.max(0, Math.floor((t1 - t0) / 1000));
        const h = Math.floor(sec / 3600);
        const m = Math.floor((sec % 3600) / 60);
        const s = sec % 60;
        if (h > 0) return `${h}h ${m}m`;
        if (m > 0) return `${m}m ${s}s`;
        return `${s}s`;
      },
    };
  }

  // -----------------------------
  // Register Alpine data providers
  // -----------------------------

  document.addEventListener("alpine:init", () => {
    const Alpine = window.Alpine;
    if (!Alpine) return;

    setupGlobalStores(Alpine);

    Alpine.data("wsStatus", wsStatus);
    Alpine.data("sidebarSessions", sidebarSessions);
    Alpine.data("dashboardHome", dashboardHome);
    Alpine.data("sessionList", sessionList);
    Alpine.data("sessionDetail", sessionDetail);
  });
})();
