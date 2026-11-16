use anyhow::{Context, Result, anyhow, bail};
use chrono::Local;
use rusqlite::{Connection, MAIN_DB, OptionalExtension, Transaction, TransactionBehavior, params};
use std::{
    env, fs,
    path::{Path, PathBuf},
};

pub const SCHEMA_VERSION: i64 = 2;

#[derive(Debug, Clone)]
pub struct Database {
    path: PathBuf,
}

#[derive(Debug, Clone)]
pub struct DatabaseOpen {
    pub database: Database,
    pub created: bool,
}

impl Database {
    pub fn default_path() -> Result<PathBuf> {
        let local = env::var_os("LOCALAPPDATA")
            .map(PathBuf::from)
            .ok_or_else(|| anyhow!("LOCALAPPDATA 未设置"))?;
        Ok(local
            .join("Zxy")
            .join("ContinuityWidget")
            .join("continuity.db"))
    }

    pub fn open_default() -> Result<DatabaseOpen> {
        Self::open(Self::default_path()?)
    }

    pub fn open(path: impl Into<PathBuf>) -> Result<DatabaseOpen> {
        let path = path.into();
        let created = !path.exists();
        if let Some(parent) = path.parent() {
            fs::create_dir_all(parent)
                .with_context(|| format!("创建数据库目录失败：{}", parent.display()))?;
        }
        let database = Self { path };
        let connection = database.connection()?;
        database.initialize(&connection)?;
        Ok(DatabaseOpen { database, created })
    }

    pub fn path(&self) -> &Path {
        &self.path
    }

    pub fn connection(&self) -> Result<Connection> {
        let connection = Connection::open(&self.path)
            .with_context(|| format!("打开数据库失败：{}", self.path.display()))?;
        connection.execute_batch(
            "PRAGMA foreign_keys = ON;
             PRAGMA journal_mode = WAL;
             PRAGMA synchronous = NORMAL;
             PRAGMA busy_timeout = 3000;",
        )?;
        Ok(connection)
    }

    fn initialize(&self, connection: &Connection) -> Result<()> {
        connection.execute_batch(
            "CREATE TABLE IF NOT EXISTS meta (
                 key TEXT PRIMARY KEY,
                 value TEXT NOT NULL
             );",
        )?;
        let version = connection
            .query_row(
                "SELECT value FROM meta WHERE key = 'schema_version'",
                [],
                |row| row.get::<_, String>(0),
            )
            .optional()?
            .map(|value| value.parse::<i64>())
            .transpose()
            .context("meta.schema_version 不是整数")?;

