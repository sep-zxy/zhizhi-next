mod vault;

use std::{net::SocketAddr, path::PathBuf, sync::Arc};

use anyhow::{Context, Result};
use axum::{
    Json, Router,
    extract::{Query, State},
    http::{HeaderMap, StatusCode, header},
    response::{Html, IntoResponse, Response},
    routing::{get, patch, post},
};
use clap::{Parser, Subcommand};
use serde::Deserialize;
use serde_json::{Value, json};
use tokio::net::TcpListener;

use vault::{
    AppState, CreateRequest, MetadataPatch, TagReplaceRequest, fill_missing_metadata,
    load_registry, normalize_vault, scan_items, validate_vault,
};

const INDEX_HTML: &str = include_str!("../static/index.html");
const APP_JS: &str = include_str!("../static/app.js");
const STYLES_CSS: &str = include_str!("../static/styles.css");
const TABLER_CSS: &str = include_str!("../static/vendor/tabler.min.css");
const LOGO_SVG: &str = include_str!("../assets/vault-manager-logo.svg");

#[derive(Parser)]
#[command(name = "vault-manager", version, about)]
struct Cli {
    #[command(subcommand)]
    command: Option<Command>,
}

#[derive(Subcommand)]
enum Command {
    Serve {
        #[arg(long, default_value = r"C:\Code\notes\zxy")]
        vault: PathBuf,
        #[arg(long, default_value_t = 49154)]
        port: u16,
        #[arg(long)]
        no_open: bool,
    },
    Validate {
        #[arg(long, default_value = r"C:\Code\notes\zxy")]
        vault: PathBuf,
        #[arg(long, default_value = "all")]
        scope: String,
        #[arg(long)]
        json: bool,
    },
    Normalize {
        #[arg(long, default_value = r"C:\Code\notes\zxy")]
        vault: PathBuf,
        #[arg(long)]
        dry_run: bool,
        #[arg(long)]
        json: bool,
    },
}

#[tokio::main]
pub async fn run() -> Result<()> {
    let cli = Cli::parse();
    match cli.command.unwrap_or(Command::Serve {
        vault: PathBuf::from(r"C:\Code\notes\zxy"),
        port: 49154,
        no_open: false,
    }) {
        Command::Serve {
            vault,
            port,
            no_open,
        } => serve(vault, port, no_open).await,
        Command::Validate { vault, scope, json } => {
            let registry = load_registry(&vault)?;
            let report = validate_vault(&vault, &registry, &scope)?;
            if json {
                println!("{}", serde_json::to_string_pretty(&report)?);
            } else {
                println!("Checked: {}", report.checked);
                println!("Errors: {}", report.errors.len());
                println!("Warnings: {}", report.warnings.len());
                for issue in report.errors.iter().chain(report.warnings.iter()) {
                    println!("{}: {} ({})", issue.level, issue.path, issue.message);
                }
            }
            if !report.errors.is_empty() {
                std::process::exit(2);
            }
            Ok(())
        }
        Command::Normalize {
            vault,
            dry_run,
            json,
        } => {
            let registry = load_registry(&vault)?;
            let report = normalize_vault(&vault, &registry, dry_run)?;
            if json {
                println!("{}", serde_json::to_string_pretty(&report)?);
            } else {
                println!("Changed: {}", report.changed.len());
                println!("Moved: {}", report.moved.len());
                println!("Conflicts: {}", report.conflicts.len());
                for action in &report.changed {
                    println!("change: {action}");
                }
                for action in &report.moved {
                    println!("move: {action}");
                }
                for action in &report.conflicts {
                    println!("conflict: {action}");
                }
            }
            if !report.conflicts.is_empty() {
                std::process::exit(3);
            }
            Ok(())
        }
    }
}

async fn serve(vault: PathBuf, port: u16, no_open: bool) -> Result<()> {
    let vault = vault.canonicalize().context("Vault path does not exist")?;
    let registry = load_registry(&vault)?;
    let token = format!(
        "{}-{}",
        std::process::id(),
        chrono::Utc::now().timestamp_nanos_opt().unwrap_or_default()
    );
    let state = Arc::new(AppState::new(vault, registry, token));

    let app = Router::new()
        .route("/", get(index))
        .route("/app.js", get(js))
        .route("/styles.css", get(css))
        .route("/vendor/tabler.min.css", get(tabler_css))
        .route("/logo.svg", get(logo))
        .route("/api/items", get(items))
        .route("/api/tags", get(tags))
        .route("/api/schema", get(schema))
        .route("/api/content", post(create_content))
        .route("/api/content/metadata", patch(update_metadata))
        .route("/api/content/fill-defaults", post(fill_defaults))
        .route("/api/tags/replace", post(replace_tags))
        .route("/api/open", post(open_item))
        .route("/api/validate", post(validate))
        .with_state(state.clone());

    let addr = SocketAddr::from(([127, 0, 0, 1], port));
    let listener = match TcpListener::bind(addr).await {
        Ok(listener) => listener,
        Err(_) => TcpListener::bind(SocketAddr::from(([127, 0, 0, 1], 0))).await?,
    };
    let actual = listener.local_addr()?;
    let url = format!("http://{actual}/");
    println!("Vault Manager: {url}");
    println!("Vault: {}", state.vault.display());
    if !no_open {
        let _ = open::that(&url);
    }
    axum::serve(listener, app).await?;
    Ok(())
}

async fn index(State(state): State<Arc<AppState>>) -> Html<String> {
    Html(INDEX_HTML.replace("__VAULT_TOKEN__", &state.token))
}

async fn js() -> impl IntoResponse {
    (
        [(header::CONTENT_TYPE, "text/javascript; charset=utf-8")],
        APP_JS,
    )
}

