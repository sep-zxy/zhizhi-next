# 安全源码补充包

本包只补齐主包排除的16份文件，不重传主包。解压到主包同一根目录，projects/growth 相对路径保持一致。16个目标在主包均不存在，不会覆盖主包已有源码。

全部命中经上下文核查为非秘密测试数据或界面文案；按字节原样复制。没有修改原件、改写测试、跳过测试或移除断言。没有发现需要脱敏的实际凭据，也没有待人工判断文件。原始扫描命中仍可能被通用扫描器报告，豁免只适用于本次记录的精确文件哈希和上下文。

## 逐文件判断

| 文件 | 原因 |
| --- | --- |
| projects/growth/tests/unit/test_allowlist.py | L101: deterministic provider-key-shaped sequential-character fixture in test_hard_block_cannot_be_suppressed_and_privacy_gates_enforce_redaction; temporary repository and redaction policy assertions. |
| projects/growth/tests/unit/test_claim_extract.py | L1732, L2125: deterministic sequential-character fixture inserted into temporary patch.diff; explicit redacted_remote extraction and payload-redaction tests. |
| projects/growth/tests/unit/test_distractor_gate.py | L209: deterministic sequential-character fixture in test_distractor_gate_report_redacts_provider_error_like_identifiers; tests report redaction. |
| projects/growth/tests/unit/test_git_capture.py | L620,1223,1259,4151,4378,4397: deterministic sequential-character fixture in temporary Git files, patch data, metadata and assertions. Literal reuse validates redacted artifacts; not a configured provider credential. |
| projects/growth/tests/unit/test_learning_inputs.py | L236,251: deterministic sequential-character fixture in CompareFileInput and a non-disclosure assertion; test_snapshot_estimate_normalizes_both_secret_bearing_versions. |
| projects/growth/tests/unit/test_provider.py | L1520: assertion expects the application's bracketed REDACTED marker, not a credential. L1515 constructs synthetic input by repeating one character; L1523 uses httpx.MockTransport. |
| projects/growth/tests/unit/test_redact.py | L51: GitHub-token-shaped cyclic alphabet fixture, verified as a known cyclic sequence after removing its provider prefix; L67,80,156,288,330 use deterministic sequential fixtures; L277 is an example-domain URL input for userinfo-redaction testing. |
| projects/growth/tests/unit/test_routes_providers.py | Provider-key-format matches are descriptive hyphenated fixture labels under test_* functions, persisted only into tmp_path test config and exercised with client/probe spies. L1656,1821 are example-domain userinfo test inputs. L1602 intentionally contains a NUL escape and is asserted to return 422 with no config/env writes. |
| projects/growth/tests/unit/test_security_hardening.py | L325: example-domain URL with conventional synthetic userinfo, deliberately passed to _pin_url_to_ip under pytest.raises(SafetyError, match='userinfo'). |
| projects/growth/tests/unit/test_stage1_task1.py | L553: deterministic sequential fixture for unknown/sensitive repo-key diagnostics; L762: descriptive fake provider literal assigned to api_key_env to verify sensitive-key detection. |
| projects/growth/tests/unit/test_structured_output.py | L272: mock _RetryProvider returns JSON with a private_password field whose value is an illustrative /Users/alice/ path; assertions L289-291 ensure raw provider output and that example path are absent from error messages. No actual password is present in the flagged value. |
| projects/growth/tests/unit/serve/test_routes_growth_model.py | L50: deterministic sequential fixture written into a temporary Git source file; L163,367 assert it is absent from reviewed model/feedback payloads. |
| projects/growth/viewer/src/i18n/messages/en.json | L2072: Settings_page.provider_api_key is a human-language UI label, surrounded by alias/env-label/location translations, consumed by ProviderCard.tsx. Not a provider settings value or actual API key. |
| projects/growth/viewer/src/i18n/messages/zh-CN.json | L2072: Chinese counterpart of the same Settings_page.provider_api_key UI label. Both locale files parse and have identical sets of 2337 translation keys. |
| projects/growth/viewer/tests/unit/client.test.ts | L298: descriptive fake backup-token label returned by vi.fn(fetch); L308-309 assert it is not disclosed by the ValidationError. Mocked response only. |
| projects/growth/viewer/tests/unit/learn-mode-dialog.test.ts | L665: example.test userinfo URL belongs to an invalid-input parameterized case alongside file/javascript URLs; exercises UI rejection, not authentication. |

## 验证与边界

- 全部16份原件与交付副本SHA256一致；归档后再次逐文件验证。
- 12份Python文件仅做AST语法解析，没有执行测试。两份TS测试没有运行构建或测试。
- 两份语言JSON均可解析，2337个键完全对应；ProviderCard.tsx 中存在对应文案消费位置。
- growth HEAD与主包一致，工作区干净。
- 保留主包携带的AGENTS规则，所有构建只能使用GitHub托管Actions runner。
- 本地未安装、构建、部署、修改项目或创建worktree。
- SHA256SUMS_SUPPLEMENT覆盖本包除其自身之外的全部文件。
- SUPPLEMENT_MANIFEST.json中的safe_modification_list为空，sanitized_files=0。
