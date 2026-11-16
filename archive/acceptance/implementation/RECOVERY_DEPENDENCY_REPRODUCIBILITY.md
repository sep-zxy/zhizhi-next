# 恢复环境的依赖复现

2026-10-20 的恢复验证发现，按 `pyproject.toml` 的允许范围重新解析依赖不能复现仓库 `uv.lock`：例如 Typer 由锁定的 0.24.1 变为 0.27.3，Starlette 由 1.3.1 变为 1.9.0，httpx2 由 2.12.0 变为 2.15.1。Click 仍是 8.4.2；因此不能仅凭 Click 版本认定 CLI 环境一致。

恢复已有版本时，先保留当前环境的 `uv pip freeze`，再使用仓库冻结锁文件，不更新锁文件，不重新按版本范围解析。专用环境的标准入口是 `uv sync --frozen --group dev --extra captions --extra local-asr --extra wechat`。若恢复流程必须使用预先创建的共享环境，可在 `projects/growth` 下导出并同步：

```powershell
$ErrorActionPreference = 'Stop'
uv export --frozen --no-emit-project --no-hashes --format requirements-txt --group dev --extra captions --extra local-asr --extra wechat --output-file locked-requirements.txt
uv pip sync --python $env:RECOVERY_PYTHON locked-requirements.txt
uv pip check --python $env:RECOVERY_PYTHON
```

`RECOVERY_PYTHON` 应指向事先授权的隔离环境。同步会移除锁文件之外的包，故先协调所有使用者暂停测试；不要在并行测试期间改动共享依赖。安装可选依赖不代表下载模型或获准调用真实模型。

复测须分别保留 CLI 帮助契约测试与真实本地路由基准结果。`serve_read_routes` 的目标 p95 仍为 50ms，硬失败门槛仍为 500ms；`warn` 不等于达到目标，不能把警告写成全部性能目标通过。相同代码的新环境必须重新验证，不沿用丢失环境的旧通过计数。隔离重 CPU 工作后再做最终整套测试，保留时间、包版本和原始 JSON / JUnit 证据。
