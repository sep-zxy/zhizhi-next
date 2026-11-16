# 云端冻结源码集成回归与可选依赖补测

验证日期：2026-10-23，UTC+08:00。

固定源码：`d115fe02d4a1ad7dc54b4198b48800dbb8278bd3`。使用 `git archive` 创建独立快照，测试期间不追随新提交；没有修改业务代码、发布、构建或操作 Windows，没有调用真实模型或使用真实账户凭据。

## 环境和复现约定

Linux x86_64；Python 3.12.14；pytest 9.1.1；uv 0.13.4；Node v24.19.0；npm 11.9.0。

以下命令将机器专属临时路径替换为变量：`SNAPSHOT` 是冻结根目录，`TEST_PYTHON` 是已有测试环境的 Python，`OPTIONAL_PYTHON` 是独立可选依赖环境的 Python，`CACHE_DIR` 是可写 uv 缓存，`REPORT_DIR` 是结果目录。运行前由执行者设置这些变量；它们不含登录信息。

Node 只读复用已有依赖，未重新安装。复用环境与冻结版本的 package-lock.json SHA256 相同：`2b8e55e94890eadfc719186801b24887490eeb0819b4f2ff63760ce2af28efc8`。

## 固定快照的完整结果

| 测试组 | 通过 | 跳过 | 失败或错误 |
|---|---:|---:|---:|
| knowledge_platform、portable_asr、Viewer token | 1666 | 11 | 0 |
| repository 契约矩阵、仓库 e2e fixture、portable 离线测试 | 166 | 0 | 0 |
| 旧 auto_capture 集成与 CLI 单元 | 30 | 0 | 0 |
| Pi TypeScript 测试 | 95 | 0 | 0 |
| 旧 pinned learn_pipeline 初次收集 | 不计通过 | 不计通过 | 1 收集错误 |

Python 完成组共 **1862 passed、11 skipped**；另有 **182 subtests passed**，不与主测试混加。1666 项细分为 agent 657、domain 595、intake 225（另11 skip）、zhizhi 176、portable_asr 4、Viewer token 9。

此外：

- `npm run typecheck` 通过。
- Python 与 TypeScript 的16工具 JSON 契约一致；该检查不执行工具或模型。
- 真实 Python JSONL 子进程与 TS provider 合成 runner 跨组件测试通过：2轮、1个工具回执、真实模型调用0次。

### 执行命令

```sh
cd "$SNAPSHOT"
# 以下四条 pytest 命令均使用此环境和调用前缀：
export PYTHONPATH="$SNAPSHOT/projects/growth/src"
export UV_CACHE_DIR="$CACHE_DIR"
uv run --no-project --python "$TEST_PYTHON" python -m pytest projects/growth/tests/knowledge_platform projects/growth/tests/portable_asr projects/growth/tests/unit/test_serve_session_token.py -q -ra --junitxml="$REPORT_DIR/knowledge-platform.xml"
uv run --no-project --python "$TEST_PYTHON" python -m pytest tests/repository tests/e2e deploy/windows-portable -q -ra --junitxml="$REPORT_DIR/repository-portable.xml"
uv run --no-project --python "$TEST_PYTHON" python -m pytest projects/growth/tests/integration projects/growth/tests/unit/test_cli.py -q -ra --junitxml="$REPORT_DIR/legacy-integration.xml"
uv run --no-project --python "$TEST_PYTHON" python -m pytest projects/growth/tests/integration/test_auto_capture.py projects/growth/tests/unit/test_cli.py -q -ra --junitxml="$REPORT_DIR/legacy-available.xml"
cd "$SNAPSHOT/integrations/pi-zhizhi"
npm test
npm run typecheck
node --import tsx test/bridge-schema-contract.mts
UV_PYTHON="$TEST_PYTHON" node --import tsx test/python-bridge-contract.mts
```

第三条初次收集失败的原因如下，不应将四组当作全部通过。

## 既有基准 fixture 缺失和后续恢复

初次错误为 `benchmark fixture directory does not exist: fixtures/eval/eval_017_token_guard`，来自 `projects/growth/tests/integration/test_learn_pipeline.py` 收集时的 benchmark manifest 校验。

基线 `57425bc0ff41a1e252f7e52004c1d342298e9a27` 同样缺失该目录；至冻结 SHA 的 benchmarks、该测试和 eval/benchmark.py 没有差异。因此这是既有快照缺失，并非本轮业务集成新回归。manifest 的其他 fixture 路径均存在。

