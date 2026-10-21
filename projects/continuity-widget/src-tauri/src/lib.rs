use continuity_core::{
    ContinuityService, DashboardView, EditorView, EntityCommandInput, ItemPatchInput,
    MilestonePatchInput, MilestoneUpsertInput, MutationResult, Project, ProjectMilestone,
    ProjectPatchInput, ProjectPreview, QuickAddInput, RevisionUpdate, Settings, SettingsPatchInput,
    SuggestionDecisionInput, WindowState,
};
use serde::Serialize;
use std::process::Command;
use tauri::{
    AppHandle, Emitter, Manager, PhysicalPosition, PhysicalSize, State, WebviewWindow,
    menu::{Menu, MenuItem},
    tray::{MouseButton, MouseButtonState, TrayIconBuilder, TrayIconEvent},
};

struct AppState {
    service: ContinuityService,
}

#[derive(Debug, Clone, Serialize)]
#[serde(rename_all = "camelCase")]
struct SnapResult {
    edge: String,
}

#[derive(Debug, Clone, Copy)]
struct Rect {
    x: i32,
    y: i32,
    width: i32,
    height: i32,
}

#[tauri::command]
fn get_dashboard(state: State<'_, AppState>) -> Result<DashboardView, String> {
    state.service.dashboard().map_err(format_error)
}

#[tauri::command]
fn get_editor(state: State<'_, AppState>) -> Result<EditorView, String> {
    state.service.editor().map_err(format_error)
}

#[tauri::command]
fn get_project_preview(
    project_id: String,
    state: State<'_, AppState>,
) -> Result<ProjectPreview, String> {
    state
        .service
        .project_preview(&project_id)
        .map_err(format_error)
}

#[tauri::command]
fn revision_poll(
    since_revision: i64,
    state: State<'_, AppState>,
) -> Result<RevisionUpdate, String> {
    state
        .service
        .revision_update(since_revision)
        .map_err(format_error)
}

#[tauri::command]
fn quick_add(
    input: QuickAddInput,
    app: AppHandle,
    state: State<'_, AppState>,
) -> Result<MutationResult<continuity_core::Item>, String> {
    let result = state.service.quick_add(input).map_err(format_error)?;
    emit_revision(&app, result.revision);
    Ok(result)
}

#[tauri::command]
fn complete_item(
    input: EntityCommandInput,
    app: AppHandle,
    state: State<'_, AppState>,
) -> Result<MutationResult<continuity_core::Item>, String> {
    let result = state.service.complete_item(input).map_err(format_error)?;
    emit_revision(&app, result.revision);
    Ok(result)
}

#[tauri::command]
fn confirm_project(
    input: EntityCommandInput,
    app: AppHandle,
    state: State<'_, AppState>,
) -> Result<MutationResult<Project>, String> {
    let result = state.service.confirm_project(input).map_err(format_error)?;
    emit_revision(&app, result.revision);
    Ok(result)
}

#[tauri::command]
fn save_project(
    input: ProjectPatchInput,
    app: AppHandle,
    state: State<'_, AppState>,
) -> Result<MutationResult<Project>, String> {
    let result = state.service.save_project(input).map_err(format_error)?;
    emit_revision(&app, result.revision);
    Ok(result)
}

#[tauri::command]
fn save_item(
    input: ItemPatchInput,
    app: AppHandle,
    state: State<'_, AppState>,
) -> Result<MutationResult<continuity_core::Item>, String> {
    let result = state.service.save_item(input).map_err(format_error)?;
    emit_revision(&app, result.revision);
    Ok(result)
}

#[tauri::command]
fn upsert_milestone(
    input: MilestoneUpsertInput,
    app: AppHandle,
    state: State<'_, AppState>,
) -> Result<MutationResult<ProjectMilestone>, String> {
    let result = state
        .service
        .milestone_upsert(input)
        .map_err(format_error)?;
    emit_revision(&app, result.revision);
    Ok(result)
}

#[tauri::command]
fn save_milestone(
    input: MilestonePatchInput,
    app: AppHandle,
    state: State<'_, AppState>,
) -> Result<MutationResult<ProjectMilestone>, String> {
    let result = state.service.save_milestone(input).map_err(format_error)?;
    emit_revision(&app, result.revision);
    Ok(result)
}

