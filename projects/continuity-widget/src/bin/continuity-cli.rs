use continuity_core::{
    ContinuityService,
    migration::{backup_database, rebuild_database_from_markdown},
    models::*,
    snapshot::export_snapshot,
};
use serde::Serialize;
use serde_json::{Value, json};
use std::{
    env, fs,
    io::{self, Read},
    path::PathBuf,
    process::ExitCode,
};

#[derive(Serialize)]
#[serde(rename_all = "camelCase")]
struct Envelope<T: Serialize> {
    ok: bool,
    revision: i64,
    data: T,
    warnings: Vec<String>,
}

fn main() -> ExitCode {
    match run() {
        Ok(value) => {
            println!(
                "{}",
                serde_json::to_string(&value).expect("序列化 CLI 响应失败")
            );
            ExitCode::SUCCESS
        }
        Err(error) => {
            let message = format!("{error:#}");
            let (code, details) = error_details(&message);
            println!(
                "{}",
                json!({
                    "ok": false,
                    "error": {"code": code, "message": human_message(&message), "details": details}
                })
            );
            ExitCode::from(1)
        }
    }
}

fn run() -> anyhow::Result<Value> {
    let args = env::args().skip(1).collect::<Vec<_>>();
    if args.len() < 2 {
        anyhow::bail!("validation_error:命令格式为 <group> <command>");
    }
    let group = args[0].as_str();
    let command = args[1].as_str();
    let input = read_input(&args[2..])?;
    if (group, command) == ("database", "rebuild-from-markdown") {
        let result = rebuild_database_from_markdown(serde_json::from_value(input)?)?;
        return Ok(serde_json::to_value(Envelope {
            ok: true,
            revision: result.revision,
            data: result,
            warnings: Vec::new(),
        })?);
    }
    let service = ContinuityService::open_default()?;

    let data = match (group, command) {
        ("project", "upsert") => {
            let input: ProjectUpsertInput = serde_json::from_value(input)?;
            backup_if_automatic(&service, input.batch.as_ref())?;
            serde_json::to_value(service.project_upsert(input)?)?
        }
        ("project", "confirm") => {
            serde_json::to_value(service.confirm_project(serde_json::from_value(input)?)?)?
        }
        ("item", "upsert") => {
            let input: ItemUpsertInput = serde_json::from_value(input)?;
            backup_if_automatic(&service, input.batch.as_ref())?;
            serde_json::to_value(service.item_upsert(input)?)?
        }
        ("item", "complete") => {
            serde_json::to_value(service.complete_item(serde_json::from_value(input)?)?)?
        }
        ("item", "list") => {
            let project_id = required_string(&input, "projectId")?;
            serde_json::to_value(service.list_items(project_id)?)?
        }
        ("project", "summarize") => {
            let input: SummarizeInput = serde_json::from_value(input)?;
            backup_if_automatic(&service, input.project.batch.as_ref())?;
            serde_json::to_value(service.summarize(input)?)?
        }
        ("project", "progress") => {
            let project_id = required_string(&input, "projectId")?;
            serde_json::to_value(service.project_progress(project_id)?)?
        }
        ("milestone", "upsert") => {
            serde_json::to_value(service.milestone_upsert(serde_json::from_value(input)?)?)?
        }
        ("milestone", "save") => {
            serde_json::to_value(service.save_milestone(serde_json::from_value(input)?)?)?
        }
        ("event", "apply") => {
            serde_json::to_value(service.apply_event(serde_json::from_value(input)?)?)?
        }
        ("event", "status") => {
            serde_json::to_value(service.event_status(serde_json::from_value(input)?)?)?
        }
        ("event", "revert") => {
            serde_json::to_value(service.revert_event(serde_json::from_value(input)?)?)?
        }
        ("event", "retry-pending") => json!({"delivered": service.retry_pending_events()?}),
        ("suggestion", "apply") => {
            serde_json::to_value(service.apply_suggestion(serde_json::from_value(input)?)?)?
        }
        ("suggestion", "reject") => {
            serde_json::to_value(service.reject_suggestion(serde_json::from_value(input)?)?)?
        }
        ("database", "repair-legacy-encoding") => {
            serde_json::to_value(service.repair_legacy_encoding(serde_json::from_value(input)?)?)?
        }
        ("snapshot", "export") => {
            let input: SnapshotExportInput = if input.is_null() || input == json!({}) {
                SnapshotExportInput { path: None }
            } else {
                serde_json::from_value(input)?
            };
            serde_json::to_value(export_snapshot(
                &service,
                input.path.as_deref().map(PathBuf::from).as_deref(),
            )?)?
        }
        ("batch", "undo-last") => json!({"revision": service.undo_last_automatic()?}),
        ("database", "status") => serde_json::to_value(service.status()?)?,
        _ => anyhow::bail!("validation_error:未知命令 {group} {command}"),
    };
    let revision = service.revision()?;
    Ok(serde_json::to_value(Envelope {
        ok: true,
        revision,
        data,
        warnings: Vec::new(),
    })?)
}

fn required_string<'a>(input: &'a Value, key: &str) -> anyhow::Result<&'a str> {
    input
        .get(key)
        .and_then(Value::as_str)
        .filter(|value| !value.trim().is_empty())
        .ok_or_else(|| anyhow::anyhow!("validation_error:{key} 不能为空"))
}

fn read_input(args: &[String]) -> anyhow::Result<Value> {
    let Some(index) = args.iter().position(|arg| arg == "--input") else {
        return Ok(json!({}));
    };
    let source = args
        .get(index + 1)
        .ok_or_else(|| anyhow::anyhow!("validation_error:--input 缺少值"))?;
    let text = if source == "-" {
        let mut text = String::new();
        io::stdin().read_to_string(&mut text)?;
        text
    } else {
        fs::read_to_string(source)?
    };
    Ok(serde_json::from_str(&text)?)
}

fn backup_if_automatic(
    service: &ContinuityService,
    batch: Option<&BatchInput>,
) -> anyhow::Result<()> {
    if batch.is_some_and(|batch| batch.kind == "automatic") {
        let _ = backup_database(service.database())?;
    }
    Ok(())
}

fn error_details(message: &str) -> (&'static str, Value) {
    if let Some(rest) = message.split("revision_conflict:").nth(1) {
        let values = rest.split(':').collect::<Vec<_>>();
        return (
            "revision_conflict",
            json!({
                "expected": values.first().and_then(|value| value.parse::<i64>().ok()),
                "actual": values.get(1).and_then(|value| value.parse::<i64>().ok())
            }),
        );
    }
    if message.contains("not_found:") {
        return ("not_found", json!({}));
    }
    if message.contains("validation_error:") {
        return ("validation_error", json!({}));
    }
    for code in ["event_conflict", "target_conflict", "event_revert_conflict"] {
        if message.contains(&format!("{code}:")) {
            return (code, json!({}));
        }
    }
    ("internal_error", json!({}))
}

fn human_message(message: &str) -> String {
    if message.contains("revision_conflict:") {
        return "数据已更新，请刷新后重试".to_owned();
    }
    if message.contains("event_conflict:") {
        return "同一事件 ID 已提交过不同内容".to_owned();
    }
    if message.contains("target_conflict:") {
        return "事件目标与项目不一致".to_owned();
    }
    if message.contains("event_revert_conflict:") {
        return "事件生效后目标已被再次修改，无法自动撤销".to_owned();
    }
    message
        .split("validation_error:")
        .last()
        .unwrap_or(message)
        .to_owned()
}
