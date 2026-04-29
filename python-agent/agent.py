"""
Per-session LangChain agent.

Mirrors agent/src/langchain-agent.ts: one instance is created per connected
UI session. Handles chat messages and UI actions, runs the LLM + tool loop,
and pushes streaming updates back to the client via MCP.
"""

from __future__ import annotations

import asyncio
import json
import logging
from datetime import datetime, timezone
from typing import Any, Callable

from langchain_core.messages import AIMessage, HumanMessage, SystemMessage, ToolMessage
from mcp import ClientSession

from runtime import SharedCache

logger = logging.getLogger("freesail-agent.session")


# ---------------------------------------------------------------------------
# Gemini workaround
# ---------------------------------------------------------------------------

def _extract_gemini_tool_calls(chunk: Any) -> Any:
    """
    Workaround for a transient Gemini streaming bug: Gemini 2.5 sometimes
    embeds function calls inside the raw content list instead of tool_calls.
    TODO: Remove once Gemini fixes this in their API.

    Mirrors extractGeminiToolCalls() from agent/src/langchain-agent.ts.
    """
    if chunk is None:
        return chunk
    tool_calls = getattr(chunk, "tool_calls", None) or []
    content = getattr(chunk, "content", None)
    if not tool_calls and isinstance(content, list):
        extracted = []
        for part in content:
            if isinstance(part, dict) and "functionCall" in part:
                fc = part["functionCall"]
                import random, string
                rand_id = "call_" + "".join(random.choices(string.ascii_lowercase + string.digits, k=7))
                extracted.append({
                    "name": fc.get("name"),
                    "args": fc.get("args", {}),
                    "id": rand_id,
                    "type": "tool_call",
                })
        if extracted:
            chunk.tool_calls = extracted
            text_parts = [p for p in content if isinstance(p, dict) and p.get("type") == "text"]
            chunk.content = text_parts if text_parts else ""
    return chunk


# ---------------------------------------------------------------------------
# FreesailLangchainSessionAgent
# ---------------------------------------------------------------------------

