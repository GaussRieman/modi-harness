"""Model Adapter implementation.

Sole owner of ``ContextPack -> LangChain messages`` conversion. Wraps
untrusted blocks per docs/architecture/15-untrusted-content.md, normalizes
the response into a Modi ``ModelResult``, and surfaces malformed tool calls
without auto-retrying them (Runtime Adapter owns repair).
"""

from __future__ import annotations

import json
from typing import Any

from langchain_core.language_models import BaseChatModel
from langchain_core.messages import (
    AIMessage,
    BaseMessage,
    HumanMessage,
    SystemMessage,
    ToolMessage,
)

from ..types import (
    ContextPack,
    Message,
    ModelResult,
    ModelUsage,
    SafetySignal,
    ToolCallProposal,
)


class ModelAdapter:
    """Normalizes Modi context to a LangChain chat model and back.

    Construct with an explicit ``BaseChatModel`` instance — provider factory
    lives elsewhere (Settings + provider lookup) and is exercised by Runtime
    Adapter. Constructing with no model raises only when ``call`` is invoked.
    """

    def __init__(self, *, chat_model: BaseChatModel | None = None) -> None:
        self._chat_model = chat_model

    # ------------------------------------------------------------------
    # public
    # ------------------------------------------------------------------

    def call(self, pack: ContextPack, options: dict[str, Any] | None = None) -> ModelResult:
        if self._chat_model is None:
            raise RuntimeError("ModelAdapter constructed without a chat model")

        messages = self.to_langchain_messages(pack)
        bound = self._bind_tools(self._chat_model, pack["tool_descriptions"])
        ai_message = bound.invoke(messages)
        return _normalize(ai_message)

    async def acall(self, pack: ContextPack, options: dict[str, Any] | None = None) -> ModelResult:
        """Async version of call(). Uses ainvoke on the bound model."""
        if self._chat_model is None:
            raise RuntimeError("ModelAdapter constructed without a chat model")

        messages = self.to_langchain_messages(pack)
        bound = self._bind_tools(self._chat_model, pack["tool_descriptions"])
        ai_message = await bound.ainvoke(messages)
        return _normalize(ai_message)

    def to_langchain_messages(self, pack: ContextPack) -> list[BaseMessage]:
        # System: untrusted-note + safety + agent + skills.
        system_parts: list[str] = [pack["system_instruction"], pack["agent_instruction"]]
        system_parts.extend(pack["skill_instructions"])
        system_msg = SystemMessage(content="\n\n".join(p for p in system_parts if p))

        # Memory blocks rendered as a single trusted system addendum.
        if pack["memory_blocks"]:
            memory_text = "\n".join(
                f"[memory:{m['scope']}:{m['type']}] {m['body']}" for m in pack["memory_blocks"]
            )
            system_msg = SystemMessage(content=system_msg.content + "\n\n" + memory_text)

        # State summary as an additional system note.
        if pack["state_summary"]:
            system_msg = SystemMessage(
                content=system_msg.content + "\n\n[state] " + pack["state_summary"]
            )

        out: list[BaseMessage] = [system_msg]

        # Untrusted references go in as Human messages with explicit wrappers.
        for ref in pack["references"]:
            wrapped = _wrap_untrusted(ref)
            if wrapped:
                out.append(HumanMessage(content=wrapped))

        # Recent messages.
        for m in pack["recent_messages"]:
            out.append(_message_to_langchain(m))

        if pack["output_requirement"] is not None:
            out.append(
                SystemMessage(
                    content=f"[output_contract]\n{json.dumps(pack['output_requirement'], ensure_ascii=False)}"
                )
            )

        return out

    # ------------------------------------------------------------------
    # internals
    # ------------------------------------------------------------------

    def _bind_tools(
        self,
        chat_model: BaseChatModel,
        tool_descriptions: list[Any],
    ) -> Any:
        if not tool_descriptions:
            return chat_model
        # Convert ToolDescription -> OpenAI-style function schemas.
        funcs = [
            {
                "type": "function",
                "function": {
                    "name": td["name"],
                    "description": td["description"],
                    "parameters": td["input_schema"],
                },
            }
            for td in tool_descriptions
        ]
        bind_tools = getattr(chat_model, "bind_tools", None)
        if callable(bind_tools):
            try:
                return bind_tools(funcs)
            except (TypeError, NotImplementedError):
                # FakeChatModel and other test doubles may not implement bind_tools.
                return chat_model
        return chat_model


# ----------------------------------------------------------------------
# helpers
# ----------------------------------------------------------------------


