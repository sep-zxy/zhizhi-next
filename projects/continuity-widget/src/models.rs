use serde::{Deserialize, Serialize};
use std::{fmt, str::FromStr};

macro_rules! string_enum {
    ($name:ident { $($variant:ident => $value:literal),+ $(,)? }) => {
        #[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
        #[serde(rename_all = "lowercase")]
        pub enum $name { $($variant),+ }

        impl $name {
            pub const fn as_str(self) -> &'static str {
                match self { $(Self::$variant => $value),+ }
            }
        }

        impl fmt::Display for $name {
            fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
                f.write_str(self.as_str())
            }
        }

        impl FromStr for $name {
            type Err = String;
            fn from_str(value: &str) -> Result<Self, Self::Err> {
                match value { $($value => Ok(Self::$variant),)+ _ => Err(format!("未知枚举值：{value}")) }
            }
        }
    };
}

string_enum!(ProjectStatus {
    Candidate => "candidate",
    Active => "active",
    Paused => "paused",
    Completed => "completed",
    Archived => "archived"
});
string_enum!(MaturityStage {
    Idea => "idea",
    Defined => "defined",
    Executing => "executing",
    Validating => "validating",
    Mature => "mature"
});
string_enum!(ItemKind { Question => "question", Action => "action" });
string_enum!(ItemHorizon { Daily => "daily", Weekly => "weekly", Later => "later" });
string_enum!(ItemStatus { Open => "open", Done => "done", Dismissed => "dismissed" });
string_enum!(MilestoneStatus {
    Planned => "planned",
    InProgress => "in_progress",
    Completed => "completed",
    Cancelled => "cancelled"
});
string_enum!(ProgressState { Unplanned => "unplanned", Planned => "planned" });
string_enum!(SuggestionStatus { Pending => "pending", Applied => "applied", Rejected => "rejected" });
string_enum!(SuggestionKind {
    ItemPatch => "item_patch",
    CompletionCandidate => "completion_candidate",
    TargetResolution => "target_resolution",
    MilestonePatch => "milestone_patch"
});
string_enum!(EventOutcome { Success => "success", Failure => "failure", Cancelled => "cancelled" });
string_enum!(EventDisposition {
    Applied => "applied",
    Suggested => "suggested",
    EvidenceOnly => "evidence_only",
    Reverted => "reverted"
});

impl Default for MaturityStage {
    fn default() -> Self {
        Self::Idea
    }
}

impl Default for MilestoneStatus {
    fn default() -> Self {
        Self::Planned
    }
}

impl Default for SuggestionKind {
    fn default() -> Self {
        Self::ItemPatch
    }
}