        match version {
            None => self.create_v2_schema(connection)?,
            Some(1) => {
                self.backup_before_migration(1)?;
                self.migrate_v1_to_v2(connection)?;
            }
            Some(SCHEMA_VERSION) => self.ensure_v2_indexes(connection)?,
            Some(other) if other > SCHEMA_VERSION => {
                bail!("database_newer:{other}:{SCHEMA_VERSION}")
            }
            Some(other) => bail!("unsupported_schema:{other}"),
        }
        connection.execute("INSERT OR IGNORE INTO settings(id) VALUES (1)", [])?;
        Ok(())
    }

    fn create_v2_schema(&self, connection: &Connection) -> Result<()> {
        connection.execute_batch(V2_SCHEMA)?;
        connection.execute(
            "INSERT INTO meta(key, value) VALUES ('schema_version', ?1)",
            [SCHEMA_VERSION.to_string()],
        )?;
        connection.execute("INSERT INTO meta(key, value) VALUES ('revision', '0')", [])?;
        connection.execute("INSERT OR IGNORE INTO settings(id) VALUES (1)", [])?;
        Ok(())
    }

    fn migrate_v1_to_v2(&self, connection: &Connection) -> Result<()> {
        connection.execute_batch("BEGIN IMMEDIATE")?;
        let migration = connection.execute_batch(
            "ALTER TABLE projects ADD COLUMN goal TEXT NOT NULL DEFAULT '';
             ALTER TABLE projects ADD COLUMN success_criteria TEXT NOT NULL DEFAULT '';
             ALTER TABLE projects ADD COLUMN maturity_stage TEXT NOT NULL DEFAULT 'idea'
               CHECK(maturity_stage IN ('idea','defined','executing','validating','mature'));
             ALTER TABLE projects ADD COLUMN maturity_manual_override INTEGER NOT NULL DEFAULT 0;

             CREATE TABLE project_milestones (
                 id TEXT PRIMARY KEY,
                 project_id TEXT NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
                 fingerprint TEXT NOT NULL,
                 title TEXT NOT NULL,
                 acceptance_criteria TEXT NOT NULL DEFAULT '',
                 status TEXT NOT NULL CHECK(status IN ('planned','in_progress','completed','cancelled')),
                 weight INTEGER NOT NULL DEFAULT 1 CHECK(weight BETWEEN 1 AND 100),
                 manual_override INTEGER NOT NULL DEFAULT 0,
                 sort_order INTEGER NOT NULL DEFAULT 0,
                 created_at TEXT NOT NULL,
                 updated_at TEXT NOT NULL,
                 completed_at TEXT,
                 revision INTEGER NOT NULL,
                 UNIQUE(project_id, fingerprint)
             );

             ALTER TABLE items ADD COLUMN milestone_id TEXT REFERENCES project_milestones(id) ON DELETE SET NULL;
             ALTER TABLE items ADD COLUMN blocking INTEGER NOT NULL DEFAULT 0;

             CREATE TABLE suggestions (
                 id TEXT PRIMARY KEY,
                 project_id TEXT NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
                 item_id TEXT REFERENCES items(id) ON DELETE CASCADE,
                 milestone_id TEXT REFERENCES project_milestones(id) ON DELETE CASCADE,
                 kind TEXT NOT NULL CHECK(kind IN ('item_patch','completion_candidate','target_resolution','milestone_patch')),
                 source TEXT NOT NULL,
                 event_id TEXT,
                 proposed_json TEXT NOT NULL,
                 status TEXT NOT NULL CHECK(status IN ('pending','applied','rejected')),
                 created_at TEXT NOT NULL,
                 resolved_at TEXT,
                 revision INTEGER NOT NULL
             );
             INSERT INTO suggestions(
                 id, project_id, item_id, milestone_id, kind, source, event_id,
                 proposed_json, status, created_at, resolved_at, revision
             )
             SELECT s.id, i.project_id, s.item_id, NULL, 'item_patch', s.batch_id, NULL,
                    s.proposed_json, s.status, s.created_at,
                    CASE WHEN s.status = 'pending' THEN NULL ELSE s.created_at END,
                    i.revision
             FROM item_suggestions s JOIN items i ON i.id = s.item_id;
             DROP TABLE item_suggestions;

             CREATE TABLE event_receipts (
                 source TEXT NOT NULL,
                 event_id TEXT NOT NULL,
                 event_type TEXT NOT NULL,
                 payload_hash TEXT NOT NULL,
                 payload_json TEXT NOT NULL,
                 disposition TEXT NOT NULL CHECK(disposition IN ('applied','suggested','evidence_only','reverted')),
                 project_id TEXT REFERENCES projects(id) ON DELETE SET NULL,
                 item_id TEXT REFERENCES items(id) ON DELETE SET NULL,
                 milestone_id TEXT REFERENCES project_milestones(id) ON DELETE SET NULL,
                 before_json TEXT NOT NULL,
                 result_json TEXT NOT NULL,
                 applied_revision INTEGER NOT NULL,
                 created_at TEXT NOT NULL,
                 reverted_at TEXT,
                 PRIMARY KEY(source, event_id)
             );
             CREATE TABLE event_outbox (
                 id TEXT PRIMARY KEY,
                 source TEXT NOT NULL,
                 event_id TEXT NOT NULL,
                 topic TEXT NOT NULL CHECK(topic IN ('snapshot_export','progress_changed')),
                 payload_json TEXT NOT NULL,
                 status TEXT NOT NULL CHECK(status IN ('pending','delivered','failed')),
                 attempts INTEGER NOT NULL DEFAULT 0,
                 next_attempt_at TEXT NOT NULL,
                 last_error TEXT,
                 created_at TEXT NOT NULL,
                 delivered_at TEXT,
                 FOREIGN KEY(source, event_id) REFERENCES event_receipts(source, event_id) ON DELETE CASCADE
             );
             CREATE INDEX idx_milestones_project_status ON project_milestones(project_id, status, sort_order);
             CREATE INDEX idx_items_milestone_status ON items(milestone_id, status, blocking);
             CREATE INDEX idx_suggestions_project_status ON suggestions(project_id, status, created_at DESC);
             CREATE INDEX idx_receipts_project_time ON event_receipts(project_id, created_at DESC);
             CREATE INDEX idx_outbox_status_time ON event_outbox(status, next_attempt_at);
             UPDATE meta SET value = '2' WHERE key = 'schema_version';",
        );
        match migration {
            Ok(()) => {
                connection.execute_batch("COMMIT")?;
                Ok(())
            }
            Err(error) => {
                let _ = connection.execute_batch("ROLLBACK");
                Err(error.into())
            }
        }
    }

    fn ensure_v2_indexes(&self, connection: &Connection) -> Result<()> {
        connection.execute_batch(
            "CREATE INDEX IF NOT EXISTS idx_milestones_project_status ON project_milestones(project_id, status, sort_order);
             CREATE INDEX IF NOT EXISTS idx_items_milestone_status ON items(milestone_id, status, blocking);
             CREATE INDEX IF NOT EXISTS idx_suggestions_project_status ON suggestions(project_id, status, created_at DESC);
             CREATE INDEX IF NOT EXISTS idx_receipts_project_time ON event_receipts(project_id, created_at DESC);
             CREATE INDEX IF NOT EXISTS idx_outbox_status_time ON event_outbox(status, next_attempt_at);",
        )?;
        Ok(())
    }

    fn backup_before_migration(&self, from_version: i64) -> Result<PathBuf> {
        let parent = self
            .path
            .parent()
            .ok_or_else(|| anyhow!("数据库没有父目录"))?;
        let target = parent.join("migration-backups").join(format!(
            "continuity-v{from_version}-{}.db",
            Local::now().format("%Y%m%d-%H%M%S")
        ));
        self.backup_to(&target)?;
        Ok(target)
    }

    pub fn revision(&self) -> Result<i64> {
        let connection = self.connection()?;
        meta_i64(&connection, "revision")
    }

    pub fn schema_version(&self) -> Result<i64> {
        let connection = self.connection()?;
        meta_i64(&connection, "schema_version")
    }

    pub fn meta(&self, key: &str) -> Result<Option<String>> {
        let connection = self.connection()?;
        Ok(connection
            .query_row("SELECT value FROM meta WHERE key = ?1", [key], |row| {
                row.get(0)
            })
            .optional()?)
    }

    pub fn set_meta_without_revision(&self, key: &str, value: &str) -> Result<()> {
        let connection = self.connection()?;
        connection.execute(
            "INSERT INTO meta(key, value) VALUES (?1, ?2)
             ON CONFLICT(key) DO UPDATE SET value = excluded.value",
            params![key, value],
        )?;
        Ok(())
    }

    pub fn with_write_transaction<T, F>(
        &self,
        expected_revision: Option<i64>,
        action: F,
    ) -> Result<(i64, T)>
    where
        F: FnOnce(&Transaction<'_>, i64) -> Result<T>,
    {
        let mut connection = self.connection()?;
        let transaction = connection.transaction_with_behavior(TransactionBehavior::Immediate)?;
        let current = meta_i64(&transaction, "revision")?;
        if let Some(expected) = expected_revision
            && expected != current
        {
            bail!("revision_conflict:{expected}:{current}");
        }
        let next = current + 1;
        let value = action(&transaction, next)?;
        transaction.execute(
            "UPDATE meta SET value = ?1 WHERE key = 'revision'",
            [next.to_string()],
        )?;
        transaction.commit()?;
        Ok((next, value))
    }

    pub fn with_transaction_without_revision<T, F>(&self, action: F) -> Result<T>
    where
        F: FnOnce(&Transaction<'_>) -> Result<T>,
    {
        let mut connection = self.connection()?;
        let transaction = connection.transaction_with_behavior(TransactionBehavior::Immediate)?;
        let value = action(&transaction)?;
        transaction.commit()?;
        Ok(value)
    }

    pub fn backup_to(&self, path: &Path) -> Result<()> {
        if let Some(parent) = path.parent() {
            fs::create_dir_all(parent)?;
        }
        let connection = self.connection()?;
        connection.backup(MAIN_DB, path, None)?;
        Ok(())
    }
}

fn meta_i64(connection: &Connection, key: &str) -> Result<i64> {
    let value: String =
        connection.query_row("SELECT value FROM meta WHERE key = ?1", [key], |row| {
            row.get(0)
        })?;
    value
        .parse::<i64>()
        .with_context(|| format!("meta.{key} 不是整数"))
}

const V2_SCHEMA: &str = r#"
CREATE TABLE projects (
    id TEXT PRIMARY KEY,
    legacy_id TEXT UNIQUE,
    name TEXT NOT NULL,
    status TEXT NOT NULL CHECK(status IN ('candidate','active','paused','completed','archived')),
    summary TEXT NOT NULL DEFAULT '',
    goal TEXT NOT NULL DEFAULT '',
    success_criteria TEXT NOT NULL DEFAULT '',
    maturity_stage TEXT NOT NULL DEFAULT 'idea' CHECK(maturity_stage IN ('idea','defined','executing','validating','mature')),
    maturity_manual_override INTEGER NOT NULL DEFAULT 0,
    project_path TEXT UNIQUE,
    threshold_override INTEGER,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    revision INTEGER NOT NULL
);
CREATE TABLE project_milestones (
    id TEXT PRIMARY KEY,
    project_id TEXT NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
    fingerprint TEXT NOT NULL,
    title TEXT NOT NULL,
    acceptance_criteria TEXT NOT NULL DEFAULT '',
    status TEXT NOT NULL CHECK(status IN ('planned','in_progress','completed','cancelled')),
    weight INTEGER NOT NULL DEFAULT 1 CHECK(weight BETWEEN 1 AND 100),
    manual_override INTEGER NOT NULL DEFAULT 0,
    sort_order INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    completed_at TEXT,
    revision INTEGER NOT NULL,
    UNIQUE(project_id, fingerprint)
);
CREATE TABLE items (
    id TEXT PRIMARY KEY,
    project_id TEXT NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
    milestone_id TEXT REFERENCES project_milestones(id) ON DELETE SET NULL,
    fingerprint TEXT NOT NULL,
    title TEXT NOT NULL,
    details TEXT NOT NULL DEFAULT '',
    kind TEXT NOT NULL CHECK(kind IN ('question','action')),
    horizon TEXT NOT NULL CHECK(horizon IN ('daily','weekly','later')),
    status TEXT NOT NULL CHECK(status IN ('open','done','dismissed')),
    blocking INTEGER NOT NULL DEFAULT 0,
    priority INTEGER NOT NULL DEFAULT 0 CHECK(priority BETWEEN 0 AND 20),
    activity_score INTEGER NOT NULL DEFAULT 0 CHECK(activity_score BETWEEN 0 AND 100),
    last_evidence_at TEXT,
    manual_override INTEGER NOT NULL DEFAULT 0,
    pinned INTEGER NOT NULL DEFAULT 0,
    auto_daily INTEGER NOT NULL DEFAULT 0,
    sort_order INTEGER NOT NULL DEFAULT 0,
    closed_at TEXT,
    revision INTEGER NOT NULL,
    UNIQUE(project_id, fingerprint)
);
CREATE TABLE item_evidence (
    id TEXT PRIMARY KEY,
    item_id TEXT NOT NULL REFERENCES items(id) ON DELETE CASCADE,
    source_ref TEXT NOT NULL,
    summary TEXT NOT NULL,
    short_quote TEXT NOT NULL DEFAULT '',
    occurred_at TEXT NOT NULL,
    fingerprint TEXT NOT NULL,
    UNIQUE(item_id, fingerprint)
);
CREATE TABLE suggestions (
    id TEXT PRIMARY KEY,
    project_id TEXT NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
    item_id TEXT REFERENCES items(id) ON DELETE CASCADE,
    milestone_id TEXT REFERENCES project_milestones(id) ON DELETE CASCADE,
    kind TEXT NOT NULL CHECK(kind IN ('item_patch','completion_candidate','target_resolution','milestone_patch')),
    source TEXT NOT NULL,
    event_id TEXT,
    proposed_json TEXT NOT NULL,
    status TEXT NOT NULL CHECK(status IN ('pending','applied','rejected')),
    created_at TEXT NOT NULL,
    resolved_at TEXT,
    revision INTEGER NOT NULL
);
CREATE TABLE update_batches (
    id TEXT PRIMARY KEY,
    kind TEXT NOT NULL,
    source_ref TEXT NOT NULL DEFAULT '',
    before_json TEXT NOT NULL,
    created_at TEXT NOT NULL,
    reverted_at TEXT
);
CREATE TABLE project_history (
    id TEXT PRIMARY KEY,
    project_id TEXT NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
    revision INTEGER NOT NULL,
    change_kind TEXT NOT NULL,
    snapshot_json TEXT NOT NULL,
    created_at TEXT NOT NULL
);
CREATE TABLE event_receipts (
    source TEXT NOT NULL,
    event_id TEXT NOT NULL,
    event_type TEXT NOT NULL,
    payload_hash TEXT NOT NULL,
    payload_json TEXT NOT NULL,
    disposition TEXT NOT NULL CHECK(disposition IN ('applied','suggested','evidence_only','reverted')),
    project_id TEXT REFERENCES projects(id) ON DELETE SET NULL,
    item_id TEXT REFERENCES items(id) ON DELETE SET NULL,
    milestone_id TEXT REFERENCES project_milestones(id) ON DELETE SET NULL,
    before_json TEXT NOT NULL,
    result_json TEXT NOT NULL,
    applied_revision INTEGER NOT NULL,
    created_at TEXT NOT NULL,
    reverted_at TEXT,
    PRIMARY KEY(source, event_id)
);
CREATE TABLE event_outbox (
    id TEXT PRIMARY KEY,
    source TEXT NOT NULL,
    event_id TEXT NOT NULL,
    topic TEXT NOT NULL CHECK(topic IN ('snapshot_export','progress_changed')),
    payload_json TEXT NOT NULL,
    status TEXT NOT NULL CHECK(status IN ('pending','delivered','failed')),
    attempts INTEGER NOT NULL DEFAULT 0,
    next_attempt_at TEXT NOT NULL,
    last_error TEXT,
    created_at TEXT NOT NULL,
    delivered_at TEXT,
    FOREIGN KEY(source, event_id) REFERENCES event_receipts(source, event_id) ON DELETE CASCADE
);
CREATE TABLE settings (
    id INTEGER PRIMARY KEY CHECK(id = 1),
    daily_threshold INTEGER NOT NULL DEFAULT 30,
    now_limit INTEGER NOT NULL DEFAULT 3 CHECK(now_limit BETWEEN 1 AND 5),
    poll_seconds INTEGER NOT NULL DEFAULT 1,
    always_on_top INTEGER NOT NULL DEFAULT 1,
    auto_snap INTEGER NOT NULL DEFAULT 1,
    autostart INTEGER NOT NULL DEFAULT 0
);
CREATE TABLE window_state (
    label TEXT PRIMARY KEY,
    x INTEGER NOT NULL,
    y INTEGER NOT NULL,
    width INTEGER NOT NULL,
    height INTEGER NOT NULL,
    snap_edge TEXT,
    height_mode TEXT NOT NULL DEFAULT 'fit',
    monitor_name TEXT
);
CREATE INDEX idx_items_project_status ON items(project_id, status);
CREATE INDEX idx_items_dashboard ON items(status, horizon, pinned, priority);
CREATE INDEX idx_evidence_item_time ON item_evidence(item_id, occurred_at DESC);
CREATE INDEX idx_history_project_revision ON project_history(project_id, revision DESC);
CREATE INDEX idx_milestones_project_status ON project_milestones(project_id, status, sort_order);
CREATE INDEX idx_items_milestone_status ON items(milestone_id, status, blocking);
CREATE INDEX idx_suggestions_project_status ON suggestions(project_id, status, created_at DESC);
CREATE INDEX idx_receipts_project_time ON event_receipts(project_id, created_at DESC);
CREATE INDEX idx_outbox_status_time ON event_outbox(status, next_attempt_at);
"#;