def _wrap_untrusted(block: dict[str, Any]) -> str | None:
    if block["content"] is None:
        return None
    trust = block.get("trust") or {}
    if trust.get("trust_level") == "trusted":
        return block["content"]
    source_kind = trust.get("source_kind", "unknown")
    source_id = trust.get("source_id", "")
    body = _escape_closing_tag(block["content"])
    return f'<untrusted source_kind="{source_kind}" source_id="{source_id}">\n{body}\n</untrusted>'


def _escape_closing_tag(content: str) -> str:
    # Prevent literal closing tags from terminating the wrapper early.
    return content.replace("</untrusted>", "<\\/untrusted>")


def _message_to_langchain(m: Message) -> BaseMessage:
    role = m["role"]
    if role == "system":
        return SystemMessage(content=m["content"])
    if role == "assistant":
        return AIMessage(content=m["content"])
    if role == "tool":
        return ToolMessage(content=m["content"], tool_call_id=m.get("tool_call_id") or "")
    return HumanMessage(content=m["content"])


def _normalize(ai_message: AIMessage) -> ModelResult:
    tool_calls = _extract_tool_calls(ai_message)
    usage = _extract_usage(ai_message)
    finish_reason = _extract_finish_reason(ai_message)
    safety_signals: list[SafetySignal] = []

    # Structured output passthrough — populated when the chat model returns it.
    draft_output: dict[str, Any] | None = None
    if hasattr(ai_message, "additional_kwargs"):
        draft_output = ai_message.additional_kwargs.get("structured_output")  # type: ignore[union-attr]

    return ModelResult(  # type: ignore[typeddict-item]
        message=Message(  # type: ignore[typeddict-item]
            role="assistant",
            content=ai_message.content if isinstance(ai_message.content, str) else str(ai_message.content),
            tool_call_id=None,
            metadata={},
        ),
        tool_calls=tool_calls,
        draft_output=draft_output,
        usage=usage,
        safety_signals=safety_signals,
        finish_reason=finish_reason,
        raw=ai_message,
    )


def _extract_tool_calls(ai: AIMessage) -> list[ToolCallProposal]:
    calls: list[ToolCallProposal] = []

    # Modern: AIMessage.tool_calls (already parsed by LangChain).
    parsed = getattr(ai, "tool_calls", None) or []
    for c in parsed:
        calls.append(
            ToolCallProposal(  # type: ignore[typeddict-item]
                tool_call_id=c.get("id") or "",
                tool_name=c.get("name") or "",
                arguments=c.get("args") or {},
                malformed=False,
                parse_error=None,
            )
        )

    # Legacy / OpenAI-format function calls in additional_kwargs.
    extra = getattr(ai, "additional_kwargs", {}) or {}
    for c in extra.get("tool_calls", []):
        fn = c.get("function", {})
        raw_args = fn.get("arguments", "")
        try:
            parsed_args = json.loads(raw_args) if isinstance(raw_args, str) else raw_args
            malformed = False
            parse_error: str | None = None
        except json.JSONDecodeError as exc:
            parsed_args = {}
            malformed = True
            parse_error = str(exc)
        calls.append(
            ToolCallProposal(  # type: ignore[typeddict-item]
                tool_call_id=c.get("id") or "",
                tool_name=fn.get("name") or "",
                arguments=parsed_args,
                malformed=malformed,
                parse_error=parse_error,
            )
        )

    return calls


def _extract_usage(ai: AIMessage) -> ModelUsage:
    meta = getattr(ai, "usage_metadata", None) or {}
    extra = getattr(ai, "additional_kwargs", {}) or {}
    legacy = extra.get("usage", {}) if isinstance(extra, dict) else {}
    return ModelUsage(  # type: ignore[typeddict-item]
        prompt_tokens=int(meta.get("input_tokens") or legacy.get("prompt_tokens") or 0),
        completion_tokens=int(meta.get("output_tokens") or legacy.get("completion_tokens") or 0),
        total_tokens=int(meta.get("total_tokens") or legacy.get("total_tokens") or 0),
        cache_read_tokens=int(meta.get("input_token_details", {}).get("cache_read", 0)) if isinstance(meta.get("input_token_details"), dict) else 0,
        cache_write_tokens=0,
        cost_usd=None,
    )


def _extract_finish_reason(ai: AIMessage) -> str:
    extra = getattr(ai, "additional_kwargs", {}) or {}
    fr = extra.get("finish_reason") if isinstance(extra, dict) else None
    return fr or "stop"
