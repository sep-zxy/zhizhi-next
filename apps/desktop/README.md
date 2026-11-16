# 知枝桌面宿主

Electron 启动包内 Python sidecar 并加载学习界面。保留 appId io.local.growthcompanion、growth-companion-desktop 用户目录与 ahadiff 技术入口。

开发目录读取 ../../backend/.venv 和 ../learning-web/dist；正式包使用 Resources/sidecar 与 Resources/viewer。只能在 GitHub 托管 runner 构建，Windows 与 macOS 打包由 build.yml 的 desktop 目标执行。

默认本地语法检查不启动 Electron，不做截图或视觉检查。未签名/临时签名包与真实安装验收须分别报告。见 [构建与测试](../../docs/testing.md)。