后续集成提交 **e7d5439** 恢复四个合成 fixture 文件并新增说明 README，没有修改业务代码、loader、测试断言或 manifest 锁。以下为修复执行者提供的已验证证据，**不是本文执行者在 d115fe02 上重新运行所得**：

- 原 manifest 全套 SHA256 精确匹配 `99feae11f8ec303abad533adc82491fbdc0861cf8b9b00e04705bc16552ef1f1`。
- learn_pipeline 与 eval 测试22 passed。
- 全 integration 与 test_cli.py 41 passed。

此证据说明缺失 fixture 已修复，但不倒改冻结快照的原始1项收集错误，也不与原统计重复相加。

## 独立可选依赖补测

原11项 skip：真实 ASR材料缺失2、webvtt缺失5、av缺失1、可选微信SDK缺失3。

为补齐可云端执行的9项，创建独立临时 uv 环境，基础测试依赖通过 `.pth` 只读引用原环境。依据冻结 `projects/growth/uv.lock` 提取精确版本和SHA256依赖闭包，使用官方 `https://pypi.org/simple` 安装；没有修改共享环境或项目 lock。

安装命令形式：

```sh
uv pip install --python "$OPTIONAL_PYTHON" --no-deps --require-hashes --only-binary :all: --index-url https://pypi.org/simple -r "$REPORT_DIR/optional-requirements.txt"
```

16包实际版本：av 19.0.1（Python 3.12对应锁分支）、certifi 2026.4.22、cffi 2.0.0、charset-normalizer 3.5.2、cryptography 50.0.1、idna 3.19、numpy 2.4.4、optionaldict 0.1.2、pycparser 3.0、python-dateutil 2.9.0.post0、requests 2.34.2、six 1.17.0、urllib3 2.8.0、webvtt-py 0.5.1、wechatpy 1.8.18、xmltodict 1.0.4。仅安装官方 wheel，不编译第三方源码。

```sh
cd "$SNAPSHOT"
PYTHONPATH="$SNAPSHOT/projects/growth/src" UV_CACHE_DIR="$CACHE_DIR" uv run --no-project --python "$OPTIONAL_PYTHON" python -m pytest projects/growth/tests/knowledge_platform/intake/test_transcripts.py projects/growth/tests/knowledge_platform/intake/test_wechat_sdk.py projects/growth/tests/knowledge_platform/intake/test_offline_asr_journey.py -q -ra --junitxml="$REPORT_DIR/optional-deps.xml"
```

结果 **18 passed、2 skipped**，退出码0。其中9项为原先缺依赖的测试，另外9项为重复回归，不应将18项全部加到原通过数。

恢复通过的是实际 webvtt字幕解析5项、实际PyAV限额解码和拒绝播放列表1项、实际wechatpy SDK合成回调加解密3项。输入均为合成数据，微信测试无真实账户、真实通知或渠道请求。

仅余 `test_real_offline_asr_speech_sample` 和离线ASR journey 两项 skip，需要核验过的本地模型权重和真实语音。没有下载模型或用合成音频冒充真实语音识别通过。

## 已覆盖的关键边界

- authenticated relations 只读桥、正式关系服务、owner/public 分类隔离、精确 revision 与 candidate 降级。
- owner 固定身份、scope、启动模式与临时 Viewer token 父子进程隔离。
- Git 子树、stdin 管道清理和取消边界；Pi 合成跨组件调用。
- CLI16工具运行时与冻结 JSON 对齐，不扩大实际身份批准的 active-tools 子集。
- repository coverage matrix 的结构和路径校验，不等于每个功能都已完整交付。
- 真实 loopback HTTP 服务与重启测试使用隔离 fixture，并非生产数据或实际渠道。

## 最终交付仍待验证

- 浏览器实际 socket、页面交互及设计还原的最终可视验收。
- GitHub 构建本提交对应产物；本轮未执行需要新产物的 portable-runtime.test.mjs，不用旧产物代替。
- Windows 更新 Git 子树后的最终产物运行，以及本人既有登录态下真实模型与 Pi 交互。
- 真实 ASR 权重和语音。
- 微信、抖音、小黑盒等实际渠道端到端链路。

此报告证明云端列明范围的回归结果，不宣称所有功能完全写完，也不代表最终端到端全部通过或可交付。

