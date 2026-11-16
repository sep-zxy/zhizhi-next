use crate::{
    ContinuityService, DEFAULT_VAULT, Database,
    models::{DatabaseRebuildInput, DatabaseRebuildResult, ItemHorizon, ItemKind, ProjectStatus},
    scoring::calculate_activity,
};
use anyhow::{Context, Result};
use chrono::{DateTime, Local, NaiveDate, TimeZone, Utc};
use regex::Regex;
use rusqlite::{OptionalExtension, Transaction, params};
use serde::{Deserialize, Serialize};
use std::{
    fs,
    path::{Path, PathBuf},
};
use uuid::Uuid;

#[derive(Debug, Clone, Default, Serialize, Deserialize)]
#[serde(rename_all = "camelCase")]
pub struct ImportSummary {
    pub projects: usize,
    pub items: usize,
    pub evidence: usize,
}

#[derive(Debug, Clone)]
struct LegacyProject {
    legacy_id: String,
    name: String,
    project_path: Option<String>,
}

#[derive(Debug, Clone)]
struct LegacyItem {
    project: LegacyProject,
    fingerprint: String,
    title: String,
    kind: ItemKind,
    horizon: ItemHorizon,
    priority: i32,
    source_ref: String,
    occurred_at: String,
}

pub fn import_legacy_if_needed(
    database: &Database,
    vault: &Path,
    database_was_created: bool,
) -> Result<ImportSummary> {
    if !database_was_created || database.meta("legacy_imported_at")?.is_some() {
        return Ok(ImportSummary::default());
    }

    create_pre_import_backup(database, vault)?;
    let continuity_path = vault.join("output").join("continuity.md");
    let todo_path = vault.join("output").join("todo.md");
    let candidates_path = vault.join("output").join("todo-candidates.md");
    let continuity = read_optional(&continuity_path)?;
    let todo = read_optional(&todo_path)?;
    let candidates = read_optional(&candidates_path)?;
    let mut items = parse_continuity(&continuity)?;
    items.extend(parse_todo(&todo, false));
    items.extend(parse_todo(&candidates, true));

    let imported_at = Local::now().to_rfc3339();
    let (_, summary) = database.with_write_transaction(None, |transaction, revision| {
        let mut summary = ImportSummary::default();
        for item in &items {
            let (project_id, inserted) = ensure_project(transaction, &item.project, revision)?;
            summary.projects += usize::from(inserted);
            let item_id = Uuid::new_v4().to_string();
            let occurred_at = DateTime::parse_from_rfc3339(&item.occurred_at)
                .map(|value| value.with_timezone(&Utc))
                .unwrap_or_else(|_| Utc::now());
            let score = calculate_activity(Utc::now(), Some(occurred_at), 1, item.priority).total;
            let changed = transaction.execute(
                "INSERT OR IGNORE INTO items(
                    id, project_id, fingerprint, title, details, kind, horizon, status,
                    priority, activity_score, last_evidence_at, manual_override, pinned,
                    auto_daily, sort_order, closed_at, revision
                 ) VALUES (?1, ?2, ?3, ?4, '', ?5, ?6, 'open', ?7, ?8, ?9, 0, 0, 0, 0, NULL, ?10)",
                params![
                    item_id,
                    project_id,
                    item.fingerprint,
                    item.title,
                    item.kind.as_str(),
                    item.horizon.as_str(),
                    item.priority,
                    score,
                    item.occurred_at,
                    revision
                ],
            )?;
            if changed == 1 {
                summary.items += 1;
                transaction.execute(
                    "INSERT INTO item_evidence(id, item_id, source_ref, summary, short_quote, occurred_at, fingerprint)
                     VALUES (?1, ?2, ?3, ?4, '', ?5, ?6)",
                    params![
                        Uuid::new_v4().to_string(),
                        item_id,
                        item.source_ref,
                        item.title,
                        item.occurred_at,
                        format!("legacy:{}", item.fingerprint)
                    ],
                )?;
                summary.evidence += 1;
            }
        }
        transaction.execute(
            "INSERT INTO meta(key, value) VALUES ('legacy_imported_at', ?1)
             ON CONFLICT(key) DO UPDATE SET value = excluded.value",
            [&imported_at],
        )?;
        Ok(summary)
    })?;
    Ok(summary)
}

