"""Launch the isolated knowledge API using explicit, protected configuration.

No credentials are created, no subscription login is performed, and no public
publishing is enabled by this command. Build/run permissions remain governed by
the checkout instructions and the user's current authorization.
"""

from __future__ import annotations

import argparse
from pathlib import Path
from typing import Any, cast

from .config_io import load_config_object, string_list


def main() -> None:
    parser = argparse.ArgumentParser(description="知枝统一知识服务（独立隔离运行）")
    parser.add_argument("--state-directory", required=True)
    parser.add_argument(
        "--identities-file",
        type=Path,
        required=True,
        help="Protected administrator-provided token/identity mapping",
    )
    parser.add_argument("--model-config", type=Path)
    parser.add_argument(
        "--asr-model-directory",
        type=Path,
        help="Existing local faster-whisper model; never downloaded at runtime",
    )
    parser.add_argument(
        "--channel-config",
        type=Path,
        help="Explicit official-channel policy and OpenBao references",
    )
    parser.add_argument("--owner", required=True)
    parser.add_argument("--provider", action="append", default=[])
    parser.add_argument(
        "--allow-classification",
        action="append",
        default=None,
        choices=["public", "personal", "employer", "third_party_private"],
    )
    parser.add_argument(
        "--input-classification",
        default="personal",
        choices=["public", "personal", "employer", "third_party_private"],
    )
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8766)
    parser.add_argument("--run-worker", action="store_true")
    parser.add_argument("--auto-commit", action="store_true")
    args = parser.parse_args()
    if args.auto_commit and not args.run_worker:
        parser.error("--auto-commit requires --run-worker")
    if not 1 <= args.port <= 65535:
        parser.error("port must be between 1 and 65535")
    from .portable import portable_root, require_portable_path, validate_portable_runtime

    validate_portable_runtime(args.state_directory)
    if portable_root() is not None:
        if args.host != "127.0.0.1":
            parser.error("Portable knowledge service must bind to 127.0.0.1")
        for configured_path in (
            args.identities_file, args.model_config, args.channel_config, args.asr_model_directory,
        ):
            if configured_path is not None:
                require_portable_path(configured_path)
    from .app import create_application
    from .auth import Actor
    from .media.transcripts import LocalWhisperASR, MediaTranscriptProcessor
    from .model import build_runner_from_config
    from .workflow import WorkflowSettings

    identities = load_config_object(args.identities_file, secret=True)
    records = identities.get("identities")
    if not isinstance(records, list) or not records:
        raise ValueError("No authenticated identities configured")
    tokens: dict[str, Actor] = {}
    for raw in cast("list[Any]", records):
        if not isinstance(raw, dict):
            raise ValueError("Identity must be an object")
        item = cast("dict[str, Any]", raw)
        if item.get("owner_id") != args.owner:
            raise ValueError("Identity owner must match the configured single-user owner")
        token = item.get("token")
        if not isinstance(token, str) or token in tokens:
            raise ValueError("Invalid or duplicate bearer credential")
        scopes = string_list(item.get("scopes", []), "Identity scopes")
        classifications = string_list(
            item.get("allowed_classifications", ["public", "personal"]),
            "Identity allowed_classifications",
        )
        tokens[token] = Actor(
            args.owner,
            item["actor_id"],
            item["kind"],
            frozenset(scopes),
            frozenset(classifications),
        )
    model_config = load_config_object(args.model_config) if args.model_config else None
    runner = build_runner_from_config(model_config)
    if args.asr_model_directory and not args.asr_model_directory.is_absolute():
        parser.error("--asr-model-directory requires an absolute local directory")
    media_processor = MediaTranscriptProcessor(
        asr=LocalWhisperASR(args.asr_model_directory) if args.asr_model_directory else None
    )
    settings = WorkflowSettings(
        owner_id=args.owner,
        allowed_provider_ids=tuple(args.provider),
        input_classification=args.input_classification,
        allowed_classifications=frozenset(args.allow_classification or ["public", "personal"]),
        auto_commit_enabled=args.auto_commit,
    )
    assembly: dict[str, Any] = {}
    if args.channel_config:
        from .channel_config import build_channels
        from .channel_runtime import DeferredChannelRuntime
        from .domain import DomainService
        from .intake import IntakeService
        from .secrets import OpenBaoReader, SecretReference

        config = load_config_object(args.channel_config)
        secret_config = config.get("secrets", {})
        identity_path = Path(secret_config["identity_file"])
        if not identity_path.is_absolute():
            identity_path = args.channel_config.parent / identity_path
        reader = OpenBaoReader(
            secret_config["address"],
            identity_path,
            {
                name: SecretReference(**reference)
                for name, reference in secret_config.get("references", {}).items()
            },
        )
        root = Path(args.state_directory).expanduser().resolve()
        domain = DomainService(
            root / "platform.sqlite3", root / "vault", object_path=root / "objects"
        )
        intake = IntakeService(
            domain,
            runner,
            allowed_provider_ids=tuple(args.provider),
            input_classification=settings.input_classification,
            media_processor=media_processor,
        )

        def channel_factory() -> tuple[Any, Any]:
            return build_channels(
                config,
                domain=domain,
                intake=intake,
                owner=args.owner,
                identities=list(tokens.values()),
                resolve_secret=reader.resolve,
                allowed_classifications=settings.allowed_classifications,
            )

        channels = DeferredChannelRuntime(domain, args.owner, channel_factory)
        wechat_enabled = (
            isinstance(config.get("wechat_kf"), dict) and config["wechat_kf"].get("enabled") is True
        )
        assembly.update(
            domain=domain,
            intake_service=intake,
            channel_supervisor=channels,
            wechat_adapter=channels if wechat_enabled else None,
        )
    app = create_application(
        args.state_directory,
        tokens=tokens,
        model_runner=runner,
        workflow_settings=settings,
        start_worker=args.run_worker,
        media_processor=media_processor,
        **assembly,
    )
    import uvicorn

    uvicorn.run(app, host=args.host, port=args.port, access_log=False)


if __name__ == "__main__":
    main()