#[tauri::command]
fn apply_suggestion(
    input: SuggestionDecisionInput,
    app: AppHandle,
    state: State<'_, AppState>,
) -> Result<MutationResult<continuity_core::ItemSuggestion>, String> {
    let result = state
        .service
        .apply_suggestion(input)
        .map_err(format_error)?;
    emit_revision(&app, result.revision);
    Ok(result)
}

#[tauri::command]
fn reject_suggestion(
    input: SuggestionDecisionInput,
    app: AppHandle,
    state: State<'_, AppState>,
) -> Result<MutationResult<continuity_core::ItemSuggestion>, String> {
    let result = state
        .service
        .reject_suggestion(input)
        .map_err(format_error)?;
    emit_revision(&app, result.revision);
    Ok(result)
}

#[tauri::command]
fn save_settings(
    input: SettingsPatchInput,
    app: AppHandle,
    state: State<'_, AppState>,
) -> Result<MutationResult<Settings>, String> {
    let result = state.service.save_settings(input).map_err(format_error)?;
    emit_revision(&app, result.revision);
    Ok(result)
}

#[tauri::command]
fn open_editor(app: AppHandle) -> Result<(), String> {
    let window = app
        .get_webview_window("main")
        .ok_or_else(|| "项目工作区未初始化".to_owned())?;
    window.show().map_err(|error| error.to_string())?;
    window.set_focus().map_err(|error| error.to_string())?;
    Ok(())
}

#[tauri::command]
fn open_widget(app: AppHandle) -> Result<(), String> {
    let window = app
        .get_webview_window("widget")
        .ok_or_else(|| "紧凑组件未初始化".to_owned())?;
    window.show().map_err(|error| error.to_string())?;
    window.set_focus().map_err(|error| error.to_string())?;
    Ok(())
}

#[tauri::command]
fn show_preview(project_id: String, app: AppHandle) -> Result<(), String> {
    let widget = app
        .get_webview_window("widget")
        .ok_or_else(|| "紧凑组件不存在".to_owned())?;
    let monitor = widget
        .current_monitor()
        .map_err(|error| error.to_string())?
        .ok_or_else(|| "无法确定当前显示器".to_owned())?;
    let position = widget.outer_position().map_err(|error| error.to_string())?;
    let size = widget.outer_size().map_err(|error| error.to_string())?;
    let area = monitor.work_area();
    let work = Rect {
        x: area.position.x,
        y: area.position.y,
        width: area.size.width as i32,
        height: area.size.height as i32,
    };
    let anchor = Rect {
        x: position.x,
        y: position.y,
        width: size.width as i32,
        height: size.height as i32,
    };
    let preview_size = (360, (anchor.height.max(480)).min(work.height - 16));
    let target = choose_preview_position(anchor, work, preview_size.0, preview_size.1);
    let preview = app
        .get_webview_window("preview")
        .ok_or_else(|| "预览窗口未初始化".to_owned())?;
    preview
        .set_size(PhysicalSize::new(
            preview_size.0 as u32,
            preview_size.1 as u32,
        ))
        .map_err(|error| error.to_string())?;
    preview
        .set_position(PhysicalPosition::new(target.x, target.y))
        .map_err(|error| error.to_string())?;
    let payload = serde_json::to_string(&project_id).map_err(|error| error.to_string())?;
    preview.eval(&format!("window.__previewProjectId={payload};window.dispatchEvent(new CustomEvent('continuity-preview-project',{{detail:{payload}}}));")).map_err(|error| error.to_string())?;
    preview.show().map_err(|error| error.to_string())?;
    Ok(())
}

#[tauri::command]
fn hide_preview(app: AppHandle) -> Result<(), String> {
    if let Some(window) = app.get_webview_window("preview") {
        window.hide().map_err(|error| error.to_string())?;
    }
    Ok(())
}

#[tauri::command]
fn open_project_path(project_id: String, state: State<'_, AppState>) -> Result<(), String> {
    let preview = state
        .service
        .project_preview(&project_id)
        .map_err(format_error)?;
    let path = preview
        .project
        .project
        .project_path
        .ok_or_else(|| "项目未绑定目录".to_owned())?;
    Command::new("explorer.exe")
        .arg(path)
        .spawn()
        .map_err(|error| error.to_string())?;
    Ok(())
}

