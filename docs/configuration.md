# 配置参考

config/toolchains.json 是精确工具链登记来源；包清单保留各自兼容范围和安装工具声明，CI 检查一致性。示例位于 config/examples，校验规则位于 config/schemas，个人开发配置放 Git 忽略的 .local/config。

业务配置由所属模块解释。统一命令不引入一层任意字段合并：明确支持的 CLI 参数覆盖显式选择的文件，其余使用安全默认值。环境变量仅使用已声明用途，不把客户端环境当作权限来源。

平台启动仍使用 --state-directory、--identities-file、--model-config、--channel-config；桌面统一模式保留 AHADIFF_KNOWLEDGE_CONFIG。model 与 channel 配置使用所属现有格式，配置示例不自动注册身份、调用模型或连接生产服务。

相对文件路径以配置文件目录为基准，组件只接受其明确支持的参数。身份和密钥引用必须由受保护文件或已授权秘密服务提供，不写进 URL、CLI 口令、前端构建变量、日志、Git 或示例。

桌面原有 growth-companion-desktop 与 Continuity 数据目录保持不变。目录迁移不会复制、初始化或变更用户已有 Vault/数据库。便携包继续由其自身 launcher 配置控制，历史 owner 验收槽不是新仓库的默认授权入口。

模型、渠道、自动公开发布默认关闭。缺少必要配置明确 waiting/blocked，不静默选择其他 provider、身份或收费回退。

platform.example.json 对应便携包 config/launch.json，不是平台 CLI 的 --config 文件。服务部署使用上列现有显式参数；示例不会自行初始化身份或读取个人配置。