fn ensure_project(
    transaction: &Transaction<'_>,
    project: &LegacyProject,
    revision: i64,
) -> Result<(String, bool)> {
    let existing = transaction
        .query_row(
            "SELECT id FROM projects WHERE legacy_id = ?1",
            [&project.legacy_id],
            |row| row.get::<_, String>(0),
        )
        .optional()?;
    if let Some(id) = existing {
        return Ok((id, false));
    }
    let id = Uuid::new_v4().to_string();
    let now = Local::now().to_rfc3339();
    transaction.execute(
        "INSERT INTO projects(
            id, legacy_id, name, status, summary, project_path,
            threshold_override, created_at, updated_at, revision
         ) VALUES (?1, ?2, ?3, ?4, '', ?5, NULL, ?6, ?6, ?7)",
        params![
            id,
            project.legacy_id,
            project.name,
            ProjectStatus::Active.as_str(),
            project.project_path,
            now,
            revision
        ],
    )?;
    Ok((id, true))
}

fn parse_continuity(markdown: &str) -> Result<Vec<LegacyItem>> {
    let project_re = Regex::new(r"<!--\s*continuity:project\s+([^>]+)-->")?;
    let item_re = Regex::new(r"<!--\s*continuity:item\s+([^>]+)-->")?;
    let attribute_re = Regex::new(r#"([A-Za-z_]+)=(\"[^\"]*\"|\S*)"#)?;
    let mut horizon = ItemHorizon::Daily;
    let mut kind = ItemKind::Action;
    let mut project_name = String::new();
    let mut project: Option<LegacyProject> = None;
    let mut pending_bullet: Option<String> = None;
    let mut output = Vec::new();

    for line in markdown.lines() {
        let trimmed = line.trim();
        if trimmed == "## 每日" {
            horizon = ItemHorizon::Daily;
        } else if trimmed == "## 本周" {
            horizon = ItemHorizon::Weekly;
        } else if let Some(name) = trimmed.strip_prefix("### ") {
            project_name = name.trim().to_owned();
            project = None;
        } else if trimmed.starts_with("#### ") {
            kind = if trimmed.contains("确认") {
                ItemKind::Question
            } else {
                ItemKind::Action
            };
        } else if let Some(captures) = project_re.captures(trimmed) {
            let attributes = parse_attributes(&attribute_re, &captures[1]);
            let legacy_id = attributes
                .get("id")
                .cloned()
                .unwrap_or_else(|| slug_fallback(&project_name));
            project = Some(LegacyProject {
                legacy_id,
                name: project_name.clone(),
                project_path: attributes
                    .get("project_path")
                    .filter(|value| !value.is_empty())
                    .cloned(),
            });
        } else if let Some(text) = trimmed.strip_prefix("- ") {
            pending_bullet = Some(text.trim().to_owned());
        } else if let Some(captures) = item_re.captures(trimmed) {
            let Some(active_project) = project.clone() else {
                continue;
            };
            let Some(title) = pending_bullet.take() else {
                continue;
            };
            let attributes = parse_attributes(&attribute_re, &captures[1]);
            let fingerprint = attributes
                .get("id")
                .cloned()
                .unwrap_or_else(|| slug_fallback(&title));
            let source_ref = attributes
                .get("sources")
                .cloned()
                .unwrap_or_else(|| "legacy:output/continuity.md".to_owned());
            let occurred_at = attributes
                .get("last_evidence")
                .and_then(|value| date_to_rfc3339(value))
                .unwrap_or_else(|| Local::now().to_rfc3339());
            let priority = if attributes.get("state").is_some_and(|value| value == "full") {
                14
            } else {
                8
            };
            output.push(LegacyItem {
                project: active_project,
                fingerprint,
                title,
                kind,
                horizon,
                priority,
                source_ref,
                occurred_at,
            });
        }
    }
    Ok(output)
}

fn parse_todo(markdown: &str, candidates: bool) -> Vec<LegacyItem> {
    let project = LegacyProject {
        legacy_id: if candidates {
            "legacy-todo-candidates".to_owned()
        } else {
            "legacy-todo".to_owned()
        },
        name: if candidates {
            "Todo 候选".to_owned()
        } else {
            "今日整理".to_owned()
        },
        project_path: None,
    };
    markdown
        .lines()
        .filter_map(|line| {
            let trimmed = line.trim();
            let title = trimmed
                .strip_prefix("- [ ] ")
                .or_else(|| candidates.then(|| trimmed.strip_prefix("- ")).flatten())?;
            if title.is_empty() {
                return None;
            }
            Some(LegacyItem {
                project: project.clone(),
                fingerprint: slug_fallback(title),
                title: title.to_owned(),
                kind: ItemKind::Action,
                horizon: if candidates {
                    ItemHorizon::Later
                } else {
                    ItemHorizon::Daily
                },
                priority: if candidates { 5 } else { 12 },
                source_ref: if candidates {
                    "legacy:output/todo-candidates.md".to_owned()
                } else {
                    "legacy:output/todo.md".to_owned()
                },
                occurred_at: Local::now().to_rfc3339(),
            })
        })
        .collect()
}

fn parse_attributes(regex: &Regex, input: &str) -> std::collections::HashMap<String, String> {
    regex
        .captures_iter(input)
        .map(|captures| {
            let value = captures[2].trim_matches('"').to_owned();
            (captures[1].to_owned(), value)
        })
        .collect()
}

fn date_to_rfc3339(value: &str) -> Option<String> {
    let date = NaiveDate::parse_from_str(value, "%Y-%m-%d").ok()?;
    Local
        .from_local_datetime(&date.and_hms_opt(12, 0, 0)?)
        .single()
        .map(|value| value.to_rfc3339())
}

fn slug_fallback(value: &str) -> String {
    let normalized = value
        .chars()
        .map(|character| {
            if character.is_ascii_alphanumeric() {
                character.to_ascii_lowercase()
            } else {
                '-'
            }
        })
        .collect::<String>();
    let collapsed = Regex::new("-+").unwrap().replace_all(&normalized, "-");
    let trimmed = collapsed.trim_matches('-');
    if trimmed.is_empty() {
        Uuid::new_v4().to_string()
    } else {
        trimmed.to_owned()
    }
}

fn create_pre_import_backup(database: &Database, vault: &Path) -> Result<()> {
    let Some(parent) = database.path().parent() else {
        return Ok(());
    };
    let directory = parent
        .join("legacy-import-backup")
        .join(Local::now().format("%Y%m%d-%H%M%S").to_string());
    fs::create_dir_all(&directory)?;
    for name in [
        "todo.md",
        "todo-candidates.md",
        "todo-archive.md",
        "continuity.md",
    ] {
        let source = vault.join("output").join(name);
        if source.exists() {
            fs::copy(&source, directory.join(name))
                .with_context(|| format!("备份旧数据失败：{}", source.display()))?;
        }
    }
    Ok(())
}

fn read_optional(path: &Path) -> Result<String> {
    if !path.exists() {
        return Ok(String::new());
    }
    fs::read_to_string(path).with_context(|| format!("读取旧数据失败：{}", path.display()))
}

pub fn backup_database(database: &Database) -> Result<Option<String>> {
    if !database.path().exists() {
        return Ok(None);
    }
    let Some(parent) = database.path().parent() else {
        return Ok(None);
    };
    let directory = parent.join("backups");
    fs::create_dir_all(&directory)?;
    let path = directory.join(format!(
        "continuity-{}.db",
        Local::now().format("%Y%m%d-%H%M%S")
    ));
    database.backup_to(&path)?;

    let mut backups = fs::read_dir(&directory)?
        .filter_map(Result::ok)
        .filter(|entry| entry.path().extension().is_some_and(|value| value == "db"))
        .collect::<Vec<_>>();
    backups.sort_by_key(|entry| entry.file_name());
    let remove_count = backups.len().saturating_sub(7);
    for entry in backups.into_iter().take(remove_count) {
        fs::remove_file(entry.path())?;
    }
    Ok(Some(path.display().to_string()))
}

pub fn rebuild_database_from_markdown(
    input: DatabaseRebuildInput,
) -> Result<DatabaseRebuildResult> {
    let database_path = input
        .database_path
        .map(PathBuf::from)
        .map(Ok)
        .unwrap_or_else(Database::default_path)?;
    let vault = input
        .vault_path
        .map(PathBuf::from)
        .unwrap_or_else(|| PathBuf::from(DEFAULT_VAULT));
    let parent = database_path
        .parent()
        .ok_or_else(|| anyhow::anyhow!("数据库路径缺少父目录：{}", database_path.display()))?;
    fs::create_dir_all(parent)?;

    let temporary_path = parent.join(format!(".continuity-rebuild-{}.db", Uuid::new_v4()));
    let preview = ContinuityService::open(&temporary_path, &vault)
        .with_context(|| format!("从 Markdown 构建临时数据库失败：{}", vault.display()))?;
    let status = preview.status()?;
    let temporary_connection = preview.database().connection()?;
    temporary_connection.execute_batch("PRAGMA wal_checkpoint(TRUNCATE);")?;
    drop(temporary_connection);
    drop(preview);

    if !input.apply {
        remove_database_files(&temporary_path)?;
        return Ok(DatabaseRebuildResult {
            applied: false,
            database_path: database_path.display().to_string(),
            backup_path: None,
            recovery_path: None,
            revision: status.revision,
            projects: status.projects,
            items: status.items,
            candidates: status.candidates,
            milestones: status.milestones,
            warnings: vec!["仅完成预演，未替换现有数据库".to_owned()],
        });
    }

    let mut backup_path = None;
    let mut recovery_path = None;
    if database_path.exists() {
        let opened = Database::open(&database_path)?;
        backup_path = backup_database(&opened.database)?;
        let connection = opened.database.connection()?;
        connection.execute_batch("PRAGMA wal_checkpoint(TRUNCATE);")?;
        drop(connection);
        drop(opened);

        let recovery_directory = parent.join("rebuild-backups");
        fs::create_dir_all(&recovery_directory)?;
        let recovery = recovery_directory.join(format!(
            "continuity-before-rebuild-{}.db",
            Local::now().format("%Y%m%d-%H%M%S")
        ));
        fs::rename(&database_path, &recovery)
            .with_context(|| "无法移动现有数据库；请先退出正在运行的 Continuity 窗口后重试")?;
        recovery_path = Some(recovery.display().to_string());
        remove_sidecars(&database_path)?;

        if let Err(error) = fs::rename(&temporary_path, &database_path) {
            let _ = fs::rename(&recovery, &database_path);
            return Err(error).context("替换数据库失败，已尝试恢复原数据库");
        }
    } else {
        fs::rename(&temporary_path, &database_path)?;
    }
    remove_sidecars(&temporary_path)?;

    Ok(DatabaseRebuildResult {
        applied: true,
        database_path: database_path.display().to_string(),
        backup_path,
        recovery_path,
        revision: status.revision,
        projects: status.projects,
        items: status.items,
        candidates: status.candidates,
        milestones: status.milestones,
        warnings: Vec::new(),
    })
}

fn remove_database_files(path: &Path) -> Result<()> {
    if path.exists() {
        fs::remove_file(path)?;
    }
    remove_sidecars(path)
}

fn remove_sidecars(path: &Path) -> Result<()> {
    for suffix in ["-wal", "-shm"] {
        let sidecar = PathBuf::from(format!("{}{suffix}", path.display()));
        if sidecar.exists() {
            fs::remove_file(sidecar)?;
        }
    }
    Ok(())
}