class FreesailLangchainSessionAgent:
    """
    Per-session agent implementing the FreesailAgent protocol.

    All per-session state (conversation history, chat log) lives here as
    instance attributes — there is no shared mutable state between sessions.

    Mirrors FreesailLangchainSessionAgent from agent/src/langchain-agent.ts.
    """

    def __init__(
        self,
        session_id: str,
        mcp_session: ClientSession,
        model: Any,
        shared_cache: SharedCache,
    ) -> None:
        self._session_id = session_id
        self._mcp_session = mcp_session
        self._model = model
        self._shared_cache = shared_cache

        # Per-session state
        self._conversation_history: list[HumanMessage | AIMessage | ToolMessage] = []
        self._chat_messages: list[dict[str, str]] = []

    # ------------------------------------------------------------------
    # FreesailAgent lifecycle hooks
    # ------------------------------------------------------------------

    async def on_session_connected(self, session_id: str) -> None:
        self._shared_cache.invalidate()
        logger.info("[%s] Session connected — agent ready", session_id)

    async def on_session_disconnected(self, session_id: str) -> None:
        self._conversation_history = []
        self._chat_messages = []
        logger.info("[%s] Session disconnected — agent state cleared", session_id)

    async def on_action(self, action: dict[str, Any]) -> None:
        # Route chat_send on __chat surface → conversational reply
        if action.get("name") == "chat_send" and action.get("surfaceId") == "__chat":
            chat_text = (action.get("context") or {}).get("text", "")
            if chat_text:
                await self._handle_chat(chat_text, is_user_chat=True)
            return

        context = action.get("context") or {}
        data_model = action.get("clientDataModel") or {}

        context_str = (
            f"\nAction data: {json.dumps(context, indent=2)}"
            if context
            else ""
        )
        data_model_str = (
            f"\nClient data model: {json.dumps(data_model, indent=2)}"
            if data_model
            else ""
        )

        # System actions (sourceComponentId == "__system") are directives from the
        # framework, not user interactions. Format them as explicit correction
        # instructions so the LLM calls the right tool rather than replying in chat.
        if action.get("sourceComponentId") == "__system":
            hint = (action.get("context") or {}).get("message", "")
            message = (
                f'[System Directive] The Freesail framework sent a "{action.get("name")}" '
                f'notification for surface "{action.get("surfaceId")}". '
                f"You MUST call the appropriate tool to fix this — do NOT reply in chat.\n"
                f"{hint}{context_str}"
            )
        else:
            message = (
                f'[UI Action] The user clicked "{action.get("name")}" on component '
                f'"{action.get("sourceComponentId")}" in surface "{action.get("surfaceId")}".'
                f"{context_str}{data_model_str}"
            )

        logger.info("[%s] Action: %s", self._session_id, action.get("name"))
        await self._handle_chat(message, is_user_chat=False)

    # ------------------------------------------------------------------
    # Chat data model helpers
    # ------------------------------------------------------------------

    async def _update_chat_model(self, path: str, value: Any) -> None:
        try:
            await self._mcp_session.call_tool(
                "update_data_model",
                arguments={
                    "surfaceId": "__chat",
                    "sessionId": self._session_id,
                    "path": path,
                    "value": value,
                },
            )
        except Exception as exc:
            logger.error("[%s] update_data_model error: %s", self._session_id, exc)

    # ------------------------------------------------------------------
    # Internal chat handler
    # ------------------------------------------------------------------

    async def _handle_chat(self, message: str, is_user_chat: bool) -> None:
        try:
            if is_user_chat:
                self._chat_messages.append({
                    "role": "user",
                    "content": message,
                    "timestamp": datetime.now(timezone.utc).isoformat(),
                })

            # Show user message and activate AgentStream
            await self._update_chat_model("/", {
                "messages": list(self._chat_messages),
                "isTyping": True,
                "stream": {"token": "", "active": True},
            })

            session_prompt = (
                f'[Session Context] The following message is from session "{self._session_id}". '
                f"When calling ANY tool (create_surface, update_components, update_data_model, delete_surface), "
                f'you MUST use sessionId: "{self._session_id}". Do NOT reuse a sessionId from a previous message.\n'
                f"Just reply normally in chat for standard conversation. "
                f"Only create new surfaces when you think the user needs visual UI.\n\n"
                f"Today's date is {datetime.now(timezone.utc).strftime('%Y-%m-%d')}\n\n"
                f"User: {message}"
            )

            def on_token(token: str) -> None:
                # Fire-and-forget — mirrors TS: onToken fires updateChatModel().catch(...)
                # without awaiting, so streaming is never blocked by MCP round-trips.
                asyncio.ensure_future(
                    self._update_chat_model("/stream/token", token)
                )

            response = await self._chat(session_prompt, on_token=on_token)

            if response and response.strip():
                self._chat_messages.append({
                    "role": "assistant",
                    "content": response,
                    "timestamp": datetime.now(timezone.utc).isoformat(),
                })

            logger.info("[%s] Assistant: %s...", self._session_id, response[:120] if response else "")

            await self._update_chat_model("/", {
                "messages": list(self._chat_messages),
                "isTyping": False,
                "stream": {"token": "", "active": False},
            })

        except Exception as exc:
            logger.error("[%s] Chat error: %s", self._session_id, exc)
            self._chat_messages.append({
                "role": "assistant",
                "content": "An error occurred.",
                "timestamp": datetime.now(timezone.utc).isoformat(),
            })
            await self._update_chat_model("/", {
                "messages": list(self._chat_messages),
                "isTyping": False,
                "stream": {"token": "", "active": False},
            })

    # ------------------------------------------------------------------
    # LLM execution loop
    # ------------------------------------------------------------------

    async def _stream_model_response(
        self,
        model_with_tools: Any,
        messages: list,
        on_token: Callable[[str], None] | None = None,
    ) -> Any:
        """Stream the model, call on_token for each text piece, return final chunk."""
        stream = model_with_tools.astream(messages)
        final_chunk: Any = None

        try:
            async for chunk in stream:
                content = getattr(chunk, "content", "")
                if isinstance(content, str) and content:
                    if on_token:
                        on_token(content)
                elif isinstance(content, list):
                    for part in content:
                        if isinstance(part, dict) and part.get("type") == "text" and part.get("text"):
                            if on_token:
                                on_token(part["text"])

                if final_chunk is None:
                    final_chunk = chunk
                else:
                    try:
                        final_chunk = final_chunk + chunk
                    except Exception:
                        final_chunk = chunk  # fallback: keep last chunk
        except (AttributeError, TypeError) as exc:
            # langchain-google-genai sometimes emits a trailing finish-reason chunk
            # with no candidate content, raising on 'parts'. Treat as clean stream end.
            if "parts" not in str(exc):
                raise

        return _extract_gemini_tool_calls(final_chunk)

    async def _chat(
        self,
        user_message: str,
        on_token: Callable[[str], None] | None = None,
    ) -> str:
        system_prompt = await self._shared_cache.get_system_prompt()
        current_tools = await self._shared_cache.get_tools()
        model_with_tools = self._model.bind_tools(current_tools)

        self._conversation_history.append(HumanMessage(user_message))

        messages = [SystemMessage(system_prompt), *self._conversation_history]
        response_chunk = await self._stream_model_response(model_with_tools, messages, on_token)

        turn_tool_messages: list[AIMessage | ToolMessage] = []

        while getattr(response_chunk, "tool_calls", None):
            tool_calls = response_chunk.tool_calls
            content = getattr(response_chunk, "content", "")
            if not isinstance(content, str):
                content = ""

            ai_msg = AIMessage(content=content, tool_calls=tool_calls)
            turn_tool_messages.append(ai_msg)

            tool_result_messages: list[ToolMessage] = []
            for tool_call in tool_calls:
                tool_name = tool_call.get("name") or tool_call.get("function", {}).get("name", "")
                tool_args = tool_call.get("args") or tool_call.get("function", {}).get("arguments", {})
                if isinstance(tool_args, str):
                    try:
                        tool_args = json.loads(tool_args)
                    except Exception:
                        tool_args = {}
                tool_call_id = tool_call.get("id") or tool_name

                matched_tool = next(
                    (t for t in current_tools if t.name == tool_name), None
                )
                try:
                    if matched_tool:
                        result = str(await matched_tool.ainvoke(tool_args))
                    else:
                        result = f"Unknown tool: {tool_name}"
                except Exception as exc:
                    result = f"Error: {exc}"
                    logger.error("Tool error (%s): %s", tool_name, exc)

                tool_result_messages.append(
                    ToolMessage(
                        content=result,
                        name=tool_name,
                        tool_call_id=tool_call_id,
                    )
                )

            turn_tool_messages.extend(tool_result_messages)
            response_chunk = await self._stream_model_response(
                model_with_tools,
                [SystemMessage(system_prompt), *self._conversation_history, *turn_tool_messages],
                on_token,
            )

        self._conversation_history.extend(turn_tool_messages)

        # Extract final text
        content = getattr(response_chunk, "content", "") if response_chunk else ""
        if isinstance(content, list):
            assistant_message = "".join(
                p.get("text", "") if isinstance(p, dict) else str(p)
                for p in content
            )
        elif isinstance(content, str):
            assistant_message = content
        else:
            assistant_message = json.dumps(content) if content else ""

        if assistant_message.strip():
            self._conversation_history.append(AIMessage(assistant_message))

        return assistant_message
