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
    identity_options = parser.add_mutually_exclusive_group()
    identity_options.add_argument(
        "--identities-file",
        type=Path,
        help="Protected existing identity mapping; omitted means unauthenticated shell only",
    )
    identity_options.add_argument(
        "--local-test-identity", type=Path,
        help="Explicit approved Windows-only DPAPI test identity, never production",
    )
    parser.add_argument(
        "--private-web-root", type=Path,
        help="Absolute compiled private Wiki assets directory, mounted at /wiki/",
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
    parser.add_argument("--owner", help="Required with an existing identities file")
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
    if args.identities_file is None:
        if (
            args.owner is not None or args.run_worker or args.auto_commit
            or args.model_config is not None or args.channel_config is not None
            or args.asr_model_directory is not None or args.provider
            or args.allow_classification or args.input_classification != "personal"
        ):
            parser.error("No-identity and local-test modes prohibit production backend options")
    elif not args.owner:
        parser.error("--owner is required with --identities-file")
    from .portable import portable_root, require_portable_path, validate_portable_runtime

    validate_portable_runtime(args.state_directory)
    if portable_root() is not None:
        if args.host != "127.0.0.1":
            parser.error("Portable knowledge service must bind to 127.0.0.1")
        for configured_path in (
            args.identities_file, args.local_test_identity,
            args.model_config, args.channel_config, args.asr_model_directory,
            args.private_web_root,
        ):
            if configured_path is not None:
                require_portable_path(configured_path)
    from .app import create_application

    if args.local_test_identity is not None:
        from .auth import TokenAuthenticator
        from .browser_sessions import LocalTestBrowserSessions, local_test_actor
        from .local_test_identity import load_local_test_identity

        try:
            test_identity = load_local_test_identity(
                args.local_test_identity, state_directory=args.state_directory,
                host=args.host, port=args.port,
            )
        except (ValueError, OSError):
            parser.error("Local test identity or isolated configuration is unavailable")
        test_tokens = {test_identity.token: local_test_actor()}
        app = create_application(
            args.state_directory, tokens=test_tokens, private_web_root=args.private_web_root,
            local_test_browser_sessions=LocalTestBrowserSessions(
                test_identity.origin, TokenAuthenticator(test_tokens)
            ),
        )
        import uvicorn

        uvicorn.run(app, host="127.0.0.1", port=test_identity.port, access_log=False)
        return

    if args.identities_file is None:
        app = create_application(
            args.state_directory, tokens={}, private_web_root=args.private_web_root
        )
        import uvicorn

        uvicorn.run(app, host=args.host, port=args.port, access_log=False)
        return

    from .auth import Actor
    from .media.transcripts import LocalWhisperASR, MediaTranscriptProcessor
    from .model import build_runner_from_config
    from .workflow import WorkflowSettings

    owner = args.owner
    if not isinstance(owner, str) or not owner.strip():
        parser.error("--owner requires an explicit existing identity owner")
    identities = load_config_object(args.identities_file, secret=True)
    records = identities.get("identities")
    if not isinstance(records, list) or not records:
        raise ValueError("No authenticated identities configured")
    tokens: dict[str, Actor] = {}
    for raw in cast("list[Any]", records):
        if not isinstance(raw, dict):
            raise ValueError("Identity must be an object")
        item = cast("dict[str, Any]", raw)
        if item.get("owner_id") != owner:
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
            owner,
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
        owner_id=owner,
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
                owner=owner,
                identities=list(tokens.values()),
                resolve_secret=reader.resolve,
                allowed_classifications=settings.allowed_classifications,
            )

        channels = DeferredChannelRuntime(domain, owner, channel_factory)
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
        private_web_root=args.private_web_root,
        **assembly,
    )
    import uvicorn

    uvicorn.run(app, host=args.host, port=args.port, access_log=False)


if __name__ == "__main__":
    main()