#[tauri::command]
fn snap_window(
    window: WebviewWindow,
    forced_edge: Option<String>,
    state: State<'_, AppState>,
) -> Result<Option<SnapResult>, String> {
    let monitor = window
        .current_monitor()
        .map_err(|error| error.to_string())?
        .ok_or_else(|| "无法确定窗口所在显示器".to_owned())?;
    let position = window.outer_position().map_err(|error| error.to_string())?;
    let size = window.outer_size().map_err(|error| error.to_string())?;
    let area = monitor.work_area();
    let left = area.position.x;
    let top = area.position.y;
    let right = left + area.size.width as i32;
    let bottom = top + area.size.height as i32;
    let width = size.width as i32;
    let height = size.height as i32;
    let threshold = (32.0 * monitor.scale_factor()).round() as i32;
    let margin = (8.0 * monitor.scale_factor()).round() as i32;
    let candidates = [
        ("left", (position.x - left).abs()),
        ("right", (position.x + width - right).abs()),
        ("top", (position.y - top).abs()),
        ("bottom", (position.y + height - bottom).abs()),
    ];
    let edge = match forced_edge.as_deref() {
        Some(edge) if candidates.iter().any(|candidate| candidate.0 == edge) => edge,
        Some(_) => return Err("未知的贴边方向".to_owned()),
        None => {
            let Some((edge, distance)) = candidates.iter().min_by_key(|candidate| candidate.1)
            else {
                return Ok(None);
            };
            if *distance > threshold {
                return Ok(None);
            }
            edge
        }
    };
    let min_x = left + margin;
    let max_x = (right - width - margin).max(min_x);
    let min_y = top + margin;
    let max_y = (bottom - height - margin).max(min_y);
    let (x, y) = match edge {
        "left" => (min_x, position.y.clamp(min_y, max_y)),
        "right" => (max_x, position.y.clamp(min_y, max_y)),
        "top" => (position.x.clamp(min_x, max_x), min_y),
        "bottom" => (position.x.clamp(min_x, max_x), max_y),
        _ => unreachable!(),
    };
    if position.x != x || position.y != y {
        window
            .set_position(PhysicalPosition::new(x, y))
            .map_err(|error| error.to_string())?;
    }
    save_current_window_state(&window, Some(edge.to_owned()), None, &state.service)?;
    Ok(Some(SnapResult {
        edge: edge.to_owned(),
    }))
}

#[tauri::command]
fn set_height_mode(
    mode: String,
    desired_height: Option<u32>,
    window: WebviewWindow,
    state: State<'_, AppState>,
) -> Result<(), String> {
    if mode != "fit" && mode != "full" {
        return Err("未知高度模式".to_owned());
    }
    let monitor = window
        .current_monitor()
        .map_err(|error| error.to_string())?
        .ok_or_else(|| "无法确定当前显示器".to_owned())?;
    let maximum = monitor.work_area().size.height.saturating_sub(16);
    let height = if mode == "full" {
        maximum
    } else {
        desired_height.unwrap_or(720).clamp(420, maximum)
    };
    let width = window
        .outer_size()
        .map_err(|error| error.to_string())?
        .width;
    window
        .set_size(PhysicalSize::new(width, height))
        .map_err(|error| error.to_string())?;
    save_current_window_state(&window, None, Some(mode), &state.service)?;
    Ok(())
}

#[tauri::command]
fn capture_window_state(window: WebviewWindow, state: State<'_, AppState>) -> Result<(), String> {
    save_current_window_state(&window, None, None, &state.service)
}

#[tauri::command]
fn quit_app(app: AppHandle) {
    app.exit(0);
}

fn save_current_window_state(
    window: &WebviewWindow,
    snap_edge: Option<String>,
    height_mode: Option<String>,
    service: &ContinuityService,
) -> Result<(), String> {
    let position = window.outer_position().map_err(|error| error.to_string())?;
    let size = window.outer_size().map_err(|error| error.to_string())?;
    let monitor_name = window
        .current_monitor()
        .map_err(|error| error.to_string())?
        .and_then(|monitor| monitor.name().cloned());
    let existing = service
        .load_window_state(window.label())
        .map_err(format_error)?;
    service
        .save_window_state(WindowState {
            label: window.label().to_owned(),
            x: position.x,
            y: position.y,
            width: size.width as i32,
            height: size.height as i32,
            snap_edge: snap_edge
                .or_else(|| existing.as_ref().and_then(|value| value.snap_edge.clone())),
            height_mode: height_mode
                .or_else(|| existing.as_ref().map(|value| value.height_mode.clone()))
                .unwrap_or_else(|| "fit".to_owned()),
            monitor_name,
        })
        .map_err(format_error)?;
    Ok(())
}

