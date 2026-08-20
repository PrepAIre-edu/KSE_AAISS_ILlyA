from .base import SourceAdapter, SourceFile, get_adapter, register, registered
from . import mit, kse, syllabus_flat  # noqa: F401  (registration side effect)

__all__ = ["SourceAdapter", "SourceFile", "get_adapter", "register", "registered"]
