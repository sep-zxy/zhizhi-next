"""Explicit desktop wiring; configuration is never accepted from retrieved text."""

from __future__ import annotations

import asyncio
from contextlib import suppress
from pathlib import Path

from .config_io import load_config_object, string_list
from .learning.backend import DurableOutbox, RemoteKnowledgeBackend
from .learning.desktop import configure_desktop_backend
from .learning.model_bridge import ModelBridgeConfiguration, configure_model_bridge
from .model import build_runner_from_config


def configure_desktop(
    path: Path, *, state_directory: Path, loop: asyncio.AbstractEventLoop
) -> RemoteKnowledgeBackend:
    """Wire the old app into the common service without copying an auth secret.

    The service token is read on each request from a protected deployment file,
    so rotation does not put credentials in the outbox or require migration.
    This function never creates credentials or initiates login.
    """
    config = load_config_object(path)
    allowed = {
        "service_url",
        "identity_file",
        "device_id",
        "model",
        "providers",
        "input_classification",
    }
    if set(config) - allowed:
        raise ValueError("Unknown desktop configuration field")
    device_id = config.get("device_id")
    if not isinstance(device_id, str) or not device_id.strip():
        raise ValueError("A configured persistent device ID is required")
    identity_path = Path(config["identity_file"])
    if not identity_path.is_absolute():
        identity_path = path.parent / identity_path

    def token_provider() -> str:
        identity = load_config_object(identity_path, secret=True)
        token = identity.get("token")
        if not isinstance(token, str) or len(token) < 24:
            raise ValueError("Protected desktop identity has no valid bearer token")
        return token

    # Fail at startup rather than report successful unified configuration.
    token_provider()
    providers = string_list(config.get("providers", []), "Providers")
    classification = config.get("input_classification", "personal")
    bridge = ModelBridgeConfiguration(
        enabled=True, allowed_provider_ids=tuple(providers), input_classification=classification
    )
    runner = build_runner_from_config(config.get("model"))
    backend = RemoteKnowledgeBackend(
        config["service_url"],
        token_provider=token_provider,
        outbox=DurableOutbox(state_directory / "knowledge-outbox"),
    )
    configure_model_bridge(runner, bridge, loop=loop)
    configure_desktop_backend(backend, device_id=device_id)
    return backend


def reset_desktop() -> None:
    """Release process-scoped configuration when the owning app shuts down."""
    configure_desktop_backend(None, device_id="unconfigured")
    configure_model_bridge(None, ModelBridgeConfiguration())


async def replay_desktop_outbox(
    backend: RemoteKnowledgeBackend, stop: asyncio.Event, *, interval: float = 15
) -> None:
    """Resume the same immutable requests on startup and after network recovery."""
    if interval <= 0:
        raise ValueError("Replay interval must be positive")
    while not stop.is_set():
        # The backend persists safe terminal/conflict states. Unexpected transport
        # failures retain the original outbox request for the next attempt.
        with suppress(Exception):
            await backend.flush()
        with suppress(TimeoutError):
            await asyncio.wait_for(stop.wait(), timeout=interval)
