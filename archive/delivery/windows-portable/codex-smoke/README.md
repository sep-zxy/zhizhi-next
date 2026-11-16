# 独立 Codex adapter 验收工具

这是云端预写、另行交付的运行工具，不改变已经交付的补丁及其身份。不编译、不安装依赖、不改配置、不新建服务 token 或设备身份。它调用最终包中的 `build_runner_from_config` 和 `ModelRunner.run`，不是直接绕过 adapter 调用 CLI。

## 先决条件

- 目标固定 `C:\Code\zhizhi-e2e-20261020`，已应用提交 95eab3d 的补丁。该补丁 ZIP SHA256 为 `6263a182c2805098ea700482c4214058aecb4bb680b01504a622de338a0bbd99`。
- 核对本工具 ZIP 的交付 SHA256，再解压到目标下单独目录，例如 `codex-adapter-smoke-tool`。不要放进 app/runtime 或覆盖已有文件。
- 先用原 stop.cmd 关闭此包应用，保留已有 data/launcher.lock。工具只锁住原文件 byte 0，不修改内容；锁缺失或占用会阻断。
- 仅使用已经核验的官方 Codex CLI 0.163.0-alpha.10.2 和原有 ChatGPT 登录。不要新登录、创建 token、填入任何密码/key，或读取、复制认证文件。

## 运行

运行 `run.cmd "已有官方 codex.exe 的绝对路径" "已有 Codex home 的绝对路径"`。这一步明确授权官方 CLI 复用既有登录，仅调用一次固定公开算术请求。参数只能填路径，不传 token。

工具先检查固定 distribution.json SHA256、全部 immutable 文件及组件清单，再加载已交付 backend。精确请求模型 gpt-6.1-sol，固定输入 `What is 2 + 2?`，输出 Schema 仅允许 `{"answer":4}`。不接受外部提示词、资料、模型、API URL 或备用路由。120 秒 adapter 超时，不自动重试。

只有报告 status=adapter_smoke_passed 且进程退出 0 才通过。waiting_auth、waiting_quota、blocked、invalid_output、fixture 和退出非零均不算通过。失败先回传固定错误码，不现场写代码修复，不跳过完整性检查。

## 写入范围与证据界限

应用写入仅在 `reports/codex-adapter-smoke/<唯一运行目录>/`，含临时 HOME、TEMP 和 report.json；不覆盖既有报告或 config/state。子进程不继承 API key、代理、Node 注入等环境。Codex home 路径只传给官方 CLI，工具不读取其内容；CLI 自己可能刷新原登录，这是已有登录复用的一部分。

report.json 不记录原始模型正文、stderr、凭据、账号标识或传入身份路径。actual_model_id 始终 null，requested 精确记录 gpt-6.1-sol。工具事件检测是事后阻断，不保证撤销副作用，不等于 OS 沙箱证明。此结果只证明该 adapter 的固定非敏感请求，不是整个平台渠道、知识入库或业务 E2E。

云端只运行无网络单元/fixture 检查。Windows Job、真实订阅和本工具真实运行必须由 Windows 验收结果确认。
