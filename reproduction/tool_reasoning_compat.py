"""Preserve Terra High reasoning on the official backend's function-tool calls."""
from __future__ import annotations

from functools import wraps


def install_tool_reasoning() -> None:
    from skillopt.model import azure_openai

    original_factory = azure_openai._make_client
    if getattr(original_factory, "_preserves_terra_tool_reasoning", False):
        return

    @wraps(original_factory)
    def factory(role):
        client = original_factory(role)
        original_create = client.chat.completions.create

        @wraps(original_create)
        def create(*args, **kwargs):
            if kwargs.get("model") == "gpt-6.1-terra" and kwargs.get("tools"):
                # The official message backend drops effort when tools exist.
                # Keep an explicit caller value so the audit can reject conflicts.
                kwargs.setdefault("reasoning_effort", "high")
            return original_create(*args, **kwargs)

        client.chat.completions.create = create
        return client

    factory._preserves_terra_tool_reasoning = True
    azure_openai._make_client = factory
