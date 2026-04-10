"""
Freesail Agent Runtime — Python implementation.

Mirrors @freesail/agent-runtime (TypeScript) which is not yet available on PyPI.
Manages per-session lifecycle by subscribing to MCP resources on the gateway:

  mcp://freesail.dev/sessions              — global list of active sessions
  mcp://freesail.dev/sessions/{sessionId}  — per-session action queue

When a session appears the runtime claims it, creates an agent via the
factory, and forwards incoming actions. When a session disappears it drains
in-flight work then releases the session.
"""

from __future__ import annotations

import asyncio
import json
import logging
import re
from collections.abc import Callable, Awaitable
from typing import Any, Protocol, runtime_checkable

import mcp.types as types
from mcp import ClientSession
from pydantic import AnyUrl

logger = logging.getLogger("freesail-agent.runtime")

SESSIONS_URI = "mcp://freesail.dev/sessions"
_SESSION_URI_RE = re.compile(r"^mcp://freesail\.dev/sessions/(.+)$")


# ---------------------------------------------------------------------------
# Protocol — agent implementations only need to implement the methods they use
# ---------------------------------------------------------------------------

@runtime_checkable
class FreesailAgent(Protocol):
    async def on_session_connected(self, session_id: str) -> None: ...
    async def on_session_disconnected(self, session_id: str) -> None: ...
    async def on_action(self, action: dict[str, Any]) -> None: ...


AgentFactory = Callable[[str], FreesailAgent]


# ---------------------------------------------------------------------------
# SharedCache
# ---------------------------------------------------------------------------

class SharedCache:
    """
    Promise-style cache for the system prompt and LangChain tools.

    Stores asyncio Tasks (not resolved values) so concurrent callers
    awaiting the same fetch all share the same in-flight coroutine — no
    thundering-herd duplicate fetches.

    Mirrors SharedCache<TTools> from @freesail/agent-runtime.
    """

    def __init__(
        self,
        mcp_session: ClientSession,
        tools_factory: Callable[[], Awaitable[Any]],
    ) -> None:
        self._session = mcp_session
        self._tools_factory = tools_factory
        self._system_prompt_task: asyncio.Task[str] | None = None
        self._tools_task: asyncio.Task[Any] | None = None

    def invalidate(self) -> None:
        """Clear the cache so the next caller fetches fresh data."""
        self._system_prompt_task = None
        self._tools_task = None

    async def get_system_prompt(self) -> str:
        if self._system_prompt_task is None:
            self._system_prompt_task = asyncio.create_task(self._fetch_prompt())
        try:
            return await self._system_prompt_task
        except Exception:
            self._system_prompt_task = None
            raise

    async def get_tools(self) -> Any:
        if self._tools_task is None:
            self._tools_task = asyncio.create_task(self._tools_factory())
        try:
            return await self._tools_task
        except Exception:
            self._tools_task = None
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


# ---------------------------------------------------------------------------
# FreesailAgentRuntime
# ---------------------------------------------------------------------------

