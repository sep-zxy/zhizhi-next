use crate::{
    DEFAULT_VAULT, Database,
    database::SCHEMA_VERSION,
    events::{canonical_payload_hash, validate_event_identity},
    migration::{backup_database, import_legacy_if_needed},
    models::*,
    progress::{derive_maturity, derive_progress},
    repair::{load_canonical_legacy, repairable_change},
    scoring::calculate_activity,
};
use anyhow::{Context, Result, anyhow, bail};
use chrono::{DateTime, Duration, Local, Utc};
use rusqlite::{Connection, OptionalExtension, Row, params};
use serde::{Deserialize, Serialize};
use serde_json::{Value, json};
use std::{
    collections::HashSet,
    path::{Path, PathBuf},
    str::FromStr,
};
use uuid::Uuid;

const PROJECT_COLUMNS: &str = "id, legacy_id, name, status, summary, goal, success_criteria, maturity_stage, maturity_manual_override, project_path, threshold_override, created_at, updated_at, revision";
const ITEM_COLUMNS: &str = "id, project_id, milestone_id, fingerprint, title, details, kind, horizon, status, blocking, priority, activity_score, last_evidence_at, manual_override, pinned, auto_daily, sort_order, closed_at, revision";
const JOINED_ITEM_COLUMNS: &str = "i.id, i.project_id, i.milestone_id, i.fingerprint, i.title, i.details, i.kind, i.horizon, i.status, i.blocking, i.priority, i.activity_score, i.last_evidence_at, i.manual_override, i.pinned, i.auto_daily, i.sort_order, i.closed_at, i.revision";
const MILESTONE_COLUMNS: &str = "id, project_id, fingerprint, title, acceptance_criteria, status, weight, manual_override, sort_order, created_at, updated_at, completed_at, revision";
const SUGGESTION_COLUMNS: &str = "id, project_id, item_id, milestone_id, kind, source, event_id, proposed_json, status, created_at, resolved_at, revision";

#[derive(Debug, Clone)]
pub struct ContinuityService {
    database: Database,
    vault: PathBuf,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
struct BackupState {
    projects: Vec<Project>,
    items: Vec<Item>,
    evidence: Vec<Evidence>,
    suggestions: Vec<ItemSuggestion>,
    history: Vec<ProjectHistory>,
    #[serde(default)]
    milestones: Vec<ProjectMilestone>,
}

impl ContinuityService {
    pub fn open_default() -> Result<Self> {
        let opened = Database::open_default()?;
        let vault = std::env::var_os("CONTINUITY_VAULT")
            .map(PathBuf::from)
            .unwrap_or_else(|| PathBuf::from(DEFAULT_VAULT));
        import_legacy_if_needed(&opened.database, &vault, opened.created)?;
        Ok(Self {
            database: opened.database,
            vault,
        })
    }

    pub fn open(database_path: impl Into<PathBuf>, vault: impl Into<PathBuf>) -> Result<Self> {
        let opened = Database::open(database_path)?;
        let vault = vault.into();
        import_legacy_if_needed(&opened.database, &vault, opened.created)?;
        Ok(Self {
            database: opened.database,
            vault,
        })
    }

    pub fn database(&self) -> &Database {
        &self.database
    }

    pub fn vault(&self) -> &Path {
        &self.vault
    }

    pub fn revision(&self) -> Result<i64> {
        self.database.revision()
    }

    pub fn dashboard(&self) -> Result<DashboardView> {
        let connection = self.database.connection()?;
        let revision = self.database.revision()?;
        let settings = load_settings(&connection)?;
        let now = query_item_views(
            &connection,
            &format!(
                "SELECT {JOINED_ITEM_COLUMNS} FROM items i
                 JOIN projects p ON p.id = i.project_id
                 WHERE p.status IN ('active','paused') AND i.status = 'open'
                   AND (i.pinned = 1 OR (i.kind = 'action' AND i.horizon = 'daily'
                     AND i.activity_score >= COALESCE(p.threshold_override, ?1)))
                 ORDER BY i.pinned DESC, i.blocking DESC, i.priority DESC, i.activity_score DESC,
                   i.last_evidence_at DESC, i.id ASC LIMIT ?2"
            ),
            params![settings.daily_threshold, settings.now_limit],
        )?;

        let project_rows = query_all(
            &connection,
            &format!(
                "SELECT {PROJECT_COLUMNS} FROM projects
                 WHERE status IN ('active','paused','candidate')
                 ORDER BY CASE status WHEN 'active' THEN 0 WHEN 'paused' THEN 1 ELSE 2 END,
                   updated_at DESC, id ASC"
            ),
            row_project,
        )?;
        let projects = project_rows
            .into_iter()
            .map(|project| load_project_dashboard(&connection, project))
            .collect::<Result<Vec<_>>>()?;

        let mut daily = Vec::new();
        for project in projects
            .iter()
            .filter(|entry| entry.project.status == ProjectStatus::Active)
        {
            let items = project
                .next_items
                .iter()
                .filter(|item| {
                    item.item.horizon == ItemHorizon::Daily
                        && item.item.activity_score
                            >= project
                                .project
                                .threshold_override
                                .unwrap_or(settings.daily_threshold)
                })
                .cloned()
                .collect::<Vec<_>>();
            if !items.is_empty() {
                daily.push(DailyProjectView {
                    project: project.project.clone(),
                    activity_score: items
                        .iter()
                        .map(|item| item.item.activity_score)
                        .max()
                        .unwrap_or(0),
                    items,
                });
            }
        }
        let weekly = projects
            .iter()
            .filter(|entry| {
                matches!(
                    entry.project.status,
                    ProjectStatus::Active | ProjectStatus::Paused
                )
            })
            .map(|entry| WeeklyProjectView {
                project: entry.project.clone(),
                activity_score: entry
                    .next_items
                    .iter()
                    .map(|item| item.item.activity_score)
                    .max()
                    .unwrap_or(0),
                summary: if entry.project.goal.trim().is_empty() {
                    entry.project.summary.clone()
                } else {
                    entry.project.goal.clone()
                },
            })
            .collect::<Vec<_>>();
        let candidate_count = projects
            .iter()
            .filter(|entry| entry.project.status == ProjectStatus::Candidate)
            .count() as i64;

        Ok(DashboardView {
            revision,
            settings,
            projects,
            now,
            daily,
            weekly,
            candidate_count,
        })
    }

    pub fn editor(&self) -> Result<EditorView> {
        let connection = self.database.connection()?;
        let projects = query_all(
            &connection,
            &format!(
                "SELECT {PROJECT_COLUMNS} FROM projects
                 ORDER BY CASE status WHEN 'active' THEN 0 WHEN 'paused' THEN 1 WHEN 'candidate' THEN 2 WHEN 'completed' THEN 3 ELSE 4 END,
                   updated_at DESC, id ASC"
            ),
            row_project,
        )?
        .into_iter()
        .map(|project| load_project_view(&connection, project))
        .collect::<Result<Vec<_>>>()?;
        Ok(EditorView {
            revision: self.database.revision()?,
            settings: load_settings(&connection)?,
            projects,
        })
    }

    pub fn project_preview(&self, project_id: &str) -> Result<ProjectPreview> {
        let connection = self.database.connection()?;
        let project = load_project(&connection, project_id)?
            .ok_or_else(|| anyhow!("not_found:project:{project_id}"))?;
        Ok(ProjectPreview {
            revision: self.database.revision()?,
            project: load_project_view(&connection, project)?,
        })
    }

    pub fn project_progress(&self, project_id: &str) -> Result<ProjectProgress> {
        let connection = self.database.connection()?;
        if load_project(&connection, project_id)?.is_none() {
            bail!("not_found:project:{project_id}");
        }
        Ok(derive_progress(&load_milestones(&connection, project_id)?))
    }

    pub fn list_items(&self, project_id: &str) -> Result<Vec<ItemView>> {
        let connection = self.database.connection()?;
        query_item_views(
            &connection,
            &format!(
                "SELECT {ITEM_COLUMNS} FROM items WHERE project_id = ?1
                 ORDER BY CASE status WHEN 'open' THEN 0 WHEN 'done' THEN 1 ELSE 2 END,
                   blocking DESC, kind DESC, priority DESC, sort_order ASC, id ASC"
            ),
            [project_id],
        )
    }

    pub fn project_upsert(&self, input: ProjectUpsertInput) -> Result<MutationResult<Project>> {
        validate_project_ref(&input.project)?;
        validate_threshold(input.threshold_override)?;
        validate_project_path_option(input.project_path.as_deref(), None, &self.database)?;
        let automatic = input
            .batch
            .as_ref()
            .is_some_and(|batch| batch.kind == "automatic");
        let (revision, project) = self.database.with_write_transaction(
            input.expected_revision,
            |transaction, revision| {
                record_batch_if_needed(transaction, input.batch.as_ref())?;
                let mut project =
                    resolve_or_create_project(transaction, &input.project, automatic, revision)?;
                if !automatic || project.summary.is_empty() {
                    if !input.summary.trim().is_empty() {
                        project.summary = input.summary.trim().to_owned();
                    }
                }
                if !automatic || project.goal.is_empty() {
                    if !input.goal.trim().is_empty() {
                        project.goal = input.goal.trim().to_owned();
                    }
                }
                if !automatic || project.success_criteria.is_empty() {
                    if !input.success_criteria.trim().is_empty() {
                        project.success_criteria = input.success_criteria.trim().to_owned();
                    }
                }
                if !automatic || project.project_path.is_none() {
                    if input.project_path.is_some() {
                        project.project_path =
                            normalize_project_path(input.project_path.as_deref())?;
                    }
                }
                if input.threshold_override.is_some() {
                    project.threshold_override = input.threshold_override;
                }
                project.updated_at = Local::now().to_rfc3339();
                project.revision = revision;
                persist_project(transaction, &project)?;
                reconcile_project(transaction, &project.id, revision)?;
                record_project_history(transaction, &project.id, revision, "project_upsert")?;
                load_project(transaction, &project.id)?
                    .ok_or_else(|| anyhow!("not_found:project:{}", project.id))
            },
        )?;
        Ok(mutation(revision, project))
    }

    pub fn confirm_project(&self, input: EntityCommandInput) -> Result<MutationResult<Project>> {
        let (revision, project) = self.database.with_write_transaction(
            input.expected_revision,
            |transaction, revision| {
                let mut project = load_project(transaction, &input.id)?
                    .ok_or_else(|| anyhow!("not_found:project:{}", input.id))?;
                project.status = ProjectStatus::Active;
                project.updated_at = Local::now().to_rfc3339();
                project.revision = revision;
                persist_project(transaction, &project)?;
                record_project_history(transaction, &project.id, revision, "project_confirm")?;
                Ok(project)
            },
        )?;
        Ok(mutation(revision, project))
    }

