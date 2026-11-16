"""Rebuildable, authorization-scoped full-text knowledge retrieval."""

from .index import SearchIndex, SearchService
from .routes import create_read_routes

__all__ = ["SearchIndex", "SearchService", "create_read_routes"]