class FreesailAgentRuntime:
    """
    Session-based agent runtime.

    Subscribes to the gateway's MCP session resources, claims new sessions,
    creates per-session agents via the factory, and routes incoming actions.

    Usage:
        runtime = FreesailAgentRuntime(agent_factory)
        # Pass runtime.message_handler to ClientSession at construction:
        async with ClientSession(read, write, message_handler=runtime.message_handler) as session:
            await runtime.start(session)

    Mirrors FreesailAgentRuntime from @freesail/agent-runtime.
    """

    def __init__(self, agent_factory: AgentFactory) -> None:
        self._agent_factory = agent_factory
        self._session: ClientSession | None = None

        self._active_agents: dict[str, FreesailAgent] = {}
        self._known_sessions: set[str] = set()
        # Per-session serial chain — keeps lifecycle events ordered within a session
        self._session_chains: dict[str, asyncio.Task[Any]] = {}
        self._active_subscriptions: set[str] = set()
        self._stop_event = asyncio.Event()

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    async def message_handler(
        self,
        message: Any,  # RequestResponder | ServerNotification | Exception
    ) -> None:
        """
        MCP message handler — pass this to ClientSession(message_handler=...).
        Dispatches ResourceUpdatedNotifications to the appropriate handler.
        """
        if not isinstance(message, types.ServerNotification):
            return

        notification = message.root
        if not isinstance(notification, types.ResourceUpdatedNotification):
            return

        uri = str(notification.params.uri)

        if uri == SESSIONS_URI:
            asyncio.ensure_future(self._handle_sessions_update())
            return

        m = _SESSION_URI_RE.match(uri)
        if m:
            session_id = m.group(1)
            self._enqueue_session_work(
                session_id, self._handle_session_actions(session_id)
            )

    async def start(self, mcp_session: ClientSession) -> None:
        """
        Subscribe to the sessions resource, do an immediate read to pick up
        pre-existing sessions, then run until stop() is called.

        The message_handler must already be registered with ClientSession
        before calling start().
        """
        self._session = mcp_session

        # Subscribe to the global sessions list
        await self._subscribe(SESSIONS_URI)

        # Pick up any sessions that connected before we started
        await self._handle_sessions_update()

        logger.info("Agent runtime started — listening for sessions")

        # Keep running until stop() is called
        await self._stop_event.wait()

    def stop(self) -> None:
        """Signal the runtime to shut down."""
        self._stop_event.set()

    # ------------------------------------------------------------------
    # Sessions list management
    # ------------------------------------------------------------------

    async def _handle_sessions_update(self) -> None:
        assert self._session is not None
        try:
            resource = await self._session.read_resource(AnyUrl(SESSIONS_URI))
            raw = _extract_text(resource.contents)
            active: list[dict] = json.loads(raw) if raw else []
        except Exception as exc:
            logger.warning("Failed to read sessions resource: %s", exc)
            return

        # Each session entry is an object like {"id": "session_...", ...}
        active_set = set(s["id"] for s in active)

        new_sessions = active_set - self._known_sessions
        removed_sessions = self._known_sessions - active_set

        logger.info(
            "Sessions update — active: %s, known: %s, new: %s, removed: %s",
            active_set, self._known_sessions, new_sessions, removed_sessions,
        )

        for sid in new_sessions:
            asyncio.ensure_future(self._safe_connect_session(sid))
        for sid in removed_sessions:
            self._enqueue_session_work(sid, self._disconnect_session(sid))

    async def _safe_connect_session(self, session_id: str) -> None:
        """Wrapper so _connect_session exceptions are logged and don't vanish silently."""
        try:
            await self._connect_session(session_id)
        except Exception as exc:
            logger.error("[%s] _connect_session unhandled error: %s", session_id, exc)
            self._known_sessions.discard(session_id)

    async def _connect_session(self, session_id: str) -> None:
        assert self._session is not None

        # Guard against double-connect from rapid notifications — claim the slot
        # immediately (before any await) so a second notification sees it already known.
        if session_id in self._known_sessions:
            return
        self._known_sessions.add(session_id)

        # Claim the session — if another agent instance gets it first we skip
        try:
            claim_result = await self._session.call_tool(
                "claim_session", arguments={"sessionId": session_id}
            )
            raw = _extract_text(claim_result.content)
            claimed = json.loads(raw) if raw else {}
            if not claimed.get("success", False):
                logger.debug("[%s] Session claim failed — skipping", session_id)
                self._known_sessions.discard(session_id)
                return
        except Exception as exc:
            logger.warning("[%s] claim_session error: %s", session_id, exc)
            return

        # Subscribe to per-session action resource
        per_session_uri = f"mcp://freesail.dev/sessions/{session_id}"
        await self._subscribe(per_session_uri)

        # Create agent instance
        agent = self._agent_factory(session_id)
        self._active_agents[session_id] = agent

        logger.info("[%s] Session connected", session_id)
        try:
            await agent.on_session_connected(session_id)
        except Exception as exc:
            logger.error("[%s] on_session_connected error: %s", session_id, exc)

    async def _disconnect_session(self, session_id: str) -> None:
        assert self._session is not None
        self._known_sessions.discard(session_id)
        agent = self._active_agents.pop(session_id, None)

        if agent is not None:
            try:
                await agent.on_session_disconnected(session_id)
            except Exception as exc:
                logger.error("[%s] on_session_disconnected error: %s", session_id, exc)

        try:
            await self._session.call_tool(
                "release_session", arguments={"sessionId": session_id}
            )
        except Exception as exc:
            logger.warning("[%s] release_session error: %s", session_id, exc)

        self._session_chains.pop(session_id, None)
        logger.info("[%s] Session disconnected", session_id)

    # ------------------------------------------------------------------
    # Action dispatch
    # ------------------------------------------------------------------

    async def _handle_session_actions(self, session_id: str) -> None:
        assert self._session is not None
        agent = self._active_agents.get(session_id)
        if agent is None:
            return

        per_session_uri = f"mcp://freesail.dev/sessions/{session_id}"
        try:
            resource = await self._session.read_resource(AnyUrl(per_session_uri))
            raw = _extract_text(resource.contents)
            events: list[dict[str, Any]] = json.loads(raw) if raw else []
        except Exception as exc:
            logger.warning("[%s] Failed to read session resource: %s", session_id, exc)
            return

        # Each event is {"action": {...}, "dataModel": {...}} or {"error": {...}}
        # (no "type" field — mirrors the TypeScript runtime's msg.action / msg.error checks)
        for event in events:
            if "error" in event:
                # client error — nothing to dispatch in this implementation
                continue

            raw_action = event.get("action")
            if not raw_action or not isinstance(raw_action.get("name"), str):
                continue
            if raw_action["name"].startswith("__session_"):
                continue

            # Attach clientDataModel if present (msg.dataModel?.dataModel in TS)
            client_data_model = (event.get("dataModel") or {}).get("dataModel")
            if client_data_model is not None:
                raw_action = {**raw_action, "clientDataModel": client_data_model}

            try:
                await agent.on_action(raw_action)
            except Exception as exc:
                logger.error("[%s] on_action error: %s", session_id, exc)

    # ------------------------------------------------------------------
    # Per-session serial queue
    # ------------------------------------------------------------------

    def _enqueue_session_work(self, session_id: str, coro: Awaitable[Any]) -> asyncio.Task[Any]:
        """Chain work onto the per-session task so events run serially."""
        prev = self._session_chains.get(session_id)

        async def _chained() -> None:
            if prev is not None:
                try:
                    await prev
                except Exception:
                    pass
            await coro  # type: ignore[misc]

        task = asyncio.create_task(_chained())
        self._session_chains[session_id] = task
        return task

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    async def _subscribe(self, uri: str) -> None:
        assert self._session is not None
        if uri in self._active_subscriptions:
            return
        try:
            await self._session.subscribe_resource(AnyUrl(uri))
            self._active_subscriptions.add(uri)
            logger.debug("Subscribed to %s", uri)
        except Exception as exc:
            logger.warning("Failed to subscribe to %s: %s", uri, exc)


def _extract_text(contents: Any) -> str:
    """Extract text from MCP resource contents (list of content blobs)."""
    if not contents:
        return ""
    parts = []
    for item in contents:
        if hasattr(item, "text"):
            parts.append(item.text)
        elif isinstance(item, str):
            parts.append(item)
    return "".join(parts)
