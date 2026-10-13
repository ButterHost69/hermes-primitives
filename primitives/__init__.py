"""Primitives: code units (source + deps + context) and their manager."""

from .manager import PrimitiveManager
from .primitive import Primitive

__all__ = ["Primitive", "PrimitiveManager"]
