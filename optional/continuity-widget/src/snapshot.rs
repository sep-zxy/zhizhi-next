use crate::{ContinuityService, models::*};
use anyhow::{Context, Result};
use chrono::Local;
use std::{
    fs,
    path::{Path, PathBuf},
};

pub fn export_snapshot(service: &ContinuityService, path: Option<&Path>) -> Result<SnapshotResult> {
    let revision = service.revision()?;
    let target = path
        .map(PathBuf::from)
        .unwrap_or_else(|| service.vault().join("output").join("continuity.md"));
    if let Ok(existing) = fs::read_to_string(&target)
        && existing.contains(&format!("generated_revision: {revision}"))
    {
        return Ok(SnapshotResult {
            path: target.display().to_string(),
            revision,
            changed: false,
        });
    }
    let editor = service.editor()?;
    let markdown = render_snapshot(&editor);
    if let Some(parent) = target.parent() {
        fs::create_dir_all(parent)?;
    }
    fs::write(&target, markdown).with_context(|| format!("写入快照失败：{}", target.display()))?;
    Ok(SnapshotResult {
        path: target.display().to_string(),
        revision,
        changed: true,
    })
}

pub fn render_snapshot(editor: &EditorView) -> String {
    let date = Local::now().format("%Y-%m-%d");
    let mut output = format!(
        "---\n\
title: 连续沉淀（只读快照）\n\
tags: [knowledge-management, workflow]\n\
created: {date}\n\
updated: {date}\n\
type: permanent\n\
publish: false\n\
summary: Continuity Widget SQLite 数据库导出的只读连续提醒快照。\n\
generated_revision: {}\n\
---\n\n\
# 连续沉淀（只读快照）\n\n\
> 本文件由 `continuity-cli.exe snapshot export` 生成。SQLite 是唯一实时数据源，请勿人工编辑。\n\n",
        editor.revision
    );

    for status in [
        ProjectStatus::Active,
        ProjectStatus::Paused,
        ProjectStatus::Candidate,
        ProjectStatus::Completed,
        ProjectStatus::Archived,
    ] {
        let projects = editor
            .projects
            .iter()
            .filter(|project| project.project.status == status)
            .collect::<Vec<_>>();
        if projects.is_empty() {
            continue;
        }
        output.push_str(&format!("## {}\n\n", status_label(status)));
        for project in projects {
            output.push_str(&format!("### {}\n\n", project.project.name));
            output.push_str(&format!("- 项目 ID：`{}`\n", project.project.id));
            if let Some(legacy_id) = &project.project.legacy_id {
                output.push_str(&format!("- Legacy ID：`{legacy_id}`\n"));
            }
            output.push_str(&format!("- 状态：`{}`\n", project.project.status));
            output.push_str(&format!(
                "- 成熟度：`{}`\n",
                maturity_label(project.project.maturity_stage)
            ));
            output.push_str(&format!("- 活跃度：{}\n", project.activity_score));
            output.push_str(&format!(
                "- 项目进度：{}（{}/{} 个里程碑，权重 {}/{}）\n",
                progress_label(&project.progress),
                project.progress.completed_milestones,
                project.progress.total_milestones,
                project.progress.completed_weight,
                project.progress.total_weight
            ));
            if !project.project.summary.is_empty() {
                output.push_str(&format!("- 摘要：{}\n", project.project.summary));
            }
            if !project.project.goal.is_empty() {
                output.push_str(&format!("- 目标：{}\n", project.project.goal));
            }
            if !project.project.success_criteria.is_empty() {
                output.push_str(&format!(
                    "- 成功标准：{}\n",
                    project.project.success_criteria
                ));
            }
            if let Some(path) = &project.project.project_path {
                output.push_str(&format!("- 项目路径：`{path}`\n"));
            }
            output.push('\n');
            if !project.milestones.is_empty() {
                output.push_str("#### 里程碑\n\n");
                for milestone in &project.milestones {
                    let checkbox = if milestone.status == MilestoneStatus::Completed {
                        "x"
                    } else {
                        " "
                    };
                    output.push_str(&format!(
                        "- [{checkbox}] [{} / 权重 {}] {}\n",
                        milestone.status, milestone.weight, milestone.title
                    ));
                    if !milestone.acceptance_criteria.is_empty() {
                        output.push_str(&format!(
                            "  - 完成条件：{}\n",
                            milestone.acceptance_criteria
                        ));
                    }
                }
                output.push('\n');
            }
            for horizon in [ItemHorizon::Daily, ItemHorizon::Weekly, ItemHorizon::Later] {
                let items = project
                    .items
                    .iter()
                    .filter(|item| item.item.horizon == horizon)
                    .collect::<Vec<_>>();
                if items.is_empty() {
                    continue;
                }
                output.push_str(&format!("#### {}\n\n", horizon_label(horizon)));
                for item in items {
                    let checkbox = match item.item.status {
                        ItemStatus::Done => "x",
                        _ => " ",
                    };
                    output.push_str(&format!(
                        "- [{checkbox}] [{} / {} / 活跃度 {}{}] {}\n",
                        kind_label(item.item.kind),
                        item.item.status,
                        item.item.activity_score,
                        if item.item.blocking { " / 阻塞" } else { "" },
                        item.item.title
                    ));
                    if !item.item.details.is_empty() {
                        output.push_str(&format!("  - 详情：{}\n", item.item.details));
                    }
                    if !item.evidence.is_empty() {
                        output.push_str("  - 来源：");
                        output.push_str(
                            &item
                                .evidence
                                .iter()
                                .map(|entry| format!("`{}`", entry.source_ref))
                                .collect::<Vec<_>>()
                                .join("、"),
                        );
                        output.push('\n');
                    }
                }
                output.push('\n');
            }
            let suggestions = project
                .items
                .iter()
                .flat_map(|item| &item.suggestions)
                .filter(|suggestion| suggestion.status == SuggestionStatus::Pending)
                .collect::<Vec<_>>();
            if !suggestions.is_empty() {
                output.push_str("#### 待处理建议\n\n");
                for suggestion in suggestions {
                    output.push_str(&format!(
                        "- `{}`：`{}`（来源 `{}`）\n",
                        suggestion.id, suggestion.kind, suggestion.source
                    ));
                }
                output.push('\n');
            }
        }
    }
    output
}

fn progress_label(progress: &ProjectProgress) -> String {
    progress
        .percent
        .map(|percent| format!("{percent}%"))
        .unwrap_or_else(|| "未规划".to_owned())
}

fn maturity_label(stage: MaturityStage) -> &'static str {
    match stage {
        MaturityStage::Idea => "想法",
        MaturityStage::Defined => "已定义",
        MaturityStage::Executing => "执行中",
        MaturityStage::Validating => "验证中",
        MaturityStage::Mature => "成熟",
    }
}

fn status_label(status: ProjectStatus) -> &'static str {
    match status {
        ProjectStatus::Candidate => "候选项目",
        ProjectStatus::Active => "活跃项目",
        ProjectStatus::Paused => "暂停项目",
        ProjectStatus::Completed => "已完成项目",
        ProjectStatus::Archived => "已归档项目",
    }
}

fn horizon_label(horizon: ItemHorizon) -> &'static str {
    match horizon {
        ItemHorizon::Daily => "今日",
        ItemHorizon::Weekly => "本周",
        ItemHorizon::Later => "稍后",
    }
}

fn kind_label(kind: ItemKind) -> &'static str {
    match kind {
        ItemKind::Question => "待确认",
        ItemKind::Action => "下一步",
    }
}
