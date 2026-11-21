# 隔离运行与验收手册

这份说明对应本仓库实现，不代表真实账号已经接入、部署完成或端到端验收通过。

## 1. 当前运行单元

- Python 服务：`python -m ahadiff.knowledge_platform`，统一任务、来源、提案、独立审核、Git Writer、检索和渠道装配。
- Pi 扩展：`integrations/pi-zhizhi`。固定 Pi 1.3.0，模型订阅资格与人工登录必须独立验证。
- 知枝原应用：设置 `AHADIFF_KNOWLEDGE_CONFIG` 指向桌面配置后，通过 RemoteBackend 接入统一领域服务；启动及每 15 秒重放持久 outbox。
- 发布：只从固定 Git revision 投影到全新 public staging，再交 Quartz；不调用模型，不直接发布 RAW。
- 密钥：OpenBao KV v2，只读精确引用；工作负载身份创建、初始化/解封和真实秘密写入不由应用执行。

## 2. 准备

在 PowerShell 7 中执行。Python 使用 uv。安装到隔离环境：

```powershell
Set-Location backend
uv sync --frozen --group dev --extra wechat
```

凭据文件由已获授权的部署方安全挂载，POSIX 权限须为 0600 或更严格。不要把真实 token 写入 Git、Wiki、提示词、截图或日志。身份文件包含 `identities` 列表，每项指定 `owner_id`、`actor_id`、`kind`、`scopes`、`allowed_classifications` 和 `token`。示例配置不生成任何真实身份。

普通服务启动：

```powershell
uv run python -m ahadiff.knowledge_platform `
  --state-directory /srv/zhizhi/state `
  --identities-file /run/secrets/zhizhi-identities.json `
  --owner personal-owner --host 127.0.0.1 --port 8766 `
  --model-config /etc/zhizhi/model.json --provider openai `
  --run-worker --auto-commit
```

不提供模型配置时明确禁用模型。`--auto-commit` 仅使已通过领域审核的提案进入 Writer，不能绕过四类人工门禁。默认工作线程允许 public/personal；处理 employer 或 third_party_private 需显式 `--allow-classification`，还须模型路由与数据发送授权同时满足。不得把放宽配置当作用户许可。

## 3. 渠道与秘密

`--channel-config` 显式加载官方连接器配置。配置含 `bindings`、`telegram`、`wechat_kf`、`secrets`，可另设 `native_notifications_enabled: true` 启用已获授权的原生回执；默认仅收件，配置本身不代替真实账号发送授权。

- bindings：channel/account_id/chat_id/sender_id 四元组映射到既有 actor_id；不根据昵称、消息正文或首次来信自动登记。
- secrets.address：自托管 OpenBao 的 HTTPS origin，不能包含账号、路径、查询串。
- secrets.identity_file：受保护的 bootstrap identity JSON，含已有 token；相对路径以渠道配置目录为基准。
- secrets.references：名称映射 `{mount, path, key}`，只允许这些精确 KV v2 引用。
- telegram：enabled、account_id、token_reference。已有 webhook 时 fail-closed，不自动删除。
- wechat_kf：enabled、account_id、corp_id、open_kfid、api_permission_confirmed、api_managed_confirmed，以及 callback_token_reference/aes_key_reference/access_token_reference。缺主体资格或 API 权限不启用。

当前读取器不为 OpenBao 发 token、不自动刷新微信 access token、不初始化秘密服务。已授权的秘密代理负责续期和轮换。OpenBao 启动时暂不可用会持久记录 waiting_auth，API 继续运行，微信回调返回 503 而非伪确认；定时重试已授权的配置读取。不会回退明文。此恢复已有合成 sealed 故障测试，尚未证明真实 OpenBao 部署和轮换全链路。

官方协议来源：[OpenBao KV v2](https://openbao.org/docs/secrets/kv/kv-v2/)。运维可使用官方 `bao kv get -mount=<mount> <path>`，不要把输出传给模型或保存到日志。应用侧使用同等 HTTP 接口，但只向服务内存返回被授权的单个字段。

## 4. 桌面配置

`AHADIFF_KNOWLEDGE_CONFIG` 指向包含 service_url、identity_file、device_id、providers、input_classification、model 的 JSON。device_id 必须稳定；identity_file 是受保护的已有服务 token。远端 service_url 必须 HTTPS，仅本机 loopback 可 HTTP。

统一模式不会因断网偷偷写第二套正式 Wiki。202 仅表示已提交，只有正式 commit receipt 才表示写入。冲突、等待认证与不确定投递均保留原始请求和状态。

知枝学习重点已改为架构、技术栈特点、适用边界、设计取舍和深入运用；代码语法只作必要证据。历史卡片保留，不冒充用户已经理解或掌握。

## 4.1 字幕与本地转写

安装 `--extra captions` 使用固定 webvtt-py；可选 `--extra local-asr` 安装固定 faster-whisper。`--asr-model-directory` 必须指向已有绝对本地目录，包含完整模型、配置、tokenizer。生产运行不自动下载权重，也不调用付费音频 API。

下载逐跳执行 SSRF 检查，字幕上限 512 KiB，媒体上限 8 MiB，解码与 ASR 有独立子进程、时长和超时限制。转写只代表音轨，不声称覆盖画面；无字幕/模型时明确等待，不能把视频标题当完整内容。原始音轨和字幕作为私有来源证据保留，不发布媒体。

## 5. 验收

首期在 GitHub 托管 runner 验证云端 CLI 和知识服务、Web 客户端与 Agent 协议。CI 检查范围、构建目标和真实验收分别见 [验证规则](../../docs/testing.md)。部署方使用模型或秘密服务前，需要准备获准的账号、凭据和最小权限身份。
