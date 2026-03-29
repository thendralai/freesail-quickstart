# Freesail Quickstarter

A minimal, fully working Freesail application — chat panel on the left, AI-driven UI surfaces on the right.

---

## What's included

| Directory | Description |
|-----------|-------------|
| `react-app/` | Vite + React UI with a chat panel and a tabbed sidebar for agent-created surfaces |
| `agent/` | LangChain agent that connects to the Freesail gateway via MCP and responds to chat messages |

---

## Prerequisites

- **Node.js 18+** and **npm**
- An API key for one of the supported LLM providers:
  - [Google Gemini](https://aistudio.google.com/app/apikey) (default)
  - [OpenAI](https://platform.openai.com/account/api-keys)
  - [Anthropic Claude](https://console.anthropic.com/)

---

## Quick start

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
bash run-all.sh
```

This starts three processes:
- **Gateway** — MCP server (port 3000) + HTTP/SSE server (port 3001)
- **Agent** — connects to the gateway and handles chat
- **UI** — Vite dev server (port 5173)

**4. Open the app**

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
├── .env.example          # Copy to .env and fill in your API key
├── run-all.sh            # Starts gateway + agent + UI
├── react-app/
│   ├── public/           # Static assets copied to dist/ on build
│   ├── src/
│   │   ├── App.tsx       # Main UI — chat panel + surface sidebar
│   │   └── main.tsx      # React entry point
│   ├── index.html
│   ├── package.json
│   ├── tsconfig.json
│   └── vite.config.ts
└── agent/
    ├── src/
    │   ├── index.ts              # Agent entry point — MCP connection + runtime
    │   ├── langchain-agent.ts    # Per-session agent (chat + tool loop)
    │   └── langchain-adapter.ts  # Wraps MCP tools as LangChain tools
    ├── package.json
    ├── tsconfig.json
    └── vite.config.ts
```
