use std::{
    collections::{HashMap, HashSet},
    fs::{self, File, OpenOptions},
    io::Write,
    path::{Component, Path, PathBuf},
    process::Command,
    time::{SystemTime, UNIX_EPOCH},
};

use anyhow::{Context, Result, anyhow, bail};
use chrono::{Datelike, Local, NaiveDate};
use fs2::FileExt;
use regex::Regex;
use serde::{Deserialize, Serialize};
use serde_json::Value as JsonValue;
use serde_yaml::{Mapping, Value as YamlValue};
use walkdir::WalkDir;

#[allow(dead_code)]
#[derive(Debug, Clone, Deserialize, Serialize)]
#[serde(rename_all = "camelCase")]
pub struct Registry {
    pub version: u32,
    pub date_format: String,
    pub tag_order: Vec<String>,
    pub semantic_tags: Vec<String>,
    #[serde(default)]
    pub structural_tags: HashMap<String, Vec<String>>,
    #[serde(default)]
    pub tag_aliases: HashMap<String, String>,
    #[serde(default)]
    pub updated_fields: Vec<String>,
    #[serde(default)]
    pub maintenance_only: Vec<String>,
    #[serde(default)]
    pub placeholder_values: HashMap<String, JsonValue>,
    pub types: HashMap<String, TypeRule>,
}

#[allow(dead_code)]
#[derive(Debug, Clone, Deserialize, Serialize)]
#[serde(rename_all = "camelCase")]
pub struct TypeRule {
    #[serde(default)]
    pub paths: Vec<String>,
    #[serde(default)]
    pub exclude: Vec<String>,
    pub order: Vec<String>,
    pub required: Vec<String>,
    #[serde(default)]
    pub non_empty_when_published: Vec<String>,
    #[serde(default)]
    pub defaults: HashMap<String, JsonValue>,
    #[serde(default)]
    pub enums: HashMap<String, Vec<String>>,
    #[serde(default)]
    pub strict: bool,
    pub archive: Option<String>,
    pub archive_by: Option<String>,
}

#[derive(Clone)]
pub struct AppState {
    pub vault: PathBuf,
    pub registry: Registry,
    pub token: String,
    tool_root: PathBuf,
}

impl AppState {
    pub fn new(vault: PathBuf, registry: Registry, token: String) -> Self {
        let tool_root = PathBuf::from(env!("CARGO_MANIFEST_DIR"));
        Self {
            vault,
            registry,
            token,
            tool_root,
        }
    }
}

#[derive(Debug, Serialize, Clone)]
pub struct Item {
    pub relative: String,
    pub title: String,
    pub kind: String,
    pub tags: Vec<String>,
    pub publish: Option<bool>,
    pub created: Option<String>,
    pub updated: Option<String>,
    pub summary: String,
    pub mtime: u64,
    pub metadata: JsonValue,
}

#[derive(Debug, Serialize)]
pub struct ValidationIssue {
    pub level: String,
    pub path: String,
    pub message: String,
}

#[derive(Debug, Serialize)]
pub struct ValidationReport {
    pub schema_version: u32,
    pub scope: String,
    pub checked: usize,
    pub errors: Vec<ValidationIssue>,
    pub warnings: Vec<ValidationIssue>,
}

#[derive(Debug, Serialize)]
pub struct NormalizeReport {
    pub dry_run: bool,
    pub changed: Vec<String>,
    pub moved: Vec<String>,
    pub conflicts: Vec<String>,
}

#[derive(Debug, Deserialize)]
pub struct CreateRequest {
    pub kind: String,
    pub title: String,
    #[serde(default)]
    pub summary: String,
    #[serde(default)]
    pub tags: Vec<String>,
    pub publish: Option<bool>,
    pub source: Option<String>,
    #[serde(default)]
    pub author: Vec<String>,
    pub published: Option<String>,
    pub category: Option<String>,
    pub software_id: Option<String>,
}

#[derive(Debug, Deserialize)]
pub struct MetadataPatch {
    pub path: String,
    pub expected_mtime: Option<u64>,
    pub fields: serde_json::Map<String, JsonValue>,
}

#[derive(Debug, Deserialize)]
pub struct TagReplaceRequest {
    pub from: String,
    pub to: Option<String>,
    #[serde(default)]
    pub preview: bool,
}

#[derive(Debug, Serialize)]
pub struct TagReplaceReport {
    pub preview: bool,
    pub affected: Vec<String>,
}

#[derive(Debug, Serialize)]
pub struct FillDefaultsReport {
    pub item: Item,
    pub applied: Vec<String>,
    pub unresolved: Vec<String>,
}

struct Document {
    path: PathBuf,
    relative: String,
    kind: String,
    frontmatter: Mapping,
    body: String,
    original: String,
    mtime: u64,
}

pub fn load_registry(vault: &Path) -> Result<Registry> {
    let schema_path = vault.join("SCHEMA.md");
    let text = fs::read_to_string(&schema_path)
        .with_context(|| format!("Cannot read {}", schema_path.display()))?;
    let re = Regex::new(r"(?s)```json vault-schema-registry\s*(\{.*?\})\s*```")?;
    let json = re
        .captures(&text)
        .and_then(|capture| capture.get(1))
        .ok_or_else(|| anyhow!("SCHEMA.md does not contain vault-schema-registry"))?
        .as_str();
    serde_json::from_str(json).context("Invalid vault-schema-registry JSON")
}

pub fn scan_items(vault: &Path, registry: &Registry) -> Result<Vec<Item>> {
    let mut items = Vec::new();
    for path in markdown_files(vault) {
        let Some(kind) = classify(vault, &path) else {
            continue;
        };
        if kind == "index" {
            continue;
        }
        let doc = read_document(vault, &path, kind)?;
        items.push(document_to_item(&doc));
    }
    items.sort_by(|a, b| {
        b.updated
            .cmp(&a.updated)
            .then_with(|| b.created.cmp(&a.created))
            .then_with(|| a.relative.cmp(&b.relative))
    });
    let _ = registry.version;
    Ok(items)
}

