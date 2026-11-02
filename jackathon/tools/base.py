"""A Tool is a Python function the model can ask us to run.

Each tool declares its arguments as a Pydantic model. That one model gives us:
  1. the JSON schema we show the model (so it knows what to send), and
  2. validation of what the model actually sends (so bad input never reaches our code).
"""

from __future__ import annotations

import copy
from collections.abc import Awaitable, Callable
from typing import Any

from pydantic import BaseModel, ValidationError

from ..llm import ToolSpec


class ToolError(Exception):
    """Raise inside a tool to send a clean error message back to the model."""


class Tool:
    def __init__(
        self,
        name: str,
        description: str,
        args_model: type[BaseModel],
        fn: Callable[[Any], Awaitable[str]],
    ):
        self.name = name
        self.description = description
        self.args_model = args_model
        self.fn = fn

    def spec(self) -> ToolSpec:
        return ToolSpec(self.name, self.description, json_schema(self.args_model))

    async def run(self, arguments: dict[str, Any]) -> str:
        """Validate the model's arguments, then call the tool function."""
        try:
            args = self.args_model.model_validate(arguments)
        except ValidationError as e:
            raise ToolError(f"Invalid arguments for {self.name}: {e}") from e
        return await self.fn(args)


def json_schema(model: type[BaseModel]) -> dict[str, Any]:
    """Pydantic's JSON schema with `$ref`s expanded in place.

    Pydantic puts nested models under `$defs` and points to them with `$ref`.
    Some providers don't follow `$ref`, so we inline everything.
    """
    schema = model.model_json_schema()
    defs = schema.pop("$defs", {})

    def expand(node: Any) -> Any:
        if isinstance(node, dict):
            ref = node.get("$ref")
            if isinstance(ref, str) and ref.startswith("#/$defs/"):
                target = copy.deepcopy(defs[ref.removeprefix("#/$defs/")])
                extras = {k: v for k, v in node.items() if k != "$ref"}  # e.g. a description
                return expand({**target, **extras})
            return {k: expand(v) for k, v in node.items()}
        if isinstance(node, list):
            return [expand(v) for v in node]
        return node

    return expand(schema)