## 第二轮最终集成：d78a91da

验证时间：2026-10-23 03:35–03:37 UTC+08:00。固定源码 **d78a91dacac0b728ae1a06295849085bb90f63b8**，独立 `git archive` 快照；原 d115fe02 记录保留，不混为同一版本。

复用上节已备妥的独立可选依赖环境，版本不变，没有安装更多依赖、修改源代码、创建真实身份、触发构建或操作 Windows。Node package-lock SHA256 仍与只读复用依赖一致。

| 本轮实际执行组 | passed | skipped | failure/error |
|---|---:|---:|---:|
| 全 knowledge_platform + portable_asr + Viewer token | 1742 | 2 | 0 |
| repository + e2e + Windows portable + 全旧 integration + CLI | 207 | 0 | 0 |
| Pi TypeScript | 107 | 0 | 0 |

Python 主测试合计 **1949 passed、2 skipped**，另 **182 subtests passed**，不将子测试混入主测试数。第一组耗时83.22秒，第二组9.07秒；Pi测试13.87秒。原 benchmark fixture 收集错误在本轮已消除，完整旧 integration 和 CLI 是本报告执行者重新运行的结果。

下列额外检查全部成功：

- TypeScript noEmit 检查。
- **18工具** Python/TypeScript JSON 契约同步。
- 实际 Python serve → JSONL → TS provider 的完整18工具握手，工具执行与模型调用均为0，预算限额保持原值。
- 实际 Python JSONL 子进程与 TS provider 的合成 runner 双轮，1个工具回执，真实模型调用0。
- 实际 ASGI、Git、SQLite 生成历史版本与diff回执，经 TypeScript 客户端解析验证。
- 实际 Python intake/CAS生成补料回执，经本人 TypeScript 交互命令消费和核验。

最后两项使用 fixture fetch 将真实 Python 回执交给 TS，并未建立真实网络服务或使用真实账户；不能把它们标为真实渠道端到端通过。

### 本轮命令

沿用前文环境变量定义，但 `SNAPSHOT` 指本轮 d78a91da 快照，`REPORT_DIR` 指本轮独立结果目录，`OPTIONAL_PYTHON` 指已安装可选依赖的环境。

```sh
cd "$SNAPSHOT"
export PYTHONPATH="$SNAPSHOT/projects/growth/src"
export UV_CACHE_DIR="$CACHE_DIR"
uv run --no-project --python "$OPTIONAL_PYTHON" python -m pytest projects/growth/tests/knowledge_platform projects/growth/tests/portable_asr projects/growth/tests/unit/test_serve_session_token.py -q -ra --junitxml="$REPORT_DIR/knowledge.xml"
uv run --no-project --python "$OPTIONAL_PYTHON" python -m pytest tests/repository tests/e2e deploy/windows-portable projects/growth/tests/integration projects/growth/tests/unit/test_cli.py -q -ra --junitxml="$REPORT_DIR/repo-portable-integration.xml"
cd "$SNAPSHOT/integrations/pi-zhizhi"
export UV_PYTHON="$OPTIONAL_PYTHON"
npm test
npm run typecheck
node --import tsx test/bridge-schema-contract.mts
node --import tsx test/python-bridge-contract.mts
node --import tsx test/all-tools-python-handshake.mts
node --import tsx test/history-python-contract.mts
node --import tsx test/material-python-contract.mts
```

### 新增模块覆盖与版本界限

相较第一轮，新增覆盖了只读条目历史列表与精确版本diff、分类权限与目录篡改拒绝、只读备份检查和恢复计划、发布manifest检查，以及本人CLI补料交互及CAS回执核验。备份恢复仅检查和dry-run，不执行实际恢复或发布，也不证明灾难恢复完成。

冻结之后提交 `55d198d` 仅涉及CLI help和矩阵本地ops入口同步。其2项help测试和2项矩阵测试通过是主集成执行者提供的证据，不是本轮完整快照测试所覆盖的提交；本报告不把 d78a91da 的全套结果延伸为对所有后续提交的验证。

仅两项 skip 仍为缺真实模型权重/语音的离线ASR测试。浏览器socket和实际页面交互、当前版本构建产物、Windows新Git子树与真实模型Pi、实际平台渠道的最终验证仍待执行。本轮无失败不等于所有功能完成或端到端交付通过。