pub fn validate_vault(vault: &Path, registry: &Registry, scope: &str) -> Result<ValidationReport> {
    if !matches!(scope, "all" | "changed" | "publish") {
        bail!("Unsupported validation scope `{scope}`; expected all, changed, or publish");
    }
    let changed_paths = (scope == "changed")
        .then(|| changed_markdown_files(vault))
        .transpose()?;
    let mut report = ValidationReport {
        schema_version: registry.version,
        scope: scope.to_string(),
        checked: 0,
        errors: Vec::new(),
        warnings: Vec::new(),
    };
    let date_re = Regex::new(r"^\d{4}-\d{2}-\d{2}$")?;
    let allowed_tags: HashSet<_> = registry.tag_order.iter().cloned().collect();
    let mut raw_outputs = HashMap::<String, String>::new();
    let all_public_raw_outputs = (scope == "changed").then(|| public_raw_outputs(vault));

    for path in markdown_files(vault) {
        let Some(kind) = classify(vault, &path) else {
            continue;
        };
        if kind == "index" {
            continue;
        }
        let path_relative = relative(vault, &path);
        if changed_paths
            .as_ref()
            .is_some_and(|paths| !paths.contains(&path_relative))
        {
            continue;
        }
        let doc = match read_document(vault, &path, kind.clone()) {
            Ok(doc) => doc,
            Err(error) => {
                report
                    .errors
                    .push(issue("error", relative(vault, &path), error.to_string()));
                continue;
            }
        };
        let published = bool_field(&doc.frontmatter, "publish").unwrap_or(false);
        if scope == "publish" && !published {
            continue;
        }
        report.checked += 1;

        let Some(rule) = registry.types.get(&kind) else {
            continue;
        };
        for field in &rule.required {
            if !doc
                .frontmatter
                .contains_key(YamlValue::String(field.clone()))
            {
                report
                    .errors
                    .push(issue("error", &doc.relative, format!("缺少字段 `{field}`")));
            }
        }
        if rule.strict {
            for key in doc.frontmatter.keys().filter_map(YamlValue::as_str) {
                if !rule.order.iter().any(|field| field == key) {
                    report.errors.push(issue(
                        "error",
                        &doc.relative,
                        format!("不允许的字段 `{key}`"),
                    ));
                }
            }
            let actual: Vec<_> = doc
                .frontmatter
                .keys()
                .filter_map(YamlValue::as_str)
                .collect();
            let expected: Vec<_> = rule.order.iter().map(String::as_str).collect();
            if actual != expected {
                report.errors.push(issue(
                    "error",
                    &doc.relative,
                    "frontmatter 字段顺序不符合 Schema",
                ));
            }
        }
        for (field, allowed) in &rule.enums {
            if let Some(value) = string_field(&doc.frontmatter, field) {
                if !allowed.iter().any(|item| item == &value) {
                    report.errors.push(issue(
                        "error",
                        &doc.relative,
                        format!("`{field}` 值 `{value}` 不合法"),
                    ));
                }
            }
        }
        for field in ["created", "updated", "published"] {
            if let Some(value) = string_field(&doc.frontmatter, field) {
                if !value.is_empty()
                    && (!date_re.is_match(&value)
                        || NaiveDate::parse_from_str(&value, "%Y-%m-%d").is_err())
                {
                    report.errors.push(issue(
                        "error",
                        &doc.relative,
                        format!("`{field}` 日期格式不合法"),
                    ));
                }
            }
        }
        let tags = tags_field(&doc.frontmatter);
        for tag in &tags {
            if !allowed_tags.contains(tag) {
                report
                    .errors
                    .push(issue("error", &doc.relative, format!("非法标签 `{tag}`")));
            }
        }
        let semantic_count = tags
            .iter()
            .filter(|tag| registry.semantic_tags.contains(tag))
            .count();
        if semantic_count > 4 {
            report
                .errors
                .push(issue("error", &doc.relative, "语义标签超过 4 个"));
        }
        if kind == "raw" && semantic_count == 0 {
            report
                .warnings
                .push(issue("warning", &doc.relative, "raw 尚未设置语义标签"));
        }
        if kind == "raw"
            && string_field(&doc.frontmatter, "summary")
                .is_some_and(|value| value.chars().count() > 120)
        {
            report
                .errors
                .push(issue("error", &doc.relative, "raw summary 超过 120 个字符"));
        }
        if published {
            for field in &rule.non_empty_when_published {
                if !field_publish_ready(&doc.frontmatter, field, registry) {
                    report.errors.push(issue(
                        "error",
                        &doc.relative,
                        format!("公开文件的 `{field}` 不能为空"),
                    ));
                }
            }
        }
        if kind == "raw" {
            if let Some(created) = string_field(&doc.frontmatter, "created") {
                if let Ok(date) = NaiveDate::parse_from_str(&created, "%Y-%m-%d") {
                    let expected = format!("raw/{:04}-{:02}/", date.year(), date.month());
                    if !doc.relative.starts_with(&expected) {
                        report.errors.push(issue(
                            "error",
                            &doc.relative,
                            format!("应归档到 `{expected}`"),
                        ));
                    }
                }
            }
            if published {
                let stem = path
                    .file_stem()
                    .and_then(|v| v.to_str())
                    .unwrap_or_default();
                let slug = public_slug(stem);
                if let Some(outputs) = &all_public_raw_outputs {
                    if let Some(previous) = outputs
                        .get(&slug)
                        .and_then(|paths| paths.iter().find(|item| *item != &doc.relative))
                    {
                        report.errors.push(issue(
                            "error",
                            &doc.relative,
                            format!("公开文章 slug 与 `{previous}` 冲突：{slug}"),
                        ));
                    }
                } else if let Some(previous) =
                    raw_outputs.insert(slug.clone(), doc.relative.clone())
                {
                    report.errors.push(issue(
                        "error",
                        &doc.relative,
                        format!("公开文章 slug 与 `{previous}` 冲突：{slug}"),
                    ));
                }
            }
        }
    }
    validate_links_and_sources(vault, scope, changed_paths.as_ref(), &mut report)?;
    Ok(report)
}

fn validate_links_and_sources(
    vault: &Path,
    scope: &str,
    changed_paths: Option<&HashSet<String>>,
    report: &mut ValidationReport,
) -> Result<()> {
    let paths = markdown_files(vault);
    let mut documents = Vec::new();
    let mut keys = HashMap::<String, Vec<String>>::new();
    for path in &paths {
        let kind = classify(vault, path).unwrap_or_else(|| "unknown".into());
        let Ok(doc) = read_document(vault, path, kind) else {
            continue;
        };
        if scope == "publish" && !bool_field(&doc.frontmatter, "publish").unwrap_or(false) {
            continue;
        }
        let rel_no_ext = doc
            .relative
            .strip_suffix(".md")
            .unwrap_or(&doc.relative)
            .to_lowercase();
        let stem = path
            .file_stem()
            .and_then(|value| value.to_str())
            .unwrap_or_default()
            .to_lowercase();
        let title = string_field(&doc.frontmatter, "title")
            .unwrap_or_default()
            .to_lowercase();
        for candidate in [rel_no_ext, stem, title]
            .into_iter()
            .filter(|value| !value.is_empty())
        {
            let values = keys.entry(candidate).or_default();
            if !values.contains(&doc.relative) {
                values.push(doc.relative.clone());
            }
        }
        if changed_paths.is_some_and(|paths| !paths.contains(&doc.relative)) {
            continue;
        }
        documents.push(doc);
    }

    let wikilink_re = Regex::new(r"\[\[([^\]|#]+)(?:#[^\]|]+)?(?:\|[^\]]+)?\]\]")?;
    let markdown_image_re = Regex::new(r"!\[[^\]]*\]\(([^\)]+)\)")?;
    let image_exts = ["png", "jpg", "jpeg", "gif", "webp", "svg", "avif"];
    let mut emitted = HashSet::new();
    for doc in &documents {
        for capture in wikilink_re.captures_iter(&doc.original) {
            let target = capture
                .get(1)
                .map(|value| value.as_str())
                .unwrap_or_default()
                .trim();
            let extension = Path::new(target)
                .extension()
                .and_then(|value| value.to_str())
                .unwrap_or_default()
                .to_lowercase();
            if image_exts.contains(&extension.as_str()) {
                if !resolve_local_asset(vault, doc, target).is_some() {
                    push_unique_error(
                        report,
                        &mut emitted,
                        &doc.relative,
                        format!("缺失嵌入资源 `{target}`"),
                    );
                }
                continue;
            }
            if target.starts_with("http://") || target.starts_with("https://") {
                continue;
            }
            let normalized = target
                .trim_start_matches('/')
                .replace('\\', "/")
                .trim_end_matches(".md")
                .to_lowercase();
            let basename = Path::new(&normalized)
                .file_name()
                .and_then(|value| value.to_str())
                .unwrap_or_default()
                .to_string();
            let candidates = keys.get(&normalized).or_else(|| keys.get(&basename));
            match candidates {
                None => push_unique_error(
                    report,
                    &mut emitted,
                    &doc.relative,
                    format!("断开的 Wikilink `{target}`"),
                ),
                Some(values) if values.len() > 1 && !target.contains('/') => {
                    let key = format!("{}|ambiguous|{}", doc.relative, target);
                    if emitted.insert(key) {
                        report.warnings.push(issue(
                            "warning",
                            &doc.relative,
                            format!("歧义 Wikilink `{target}`：{}", values.join(", ")),
                        ));
                    }
                }
                _ => {}
            }
        }
        for capture in markdown_image_re.captures_iter(&doc.original) {
            let target = capture
                .get(1)
                .map(|value| value.as_str())
                .unwrap_or_default()
                .split_whitespace()
                .next()
                .unwrap_or_default()
                .trim_matches(['<', '>']);
            if target.starts_with("http://")
                || target.starts_with("https://")
                || target.starts_with("//")
            {
                continue;
            }
            if resolve_local_asset(vault, doc, target).is_none() {
                push_unique_error(
                    report,
                    &mut emitted,
                    &doc.relative,
                    format!("缺失本地图片 `{target}`"),
                );
            }
        }
        if doc.kind == "wiki" {
            match doc.frontmatter.get(key("sources")) {
                Some(YamlValue::Sequence(values)) => {
                    for value in values.iter().filter_map(YamlValue::as_str) {
                        if value.starts_with("http://") || value.starts_with("https://") {
                            continue;
                        }
                        let clean = value.split('#').next().unwrap_or(value).replace('\\', "/");
                        if (clean.starts_with("daily/")
                            || clean.starts_with("raw/")
                            || clean.starts_with("notes/")
                            || clean.starts_with("wiki/")
                            || clean.starts_with("output/"))
                            && !vault.join(&clean).is_file()
                        {
                            push_unique_error(
                                report,
                                &mut emitted,
                                &doc.relative,
                                format!("不存在的 source `{value}`"),
                            );
                        }
                    }
                }
                _ => push_unique_error(
                    report,
                    &mut emitted,
                    &doc.relative,
                    "Wiki sources 必须为列表".to_string(),
                ),
            }
        }
        if doc.kind == "app-note" {
            if let Some(id) = string_field(&doc.frontmatter, "related_software") {
                if !vault
                    .join("notes")
                    .join("software")
                    .join(format!("{id}.md"))
                    .is_file()
                {
                    push_unique_error(
                        report,
                        &mut emitted,
                        &doc.relative,
                        format!("related_software `{id}` 没有对应软件页"),
                    );
                }
            }
        }
    }
    Ok(())
}

