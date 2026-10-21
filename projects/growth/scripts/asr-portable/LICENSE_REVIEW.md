# ASR Windows 独立组件分发材料复核

2026-10-20。本报告是工程证据记录，不是GPL兼容性或法律合规结论。资源状态 `license_pending`；基础包可独立验收。不得把本组件算作已最终交付，也不得将静态PE检查当作Windows加载/识别通过。没有改变uv.lock、删除DLL、安装系统运行库或重编译第三方依赖。

## 已固定证据

- PyAV：`av-19.0.1-cp312-abi3-win_amd64.whl`，锁定SHA256 `906fc3db09288319a75ea23ffefb59961c7dbe0d1c074601507a89de7d8593d8`。官方构建资源映射：https://raw.githubusercontent.com/PyAV-Org/PyAV/v19.0.1/scripts/ffmpeg-latest.json ，指向pyav-ffmpeg `9.0.2-1`。
- 官方FFmpeg vendor包：https://github.com/PyAV-Org/pyav-ffmpeg/releases/download/9.0.2-1/ffmpeg-windows-x86_64.tar.gz ，30535039字节；GitHub官方asset digest与本轮下载SHA256一致：`add88a2e31f63be16f4ba671862770f7f9313de2f8d2967b1eade4c18bf4b093`。382个成员中未找到LICENSE/COPYING/NOTICE名字。
- 固定构建脚本、源包URL与SHA256：https://raw.githubusercontent.com/PyAV-Org/pyav-ffmpeg/9.0.2-1/scripts/pkg.py 及同版本 `scripts/build-ffmpeg.py`。保存原件供复核，不执行上游脚本。
- 固定patch：https://raw.githubusercontent.com/PyAV-Org/pyav-ffmpeg/9.0.2-1/patches/ffmpeg.patch ，SHA256 `661b7f0553ae145ce6526bb5514ed0f50a699e337dcc85c6d743809685f47020`。它将libx264/libx265从GPL列表移至version3列表。实际wheel avcodec DLL字符串含FFmpeg9.0.2、enable-libx264、enable-libx265及LGPL version3。不能据此简单宣称整个分发闭包为LGPL。
- 已按该脚本的hash下载FFmpeg9.0.2、x264 commit b35605ace3ddf7c1a5d67a2eb553f034aef41d55、x2654.3的完整源码，提取原版权/许可；两项codec COPYING均为GPLv2。另保留源码取得说明与原patch。FFmpeg官方说明：https://ffmpeg.org/legal.html 。最小待决事项：复核此具体构建的完整对应源码/codec条款与应用分发方式，不以PyAV BSD或修改后的标签代替。
- Intel OpenMP：官方PyPI `intel-openmp==2025.3.0` win_amd64 wheel逐项hash核验，内含`libiomp5md.dll`与CTranslate2 wheel中字节完全一致，DLL SHA256 `982233366b0afcda1e0f55a0b134097e35b779613f54ddb69e685e6cd06b755f`。已保留原`Intel End User License Agreement for Developer Tools (Version August 2024)`，不是套用无关简化许可。来源 https://pypi.org/project/intel-openmp/2025.3.0/ 。许可的再分发条款仍须作为整包复核的一部分。
- cuDNN：CTranslate2 v4.8.3固定Windows构建脚本 https://raw.githubusercontent.com/OpenNMT/CTranslate2/v4.8.3/python/tools/prepare_build_environment_windows.sh 指向cuDNN9.10.2及Intel oneAPI2025.3；wheel包含266288字节的cudnn64_9.dll。保留该DLL，不凭CPU路径不用就删除。已保存NVIDIA对应版本EULA、Acknowledgements与Notices：https://docs.nvidia.com/deeplearning/cudnn/backend/v9.10.2/reference/eula.html 。未下载/安装完整SDK，未声称CPU运行就可忽略发行条款。

## 必须区分的未完成事项

1. 分发许可复核：以上证据和对应源包/获取记录已分开保留，当前状态仍待复核。不要擅自升级/降级锁文件。若选替代官方wheel，须重新验证CP313/win_amd64 tag、传递闭包、模型推理及分发材料。
2. Windows实际验证：VC++ runtime缺失时健康检查BLOCKED，无自动安装；实际动态import、离线模型加载、合成样本推理尚未在Windows执行。
3. 产品真实E2E：tiny.en仅英文合成冒烟，不代表中文、自然人录音、真实平台抓取、完整产品流程完成。
