# Pi 原生 Codex CLI Provider 薄桥

## 与旧路径的边界

新的 `portable/launch-codex-interactive.mjs` 是独立、固定的便携主验收入口，只有
`zhizhi-codex-cli / gpt-6.1-sol`。原 `launch-interactive.mjs` 和后台 model-guard 路线保留，
但新桥无论失败、配额不足、取消或缺认证都不会跳过去。

Pi 仍是官方锁定 1.3.0，注册完整原生 Provider；不是修改 Pi 的模型协议、fork Pi 或调用其原生
OpenAI Provider。Python 沿现有 CodexModelRunner 使用已授权的正式 Codex CLI 登录。此改动不登录、
创建 OAuth、填充假 API key 或复制 token。Provider 的官方 ambient auth 槽只表明包内配置可读，
`hello ready` 不是订阅认证或模型成功的证明。生产 Python 工厂缺少独立授权配置时保持 blocked。

## 受控启动合同

启动器提供已验证的 `ZHIZHI_PORTABLE_ROOT`，`ZHIZHI_PI_PYTHON` 必须精确指向其中
`runtime/python/python.exe`，不允许另外的 Python、URL、模型或任意 argv。固定命令：

`python -B -s -X utf8 -m ahadiff.knowledge_platform.model.pi_bridge --owner-acceptance`

`ZHIZHI_PORTABLE_STATE`、HOME/USERPROFILE/APPDATA/LOCALAPPDATA、TEMP/TMP/TMPDIR 及
XDG_CONFIG_HOME/XDG_CACHE_HOME/XDG_DATA_HOME 都必须存在并在包内；TEMP 三项一致。
只把这些路径、系统运行所需 PATH/SystemRoot/WINDIR 及禁用用户 site/字节码环境送到 Python。
不向 Python/Codex 传领域 agent/user token、API key、代理、NODE_OPTIONS 或 PYTHONPATH。

Pi 使用包内独立目录，禁止 models.json、外来扩展、MCP、skills、上下文发现及会话落盘。
固定十项领域工具 CLI 白名单，再由 Python 可信 hello 收紧到当前允许子集。
Pi 1.3.0 的裸 `--no-tools` 会移除全部扩展工具，不能靠 setActiveTools 恢复；因此新入口使用
`--no-builtin-tools --tools <固定领域工具>`。模型只能执行本进程实际发出且仅执行一次的工具建议。

## 消息与终态

私有 JSONL v1 合同见 `../../archive/acceptance/implementation/PI_CODEX_PROVIDER_BRIDGE.md`。
工具 schema 必须与官方 getCurrentTools 返回的当前声明以及后端可信 schema 一致。
只把当前新用户轮的明文、单个工具建议及完整分类领域回执发给 Python，不发送 Pi 系统提示、cwd、
隐藏 thinking 或旧进程工具回执。工具异常只发送固定 DOMAIN_REQUEST_FAILED。

每轮最多 4 个模型调用、3 个工具调用；每进程最多 16 个模型调用，部署可进一步收紧。
同名同参不重发，写入失败/未知后本轮只读核对；最后模型调用不能再发工具。
失败/取消是独立 error/aborted 终态，不包装为成功文本，不自动重试或降级。
取消通过私有管道传播，等待有限退出期限后终止自有进程。

model task_id 仅关联本次模型调用，不是领域持久任务。只有领域 Envelope.task_id 可供任务查询。
actual_model_id 没证据就是 null，不填 responseModel；上游无 usage 时标记 usage_available=false。
Pi 必需数值槽和 catalog cost 的 0 仅为协议占位，cost_available=false，不表示实测免费或零消耗。

## 可复核测试边界

- `npm run typecheck` 和 `npm test`：协议/schema、严格工具来源、分类、预算、防重放、取消、
  子进程 framing/错误/清理以及真实锁版本 Pi RPC 工具循环。
- Pi RPC 测试确实完成两轮 Provider 与一次真实领域工具执行，但模型和 HTTP 回执全部是合成注入，
  无网络请求、无真实身份、无 Codex 调用。测试注入入口不在生产环境变量中开放，测试文件不装包。
- `node --import tsx test/python-bridge-contract.mts`：显式额外跨语言检查，要求已配置项目 Python
  测试依赖与 PYTHONPATH，命令仍经 uv 启动 Python。真实 Python serve/PiBridge、TS JSONL 和 Provider
  交换 hello、工具建议、完整分类回执与最终文本，但 ModelRunner 是测试内合成实现。
- `node --import tsx test/bridge-schema-contract.mts [可信后端 JSON 路径]`：静态对照全部十项
  实际注册 TypeBox 与 Python pi_tools_v1.json，仅归一化无 required 与 required 空数组，不激活工具。
- 以上不证明 Windows 成品、正式账号登录、真实模型或完整用户 E2E 已通过。不得据此激活或收费。
