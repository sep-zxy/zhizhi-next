#![cfg_attr(all(windows, not(debug_assertions)), windows_subsystem = "windows")]

use std::{fs, path::PathBuf};

fn main() {
    if let Err(error) = vault_manager::run() {
        let log_path = error_log_path();
        let message = format!("Vault Manager 启动失败\n\n{error:#}\n");
        let _ = fs::write(&log_path, message.as_bytes());
        let _ = open::that(log_path);
    }
}

fn error_log_path() -> PathBuf {
    std::env::current_exe()
        .ok()
        .and_then(|path| {
            path.parent()
                .map(|parent| parent.join("vault-manager-error.log"))
        })
        .unwrap_or_else(|| PathBuf::from("vault-manager-error.log"))
}
