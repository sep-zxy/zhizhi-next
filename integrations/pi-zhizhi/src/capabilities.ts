/** A factual local capability view, not a readiness probe or authorization grant. */
export function capabilitySummary(toolNames: readonly string[], humanCommandsAvailable: boolean, learningAvailable = false): string {
  return [
    "统一知识 CLI · 已注册能力（本地清单，不表示后端/账号已就绪）",
    "个人 Wiki：临时收件、检索、条目阅读、知识提案、重大决定队列、任务列表、状态与脱敏事件。",
    "知枝：已有关系与候选雷达查询；聚焦架构和技术栈，不推断掌握分。",
    `实际模型工具：${toolNames.join("、")}`,
    humanCommandsAvailable
      ? "本人 TUI 命令：/zhizhi-keep <intake_id>、/zhizhi-discard <intake_id>、/zhizhi-review <change_id>、/zhizhi-task-resume <task_id>、/zhizhi-task-cancel <task_id>。仍须有效身份及具体确认。"
      : "本人决定命令未注册：当前桥接槽没有独立本人身份。不会替你批准或创建身份。",
    learningAvailable ? "本人学习命令：/zhizhi-learn、/zhizhi-learn-open、/zhizhi-learn-note、/zhizhi-learn-submit。精确版本与原话确认，模型不能调用。" : "本人学习命令未启用：需明确配置同机 viewer 地址与既有独立身份；不会猜端口、读取密钥或复制领域令牌。",
    "尚未接入统一 CLI：指定历史版本、发布/恢复、动态工具安装。请使用已实现的相应业务页面；未提供的操作不能视为完成。",
    "kb … 是设计命令占位，不是已安装的 shell 命令。",
    "保留仅授权私有保存并进入后续处理；提案不等于 Writer 提交，公开意图不等于已发布。",
    "任务以服务端 task_id 回执为准；本清单不调用模型、不检查账号、不写知识。",
  ].join("\n");
}
