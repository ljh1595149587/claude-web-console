# claude-web-console

English | [中文](README.md)

A **local, self-hosted** web console that lets you drive the `claude` CLI from your phone's browser: chat in a chosen working directory, watch what Claude does, and browse files. The Claude process, file reads, and command execution all stay on your machine — the phone is just a remote interface. Access is guarded by a single token.

> ⚠️ **Local / self-hosted use only.** This runs `claude` with file-write and command-execution tools behind a web endpoint. Protect it with the built-in access token and never expose the port directly to the public internet (prefer Tailscale).

## Features

- **Streaming chat** — token-by-token over SSE, multi-turn sessions, stop-generation button.
- **Tool observability** — tool calls render as collapsible cards (command / file path / arguments) with results filled back in (✓ or error + output). Essential when operating blind from a phone: you see exactly what Claude did on your computer.
- **Session history** — lists past conversations for the current working directory; tap one to replay it (bubbles, tool calls, thinking) and resume chatting via `--resume`. Refresh-restore brings back your last conversation automatically.
- **Markdown + tables** — dependency-free renderer (XSS-safe) for headings, bold/italic, inline & fenced code, lists, quotes, links, and GFM tables.
- **Thinking** — a "thinking…" pulse while waiting, plus 💭 bubbles for extended-thinking output.
- **Structured questions** — `AskUserQuestion` renders as tappable option cards.
- **Image input** — send photos/screenshots from your phone (base64, multimodal).
- **Permission modes** — pick per conversation: Ask before edits / Edit automatically / Plan mode / Bypass permissions, mirroring Claude Code's UI.
- **Multi-project switching** — switch between a whitelist of working directories from Settings.
- **Notifications** — browser notifications when the tab is backgrounded, plus optional WeChat/App push (Server酱) so you're notified even with the browser closed.
- **Two auth modes** — Max/subscription, or a third-party relay (Anthropic-compatible endpoint).
- **Claude Code–style UI** — mobile single-page app with slide-in panels for history, files, and settings.

## 1. Install dependencies

```powershell
cd d:\work\claude-web-console
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
```

## 2. Authentication

### Option A — Max / subscription (recommended)

Generate a long-lived subscription credential once:

```powershell
claude setup-token   # log in with your Max account
```

This prints an OAuth token of the form `sk-ant-oat01-...`. **This is a secret — never share it.** It's used via the `claude_code_oauth_token` config field (or the `CLAUDE_CODE_OAUTH_TOKEN` env var).

> Do **not** set `ANTHROPIC_API_KEY` in subscription mode — it would switch to pay-as-you-go billing instead of your subscription.

### Option B — Relay (third-party proxy)

Point at an Anthropic-compatible relay endpoint with `base_url` + a key. This is pay-as-you-go through someone else's proxy — **not** your Max subscription. Evaluate the relay's trustworthiness and data-privacy implications yourself.

## 3. Write the config file

Copy `config.example.json` to `config.json` (it's in `.gitignore`, so it never enters version control), then fill it in:

```json
{
  "web_console_token": "a long random access token",

  "auth_mode": "",
  "claude_code_oauth_token": "sk-ant-oat01-...   (from setup-token; secret!)",

  "base_url": "",
  "auth_token": "",
  "api_key": "",

  "work_dir": "d:\\work\\AI_NovelGenerator-main",
  "work_dirs": [],

  "host": "0.0.0.0",
  "port": 8765,
  "claude_bin": "",
  "proxy": "",
  "model": "",
  "permission_mode": "",
  "serverchan_sendkey": ""
}
```

Key fields:

- `web_console_token` — the token checked on every request. Make it long and random.
- `auth_mode` — `subscription` (Max/subscription) or `relay` (third-party proxy). Leave empty to auto-detect: if `base_url` is set it's treated as `relay`, otherwise `subscription`.
- `claude_code_oauth_token` — subscription credential; if omitted, the machine's existing `claude /login` session is reused.
- `base_url` / `auth_token` / `api_key` — relay mode: `base_url` is the Anthropic-compatible endpoint; use **either** `auth_token` (→ `ANTHROPIC_AUTH_TOKEN`, Bearer — what most claude-code relays use) **or** `api_key` (→ `ANTHROPIC_API_KEY`, `x-api-key`). Don't set both.
- `work_dir` — the working directory Claude operates in.
- `work_dirs` — optional whitelist for multi-project switching. Each entry is a string path or `{path, label}`. The frontend can only pick from this list — arbitrary paths are rejected.
- `proxy` — set if you need a proxy to reach Anthropic (e.g. `http://127.0.0.1:7890`); leave empty when using a global/TUN VPN.
- `model` / `permission_mode` — defaults for the model and permission mode (both selectable in the UI).
- `serverchan_sendkey` — optional [Server酱](https://sc3.ft07.com/) key; when set, a push is sent when a task finishes or errors. Both Server酱³ (`sctp…` keys) and the legacy Turbo version are auto-detected.

Any field can be overridden by an uppercase environment variable of the same name (e.g. `$env:PORT`). Precedence: **env var > config.json > default**.

## 4. Start

```powershell
python server.py
```

On startup the log prints the access URL and token, e.g.:

```
http://<your-ip>:8765/?token=xxxxxxxx
```

## 5. Access from your phone

- **Same Wi-Fi** — open `http://<computer-LAN-IP>:8765/` in your phone's browser and enter the token.
- **Remote** — put phone and computer on the same private network with [Tailscale](https://tailscale.com/) and use the Tailscale IP; or tunnel port **8765** out (always keep the token, and add HTTPS).

## API

| Method | Path | Notes |
|--------|------|-------|
| POST | `/chat` | body `{prompt, session_id?, model?, permission_mode?, work_dir?, images?}`, SSE stream; `session_id` resumes a conversation |
| GET  | `/api/info` | work dir(s), model list, permission modes, push status |
| GET  | `/sessions` | list past sessions for the working dir (read-only) |
| GET  | `/session/{sid}` | replay one session (read-only) |
| GET  | `/files?path=` | list a directory (confined to the working dir) |
| GET  | `/file?path=` | read a file (≤2MB; binary not shown) |

All endpoints authenticate via `Authorization: Bearer <token>` or `?token=`.

## Security

The web UI can run `claude` on your computer (with file-write and command-execution tools by default). Be sure to:

- Use a long, random token.
- Never expose port **8765** directly to the public internet — prefer Tailscale, otherwise a tunnel + HTTPS + token.
- Keep `config.json` and `.claude/settings*.json` out of git (already gitignored — they contain secrets).
- For extra safety, use **Plan mode** (read-only planning) from the settings, or restrict tools via `--allowed-tools` in `server.py`.
