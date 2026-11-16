# Codex CLI 适配验证（2026-10-20）

## 云端已通过

- `tests/knowledge_platform/agent` + `deploy/windows-portable/test_codex_patch.py`：158 passed，无 skipped。包括 148 项 agent/模型/生成复核/SQLite-Git fixture 测试及 10 项补丁校验/回滚测试。
- 所有修改的 model Python、新 fixture/tests 与补丁构建/应用脚本：ruff check 通过。
- 新 runner/config/Windows Job：pyright Windows 目标静态检查 0 errors / 0 warnings。此项不是 Windows 上执行。
- `git diff --check` 通过。
- 产品依赖与 uv.lock 没有变化。验证复用了云端已有 Python 3.12.14、pytest 9.1.1、jsonschema 4.26.0、portalocker 3.2.0、markdown-it-py 4.2.0、Starlette 1.6.0、ruff 0.12.12、pyright 1.1.414；不是完整 frozen 产品环境全仓测试。

## 本次覆盖

显式开关、Pi 默认、版本/ChatGPT 登录状态、拒绝 API-key/其他 provider/多路 fallback、禁止继承 API keys/代理/注入环境、stdin 数据传递、精确 requested model、actual unknown、完整 JSONL 终态与 exit 0、未知/工具事件阻断、输入分类/秘密/体积/Schema 门槛、外层结构化 envelope 与内层 Schema、重复 key、深层 JSON、本地引用失败、输出总字节/记录限制、token 事后限制、截止时间、超时和调用方取消、父进程先退出后的 POSIX 后代清理、额度冷却、并发排队、幂等冲突、脱敏账本字段。

补丁测试覆盖：原 immutable 文件和 runtime 改动检测、payload hash 改动检测、新增代码冲突、仅 backend/provenance 白名单、正常应用/完整性检查/精确回滚、部分应用后的回滚、后续非补丁编辑拒绝覆盖、config 与 state 不变。

只读复核提出的进程后代清理、递归扫描异常、对象键中的秘密漏检与首次启动锁缺失问题已修复并新增回归。缺失锁文件时 Windows 修改操作拒绝执行，不生成 state 文件。新增 Windows Job 只用标准库 ctypes，未添加运行依赖。

## 未测，不能冒认为通过

- 云端未使用任何真实凭据，也未发起模型网络请求。
- 目标 Codex CLI 0.163.0-alpha.10.2 的本适配结构化输出正向请求、Windows Job 实机行为和 Windows 适配运行尚待已有运行任务验收。
- 此前独立 CLI smoke 不是本适配测试，更不是整个平台真实模型/渠道/知识入库 E2E。
- 本次不是全仓测试、Windows 安装器构建、生产发布或公开部署。

实际原包复制、补丁应用、所有 immutable/component 清单复验、新旧文件全量差异与回滚还原的结果由云端补丁构建器产出独立 verification.json；没有该报告前不宣称打包验收完成。报告中的 Windows E2E 必须保持 not_run，直到实机给出证据。
