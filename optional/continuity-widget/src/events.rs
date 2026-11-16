use anyhow::{Result, bail};
use serde::Serialize;
use serde_json::{Map, Value};
use sha2::{Digest, Sha256};

pub fn canonical_payload_hash<T: Serialize>(value: &T) -> Result<String> {
    let value = serde_json::to_value(value)?;
    let canonical = canonicalize(value);
    let bytes = serde_json::to_vec(&canonical)?;
    Ok(format!("{:x}", Sha256::digest(bytes)))
}

pub fn validate_event_identity(source: &str, event_id: &str, version: i32) -> Result<()> {
    if version != 1 {
        bail!("validation_error:事件 version 目前只能是 1");
    }
    if source.trim().is_empty() || event_id.trim().is_empty() {
        bail!("validation_error:source 和 eventId 不能为空");
    }
    Ok(())
}

fn canonicalize(value: Value) -> Value {
    match value {
        Value::Array(values) => Value::Array(values.into_iter().map(canonicalize).collect()),
        Value::Object(values) => {
            let mut entries = values.into_iter().collect::<Vec<_>>();
            entries.sort_by(|left, right| left.0.cmp(&right.0));
            let mut output = Map::new();
            for (key, value) in entries {
                output.insert(key, canonicalize(value));
            }
            Value::Object(output)
        }
        other => other,
    }
}
