# Codex alpha.11.2 精确兼容复核

复核日期：2026-10-23。此文档是源码与无模型能力复核，不是端到端通过报告。

## 问题与最小修复

Windows 官方客户端更新后输出 `codex-cli 0.163.0-alpha.11.2`。现有产品要求
`0.163.0-alpha.11`，因此在登录检查、激活与模型请求之前以
`CODEX_VERSION_MISMATCH` 拒绝。虽然已有 `expected_version` 配置，但构造器仅接受
已审核精确版本，验收材料清单与通知投影也单独绑定版本；仅改配置并不可行。

本修复新增唯一精确版本 `0.163.0-alpha.11.2`，作为产品默认值，并同步验收清单、
示例配置及投影版本。通用适配器保留旧版精确配置支持，但不会自动回退、前缀匹配、
接受通配符，或在不同实际版本间静默切换。alpha.11.1、alpha.11.3、alpha.11.20
均未审核，仍拒绝。历史报告与原始通知生产者引用不重写。

## 官方证据

- [官方 tag](https://github.com/openai/codex/releases/tag/rust-v0.163.0-alpha.11.2)
  解析到提交 `51f97c6bca5500edbb986f6d9fe64fa2e63cbdd4`。
- [变更比较](https://github.com/openai/codex/compare/5425d3e625daa58eea3ad1de27c087eb3b2ffbd3...51f97c6bca5500edbb986f6d9fe64fa2e63cbdd4)
  是 diverged 比较，所以另行读取两个完整、未截断的 Git 树逐文件核对，不能仅凭
  compare 中没有文件就认定相同。精确 blob 记录见 `codex-version-review.json`。
- exec CLI 定义、JSONL 事件结构与输出器、登录实现、功能开关、配置类型、工具规划
  的已核查文件与 alpha.11 相同。exec/lib 仅 UTF-16 解码迭代从 chunks_exact 改为
  as_chunks，不改变两字节分组或调用协议。通知生产者中仅此文件 blob 改变，通知逻辑未变。
- Windows 补丁同时更新 MXC SDK、过滤不可检查的隐式网络只读根，以及允许从 HKLM
  64 位策略注册表读取绝对 RequirementsPath。适配器不写注册表、不覆盖组织策略，
  不扩大沙箱权限；这些宿主差异仍必须通过真实 Windows 运行验证。

## Windows 无模型能力核查

协调方回传的实际 Windows 检查：OpenAI.Codex 26.1016.8622.0，SignatureKind Store，
版本为 alpha.11.2；version/help 均退出 0、stderr 0。exec help 显示 JSON、输出 schema、
output-last-message、ephemeral、ignore-user-config；sandbox 在 exec/global 可用，
不在 exec resume；ask-for-approval 在 global 可用（on-request/never）。
现有适配器使用 exec 而非 resume，并用 `-c approval_policy=never`，无需换调用协议。
这一步没有登录、模型调用、配置修改，也没有验证真实 JSONL 返回。

## 不变的验收门禁

每次调用仍要求实际版本与配置完全相等、现有 ChatGPT 登录、官方 OpenAI route、
read-only sandbox、禁用工具、隔离工作目录、受控子进程清理。
保留 JSONL 状态机、未知事件/工具活动拒绝、边界违规拒绝、完整 turn 与退出状态检查、
严格 envelope 和业务 schema 双重验证、输出秘密检查；模型实际 ID 仍不伪称已证实。
身份、凭据保存、原账本、材料字节绑定、额度、授权期限与 release 绑定均不放宽。

## 发布后的真实验证顺序

1. GitHub 构建新提交；Windows 仅通过 Git 同步最终产物并校验清单，不本地编译。
2. 新产物运行无模型诊断，确认 exact version、现有 ChatGPT 登录、清理正常。
3. 按已有明确授权复用 R18 身份，对新 release 单独登记原定最多 5 次材料、2 次 Pi、
   1 次搜索；若授权绑定需要调整，由协调方依据原授权核实，不能程序自动延展或重置旧账本。
4. 真实调用逐项验证 JSONL、schema、Writer 写入与引用、Pi 握手与调用、搜索、进程退出；
   保存脱敏报告和截图。任何失败都保留原始已消费额度，不隐藏失败或把 fixture/CI 当成 E2E。

本次云端测试只能证明版本门禁及模拟协议回归；Windows 真模型兼容性尚待上述验证。