fn choose_preview_position(anchor: Rect, work: Rect, width: i32, height: i32) -> Rect {
    let gap = 8;
    let min_x = work.x + gap;
    let max_x = (work.x + work.width - width - gap).max(min_x);
    let min_y = work.y + gap;
    let max_y = (work.y + work.height - height - gap).max(min_y);
    let left = anchor.x - width - gap;
    let right = anchor.x + anchor.width + gap;
    let x = if left >= min_x {
        left
    } else {
        right.clamp(min_x, max_x)
    };
    Rect {
        x,
        y: anchor.y.clamp(min_y, max_y),
        width,
        height,
    }
}

fn emit_revision(app: &AppHandle, revision: i64) {
    let _ = app.emit("continuity://revision", revision);
}

fn show_main(app: &AppHandle) {
    if let Some(window) = app.get_webview_window("main") {
        let _ = window.show();
        let _ = window.set_focus();
    }
}

fn format_error(error: anyhow::Error) -> String {
    format!("{error:#}")
}

pub fn run() {
    let service = ContinuityService::open_default().expect("初始化 Continuity SQLite 服务失败");
    tauri::Builder::default()
        .plugin(tauri_plugin_notification::init())
        .plugin(
            tauri_plugin_autostart::Builder::new()
                .app_name("ZxyContinuityWidget")
                .args(["--background"])
                .build(),
        )
        .manage(AppState { service })
        .invoke_handler(tauri::generate_handler![
            get_dashboard,
            get_editor,
            get_project_preview,
            revision_poll,
            quick_add,
            complete_item,
            confirm_project,
            save_project,
            save_item,
            upsert_milestone,
            save_milestone,
            apply_suggestion,
            reject_suggestion,
            save_settings,
            open_editor,
            open_widget,
            show_preview,
            hide_preview,
            open_project_path,
            snap_window,
            set_height_mode,
            capture_window_state,
            quit_app
        ])
        .setup(|app| {
            let show = MenuItem::with_id(app, "show", "显示项目工作区", true, None::<&str>)?;
            let widget = MenuItem::with_id(app, "widget", "显示紧凑组件", true, None::<&str>)?;
            let quit = MenuItem::with_id(app, "quit", "退出", true, None::<&str>)?;
            let menu = Menu::with_items(app, &[&show, &widget, &quit])?;
            let icon = app.default_window_icon().cloned().ok_or("应用图标未加载")?;
            TrayIconBuilder::new()
                .icon(icon)
                .menu(&menu)
                .show_menu_on_left_click(false)
                .on_menu_event(|app, event| match event.id.as_ref() {
                    "show" => show_main(app),
                    "widget" => {
                        let _ = open_widget(app.clone());
                    }
                    "quit" => app.exit(0),
                    _ => {}
                })
                .on_tray_icon_event(|tray, event| {
                    if let TrayIconEvent::Click {
                        button: MouseButton::Left,
                        button_state: MouseButtonState::Up,
                        ..
                    } = event
                    {
                        show_main(tray.app_handle());
                    }
                })
                .build(app)?;
            if std::env::args().any(|arg| arg == "--background") {
                if let Some(window) = app.get_webview_window("main") {
                    let _ = window.hide();
                }
            }
            Ok(())
        })
        .on_window_event(|window, event| {
            if let tauri::WindowEvent::CloseRequested { api, .. } = event {
                api.prevent_close();
                let _ = window.hide();
            }
        })
        .run(tauri::generate_context!())
        .expect("启动 Continuity 项目工作区失败");
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn preview_prefers_left_then_clamps_right() {
        let work = Rect {
            x: 0,
            y: 0,
            width: 1920,
            height: 1080,
        };
        let left = choose_preview_position(
            Rect {
                x: 800,
                y: 100,
                width: 450,
                height: 720,
            },
            work,
            360,
            720,
        );
        assert_eq!(left.x, 432);
        let right = choose_preview_position(
            Rect {
                x: 12,
                y: 100,
                width: 450,
                height: 720,
            },
            work,
            360,
            720,
        );
        assert_eq!(right.x, 470);
    }
}
