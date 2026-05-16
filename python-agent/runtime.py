"""
Freesail Agent Runtime — Python implementation.

Mirrors @freesail/agent-runtime (TypeScript).

Architecture:
  - One coordinator MCP client that subscribes to the sessions list
    and provides shared tool definitions / system prompt.
  - One dedicated MCP client per active UI session — the gateway enforces
    a maximum of 1 claimed session per MCP connection, so each session needs
    its own connection.

Mirrors FreesailAgentRuntime from @freesail/agent-runtime.
"""

from __future__ import annotations

import asyncio
import json
import logging
import re
from collections.abc import Callable, Awaitable
from dataclasses import dataclass
from typing import Any, Protocol, runtime_checkable

import mcp.types as types
from mcp import ClientSession
from pydantic import AnyUrl

logger = logging.getLogger("freesail-agent.runtime")

SESSIONS_URI = "mcp://freesail.dev/sessions"
_SESSION_URI_RE = re.compile(r"^mcp://freesail\.dev/sessions/(.+)$")


# ---------------------------------------------------------------------------
# ToolDefinition — mirrors ToolDefinition from @freesail/agent-runtime
# ---------------------------------------------------------------------------

@dataclass
class ToolDefinition:
    name: str
    description: str
    input_schema: dict[str, Any]


# ---------------------------------------------------------------------------
# FreesailSessionClient — per-session MCP wrapper
# ---------------------------------------------------------------------------

class FreesailSessionClient:
    """
    Per-session client wrapping the dedicated MCP session for this session.
    Provides update_data_model and call_tool scoped to a specific session.
    Mirrors FreesailSessionClient from @freesail/agent-runtime.
    """

    def __init__(self, session_id: str, mcp_session: ClientSession) -> None:
        self._session_id = session_id
        self._mcp_session = mcp_session

    async def update_data_model(self, surface_id: str, path: str, value: Any) -> None:
        try:
            await self._mcp_session.call_tool(
                "update_data_model",
                arguments={
                    "surfaceId": surface_id,
                    "sessionId": self._session_id,
                    "path": path,
                    "value": value,
                },
            )
        except Exception as exc:
            logger.error("[%s] update_data_model error: %s", self._session_id, exc)

    async def call_tool(self, name: str, args: dict[str, Any]) -> Any:
        result = await self._mcp_session.call_tool(name, arguments=args)
        parts = result.content or []
        return "\n".join(
            (p.text if hasattr(p, "text") and p.type == "text" else str(p))
            for p in parts
        )


# ---------------------------------------------------------------------------
# FreesailToolProvider — protocol for shared prompt/tool access
# ---------------------------------------------------------------------------

@runtime_checkable
class FreesailToolProvider(Protocol):
    async def get_system_prompt(self) -> str: ...
    async def get_tool_definitions(self) -> list[ToolDefinition]: ...


# ---------------------------------------------------------------------------
# FreesailAgent — protocol for session agent implementations
# ---------------------------------------------------------------------------

@runtime_checkable
class FreesailAgent(Protocol):
    async def on_session_connected(self, session_id: str) -> None: ...
    async def on_session_disconnected(self, session_id: str) -> None: ...
    async def on_session_notification(self, notification: dict[str, Any]) -> None: ...


AgentFactory = Callable[[str, FreesailSessionClient], FreesailAgent]


# ---------------------------------------------------------------------------
# _SharedCache — coordinator-level prompt/tool cache
# ---------------------------------------------------------------------------

class _SharedCache:
    """
    Caches system prompt and tool definitions fetched via the coordinator client.
    Stores asyncio Tasks so concurrent callers share the same in-flight fetch.
    """

    def __init__(self, mcp_session: ClientSession) -> None:
        self._session = mcp_session
        self._system_prompt_task: asyncio.Task[str] | None = None
        self._tool_defs_task: asyncio.Task[list[ToolDefinition]] | None = None

    def invalidate(self) -> None:
        self._system_prompt_task = None
        self._tool_defs_task = None

    async def get_system_prompt(self) -> str:
        if self._system_prompt_task is None:
            self._system_prompt_task = asyncio.create_task(self._fetch_prompt())
        try:
            return await self._system_prompt_task
        except Exception:
            self._system_prompt_task = None
            raise

    async def get_tool_definitions(self) -> list[ToolDefinition]:
        if self._tool_defs_task is None:
            self._tool_defs_task = asyncio.create_task(self._fetch_tool_definitions())
        try:
            return await self._tool_defs_task
        except Exception:
            self._tool_defs_task = None
            raise

    async def _fetch_prompt(self) -> str:
        try:
            result = await self._session.get_prompt("a2ui_system", arguments={})
            parts = result.messages or []
            texts = []
            for msg in parts:
                content = msg.content
                if hasattr(content, "text"):
                    texts.append(content.text)
                elif isinstance(content, str):
                    texts.append(content)
            return "\n".join(texts)
        except Exception as exc:
            logger.warning("Failed to fetch system prompt: %s — using empty fallback", exc)
            return (
                "You are a helpful AI assistant with access to Freesail tools. "
                "Use the available tools to create and manage UI surfaces when the user needs visual output."
            )

    async def _fetch_tool_definitions(self) -> list[ToolDefinition]:
        result = await self._session.list_tools()
        return [
            ToolDefinition(
                name=t.name,
                description=t.description or f"Freesail tool: {t.name}",
                input_schema=dict(t.inputSchema) if t.inputSchema else {},
            )
            for t in result.tools
        ]


