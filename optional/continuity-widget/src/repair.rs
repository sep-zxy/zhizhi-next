use crate::models::EncodingRepairChange;
use anyhow::{Context, Result};
use encoding_rs::GBK;
use regex::Regex;
use std::{collections::HashMap, fs, path::Path};

#[derive(Debug, Clone)]
pub struct CanonicalLegacyData {
    pub projects: HashMap<String, String>,
    pub items: HashMap<String, String>,
}

pub fn load_canonical_legacy(path: &Path) -> Result<CanonicalLegacyData> {
    let markdown = fs::read_to_string(path)
        .with_context(|| format!("读取乱码修复来源失败：{}", path.display()))?;
    let project_re = Regex::new(r"<!--\s*continuity:project\s+([^>]+)-->")?;
    let item_re = Regex::new(r"<!--\s*continuity:item\s+([^>]+)-->")?;
    let id_re = Regex::new(r#"\bid=(?:\"([^\"]+)\"|(\S+))"#)?;
    let mut project_title = String::new();
    let mut item_title = String::new();
    let mut projects = HashMap::new();
    let mut items = HashMap::new();

    for line in markdown.lines() {
        let trimmed = line.trim();
        if let Some(title) = trimmed.strip_prefix("### ") {
            project_title = title.trim().to_owned();
        } else if let Some(title) = trimmed.strip_prefix("- [ ] ") {
            item_title = title.trim().to_owned();
        } else if let Some(title) = trimmed.strip_prefix("- [x] ") {
            item_title = title.trim().to_owned();
        } else if let Some(title) = trimmed.strip_prefix("- ") {
            item_title = title.trim().to_owned();
        }

        if let Some(captures) = project_re.captures(trimmed)
            && let Some(id) = capture_id(&id_re, &captures[1])
        {
            projects.insert(id, project_title.clone());
        }
        if let Some(captures) = item_re.captures(trimmed)
            && let Some(id) = capture_id(&id_re, &captures[1])
        {
            items.insert(id, item_title.clone());
        }
    }
    Ok(CanonicalLegacyData { projects, items })
}

pub fn repairable_change(
    entity: &str,
    id: &str,
    field: &str,
    current: &str,
    canonical: &str,
) -> Option<EncodingRepairChange> {
    if current == canonical || current != mojibake(canonical) {
        return None;
    }
    Some(EncodingRepairChange {
        entity: entity.to_owned(),
        id: id.to_owned(),
        field: field.to_owned(),
        before: current.to_owned(),
        after: canonical.to_owned(),
    })
}

fn capture_id(regex: &Regex, attributes: &str) -> Option<String> {
    let captures = regex.captures(attributes)?;
    captures
        .get(1)
        .or_else(|| captures.get(2))
        .map(|value| value.as_str().trim_matches('"').to_owned())
}

fn mojibake(value: &str) -> String {
    let (decoded, _, _) = GBK.decode(value.as_bytes());
    decoded.into_owned()
}
