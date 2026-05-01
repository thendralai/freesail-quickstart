"""
Freesail Python Agent — entry point.

Connects to the Freesail gateway via MCP HTTP Streamable transport.
The gateway runs as a separate process; this agent connects to it over HTTP.

Chat communication flows through the A2UI protocol via a __chat surface
rather than a separate HTTP endpoint. When a client connects, the runtime
creates a new FreesailLangchainSessionAgent for that session via the factory
pattern, achieving full per-session state isolation.

Mirrors agent/src/index.ts.
"""

from __future__ import annotations

import asyncio
import logging
import os
import signal
import sys
from pathlib import Path

# Load .env from the project root (one directory above python-agent/)
from dotenv import load_dotenv

_env_path = Path(__file__).resolve().parent.parent / ".env"
load_dotenv(dotenv_path=_env_path)

# ============================================================================
# Logging
# ============================================================================

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(name)s] %(levelname)s %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger("freesail-agent")

# Suppress expected shutdown noise from the MCP SDK's internal reconnect/termination
# logic — these fire on every clean Ctrl+C and are not actionable.
logging.getLogger("mcp.client.streamable_http").setLevel(logging.ERROR)

# ============================================================================
# Configuration
# ============================================================================

MCP_PORT = int(os.environ.get("MCP_PORT", "3000"))
GATEWAY_PORT = int(os.environ.get("GATEWAY_PORT", "3001"))
LLM_PROVIDER = os.environ.get("LLM_PROVIDER", "gemini").lower()
LLM_TEMPERATURE = float(os.environ.get("LLM_TEMPERATURE", "0.7"))

# Custom prompt — loaded once at startup.
# Path resolved from CUSTOM_PROMPT_FILE env var, or defaults to customprompt.txt in the project root.
_project_root = Path(__file__).resolve().parent.parent
_custom_prompt_env = os.environ.get("CUSTOM_PROMPT_FILE", "")
if _custom_prompt_env:
    _custom_prompt_path = Path(_custom_prompt_env) if Path(_custom_prompt_env).is_absolute() else _project_root / _custom_prompt_env
else:
    _custom_prompt_path = _project_root / "customprompt.txt"
CUSTOM_PROMPT = ""
try:
    _content = _custom_prompt_path.read_text(encoding="utf-8").strip()
    if _content:
        CUSTOM_PROMPT = _content
        logger.info("Loaded custom prompt from %s (%d chars)", _custom_prompt_path, len(_content))
except OSError:
    pass


# ============================================================================
# LLM provider selection
# Supported: 'gemini' (default), 'openai', 'claude'
# ============================================================================

def _build_model() -> object:
    if LLM_PROVIDER == "openai":
        api_key = os.environ.get("OPENAI_API_KEY")
        if not api_key:
            logger.critical("OPENAI_API_KEY is required when LLM_PROVIDER=openai.")
            sys.exit(1)
        from langchain_openai import ChatOpenAI
        model_name = os.environ.get("OPENAI_MODEL", "gpt-4o")
        logger.info("LLM provider: OpenAI (%s)", model_name)
        return ChatOpenAI(api_key=api_key, model=model_name, temperature=LLM_TEMPERATURE, streaming=True)

    if LLM_PROVIDER == "claude":
        api_key = os.environ.get("ANTHROPIC_API_KEY")
        if not api_key:
            logger.critical("ANTHROPIC_API_KEY is required when LLM_PROVIDER=claude.")
            sys.exit(1)
        from langchain_anthropic import ChatAnthropic
        model_name = os.environ.get("CLAUDE_MODEL", "claude-sonnet-4-5-20250929")
        logger.info("LLM provider: Anthropic Claude (%s)", model_name)
        return ChatAnthropic(anthropic_api_key=api_key, model=model_name, temperature=LLM_TEMPERATURE, streaming=True)

    if LLM_PROVIDER == "gemini":
        api_key = os.environ.get("GOOGLE_API_KEY")
        if not api_key:
            logger.critical(
                "GOOGLE_API_KEY is required when LLM_PROVIDER=gemini (default). "
                "Set it with: export GOOGLE_API_KEY=your-api-key"
            )
            sys.exit(1)
        from langchain_google_genai import ChatGoogleGenerativeAI
        model_name = os.environ.get("GEMINI_MODEL", "gemini-2.5-pro")
        logger.info("LLM provider: Google Gemini (%s)", model_name)
        return ChatGoogleGenerativeAI(google_api_key=api_key, model=model_name, temperature=LLM_TEMPERATURE)

    logger.critical("Unknown LLM_PROVIDER '%s'. Must be gemini, openai, or claude.", LLM_PROVIDER)
    sys.exit(1)


# ============================================================================
# Main
# ============================================================================

async def main() -> None:
    from mcp import ClientSession
    from mcp.client.streamable_http import streamablehttp_client

    from adapter import MCPAdapter
    from agent import FreesailLangchainSessionAgent
    from runtime import FreesailAgentRuntime, SharedCache

    model = _build_model()

    mcp_url = f"http://localhost:{MCP_PORT}/mcp"
    logger.info("Connecting to Freesail gateway MCP at %s ...", mcp_url)

    # Create the runtime first so its message_handler can be passed to ClientSession.
    # The session and shared_cache are injected after the session is initialised.
    runtime = FreesailAgentRuntime(agent_factory=lambda sid: _make_agent(sid))

    # Placeholders — filled in once the session is ready (before runtime.start())
    _mcp_session_ref: list[ClientSession] = []
    _shared_cache_ref: list[SharedCache] = []

    def _make_agent(session_id: str) -> FreesailLangchainSessionAgent:
        return FreesailLangchainSessionAgent(
            session_id=session_id,
            mcp_session=_mcp_session_ref[0],
            model=model,
            shared_cache=_shared_cache_ref[0],
            custom_prompt=CUSTOM_PROMPT,
        )

    async with streamablehttp_client(mcp_url) as (read_stream, write_stream, _):
        async with ClientSession(
            read_stream,
            write_stream,
            message_handler=runtime.message_handler,
        ) as mcp_session:
            await mcp_session.initialize()
            logger.info("Connected to gateway MCP server via Streamable HTTP")

            # Inject session and cache into the factory closures
            _mcp_session_ref.append(mcp_session)
            shared_cache = SharedCache(
                mcp_session,
                tools_factory=lambda: MCPAdapter.get_tools(mcp_session),
            )
            _shared_cache_ref.append(shared_cache)

            # Log available tools and prompts
            tools_result = await mcp_session.list_tools()
            logger.info("MCP tools: %s", ", ".join(t.name for t in tools_result.tools))

            prompts_result = await mcp_session.list_prompts()
            logger.info("MCP prompts: %s", ", ".join(p.name for p in prompts_result.prompts))

            # Graceful shutdown
            loop = asyncio.get_running_loop()

            def _shutdown() -> None:
                logger.info("Shutting down...")
                runtime.stop()

            for sig in (signal.SIGINT, signal.SIGTERM):
                try:
                    loop.add_signal_handler(sig, _shutdown)
                except (NotImplementedError, RuntimeError):
                    # Windows / non-main-thread fallback
                    signal.signal(sig, lambda s, f: _shutdown())

            logger.info("Chat flows through A2UI __chat surface")
            logger.info("Gateway MCP:  http://localhost:%d/mcp", MCP_PORT)
            logger.info("Gateway HTTP: http://localhost:%d", GATEWAY_PORT)

            await runtime.start(mcp_session)


if __name__ == "__main__":
    asyncio.run(main())