#[derive(Debug, Clone, Serialize, Deserialize)]
#[serde(rename_all = "camelCase")]
pub struct Project {
    pub id: String,
    pub legacy_id: Option<String>,
    pub name: String,
    pub status: ProjectStatus,
    pub summary: String,
    #[serde(default)]
    pub goal: String,
    #[serde(default)]
    pub success_criteria: String,
    #[serde(default)]
    pub maturity_stage: MaturityStage,
    #[serde(default)]
    pub maturity_manual_override: bool,
    pub project_path: Option<String>,
    pub threshold_override: Option<i32>,
    pub created_at: String,
    pub updated_at: String,
    pub revision: i64,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
#[serde(rename_all = "camelCase")]
pub struct Item {
    pub id: String,
    pub project_id: String,
    #[serde(default)]
    pub milestone_id: Option<String>,
    pub fingerprint: String,
    pub title: String,
    pub details: String,
    pub kind: ItemKind,
    pub horizon: ItemHorizon,
    pub status: ItemStatus,
    #[serde(default)]
    pub blocking: bool,
    pub priority: i32,
    pub activity_score: i32,
    pub last_evidence_at: Option<String>,
    pub manual_override: bool,
    pub pinned: bool,
    pub auto_daily: bool,
    pub sort_order: i32,
    pub closed_at: Option<String>,
    pub revision: i64,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
#[serde(rename_all = "camelCase")]
pub struct ProjectMilestone {
    pub id: String,
    pub project_id: String,
    pub fingerprint: String,
    pub title: String,
    pub acceptance_criteria: String,
    pub status: MilestoneStatus,
    pub weight: i32,
    pub manual_override: bool,
    pub sort_order: i32,
    pub created_at: String,
    pub updated_at: String,
    pub completed_at: Option<String>,
    pub revision: i64,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
#[serde(rename_all = "camelCase")]
pub struct ProjectProgress {
    pub state: ProgressState,
    pub percent: Option<i32>,
    pub completed_weight: i32,
    pub total_weight: i32,
    pub completed_milestones: i64,
    pub total_milestones: i64,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
#[serde(rename_all = "camelCase")]
pub struct Evidence {
    pub id: String,
    pub item_id: String,
    pub source_ref: String,
    pub summary: String,
    pub short_quote: String,
    pub occurred_at: String,
    pub fingerprint: String,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
#[serde(rename_all = "camelCase")]
pub struct ItemSuggestion {
    pub id: String,
    #[serde(default)]
    pub project_id: String,
    #[serde(default)]
    pub item_id: Option<String>,
    #[serde(default)]
    pub milestone_id: Option<String>,
    #[serde(default)]
    pub kind: SuggestionKind,
    #[serde(default, alias = "batchId")]
    pub source: String,
    #[serde(default)]
    pub event_id: Option<String>,
    pub proposed_json: serde_json::Value,
    pub status: SuggestionStatus,
    pub created_at: String,
    #[serde(default)]
    pub resolved_at: Option<String>,
    #[serde(default)]
    pub revision: i64,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
#[serde(rename_all = "camelCase")]
pub struct ProjectHistory {
    pub id: String,
    pub project_id: String,
    pub revision: i64,
    pub change_kind: String,
    pub snapshot_json: serde_json::Value,
    pub created_at: String,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
#[serde(rename_all = "camelCase")]
pub struct EventReceiptSummary {
    pub source: String,
    pub event_id: String,
    pub event_type: String,
    pub disposition: EventDisposition,
    pub project_id: Option<String>,
    pub item_id: Option<String>,
    pub milestone_id: Option<String>,
    pub applied_revision: i64,
    pub created_at: String,
    pub reverted_at: Option<String>,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
#[serde(rename_all = "camelCase")]
pub struct Settings {
    pub daily_threshold: i32,
    pub now_limit: i32,
    pub poll_seconds: i32,
    pub always_on_top: bool,
    pub auto_snap: bool,
    pub autostart: bool,
}

impl Default for Settings {
    fn default() -> Self {
        Self {
            daily_threshold: 30,
            now_limit: 3,
            poll_seconds: 1,
            always_on_top: true,
            auto_snap: true,
            autostart: false,
        }
    }
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "camelCase")]
pub struct WindowState {
    pub label: String,
    pub x: i32,
    pub y: i32,
    pub width: i32,
    pub height: i32,
    pub snap_edge: Option<String>,
    pub height_mode: String,
    pub monitor_name: Option<String>,
}

#[derive(Debug, Clone, Serialize, Deserialize, Default)]
#[serde(rename_all = "camelCase")]
pub struct ActivityBreakdown {
    pub recency: i32,
    pub frequency: i32,
    pub priority: i32,
    pub total: i32,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
#[serde(rename_all = "camelCase")]
pub struct ItemView {
    #[serde(flatten)]
    pub item: Item,
    pub evidence: Vec<Evidence>,
    pub suggestions: Vec<ItemSuggestion>,
    pub activity: ActivityBreakdown,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
#[serde(rename_all = "camelCase")]
pub struct ProjectView {
    #[serde(flatten)]
    pub project: Project,
    pub activity_score: i32,
    pub progress: ProjectProgress,
    pub milestones: Vec<ProjectMilestone>,
    pub items: Vec<ItemView>,
    pub history: Vec<ProjectHistory>,
    pub events: Vec<EventReceiptSummary>,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
#[serde(rename_all = "camelCase")]
pub struct ProjectDashboardView {
    pub project: Project,
    pub progress: ProjectProgress,
    pub open_todo_count: i64,
    pub open_question_count: i64,
    pub blocking_count: i64,
    pub pending_suggestion_count: i64,
    pub next_items: Vec<ItemView>,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
#[serde(rename_all = "camelCase")]
pub struct DailyProjectView {
    pub project: Project,
    pub activity_score: i32,
    pub items: Vec<ItemView>,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
#[serde(rename_all = "camelCase")]
pub struct WeeklyProjectView {
    pub project: Project,
    pub activity_score: i32,
    pub summary: String,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
#[serde(rename_all = "camelCase")]
pub struct DashboardView {
    pub revision: i64,
    pub settings: Settings,
    pub projects: Vec<ProjectDashboardView>,
    pub now: Vec<ItemView>,
    pub daily: Vec<DailyProjectView>,
    pub weekly: Vec<WeeklyProjectView>,
    pub candidate_count: i64,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
#[serde(rename_all = "camelCase")]
pub struct EditorView {
    pub revision: i64,
    pub settings: Settings,
    pub projects: Vec<ProjectView>,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
#[serde(rename_all = "camelCase")]
pub struct ProjectPreview {
    pub revision: i64,
    pub project: ProjectView,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
#[serde(rename_all = "camelCase")]
pub struct ProjectRefInput {
    pub id: Option<String>,
    pub legacy_id: Option<String>,
    #[serde(default)]
    pub name: String,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
#[serde(rename_all = "camelCase")]
pub struct SourceInput {
    #[serde(rename = "ref")]
    pub source_ref: String,
    pub summary: String,
    #[serde(default)]
    pub short_quote: String,
    pub occurred_at: String,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
#[serde(rename_all = "camelCase")]
pub struct BatchInput {
    pub id: String,
    pub kind: String,
    #[serde(default)]
    pub source_ref: String,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
#[serde(rename_all = "camelCase")]
pub struct ProjectUpsertInput {
    pub project: ProjectRefInput,
    #[serde(default)]
    pub summary: String,
    #[serde(default)]
    pub goal: String,
    #[serde(default)]
    pub success_criteria: String,
    pub project_path: Option<String>,
    pub threshold_override: Option<i32>,
    pub batch: Option<BatchInput>,
    pub expected_revision: Option<i64>,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
#[serde(rename_all = "camelCase")]
pub struct ItemInput {
    pub id: Option<String>,
    pub fingerprint: String,
    pub title: String,
    #[serde(default)]
    pub details: String,
    pub kind: ItemKind,
    pub horizon: ItemHorizon,
    #[serde(default)]
    pub priority: i32,
    pub source: Option<SourceInput>,
    #[serde(default)]
    pub pinned: bool,
    pub milestone_id: Option<String>,
    #[serde(default)]
    pub blocking: bool,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
#[serde(rename_all = "camelCase")]
pub struct ItemUpsertInput {
    pub project: ProjectRefInput,
    pub item: ItemInput,
    pub batch: Option<BatchInput>,
    pub expected_revision: Option<i64>,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
#[serde(rename_all = "camelCase")]
pub struct ProjectPatchInput {
    pub id: String,
    pub name: Option<String>,
    pub summary: Option<String>,
    pub goal: Option<String>,
    pub success_criteria: Option<String>,
    pub maturity_stage: Option<MaturityStage>,
    pub status: Option<ProjectStatus>,
    pub project_path: Option<Option<String>>,
    pub threshold_override: Option<Option<i32>>,
    pub expected_revision: Option<i64>,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
#[serde(rename_all = "camelCase")]
pub struct ItemPatchInput {
    pub id: String,
    pub title: Option<String>,
    pub details: Option<String>,
    pub kind: Option<ItemKind>,
    pub horizon: Option<ItemHorizon>,
    pub status: Option<ItemStatus>,
    pub priority: Option<i32>,
    pub pinned: Option<bool>,
    pub milestone_id: Option<Option<String>>,
    pub blocking: Option<bool>,
    pub sort_order: Option<i32>,
    pub expected_revision: Option<i64>,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
#[serde(rename_all = "camelCase")]
pub struct MilestoneUpsertInput {
    pub id: Option<String>,
    pub project_id: String,
    pub fingerprint: String,
    pub title: String,
    #[serde(default)]
    pub acceptance_criteria: String,
    #[serde(default)]
    pub status: MilestoneStatus,
    #[serde(default = "default_milestone_weight")]
    pub weight: i32,
    #[serde(default)]
    pub sort_order: i32,
    pub expected_revision: Option<i64>,
}

fn default_milestone_weight() -> i32 {
    1
}

#[derive(Debug, Clone, Serialize, Deserialize)]
#[serde(rename_all = "camelCase")]
pub struct MilestonePatchInput {
    pub id: String,
    pub title: Option<String>,
    pub acceptance_criteria: Option<String>,
    pub status: Option<MilestoneStatus>,
    pub weight: Option<i32>,
    pub sort_order: Option<i32>,
    pub expected_revision: Option<i64>,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
#[serde(rename_all = "camelCase")]
pub struct EntityCommandInput {
    pub id: String,
    pub expected_revision: Option<i64>,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
#[serde(rename_all = "camelCase")]
pub struct QuickAddInput {
    pub title: String,
    pub project_id: Option<String>,
    pub expected_revision: Option<i64>,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
#[serde(rename_all = "camelCase")]
pub struct SettingsPatchInput {
    pub daily_threshold: Option<i32>,
    pub now_limit: Option<i32>,
    pub always_on_top: Option<bool>,
    pub auto_snap: Option<bool>,
    pub autostart: Option<bool>,
    pub expected_revision: Option<i64>,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
#[serde(rename_all = "camelCase")]
pub struct SummarizeInput {
    pub project: ProjectUpsertInput,
    #[serde(default)]
    pub items: Vec<ItemInput>,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
#[serde(rename_all = "camelCase")]
pub struct SnapshotExportInput {
    pub path: Option<String>,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
#[serde(rename_all = "camelCase")]
pub struct EventProjectInput {
    pub id: Option<String>,
    pub path: Option<String>,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
#[serde(rename_all = "camelCase")]
pub struct EventTargetInput {
    pub item_id: Option<String>,
    pub fingerprint: Option<String>,
    pub milestone_id: Option<String>,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
#[serde(rename_all = "camelCase")]
pub struct EventEvidenceInput {
    pub source_ref: String,
    pub summary: String,
    #[serde(default)]
    pub short_quote: String,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
#[serde(rename_all = "camelCase")]
pub struct ApplyEventInput {
    pub version: i32,
    pub source: String,
    pub event_id: String,
    pub event_type: String,
    pub occurred_at: String,
    pub outcome: EventOutcome,
    pub project: EventProjectInput,
    pub target: EventTargetInput,
    pub evidence: EventEvidenceInput,
    pub expected_revision: Option<i64>,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
#[serde(rename_all = "camelCase")]
pub struct EventKeyInput {
    pub source: String,
    pub event_id: String,
    pub expected_revision: Option<i64>,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
#[serde(rename_all = "camelCase")]
pub struct EventApplyResult {
    pub disposition: EventDisposition,
    pub replayed: bool,
    pub source: String,
    pub event_id: String,
    pub project_id: Option<String>,
    pub item_id: Option<String>,
    pub milestone_id: Option<String>,
    pub project_progress: Option<ProjectProgress>,
    pub suggestion_id: Option<String>,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
#[serde(rename_all = "camelCase")]
pub struct SuggestionDecisionInput {
    pub id: String,
    pub expected_revision: Option<i64>,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
#[serde(rename_all = "camelCase")]
pub struct EncodingRepairInput {
    pub source_path: String,
    #[serde(default)]
    pub apply: bool,
    pub expected_revision: Option<i64>,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
#[serde(rename_all = "camelCase")]
pub struct EncodingRepairChange {
    pub entity: String,
    pub id: String,
    pub field: String,
    pub before: String,
    pub after: String,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
#[serde(rename_all = "camelCase")]
pub struct EncodingRepairResult {
    pub matched: usize,
    pub repairable: usize,
    pub skipped: usize,
    pub changes: Vec<EncodingRepairChange>,
    pub backup_path: Option<String>,
    pub applied: bool,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
#[serde(rename_all = "camelCase")]
pub struct MutationResult<T> {
    pub revision: i64,
    pub value: T,
    #[serde(default)]
    pub possible_duplicates: Vec<String>,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
#[serde(rename_all = "camelCase")]
pub struct DatabaseStatus {
    pub database_path: String,
    pub revision: i64,
    pub schema_version: i64,
    pub projects: i64,
    pub items: i64,
    pub candidates: i64,
    pub milestones: i64,
    pub pending_suggestions: i64,
    pub pending_outbox: i64,
    pub legacy_imported_at: Option<String>,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
#[serde(rename_all = "camelCase")]
pub struct DatabaseRebuildInput {
    pub vault_path: Option<String>,
    pub database_path: Option<String>,
    #[serde(default)]
    pub apply: bool,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
#[serde(rename_all = "camelCase")]
pub struct DatabaseRebuildResult {
    pub applied: bool,
    pub database_path: String,
    pub backup_path: Option<String>,
    pub recovery_path: Option<String>,
    pub revision: i64,
    pub projects: i64,
    pub items: i64,
    pub candidates: i64,
    pub milestones: i64,
    pub warnings: Vec<String>,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
#[serde(rename_all = "camelCase")]
pub struct SnapshotResult {
    pub path: String,
    pub revision: i64,
    pub changed: bool,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
#[serde(rename_all = "camelCase")]
pub struct RevisionUpdate {
    pub revision: i64,
    pub changed: bool,
    pub dashboard: Option<DashboardView>,
}