    pub fn save_project(&self, input: ProjectPatchInput) -> Result<MutationResult<Project>> {
        if input
            .name
            .as_ref()
            .is_some_and(|name| name.trim().is_empty())
        {
            bail!("validation_error:项目名不能为空");
        }
        if let Some(Some(path)) = &input.project_path {
            validate_project_path_option(Some(path), Some(&input.id), &self.database)?;
        }
        if let Some(Some(threshold)) = input.threshold_override {
            validate_threshold(Some(threshold))?;
        }
        let (revision, project) = self.database.with_write_transaction(
            input.expected_revision,
            |transaction, revision| {
                let mut project = load_project(transaction, &input.id)?
                    .ok_or_else(|| anyhow!("not_found:project:{}", input.id))?;
                if let Some(name) = &input.name {
                    project.name = name.trim().to_owned();
                }
                if let Some(summary) = &input.summary {
                    project.summary = summary.trim().to_owned();
                }
                if let Some(goal) = &input.goal {
                    project.goal = goal.trim().to_owned();
                }
                if let Some(criteria) = &input.success_criteria {
                    project.success_criteria = criteria.trim().to_owned();
                }
                if let Some(stage) = input.maturity_stage {
                    project.maturity_stage = stage;
                    project.maturity_manual_override = true;
                }
                if let Some(status) = input.status {
                    project.status = status;
                }
                if let Some(path) = &input.project_path {
                    project.project_path = match path {
                        Some(value) => normalize_project_path(Some(value))?,
                        None => None,
                    };
                }
                if let Some(threshold) = input.threshold_override {
                    project.threshold_override = threshold;
                }
                project.updated_at = Local::now().to_rfc3339();
                project.revision = revision;
                persist_project(transaction, &project)?;
                reconcile_project(transaction, &project.id, revision)?;
                record_project_history(transaction, &project.id, revision, "project_manual")?;
                load_project(transaction, &project.id)?
                    .ok_or_else(|| anyhow!("not_found:project:{}", project.id))
            },
        )?;
        Ok(mutation(revision, project))
    }

    pub fn item_upsert(&self, input: ItemUpsertInput) -> Result<MutationResult<Item>> {
        validate_project_ref(&input.project)?;
        validate_item_input(&input.item)?;
        let automatic = input
            .batch
            .as_ref()
            .is_some_and(|batch| batch.kind == "automatic");
        let (revision, (item, duplicates)) = self.database.with_write_transaction(
            input.expected_revision,
            |transaction, revision| {
                record_batch_if_needed(transaction, input.batch.as_ref())?;
                let project =
                    resolve_or_create_project(transaction, &input.project, automatic, revision)?;
                validate_milestone_project(
                    transaction,
                    input.item.milestone_id.as_deref(),
                    &project.id,
                )?;
                let existing = find_item(transaction, &project.id, &input.item)?;
                let duplicates = find_possible_duplicates(
                    transaction,
                    &project.id,
                    &input.item.title,
                    existing.as_ref().map(|item| item.id.as_str()),
                )?;
                let item = if let Some(mut item) = existing {
                    if automatic
                        && (item.manual_override
                            || item.pinned
                            || (!item.auto_daily && item.horizon == ItemHorizon::Daily))
                    {
                        insert_suggestion(
                            transaction,
                            &project.id,
                            Some(&item.id),
                            input.item.milestone_id.as_deref(),
                            SuggestionKind::ItemPatch,
                            input
                                .batch
                                .as_ref()
                                .map(|batch| batch.id.as_str())
                                .unwrap_or("automatic"),
                            None,
                            &serde_json::to_value(&input.item)?,
                            revision,
                        )?;
                    } else {
                        apply_item_input(&mut item, &input.item, automatic, revision);
                        persist_item(transaction, &item)?;
                    }
                    item
                } else {
                    let item = item_from_input(&project.id, &input.item, automatic, revision);
                    insert_item(transaction, &item)?;
                    item
                };
                if let Some(source) = &input.item.source {
                    append_evidence(transaction, &item.id, source, None)?;
                }
                let item = refresh_item_activity(transaction, &item.id, revision)?;
                reconcile_project(transaction, &project.id, revision)?;
                record_project_history(transaction, &project.id, revision, "item_upsert")?;
                Ok((item, duplicates))
            },
        )?;
        Ok(MutationResult {
            revision,
            value: item,
            possible_duplicates: duplicates,
        })
    }

    pub fn summarize(&self, input: SummarizeInput) -> Result<MutationResult<Project>> {
        validate_project_ref(&input.project.project)?;
        for item in &input.items {
            validate_item_input(item)?;
        }
        let expected = input.project.expected_revision;
        let batch = input.project.batch.clone();
        let (revision, project) = self.database.with_write_transaction(expected, |transaction, revision| {
            record_batch_if_needed(transaction, batch.as_ref())?;
            let mut project = resolve_or_create_project(transaction, &input.project.project, true, revision)?;
            if project.summary.is_empty() && !input.project.summary.trim().is_empty() {
                project.summary = input.project.summary.trim().to_owned();
            }
            if project.goal.is_empty() && !input.project.goal.trim().is_empty() {
                project.goal = input.project.goal.trim().to_owned();
            }
            if project.success_criteria.is_empty() && !input.project.success_criteria.trim().is_empty() {
                project.success_criteria = input.project.success_criteria.trim().to_owned();
            }
            project.updated_at = Local::now().to_rfc3339();
            project.revision = revision;
            persist_project(transaction, &project)?;

            let mut seen = HashSet::new();
            for item_input in &input.items {
                seen.insert(item_input.fingerprint.clone());
                validate_milestone_project(transaction, item_input.milestone_id.as_deref(), &project.id)?;
                let existing = find_item(transaction, &project.id, item_input)?;
                let item = if let Some(mut item) = existing {
                    if item.manual_override || item.pinned || (!item.auto_daily && item.horizon == ItemHorizon::Daily) {
                        insert_suggestion(
                            transaction,
                            &project.id,
                            Some(&item.id),
                            item_input.milestone_id.as_deref(),
                            SuggestionKind::ItemPatch,
                            batch.as_ref().map(|value| value.id.as_str()).unwrap_or("summarize"),
                            None,
                            &serde_json::to_value(item_input)?,
                            revision,
                        )?;
                    } else {
                        apply_item_input(&mut item, item_input, true, revision);
                        persist_item(transaction, &item)?;
                    }
                    item
                } else {
                    let item = item_from_input(&project.id, item_input, true, revision);
                    insert_item(transaction, &item)?;
                    item
                };
                if let Some(source) = &item_input.source {
                    append_evidence(transaction, &item.id, source, None)?;
                }
                refresh_item_activity(transaction, &item.id, revision)?;
            }
            let stale = query_all_with(
                transaction,
                "SELECT id, fingerprint FROM items WHERE project_id = ?1 AND status = 'open' AND auto_daily = 1",
                [&project.id],
                |row| Ok((row.get::<_, String>(0)?, row.get::<_, String>(1)?)),
            )?;
            for (id, fingerprint) in stale {
                if !seen.contains(&fingerprint) {
                    transaction.execute(
                        "UPDATE items SET horizon = 'weekly', auto_daily = 0, revision = ?2 WHERE id = ?1",
                        params![id, revision],
                    )?;
                }
            }
            reconcile_project(transaction, &project.id, revision)?;
            record_project_history(transaction, &project.id, revision, "project_summarize")?;
            load_project(transaction, &project.id)?.ok_or_else(|| anyhow!("not_found:project:{}", project.id))
        })?;
        Ok(mutation(revision, project))
    }

    pub fn save_item(&self, input: ItemPatchInput) -> Result<MutationResult<Item>> {
        if input
            .title
            .as_ref()
            .is_some_and(|title| title.trim().is_empty())
        {
            bail!("validation_error:事项标题不能为空");
        }
        if input
            .priority
            .is_some_and(|priority| !(0..=20).contains(&priority))
        {
            bail!("validation_error:优先级只能是 0–20");
        }
        let (revision, item) = self.database.with_write_transaction(
            input.expected_revision,
            |transaction, revision| {
                let mut item = load_item(transaction, &input.id)?
                    .ok_or_else(|| anyhow!("not_found:item:{}", input.id))?;
                if let Some(Some(milestone_id)) = &input.milestone_id {
                    validate_milestone_project(transaction, Some(milestone_id), &item.project_id)?;
                }
                if let Some(title) = &input.title {
                    item.title = title.trim().to_owned();
                }
                if let Some(details) = &input.details {
                    item.details = details.trim().to_owned();
                }
                if let Some(kind) = input.kind {
                    item.kind = kind;
                }
                if let Some(horizon) = input.horizon {
                    item.horizon = horizon;
                    item.auto_daily = false;
                }
                if let Some(status) = input.status {
                    item.status = status;
                    item.closed_at = if status == ItemStatus::Open {
                        None
                    } else {
                        Some(Local::now().to_rfc3339())
                    };
                }
                if let Some(priority) = input.priority {
                    item.priority = priority;
                }
                if let Some(pinned) = input.pinned {
                    item.pinned = pinned;
                }
                if let Some(milestone_id) = &input.milestone_id {
                    item.milestone_id = milestone_id.clone();
                }
                if let Some(blocking) = input.blocking {
                    item.blocking = blocking;
                }
                if let Some(sort_order) = input.sort_order {
                    item.sort_order = sort_order;
                }
                item.manual_override = true;
                item.revision = revision;
                persist_item(transaction, &item)?;
                let item = refresh_item_activity(transaction, &item.id, revision)?;
                reconcile_project(transaction, &item.project_id, revision)?;
                record_project_history(transaction, &item.project_id, revision, "item_manual")?;
                Ok(item)
            },
        )?;
        Ok(mutation(revision, item))
    }

    pub fn complete_item(&self, input: EntityCommandInput) -> Result<MutationResult<Item>> {
        self.save_item(ItemPatchInput {
            id: input.id,
            title: None,
            details: None,
            kind: None,
            horizon: None,
            status: Some(ItemStatus::Done),
            priority: None,
            pinned: None,
            milestone_id: None,
            blocking: None,
            sort_order: None,
            expected_revision: input.expected_revision,
        })
    }

    pub fn quick_add(&self, input: QuickAddInput) -> Result<MutationResult<Item>> {
        if input.title.trim().is_empty() {
            bail!("validation_error:事项标题不能为空");
        }
        let (revision, item) = self.database.with_write_transaction(
            input.expected_revision,
            |transaction, revision| {
                let current = match &input.project_id {
                    Some(id) => load_project(transaction, id)?,
                    None => top_active_project(transaction)?,
                };
                let (project, pinned) = if let Some(project) = current {
                    (project, false)
                } else {
                    let reference = ProjectRefInput {
                        id: None,
                        legacy_id: Some("quick-inbox".to_owned()),
                        name: "临时收件箱".to_owned(),
                    };
                    (
                        resolve_or_create_project(transaction, &reference, false, revision)?,
                        true,
                    )
                };
                let item = Item {
                    id: Uuid::new_v4().to_string(),
                    project_id: project.id.clone(),
                    milestone_id: None,
                    fingerprint: format!("manual-{}", Uuid::new_v4()),
                    title: input.title.trim().to_owned(),
                    details: String::new(),
                    kind: ItemKind::Action,
                    horizon: ItemHorizon::Daily,
                    status: ItemStatus::Open,
                    blocking: false,
                    priority: 10,
                    activity_score: 10,
                    last_evidence_at: None,
                    manual_override: true,
                    pinned,
                    auto_daily: false,
                    sort_order: 0,
                    closed_at: None,
                    revision,
                };
                insert_item(transaction, &item)?;
                reconcile_project(transaction, &project.id, revision)?;
                record_project_history(transaction, &project.id, revision, "quick_add")?;
                Ok(item)
            },
        )?;
        Ok(mutation(revision, item))
    }

