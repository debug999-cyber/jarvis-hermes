<p align="center"><img src="docs/img/banner.jpg" alt="J.A.R.V.I.S." width="100%"></p>

# J.A.R.V.I.S. on Hermes Agent

<p align="right"><a href="README.md">🇷🇺 Русский</a></p>

> **Just A Rather Very Intelligent System** — a personal voice AI assistant for macOS built on
> [Hermes Agent](https://github.com/NousResearch/hermes-agent) (Nous Research, MIT). Hermes provides the *brain*
> (LLM, memory, skills, tools, scheduler, messengers); this project adds the *body and personality*: Mac control, voice,
> wake word, a holographic HUD, briefings, modes, a structured knowledge base and a file/project vault.

```
  You:    "Hey Jarvis… what's on today, and switch to focus mode"
  JARVIS: "Good morning, sir. Team call at half past two, gym in the evening.
           Focus mode is on — I won't disturb you until the meeting is over."
```

> The assistant speaks **Russian by default** (personality, prompts, HUD, docs). Everything is plain text in
> `config/SOUL.md`, `plugins/*/skills/*.md` and `hud/static/index.html` — switch the language by editing those files.

---

## What it does

| Area | Capabilities | Where |
|---|---|---|
| 🎙 **Voice** | wake word "Hey Jarvis" (local, openWakeWord), push-to-talk `Ctrl+B`, local Whisper STT, Edge/ElevenLabs/OpenAI TTS, barge-in, stop phrases | Hermes voice mode + our config |
| 🖥 **Mac control** | apps, windows, volume, brightness, dark mode, Wi-Fi/Bluetooth, battery, sleep/lock, **"what's on my screen?" → screenshot + vision without extra questions**, camera, clipboard, typing & hotkeys, Spotlight, Finder, files (delete = Trash only), wallpaper | plugin `jarvis-macos` (29 tools) |
| 📅 **Productivity** | Calendar, Reminders, Notes, Shortcuts, timers & alarms, morning/evening briefings | `jarvis-macos` + `jarvis-core` |
| 🎵 **Media** | Apple Music / Spotify: play/pause/next, "what's playing", playlists | `jarvis-macos` |
| 🧠 **Brain** | any LLM (OpenRouter, Anthropic, OpenAI, Gemini, local Ollama…), long-term memory, self-taught skills, FTS search over past sessions | Hermes |
| 📁 **File & project vault** | the `~/JARVIS` folder: drop any documents, PDFs, spreadsheets, decks, and link whole projects — (or iCloud/Documents/Obsidian with one command) — JARVIS indexes the content (SQLite FTS5), searches, reads, **writes, sorts into folders, renames** (delete = Trash only), edits and runs code; new files are noticed automatically, summarized into the knowledge base, and JARVIS suggests what to do. Secrets are never indexed. [docs/VAULT.md](docs/VAULT.md) | `jarvis-brain` (`vault_*`) |
| 🗄 **Knowledge base** | own structured store (SQLite+FTS5): people, projects, preferences, decisions, daily diary; JARVIS fills it during conversation, injects relevant facts into every turn and **reviews the structure nightly** (duplicates, conflicts, taxonomy, entity cards) with backup + changelog. Temporal facts ("what used to be true"), living entity summaries, learns from its own tool failures, **lowers confidence when you say "that's not right"** | `jarvis-brain` |
| 🌐 **Web** | search, page extraction, browser (Playwright), images, YouTube on the HUD | Hermes + `jarvis_hud` |
| 💻 **Dev** | terminal, files, patches, code execution, sub-agents, Claude Code / Codex as skills, MCP servers | Hermes |
| 🕹 **HUD** | a desktop in the browser: reactive sphere, widgets on live Mac data (today's calendar, battery, Focus, model, timers, knowledge base & vault), agent panels (text/images/video/web/charts), chat, **server-side TTS with the same Edge voice as voice mode** | `hud/` |
| 📱 **Everywhere** | Telegram, Discord (incl. voice channels), WhatsApp, Slack, iMessage, Email — one agent, one memory | Hermes gateway |
| ⏰ **Autonomy** | **event-driven triggers** (new files in the vault, you're back at the Mac → briefing, low disk, power unplugged — no LLM until there's a reason), cron jobs (08:00 briefing, evening recap, 03:30 nightly review), local watchdog (battery, **meeting prep from the knowledge base**, **macOS Focus → JARVIS mode**), `HEARTBEAT.md` checklist, focus/night/presentation modes | Hermes cron + `jarvis-core` |
| 🏠 **Smart home** | Home Assistant (built-in toolset) or HomeKit via Shortcuts | skill `jarvis-home-automation` |
| 📦 **App** | JARVIS.app in the menu bar (status, HUD, voice, "Ask…", updates, **first-run wizard**, diagnostics), launch at login, **auto-update from GitHub** with backup & rollback, Shortcuts for Siri/Finder | `app/`, `scripts/update.py` |
| 🔒 **Safety** | dangerous commands need approval (approvals: smart), irreversible actions require `confirmed=true`, local STT, secrets never leave the Mac | Hermes + our tools |

## Screenshots

<p align="center"><img src="docs/img/hud-calm.jpg" alt="HUD: calm" width="100%"></p>
<p align="center"><sub>Desktop: today's calendar, knowledge base, system and timers — all on live Mac data.</sub></p>

<p align="center"><img src="docs/img/hud-panels.jpg" alt="HUD: panels" width="100%"></p>
<p align="center"><sub>The agent showed a chart with <code>jarvis_hud</code> — the sphere steps aside, the dialog stays at hand.</sub></p>

---

## Install (macOS, 5 minutes)

One command in Terminal (downloads the latest release and runs the interactive installer):

```bash
curl -fsSL https://raw.githubusercontent.com/debug999-cyber/jarvis-hermes/main/get.sh | bash
```

Or manually: [download the release zip](https://github.com/debug999-cyber/jarvis-hermes/releases/latest) (contains a
prebuilt JARVIS.app — no compiler needed) → unzip → `bash install.sh`. Or `git clone … && cd jarvis-hermes && ./install.sh`.

The installer sets up Homebrew dependencies, Hermes Agent, voice packages, plugins, personality, skills, cron jobs, the
`jarvis` command and (optionally) launch-at-login. It asks for an LLM provider at the end and **pings the model** to make
sure it actually answers.

After install JARVIS.app runs a **first-run wizard** (model → macOS permissions → vault folder → HUD).
If anything misbehaves: **`jarvis doctor --fix`** — checks the model, plugins, API server, gateway, HUD, launchd,
permissions, vault and version, and repairs what it can. Details: [docs/INSTALL.md](docs/INSTALL.md) (Russian).

## Run

```bash
jarvis            # voice TUI: "Hey Jarvis" or Ctrl+B
jarvis hud        # holographic HUD → http://127.0.0.1:8765
jarvis up         # gateway (API + messengers) + HUD + TUI
jarvis status     # what's running
jarvis vault open # the ~/JARVIS folder: drop files and projects here
jarvis shortcuts  # Siri "Ask JARVIS", Finder quick action "Send to JARVIS vault", "Hush", briefing
jarvis doctor --fix
```

Inside chat: `/voice on`, `/wake on`, `/brief`, `/focus`, `/timer 10 tea`, `/screen`, `/vol 30`, `/lock`,
`/remember …`, `/recall …`, `/brain stats`, `/jarvis` (all skills). Full command list and 80+ example phrases:
[docs/USAGE.md](docs/USAGE.md).

## Project layout

```
jarvis-hermes/
├── get.sh / install.sh        ← one-line installer / idempotent macOS installer
├── app/                       ← JARVIS.app: single Swift file, Info.plist, build.sh, icon generator
├── bin/jarvis                 ← CLI wrapper: voice / hud / gateway / vault / doctor / update / shortcuts …
├── plugins/
│   ├── jarvis-core/           ← turn context, HUD events, timers, modes, weather, watchdog, event triggers, screen context
│   ├── jarvis-macos/          ← 29 macOS tools (osascript/JXA, no Xcode needed)
│   └── jarvis-brain/          ← knowledge base (SQLite+FTS5), nightly review, file/project vault
├── hud/                       ← HUD server (stdlib only), static UI, server-side TTS
├── config/                    ← SOUL.md (personality), config.jarvis.yaml, HEARTBEAT.md, launchd plists
├── skills/, hooks/            ← Hermes skills and gateway hooks
├── scripts/                   ← doctor.py, selftest.py, update.py, make_shortcuts.py, setup_cron.sh
├── tests/                     ← pytest (110+ tests, Linux + macOS) + browser e2e for the HUD (Playwright)
└── docs/                      ← INSTALL, USAGE, VAULT, BRAIN, APP, ARCHITECTURE, DEVELOPMENT, TROUBLESHOOTING, SECURITY, CHANGELOG
```

## Architecture in one paragraph

Hermes Agent is installed by its official installer and left untouched. JARVIS is a set of Hermes **plugins** (tools +
hooks + skills) plus a personality (`SOUL.md`), a config overlay, and two local services: the **HUD** (Python stdlib HTTP
+ SSE, talks to Hermes through its OpenAI-compatible API on `:8642`) and the **menu-bar app** (one Swift file, a button
over the `jarvis` CLI). Memory and the vault share one SQLite database with FTS5; a nightly cron job lets the agent
review its own knowledge. Proactivity is local first (watchdog + event triggers, zero LLM calls) and calls the model only
when there is a concrete reason. See [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md).

## Requirements

- macOS 13+ (Apple Silicon or Intel), ~3 GB disk (Python, Node, Whisper `small`)
- An API key for any LLM provider **or** Ollama with a local model (then everything is offline except Edge TTS)
- A microphone; macOS permissions for screenshots/hotkeys (the installer and `jarvis selftest --fix` guide you)

## Credits

Core: **Hermes Agent** (NousResearch, MIT). HUD ideas: **eadmin2/jarvis_ai**. Voice command set: **nixfred/MacOS_Mark-XXXV**
and classic Python "Jarvis" projects. Memory design: **Hindsight** (reflect), **Graphiti/Zep** (temporal facts),
**Mem0** (entity linking), **Letta/MemGPT** and *Generative Agents* (self-managed memory). Heartbeat: **OpenClaw**.
Full list with "what was borrowed": [docs/SOURCES.md](docs/SOURCES.md).

## Status & license

**1.8.1 — stable.** 115 automated tests (Linux + macOS, Python 3.11/3.12), `ruff`/`shellcheck`, a Swift build and a real
browser e2e run on every commit; every release ships a prebuilt JARVIS.app. MIT. Hermes Agent — MIT © Nous Research.
