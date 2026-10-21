"""Exact public notification texts, never permission to accept an error item.

Source/provenance: deploy/windows-portable/codex-notification-review.json.
Only fixed public expansions for the already configured model are enumerated.
Matching never interpolates or reveals an observed account, model or path.
"""

OFFICIAL_NOTICE_COMMIT = '5425d3e625daa58eea3ad1de27c087eb3b2ffbd3'

EXACT_NOTIFICATION_TEMPLATES = {
    (
        "Model metadata for `gpt-6.1-sol` not found. Defaulting to fallback metadata; this "
        "can degrade performance and cause issues."
    ): "model_metadata_missing_fixed_model",
    (
        "Configured service tier `priority` is not advertised as supported for model "
        "`gpt-6.1-sol` and will be omitted from requests."
    ): "service_tier_priority_unsupported_fixed_model",
    (
        "Code Mode is enabled in configuration, but model `gpt-6.1-sol` does not advertise "
        "Code Mode support. This may degrade model performance. Disable "
        "`features.code_mode` and `features.code_mode_only`, or select a model whose "
        "metadata enables Code Mode."
    ): "code_mode_unsupported_fixed_model",
    (
        "Stop hook requested continuation without a prompt; ignoring the block."
    ): "stop_hook_empty_continuation",
    (
        "Heads up: Long threads and multiple compactions can cause the model to be less "
        "accurate. Start a new thread when possible to keep threads small and targeted."
    ): "compaction_accuracy_notice",
    (
        "`[features.guardianv2].thread_context` is deprecated and ignored. (Thread-owned "
        "Guardian context is always enabled. Remove `thread_context` from "
        "[features.guardianv2] in config.toml, including profile overrides.)"
    ): "guardian_thread_context_deprecation",
    (
        "`[features].transcript_v2` is deprecated and ignored. (Use "
        "`[tui].fullscreen_transcript` in config.toml instead.)"
    ): "transcript_v2_deprecation",
}