    pub fn milestone_upsert(
        &self,
        input: MilestoneUpsertInput,
    ) -> Result<MutationResult<ProjectMilestone>> {
        validate_milestone_input(&input.title, input.weight)?;
        let (revision, milestone) = self.database.with_write_transaction(
            input.expected_revision,
            |transaction, revision| {
                let project = load_project(transaction, &input.project_id)?
                    .ok_or_else(|| anyhow!("not_found:project:{}", input.project_id))?;
                let existing = if let Some(id) = input.id.as_deref() {
                    load_milestone(transaction, id)?
                } else {
                    transaction
                        .query_row(
                            &format!("SELECT {MILESTONE_COLUMNS} FROM project_milestones WHERE project_id = ?1 AND fingerprint = ?2"),
                            params![project.id, input.fingerprint],
                            row_milestone,
                        )
                        .optional()?
                };
                let now = Local::now().to_rfc3339();
                let milestone = if let Some(mut milestone) = existing {
                    if milestone.project_id != project.id {
                        bail!("validation_error:里程碑不属于指定项目");
                    }
                    milestone.title = input.title.trim().to_owned();
                    milestone.acceptance_criteria = input.acceptance_criteria.trim().to_owned();
                    milestone.status = input.status;
                    milestone.weight = input.weight;
                    milestone.sort_order = input.sort_order;
                    milestone.updated_at = now.clone();
                    milestone.completed_at = if milestone.status == MilestoneStatus::Completed {
                        Some(now)
                    } else {
                        None
                    };
                    milestone.revision = revision;
                    persist_milestone(transaction, &milestone)?;
                    milestone
                } else {
                    let milestone = ProjectMilestone {
                        id: input.id.clone().unwrap_or_else(|| Uuid::new_v4().to_string()),
                        project_id: project.id.clone(),
                        fingerprint: input.fingerprint.trim().to_owned(),
                        title: input.title.trim().to_owned(),
                        acceptance_criteria: input.acceptance_criteria.trim().to_owned(),
                        status: input.status,
                        weight: input.weight,
                        manual_override: false,
                        sort_order: input.sort_order,
                        created_at: now.clone(),
                        updated_at: now.clone(),
                        completed_at: if input.status == MilestoneStatus::Completed { Some(now) } else { None },
                        revision,
                    };
                    insert_milestone(transaction, &milestone)?;
                    milestone
                };
                reconcile_project(transaction, &project.id, revision)?;
                record_project_history(transaction, &project.id, revision, "milestone_upsert")?;
                Ok(milestone)
            },
        )?;
        Ok(mutation(revision, milestone))
    }

    pub fn save_milestone(
        &self,
        input: MilestonePatchInput,
    ) -> Result<MutationResult<ProjectMilestone>> {
        if let Some(title) = &input.title
            && title.trim().is_empty()
        {
            bail!("validation_error:里程碑标题不能为空");
        }
        if input
            .weight
            .is_some_and(|weight| !(1..=100).contains(&weight))
        {
            bail!("validation_error:里程碑权重只能是 1–100");
        }
        let (revision, milestone) = self.database.with_write_transaction(
            input.expected_revision,
            |transaction, revision| {
                let mut milestone = load_milestone(transaction, &input.id)?
                    .ok_or_else(|| anyhow!("not_found:milestone:{}", input.id))?;
                if let Some(title) = &input.title {
                    milestone.title = title.trim().to_owned();
                }
                if let Some(criteria) = &input.acceptance_criteria {
                    milestone.acceptance_criteria = criteria.trim().to_owned();
                }
                if let Some(status) = input.status {
                    milestone.status = status;
                    milestone.manual_override = true;
                    milestone.completed_at = if status == MilestoneStatus::Completed {
                        Some(Local::now().to_rfc3339())
                    } else {
                        None
                    };
                }
                if let Some(weight) = input.weight {
                    milestone.weight = weight;
                }
                if let Some(sort_order) = input.sort_order {
                    milestone.sort_order = sort_order;
                }
                milestone.updated_at = Local::now().to_rfc3339();
                milestone.revision = revision;
                persist_milestone(transaction, &milestone)?;
                reconcile_project(transaction, &milestone.project_id, revision)?;
                record_project_history(
                    transaction,
                    &milestone.project_id,
                    revision,
                    "milestone_manual",
                )?;
                Ok(milestone)
            },
        )?;
        Ok(mutation(revision, milestone))
    }

    pub fn save_settings(&self, input: SettingsPatchInput) -> Result<MutationResult<Settings>> {
        if input
            .daily_threshold
            .is_some_and(|value| !(0..=100).contains(&value))
        {
            bail!("validation_error:每日阈值只能是 0–100");
        }
        if input
            .now_limit
            .is_some_and(|value| !(1..=5).contains(&value))
        {
            bail!("validation_error:现在做数量只能是 1–5");
        }
        let (revision, settings) =
            self.database
                .with_write_transaction(input.expected_revision, |transaction, _| {
                    let mut settings = load_settings(transaction)?;
                    if let Some(value) = input.daily_threshold {
                        settings.daily_threshold = value;
                    }
                    if let Some(value) = input.now_limit {
                        settings.now_limit = value;
                    }
                    if let Some(value) = input.always_on_top {
                        settings.always_on_top = value;
                    }
                    if let Some(value) = input.auto_snap {
                        settings.auto_snap = value;
                    }
                    if let Some(value) = input.autostart {
                        settings.autostart = value;
                    }
                    transaction.execute(
                    "UPDATE settings SET daily_threshold = ?1, now_limit = ?2, poll_seconds = 1,
                     always_on_top = ?3, auto_snap = ?4, autostart = ?5 WHERE id = 1",
                    params![
                        settings.daily_threshold,
                        settings.now_limit,
                        settings.always_on_top,
                        settings.auto_snap,
                        settings.autostart
                    ],
                )?;
                    Ok(settings)
                })?;
        Ok(mutation(revision, settings))
    }

    pub fn save_window_state(&self, state: WindowState) -> Result<()> {
        if self.load_window_state(&state.label)?.as_ref() == Some(&state) {
            return Ok(());
        }
        self.database.with_transaction_without_revision(|transaction| {
            transaction.execute(
                "INSERT INTO window_state(label, x, y, width, height, snap_edge, height_mode, monitor_name)
                 VALUES (?1, ?2, ?3, ?4, ?5, ?6, ?7, ?8)
                 ON CONFLICT(label) DO UPDATE SET x = excluded.x, y = excluded.y,
                   width = excluded.width, height = excluded.height, snap_edge = excluded.snap_edge,
                   height_mode = excluded.height_mode, monitor_name = excluded.monitor_name",
                params![state.label, state.x, state.y, state.width, state.height, state.snap_edge, state.height_mode, state.monitor_name],
            )?;
            Ok(())
        })
    }

    pub fn load_window_state(&self, label: &str) -> Result<Option<WindowState>> {
        let connection = self.database.connection()?;
        Ok(connection
            .query_row(
                "SELECT label, x, y, width, height, snap_edge, height_mode, monitor_name FROM window_state WHERE label = ?1",
                [label],
                |row| Ok(WindowState {
                    label: row.get(0)?, x: row.get(1)?, y: row.get(2)?, width: row.get(3)?,
                    height: row.get(4)?, snap_edge: row.get(5)?, height_mode: row.get(6)?, monitor_name: row.get(7)?,
                }),
            )
            .optional()?)
    }

    pub fn maintenance(&self) -> Result<i64> {
        let cutoff = (Utc::now() - Duration::days(90)).to_rfc3339();
        let (revision, ()) = self
            .database
            .with_write_transaction(None, |transaction, _| {
                transaction.execute(
                    "DELETE FROM items WHERE status IN ('done','dismissed') AND closed_at < ?1",
                    [&cutoff],
                )?;
                Ok(())
            })?;
        Ok(revision)
    }

    pub fn undo_last_automatic(&self) -> Result<i64> {
        let (revision, ()) = self.database.with_write_transaction(None, |transaction, revision| {
            let batch = transaction
                .query_row(
                    "SELECT id, before_json FROM update_batches WHERE kind = 'automatic' AND reverted_at IS NULL ORDER BY created_at DESC LIMIT 1",
                    [],
                    |row| Ok((row.get::<_, String>(0)?, row.get::<_, String>(1)?)),
                )
                .optional()?
                .ok_or_else(|| anyhow!("not_found:batch:automatic"))?;
            let state: BackupState = serde_json::from_str(&batch.1)?;
            transaction.execute("DELETE FROM projects", [])?;
            for project in &state.projects { insert_project_exact(transaction, project)?; }
            for milestone in &state.milestones { insert_milestone(transaction, milestone)?; }
            for item in &state.items { insert_item(transaction, item)?; }
            for evidence in &state.evidence { insert_evidence_exact(transaction, evidence)?; }
            for suggestion in &state.suggestions { insert_suggestion_exact(transaction, suggestion)?; }
            for history in &state.history { insert_history_exact(transaction, history)?; }
            transaction.execute(
                "UPDATE update_batches SET reverted_at = ?2 WHERE id = ?1",
                params![batch.0, Local::now().to_rfc3339()],
            )?;
            for project in &state.projects {
                record_project_history(transaction, &project.id, revision, "batch_undo")?;
            }
            Ok(())
        })?;
        Ok(revision)
    }

