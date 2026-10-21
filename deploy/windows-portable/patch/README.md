# 知枝 Codex CLI 小补丁

此补丁全部在云端制作。Windows 只运行现有包内 Python 和已写好的脚本，不安装依赖、不编译、不生成代码。默认仍使用 Pi，补丁不启动服务、不调用模型、不登录、不新建 token，也不改任何现有配置、状态、资料或浏览器目录。

## 前置条件

- 目标只能是 `C:\Code\zhizhi-e2e-20261020`。
- 原始发布包 `zhizhi-windows-x64-portable.zip` 的 SHA256 必须是 `f326f38b3c5ef8df821fa3b8d8f8c48d8bfa8bc18fbb12d1e74931ef91a473d0`。已解压目录用原 distribution.json 精确 hash 以及全部 immutable 文件逐项核验。
- 先通过原包的 stop.cmd 关闭本次应用。不要关闭无关用户程序。
- 目标需保留之前启动所生成的 data/launcher.lock。Windows 在校验和修改期间锁住该文件的 byte 0；锁被占用或文件缺失时拒绝修改，不创建或改写 state。缺失时先报告，不手工造锁或绕过检查。
- 将补丁解压到目标根目录下单独子目录，例如 `codex-cli-patch`，不能解压到 app 或 runtime，也不能直接手工覆盖 payload。

## 操作

1. 运行补丁目录的 `check.cmd`，只验证原包和补丁 payload。
2. 运行 `apply.cmd`。只更新 patch.json 列出的 backend/provenance 文件，最后更新完整性清单，再检查所有 immutable 文件与 backend/Pi/Wiki 子清单。
3. 要还原，关闭应用后运行 `rollback.cmd`。before 目录含旧文件原始字节；补丁新增文件会删除。原文件内容有非补丁修改时拒绝覆盖。
4. 如果执行中断，启动器会因清单不匹配拒绝启动。可运行 rollback.cmd 恢复；若提示文件已被第三方改动或残留 staging 文件，先报告，不跳过清单或强制覆盖。

patch.json 给出原/新文件的完整 SHA256 清单。它不是数字签名；请从已确认来源获取补丁并核对交付时的 ZIP SHA256。不要运行陌生来源重新生成的补丁。

## 模型配置

`model-codex.example.json` 只是占位示例，不会自动写入现有 config。由已有授权的运行任务填写官方 CLI 路径和已有 Codex home，模型精确为 gpt-6.1-sol。不要填入密码、token 或 API key。官方 CLI 自己访问并可能刷新原登录；本应用不会读取或复制其认证文件。

详见 ADAPTER-CONTRACT.md。工具事件检测不能撤销工具副作用，read-only 不等于绝对无工具；实际模型在 JSONL 中未知，不能把 requested_model_id 当实际型号。此次补丁的云端 fixture/完整性/回滚验证不等于 Windows 实机或真实模型 E2E 通过。
