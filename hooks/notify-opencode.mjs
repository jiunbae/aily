// OpenCode plugin for aily notification relay.
// Install: symlink to ~/.config/opencode/plugins/aily-notify.mjs
//
// Fires on session.idle -> extracts last assistant message -> calls post.sh

import { execFile } from "node:child_process";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";

const __dirname = dirname(fileURLToPath(import.meta.url));

export const ailyNotify = async ({ project, directory }) => {
  // Resolve post.sh relative to this plugin file.
  // When symlinked from ~/.config/opencode/plugins/, __dirname resolves to the real hooks/ dir.
  const hookDir = __dirname;
  const postScript = join(hookDir, "post.sh");

  let lastMessageText = "";

  return {
    "message.updated": async (msg) => {
      // Track last assistant message content
      if (msg?.role === "assistant" && msg?.content) {
        const parts = Array.isArray(msg.content) ? msg.content : [msg.content];
        const texts = parts
          .map((p) => (typeof p === "string" ? p : p?.text || ""))
          .filter(Boolean);
        if (texts.length > 0) {
          lastMessageText = texts.join("\n").trim();
        }
      }
    },

    "session.idle": async () => {
      if (!lastMessageText || lastMessageText.length < 20) return;
      if (!process.env.TMUX && !process.env.TMUX_PANE) return;

      const message =
        lastMessageText.length > 1000
          ? lastMessageText.slice(0, 1000) + "..."
          : lastMessageText;

      // Fire-and-forget: call post.sh in background
      try {
        execFile("bash", [postScript, "opencode", message], {
          cwd: directory || process.cwd(),
          stdio: "ignore",
          detached: true,
        }).unref();
      } catch {
        // Silently ignore errors
      }

      lastMessageText = "";
    },
  };
};