    pub fn apply_event(&self, input: ApplyEventInput) -> Result<MutationResult<EventApplyResult>> {
        validate_event_identity(&input.source, &input.event_id, input.version)?;
        let payload_hash = canonical_payload_hash(&input)?;
        if let Some((existing_hash, result)) =
            self.load_event_result(&input.source, &input.event_id)?
        {
            if existing_hash != payload_hash {
                bail!("event_conflict:{}:{}", input.source, input.event_id);
            }
            let mut result = result;
            result.replayed = true;
            return Ok(mutation(self.revision()?, result));
        }

        let source = input.source.clone();
        let event_id = input.event_id.clone();
        let event_type = input.event_type.clone();
        let occurred_at = input.occurred_at.clone();
        let payload_json = serde_json::to_string(&input)?;
        let (revision, result) = self.database.with_write_transaction(
            input.expected_revision,
            |transaction, revision| {
                let project = resolve_event_project(transaction, &input.project)?;
                let item = resolve_event_item(transaction, &project.id, &input.target)?;
                let milestone = match input.target.milestone_id.as_deref() {
                    Some(id) => load_milestone(transaction, id)?,
                    None => item
                        .as_ref()
                        .and_then(|item| item.milestone_id.as_deref())
                        .map(|id| load_milestone(transaction, id))
                        .transpose()?
                        .flatten(),
                };
                if milestone.as_ref().is_some_and(|value| value.project_id != project.id) {
                    bail!("target_conflict:milestone");
                }
                let before = json!({"item": item, "milestone": milestone});
                let mut disposition = EventDisposition::EvidenceOnly;
                let mut suggestion_id = None;
                let mut item_id = None;

                if let Some(mut item) = item {
                    item_id = Some(item.id.clone());
                    append_event_evidence(transaction, &item.id, &input)?;
                    if input.outcome == EventOutcome::Success && input.event_type == "work_item.completed" {
                        if item.kind == ItemKind::Action
                            && item.status == ItemStatus::Open
                            && !item.manual_override
                        {
                            item.status = ItemStatus::Done;
                            item.closed_at = Some(occurred_at.clone());
                            item.revision = revision;
                            persist_item(transaction, &item)?;
                            disposition = EventDisposition::Applied;
                        } else {
                            suggestion_id = Some(insert_suggestion(
                                transaction,
                                &project.id,
                                Some(&item.id),
                                item.milestone_id.as_deref(),
                                SuggestionKind::CompletionCandidate,
                                &source,
                                Some(&event_id),
                                &json!({"status": "done", "evidence": input.evidence}),
                                revision,
                            )?);
                            disposition = EventDisposition::Suggested;
                        }
                    }
                } else {
                    suggestion_id = Some(insert_suggestion(
                        transaction,
                        &project.id,
                        None,
                        input.target.milestone_id.as_deref(),
                        SuggestionKind::TargetResolution,
                        &source,
                        Some(&event_id),
                        &serde_json::to_value(&input)?,
                        revision,
                    )?);
                    disposition = EventDisposition::Suggested;
                }

                reconcile_project(transaction, &project.id, revision)?;
                if item_id.is_some() {
                    record_project_history(transaction, &project.id, revision, "event_apply")?;
                }
                let progress = derive_progress(&load_milestones(transaction, &project.id)?);
                let result = EventApplyResult {
                    disposition,
                    replayed: false,
                    source: source.clone(),
                    event_id: event_id.clone(),
                    project_id: Some(project.id.clone()),
                    item_id: item_id.clone(),
                    milestone_id: milestone.as_ref().map(|value| value.id.clone()),
                    project_progress: Some(progress.clone()),
                    suggestion_id,
                };
                transaction.execute(
                    "INSERT INTO event_receipts(source, event_id, event_type, payload_hash, payload_json, disposition,
                     project_id, item_id, milestone_id, before_json, result_json, applied_revision, created_at, reverted_at)
                     VALUES (?1, ?2, ?3, ?4, ?5, ?6, ?7, ?8, ?9, ?10, ?11, ?12, ?13, NULL)",
                    params![source, event_id, event_type, payload_hash, payload_json, disposition.as_str(), project.id,
                        item_id, milestone.as_ref().map(|value| &value.id), before.to_string(), serde_json::to_string(&result)?, revision, Local::now().to_rfc3339()],
                )?;
                let now = Local::now().to_rfc3339();
                transaction.execute(
                    "INSERT INTO event_outbox(id, source, event_id, topic, payload_json, status, attempts, next_attempt_at, last_error, created_at, delivered_at)
                     VALUES (?1, ?2, ?3, 'snapshot_export', ?4, 'pending', 0, ?5, NULL, ?5, NULL)",
                    params![Uuid::new_v4().to_string(), input.source, input.event_id, json!({"revision": revision}).to_string(), now],
                )?;
                Ok(result)
            },
        )?;
        Ok(mutation(revision, result))
    }

    pub fn event_status(&self, input: EventKeyInput) -> Result<EventApplyResult> {
        self.load_event_result(&input.source, &input.event_id)?
            .map(|(_, result)| result)
            .ok_or_else(|| anyhow!("not_found:event:{}:{}", input.source, input.event_id))
    }

    pub fn revert_event(&self, input: EventKeyInput) -> Result<MutationResult<EventApplyResult>> {
        let connection = self.database.connection()?;
        let receipt = load_receipt_full(&connection, &input.source, &input.event_id)?
            .ok_or_else(|| anyhow!("not_found:event:{}:{}", input.source, input.event_id))?;
        if receipt.disposition == EventDisposition::Reverted {
            let mut result: EventApplyResult = serde_json::from_str(&receipt.result_json)?;
            result.replayed = true;
            return Ok(mutation(self.revision()?, result));
        }
        let (revision, result) = self.database.with_write_transaction(
            input.expected_revision,
            |transaction, revision| {
                let before: Value = serde_json::from_str(&receipt.before_json)?;
                if let Some(value) = before.get("item").filter(|value| !value.is_null()) {
                    let item: Item = serde_json::from_value(value.clone())?;
                    let current = load_item(transaction, &item.id)?
                        .ok_or_else(|| anyhow!("not_found:item:{}", item.id))?;
                    if current.revision > receipt.applied_revision {
                        bail!("event_revert_conflict:{}", item.id);
                    }
                    persist_item(transaction, &item)?;
                    transaction.execute(
                        "DELETE FROM item_evidence WHERE item_id = ?1 AND fingerprint = ?2",
                        params![item.id, format!("{}:{}", receipt.source, receipt.event_id)],
                    )?;
                }
                if let Some(value) = before.get("milestone").filter(|value| !value.is_null()) {
                    let milestone: ProjectMilestone = serde_json::from_value(value.clone())?;
                    persist_milestone(transaction, &milestone)?;
                }
                transaction.execute(
                    "DELETE FROM suggestions WHERE source = ?1 AND event_id = ?2",
                    params![receipt.source, receipt.event_id],
                )?;
                if let Some(project_id) = receipt.project_id.as_deref() {
                    reconcile_project(transaction, project_id, revision)?;
                    record_project_history(transaction, project_id, revision, "event_revert")?;
                }
                let result = EventApplyResult {
                    disposition: EventDisposition::Reverted,
                    replayed: false,
                    source: receipt.source.clone(),
                    event_id: receipt.event_id.clone(),
                    project_id: receipt.project_id.clone(),
                    item_id: receipt.item_id.clone(),
                    milestone_id: receipt.milestone_id.clone(),
                    project_progress: receipt
                        .project_id
                        .as_deref()
                        .map(|project_id| load_milestones(transaction, project_id).map(|values| derive_progress(&values)))
                        .transpose()?,
                    suggestion_id: None,
                };
                transaction.execute(
                    "UPDATE event_receipts SET disposition = 'reverted', result_json = ?3, reverted_at = ?4 WHERE source = ?1 AND event_id = ?2",
                    params![receipt.source, receipt.event_id, serde_json::to_string(&result)?, Local::now().to_rfc3339()],
                )?;
                Ok(result)
            },
        )?;
        Ok(mutation(revision, result))
    }

    pub fn retry_pending_events(&self) -> Result<usize> {
        let connection = self.database.connection()?;
        let entries = query_all(
            &connection,
            "SELECT id, topic FROM event_outbox WHERE status IN ('pending','failed') AND next_attempt_at <= datetime('now') ORDER BY created_at, id",
            |row| Ok((row.get::<_, String>(0)?, row.get::<_, String>(1)?)),
        )?;
        let mut delivered = 0;
        for (id, topic) in entries {
            let result = if topic == "snapshot_export" {
                crate::snapshot::export_snapshot(self, None).map(|_| ())
            } else {
                Ok(())
            };
            self.database.with_transaction_without_revision(|transaction| {
                match &result {
                    Ok(()) => {
                        transaction.execute(
                            "UPDATE event_outbox SET status = 'delivered', attempts = attempts + 1, delivered_at = ?2, last_error = NULL WHERE id = ?1",
                            params![id, Local::now().to_rfc3339()],
                        )?;
                    }
                    Err(error) => {
                        transaction.execute(
                            "UPDATE event_outbox SET status = 'failed', attempts = attempts + 1,
                             next_attempt_at = datetime('now', CASE WHEN attempts < 1 THEN '+1 minute' WHEN attempts < 2 THEN '+5 minutes' ELSE '+30 minutes' END),
                             last_error = ?2 WHERE id = ?1",
                            params![id, format!("{error:#}")],
                        )?;
                    }
                }
                Ok(())
            })?;
            if result.is_ok() {
                delivered += 1;
            }
        }
        Ok(delivered)
    }

    pub fn apply_suggestion(
        &self,
        input: SuggestionDecisionInput,
    ) -> Result<MutationResult<ItemSuggestion>> {
        self.decide_suggestion(input, true)
    }

    pub fn reject_suggestion(
        &self,
        input: SuggestionDecisionInput,
    ) -> Result<MutationResult<ItemSuggestion>> {
        self.decide_suggestion(input, false)
    }

    fn decide_suggestion(
        &self,
        input: SuggestionDecisionInput,
        apply: bool,
    ) -> Result<MutationResult<ItemSuggestion>> {
        let (revision, suggestion) = self.database.with_write_transaction(
            input.expected_revision,
            |transaction, revision| {
                let mut suggestion = load_suggestion(transaction, &input.id)?
                    .ok_or_else(|| anyhow!("not_found:suggestion:{}", input.id))?;
                if suggestion.status != SuggestionStatus::Pending {
                    return Ok(suggestion);
                }
                if apply {
                    if let Some(item_id) = suggestion.item_id.as_deref() {
                        let mut item = load_item(transaction, item_id)?
                            .ok_or_else(|| anyhow!("not_found:item:{item_id}"))?;
                        match suggestion.kind {
                            SuggestionKind::CompletionCandidate => {
                                item.status = ItemStatus::Done;
                                item.closed_at = Some(Local::now().to_rfc3339());
                            }
                            SuggestionKind::ItemPatch => {
                                if let Ok(proposed) = serde_json::from_value::<ItemInput>(
                                    suggestion.proposed_json.clone(),
                                ) {
                                    apply_item_input(&mut item, &proposed, false, revision);
                                }
                            }
                            _ => {}
                        }
                        item.revision = revision;
                        persist_item(transaction, &item)?;
                    }
                    suggestion.status = SuggestionStatus::Applied;
                } else {
                    suggestion.status = SuggestionStatus::Rejected;
                }
                suggestion.resolved_at = Some(Local::now().to_rfc3339());
                suggestion.revision = revision;
                persist_suggestion(transaction, &suggestion)?;
                reconcile_project(transaction, &suggestion.project_id, revision)?;
                record_project_history(
                    transaction,
                    &suggestion.project_id,
                    revision,
                    "suggestion_decision",
                )?;
                Ok(suggestion)
            },
        )?;
        Ok(mutation(revision, suggestion))
    }