fn changed_markdown_files(vault: &Path) -> Result<HashSet<String>> {
    let commands: &[&[&str]] = &[
        &["diff", "--name-only", "-z", "--diff-filter=ACMR"],
        &[
            "diff",
            "--cached",
            "--name-only",
            "-z",
            "--diff-filter=ACMR",
        ],
        &["ls-files", "--others", "--exclude-standard", "-z"],
    ];
    let mut paths = HashSet::new();
    for args in commands {
        let output = Command::new("git")
            .arg("-C")
            .arg(vault)
            .args(*args)
            .output()
            .context("Failed to execute git for changed validation scope")?;
        if !output.status.success() {
            bail!(
                "Cannot determine changed files: {}",
                String::from_utf8_lossy(&output.stderr).trim()
            );
        }
        for path in parse_null_paths(&output.stdout) {
            if path.to_ascii_lowercase().ends_with(".md") && vault.join(&path).is_file() {
                paths.insert(path);
            }
        }
    }
    Ok(paths)
}

fn parse_null_paths(output: &[u8]) -> Vec<String> {
    output
        .split(|byte| *byte == 0)
        .filter(|path| !path.is_empty())
        .map(|path| String::from_utf8_lossy(path).replace('\\', "/"))
        .collect()
}

fn public_raw_outputs(vault: &Path) -> HashMap<String, Vec<String>> {
    let mut outputs = HashMap::<String, Vec<String>>::new();
    for path in markdown_files(vault) {
        if classify(vault, &path).as_deref() != Some("raw") {
            continue;
        }
        let Ok(doc) = read_document(vault, &path, "raw".into()) else {
            continue;
        };
        if !bool_field(&doc.frontmatter, "publish").unwrap_or(false) {
            continue;
        }
        let stem = path
            .file_stem()
            .and_then(|value| value.to_str())
            .unwrap_or_default();
        outputs
            .entry(public_slug(stem))
            .or_default()
            .push(doc.relative);
    }
    outputs
}

fn resolve_local_asset(vault: &Path, doc: &Document, target: &str) -> Option<PathBuf> {
    let clean = target
        .split('#')
        .next()
        .unwrap_or(target)
        .replace("%20", " ");
    let candidates = [
        doc.path.parent().unwrap_or(vault).join(&clean),
        vault.join(&clean),
        vault.join("assets").join(&clean),
    ];
    candidates
        .into_iter()
        .find(|path| path.is_file())
        .or_else(|| {
            let name = Path::new(&clean).file_name()?;
            WalkDir::new(vault.join("assets"))
                .into_iter()
                .filter_map(Result::ok)
                .find(|entry| entry.file_type().is_file() && entry.file_name() == name)
                .map(|entry| entry.into_path())
        })
}

fn push_unique_error(
    report: &mut ValidationReport,
    emitted: &mut HashSet<String>,
    path: &str,
    message: String,
) {
    let key = format!("{path}|{message}");
    if emitted.insert(key) {
        report.errors.push(issue("error", path, message));
    }
}

pub fn normalize_vault(
    vault: &Path,
    registry: &Registry,
    dry_run: bool,
) -> Result<NormalizeReport> {
    let raw_rule = registry
        .types
        .get("raw")
        .ok_or_else(|| anyhow!("Schema has no raw rule"))?;
    let mut report = NormalizeReport {
        dry_run,
        changed: vec![],
        moved: vec![],
        conflicts: vec![],
    };
    let mut moves = Vec::<(String, String)>::new();
    let files: Vec<_> = WalkDir::new(vault.join("raw"))
        .into_iter()
        .filter_map(Result::ok)
        .filter(|entry| {
            entry.file_type().is_file()
                && entry
                    .path()
                    .extension()
                    .is_some_and(|v| v.eq_ignore_ascii_case("md"))
        })
        .map(|entry| entry.into_path())
        .filter(|path| path.file_name().and_then(|v| v.to_str()) != Some("index.md"))
        .collect();

    for path in files {
        let mut doc = read_document(vault, &path, "raw".to_string())?;
        let effective = normalize_raw_frontmatter(&mut doc.frontmatter, registry, raw_rule)?;
        let cleaned_body = clean_raw_body(&doc.body)?;
        doc.body = cleaned_body;
        let created = string_field(&doc.frontmatter, "created")
            .filter(|v| !v.is_empty())
            .unwrap_or_else(|| file_date(&path));
        set_string(&mut doc.frontmatter, "created", &created);
        if !field_non_empty(&doc.frontmatter, "updated") {
            set_string(&mut doc.frontmatter, "updated", &created);
        }
        if effective {
            set_string(&mut doc.frontmatter, "updated", &today());
        }
        let date = NaiveDate::parse_from_str(&created, "%Y-%m-%d")
            .with_context(|| format!("Invalid created date in {}", doc.relative))?;
        let folder = vault
            .join("raw")
            .join(format!("{:04}-{:02}", date.year(), date.month()));
        let target = folder.join(
            path.file_name()
                .ok_or_else(|| anyhow!("Missing file name"))?,
        );
        let target_rel = relative(vault, &target);
        let rendered = render_document(&doc.frontmatter, &doc.body, raw_rule);
        let content_changed = rendered != normalize_newlines(&doc.original);
        let moving = path != target;
        if moving && target.exists() {
            report
                .conflicts
                .push(format!("{} -> {}", doc.relative, target_rel));
            continue;
        }
        if content_changed {
            report.changed.push(doc.relative.clone());
        }
        if moving {
            report
                .moved
                .push(format!("{} -> {}", doc.relative, target_rel));
            moves.push((doc.relative.clone(), target_rel.clone()));
        }
        if dry_run || (!content_changed && !moving) {
            continue;
        }
        fs::create_dir_all(&folder)?;
        backup_path(vault, &path, Path::new(env!("CARGO_MANIFEST_DIR")))?;
        atomic_write(&target, rendered.as_bytes())?;
        if moving {
            fs::remove_file(&path)?;
        }
    }
    if !dry_run && report.conflicts.is_empty() && !moves.is_empty() {
        update_references(vault, &moves)?;
        remove_empty_raw_dirs(vault)?;
    }
    Ok(report)
}

