# 离线真实 ASR 重新应用及本轮验收

日期：2026-10-20。本次从持久 baseline 33b4dc1 重新应用实现，不是找回旧提交。旧会话测试不计入本轮结果。用户本轮临时允许云端 Bash/Python CLI；未使用用户电脑、绕过被拒下载、公开推送或付费服务。

## 本轮实跑

- 阶段1提交 52de7e3c318b009e414fbfd56be55e5164f1b9bc：通用样本及 intake→preview→keep 测试；无模型时 157 passed / 2 skipped。
- 阶段2提交 6d62018c3b81d94cb8711ca7e91a96a911880789：越界时间戳隔离；无模型时 161 passed / 2 skipped。
- 重新下载、核验并配置真实模型后：完整 intake 目录 **163 passed、0 skipped，3.70 秒**。真实识别测试 1.24 秒、入口至收藏测试 1.06 秒。
- 独立实际推理耗时 1.103 秒，engine=faster-whisper、language=en、音频 5.49 秒。输出：The quick brown fox jumps over the lazy dog. This is a local speech recognition test.
- 原始模型时间 start=0/end=10 超过真实音频时长，现返回有效 segments=[]、timing_valid=false，原始片段保留为诊断。固定警告：语音模型时间戳超出实际音频范围，已停用时间定位；原始时间仅供诊断。
- ruff check/format 与 git diff --check 通过。没有宣称全项目或真实平台全部 E2E 通过。

## 样本来源、许可与范围

仅使用云环境已安装 FFmpeg 7.1.5-0+deb13u1、CMU Flite 2.2、slt voice 合成上述非用户测试句。音频为 **synthetic audio**，但 PyAV 解码和 tiny.en 推理真实执行，不使用静音、mock识别、字幕回填或把预期文本传给模型。页面/媒体 HTTP transport 是 fixture；不代表 JFK 官方录音或抖音真实视频 E2E。旧固定 JFK 用例已明确改名为通用语音样本，默认旧短语仅为兼容旧配置。

许可本轮重新核对 `/usr/share/doc/libflite1/copyright`：CMU/Language Technologies Institute 宽松许可允许使用、修改、分发，需保留署名与免责条款；来源 http://cmuflite.org 。没有重新分发声音库/模型二进制。FFmpeg 和 Flite 均为现成环境工具，没有安装未知软件。

样本 16 kHz、单声道 PCM s16le；SHA256：ecc0b6057327c4e4ef89fd34e8555ea9ed6ca347666201c918f4fe1c72b9f6dd。生成脚本为本目录 generate_asr_speech.ps1；本轮无PowerShell，按已授权等价 Bash 调用 FFmpeg 参数生成。不同工具版本需重新核验hash。

## 权重核验

正常官方来源 https://huggingface.co/Systran/faster-whisper-tiny.en ，固定 revision `0d3d19a32d3338f10357c0889762bd8d64bbdeba`。本轮官方元数据 sha 与固定版本一致。五文件逐项校验长度；model.bin 校验官方LFS SHA256，其他文件校验官方Git blob SHA1并另记SHA256：

- README.md：1323 bytes；1b7816626bf0548e4b71a467f4112b4592924bc818e3117f19aefcc50ce4b6aa
- config.json：2317 bytes；14b1b421a90349bc551b881461426b561a874049cb9e4c4864f2ca384f6a7cc5
- model.bin：75537502 bytes；1a5afae06a4db91c975c9a9d78be5cc110ee4ea022ad57d55492e4550e936b2a
- tokenizer.json：2128466 bytes；929c5252409436dce1b38a75d1abbcb5e132d170d8e324e4e04ed915fa2d22df
- vocabulary.txt：422309 bytes；ff77588746d3a2595d32ab5b69ffd7b95ce2441ac57533cb66fc3eb575a115cf

运行环境 Python3.12.14、faster-whisper1.2.1、av19.0.0；CPU/int8、2线程。模型只从本地路径加载，原 local_files_only、离线变量、最小子进程环境、超时终止与禁网络媒体解码保持不变。

## 复现与兼容性

```bash
export UV_CACHE_DIR=/workspace/shared/tools/uv-cache
export PYTHONPATH="$PWD/src"
export ZHIZHI_TEST_ASR_MODEL=/workspace/shared/knowledge-platform/asr-test/model
export ZHIZHI_TEST_ASR_SAMPLE=/workspace/shared/knowledge-platform/asr-test/synthetic-speech.wav
export ZHIZHI_TEST_ASR_EXPECTED='["quick brown fox","lazy dog","local speech recognition test"]'
export ZHIZHI_TEST_ASR_SHA256=ecc0b6057327c4e4ef89fd34e8555ea9ed6ca347666201c918f4fe1c72b9f6dd
uv run --no-project --python /workspace/shared/tools/knowledge-venv/bin/python -m pytest tests/knowledge_platform/intake -q --durations=4
```

工作目录为 projects/growth。模型未配置时两项真实测试明确skip；已配置时样本或hash问题不以skip掩盖。正常/恰好时长边界片段值不变；任一片段越界则整组有效segments为空，不静默裁剪。诊断放在 document.metadata.transcript.timing_diagnostics，包含 raw_model_segments、code=timestamp_unreliable、usable_for_location=false。timing_valid仅表示边界有效，不代表强制对齐精度。

新增Transcript末尾默认字段兼容旧调用；字幕没有可信解码时长，不新增肯定性验证。元数据无需数据库迁移，历史已收藏数据不回写。预览、source descriptor及持久化bundle一致保留文本/告警/诊断，真实样本原始字节与哈希亦已验证。body_complete=false、visual_coverage=not_processed、私有source及rights=unknown不改变。
