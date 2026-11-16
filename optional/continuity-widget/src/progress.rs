use crate::models::{
    MaturityStage, MilestoneStatus, ProgressState, Project, ProjectMilestone, ProjectProgress,
};

pub fn derive_progress(milestones: &[ProjectMilestone]) -> ProjectProgress {
    let active = milestones
        .iter()
        .filter(|milestone| milestone.status != MilestoneStatus::Cancelled)
        .collect::<Vec<_>>();
    if active.is_empty() {
        return ProjectProgress {
            state: ProgressState::Unplanned,
            percent: None,
            completed_weight: 0,
            total_weight: 0,
            completed_milestones: 0,
            total_milestones: 0,
        };
    }

    let total_weight = active.iter().map(|milestone| milestone.weight).sum::<i32>();
    let completed = active
        .iter()
        .filter(|milestone| milestone.status == MilestoneStatus::Completed)
        .collect::<Vec<_>>();
    let completed_weight = completed
        .iter()
        .map(|milestone| milestone.weight)
        .sum::<i32>();
    let percent = if total_weight == 0 {
        0
    } else {
        ((completed_weight as f64 / total_weight as f64) * 100.0).round() as i32
    };
    ProjectProgress {
        state: ProgressState::Planned,
        percent: Some(percent.clamp(0, 100)),
        completed_weight,
        total_weight,
        completed_milestones: completed.len() as i64,
        total_milestones: active.len() as i64,
    }
}

pub fn derive_maturity(
    project: &Project,
    milestones: &[ProjectMilestone],
    progress: &ProjectProgress,
    has_blocking_question: bool,
) -> MaturityStage {
    if project.maturity_manual_override {
        return project.maturity_stage;
    }

    let mut derived = MaturityStage::Idea;
    if !project.goal.trim().is_empty()
        && !project.success_criteria.trim().is_empty()
        && milestones
            .iter()
            .any(|milestone| milestone.status != MilestoneStatus::Cancelled)
    {
        derived = MaturityStage::Defined;
    }
    if milestones.iter().any(|milestone| {
        matches!(
            milestone.status,
            MilestoneStatus::InProgress | MilestoneStatus::Completed
        )
    }) {
        derived = MaturityStage::Executing;
    }
    if progress.percent == Some(100) && !has_blocking_question {
        derived = MaturityStage::Validating;
    }

    max_stage(project.maturity_stage, derived)
}

fn max_stage(left: MaturityStage, right: MaturityStage) -> MaturityStage {
    if stage_rank(right) > stage_rank(left) {
        right
    } else {
        left
    }
}

fn stage_rank(stage: MaturityStage) -> u8 {
    match stage {
        MaturityStage::Idea => 0,
        MaturityStage::Defined => 1,
        MaturityStage::Executing => 2,
        MaturityStage::Validating => 3,
        MaturityStage::Mature => 4,
    }
}
