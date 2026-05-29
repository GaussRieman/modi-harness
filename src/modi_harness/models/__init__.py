"""Model Adapter: ContextPack -> LangChain messages -> ModelResult."""

from __future__ import annotations

from .adapter import ModelAdapter
from .errors import ModelConfigError, ModelError, ModelErrorCode, classify_error
from .factory import create_chat_model

__all__ = [
    "ModelAdapter",
    "ModelConfigError",
    "ModelError",
    "ModelErrorCode",
    "classify_error",
    "create_chat_model",
]
