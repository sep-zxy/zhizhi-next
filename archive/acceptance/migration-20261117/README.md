# 2026-11-17 仓库迁移验证记录

此记录描述对应提交的实际验证范围，不是产品完整验收，也不替代后续 Actions 结果。

## 源码和历史

- 原始基线：62397c2ffed74d4938149f5d40e4d7a1b88c7c29。
- 私有仓库：<https://github.com/sep-zxy/zhizhi-next>；main 保留基线的祖先关系。
- 原始快照标签：migration/zhizhi-platform-62397c2f。原仓库远端保留为 upstream，新仓库为 origin。
- 精确路径映射见 [layout.json](../../../docs/migration/layout.json)。保留 ahadiff 包名、CLI、应用数据标识及三个可选组件。

## 云端证据

| 来源提交 | 证据 | 结果与边界 |
|---|---|---|
| dd76aae02e3f3e73139140b1fe85c222999db59b | [后端构建](https://github.com/sep-zxy/zhizhi-next/actions/runs/43908421669) | 成功生成含学习前端的 wheel，通过资源检查，保存校验值和来源回执；不证明原生安装或真实账号可用 |
| f0033f166682d22aa9958854e9b3564bc507871c | [全组件 CI](https://github.com/sep-zxy/zhizhi-next/actions/runs/43910744472) | 全仓语法与仓库规则、两个浏览器交互任务、Python/TS 协议组合及其余八个组件检查通过；后端 Linux 任务亦成功，其他平台分别报告 |

学习界面单元测试为 769 项通过。交互测试使用合成资料，不调用真实模型，不执行截图比对、录屏或视觉评审。桌面组件的普通 CI 仅验证宿主语法；Windows/macOS 原生安装包使用手动构建工作流，本记录不宣称已完成。

同一 f0033f16 运行中，后端 Linux 单元与仓库回归为 4,318 项通过、16 项原生平台条件跳过；隔离组合为 1,870 项通过、3 项 ASR 权重/依赖条件跳过，Graphify 性能门禁通过。上述两组数量分别报告，没有累加不同运行的通过数。

Python 严格类型对照在 Linux 检出当前 2,238 个错误，原始提交同为 2,238 个，新增 0 个；这表示没有新增同类诊断，不表示严格类型已清零。Windows 新增检查脚本的默认编码和跨盘临时目录问题另行修复，不调整原有类型规则或忽略清单。

## Windows 失败与后续修复方向

f0033f16 的 Windows 单元组在 10 项失败时停止：231 项通过，其余未执行，不计作通过。隔离组合组为 8 项失败、59 项通过、2 项跳过、2 项错误。实际日志和 JUnit 保留在上述 CI 任务。

1. 部分测试只提供 HOME 的显式环境，却走到了 Windows 必须提供 APPDATA 的配置分支。需补齐跨平台测试环境，并保留“APPDATA 缺失应报错”的反例。
2. SQLite 并发测试清理临时目录时遇到 WinError 32。需核对连接释放与线程退出顺序，再验证原生 Windows 文件占用语义。
3. Codex 进程清理测试把模拟进程交给真实 Windows Job Object，遇到 WinError 87。需区分模拟进程与原生句柄的测试边界，保留真实进程树清理验证。
4. 一个超长参数用例使 PYTEST_CURRENT_TEST 超过 Windows 环境变量长度。需缩短测试 ID，保持原测试内容和长度断言。

相关源码及代表性测试与原始基线的 Git blob 一致，核对信息见 [windows-source-comparison.json](windows-source-comparison.json)。该核对只证明文件未改写，不能代替在原始树中重新执行整套 Windows 测试；本轮不扩大为底座行为重写，也不通过删除、跳过或非阻断配置掩盖失败。

macOS runner 长时间排队后已开始执行，截至本记录尚未取得完成证据，应报告未验证。真实账号、模型、渠道、Obsidian 双向编辑、原生安装及公开部署不在本次 fixture 和构建证据范围内。