pub fn create_content(state: &AppState, request: CreateRequest) -> Result<Item> {
    let title = request.title.trim();
    if title.is_empty() {
        bail!("标题不能为空");
    }
    let today = today();
    let tags = normalize_tags(&request.tags, &state.registry);
    let (relative_path, kind, map, body) = match request.kind.as_str() {
        "article" | "raw" => {
            let source = request
                .source
                .filter(|value| !value.trim().is_empty())
                .or_else(|| {
                    state
                        .registry
                        .placeholder_values
                        .get("source")
                        .and_then(JsonValue::as_str)
                        .map(str::to_string)
                })
                .unwrap_or_default();
            let publish = request.publish.unwrap_or(true) && !source.is_empty();
            let relative = format!("raw/{}/{}.md", &today[..7], safe_file_name(title));
            let mut map = Mapping::new();
            set_string(&mut map, "title", title);
            set_string(&mut map, "source", &source);
            set_sequence(&mut map, "author", request.author);
            set_optional_string(&mut map, "published", request.published.as_deref());
            set_string(&mut map, "created", &today);
            set_string(&mut map, "updated", &today);
            set_sequence(&mut map, "tags", tags);
            set_string(&mut map, "type", "literature");
            set_string(&mut map, "summary", request.summary.trim());
            set_bool(&mut map, "publish", publish);
            set_string(&mut map, "section", "article");
            let body = format!("# {title}\n\n在这里整理文章正文。\n");
            (relative, "raw".to_string(), map, body)
        }
        "blog" => {
            let relative = format!("notes/blog/{}.md", safe_file_name(title));
            let mut map = Mapping::new();
            set_string(&mut map, "title", title);
            set_bool(&mut map, "publish", request.publish.unwrap_or(false));
            set_string(&mut map, "section", "blog");
            set_string(&mut map, "type", "permanent");
            set_string(&mut map, "summary", request.summary.trim());
            set_sequence(&mut map, "tags", tags);
            set_string(
                &mut map,
                "category",
                request.category.as_deref().unwrap_or("随笔"),
            );
            set_string(&mut map, "status", "draft");
            set_string(&mut map, "created", &today);
            set_string(&mut map, "updated", &today);
            (
                relative,
                "blog".to_string(),
                map,
                format!("# {title}\n\n## 正文\n\n-\n"),
            )
        }
        "software" => {
            let software_id = request.software_id.as_deref().unwrap_or_default();
            let id_re = Regex::new(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")?;
            if !id_re.is_match(software_id) {
                bail!("software_id 必须为小写字母、数字和连字符");
            }
            let relative = format!("notes/software/{software_id}.md");
            let mut map = Mapping::new();
            set_string(&mut map, "title", title);
            set_bool(&mut map, "publish", request.publish.unwrap_or(false));
            set_string(&mut map, "section", "software");
            set_string(&mut map, "software_id", software_id);
            set_string(&mut map, "status", "wishlist");
            set_string(
                &mut map,
                "category",
                request.category.as_deref().unwrap_or("待分类"),
            );
            set_sequence(
                &mut map,
                "tags",
                if tags.is_empty() {
                    vec!["tooling".to_string()]
                } else {
                    tags
                },
            );
            set_string(&mut map, "type", "permanent");
            set_sequence(&mut map, "platforms", Vec::<String>::new());
            set_string(&mut map, "pricing", "unknown");
            map.insert(key("open_source"), YamlValue::String("unknown".into()));
            set_string(&mut map, "official_url", "待确认");
            set_string(&mut map, "download_url", "待确认");
            set_string(&mut map, "summary", request.summary.trim());
            map.insert(
                key("related"),
                serde_yaml::from_str("wiki: []\narticles: []\napp_notes: []")?,
            );
            set_sequence(&mut map, "sources", Vec::<String>::new());
            set_string(&mut map, "created", &today);
            set_string(&mut map, "updated", &today);
            let body = format!(
                "# {title}\n\n## 简介\n\n-\n\n## 我的使用判断\n\n-\n\n## 来源\n\n- 待补充官方来源。\n"
            );
            (relative, "software".to_string(), map, body)
        }
        _ => bail!("不支持的内容类型"),
    };
    let rule = state
        .registry
        .types
        .get(&kind)
        .ok_or_else(|| anyhow!("Missing rule for {kind}"))?;
    let path = resolve_allowed_path(&state.vault, &relative_path)?;
    if path.exists() {
        bail!("文件已存在：{relative_path}");
    }
    fs::create_dir_all(path.parent().ok_or_else(|| anyhow!("Invalid target"))?)?;
    let rendered = render_document(&map, &body, rule);
    atomic_write(&path, rendered.as_bytes())?;
    read_document(&state.vault, &path, kind).map(|doc| document_to_item(&doc))
}

pub fn update_metadata(state: &AppState, request: MetadataPatch) -> Result<Item> {
    let path = resolve_allowed_path(&state.vault, &request.path)?;
    let kind = classify(&state.vault, &path).ok_or_else(|| anyhow!("不支持的文件路径"))?;
    let rule = state
        .registry
        .types
        .get(&kind)
        .ok_or_else(|| anyhow!("该文件类型没有 Schema"))?;
    let lock_path = metadata_lock_path(state, &path)?;
    if let Some(parent) = lock_path.parent() {
        fs::create_dir_all(parent)?;
    }
    let lock = OpenOptions::new()
        .create(true)
        .read(true)
        .write(true)
        .open(&lock_path)?;
    lock.lock_exclusive()?;
    let mut doc = read_document(&state.vault, &path, kind.clone())?;
    if request
        .expected_mtime
        .is_some_and(|value| value != doc.mtime)
    {
        FileExt::unlock(&lock)?;
        bail!("文件已被其他程序修改，请刷新后重试");
    }
    let allowed: HashSet<&str> = rule.order.iter().map(String::as_str).collect();
    let mutable: HashSet<&str> = [
        "title",
        "source",
        "author",
        "published",
        "summary",
        "tags",
        "publish",
        "category",
        "status",
        "pricing",
        "platforms",
        "open_source",
        "official_url",
        "download_url",
        "sources",
        "related_software",
    ]
    .into_iter()
    .collect();
    let mut effective = false;
    for (field, value) in request.fields {
        if !allowed.contains(field.as_str()) || !mutable.contains(field.as_str()) {
            FileExt::unlock(&lock)?;
            bail!("字段 `{field}` 不允许在管理器中修改");
        }
        let yaml = metadata_value_to_yaml(&field, value)?;
        let old = doc.frontmatter.get(key(&field));
        if old != Some(&yaml) {
            doc.frontmatter.insert(key(&field), yaml);
            effective = true;
        }
    }
    if doc.frontmatter.contains_key(key("tags")) {
        let normalized = normalize_tags(&tags_field(&doc.frontmatter), &state.registry);
        set_sequence(&mut doc.frontmatter, "tags", normalized);
    }
    if bool_field(&doc.frontmatter, "publish").unwrap_or(false) {
        let missing: Vec<_> = rule
            .non_empty_when_published
            .iter()
            .filter(|field| !field_publish_ready(&doc.frontmatter, field, &state.registry))
            .cloned()
            .collect();
        if !missing.is_empty() {
            FileExt::unlock(&lock)?;
            bail!("公开前必须补充：{}", missing.join("、"));
        }
    }
    if effective && doc.frontmatter.contains_key(key("updated")) {
        set_string(&mut doc.frontmatter, "updated", &today());
    }
    let rendered = render_document(&doc.frontmatter, &doc.body, rule);
    if rendered != normalize_newlines(&doc.original) {
        backup_path(&state.vault, &path, &state.tool_root)?;
        atomic_write(&path, rendered.as_bytes())?;
    }
    FileExt::unlock(&lock)?;
    read_document(&state.vault, &path, kind).map(|doc| document_to_item(&doc))
}

pub fn fill_missing_metadata(state: &AppState, relative_path: &str) -> Result<FillDefaultsReport> {
    let path = resolve_allowed_path(&state.vault, relative_path)?;
    let kind = classify(&state.vault, &path).ok_or_else(|| anyhow!("不支持的文件路径"))?;
    let rule = state
        .registry
        .types
        .get(&kind)
        .ok_or_else(|| anyhow!("该文件类型没有 Schema"))?;
    let lock_path = metadata_lock_path(state, &path)?;
    if let Some(parent) = lock_path.parent() {
        fs::create_dir_all(parent)?;
    }
    let lock = OpenOptions::new()
        .create(true)
        .read(true)
        .write(true)
        .open(&lock_path)?;
    lock.lock_exclusive()?;

    let mut doc = read_document(&state.vault, &path, kind.clone())?;
    let mut applied = Vec::new();
    for field in &rule.required {
        let missing = !doc.frontmatter.contains_key(key(field));
        let empty_and_fillable = !field_non_empty(&doc.frontmatter, field)
            && matches!(
                field.as_str(),
                "title"
                    | "summary"
                    | "category"
                    | "created"
                    | "updated"
                    | "software_id"
                    | "related_software"
                    | "type"
                    | "section"
                    | "status"
                    | "pricing"
                    | "open_source"
                    | "source"
                    | "official_url"
                    | "download_url"
            );
        if !missing && !empty_and_fillable {
            continue;
        }
        if let Some(value) = safe_default_value(&doc, field, rule, &state.registry)? {
            if doc.frontmatter.get(key(field)) != Some(&value) {
                doc.frontmatter.insert(key(field), value);
                applied.push(field.clone());
            }
        }
    }

    if kind == "raw" && !field_publish_ready(&doc.frontmatter, "source", &state.registry) {
        let publish_key = key("publish");
        if doc.frontmatter.get(&publish_key) != Some(&YamlValue::Bool(false)) {
            doc.frontmatter.insert(publish_key, YamlValue::Bool(false));
            applied.push("publish".into());
        }
    }
    if !applied.is_empty() && doc.frontmatter.contains_key(key("updated")) {
        set_string(&mut doc.frontmatter, "updated", &today());
    }

    let rendered = render_document(&doc.frontmatter, &doc.body, rule);
    if rendered != normalize_newlines(&doc.original) {
        backup_path(&state.vault, &path, &state.tool_root)?;
        atomic_write(&path, rendered.as_bytes())?;
    }
    FileExt::unlock(&lock)?;

    let refreshed = read_document(&state.vault, &path, kind)?;
    let unresolved = unresolved_fields(&refreshed.frontmatter, rule, &state.registry);
    Ok(FillDefaultsReport {
        item: document_to_item(&refreshed),
        applied,
        unresolved,
    })
}

fn safe_default_value(
    doc: &Document,
    field: &str,
    rule: &TypeRule,
    registry: &Registry,
) -> Result<Option<YamlValue>> {
    if let Some(value) = rule.defaults.get(field) {
        return Ok(Some(serde_yaml::to_value(value)?));
    }
    let stem = doc
        .path
        .file_stem()
        .and_then(|value| value.to_str())
        .unwrap_or("未命名");
    let value = match field {
        "title" => YamlValue::String(stem.to_string()),
        "created" => YamlValue::String(file_date(&doc.path)),
        "updated" => YamlValue::String(
            string_field(&doc.frontmatter, "created")
                .filter(|value| !value.is_empty())
                .unwrap_or_else(today),
        ),
        "summary" => YamlValue::String(infer_summary(&doc.body, stem)),
        "category" => YamlValue::String("未分类".into()),
        "software_id" => YamlValue::String(stem.to_lowercase()),
        "related_software" => YamlValue::String(
            stem.split_once('-')
                .map(|(prefix, _)| prefix)
                .unwrap_or(stem)
                .to_lowercase(),
        ),
        "source" | "official_url" | "download_url" => {
            let Some(value) = registry.placeholder_values.get(field) else {
                return Ok(None);
            };
            serde_yaml::to_value(value)?
        }
        "published" => YamlValue::Null,
        "author" | "tags" | "platforms" | "sources" => YamlValue::Sequence(vec![]),
        "related" => serde_yaml::from_str("wiki: []\narticles: []\napp_notes: []")?,
        "publish" => YamlValue::Bool(false),
        "type" if doc.kind == "wiki" => {
            let inferred = if doc.relative.starts_with("wiki/concepts/") {
                "concept"
            } else if doc.relative.starts_with("wiki/entities/") {
                "entity"
            } else {
                "topic"
            };
            YamlValue::String(inferred.into())
        }
        _ => return Ok(None),
    };
    Ok(Some(value))
}

fn infer_summary(body: &str, fallback: &str) -> String {
    let candidate = body
        .lines()
        .map(str::trim)
        .find(|line| {
            !line.is_empty()
                && !line.starts_with('#')
                && !line.starts_with("![")
                && !line.starts_with("<!--")
                && *line != "-"
        })
        .unwrap_or(fallback)
        .trim_start_matches(['-', '*', '>'])
        .trim();
    candidate.chars().take(120).collect()
}

fn unresolved_fields(frontmatter: &Mapping, rule: &TypeRule, registry: &Registry) -> Vec<String> {
    let mut unresolved: Vec<String> = rule
        .required
        .iter()
        .filter(|field| !frontmatter.contains_key(key(field)))
        .chain(
            rule.non_empty_when_published
                .iter()
                .filter(|field| !field_publish_ready(frontmatter, field, registry)),
        )
        .cloned()
        .collect::<HashSet<_>>()
        .into_iter()
        .collect();
    unresolved.sort();
    unresolved
}

fn metadata_lock_path(state: &AppState, path: &Path) -> Result<PathBuf> {
    let relative = path
        .strip_prefix(&state.vault)
        .context("Managed file is outside the Vault")?;
    let mut lock_path = state.tool_root.join("locks").join(relative);
    let file_name = lock_path
        .file_name()
        .and_then(|value| value.to_str())
        .ok_or_else(|| anyhow!("Invalid lock target"))?;
    lock_path.set_file_name(format!("{file_name}.lock"));
    Ok(lock_path)
}

pub fn replace_tags(state: &AppState, request: TagReplaceRequest) -> Result<TagReplaceReport> {
    let from = canonical_tag(&request.from, &state.registry).unwrap_or(request.from.to_lowercase());
    let to = request
        .to
        .as_deref()
        .and_then(|value| canonical_tag(value, &state.registry));
    let mut affected = Vec::new();
    for path in markdown_files(&state.vault) {
        let Some(kind) = classify(&state.vault, &path) else {
            continue;
        };
        let Some(rule) = state.registry.types.get(&kind) else {
            continue;
        };
        let mut doc = match read_document(&state.vault, &path, kind) {
            Ok(doc) => doc,
            Err(_) => continue,
        };
        let tags = tags_field(&doc.frontmatter);
        if !tags
            .iter()
            .any(|tag| canonical_tag(tag, &state.registry).as_deref() == Some(from.as_str()))
        {
            continue;
        }
        let replaced: Vec<_> = tags
            .into_iter()
            .filter_map(|tag| {
                let canonical = canonical_tag(&tag, &state.registry).unwrap_or(tag);
                if canonical == from {
                    to.clone()
                } else {
                    Some(canonical)
                }
            })
            .collect();
        let normalized = normalize_tags(&replaced, &state.registry);
        affected.push(doc.relative.clone());
        if !request.preview {
            set_sequence(&mut doc.frontmatter, "tags", normalized);
            if doc.frontmatter.contains_key(key("updated")) {
                set_string(&mut doc.frontmatter, "updated", &today());
            }
            backup_path(&state.vault, &path, &state.tool_root)?;
            atomic_write(
                &path,
                render_document(&doc.frontmatter, &doc.body, rule).as_bytes(),
            )?;
        }
    }
    Ok(TagReplaceReport {
        preview: request.preview,
        affected,
    })
}

pub fn resolve_allowed_path(vault: &Path, relative_path: &str) -> Result<PathBuf> {
    let relative_path = relative_path.replace('\\', "/");
    let relative = Path::new(&relative_path);
    if relative.is_absolute()
        || relative.components().any(|component| {
            matches!(
                component,
                Component::ParentDir | Component::RootDir | Component::Prefix(_)
            )
        })
    {
        bail!("非法路径");
    }
    let allowed = ["daily", "raw", "notes", "wiki", "output", "templates"];
    let first = relative
        .components()
        .next()
        .and_then(|component| component.as_os_str().to_str())
        .unwrap_or_default();
    if !allowed.contains(&first) {
        bail!("路径不在允许目录中");
    }
    Ok(vault.join(relative))
}

fn markdown_files(vault: &Path) -> Vec<PathBuf> {
    ["daily", "raw", "notes", "wiki", "output"]
        .into_iter()
        .flat_map(|dir| {
            WalkDir::new(vault.join(dir))
                .into_iter()
                .filter_map(Result::ok)
        })
        .filter(|entry| {
            entry.file_type().is_file()
                && entry
                    .path()
                    .extension()
                    .is_some_and(|v| v.eq_ignore_ascii_case("md"))
        })
        .map(|entry| entry.into_path())
        .collect()
}

fn classify(vault: &Path, path: &Path) -> Option<String> {
    let rel = relative(vault, path);
    if rel.ends_with("/index.md")
        || rel == "wiki/index.md"
        || rel == "output/index.md"
        || rel == "daily/index.md"
        || rel == "raw/index.md"
        || rel == "notes/index.md"
    {
        return Some("index".into());
    }
    if rel.starts_with("raw/") {
        Some("raw".into())
    } else if rel.starts_with("daily/") {
        Some("daily".into())
    } else if rel.starts_with("notes/blog/") {
        Some("blog".into())
    } else if rel.starts_with("notes/software/") {
        Some("software".into())
    } else if rel.starts_with("notes/apps/") {
        Some("app-note".into())
    } else if rel.starts_with("notes/") {
        Some("note".into())
    } else if rel.starts_with("wiki/concepts/")
        || rel.starts_with("wiki/entities/")
        || rel.starts_with("wiki/topics/")
    {
        Some("wiki".into())
    } else if rel.starts_with("output/") {
        Some("output".into())
    } else {
        None
    }
}

fn read_document(vault: &Path, path: &Path, kind: String) -> Result<Document> {
    let original =
        fs::read_to_string(path).with_context(|| format!("Cannot read {}", path.display()))?;
    parse_document(vault, path, kind, original)
}

fn parse_document(vault: &Path, path: &Path, kind: String, original: String) -> Result<Document> {
    let normalized = normalize_newlines(&original);
    let (yaml, body) = split_frontmatter(&normalized)?;
    let frontmatter: Mapping = serde_yaml::from_str(yaml).context("Invalid YAML frontmatter")?;
    let mtime = fs::metadata(path)?
        .modified()
        .unwrap_or(SystemTime::UNIX_EPOCH)
        .duration_since(UNIX_EPOCH)
        .unwrap_or_default()
        .as_secs();
    Ok(Document {
        path: path.to_path_buf(),
        relative: relative(vault, path),
        kind,
        frontmatter,
        body: body.to_string(),
        original,
        mtime,
    })
}

fn split_frontmatter(text: &str) -> Result<(&str, &str)> {
    if !text.starts_with("---\n") {
        bail!("缺少文件开头 frontmatter");
    }
    let remainder = &text[4..];
    let end = remainder
        .find("\n---\n")
        .ok_or_else(|| anyhow!("frontmatter 未闭合"))?;
    Ok((&remainder[..end], &remainder[end + 5..]))
}

fn document_to_item(doc: &Document) -> Item {
    Item {
        relative: doc.relative.clone(),
        title: string_field(&doc.frontmatter, "title").unwrap_or_else(|| {
            doc.path
                .file_stem()
                .and_then(|v| v.to_str())
                .unwrap_or_default()
                .to_string()
        }),
        kind: doc.kind.clone(),
        tags: tags_field(&doc.frontmatter),
        publish: bool_field(&doc.frontmatter, "publish"),
        created: string_field(&doc.frontmatter, "created"),
        updated: string_field(&doc.frontmatter, "updated"),
        summary: string_field(&doc.frontmatter, "summary").unwrap_or_default(),
        mtime: doc.mtime,
        metadata: serde_json::to_value(&doc.frontmatter).unwrap_or(JsonValue::Null),
    }
}

fn normalize_raw_frontmatter(
    map: &mut Mapping,
    registry: &Registry,
    rule: &TypeRule,
) -> Result<bool> {
    let before_tags = tags_field(map);
    let tags = normalize_tags(&before_tags, registry);
    let mut effective = tags != before_tags;
    let description = string_field(map, "description").unwrap_or_default();
    let summary = string_field(map, "summary").unwrap_or_default();
    let merged = if summary.trim().is_empty() {
        concise_summary(description.trim())
    } else {
        concise_summary(summary.trim())
    };
    if merged != summary {
        effective = true;
    }
    let source = string_field(map, "source").unwrap_or_default();
    let publish_before = bool_field(map, "publish").unwrap_or(true);
    let publish = publish_before && !source.trim().is_empty();
    if publish != publish_before {
        effective = true;
    }
    let author = match map.get(key("author")) {
        Some(YamlValue::Sequence(values)) => values
            .iter()
            .filter_map(YamlValue::as_str)
            .map(ToString::to_string)
            .collect(),
        Some(YamlValue::String(value)) if !value.trim().is_empty() => {
            vec![value.trim().to_string()]
        }
        _ => Vec::new(),
    };
    let published = string_field(map, "published").filter(|v| !v.trim().is_empty());
    let created = string_field(map, "created").unwrap_or_default();
    let updated = string_field(map, "updated").unwrap_or_else(|| created.clone());
    let title = string_field(map, "title").unwrap_or_default();

    let mut ordered = Mapping::new();
    set_string(&mut ordered, "title", &title);
    set_string(&mut ordered, "source", &source);
    set_sequence(&mut ordered, "author", author);
    set_optional_string(&mut ordered, "published", published.as_deref());
    set_string(&mut ordered, "created", &created);
    set_string(&mut ordered, "updated", &updated);
    set_sequence(&mut ordered, "tags", tags);
    set_string(&mut ordered, "type", "literature");
    set_string(&mut ordered, "summary", &merged);
    set_bool(&mut ordered, "publish", publish);
    set_string(&mut ordered, "section", "article");
    *map = ordered;
    let _ = rule.strict;
    Ok(effective)
}

fn clean_raw_body(body: &str) -> Result<String> {
    let discourse_image_start_re =
        Regex::new(r#"^(?P<indent>\s*)\[!\[(?P<alt>[^\]]*)\]\([^\s\)]+(?:\s+\"[^\"]*\")?\)\s*$"#)?;
    let discourse_image_meta_re =
        Regex::new(r"(?i)^\s*(?:image)?\d+[×x]\d+\s+\d+(?:\.\d+)?\s*(?:B|KB|MB)\s*$")?;
    let discourse_image_end_re =
        Regex::new(r#"^\s*\]\((?P<target>[^\s\)]+)(?:\s+\"[^\"]*\")?\)\s*$"#)?;
    let image_re = Regex::new(r"^\s*!\[[^\]]*\]\((?P<target>[^\)]+)\)\s*$")?;
    let source_lines: Vec<&str> = body.lines().collect();
    let mut normalized_lines = Vec::new();
    let mut index = 0;
    while index < source_lines.len() {
        if let Some(start) = discourse_image_start_re.captures(source_lines[index]) {
            let mut cursor = index + 1;
            while cursor < source_lines.len() && source_lines[cursor].trim().is_empty() {
                cursor += 1;
            }
            if cursor < source_lines.len() && discourse_image_meta_re.is_match(source_lines[cursor])
            {
                cursor += 1;
                while cursor < source_lines.len() && source_lines[cursor].trim().is_empty() {
                    cursor += 1;
                }
                if cursor < source_lines.len()
                    && let Some(end) = discourse_image_end_re.captures(source_lines[cursor])
                {
                    let indent = start.name("indent").map_or("", |value| value.as_str());
                    let alt = start.name("alt").map_or("", |value| value.as_str());
                    let target = end.name("target").map_or("", |value| value.as_str());
                    normalized_lines.push(format!("{indent}![{alt}]({target})"));
                    index = cursor + 1;
                    continue;
                }
            }
        }
        normalized_lines.push(source_lines[index].to_string());
        index += 1;
    }

    let mut seen = HashSet::<String>::new();
    let mut output = Vec::new();
    let mut blank_count = 0;
    for line in &normalized_lines {
        if let Some(capture) = image_re.captures(line) {
            let target = capture
                .name("target")
                .map(|value| value.as_str())
                .unwrap_or_default();
            let url = target
                .split_whitespace()
                .next()
                .unwrap_or_default()
                .trim_matches(['<', '>']);
            if url.contains("/images/emoji/twemoji/") || !seen.insert(url.to_string()) {
                continue;
            }
        }
        if line.trim().is_empty() {
            blank_count += 1;
            if blank_count > 2 {
                continue;
            }
        } else {
            blank_count = 0;
        }
        output.push(line.as_str());
    }
    let mut cleaned = output.join("\n");
    if body.ends_with('\n') {
        cleaned.push('\n');
    }
    Ok(cleaned)
}

fn concise_summary(value: &str) -> String {
    if value.chars().count() <= 120 {
        return value.to_string();
    }
    for separator in ['；', '。', '\n'] {
        if let Some((head, _)) = value.split_once(separator) {
            if !head.trim().is_empty() && head.chars().count() <= 120 {
                return head.trim().to_string();
            }
        }
    }
    value
        .chars()
        .take(120)
        .collect::<String>()
        .trim()
        .to_string()
}

fn render_document(map: &Mapping, body: &str, rule: &TypeRule) -> String {
    let mut ordered = Mapping::new();
    for field in &rule.order {
        if let Some(value) = map.get(key(field)) {
            ordered.insert(key(field), value.clone());
        }
    }
    if !rule.strict {
        for (key_value, value) in map {
            if !ordered.contains_key(key_value) {
                ordered.insert(key_value.clone(), value.clone());
            }
        }
    }
    let mut yaml = serde_yaml::to_string(&ordered).unwrap_or_default();
    yaml = yaml.trim_start_matches("---\n").to_string();
    yaml = yaml.replace("published: null\n", "published:\n");
    format!(
        "---\n{}---\n{}",
        yaml,
        if body.starts_with('\n') {
            body.to_string()
        } else {
            format!("\n{body}")
        }
    )
}

fn normalize_tags(tags: &[String], registry: &Registry) -> Vec<String> {
    let allowed: HashSet<_> = registry.tag_order.iter().cloned().collect();
    let mut unique = HashSet::new();
    let mut normalized: Vec<String> = tags
        .iter()
        .filter_map(|tag| canonical_tag(tag, registry))
        .filter(|tag| allowed.contains(tag))
        .filter(|tag| unique.insert(tag.clone()))
        .collect();
    normalized.sort_by_key(|tag| {
        registry
            .tag_order
            .iter()
            .position(|item| item == tag)
            .unwrap_or(usize::MAX)
    });
    let mut semantic_seen = 0;
    normalized.retain(|tag| {
        if registry.semantic_tags.contains(tag) {
            semantic_seen += 1;
            semantic_seen <= 4
        } else {
            true
        }
    });
    normalized
}

fn canonical_tag(tag: &str, registry: &Registry) -> Option<String> {
    let trimmed = tag.trim().trim_start_matches('#');
    if trimmed.is_empty() {
        return None;
    }
    if let Some(mapped) = registry.tag_aliases.get(trimmed) {
        return Some(mapped.clone());
    }
    let normalized = trimmed.to_lowercase().replace('_', "-").replace(' ', "-");
    if let Some(mapped) = registry.tag_aliases.get(&normalized) {
        Some(mapped.clone())
    } else {
        Some(normalized)
    }
}

fn tags_field(map: &Mapping) -> Vec<String> {
    match map.get(key("tags")) {
        Some(YamlValue::Sequence(values)) => values
            .iter()
            .filter_map(|value| match value {
                YamlValue::String(value) => Some(value.clone()),
                _ => None,
            })
            .collect(),
        Some(YamlValue::String(value)) => value
            .split(',')
            .map(str::trim)
            .filter(|v| !v.is_empty())
            .map(ToString::to_string)
            .collect(),
        _ => Vec::new(),
    }
}

fn update_references(vault: &Path, moves: &[(String, String)]) -> Result<()> {
    for path in markdown_files(vault) {
        let original = fs::read_to_string(&path)?;
        let mut updated = original.clone();
        for (from, to) in moves {
            updated = updated.replace(from, to);
            updated = updated.replace(&from.trim_end_matches(".md"), &to.trim_end_matches(".md"));
        }
        if updated != original {
            backup_path(vault, &path, Path::new(env!("CARGO_MANIFEST_DIR")))?;
            atomic_write(&path, updated.as_bytes())?;
        }
    }
    Ok(())
}

fn remove_empty_raw_dirs(vault: &Path) -> Result<()> {
    let root = vault.join("raw");
    let mut dirs: Vec<_> = WalkDir::new(&root)
        .min_depth(1)
        .into_iter()
        .filter_map(Result::ok)
        .filter(|entry| entry.file_type().is_dir())
        .map(|entry| entry.into_path())
        .collect();
    dirs.sort_by_key(|path| std::cmp::Reverse(path.components().count()));
    for dir in dirs {
        if fs::read_dir(&dir)?.next().is_none() {
            fs::remove_dir(&dir)?;
        }
    }
    Ok(())
}

fn backup_path(vault: &Path, path: &Path, tool_root: &Path) -> Result<()> {
    if !path.exists() {
        return Ok(());
    }
    let stamp = Local::now().format("%Y%m%d-%H%M%S").to_string();
    let rel = path.strip_prefix(vault).unwrap_or(path);
    let target = tool_root.join("backups").join(stamp).join(rel);
    if let Some(parent) = target.parent() {
        fs::create_dir_all(parent)?;
    }
    fs::copy(path, target)?;
    Ok(())
}

fn atomic_write(path: &Path, bytes: &[u8]) -> Result<()> {
    let parent = path
        .parent()
        .ok_or_else(|| anyhow!("Target has no parent"))?;
    fs::create_dir_all(parent)?;
    let temp = parent.join(format!(
        ".{}.vault-manager-{}.tmp",
        path.file_name().and_then(|v| v.to_str()).unwrap_or("file"),
        std::process::id()
    ));
    {
        let mut file = File::create(&temp)?;
        file.write_all(bytes)?;
        file.sync_all()?;
    }
    #[cfg(windows)]
    {
        use std::os::windows::ffi::OsStrExt;
        use windows_sys::Win32::Storage::FileSystem::{
            MOVEFILE_REPLACE_EXISTING, MOVEFILE_WRITE_THROUGH, MoveFileExW,
        };
        let from: Vec<u16> = temp.as_os_str().encode_wide().chain(Some(0)).collect();
        let to: Vec<u16> = path.as_os_str().encode_wide().chain(Some(0)).collect();
        let ok = unsafe {
            MoveFileExW(
                from.as_ptr(),
                to.as_ptr(),
                MOVEFILE_REPLACE_EXISTING | MOVEFILE_WRITE_THROUGH,
            )
        };
        if ok == 0 {
            let _ = fs::remove_file(&temp);
            return Err(std::io::Error::last_os_error().into());
        }
    }
    #[cfg(not(windows))]
    fs::rename(&temp, path)?;
    Ok(())
}

fn relative(vault: &Path, path: &Path) -> String {
    path.strip_prefix(vault)
        .unwrap_or(path)
        .to_string_lossy()
        .replace('\\', "/")
}

fn normalize_newlines(value: &str) -> String {
    value.replace("\r\n", "\n")
}
fn today() -> String {
    Local::now().format("%Y-%m-%d").to_string()
}
fn file_date(path: &Path) -> String {
    fs::metadata(path)
        .and_then(|m| m.created().or_else(|_| m.modified()))
        .ok()
        .map(chrono::DateTime::<Local>::from)
        .map(|d| d.format("%Y-%m-%d").to_string())
        .unwrap_or_else(today)
}
fn safe_file_name(value: &str) -> String {
    let invalid = Regex::new(r#"[<>:\"/\\|?*]"#).unwrap();
    let cleaned = invalid
        .replace_all(value.trim(), "-")
        .trim_matches(['.', ' '])
        .to_string();
    if cleaned.is_empty() {
        "untitled".into()
    } else {
        cleaned
    }
}
fn public_slug(value: &str) -> String {
    value
        .trim()
        .to_lowercase()
        .replace(' ', "-")
        .replace('_', "-")
}
fn issue(level: &str, path: impl Into<String>, message: impl Into<String>) -> ValidationIssue {
    ValidationIssue {
        level: level.into(),
        path: path.into(),
        message: message.into(),
    }
}
fn key(value: &str) -> YamlValue {
    YamlValue::String(value.to_string())
}
fn string_field(map: &Mapping, field: &str) -> Option<String> {
    map.get(key(field)).and_then(|value| match value {
        YamlValue::String(value) => Some(value.clone()),
        YamlValue::Number(value) => Some(value.to_string()),
        YamlValue::Null => Some(String::new()),
        _ => None,
    })
}
fn bool_field(map: &Mapping, field: &str) -> Option<bool> {
    map.get(key(field)).and_then(YamlValue::as_bool)
}
fn metadata_value_to_yaml(field: &str, value: JsonValue) -> Result<YamlValue> {
    if field == "published" && value.as_str().is_some_and(|item| item.trim().is_empty()) {
        return Ok(YamlValue::Null);
    }
    serde_yaml::to_value(value).map_err(Into::into)
}
fn field_non_empty(map: &Mapping, field: &str) -> bool {
    match map.get(key(field)) {
        Some(YamlValue::String(value)) => !value.trim().is_empty(),
        Some(YamlValue::Sequence(value)) => !value.is_empty(),
        Some(YamlValue::Mapping(value)) => !value.is_empty(),
        Some(YamlValue::Null) | None => false,
        _ => true,
    }
}

fn field_publish_ready(map: &Mapping, field: &str, _registry: &Registry) -> bool {
    field_non_empty(map, field)
}
fn set_string(map: &mut Mapping, field: &str, value: &str) {
    map.insert(key(field), YamlValue::String(value.to_string()));
}
fn set_optional_string(map: &mut Mapping, field: &str, value: Option<&str>) {
    map.insert(
        key(field),
        value
            .filter(|v| !v.trim().is_empty())
            .map(|v| YamlValue::String(v.to_string()))
            .unwrap_or(YamlValue::Null),
    );
}
fn set_bool(map: &mut Mapping, field: &str, value: bool) {
    map.insert(key(field), YamlValue::Bool(value));
}
fn set_sequence<I, S>(map: &mut Mapping, field: &str, values: I)
where
    I: IntoIterator<Item = S>,
    S: Into<String>,
{
    map.insert(
        key(field),
        YamlValue::Sequence(
            values
                .into_iter()
                .map(|v| YamlValue::String(v.into()))
                .collect(),
        ),
    );
}

#[cfg(test)]
mod tests {
    use super::*;

    fn registry() -> Registry {
        Registry {
            version: 1,
            date_format: "YYYY-MM-DD".into(),
            tag_order: vec!["ai", "tooling", "workflow", "daily"]
                .into_iter()
                .map(String::from)
                .collect(),
            semantic_tags: vec!["ai", "tooling", "workflow"]
                .into_iter()
                .map(String::from)
                .collect(),
            structural_tags: HashMap::new(),
            tag_aliases: HashMap::from([
                ("AI".into(), "ai".into()),
                ("tools".into(), "tooling".into()),
            ]),
            updated_fields: vec![],
            maintenance_only: vec![],
            placeholder_values: HashMap::from([
                (
                    "source".into(),
                    serde_json::json!("https://placeholder.invalid/source"),
                ),
                (
                    "official_url".into(),
                    serde_json::json!("https://placeholder.invalid/official"),
                ),
                (
                    "download_url".into(),
                    serde_json::json!("https://placeholder.invalid/download"),
                ),
            ]),
            types: HashMap::new(),
        }
    }

    #[test]
    fn tags_are_mapped_deduplicated_and_ordered() {
        let tags = vec![
            "tools".into(),
            "AI".into(),
            "ai".into(),
            "temporary".into(),
            "workflow".into(),
        ];
        assert_eq!(
            normalize_tags(&tags, &registry()),
            vec!["ai", "tooling", "workflow"]
        );
    }

    #[test]
    fn cleaner_removes_emoji_and_duplicate_images() {
        let body = "正文\n\n![:sob:](https://cdn.example/images/emoji/twemoji/sob.png)\n![a](https://img/a.png)\n![a2](https://img/a.png)\n";
        let cleaned = clean_raw_body(body).unwrap();
        assert!(cleaned.contains("正文"));
        assert!(!cleaned.contains("twemoji"));
        assert_eq!(cleaned.matches("https://img/a.png").count(), 1);
    }

    #[test]
    fn cleaner_collapses_discourse_image_metadata_blocks() {
        let body = "正文\n\n[![image](https://img/preview.png)\n\nimage1104×570 7.32 KB\n\n](https://img/original.png \"image\")\n\n- 列表\n\t[![截图](https://img/list-preview.png)\n\timage800x600 1.2 MB\n\t](https://img/list-original.png \"截图\")\n";
        let cleaned = clean_raw_body(body).unwrap();
        assert!(cleaned.contains("![image](https://img/original.png)"));
        assert!(cleaned.contains("\t![截图](https://img/list-original.png)"));
        assert!(!cleaned.contains("image1104×570 7.32 KB"));
        assert!(!cleaned.contains("preview.png"));
    }

    #[test]
    fn file_name_rejects_windows_reserved_characters() {
        assert_eq!(safe_file_name("A:B/C?"), "A-B-C-");
    }

    #[test]
    fn long_collected_description_is_not_kept_in_summary() {
        let value = format!("简洁摘要；{}", "网页噪声".repeat(40));
        assert_eq!(concise_summary(&value), "简洁摘要");
    }

    #[test]
    fn git_null_paths_are_parsed_without_losing_spaces_or_chinese() {
        let paths = parse_null_paths("raw/2026-07/中文 文件.md\0notes/blog/a.md\0".as_bytes());
        assert_eq!(paths, vec!["raw/2026-07/中文 文件.md", "notes/blog/a.md"]);
    }

    #[test]
    fn empty_published_value_stays_yaml_null() {
        assert_eq!(
            metadata_value_to_yaml("published", JsonValue::String(String::new())).unwrap(),
            YamlValue::Null
        );
    }

    #[test]
    fn publish_can_switch_in_both_directions_while_file_is_locked() {
        let unique = SystemTime::now()
            .duration_since(UNIX_EPOCH)
            .unwrap()
            .as_nanos();
        let root = std::env::temp_dir().join(format!(
            "vault-manager-publish-test-{}-{unique}",
            std::process::id()
        ));
        let raw_dir = root.join("raw").join("2026-07");
        fs::create_dir_all(&raw_dir).unwrap();
        let path = raw_dir.join("test.md");
        let content = "---\ntitle: Test\nsource: https://example.com\nauthor: []\npublished:\ncreated: 2026-07-25\nupdated: 2026-07-25\ntags:\n- tooling\ntype: literature\nsummary: Test summary\npublish: false\nsection: article\n---\n\nBody\n";
        fs::write(&path, content).unwrap();

        let mut test_registry = registry();
        test_registry.types.insert(
            "raw".into(),
            TypeRule {
                paths: vec!["raw/**/*.md".into()],
                exclude: vec![],
                order: vec![
                    "title",
                    "source",
                    "author",
                    "published",
                    "created",
                    "updated",
                    "tags",
                    "type",
                    "summary",
                    "publish",
                    "section",
                ]
                .into_iter()
                .map(String::from)
                .collect(),
                required: vec![],
                non_empty_when_published: vec!["source".into()],
                defaults: HashMap::new(),
                enums: HashMap::new(),
                strict: true,
                archive: None,
                archive_by: None,
            },
        );
        let state = AppState {
            vault: root.clone(),
            registry: test_registry,
            token: "test".into(),
            tool_root: root.join("tool"),
        };
        let initial = document_to_item(&read_document(&root, &path, "raw".into()).unwrap());
        let public = update_metadata(
            &state,
            MetadataPatch {
                path: "raw/2026-07/test.md".into(),
                expected_mtime: Some(initial.mtime),
                fields: serde_json::json!({"publish": true})
                    .as_object()
                    .unwrap()
                    .clone(),
            },
        )
        .unwrap();
        assert_eq!(public.publish, Some(true));
        assert!(fs::read_to_string(&path).unwrap().contains("publish: true"));

        let private = update_metadata(
            &state,
            MetadataPatch {
                path: "raw/2026-07/test.md".into(),
                expected_mtime: Some(public.mtime),
                fields: serde_json::json!({"publish": false})
                    .as_object()
                    .unwrap()
                    .clone(),
            },
        )
        .unwrap();
        assert_eq!(private.publish, Some(false));
        assert!(
            fs::read_to_string(&path)
                .unwrap()
                .contains("publish: false")
        );

        fs::remove_dir_all(root).unwrap();
    }

    #[test]
    fn fill_defaults_uses_schema_and_keeps_sourceless_raw_private() {
        let unique = SystemTime::now()
            .duration_since(UNIX_EPOCH)
            .unwrap()
            .as_nanos();
        let root = std::env::temp_dir().join(format!(
            "vault-manager-fill-test-{}-{unique}",
            std::process::id()
        ));
        let raw_dir = root.join("raw").join("2026-07");
        fs::create_dir_all(&raw_dir).unwrap();
        let path = raw_dir.join("test.md");
        fs::write(
            &path,
            "---\ntitle: Test\nsource: ''\npublish: true\n---\n\n# Test\n\n正文第一段。\n",
        )
        .unwrap();

        let mut test_registry = registry();
        test_registry.types.insert(
            "raw".into(),
            TypeRule {
                paths: vec!["raw/**/*.md".into()],
                exclude: vec![],
                order: vec![
                    "title",
                    "source",
                    "author",
                    "published",
                    "created",
                    "updated",
                    "tags",
                    "type",
                    "summary",
                    "publish",
                    "section",
                ]
                .into_iter()
                .map(String::from)
                .collect(),
                required: vec![
                    "title",
                    "source",
                    "author",
                    "published",
                    "created",
                    "updated",
                    "tags",
                    "type",
                    "summary",
                    "publish",
                    "section",
                ]
                .into_iter()
                .map(String::from)
                .collect(),
                non_empty_when_published: vec!["source".into(), "summary".into()],
                defaults: HashMap::from([
                    ("author".into(), serde_json::json!([])),
                    ("published".into(), JsonValue::Null),
                    ("tags".into(), serde_json::json!([])),
                    ("type".into(), serde_json::json!("literature")),
                    ("publish".into(), serde_json::json!(true)),
                    ("section".into(), serde_json::json!("article")),
                ]),
                enums: HashMap::new(),
                strict: true,
                archive: None,
                archive_by: None,
            },
        );
        let state = AppState {
            vault: root.clone(),
            registry: test_registry,
            token: "test".into(),
            tool_root: root.join("tool"),
        };

        let report = fill_missing_metadata(&state, "raw/2026-07/test.md").unwrap();
        assert_eq!(report.item.publish, Some(true));
        assert_eq!(report.item.summary, "正文第一段。");
        assert!(report.unresolved.is_empty());
        assert_eq!(
            report.item.metadata["source"],
            serde_json::json!("https://placeholder.invalid/source")
        );
        assert!(report.applied.contains(&"summary".to_string()));
        assert!(report.applied.contains(&"source".to_string()));
        assert!(
            fs::read_to_string(&path)
                .unwrap()
                .contains("type: literature")
        );

        let published = update_metadata(
            &state,
            MetadataPatch {
                path: "raw/2026-07/test.md".into(),
                expected_mtime: Some(report.item.mtime),
                fields: serde_json::json!({"publish": true})
                    .as_object()
                    .unwrap()
                    .clone(),
            },
        )
        .unwrap();
        assert_eq!(published.publish, Some(true));

        fs::remove_dir_all(root).unwrap();
    }
}
