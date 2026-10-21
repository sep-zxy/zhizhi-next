"""Disposable, evidence-bound relationship views over formal knowledge."""

from .routes import create_relation_routes
from .service import RelationService

__all__ = ["RelationService", "create_relation_routes"]
