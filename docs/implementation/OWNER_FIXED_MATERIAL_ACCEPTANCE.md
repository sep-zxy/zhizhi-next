# 固定合成材料的独立模型验收入口

状态：源码与离线合成测试；不是 Windows 身份创建、模型调用或最终 E2E 证据。
真实操作需另外明确批准具体版本目录、既有官方 Codex 登录目录、独立 owner 身份及以下材料/阶段。
命令开关只是安全约束，不能代替该批准。此入口不包含 Pi provider 激活。

## 默认关闭与来源绑定

- 普通 `start.cmd`、`local-test-start.cmd` 和基础 `owner-acceptance-start.cmd` 行为不变。
  基础 owner 的 `desktop.json` 始终要求 `model.enabled=false`、`providers=[]`。
- `owner-model-activate.cmd` 不内置批准参数。仅显式传入
  `--approve-owner-acceptance-model --expected-source <40位GitHub源码SHA> --expected-release <source12-run-attempt>`
  以及 `--codex-exe <已有官方CLI绝对路径> --codex-home <已有获准登录目录绝对路径>` 才可申请激活。
  激活不调用 CLI，不登录、不复制凭据，不创建身份。必须先另行初始化获准 owner，且平台/桌面目录仍为空。
- 激活核验 GitHub 构建来源、候选版本、产品逐文件摘要和必需阶段，不采用旧云端包。
  记录绑定完整版本根、源码 SHA、release、distribution 摘要、固定材料 manifest、端口和已有 CLI 路径。
  任何改变都拒绝复用旧激活；不会删除、迁移或接管已有账本。
- `owner-model-start.cmd` 只读取既存绑定，缺失即拒绝。启动 worker 与私有 Writer 仅限下述固定链。
  不接受任意模型配置、外部材料文件、渠道、ASR、公开发布、任意 answer 或 legacy prompt。

## 模型和材料

`acceptance_model.material_manifest()` 是唯一材料定义：两段源码固定 UTF-8 文本，无结尾换行，
分别标记合成原证据及“非真实用户立场”的合成解释，包含长度与 SHA-256。模型固定
`codex_cli / openai / gpt-6.1-sol / 0.163.0-alpha.11`，只复用已有 ChatGPT 订阅登录。
不降级、不通配版本、不回退 API key/其他提供商；原 Codex 通知阻断策略不变。

应用层至多五个 CLI 模型任务：原文预览、原文生成、独立复核、解释生成、解释独立复核。
这是应用调用次数界限，不声称掌握官方 CLI 或服务商内部网络重试数。
每阶段调用前以独占文件创建持久占用，失败/丢失/未知即消耗该阶段，不自动再请求。
成功结果仅允许同一指纹/任务的精确本地重放，不形成第二次调用。
返回必须来自真实配置的 Codex runner，fixture、版本/请求模型/输入指纹/输出摘要不匹配全部拒绝。
上游未提供实际模型 ID 时保持未知，不能将请求模型或模拟元数据冒充实际报告。

## 真实链路与边界

1. 只接收固定 RAW 文本；URL、补料、附件、私人附言、其他渠道和任意扩展字段拒绝。
2. 使用原 Intake/Generation/AIReview/Domain/Git Writer 服务；不以模板覆盖真实模型输出。
   正式正文可为真实同链派生，签封实际 entry ID、修订、内容摘要和标题。
3. 独立生成者、复核者与 Writer 保持原主体权限。普通 `needs_ai_review` 不可由用户绕过模型复核；
   原策略判为 `needs_user` 的重大个人表达仍要求真实用户确认精确 diff、nonce 和基准修订，
   不伪造该分支已经调用 AI reviewer。
4. 自由学习解释只能引用上述真实正式版本，目标须独立。服务只读固定桌面库，校验已保存 context、
   owner/device/origin、精确来源和不可变原话快照；不接受请求传来的路径或哈希作为唯一证据。
   launcher 的 `--repo-root` 是 `data/owner-acceptance/desktop`；viewer 的真实状态目录为其
   `.ahadiff` 子目录，跨进程核验固定读取 `desktop/.ahadiff/growth.sqlite`。不回退到
   `desktop/growth.sqlite`，不复制/移动旧库，不在验证时初始化或迁移。装配回归使用实际 launcher
   命令与 CLI 状态解析及真实 context/CAS/outbox 读写，但不运行 Windows、DPAPI 或模型。
5. 验收域只将 publication intent 收窄为 private，不改原证据/解释字节；存储回执绑定收窄后的实际 payload。
   来源、候选、模型复核、批准和 Writer 均有同链摘要核验；公开发布出口明确拒绝。

签封使用已有 DPAPI owner token 在内存域分离派生的 HMAC，不新增或保存独立密钥，不把 token 送入模型。
状态位于版本根 `data/owner-acceptance`，交付 Git 忽略，不上传运行状态。
路径逐祖先拒绝 reparse，但不宣称这是对同一 Windows 用户恶意进程的 OS 沙箱。

## 崩溃与验收事实

领域数据库/Git 写入和独立签封文件不是跨存储原子事务。若写库/Writer 后、签封前崩溃，可能停在
未完整对账状态；缺失、部分写入或不匹配的签封必须 fail closed，不能宣称成功或无限重发模型。
先核对真实领域幂等回执与 Git 结果，再由协调者处理，不能手工编辑签封或清空预算绕过阻断。

离线测试使用明确合成身份与模拟 transport，同时执行真实隔离 Domain/Git Writer，覆盖普通独立复核、
重大原话人工确认、完整 preview→keep→generate→review→Writer、篡改、重放及默认关闭包装。
这些测试不证明真实 Windows DPAPI、订阅模型、浏览器或跨应用 E2E 已通过。
