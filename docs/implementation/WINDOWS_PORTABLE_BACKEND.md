# 便携包后端路径与 Git 隔离契约

本轮仅在云端实现和验证。Windows 只接收、运行预构建便携包，不安装开发依赖或编译。本文件不宣称 Windows 原生执行已通过，也不是操作系统级沙箱保证。

## 启动方必须提供

使用包内 Python，启动参数至少含 `-B -s`；启动前设置 `PYTHONNOUSERSITE=1`、`PYTHONDONTWRITEBYTECODE=1`。嵌入式 Python 的导入路径必须全部位于包内，不依赖用户 site-packages；包内 Git 放入进程 PATH 的首选位置。工作目录必须在包内。

- `ZHIZHI_PORTABLE_ROOT`：已存在的包根绝对路径，不允许网络路径、根目录或其祖先经 symlink/junction 重定向。
- `ZHIZHI_PORTABLE_STATE`：包内 `data` 绝对路径。领域 SQLite、vault、对象、收件缓存及领域备份均必须在此目录内。
- `HOME`、`USERPROFILE`、`APPDATA`、`LOCALAPPDATA`、`TEMP`、`TMP`、`TMPDIR`、`XDG_CONFIG_HOME`、`XDG_CACHE_HOME`、`XDG_DATA_HOME`：全部显式设置为包内绝对路径。TEMP/TMP/TMPDIR须指向同一提前存在且可写目录，可用包根/temp。
- 如设置 `PYTHONPYCACHEPREFIX`、`HF_HOME`、`HF_HUB_CACHE`、`HUGGINGFACE_HUB_CACHE`、`TORCH_HOME`、`MPLCONFIGDIR`、`NUMBA_CACHE_DIR`、`PIP_CACHE_DIR`、`UV_CACHE_DIR`，同样必须包内。缺省缓存以已隔离HOME/XDG为基础，不在运行时下载依赖或模型。
- 浏览器独立profile、Node缓存、前端构建产物和进程日志由总装入口显式放包内；本后端检查不替代其验证。

只读预检命令：

```text
<包内Python> -B -s -m ahadiff.knowledge_platform.portable --state-directory <包根>/data --write-path <包根>/logs
```

成功退出0，stdout JSON包含 `portable:true`、`status:"ok"`、`git_bundled:true`；失败退出2并输出错误原因。预检不创建目录、数据库、身份或配置，不启动服务。它会将当前Python进程的tempfile位置绑定到有效TEMP，后续目录不可写时失败而不是回退系统temp。单独预检进程不替代实际服务里的同项检查。

## 后端实际强制点

- 现有 `ahadiff serve --repo-root ...` 在读取工作区配置前验证便携环境与repo根，viewer应用构造时再次验证state/config/lock路径。普通非便携模式保持兼容。
- 统一知识服务启动前验证state和身份/模型/渠道/ASR配置路径；便携模式只允许127.0.0.1。没有合法身份配置时不绕过现有验证、不创建账号、不把只读界面冒称统一写入已接通。
- DomainService在打开SQLite之前验证全部写路径，拒绝包外数据库、vault、objects及cache；只读领域入口也验证其读路径。领域备份仅允许data内。
- 路径使用实际宿主的Path解析：Windows大小写按本机路径语义处理，已有父目录的symlink/junction及不存在子路径均解析后判断，不用字符串前缀判断归属。
- Python字节码必须在解释器启动时已关闭。预检无法撤销Python启动前、恶意扩展或外部进程已经发生的写入。

## Git不依赖启动器的配置注入

GitWriter仍清除继承的所有GIT_*，再显式禁system/global config及交互提示；每条实际Git命令都通过argv覆盖hooks、fsmonitor、自动gc/maintenance、签名、credential helper和网络协议。即使启动器GIT_CONFIG_COUNT被清掉，这些限制仍生效；便携模式每次调用还检查Git可执行文件确实在包内。

新vault执行 `git init --bare --template=`，不复制用户或系统模板。prepare的临时index只建在专用bare vault内，由TemporaryDirectory清理本次独占随机目录，不扫描删除其他同名前缀的既有目录。只读Git、CAS更新、恢复祖先检查使用同一安全命令构造器。

## 验证口径与剩余边界

新增测试实际执行Git：恶意global/system/环境模板不被读取或复制，仓库内reference-transaction hook无法触发，临时index包内且既有内容保留；路径测试覆盖外部缓存、外部Git、未禁字节码、无效父目录链接、缺失子路径、相似前缀目录及身份路径越界。云端Linux结果不能替代Windows Git/NUL、大小写、junction、嵌入式Python和最终启动器原生验收。

该机制隔离正常产品运行的配置/数据/缓存路径，不承诺操作系统事件日志、杀毒软件、崩溃报告、预取或其他系统服务完全不写磁盘；也不替代对同一用户恶意并发替换文件的OS权限隔离。原授权、安全门槛、模型/渠道身份以及真实E2E要求不变。

后台 Pi 子进程仅在便携模式继承已逐项解析校验的包内 HOME、USERPROFILE、APPDATA、LOCALAPPDATA、TEMP/TMP/TMPDIR、XDG 和可选缓存路径，以及便携根/状态标识。其身份目录必须位于 data 内，guard 文件必须在包内；不增加 provider_environment 的允许项，不继承 ambient API key、代理或 NODE_OPTIONS。这不是新增订阅授权。

离线 ASR worker 的 Python 使用 `-I -B`，避免 `-I` 忽略环境变量后重新生成运行时 pyc；便携模式模型目录与继承缓存路径同样必须在包内。模型离线资源是否随包分发仍由其许可和资源验收决定。

本次云端回归：完整 `tests/knowledge_platform`、仓库 `tests/e2e`、serve app/static/tasks 和 install 单测合计 **942 passed / 0 skipped，32.40s**；包含22项新便携隔离测试，真实Git和实际tiny.en识别合成音频。其他远端模型/网络为fixture，不能算真实账号或Windows端到端。Ruff针对新增及相关后端模块检查通过，`git diff --check`通过。
