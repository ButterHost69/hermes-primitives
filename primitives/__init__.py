"""Primitives: code units (source + deps) and their manager."""

from .manager import PrimitiveManager
from roles.primitive import Primitive

__all__ = ["Primitive", "PrimitiveManager"]
