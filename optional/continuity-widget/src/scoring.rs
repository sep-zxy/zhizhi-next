use crate::models::ActivityBreakdown;
use chrono::{DateTime, Utc};

pub fn calculate_activity(
    now: DateTime<Utc>,
    last_evidence_at: Option<DateTime<Utc>>,
    evidence_count_last_7_days: i64,
    priority: i32,
) -> ActivityBreakdown {
    let recency = last_evidence_at
        .map(|last| {
            let days = now.signed_duration_since(last).num_days().max(0) as i32;
            (50 - days * 2).max(0)
        })
        .unwrap_or(0);
    let frequency = (evidence_count_last_7_days.max(0) as i32 * 6).min(30);
    let priority = priority.clamp(0, 20);
    ActivityBreakdown {
        recency,
        frequency,
        priority,
        total: (recency + frequency + priority).clamp(0, 100),
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn activity_is_clamped_to_expected_range() {
        let now = Utc::now();
        assert_eq!(calculate_activity(now, None, 0, 99).total, 20);
        assert_eq!(calculate_activity(now, Some(now), 99, 20).total, 100);
    }
}
