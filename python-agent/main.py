"""
Freesail Python Agent — entry point.

Connects to the Freesail gateway via the agent runtime.
The runtime manages the MCP connection internally.

Chat communication flows through the A2UI protocol via a __chat surface.

Mirrors agent/src/index.ts.
"""

from __future__ import annotations

import asyncio
import logging
import os
import signal
import sys
from pathlib import Path

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

# Suppress expected shutdown noise from the MCP SDK's internal reconnect/termination logic.
logging.getLogger("mcp.client.streamable_http").setLevel(logging.ERROR)

# ============================================================================
# Configuration
# ============================================================================

MCP_PORT = int(os.environ.get("MCP_PORT", "3000"))
GATEWAY_PORT = int(os.environ.get("GATEWAY_PORT", "3001"))
AGENT_ID = "freesail-quickstart-agent"
LLM_PROVIDER = os.environ.get("LLM_PROVIDER", "gemini").lower()
LLM_TEMPERATURE = float(os.environ.get("LLM_TEMPERATURE", "0.7"))

# Custom prompt — loaded once at startup.
# Path resolved from CUSTOM_PROMPT_FILE env var, or defaults to customprompt.txt in the project root.
_project_root = Path(__file__).resolve().parent.parent
_custom_prompt_env = os.environ.get("CUSTOM_PROMPT_FILE", "")
if _custom_prompt_env:
    _custom_prompt_path = (
        Path(_custom_prompt_env)
        if Path(_custom_prompt_env).is_absolute()
        else _project_root / _custom_prompt_env
    )
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
    from agent import FreesailLangchainSessionAgent
    from runtime import FreesailAgentRuntime

    model = _build_model()

    mcp_url = f"http://localhost:{MCP_PORT}/mcp"
    logger.info("Connecting to Freesail gateway at %s ...", mcp_url)

    runtime = FreesailAgentRuntime(
        gateway_url=mcp_url,
        client_info={"name": "freesail-agent", "version": "0.1.0"},
        agent_factory=lambda session_id, session: FreesailLangchainSessionAgent(
            session_id=session_id,
            session=session,
            model=model,
            runtime=runtime,
            custom_prompt=CUSTOM_PROMPT,
        ),
    )

    loop = asyncio.get_running_loop()

    def _shutdown() -> None:
        logger.info("Shutting down...")
        runtime.stop()

    for sig in (signal.SIGINT, signal.SIGTERM):
        try:
            loop.add_signal_handler(sig, _shutdown)
        except (NotImplementedError, RuntimeError):
            signal.signal(sig, lambda s, f: _shutdown())

    logger.info("Chat flows through A2UI __chat surface")
    logger.info("Gateway MCP:  http://localhost:%d/mcp", MCP_PORT)
    logger.info("Gateway HTTP: http://localhost:%d", GATEWAY_PORT)
    logger.info("Agent ID: %s", AGENT_ID)

    await runtime.start()


if __name__ == "__main__":
    asyncio.run(main())
