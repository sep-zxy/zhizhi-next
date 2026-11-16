pub mod database;
pub mod events;
pub mod migration;
pub mod models;
pub mod progress;
pub mod repair;
pub mod scoring;
pub mod service;
pub mod snapshot;

pub use database::{Database, DatabaseOpen};
pub use models::*;
pub use service::ContinuityService;

pub const DEFAULT_VAULT: &str = r"C:\Code\notes\zxy";