# ---------------------------------------------------------------------------
# FreesailAgentRuntime
# ---------------------------------------------------------------------------

class FreesailAgentRuntime:
    """
    Session-based agent runtime.

    Manages one coordinator MCP connection for the sessions list and creates
    a dedicated MCP client per UI session (the gateway allows one claimed
    session per MCP connection).

    Mirrors FreesailAgentRuntime from @freesail/agent-runtime.
    """

    def __init__(
        self,
        gateway_url: str,
        client_info: dict[str, str],
        agent_factory: AgentFactory,
    ) -> None:
        self._gateway_url = gateway_url
        self._client_info = client_info
        self._agent_factory = agent_factory

        # Coordinator state
        self._coordinator: ClientSession | None = None
        self._shared_cache: _SharedCache | None = None

        # Per-session state
        self._active_agents: dict[str, FreesailAgent] = {}
        self._session_clients: dict[str, ClientSession] = {}
        self._known_sessions: set[str] = set()
        self._session_disconnect_events: dict[str, asyncio.Event] = {}
        self._session_tasks: dict[str, asyncio.Task[Any]] = {}
        self._session_chains: dict[str, asyncio.Task[Any]] = {}

        self._stop_event = asyncio.Event()

    # ------------------------------------------------------------------
    # FreesailToolProvider implementation (delegates to coordinator)
    # ------------------------------------------------------------------

    async def get_system_prompt(self) -> str:
        assert self._shared_cache is not None, "Runtime not started"
        return await self._shared_cache.get_system_prompt()

    async def get_tool_definitions(self) -> list[ToolDefinition]:
        assert self._shared_cache is not None, "Runtime not started"
        return await self._shared_cache.get_tool_definitions()

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    async def start(self) -> None:
        """Connect the coordinator, subscribe to sessions, and run until stop()."""
        from mcp import ClientSession as _ClientSession
        from mcp.client.streamable_http import streamablehttp_client

        async with streamablehttp_client(self._gateway_url) as (read_stream, write_stream, _):
            async with _ClientSession(
                read_stream,
                write_stream,
                message_handler=self._coordinator_message_handler,
            ) as coordinator:
                await coordinator.initialize()
                logger.info("Connected to gateway MCP server")

                self._coordinator = coordinator
                self._shared_cache = _SharedCache(coordinator)

                tools_result = await coordinator.list_tools()
                logger.info("MCP tools: %s", ", ".join(t.name for t in tools_result.tools))

                prompts_result = await coordinator.list_prompts()
                logger.info("MCP prompts: %s", ", ".join(p.name for p in prompts_result.prompts))

                await coordinator.subscribe_resource(AnyUrl(SESSIONS_URI))
                await self._handle_sessions_update()

                logger.info("Agent runtime started — listening for sessions")
                await self._stop_event.wait()

    def stop(self) -> None:
        """Signal the runtime to shut down."""
        self._stop_event.set()
        for event in self._session_disconnect_events.values():
            event.set()

    # ------------------------------------------------------------------
    # Coordinator message handler
    # ------------------------------------------------------------------

    async def _coordinator_message_handler(self, message: Any) -> None:
        if not isinstance(message, types.ServerNotification):
            return
        notification = message.root
        if not isinstance(notification, types.ResourceUpdatedNotification):
            return
        if str(notification.params.uri) == SESSIONS_URI:
            asyncio.ensure_future(self._handle_sessions_update())

    # ------------------------------------------------------------------
    # Sessions list management
    # ------------------------------------------------------------------

    async def _handle_sessions_update(self) -> None:
        assert self._coordinator is not None
        try:
            resource = await self._coordinator.read_resource(AnyUrl(SESSIONS_URI))
            raw = _extract_text(resource.contents)
            active: list[dict] = json.loads(raw) if raw else []
        except Exception as exc:
            logger.warning("Failed to read sessions resource: %s", exc)
            return

        active_set = {s["id"] for s in active}
        new_sessions = active_set - self._known_sessions
        removed_sessions = self._known_sessions - active_set

        logger.info(
            "Sessions update — active: %s, known: %s, new: %s, removed: %s",
            active_set, self._known_sessions, new_sessions, removed_sessions,
        )

        for sid in new_sessions:
            self._known_sessions.add(sid)
            task = asyncio.create_task(self._safe_run_session(sid))
            self._session_tasks[sid] = task

        for sid in removed_sessions:
            self._known_sessions.discard(sid)
            event = self._session_disconnect_events.get(sid)
            if event is not None:
                event.set()

    # ------------------------------------------------------------------
    # Per-session connection lifecycle
    # ------------------------------------------------------------------

    async def _safe_run_session(self, session_id: str) -> None:
        try:
            await self._run_session(session_id)
        except Exception as exc:
            logger.error("[%s] Session task error: %s", session_id, exc)
        finally:
            agent = self._active_agents.get(session_id)
            if agent is not None:
                try:
                    await agent.on_session_disconnected(session_id)
                except Exception as exc:
                    logger.error("[%s] on_session_disconnected error: %s", session_id, exc)
            self._known_sessions.discard(session_id)
            self._session_clients.pop(session_id, None)
            self._active_agents.pop(session_id, None)
            self._session_disconnect_events.pop(session_id, None)
            self._session_tasks.pop(session_id, None)
            self._session_chains.pop(session_id, None)
            logger.info("[%s] Session cleaned up", session_id)

    async def _run_session(self, session_id: str) -> None:
        """
        Creates a dedicated MCP client for this session and runs it until disconnect.
        Retries on network errors; stops immediately on gateway rejection or shutdown.
        """
        if self._stop_event.is_set():
            return

        disconnect_event = asyncio.Event()
        self._session_disconnect_events[session_id] = disconnect_event

        RETRY_DELAYS = [0.2, 1.0]

        for attempt in range(len(RETRY_DELAYS) + 1):
            if attempt > 0:
                if self._stop_event.is_set():
                    return
                await asyncio.sleep(RETRY_DELAYS[attempt - 1])
            try:
                result = await self._connect_and_claim(
                    session_id, disconnect_event, attempt, len(RETRY_DELAYS) + 1
                )
                if result is True:
                    return  # Session ran to completion
                if result is False:
                    break   # Gateway rejected claim — don't retry
                # result is None: network error, retry with a new connection
            except Exception as exc:
                if self._stop_event.is_set():
                    return  # Connection tore down during shutdown — expected
                logger.warning(
                    "[%s] Session connection error (attempt %d/%d): %s",
                    session_id, attempt + 1, len(RETRY_DELAYS) + 1, exc,
                )

        logger.warning("[%s] Gave up connecting after %d attempt(s)", session_id, len(RETRY_DELAYS) + 1)
        self._known_sessions.discard(session_id)

    async def _connect_and_claim(
        self,
        session_id: str,
        disconnect_event: asyncio.Event,
        attempt: int,
        max_attempts: int,
    ) -> bool | None:
        """
        Opens a fresh MCP connection and attempts to claim the session.

        Returns:
          True  — session claimed and ran to completion
          False — gateway explicitly rejected (don't retry)
          None  — network/transport error (caller should retry)
        """
        from mcp import ClientSession as _ClientSession
        from mcp.client.streamable_http import streamablehttp_client

        session_uri = f"mcp://freesail.dev/sessions/{session_id}"

        async with streamablehttp_client(self._gateway_url) as (read, write, _):
            async with _ClientSession(
                read, write,
                message_handler=self._make_session_handler(session_id),
            ) as sess:
                await sess.initialize()

                # Attempt to claim the session
                try:
                    claim_result = await sess.call_tool(
                        "claim_session", arguments={"sessionId": session_id}
                    )
                    raw = _extract_text(claim_result.content)
                    parsed = json.loads(raw) if raw else {}
                    if not parsed.get("success", False):
                        logger.warning(
                            "[%s] claim_session rejected (attempt %d/%d): %s",
                            session_id, attempt + 1, max_attempts, parsed.get("error", "unknown"),
                        )
                        return False  # Gateway explicitly rejected — stop retrying
                except Exception as exc:
                    logger.warning(
                        "[%s] claim_session network error (attempt %d/%d): %s",
                        session_id, attempt + 1, max_attempts, exc,
                    )
                    return None  # Transport error — caller will retry with a new connection

                # Claimed — register this session's dedicated client
                self._session_clients[session_id] = sess

                # Subscribe to per-session resource notifications
                try:
                    await sess.subscribe_resource(AnyUrl(session_uri))
                except Exception as exc:
                    logger.warning("[%s] subscribe error: %s", session_id, exc)

                # Create the per-session agent
                session_client = FreesailSessionClient(session_id, sess)
                agent = self._agent_factory(session_id, session_client)
                self._active_agents[session_id] = agent

                logger.info("[%s] Session connected", session_id)
                try:
                    await agent.on_session_connected(session_id)
                except Exception as exc:
                    logger.error("[%s] on_session_connected error: %s", session_id, exc)

                # Drain any actions that arrived before we subscribed
                await self._handle_session_actions(session_id, sess)

                # Keep the dedicated connection alive until the coordinator
                # signals that this session has been removed from the active list
                await disconnect_event.wait()

                # Release the claim before the connection closes
                try:
                    await sess.call_tool("release_session", arguments={"sessionId": session_id})
                except Exception as exc:
                    logger.warning("[%s] release_session error: %s", session_id, exc)

                return True

    # ------------------------------------------------------------------
    # Per-session notification handler factory
    # ------------------------------------------------------------------

    def _make_session_handler(self, session_id: str) -> Any:
        """Returns a message_handler for the dedicated per-session MCP client."""
        async def _handler(message: Any) -> None:
            if not isinstance(message, types.ServerNotification):
                return
            notification = message.root
            if not isinstance(notification, types.ResourceUpdatedNotification):
                return
            uri = str(notification.params.uri)
            m = _SESSION_URI_RE.match(uri)
            if not (m and m.group(1) == session_id):
                return
            client = self._session_clients.get(session_id)
            if client is not None:
                self._enqueue_session_work(
                    session_id, lambda: self._handle_session_actions(session_id, client)
                )
        return _handler

    # ------------------------------------------------------------------
    # Notification dispatch
    # ------------------------------------------------------------------

    async def _handle_session_actions(self, session_id: str, client: ClientSession) -> None:
        agent = self._active_agents.get(session_id)
        if agent is None:
            return

        per_session_uri = f"mcp://freesail.dev/sessions/{session_id}"
        try:
            resource = await client.read_resource(AnyUrl(per_session_uri))
            raw = _extract_text(resource.contents)
            events: list[dict[str, Any]] = json.loads(raw) if raw else []
        except Exception as exc:
            logger.warning("[%s] Failed to read session resource: %s", session_id, exc)
            return

        for event in events:
            if "error" in event:
                err = event["error"]
                notification: dict[str, Any] = {
                    "type": "error",
                    "event": {
                        "surfaceId": err.get("surfaceId", ""),
                        "code": err.get("code", "UNKNOWN"),
                        "message": err.get("message", ""),
                        "path": err.get("path"),
                    },
                }
                try:
                    await agent.on_session_notification(notification)
                except Exception as exc:
                    logger.error("[%s] on_session_notification error: %s", session_id, exc)
                continue

            raw_action = event.get("action")
            if not raw_action or not isinstance(raw_action.get("name"), str):
                continue
            if raw_action["name"].startswith("__session_"):
                continue

            client_data_model = (event.get("dataModel") or {}).get("dataModel")
            if client_data_model is not None:
                raw_action = {**raw_action, "clientDataModel": client_data_model}

            notification = {"type": "action", "event": raw_action}
            try:
                await agent.on_session_notification(notification)
            except Exception as exc:
                logger.error("[%s] on_session_notification error: %s", session_id, exc)

    # ------------------------------------------------------------------
    # Per-session serial queue
    # ------------------------------------------------------------------

    def _enqueue_session_work(
        self, session_id: str, coro_fn: Callable[[], Awaitable[Any]]
    ) -> asyncio.Task[Any]:
        # Accept a factory (not a coroutine object) so the coroutine is created
        # only when we're about to await it — prevents "coroutine never awaited"
        # warnings when the task is cancelled during shutdown.
        prev = self._session_chains.get(session_id)

        async def _chained() -> None:
            if prev is not None:
                try:
                    await prev
                except Exception:
                    pass
            await coro_fn()

        task = asyncio.create_task(_chained())
        self._session_chains[session_id] = task
        return task


def _extract_text(contents: Any) -> str:
    if not contents:
        return ""
    parts = []
    for item in contents:
        if hasattr(item, "text"):
            parts.append(item.text)
        elif isinstance(item, str):
            parts.append(item)
    return "".join(parts)