    pub fn repair_legacy_encoding(
        &self,
        input: EncodingRepairInput,
    ) -> Result<EncodingRepairResult> {
        let canonical = load_canonical_legacy(Path::new(&input.source_path))?;
        let connection = self.database.connection()?;
        let projects = query_all(
            &connection,
            &format!("SELECT {PROJECT_COLUMNS} FROM projects WHERE legacy_id IS NOT NULL"),
            row_project,
        )?;
        let items = query_all(
            &connection,
            &format!("SELECT {ITEM_COLUMNS} FROM items"),
            row_item,
        )?;
        let mut matched: usize = 0;
        let mut changes = Vec::new();
        for project in projects {
            if let Some(legacy_id) = project.legacy_id.as_deref()
                && let Some(name) = canonical.projects.get(legacy_id)
            {
                matched += 1;
                if let Some(change) =
                    repairable_change("project", &project.id, "name", &project.name, name)
                {
                    changes.push(change);
                }
            }
        }
        for item in items.iter().filter(|item| !item.manual_override) {
            if let Some(title) = canonical.items.get(&item.fingerprint) {
                matched += 1;
                if let Some(change) =
                    repairable_change("item", &item.id, "title", &item.title, title)
                {
                    changes.push(change);
                }
            }
        }
        let repairable = changes.len();
        let skipped = matched.saturating_sub(repairable);
        if !input.apply || changes.is_empty() {
            return Ok(EncodingRepairResult {
                matched,
                repairable,
                skipped,
                changes,
                backup_path: None,
                applied: false,
            });
        }
        let backup_path = backup_database(&self.database)?;
        self.database.with_write_transaction(input.expected_revision, |transaction, revision| {
            for change in &changes {
                match (change.entity.as_str(), change.field.as_str()) {
                    ("project", "name") => {
                        transaction.execute(
                            "UPDATE projects SET name = ?2, updated_at = ?3, revision = ?4 WHERE id = ?1",
                            params![change.id, change.after, Local::now().to_rfc3339(), revision],
                        )?;
                    }
                    ("item", "title") => {
                        transaction.execute(
                            "UPDATE items SET title = ?2, revision = ?3 WHERE id = ?1",
                            params![change.id, change.after, revision],
                        )?;
                        transaction.execute(
                            "UPDATE item_evidence SET summary = ?2 WHERE item_id = ?1 AND summary = ?3",
                            params![change.id, change.after, change.before],
                        )?;
                    }
                    _ => {}
                }
            }
            Ok(())
        })?;
        Ok(EncodingRepairResult {
            matched,
            repairable,
            skipped,
            changes,
            backup_path,
            applied: true,
        })
    }

    pub fn status(&self) -> Result<DatabaseStatus> {
        let connection = self.database.connection()?;
        Ok(DatabaseStatus {
            database_path: self.database.path().display().to_string(),
            revision: self.database.revision()?,
            schema_version: SCHEMA_VERSION,
            projects: scalar_i64(&connection, "SELECT COUNT(*) FROM projects")?,
            items: scalar_i64(&connection, "SELECT COUNT(*) FROM items")?,
            candidates: scalar_i64(
                &connection,
                "SELECT COUNT(*) FROM projects WHERE status = 'candidate'",
            )?,
            milestones: scalar_i64(&connection, "SELECT COUNT(*) FROM project_milestones")?,
            pending_suggestions: scalar_i64(
                &connection,
                "SELECT COUNT(*) FROM suggestions WHERE status = 'pending'",
            )?,
            pending_outbox: scalar_i64(
                &connection,
                "SELECT COUNT(*) FROM event_outbox WHERE status IN ('pending','failed')",
            )?,
            legacy_imported_at: self.database.meta("legacy_imported_at")?,
        })
    }

    pub fn revision_update(&self, since_revision: i64) -> Result<RevisionUpdate> {
        let revision = self.revision()?;
        if revision == since_revision {
            return Ok(RevisionUpdate {
                revision,
                changed: false,
                dashboard: None,
            });
        }
        Ok(RevisionUpdate {
            revision,
            changed: true,
            dashboard: Some(self.dashboard()?),
        })
    }

    fn load_event_result(
        &self,
        source: &str,
        event_id: &str,
    ) -> Result<Option<(String, EventApplyResult)>> {
        let connection = self.database.connection()?;
        Ok(connection
            .query_row(
                "SELECT payload_hash, result_json FROM event_receipts WHERE source = ?1 AND event_id = ?2",
                params![source, event_id],
                |row| Ok((row.get::<_, String>(0)?, row.get::<_, String>(1)?)),
            )
            .optional()?
            .map(|(hash, result)| serde_json::from_str(&result).map(|value| (hash, value)))
            .transpose()?)
    }
}

#[derive(Debug, Clone)]
struct EventReceiptFull {
    source: String,
    event_id: String,
    disposition: EventDisposition,
    project_id: Option<String>,
    item_id: Option<String>,
    milestone_id: Option<String>,
    before_json: String,
    result_json: String,
    applied_revision: i64,
}

fn mutation<T>(revision: i64, value: T) -> MutationResult<T> {
    MutationResult {
        revision,
        value,
        possible_duplicates: Vec::new(),
    }
}

fn validate_project_ref(project: &ProjectRefInput) -> Result<()> {
    if project.id.as_deref().is_none_or(str::is_empty)
        && project.legacy_id.as_deref().is_none_or(str::is_empty)
        && project.name.trim().is_empty()
    {
        bail!("validation_error:项目 id、legacyId 和名称不能同时为空");
    }
    Ok(())
}

fn validate_item_input(item: &ItemInput) -> Result<()> {
    if item.title.trim().is_empty() || item.fingerprint.trim().is_empty() {
        bail!("validation_error:事项标题和 fingerprint 不能为空");
    }
    if !(0..=20).contains(&item.priority) {
        bail!("validation_error:优先级只能是 0–20");
    }
    Ok(())
}

fn validate_milestone_input(title: &str, weight: i32) -> Result<()> {
    if title.trim().is_empty() {
        bail!("validation_error:里程碑标题不能为空");
    }
    if !(1..=100).contains(&weight) {
        bail!("validation_error:里程碑权重只能是 1–100");
    }
    Ok(())
}

fn validate_threshold(value: Option<i32>) -> Result<()> {
    if value.is_some_and(|threshold| !(0..=100).contains(&threshold)) {
        bail!("validation_error:阈值只能是 0–100");
    }
    Ok(())
}

fn validate_project_path_option(
    value: Option<&str>,
    current_id: Option<&str>,
    database: &Database,
) -> Result<()> {
    let Some(value) = value.filter(|value| !value.trim().is_empty()) else {
        return Ok(());
    };
    let normalized = normalize_project_path(Some(value))?
        .ok_or_else(|| anyhow!("validation_error:项目路径无效"))?;
    let path = PathBuf::from(&normalized);
    if !path.is_absolute() || !path.is_dir() {
        bail!("validation_error:项目路径必须是存在的绝对目录");
    }
    let connection = database.connection()?;
    let conflict = connection
        .query_row(
            "SELECT id FROM projects WHERE project_path = ?1 AND (?2 IS NULL OR id <> ?2)",
            params![normalized, current_id],
            |row| row.get::<_, String>(0),
        )
        .optional()?;
    if conflict.is_some() {
        bail!("validation_error:项目路径已绑定其他项目");
    }
    Ok(())
}

fn normalize_project_path(value: Option<&str>) -> Result<Option<String>> {
    let Some(value) = value.filter(|value| !value.trim().is_empty()) else {
        return Ok(None);
    };
    let canonical = std::fs::canonicalize(value)
        .with_context(|| format!("validation_error:项目路径不存在：{value}"))?;
    Ok(Some(canonical.to_string_lossy().replace('\\', "/")))
}

fn resolve_or_create_project(
    connection: &Connection,
    input: &ProjectRefInput,
    automatic: bool,
    revision: i64,
) -> Result<Project> {
    if let Some(id) = input.id.as_deref().filter(|value| !value.is_empty())
        && let Some(project) = load_project(connection, id)?
    {
        return Ok(project);
    }
    if let Some(legacy_id) = input.legacy_id.as_deref().filter(|value| !value.is_empty()) {
        if let Some(project) = connection
            .query_row(
                &format!("SELECT {PROJECT_COLUMNS} FROM projects WHERE legacy_id = ?1"),
                [legacy_id],
                row_project,
            )
            .optional()?
        {
            return Ok(project);
        }
    }
    let now = Local::now().to_rfc3339();
    let project = Project {
        id: Uuid::new_v4().to_string(),
        legacy_id: input.legacy_id.clone().filter(|value| !value.is_empty()),
        name: input.name.trim().to_owned(),
        status: if automatic {
            ProjectStatus::Candidate
        } else {
            ProjectStatus::Active
        },
        summary: String::new(),
        goal: String::new(),
        success_criteria: String::new(),
        maturity_stage: MaturityStage::Idea,
        maturity_manual_override: false,
        project_path: None,
        threshold_override: None,
        created_at: now.clone(),
        updated_at: now,
        revision,
    };
    insert_project_exact(connection, &project)?;
    Ok(project)
}

fn resolve_event_project(connection: &Connection, input: &EventProjectInput) -> Result<Project> {
    let by_id = input
        .id
        .as_deref()
        .map(|id| load_project(connection, id))
        .transpose()?
        .flatten();
    let by_path = input
        .path
        .as_deref()
        .map(|path| {
            let normalized = path.replace('\\', "/");
            connection
                .query_row(
                    &format!("SELECT {PROJECT_COLUMNS} FROM projects WHERE project_path = ?1"),
                    [normalized],
                    row_project,
                )
                .optional()
                .map_err(anyhow::Error::from)
        })
        .transpose()?
        .flatten();
    if let (Some(left), Some(right)) = (&by_id, &by_path)
        && left.id != right.id
    {
        bail!("target_conflict:project");
    }
    by_id
        .or(by_path)
        .ok_or_else(|| anyhow!("validation_error:事件必须精确匹配已登记项目"))
}

fn resolve_event_item(
    connection: &Connection,
    project_id: &str,
    target: &EventTargetInput,
) -> Result<Option<Item>> {
    let by_id = target
        .item_id
        .as_deref()
        .map(|id| load_item(connection, id))
        .transpose()?
        .flatten();
    if by_id
        .as_ref()
        .is_some_and(|item| item.project_id != project_id)
    {
        bail!("target_conflict:item_project");
    }
    let by_fingerprint = target.fingerprint.as_deref().map(|fingerprint| {
        connection
            .query_row(
                &format!("SELECT {ITEM_COLUMNS} FROM items WHERE project_id = ?1 AND fingerprint = ?2"),
                params![project_id, fingerprint],
                row_item,
            )
            .optional()
            .map_err(anyhow::Error::from)
    }).transpose()?.flatten();
    if let (Some(left), Some(right)) = (&by_id, &by_fingerprint)
        && left.id != right.id
    {
        bail!("target_conflict:item");
    }
    Ok(by_id.or(by_fingerprint))
}

