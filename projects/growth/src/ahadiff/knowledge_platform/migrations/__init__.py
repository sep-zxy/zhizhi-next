"""Read-only, deterministic v5-to-v6 migration planning; no real asset mutation."""

from .dry_run import build_migration_plan, load_registry

__all__ = ["build_migration_plan", "load_registry"]