async fn css() -> impl IntoResponse {
    (
        [(header::CONTENT_TYPE, "text/css; charset=utf-8")],
        STYLES_CSS,
    )
}

async fn tabler_css() -> impl IntoResponse {
    (
        [(header::CONTENT_TYPE, "text/css; charset=utf-8")],
        TABLER_CSS,
    )
}

async fn logo() -> impl IntoResponse {
    (
        [(header::CONTENT_TYPE, "image/svg+xml; charset=utf-8")],
        LOGO_SVG,
    )
}

#[derive(Deserialize, Default)]
struct ItemQuery {
    kind: Option<String>,
    publish: Option<bool>,
    tag: Option<String>,
    q: Option<String>,
}

async fn items(State(state): State<Arc<AppState>>, Query(query): Query<ItemQuery>) -> ApiResult {
    let mut items = scan_items(&state.vault, &state.registry)?;
    items.retain(|item| {
        query.kind.as_ref().is_none_or(|v| &item.kind == v)
            && query.publish.is_none_or(|v| item.publish == Some(v))
            && query
                .tag
                .as_ref()
                .is_none_or(|v| item.tags.iter().any(|tag| tag == v))
            && query.q.as_ref().is_none_or(|v| {
                let q = v.to_lowercase();
                item.title.to_lowercase().contains(&q) || item.relative.to_lowercase().contains(&q)
            })
    });
    Ok(Json(json!({"items": items, "count": items.len()})))
}

async fn tags(State(state): State<Arc<AppState>>) -> ApiResult {
    let items = scan_items(&state.vault, &state.registry)?;
    let mut counts = std::collections::BTreeMap::<String, usize>::new();
    for item in items {
        for tag in item.tags {
            *counts.entry(tag).or_default() += 1;
        }
    }
    Ok(Json(json!({
        "order": state.registry.tag_order,
        "semantic": state.registry.semantic_tags,
        "aliases": state.registry.tag_aliases,
        "counts": counts
    })))
}

async fn schema(State(state): State<Arc<AppState>>) -> ApiResult {
    Ok(Json(json!({
        "version": state.registry.version,
        "types": state.registry.types,
        "placeholders": state.registry.placeholder_values
    })))
}

async fn create_content(
    State(state): State<Arc<AppState>>,
    headers: HeaderMap,
    Json(payload): Json<CreateRequest>,
) -> ApiResult {
    require_token(&state, &headers)?;
    let item = vault::create_content(&state, payload)?;
    Ok(Json(json!({"item": item})))
}

async fn update_metadata(
    State(state): State<Arc<AppState>>,
    headers: HeaderMap,
    Json(payload): Json<MetadataPatch>,
) -> ApiResult {
    require_token(&state, &headers)?;
    let item = vault::update_metadata(&state, payload)?;
    Ok(Json(json!({"item": item})))
}

async fn fill_defaults(
    State(state): State<Arc<AppState>>,
    headers: HeaderMap,
    Json(payload): Json<PathRequest>,
) -> ApiResult {
    require_token(&state, &headers)?;
    let report = fill_missing_metadata(&state, &payload.path)?;
    Ok(Json(serde_json::to_value(report)?))
}

async fn replace_tags(
    State(state): State<Arc<AppState>>,
    headers: HeaderMap,
    Json(payload): Json<TagReplaceRequest>,
) -> ApiResult {
    require_token(&state, &headers)?;
    let report = vault::replace_tags(&state, payload)?;
    Ok(Json(serde_json::to_value(report)?))
}

#[derive(Deserialize)]
struct PathRequest {
    path: String,
}

async fn open_item(
    State(state): State<Arc<AppState>>,
    headers: HeaderMap,
    Json(payload): Json<PathRequest>,
) -> ApiResult {
    require_token(&state, &headers)?;
    let path = vault::resolve_allowed_path(&state.vault, &payload.path)?;
    open::that(path)?;
    Ok(Json(json!({"opened": payload.path})))
}

async fn validate(State(state): State<Arc<AppState>>, headers: HeaderMap) -> ApiResult {
    require_token(&state, &headers)?;
    let report = validate_vault(&state.vault, &state.registry, "all")?;
    Ok(Json(serde_json::to_value(report)?))
}

fn require_token(state: &AppState, headers: &HeaderMap) -> Result<(), ApiError> {
    let supplied = headers.get("x-vault-token").and_then(|v| v.to_str().ok());
    if supplied != Some(state.token.as_str()) {
        return Err(ApiError::new(
            StatusCode::FORBIDDEN,
            "Invalid local session token",
        ));
    }
    Ok(())
}

type ApiResult = Result<Json<Value>, ApiError>;

struct ApiError {
    status: StatusCode,
    message: String,
}

impl ApiError {
    fn new(status: StatusCode, message: impl Into<String>) -> Self {
        Self {
            status,
            message: message.into(),
        }
    }
}

impl From<anyhow::Error> for ApiError {
    fn from(value: anyhow::Error) -> Self {
        Self::new(StatusCode::BAD_REQUEST, value.to_string())
    }
}

impl From<serde_json::Error> for ApiError {
    fn from(value: serde_json::Error) -> Self {
        Self::new(StatusCode::BAD_REQUEST, value.to_string())
    }
}

impl From<std::io::Error> for ApiError {
    fn from(value: std::io::Error) -> Self {
        Self::new(StatusCode::BAD_REQUEST, value.to_string())
    }
}

impl IntoResponse for ApiError {
    fn into_response(self) -> Response {
        (self.status, Json(json!({"error": self.message}))).into_response()
    }
}