fn validate_milestone_project(
    connection: &Connection,
    milestone_id: Option<&str>,
    project_id: &str,
) -> Result<()> {
    let Some(id) = milestone_id else {
        return Ok(());
    };
    let milestone =
        load_milestone(connection, id)?.ok_or_else(|| anyhow!("not_found:milestone:{id}"))?;
    if milestone.project_id != project_id {
        bail!("validation_error:里程碑不属于事项项目");
    }
    Ok(())
}

fn apply_item_input(item: &mut Item, input: &ItemInput, automatic: bool, revision: i64) {
    item.title = input.title.trim().to_owned();
    item.details = input.details.trim().to_owned();
    item.kind = input.kind;
    item.horizon = input.horizon;
    item.priority = input.priority.clamp(0, 20);
    item.pinned = input.pinned;
    item.milestone_id = input.milestone_id.clone();
    item.blocking = input.blocking;
    item.auto_daily = automatic && input.horizon == ItemHorizon::Daily;
    item.revision = revision;
}

fn item_from_input(project_id: &str, input: &ItemInput, automatic: bool, revision: i64) -> Item {
    Item {
        id: input
            .id
            .clone()
            .unwrap_or_else(|| Uuid::new_v4().to_string()),
        project_id: project_id.to_owned(),
        milestone_id: input.milestone_id.clone(),
        fingerprint: input.fingerprint.trim().to_owned(),
        title: input.title.trim().to_owned(),
        details: input.details.trim().to_owned(),
        kind: input.kind,
        horizon: input.horizon,
        status: ItemStatus::Open,
        blocking: input.blocking,
        priority: input.priority.clamp(0, 20),
        activity_score: 0,
        last_evidence_at: None,
        manual_override: false,
        pinned: input.pinned,
        auto_daily: automatic && input.horizon == ItemHorizon::Daily,
        sort_order: 0,
        closed_at: None,
        revision,
    }
}

fn top_active_project(connection: &Connection) -> Result<Option<Project>> {
    Ok(connection
        .query_row(
            &format!("SELECT {PROJECT_COLUMNS} FROM projects WHERE status = 'active' ORDER BY updated_at DESC, id ASC LIMIT 1"),
            [],
            row_project,
        )
        .optional()?)
}

fn find_item(connection: &Connection, project_id: &str, input: &ItemInput) -> Result<Option<Item>> {
    if let Some(id) = input.id.as_deref().filter(|value| !value.is_empty())
        && let Some(item) = load_item(connection, id)?
    {
        return Ok(Some(item));
    }
    Ok(connection
        .query_row(
            &format!("SELECT {ITEM_COLUMNS} FROM items WHERE project_id = ?1 AND fingerprint = ?2"),
            params![project_id, input.fingerprint],
            row_item,
        )
        .optional()?)
}

fn find_possible_duplicates(
    connection: &Connection,
    project_id: &str,
    title: &str,
    exclude_id: Option<&str>,
) -> Result<Vec<String>> {
    let first = title.split_whitespace().next().unwrap_or(title);
    if first.chars().count() < 2 {
        return Ok(Vec::new());
    }
    let pattern = format!("%{first}%");
    query_all_with(
        connection,
        "SELECT title FROM items WHERE project_id = ?1 AND status = 'open' AND title LIKE ?2 AND (?3 IS NULL OR id <> ?3) ORDER BY id LIMIT 5",
        params![project_id, pattern, exclude_id],
        |row| row.get(0),
    )
}

fn append_evidence(
    connection: &Connection,
    item_id: &str,
    source: &SourceInput,
    fingerprint: Option<&str>,
) -> Result<()> {
    let fingerprint = fingerprint
        .map(str::to_owned)
        .unwrap_or_else(|| format!("{}:{}", source.source_ref, source.occurred_at));
    connection.execute(
        "INSERT OR IGNORE INTO item_evidence(id, item_id, source_ref, summary, short_quote, occurred_at, fingerprint)
         VALUES (?1, ?2, ?3, ?4, ?5, ?6, ?7)",
        params![Uuid::new_v4().to_string(), item_id, source.source_ref, source.summary, source.short_quote, source.occurred_at, fingerprint],
    )?;
    Ok(())
}

fn append_event_evidence(
    connection: &Connection,
    item_id: &str,
    input: &ApplyEventInput,
) -> Result<()> {
    append_evidence(
        connection,
        item_id,
        &SourceInput {
            source_ref: input.evidence.source_ref.clone(),
            summary: input.evidence.summary.clone(),
            short_quote: input.evidence.short_quote.clone(),
            occurred_at: input.occurred_at.clone(),
        },
        Some(&format!("{}:{}", input.source, input.event_id)),
    )
}

fn refresh_item_activity(connection: &Connection, item_id: &str, revision: i64) -> Result<Item> {
    let mut item =
        load_item(connection, item_id)?.ok_or_else(|| anyhow!("not_found:item:{item_id}"))?;
    let recent = (Utc::now() - Duration::days(7)).to_rfc3339();
    let (last, count): (Option<String>, i64) = connection.query_row(
        "SELECT MAX(occurred_at), SUM(CASE WHEN occurred_at >= ?2 THEN 1 ELSE 0 END) FROM item_evidence WHERE item_id = ?1",
        params![item_id, recent],
        |row| Ok((row.get(0)?, row.get::<_, Option<i64>>(1)?.unwrap_or(0))),
    )?;
    let parsed = last
        .as_deref()
        .and_then(|value| DateTime::parse_from_rfc3339(value).ok())
        .map(|value| value.with_timezone(&Utc));
    item.last_evidence_at = last;
    item.activity_score = calculate_activity(Utc::now(), parsed, count, item.priority).total;
    item.revision = revision;
    persist_item(connection, &item)?;
    Ok(item)
}

fn reconcile_project(connection: &Connection, project_id: &str, revision: i64) -> Result<()> {
    let milestones = load_milestones(connection, project_id)?;
    for mut milestone in milestones {
        if milestone.manual_override || milestone.status == MilestoneStatus::Cancelled {
            continue;
        }
        let (total, open_actions, done_actions, blocking_questions): (i64, i64, i64, i64) = connection.query_row(
            "SELECT
               SUM(CASE WHEN kind = 'action' AND status <> 'dismissed' THEN 1 ELSE 0 END),
               SUM(CASE WHEN kind = 'action' AND status = 'open' THEN 1 ELSE 0 END),
               SUM(CASE WHEN kind = 'action' AND status = 'done' THEN 1 ELSE 0 END),
               SUM(CASE WHEN kind = 'question' AND status = 'open' AND blocking = 1 THEN 1 ELSE 0 END)
             FROM items WHERE milestone_id = ?1",
            [&milestone.id],
            |row| Ok((row.get::<_, Option<i64>>(0)?.unwrap_or(0), row.get::<_, Option<i64>>(1)?.unwrap_or(0), row.get::<_, Option<i64>>(2)?.unwrap_or(0), row.get::<_, Option<i64>>(3)?.unwrap_or(0))),
        )?;
        let desired = if total > 0 && open_actions == 0 && blocking_questions == 0 {
            MilestoneStatus::Completed
        } else if done_actions > 0 || milestone.status == MilestoneStatus::Completed {
            MilestoneStatus::InProgress
        } else {
            MilestoneStatus::Planned
        };
        if desired != milestone.status {
            milestone.status = desired;
            milestone.completed_at = if desired == MilestoneStatus::Completed {
                Some(Local::now().to_rfc3339())
            } else {
                None
            };
            milestone.updated_at = Local::now().to_rfc3339();
            milestone.revision = revision;
            persist_milestone(connection, &milestone)?;
        }
    }
    let milestones = load_milestones(connection, project_id)?;
    let progress = derive_progress(&milestones);
    let has_blocking = connection.query_row(
        "SELECT EXISTS(SELECT 1 FROM items WHERE project_id = ?1 AND kind = 'question' AND status = 'open' AND blocking = 1)",
        [project_id],
        |row| row.get::<_, bool>(0),
    )?;
    let mut project = load_project(connection, project_id)?
        .ok_or_else(|| anyhow!("not_found:project:{project_id}"))?;
    let derived = derive_maturity(&project, &milestones, &progress, has_blocking);
    if derived != project.maturity_stage {
        project.maturity_stage = derived;
        project.updated_at = Local::now().to_rfc3339();
        project.revision = revision;
        persist_project(connection, &project)?;
    }
    Ok(())
}

fn record_batch_if_needed(connection: &Connection, batch: Option<&BatchInput>) -> Result<()> {
    let Some(batch) = batch.filter(|batch| batch.kind == "automatic") else {
        return Ok(());
    };
    let exists = connection
        .query_row(
            "SELECT 1 FROM update_batches WHERE id = ?1",
            [&batch.id],
            |_| Ok(()),
        )
        .optional()?
        .is_some();
    if exists {
        return Ok(());
    }
    let state = capture_state(connection)?;
    connection.execute(
        "INSERT INTO update_batches(id, kind, source_ref, before_json, created_at, reverted_at) VALUES (?1, ?2, ?3, ?4, ?5, NULL)",
        params![batch.id, batch.kind, batch.source_ref, serde_json::to_string(&state)?, Local::now().to_rfc3339()],
    )?;
    Ok(())
}

fn capture_state(connection: &Connection) -> Result<BackupState> {
    Ok(BackupState {
        projects: query_all(
            connection,
            &format!("SELECT {PROJECT_COLUMNS} FROM projects ORDER BY id"),
            row_project,
        )?,
        milestones: query_all(
            connection,
            &format!("SELECT {MILESTONE_COLUMNS} FROM project_milestones ORDER BY id"),
            row_milestone,
        )?,
        items: query_all(
            connection,
            &format!("SELECT {ITEM_COLUMNS} FROM items ORDER BY id"),
            row_item,
        )?,
        evidence: query_all(
            connection,
            "SELECT id, item_id, source_ref, summary, short_quote, occurred_at, fingerprint FROM item_evidence ORDER BY id",
            row_evidence,
        )?,
        suggestions: query_all(
            connection,
            &format!("SELECT {SUGGESTION_COLUMNS} FROM suggestions ORDER BY id"),
            row_suggestion,
        )?,
        history: query_all(
            connection,
            "SELECT id, project_id, revision, change_kind, snapshot_json, created_at FROM project_history ORDER BY id",
            row_history,
        )?,
    })
}

fn record_project_history(
    connection: &Connection,
    project_id: &str,
    revision: i64,
    change_kind: &str,
) -> Result<()> {
    let project = load_project(connection, project_id)?
        .ok_or_else(|| anyhow!("not_found:project:{project_id}"))?;
    let items = query_all_with(
        connection,
        &format!("SELECT {ITEM_COLUMNS} FROM items WHERE project_id = ?1 ORDER BY sort_order, id"),
        [project_id],
        row_item,
    )?;
    let milestones = load_milestones(connection, project_id)?;
    let snapshot = json!({"project": project, "milestones": milestones, "items": items});
    connection.execute(
        "INSERT INTO project_history(id, project_id, revision, change_kind, snapshot_json, created_at) VALUES (?1, ?2, ?3, ?4, ?5, ?6)",
        params![Uuid::new_v4().to_string(), project_id, revision, change_kind, snapshot.to_string(), Local::now().to_rfc3339()],
    )?;
    connection.execute(
        "DELETE FROM project_history WHERE project_id = ?1 AND id NOT IN (
           SELECT id FROM project_history WHERE project_id = ?1 ORDER BY revision DESC, created_at DESC LIMIT 50)",
        [project_id],
    )?;
    Ok(())
}

