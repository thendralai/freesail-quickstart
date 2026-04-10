"""
MCP → LangChain tool adapter.

Mirrors agent/src/langchain-adapter.ts: wraps each MCP tool as a LangChain
StructuredTool so the session agent can bind them to the model.
"""

from __future__ import annotations

import logging
from typing import Any, Optional

from langchain_core.tools import StructuredTool
from mcp import ClientSession
from pydantic import BaseModel, ConfigDict, Field, create_model

logger = logging.getLogger("freesail-agent.adapter")


# ---------------------------------------------------------------------------
# JSON Schema → Pydantic model
# ---------------------------------------------------------------------------

def _schema_type_to_python(prop: dict[str, Any]) -> Any:
    """Convert a single JSON Schema property definition to a Python type."""
    t = prop.get("type", "string")
    if t == "string":
        if "enum" in prop:
            # Use Literal for enums
            from typing import Literal
            return Literal[tuple(prop["enum"])]  # type: ignore[return-value]
        return str
    if t in ("number", "integer"):
        return float if t == "number" else int
    if t == "boolean":
        return bool
    if t == "array":
        item_schema = prop.get("items", {})
        item_type = _schema_type_to_python(item_schema)
        return list[item_type]  # type: ignore[valid-type]
    if t == "object":
        if prop.get("properties"):
            return _json_schema_to_pydantic(prop, "NestedModel")
        return dict  # no properties defined — accept any dict (mirrors z.record(z.unknown()))
    return Any


def _json_schema_to_pydantic(schema: dict[str, Any], model_name: str) -> type[BaseModel]:
    """
    Recursively convert a JSON Schema object to a Pydantic BaseModel subclass.

    Mirrors jsonSchemaToZod() from @freesail/agent-runtime.
    """
    properties: dict[str, Any] = schema.get("properties", {})
    required: list[str] = schema.get("required", [])
    fields: dict[str, Any] = {}

    for prop_name, prop_schema in properties.items():
        py_type = _schema_type_to_python(prop_schema)
        description = prop_schema.get("description", "")
        if prop_name in required:
            fields[prop_name] = (py_type, Field(description=description))
        else:
            fields[prop_name] = (Optional[py_type], Field(None, description=description))

    # extra='allow' mirrors Zod's .passthrough() — unknown fields are preserved,
    # not stripped. Critical for component props that vary by component type.
    config = ConfigDict(extra="allow")
    if not fields:
        return create_model(model_name, __config__=config)
    return create_model(model_name, __config__=config, **fields)


# ---------------------------------------------------------------------------
# MCPAdapter
# ---------------------------------------------------------------------------

class MCPAdapter:
    """Build LangChain StructuredTools from MCP tool definitions."""

    @staticmethod
    async def get_tools(mcp_session: ClientSession) -> list[StructuredTool]:
        """
        Fetch all tools from the MCP server and wrap them as LangChain
        StructuredTools that proxy calls through the MCP session.
        """
        result = await mcp_session.list_tools()
        tools: list[StructuredTool] = []

        for mcp_tool in result.tools:
            # Capture loop variable
            tool_name = mcp_tool.name
            tool_description = mcp_tool.description or f"MCP tool: {tool_name}"
            input_schema = dict(mcp_tool.inputSchema) if mcp_tool.inputSchema else {}
            args_schema = _json_schema_to_pydantic(input_schema, f"{tool_name}_args")

            async def _invoke(**kwargs: Any) -> str:
                # kwargs injected from the closure below via a default-arg trick
                _name: str = kwargs.pop("__tool_name__")
                _session: ClientSession = kwargs.pop("__mcp_session__")

                # Block writes to client-managed surfaces
                surface_id = kwargs.get("surfaceId", "")
                if isinstance(surface_id, str) and surface_id.startswith("__"):
                    return (
                        f'Error: "{surface_id}" is a client-managed surface. '
                        f"Agents may not call {_name} on it. "
                        "Use a surface you created with create_surface instead."
                    )

                if _name == "update_components":
                    logger.debug(
                        "Calling update_components for surface %s with %d components",
                        surface_id,
                        len(kwargs.get("components") or []),
                    )
                if _name == "update_data_model":
                    logger.debug("Calling update_data_model for surface %s: %s", surface_id, kwargs)

                call_result = await _session.call_tool(_name, arguments=kwargs)
                parts = call_result.content or []
                return "\n".join(
                    (p.text if hasattr(p, "text") and p.type == "text" else str(p))
                    for p in parts
                )

            # Use a factory to close over the right values for each tool
            def _make_coroutine(tname: str, sess: ClientSession):
                async def coroutine(**kwargs: Any) -> str:
                    kwargs["__tool_name__"] = tname
                    kwargs["__mcp_session__"] = sess
                    return await _invoke(**kwargs)
                return coroutine

            structured_tool = StructuredTool.from_function(
                coroutine=_make_coroutine(tool_name, mcp_session),
                name=tool_name,
                description=tool_description,
                args_schema=args_schema,
            )
            tools.append(structured_tool)

        return tools
