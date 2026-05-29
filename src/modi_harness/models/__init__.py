"""Model Adapter: ContextPack -> LangChain messages -> ModelResult."""

from __future__ import annotations

from .adapter import ModelAdapter
from .errors import ModelConfigError
from .factory import create_chat_model

__all__ = ["ModelAdapter", "ModelConfigError", "create_chat_model"]