fn load_project_dashboard(
    connection: &Connection,
    project: Project,
) -> Result<ProjectDashboardView> {
    let view = load_project_view(connection, project.clone())?;
    let open_todo_count = view
        .items
        .iter()
        .filter(|item| item.item.kind == ItemKind::Action && item.item.status == ItemStatus::Open)
        .count() as i64;
    let open_question_count = view
        .items
        .iter()
        .filter(|item| item.item.kind == ItemKind::Question && item.item.status == ItemStatus::Open)
        .count() as i64;
    let blocking_count = view
        .items
        .iter()
        .filter(|item| item.item.blocking && item.item.status == ItemStatus::Open)
        .count() as i64;
    let pending_suggestion_count = view.items.iter().flat_map(|item| &item.suggestions).filter(|suggestion| suggestion.status == SuggestionStatus::Pending).count() as i64
        + query_all_with(connection, &format!("SELECT {SUGGESTION_COLUMNS} FROM suggestions WHERE project_id = ?1 AND item_id IS NULL AND status = 'pending'"), [&project.id], row_suggestion)?.len() as i64;
    let next_items = view
        .items
        .iter()
        .filter(|item| item.item.status == ItemStatus::Open)
        .take(12)
        .cloned()
        .collect();
    Ok(ProjectDashboardView {
        project,
        progress: view.progress,
        open_todo_count,
        open_question_count,
        blocking_count,
        pending_suggestion_count,
        next_items,
    })
}

fn load_project_view(connection: &Connection, project: Project) -> Result<ProjectView> {
    let items = query_item_views(
        connection,
        &format!(
            "SELECT {ITEM_COLUMNS} FROM items WHERE project_id = ?1 ORDER BY CASE status WHEN 'open' THEN 0 WHEN 'done' THEN 1 ELSE 2 END, blocking DESC, kind DESC, sort_order ASC, priority DESC, id ASC"
        ),
        [&project.id],
    )?;
    let milestones = load_milestones(connection, &project.id)?;
    let activity_score = items
        .iter()
        .filter(|item| item.item.status == ItemStatus::Open)
        .map(|item| item.item.activity_score)
        .max()
        .unwrap_or(0);
    let history = query_all_with(
        connection,
        "SELECT id, project_id, revision, change_kind, snapshot_json, created_at FROM project_history WHERE project_id = ?1 ORDER BY revision DESC, created_at DESC LIMIT 50",
        [&project.id],
        row_history,
    )?;
    let events = query_all_with(
        connection,
        "SELECT source, event_id, event_type, disposition, project_id, item_id, milestone_id, applied_revision, created_at, reverted_at FROM event_receipts WHERE project_id = ?1 ORDER BY created_at DESC LIMIT 100",
        [&project.id],
        row_receipt_summary,
    )?;
    Ok(ProjectView {
        progress: derive_progress(&milestones),
        project,
        activity_score,
        milestones,
        items,
        history,
        events,
    })
}

fn query_item_views<P: rusqlite::Params>(
    connection: &Connection,
    sql: &str,
    params: P,
) -> Result<Vec<ItemView>> {
    let items = query_all_with(connection, sql, params, row_item)?;
    items.into_iter().map(|item| {
        let evidence = query_all_with(connection, "SELECT id, item_id, source_ref, summary, short_quote, occurred_at, fingerprint FROM item_evidence WHERE item_id = ?1 ORDER BY occurred_at DESC, id ASC", [&item.id], row_evidence)?;
        let suggestions = query_all_with(connection, &format!("SELECT {SUGGESTION_COLUMNS} FROM suggestions WHERE item_id = ?1 ORDER BY created_at DESC, id ASC"), [&item.id], row_suggestion)?;
        let recent = (Utc::now() - Duration::days(7)).to_rfc3339();
        let count = evidence.iter().filter(|entry| entry.occurred_at >= recent).count() as i64;
        let last = item.last_evidence_at.as_deref().and_then(|value| DateTime::parse_from_rfc3339(value).ok()).map(|value| value.with_timezone(&Utc));
        let activity = calculate_activity(Utc::now(), last, count, item.priority);
        Ok(ItemView { item, evidence, suggestions, activity })
    }).collect()
}

fn load_settings(connection: &Connection) -> Result<Settings> {
    Ok(connection.query_row(
        "SELECT daily_threshold, now_limit, poll_seconds, always_on_top, auto_snap, autostart FROM settings WHERE id = 1",
        [],
        |row| Ok(Settings { daily_threshold: row.get(0)?, now_limit: row.get(1)?, poll_seconds: row.get(2)?, always_on_top: row.get(3)?, auto_snap: row.get(4)?, autostart: row.get(5)? }),
    )?)
}

fn load_project(connection: &Connection, id: &str) -> Result<Option<Project>> {
    Ok(connection
        .query_row(
            &format!("SELECT {PROJECT_COLUMNS} FROM projects WHERE id = ?1"),
            [id],
            row_project,
        )
        .optional()?)
}

fn load_item(connection: &Connection, id: &str) -> Result<Option<Item>> {
    Ok(connection
        .query_row(
            &format!("SELECT {ITEM_COLUMNS} FROM items WHERE id = ?1"),
            [id],
            row_item,
        )
        .optional()?)
}

fn load_milestone(connection: &Connection, id: &str) -> Result<Option<ProjectMilestone>> {
    Ok(connection
        .query_row(
            &format!("SELECT {MILESTONE_COLUMNS} FROM project_milestones WHERE id = ?1"),
            [id],
            row_milestone,
        )
        .optional()?)
}

fn load_milestones(connection: &Connection, project_id: &str) -> Result<Vec<ProjectMilestone>> {
    query_all_with(
        connection,
        &format!(
            "SELECT {MILESTONE_COLUMNS} FROM project_milestones WHERE project_id = ?1 ORDER BY sort_order, created_at, id"
        ),
        [project_id],
        row_milestone,
    )
}

fn load_suggestion(connection: &Connection, id: &str) -> Result<Option<ItemSuggestion>> {
    Ok(connection
        .query_row(
            &format!("SELECT {SUGGESTION_COLUMNS} FROM suggestions WHERE id = ?1"),
            [id],
            row_suggestion,
        )
        .optional()?)
}

fn load_receipt_full(
    connection: &Connection,
    source: &str,
    event_id: &str,
) -> Result<Option<EventReceiptFull>> {
    Ok(connection.query_row(
        "SELECT source, event_id, disposition, project_id, item_id, milestone_id, before_json, result_json, applied_revision FROM event_receipts WHERE source = ?1 AND event_id = ?2",
        params![source, event_id],
        |row| Ok(EventReceiptFull {
            source: row.get(0)?, event_id: row.get(1)?,
            disposition: EventDisposition::from_str(row.get::<_, String>(2)?.as_str()).map_err(sql_conversion_error)?,
            project_id: row.get(3)?, item_id: row.get(4)?, milestone_id: row.get(5)?, before_json: row.get(6)?, result_json: row.get(7)?, applied_revision: row.get(8)?,
        }),
    ).optional()?)
}

fn row_project(row: &Row<'_>) -> rusqlite::Result<Project> {
    Ok(Project {
        id: row.get(0)?,
        legacy_id: row.get(1)?,
        name: row.get(2)?,
        status: ProjectStatus::from_str(row.get::<_, String>(3)?.as_str())
            .map_err(sql_conversion_error)?,
        summary: row.get(4)?,
        goal: row.get(5)?,
        success_criteria: row.get(6)?,
        maturity_stage: MaturityStage::from_str(row.get::<_, String>(7)?.as_str())
            .map_err(sql_conversion_error)?,
        maturity_manual_override: row.get(8)?,
        project_path: row.get(9)?,
        threshold_override: row.get(10)?,
        created_at: row.get(11)?,
        updated_at: row.get(12)?,
        revision: row.get(13)?,
    })
}

fn row_item(row: &Row<'_>) -> rusqlite::Result<Item> {
    Ok(Item {
        id: row.get(0)?,
        project_id: row.get(1)?,
        milestone_id: row.get(2)?,
        fingerprint: row.get(3)?,
        title: row.get(4)?,
        details: row.get(5)?,
        kind: ItemKind::from_str(row.get::<_, String>(6)?.as_str())
            .map_err(sql_conversion_error)?,
        horizon: ItemHorizon::from_str(row.get::<_, String>(7)?.as_str())
            .map_err(sql_conversion_error)?,
        status: ItemStatus::from_str(row.get::<_, String>(8)?.as_str())
            .map_err(sql_conversion_error)?,
        blocking: row.get(9)?,
        priority: row.get(10)?,
        activity_score: row.get(11)?,
        last_evidence_at: row.get(12)?,
        manual_override: row.get(13)?,
        pinned: row.get(14)?,
        auto_daily: row.get(15)?,
        sort_order: row.get(16)?,
        closed_at: row.get(17)?,
        revision: row.get(18)?,
    })
}

fn row_milestone(row: &Row<'_>) -> rusqlite::Result<ProjectMilestone> {
    Ok(ProjectMilestone {
        id: row.get(0)?,
        project_id: row.get(1)?,
        fingerprint: row.get(2)?,
        title: row.get(3)?,
        acceptance_criteria: row.get(4)?,
        status: MilestoneStatus::from_str(row.get::<_, String>(5)?.as_str())
            .map_err(sql_conversion_error)?,
        weight: row.get(6)?,
        manual_override: row.get(7)?,
        sort_order: row.get(8)?,
        created_at: row.get(9)?,
        updated_at: row.get(10)?,
        completed_at: row.get(11)?,
        revision: row.get(12)?,
    })
}

fn row_evidence(row: &Row<'_>) -> rusqlite::Result<Evidence> {
    Ok(Evidence {
        id: row.get(0)?,
        item_id: row.get(1)?,
        source_ref: row.get(2)?,
        summary: row.get(3)?,
        short_quote: row.get(4)?,
        occurred_at: row.get(5)?,
        fingerprint: row.get(6)?,
    })
}

fn row_suggestion(row: &Row<'_>) -> rusqlite::Result<ItemSuggestion> {
    let proposed: String = row.get(7)?;
    Ok(ItemSuggestion {
        id: row.get(0)?,
        project_id: row.get(1)?,
        item_id: row.get(2)?,
        milestone_id: row.get(3)?,
        kind: SuggestionKind::from_str(row.get::<_, String>(4)?.as_str())
            .map_err(sql_conversion_error)?,
        source: row.get(5)?,
        event_id: row.get(6)?,
        proposed_json: serde_json::from_str(&proposed).unwrap_or(Value::Null),
        status: SuggestionStatus::from_str(row.get::<_, String>(8)?.as_str())
            .map_err(sql_conversion_error)?,
        created_at: row.get(9)?,
        resolved_at: row.get(10)?,
        revision: row.get(11)?,
    })
}

