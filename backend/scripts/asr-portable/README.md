# Windows x64 离线 ASR 组件协议

用于 CPython 3.13.16 embed、CPU/int8；不是 GPU 包。资源由云端准备，Windows 只接收已装配结果，禁止 pip/npm/编译与自动安装 VC++ Runtime。

## 交付约定

- `wheels/`：来自 growth/uv.lock 的 faster-whisper 全部传递依赖，官方 PyPI wheel URL、版本、SHA256 固定。总装按文件名和hash与基础依赖去重；使用云端 cross-target 安装到总装 site-packages，保留 dist-info、licenses 和 DLL 原布局。
- `data/models/asr/tiny.en/`：Systran/faster-whisper-tiny.en revision 0d3d19a32d3338f10357c0889762bd8d64bbdeba，含 tokenizer 与词表。模型 MIT；保留模型卡和上游许可。这是英文小模型，不能宣称已通过中文质量验收。
- `ASR_RESOURCE_MANIFEST.json`：资源与许可来源/hash；模型文件先前已对照官方 Git blob/LFS 校验，本轮再次 SHA256 校验。
- `samples/synthetic-speech.wav`：5.49秒合成英文冒烟样本；保留 Flite 版权。不是自然人录音，也不是抖音真实渠道 E2E。
- `PE_IMPORT_AUDIT.json`：云端 objdump 静态 import 表，不是 Windows 动态加载验证。
- `healthcheck.py`：放到总装 scripts，使用已装配 python.exe，参数 `--root <包根> --manifest <ASR_RESOURCE_MANIFEST.json> --sample <samples/synthetic-speech.wav> --output <报告路径>`。不传 sample 仅验证模型加载，不能算语音识别通过。在Linux执行必须返回 BLOCKED。

## 系统前置与失败策略

CTranslate2 官方安装说明：https://opennmt.net/CTranslate2/installation.html 。Windows 要求 Visual C++ runtime。静态依赖包含 MSVCP140.dll、MSVCP140_1.dll、VCRUNTIME140.dll、VCRUNTIME140_1.dll。健康检查逐项加载；任一缺失返回 VC_RUNTIME_MISSING / BLOCKED，不下载、不安装、不更改系统。若目标未满足须单独请求用户对系统安装的许可，不能把它算作已完全自包含的免安装包。OS 的 UCRT、系统DLL和 CPython runtime DLL同样须可加载。

Intel OpenMP libiomp5md.dll、ctranslate2.dll、NumPy OpenBLAS、PyAV FFmpeg组件由已核验 wheel提供。CPU路径不要求 CUDA/cuDNN。静态 import不涵盖全部运行期动态加载，必须在授权的Windows验收阶段实跑。PyAV wheel目前仅检出PyAV BSD正文，不能替代其FFmpeg及codec依赖许可；Intel OpenMP亦需独立许可。资源manifest明确标记license_pending，未补齐前不发最终包。此包未重编译这些依赖。

## 验收等级

1. 云端：锁闭包/Windows tag、资源hash、许可证保留、静态PE检查和单元测试。
2. Windows：实际DLL/import、离线模型加载、合成音频实际推理及预期短语。报告不能用Linux结果替代。
3. 完整产品 E2E：在获准测试范围内拿到真实媒体，真正解码/识别、preview→keep→后续流程；字幕、标题、fixture或合成音频不能代替。

时间戳越界时生产逻辑保留原始诊断、有效segments为空；不能声称精确定位通过。
