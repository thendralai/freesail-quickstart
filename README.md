# Freesail Quickstarter

A minimal, fully working Freesail application — chat panel on the left, AI-driven UI surfaces on the right.

---

## What's included

| Directory | Description |
|-----------|-------------|
| `react-app/` | Vite + React UI with a chat panel and a tabbed sidebar for agent-created surfaces |
| `agent/` | LangChain agent (TypeScript) — connects to the Freesail gateway via MCP and responds to chat messages |
| `python-agent/` | LangChain agent (Python) — feature-equivalent alternative to the TypeScript agent |

---

## Prerequisites

- **Node.js 18+** and **npm** (gateway + UI)
- **Python 3.11+** (only if using the Python agent)
- An API key for one of the supported LLM providers:
  - [Google Gemini](https://aistudio.google.com/app/apikey) (default)
  - [OpenAI](https://platform.openai.com/account/api-keys)
  - [Anthropic Claude](https://console.anthropic.com/)

---

## Quick start

Choose either the **TypeScript agent** or the **Python agent** — both are feature-equivalent.

### Option A: TypeScript agent

**1. Install dependencies**

```bash
cd react-app && npm install && cd ..
cd agent && npm install && cd ..
```

**2. Configure your API key**

```bash
cp .env.example .env
```

Open `.env` and fill in your API key (see [Configuration](#configuration) below).

**3. Start the stack**

```bash
# Linux / macOS
bash run-all.sh

# Windows (PowerShell)
.\run-all.ps1
```

### Option B: Python agent

**1. Install dependencies**

```bash
# Linux / macOS
cd react-app && npm install && cd ..
```

```powershell
# Windows (PowerShell)
cd react-app; npm install; cd ..
```

**2. Configure your API key**

```bash
cp .env.example .env      # Linux / macOS
```
```powershell
Copy-Item .env.example .env   # Windows (PowerShell)
```

Open `.env` and fill in your API key (see [Configuration](#configuration) below).

**3. Start the stack**

```bash
# Linux / macOS
bash run-all-py.sh

# Windows (PowerShell)
.\run-all-py.ps1
```

### Open the app

Both options start three processes:
- **Gateway** — MCP server (port 3000) + HTTP/SSE server (port 3001)
- **Agent** — connects to the gateway and handles chat
- **UI** — Vite dev server (port 5173)

```
http://localhost:5173
```

---

## Configuration

All configuration lives in `.env` at the project root (copied from `.env.example`).

| Variable | Default | Description |
|----------|---------|-------------|
| `LLM_PROVIDER` | `gemini` | LLM backend: `gemini`, `openai`, or `claude` |
| `GOOGLE_API_KEY` | — | Required when `LLM_PROVIDER=gemini` |
| `GEMINI_MODEL` | `gemini-2.5-pro` | Gemini model name |
| `OPENAI_API_KEY` | — | Required when `LLM_PROVIDER=openai` |
| `OPENAI_MODEL` | `gpt-4o` | OpenAI model name |
| `ANTHROPIC_API_KEY` | — | Required when `LLM_PROVIDER=claude` |
| `CLAUDE_MODEL` | `claude-sonnet-4-5-20250929` | Anthropic model name |
| `LLM_TEMPERATURE` | `0.7` | Sampling temperature |
| `GATEWAY_PORT` | `3001` | Gateway HTTP/SSE port |
| `MCP_PORT` | `3000` | Gateway MCP port (agent only, localhost) |

---

## LLM providers

Switch providers by setting `LLM_PROVIDER` in `.env`:

```bash
# Google Gemini (default)
LLM_PROVIDER=gemini
GOOGLE_API_KEY=your-key

# OpenAI
LLM_PROVIDER=openai
OPENAI_API_KEY=your-key

# Anthropic Claude
LLM_PROVIDER=claude
ANTHROPIC_API_KEY=your-key
```

---

## Architecture

The Freesail gateway runs as a standalone process, exposing an MCP endpoint (port 3000, localhost only) for the agent and an HTTP/SSE endpoint (port 3001) for the React UI. When the UI connects, the agent receives a session and initialises the `__chat` surface. All subsequent chat messages and UI actions flow through the gateway — the agent reads them via MCP, processes them with the LLM, and pushes UI updates back through the A2UI protocol.

---

## Project structure

```
freesail-quickstart/
├── .env.example              # Copy to .env and fill in your API key
├── run-all.sh                # Starts gateway + TS agent + UI (Linux/macOS)
├── run-all.ps1               # Starts gateway + TS agent + UI (Windows)
├── run-all-py.sh         # Starts gateway + Python agent + UI (Linux/macOS)
├── run-all-py.ps1        # Starts gateway + Python agent + UI (Windows)
├── react-app/
│   ├── public/               # Static assets copied to dist/ on build
│   ├── src/
│   │   ├── App.tsx           # Main UI — chat panel + surface sidebar
│   │   └── main.tsx          # React entry point
│   ├── index.html
│   ├── package.json
│   ├── tsconfig.json
│   └── vite.config.ts
├── agent/                    # TypeScript agent
│   ├── src/
│   │   ├── index.ts              # Agent entry point — MCP connection + runtime
│   │   ├── langchain-agent.ts    # Per-session agent (chat + tool loop)
│   │   └── langchain-adapter.ts  # Wraps MCP tools as LangChain tools
│   ├── package.json
│   ├── tsconfig.json
│   └── vite.config.ts
├── python-agent/             # Python agent (feature-equivalent to agent/)
│   ├── requirements.txt
│   ├── main.py               # Agent entry point — MCP connection + runtime
│   ├── runtime.py            # Session runtime (FreesailAgentRuntime + SharedCache)
│   ├── agent.py              # Per-session agent (chat + tool loop)
│   └── adapter.py            # Wraps MCP tools as LangChain StructuredTools
├── run-all-py.sh         # Starts gateway + Python agent + UI (Linux/macOS)
└── run-all-py.ps1        # Starts gateway + Python agent + UI (Windows)

```