fn row_history(row: &Row<'_>) -> rusqlite::Result<ProjectHistory> {
    let snapshot: String = row.get(4)?;
    Ok(ProjectHistory {
        id: row.get(0)?,
        project_id: row.get(1)?,
        revision: row.get(2)?,
        change_kind: row.get(3)?,
        snapshot_json: serde_json::from_str(&snapshot).unwrap_or(Value::Null),
        created_at: row.get(5)?,
    })
}

fn row_receipt_summary(row: &Row<'_>) -> rusqlite::Result<EventReceiptSummary> {
    Ok(EventReceiptSummary {
        source: row.get(0)?,
        event_id: row.get(1)?,
        event_type: row.get(2)?,
        disposition: EventDisposition::from_str(row.get::<_, String>(3)?.as_str())
            .map_err(sql_conversion_error)?,
        project_id: row.get(4)?,
        item_id: row.get(5)?,
        milestone_id: row.get(6)?,
        applied_revision: row.get(7)?,
        created_at: row.get(8)?,
        reverted_at: row.get(9)?,
    })
}

fn sql_conversion_error(error: String) -> rusqlite::Error {
    rusqlite::Error::FromSqlConversionFailure(
        0,
        rusqlite::types::Type::Text,
        Box::new(std::io::Error::new(std::io::ErrorKind::InvalidData, error)),
    )
}

fn persist_project(connection: &Connection, project: &Project) -> Result<()> {
    connection.execute(
        "UPDATE projects SET legacy_id = ?2, name = ?3, status = ?4, summary = ?5, goal = ?6, success_criteria = ?7,
         maturity_stage = ?8, maturity_manual_override = ?9, project_path = ?10, threshold_override = ?11,
         created_at = ?12, updated_at = ?13, revision = ?14 WHERE id = ?1",
        params![project.id, project.legacy_id, project.name, project.status.as_str(), project.summary, project.goal, project.success_criteria,
            project.maturity_stage.as_str(), project.maturity_manual_override, project.project_path, project.threshold_override, project.created_at, project.updated_at, project.revision],
    )?;
    Ok(())
}

fn insert_project_exact(connection: &Connection, project: &Project) -> Result<()> {
    connection.execute(
        "INSERT INTO projects(id, legacy_id, name, status, summary, goal, success_criteria, maturity_stage, maturity_manual_override,
         project_path, threshold_override, created_at, updated_at, revision) VALUES (?1, ?2, ?3, ?4, ?5, ?6, ?7, ?8, ?9, ?10, ?11, ?12, ?13, ?14)",
        params![project.id, project.legacy_id, project.name, project.status.as_str(), project.summary, project.goal, project.success_criteria,
            project.maturity_stage.as_str(), project.maturity_manual_override, project.project_path, project.threshold_override, project.created_at, project.updated_at, project.revision],
    )?;
    Ok(())
}

fn persist_item(connection: &Connection, item: &Item) -> Result<()> {
    connection.execute(
        "UPDATE items SET project_id = ?2, milestone_id = ?3, fingerprint = ?4, title = ?5, details = ?6, kind = ?7,
         horizon = ?8, status = ?9, blocking = ?10, priority = ?11, activity_score = ?12, last_evidence_at = ?13,
         manual_override = ?14, pinned = ?15, auto_daily = ?16, sort_order = ?17, closed_at = ?18, revision = ?19 WHERE id = ?1",
        params![item.id, item.project_id, item.milestone_id, item.fingerprint, item.title, item.details, item.kind.as_str(), item.horizon.as_str(), item.status.as_str(),
            item.blocking, item.priority, item.activity_score, item.last_evidence_at, item.manual_override, item.pinned, item.auto_daily, item.sort_order, item.closed_at, item.revision],
    )?;
    Ok(())
}

fn insert_item(connection: &Connection, item: &Item) -> Result<()> {
    connection.execute(
        "INSERT INTO items(id, project_id, milestone_id, fingerprint, title, details, kind, horizon, status, blocking, priority,
         activity_score, last_evidence_at, manual_override, pinned, auto_daily, sort_order, closed_at, revision)
         VALUES (?1, ?2, ?3, ?4, ?5, ?6, ?7, ?8, ?9, ?10, ?11, ?12, ?13, ?14, ?15, ?16, ?17, ?18, ?19)",
        params![item.id, item.project_id, item.milestone_id, item.fingerprint, item.title, item.details, item.kind.as_str(), item.horizon.as_str(), item.status.as_str(),
            item.blocking, item.priority, item.activity_score, item.last_evidence_at, item.manual_override, item.pinned, item.auto_daily, item.sort_order, item.closed_at, item.revision],
    )?;
    Ok(())
}

fn persist_milestone(connection: &Connection, milestone: &ProjectMilestone) -> Result<()> {
    connection.execute(
        "UPDATE project_milestones SET project_id = ?2, fingerprint = ?3, title = ?4, acceptance_criteria = ?5, status = ?6,
         weight = ?7, manual_override = ?8, sort_order = ?9, created_at = ?10, updated_at = ?11, completed_at = ?12, revision = ?13 WHERE id = ?1",
        params![milestone.id, milestone.project_id, milestone.fingerprint, milestone.title, milestone.acceptance_criteria, milestone.status.as_str(),
            milestone.weight, milestone.manual_override, milestone.sort_order, milestone.created_at, milestone.updated_at, milestone.completed_at, milestone.revision],
    )?;
    Ok(())
}

fn insert_milestone(connection: &Connection, milestone: &ProjectMilestone) -> Result<()> {
    connection.execute(
        "INSERT INTO project_milestones(id, project_id, fingerprint, title, acceptance_criteria, status, weight, manual_override,
         sort_order, created_at, updated_at, completed_at, revision) VALUES (?1, ?2, ?3, ?4, ?5, ?6, ?7, ?8, ?9, ?10, ?11, ?12, ?13)",
        params![milestone.id, milestone.project_id, milestone.fingerprint, milestone.title, milestone.acceptance_criteria, milestone.status.as_str(),
            milestone.weight, milestone.manual_override, milestone.sort_order, milestone.created_at, milestone.updated_at, milestone.completed_at, milestone.revision],
    )?;
    Ok(())
}

fn insert_evidence_exact(connection: &Connection, evidence: &Evidence) -> Result<()> {
    connection.execute(
        "INSERT INTO item_evidence(id, item_id, source_ref, summary, short_quote, occurred_at, fingerprint) VALUES (?1, ?2, ?3, ?4, ?5, ?6, ?7)",
        params![evidence.id, evidence.item_id, evidence.source_ref, evidence.summary, evidence.short_quote, evidence.occurred_at, evidence.fingerprint],
    )?;
    Ok(())
}

fn insert_suggestion(
    connection: &Connection,
    project_id: &str,
    item_id: Option<&str>,
    milestone_id: Option<&str>,
    kind: SuggestionKind,
    source: &str,
    event_id: Option<&str>,
    proposed: &Value,
    revision: i64,
) -> Result<String> {
    let id = Uuid::new_v4().to_string();
    connection.execute(
        "INSERT INTO suggestions(id, project_id, item_id, milestone_id, kind, source, event_id, proposed_json, status, created_at, resolved_at, revision)
         VALUES (?1, ?2, ?3, ?4, ?5, ?6, ?7, ?8, 'pending', ?9, NULL, ?10)",
        params![id, project_id, item_id, milestone_id, kind.as_str(), source, event_id, proposed.to_string(), Local::now().to_rfc3339(), revision],
    )?;
    Ok(id)
}

fn persist_suggestion(connection: &Connection, suggestion: &ItemSuggestion) -> Result<()> {
    connection.execute(
        "UPDATE suggestions SET project_id = ?2, item_id = ?3, milestone_id = ?4, kind = ?5, source = ?6, event_id = ?7,
         proposed_json = ?8, status = ?9, created_at = ?10, resolved_at = ?11, revision = ?12 WHERE id = ?1",
        params![suggestion.id, suggestion.project_id, suggestion.item_id, suggestion.milestone_id, suggestion.kind.as_str(), suggestion.source,
            suggestion.event_id, suggestion.proposed_json.to_string(), suggestion.status.as_str(), suggestion.created_at, suggestion.resolved_at, suggestion.revision],
    )?;
    Ok(())
}

fn insert_suggestion_exact(connection: &Connection, suggestion: &ItemSuggestion) -> Result<()> {
    let project_id = if suggestion.project_id.is_empty() {
        suggestion
            .item_id
            .as_deref()
            .map(|item_id| load_item(connection, item_id))
            .transpose()?
            .flatten()
            .map(|item| item.project_id)
            .unwrap_or_default()
    } else {
        suggestion.project_id.clone()
    };
    connection.execute(
        "INSERT INTO suggestions(id, project_id, item_id, milestone_id, kind, source, event_id, proposed_json, status, created_at, resolved_at, revision)
         VALUES (?1, ?2, ?3, ?4, ?5, ?6, ?7, ?8, ?9, ?10, ?11, ?12)",
        params![suggestion.id, project_id, suggestion.item_id, suggestion.milestone_id, suggestion.kind.as_str(), suggestion.source,
            suggestion.event_id, suggestion.proposed_json.to_string(), suggestion.status.as_str(), suggestion.created_at, suggestion.resolved_at, suggestion.revision],
    )?;
    Ok(())
}

fn insert_history_exact(connection: &Connection, history: &ProjectHistory) -> Result<()> {
    connection.execute(
        "INSERT INTO project_history(id, project_id, revision, change_kind, snapshot_json, created_at) VALUES (?1, ?2, ?3, ?4, ?5, ?6)",
        params![history.id, history.project_id, history.revision, history.change_kind, history.snapshot_json.to_string(), history.created_at],
    )?;
    Ok(())
}

fn scalar_i64(connection: &Connection, sql: &str) -> Result<i64> {
    Ok(connection.query_row(sql, [], |row| row.get(0))?)
}

fn query_all<T, F>(connection: &Connection, sql: &str, mapper: F) -> Result<Vec<T>>
where
    F: FnMut(&Row<'_>) -> rusqlite::Result<T>,
{
    let mut statement = connection.prepare(sql)?;
    Ok(statement
        .query_map([], mapper)?
        .collect::<rusqlite::Result<Vec<_>>>()?)
}

fn query_all_with<T, P, F>(
    connection: &Connection,
    sql: &str,
    params: P,
    mapper: F,
) -> Result<Vec<T>>
where
    P: rusqlite::Params,
    F: FnMut(&Row<'_>) -> rusqlite::Result<T>,
{
    let mut statement = connection.prepare(sql)?;
    Ok(statement
        .query_map(params, mapper)?
        .collect::<rusqlite::Result<Vec<_>>>()?)
}
