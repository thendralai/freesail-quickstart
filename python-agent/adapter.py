"""
Tool adapter.

Mirrors agent/src/langchain-adapter.ts: wraps ToolDefinition objects as LangChain
StructuredTools bound to a specific FreesailSessionClient.
"""

from __future__ import annotations

import logging
from typing import Any, Optional

from langchain_core.tools import StructuredTool
from pydantic import BaseModel, ConfigDict, Field, create_model

from runtime import FreesailSessionClient, ToolDefinition

logger = logging.getLogger("freesail-agent.adapter")


# ---------------------------------------------------------------------------
# JSON Schema → Pydantic model
# ---------------------------------------------------------------------------

def _schema_type_to_python(prop: dict[str, Any]) -> Any:
    """Convert a single JSON Schema property definition to a Python type."""
    t = prop.get("type", "string")
    if t == "string":
        if "enum" in prop:
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
        return dict  # no properties defined — accept any dict
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
# LangChainAdapter
# ---------------------------------------------------------------------------

class LangChainAdapter:
    """
    Bind ToolDefinition objects to a specific FreesailSessionClient.
    All tool invocations are routed through the session's call_tool method.

    Mirrors LangChainAdapter from agent/src/langchain-adapter.ts.
    """

    @staticmethod
    def bind_tools(tool_defs: list[ToolDefinition], session: FreesailSessionClient) -> list[StructuredTool]:
        tools: list[StructuredTool] = []

        for tool_def in tool_defs:
            tool_name = tool_def.name
            tool_description = tool_def.description
            args_schema = _json_schema_to_pydantic(tool_def.input_schema, f"{tool_name}_args")

            def _make_coroutine(tname: str, sess: FreesailSessionClient):
                async def coroutine(**kwargs: Any) -> str:
                    surface_id = kwargs.get("surfaceId", "")
                    if tname == "update_components":
                        logger.debug(
                            "Calling update_components for surface %s with %d components",
                            surface_id, len(kwargs.get("components") or []),
                        )
                    if tname == "update_data_model":
                        logger.debug("Calling update_data_model for surface %s: %s", surface_id, kwargs)
                    return str(await sess.call_tool(tname, kwargs))
                return coroutine

            structured_tool = StructuredTool.from_function(
                coroutine=_make_coroutine(tool_name, session),
                name=tool_name,
                description=tool_description,
                args_schema=args_schema,
            )
            tools.append(structured_tool)

        return tools
