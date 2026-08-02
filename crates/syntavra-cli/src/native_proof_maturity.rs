#![forbid(unsafe_code)]

use std::cmp::Ordering;
use std::collections::hash_map::Entry;
use std::collections::{BTreeMap, BTreeSet, HashMap};
use std::fs;
use std::path::Path;
use std::time::{SystemTime, UNIX_EPOCH};

use serde_json::{json, Value};

const VERSION: &str = "0.0.1";
const CHANNEL: &str = "pre-release";

const MINIMUM_DAYS: i64 = 90;
const MINIMUM_ONBOARDING_RECEIPTS: usize = 1_000;
const MINIMUM_USERS: usize = 50;
const MINIMUM_REPOSITORIES: usize = 100;
const MINIMUM_INTEGRATIONS: usize = 5;
const MINIMUM_OPERATING_SYSTEMS: usize = 3;
const MINIMUM_ONBOARDING_SUCCESS: f64 = 0.99;
const MAXIMUM_P95_INSTALL_SECONDS: f64 = 60.0;
const MINIMUM_DISTRIBUTION_CHANNELS: usize = 2;
const MINIMUM_PUBLIC_DOWNLOADS: i64 = 1_000;
const MINIMUM_UNIQUE_INSTALLATIONS: i64 = 250;
const MINIMUM_VERIFIED_RELEASES: usize = 4;
const MAXIMUM_RELEASE_GAP_DAYS: f64 = 45.0;

pub struct ProofDecision {
    pub value: Value,
    pub exit_code: u8,
}

#[derive(Clone, Copy, PartialEq, Eq)]
enum Flag {
    Enabled,
    Disabled,
}

impl Flag {
    const fn from_bool(value: bool) -> Self {
        if value {
            Self::Enabled
        } else {
            Self::Disabled
        }
    }

    const fn is_enabled(self) -> bool {
        matches!(self, Self::Enabled)
    }
}

#[derive(Clone)]
struct OnboardingReceipt {
    observed_at: String,
    user_hash: String,
    repository_hash: String,
    integration_id: String,
    operating_system: String,
    install_wall_time_ms: f64,
    success: Flag,
    rollback_verified: Flag,
    doctor_passed: Flag,
    synthetic: Flag,
    version: String,
    channel: String,
}

#[derive(Clone)]
struct DistributionReceipt {
    observed_at: String,
    channel_name: String,
    version: String,
    downloads: i64,
    unique_installations: i64,
    source_verified: Flag,
    synthetic: Flag,
}

#[derive(Clone)]
struct ReleaseReceipt {
    published_at: String,
    version: String,
    channel: String,
    signed: Flag,
    provenance: Flag,
    source_verified: Flag,
    synthetic: Flag,
}

#[derive(Clone)]
struct SignalRun {
    task_id: String,
    arm_id: String,
    repetition: i64,
    success: Flag,
    verified_work: f64,
    quota_cost: Option<f64>,
    security_regressions: i64,
    verifier_skips: i64,
    cache_mode: String,
    provider_observed: Flag,
}

struct MaturityDocument {
    onboarding: Vec<OnboardingReceipt>,
    distributions: Vec<DistributionReceipt>,
    releases: Vec<ReleaseReceipt>,
}

struct OnboardingAssessment {
    earliest: Option<f64>,
    receipts: usize,
    users: usize,
    repositories: usize,
    integrations: usize,
    operating_systems: usize,
    success_rate: f64,
    rollback_rate: f64,
    mean_install_ms: Option<f64>,
    p95_install_ms: Option<f64>,
    reasons: Vec<String>,
}

struct DistributionAssessment {
    earliest: Option<f64>,
    channels: usize,
    downloads: i64,
    installations: i64,
    reasons: Vec<String>,
}

struct ReleaseAssessment {
    earliest: Option<f64>,
    releases: usize,
    maximum_gap_days: f64,
    reasons: Vec<String>,
}

type SignalKey = (String, i64, String, String);

struct SignalIndex {
    rows: HashMap<SignalKey, SignalRun>,
    order: Vec<SignalKey>,
}

struct ProviderPairStats {
    ratios: Vec<f64>,
    observed_pairs: Vec<Flag>,
    invalid_pairs: Vec<Value>,
    quality: BTreeMap<String, i64>,
    total: BTreeMap<String, i64>,
}

fn string_value(value: Option<&Value>, default: &str) -> String {
    match value {
        None => default.to_owned(),
        Some(Value::String(text)) => text.clone(),
        Some(Value::Null) => "None".to_owned(),
        Some(Value::Bool(flag)) => if *flag { "True" } else { "False" }.to_owned(),
        Some(Value::Number(number)) => number.to_string(),
        Some(other) => other.to_string(),
    }
}

fn bool_value(value: Option<&Value>, default: bool) -> bool {
    value.map_or(default, |item| match item {
        Value::Null => false,
        Value::Bool(flag) => *flag,
        Value::Number(number) => number.as_f64().is_some_and(|number| number != 0.0),
        Value::String(text) => !text.is_empty(),
        Value::Array(items) => !items.is_empty(),
        Value::Object(items) => !items.is_empty(),
    })
}

fn integer_value(value: Option<&Value>, default: i64) -> i64 {
    value
        .and_then(|item| {
            item.as_i64()
                .or_else(|| item.as_bool().map(i64::from))
                .or_else(|| item.as_str().and_then(|text| text.parse::<i64>().ok()))
                .or_else(|| {
                    item.as_f64().and_then(|number| {
                        if number.is_finite() {
                            number.trunc().to_string().parse::<i64>().ok()
                        } else {
                            None
                        }
                    })
                })
        })
        .unwrap_or(default)
}

fn float_value(value: Option<&Value>, default: f64) -> f64 {
    value
        .and_then(|item| {
            item.as_f64()
                .or_else(|| item.as_bool().map(|flag| if flag { 1.0 } else { 0.0 }))
                .or_else(|| item.as_str().and_then(|text| text.parse::<f64>().ok()))
        })
        .unwrap_or(default)
}

fn i64_as_f64(value: i64) -> f64 {
    value.to_string().parse::<f64>().unwrap_or(0.0)
}

fn usize_as_f64(value: usize) -> f64 {
    value.to_string().parse::<f64>().unwrap_or(0.0)
}

fn floor_as_usize(value: f64) -> usize {
    if !value.is_finite() || value <= 0.0 {
        return 0;
    }
    value
        .floor()
        .to_string()
        .split('.')
        .next()
        .and_then(|text| text.parse::<usize>().ok())
        .unwrap_or(0)
}

impl OnboardingReceipt {
    fn from_value(value: &Value) -> Self {
        Self {
            observed_at: string_value(value.get("observed_at"), ""),
            user_hash: string_value(value.get("user_hash"), ""),
            repository_hash: string_value(value.get("repository_hash"), ""),
            integration_id: string_value(value.get("integration_id"), ""),
            operating_system: string_value(value.get("operating_system"), ""),
            install_wall_time_ms: float_value(value.get("install_wall_time_ms"), -1.0),
            success: Flag::from_bool(bool_value(value.get("success"), false)),
            rollback_verified: Flag::from_bool(bool_value(value.get("rollback_verified"), false)),
            doctor_passed: Flag::from_bool(bool_value(value.get("doctor_passed"), false)),
            synthetic: Flag::from_bool(bool_value(value.get("synthetic"), true)),
            version: string_value(value.get("version"), VERSION),
            channel: string_value(value.get("channel"), CHANNEL),
        }
    }
}

impl DistributionReceipt {
    fn from_value(value: &Value) -> Self {
        Self {
            observed_at: string_value(value.get("observed_at"), ""),
            channel_name: string_value(value.get("channel_name"), ""),
            version: string_value(value.get("version"), ""),
            downloads: integer_value(value.get("downloads"), -1),
            unique_installations: integer_value(value.get("unique_installations"), -1),
            source_verified: Flag::from_bool(bool_value(value.get("source_verified"), false)),
            synthetic: Flag::from_bool(bool_value(value.get("synthetic"), true)),
        }
    }
}

impl ReleaseReceipt {
    fn from_value(value: &Value) -> Self {
        Self {
            published_at: string_value(value.get("published_at"), ""),
            version: string_value(value.get("version"), ""),
            channel: string_value(value.get("channel"), ""),
            signed: Flag::from_bool(bool_value(value.get("signed"), false)),
            provenance: Flag::from_bool(bool_value(value.get("provenance"), false)),
            source_verified: Flag::from_bool(bool_value(value.get("source_verified"), false)),
            synthetic: Flag::from_bool(bool_value(value.get("synthetic"), true)),
        }
    }
}

impl SignalRun {
    fn from_value(value: &Value) -> Self {
        Self {
            task_id: string_value(value.get("task_id"), ""),
            arm_id: string_value(value.get("arm_id"), ""),
            repetition: integer_value(value.get("repetition"), 0),
            success: Flag::from_bool(bool_value(value.get("success"), false)),
            verified_work: float_value(value.get("verified_work"), 0.0),
            quota_cost: value
                .get("quota_cost")
                .filter(|item| !item.is_null())
                .map(|item| float_value(Some(item), 0.0)),
            security_regressions: integer_value(value.get("security_regressions"), 0),
            verifier_skips: integer_value(value.get("verifier_skips"), 0),
            cache_mode: string_value(value.get("cache_mode"), ""),
            provider_observed: Flag::from_bool(bool_value(value.get("provider_observed"), false)),
        }
    }
}

fn load_json_argument(argument: &str) -> Result<Value, String> {
    let path = Path::new(argument);
    let text = if path.is_file() {
        fs::read_to_string(path).map_err(|error| format!("PROOF_DOCUMENT_READ_FAILED:{error}"))?
    } else {
        argument.to_owned()
    };
    serde_json::from_str(&text).map_err(|error| format!("PROOF_DOCUMENT_JSON_INVALID:{error}"))
}

fn command_path<'a>(arguments: &'a [String], action: &str) -> Result<&'a str, String> {
    let index = arguments
        .windows(2)
        .position(|window| window[0] == "prove" && window[1] == action)
        .ok_or_else(|| "PROOF_COMMAND_MISSING".to_owned())?;
    arguments
        .get(index + 2)
        .filter(|value| !value.starts_with('-'))
        .map(String::as_str)
        .ok_or_else(|| "PROOF_DOCUMENT_PATH_MISSING".to_owned())
}

fn option_value(arguments: &[String], name: &str, default: &str) -> String {
    arguments
        .iter()
        .position(|value| value == name)
        .and_then(|index| arguments.get(index + 1))
        .cloned()
        .or_else(|| {
            let prefix = format!("{name}=");
            arguments
                .iter()
                .find_map(|value| value.strip_prefix(&prefix).map(str::to_owned))
        })
        .unwrap_or_else(|| default.to_owned())
}

fn civil_days(year: i64, month: i64, day: i64) -> i64 {
    let adjusted_year = year - i64::from(month <= 2);
    let era = if adjusted_year >= 0 {
        adjusted_year
    } else {
        adjusted_year - 399
    } / 400;
    let year_of_era = adjusted_year - era * 400;
    let adjusted_month = month + if month > 2 { -3 } else { 9 };
    let day_of_year = (153 * adjusted_month + 2) / 5 + day - 1;
    let day_of_era = year_of_era * 365 + year_of_era / 4 - year_of_era / 100 + day_of_year;
    era * 146_097 + day_of_era - 719_468
}

fn parse_two(text: &str) -> Option<i64> {
    if text.len() != 2 {
        return None;
    }
    text.parse::<i64>().ok()
}

fn parse_datetime(value: &str) -> Option<f64> {
    let normalized = value.strip_suffix('Z').unwrap_or(value);
    let (date, time_and_zone) = normalized
        .split_once('T')
        .or_else(|| normalized.split_once(' '))
        .map_or((normalized, "00:00:00"), |parts| parts);
    let mut date_parts = date.split('-');
    let year = date_parts.next()?.parse::<i64>().ok()?;
    let month = parse_two(date_parts.next()?)?;
    let day = parse_two(date_parts.next()?)?;
    if date_parts.next().is_some() || !(1..=12).contains(&month) || !(1..=31).contains(&day) {
        return None;
    }

    let zone_position = time_and_zone
        .char_indices()
        .skip(1)
        .find_map(|(index, character)| matches!(character, '+' | '-').then_some(index));
    let (clock, zone) = zone_position.map_or((time_and_zone, ""), |index| {
        (&time_and_zone[..index], &time_and_zone[index..])
    });
    let mut clock_parts = clock.split(':');
    let hour = parse_two(clock_parts.next()?)?;
    let minute = parse_two(clock_parts.next()?)?;
    let second_text = clock_parts.next()?;
    if clock_parts.next().is_some() || hour > 23 || minute > 59 {
        return None;
    }
    let second = second_text.parse::<f64>().ok()?;
    if !(0.0..60.0).contains(&second) {
        return None;
    }
    let offset = parse_offset(zone)?;
    Some(
        i64_as_f64(civil_days(year, month, day)) * 86_400.0
            + i64_as_f64(hour) * 3_600.0
            + i64_as_f64(minute) * 60.0
            + second
            - offset,
    )
}

fn parse_offset(zone: &str) -> Option<f64> {
    if zone.is_empty() {
        return Some(0.0);
    }
    let sign = if zone.starts_with('-') { -1.0 } else { 1.0 };
    let zone = zone.get(1..)?;
    let (hours, minutes) = zone.split_once(':')?;
    let hours = hours.parse::<f64>().ok()?;
    let minutes = minutes.parse::<f64>().ok()?;
    Some(sign * ((hours * 60.0 + minutes) * 60.0))
}

fn current_epoch_seconds() -> f64 {
    SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .map_or(0.0, |duration| duration.as_secs_f64())
}

fn precise_mean(values: &[f64]) -> Option<f64> {
    if values.is_empty() {
        return None;
    }
    let mut partials = Vec::<f64>::new();
    for &value in values {
        let mut x = value;
        let mut index = 0usize;
        for position in 0..partials.len() {
            let mut y = partials[position];
            if x.abs() < y.abs() {
                std::mem::swap(&mut x, &mut y);
            }
            let high = x + y;
            let low = y - (high - x);
            if low != 0.0 {
                partials[index] = low;
                index += 1;
            }
            x = high;
        }
        partials.truncate(index);
        if x != 0.0 {
            partials.push(x);
        }
    }
    let sum = partials.iter().rev().sum::<f64>();
    Some(sum / usize_as_f64(values.len()))
}

fn python_round_nonnegative(value: f64) -> usize {
    let floor = floor_as_usize(value);
    let fraction = value - usize_as_f64(floor);
    if fraction < 0.5 {
        floor
    } else if fraction > 0.5 {
        floor + 1
    } else {
        floor + usize::from(floor % 2 == 1)
    }
}

fn percentile(values: &[f64], percentile: f64) -> f64 {
    if values.is_empty() {
        return 0.0;
    }
    let mut ordered = values.to_vec();
    ordered.sort_by(|left, right| left.partial_cmp(right).unwrap_or(Ordering::Equal));
    let raw = usize_as_f64(ordered.len() - 1) * percentile;
    let index = python_round_nonnegative(raw).min(ordered.len() - 1);
    ordered[index]
}

fn object_rows(value: Option<&Value>) -> Vec<&Value> {
    value
        .and_then(Value::as_array)
        .map_or_else(Vec::new, |rows| {
            rows.iter().filter(|row| row.is_object()).collect()
        })
}

impl MaturityDocument {
    fn from_value(value: &Value) -> Result<Self, String> {
        let object = value
            .as_object()
            .ok_or_else(|| "MATURITY_DOCUMENT_NOT_OBJECT".to_owned())?;
        Ok(Self {
            onboarding: object_rows(object.get("onboarding"))
                .into_iter()
                .map(OnboardingReceipt::from_value)
                .collect(),
            distributions: object_rows(object.get("distributions"))
                .into_iter()
                .map(DistributionReceipt::from_value)
                .collect(),
            releases: object_rows(object.get("releases"))
                .into_iter()
                .map(ReleaseReceipt::from_value)
                .collect(),
        })
    }

    fn contains_synthetic(&self) -> bool {
        self.onboarding.iter().any(|row| row.synthetic.is_enabled())
            || self
                .distributions
                .iter()
                .any(|row| row.synthetic.is_enabled())
            || self.releases.iter().any(|row| row.synthetic.is_enabled())
    }
}

fn assess_onboarding(rows: &[OnboardingReceipt]) -> OnboardingAssessment {
    let live = rows
        .iter()
        .filter_map(|row| {
            if row.synthetic.is_enabled() || row.version != VERSION || row.channel != CHANNEL {
                return None;
            }
            parse_datetime(&row.observed_at).map(|time| (row, time))
        })
        .collect::<Vec<_>>();
    let users = distinct_nonempty(&live, |row| row.user_hash.as_str());
    let repositories = distinct_nonempty(&live, |row| row.repository_hash.as_str());
    let integrations = distinct_nonempty(&live, |row| row.integration_id.as_str());
    let operating_systems = distinct_nonempty(&live, |row| row.operating_system.as_str());
    let denominator = usize_as_f64(live.len().max(1));
    let success_rate = usize_as_f64(
        live.iter()
            .filter(|(row, _)| row.success.is_enabled() && row.doctor_passed.is_enabled())
            .count(),
    ) / denominator;
    let rollback_rate = usize_as_f64(
        live.iter()
            .filter(|(row, _)| row.rollback_verified.is_enabled())
            .count(),
    ) / denominator;
    let install_times = live
        .iter()
        .filter_map(|(row, _)| {
            (row.install_wall_time_ms >= 0.0).then_some(row.install_wall_time_ms)
        })
        .collect::<Vec<_>>();
    let p95_install_ms = (!install_times.is_empty()).then_some(percentile(&install_times, 0.95));
    let mut assessment = OnboardingAssessment {
        earliest: minimum_time(live.iter().map(|(_, time)| *time)),
        receipts: live.len(),
        users,
        repositories,
        integrations,
        operating_systems,
        success_rate,
        rollback_rate,
        mean_install_ms: precise_mean(&install_times),
        p95_install_ms,
        reasons: Vec::new(),
    };
    add_onboarding_reasons(&mut assessment);
    assessment
}

fn distinct_nonempty<'a, F>(rows: &[(&'a OnboardingReceipt, f64)], value: F) -> usize
where
    F: Fn(&'a OnboardingReceipt) -> &'a str,
{
    rows.iter()
        .filter_map(|(row, _)| {
            let selected = value(row);
            (!selected.is_empty()).then_some(selected)
        })
        .collect::<BTreeSet<_>>()
        .len()
}

fn add_onboarding_reasons(assessment: &mut OnboardingAssessment) {
    for (condition, reason) in [
        (
            assessment.receipts < MINIMUM_ONBOARDING_RECEIPTS,
            "insufficient-onboarding-receipts",
        ),
        (assessment.users < MINIMUM_USERS, "insufficient-users"),
        (
            assessment.repositories < MINIMUM_REPOSITORIES,
            "insufficient-repositories",
        ),
        (
            assessment.integrations < MINIMUM_INTEGRATIONS,
            "insufficient-live-integrations",
        ),
        (
            assessment.operating_systems < MINIMUM_OPERATING_SYSTEMS,
            "insufficient-operating-system-coverage",
        ),
        (
            assessment.success_rate < MINIMUM_ONBOARDING_SUCCESS,
            "onboarding-success-target-missed",
        ),
        (
            assessment.rollback_rate < MINIMUM_ONBOARDING_SUCCESS,
            "rollback-verification-target-missed",
        ),
        (
            assessment
                .p95_install_ms
                .is_some_and(|value| value > MAXIMUM_P95_INSTALL_SECONDS * 1_000.0),
            "installation-speed-target-missed",
        ),
    ] {
        if condition {
            assessment.reasons.push(reason.to_owned());
        }
    }
}

fn assess_distributions(rows: &[DistributionReceipt]) -> DistributionAssessment {
    let verified = rows
        .iter()
        .filter_map(|row| {
            if row.synthetic.is_enabled()
                || !row.source_verified.is_enabled()
                || row.version != VERSION
            {
                return None;
            }
            parse_datetime(&row.observed_at).map(|time| (row, time))
        })
        .collect::<Vec<_>>();
    let channels = verified
        .iter()
        .filter_map(|(row, _)| (!row.channel_name.is_empty()).then_some(row.channel_name.as_str()))
        .collect::<BTreeSet<_>>()
        .len();
    let downloads = verified
        .iter()
        .map(|(row, _)| row.downloads.max(0))
        .sum::<i64>();
    let installations = verified
        .iter()
        .map(|(row, _)| row.unique_installations.max(0))
        .sum::<i64>();
    let mut reasons = Vec::new();
    if channels < MINIMUM_DISTRIBUTION_CHANNELS {
        reasons.push("insufficient-distribution-channels".to_owned());
    }
    if downloads < MINIMUM_PUBLIC_DOWNLOADS {
        reasons.push("insufficient-public-downloads".to_owned());
    }
    if installations < MINIMUM_UNIQUE_INSTALLATIONS {
        reasons.push("insufficient-unique-installations".to_owned());
    }
    DistributionAssessment {
        earliest: minimum_time(verified.iter().map(|(_, time)| *time)),
        channels,
        downloads,
        installations,
        reasons,
    }
}

fn assess_releases(rows: &[ReleaseReceipt]) -> ReleaseAssessment {
    let mut verified = rows
        .iter()
        .filter_map(|row| {
            let eligible = !row.synthetic.is_enabled()
                && row.source_verified.is_enabled()
                && row.signed.is_enabled()
                && row.provenance.is_enabled()
                && row.version == VERSION
                && row.channel == CHANNEL;
            if !eligible {
                return None;
            }
            parse_datetime(&row.published_at).map(|time| (row, time))
        })
        .collect::<Vec<_>>();
    verified.sort_by(|left, right| left.1.partial_cmp(&right.1).unwrap_or(Ordering::Equal));
    let maximum_gap_days = verified
        .windows(2)
        .map(|window| (window[1].1 - window[0].1) / 86_400.0)
        .fold(0.0, f64::max);
    let mut reasons = Vec::new();
    if verified.len() < MINIMUM_VERIFIED_RELEASES {
        reasons.push("insufficient-verified-releases".to_owned());
    }
    if maximum_gap_days > MAXIMUM_RELEASE_GAP_DAYS {
        reasons.push("release-cadence-gap-too-large".to_owned());
    }
    ReleaseAssessment {
        earliest: minimum_time(verified.iter().map(|(_, time)| *time)),
        releases: verified.len(),
        maximum_gap_days,
        reasons,
    }
}

fn minimum_time<I>(values: I) -> Option<f64>
where
    I: Iterator<Item = f64>,
{
    values.min_by(|left, right| left.partial_cmp(right).unwrap_or(Ordering::Equal))
}

fn evaluate_maturity(value: &Value) -> Result<Value, String> {
    let document = MaturityDocument::from_value(value)?;
    let onboarding = assess_onboarding(&document.onboarding);
    let distributions = assess_distributions(&document.distributions);
    let releases = assess_releases(&document.releases);
    let earliest = minimum_time(
        [
            onboarding.earliest,
            distributions.earliest,
            releases.earliest,
        ]
        .into_iter()
        .flatten(),
    );
    let days = earliest.map_or(0.0, |time| (current_epoch_seconds() - time) / 86_400.0);
    let mut reasons = Vec::new();
    if document.contains_synthetic() {
        reasons.push("synthetic-receipts-present".to_owned());
    }
    reasons.extend(onboarding.reasons.iter().cloned());
    reasons.extend(distributions.reasons.iter().cloned());
    reasons.extend(releases.reasons.iter().cloned());
    if days < i64_as_f64(MINIMUM_DAYS) {
        reasons.push("insufficient-operation-window".to_owned());
    }
    let reasons = reasons
        .into_iter()
        .collect::<BTreeSet<_>>()
        .into_iter()
        .collect::<Vec<_>>();
    let ok = reasons.is_empty();
    Ok(maturity_value(
        ok,
        days,
        reasons,
        &onboarding,
        &distributions,
        &releases,
    ))
}

fn maturity_value(
    ok: bool,
    days: f64,
    reasons: Vec<String>,
    onboarding: &OnboardingAssessment,
    distributions: &DistributionAssessment,
    releases: &ReleaseAssessment,
) -> Value {
    json!({
        "ok": ok,
        "claim": if ok { "PUBLIC_PRODUCT_MATURITY_VERIFIED" } else { "PUBLIC_PRODUCT_MATURITY_NOT_PROVEN" },
        "version": VERSION,
        "channel": CHANNEL,
        "reasons": reasons,
        "metrics": {
            "days": days,
            "onboarding_receipts": onboarding.receipts,
            "users": onboarding.users,
            "repositories": onboarding.repositories,
            "live_integrations": onboarding.integrations,
            "operating_systems": onboarding.operating_systems,
            "onboarding_success": onboarding.success_rate,
            "rollback_verified": onboarding.rollback_rate,
            "mean_install_wall_time_ms": onboarding.mean_install_ms,
            "p95_install_wall_time_ms": onboarding.p95_install_ms,
            "distribution_channels": distributions.channels,
            "public_downloads": distributions.downloads,
            "unique_installations": distributions.installations,
            "verified_releases": releases.releases,
            "maximum_release_gap_days": releases.maximum_gap_days,
        },
        "requirements": {
            "minimum_days": MINIMUM_DAYS,
            "minimum_onboarding_receipts": MINIMUM_ONBOARDING_RECEIPTS,
            "minimum_users": MINIMUM_USERS,
            "minimum_repositories": MINIMUM_REPOSITORIES,
            "minimum_integrations": MINIMUM_INTEGRATIONS,
            "minimum_operating_systems": MINIMUM_OPERATING_SYSTEMS,
            "minimum_onboarding_success": MINIMUM_ONBOARDING_SUCCESS,
            "maximum_p95_install_seconds": MAXIMUM_P95_INSTALL_SECONDS,
            "minimum_distribution_channels": MINIMUM_DISTRIBUTION_CHANNELS,
            "minimum_public_downloads": MINIMUM_PUBLIC_DOWNLOADS,
            "minimum_unique_installations": MINIMUM_UNIQUE_INSTALLATIONS,
            "minimum_verified_releases": MINIMUM_VERIFIED_RELEASES,
            "maximum_release_gap_days": MAXIMUM_RELEASE_GAP_DAYS,
        },
    })
}

struct PythonRandom {
    state: [u32; 624],
    index: usize,
}

impl PythonRandom {
    fn new(seed: u32) -> Self {
        let mut random = Self {
            state: [0; 624],
            index: 624,
        };
        random.init_by_array(&[seed]);
        random
    }

    fn init_genrand(&mut self, seed: u32) {
        self.state[0] = seed;
        for index in 1..624 {
            let index_u32 = u32::try_from(index).expect("MT state index is bounded");
            self.state[index] = 1_812_433_253u32
                .wrapping_mul(self.state[index - 1] ^ (self.state[index - 1] >> 30))
                .wrapping_add(index_u32);
        }
        self.index = 624;
    }

    fn init_by_array(&mut self, key: &[u32]) {
        self.init_genrand(19_650_218);
        let mut i = 1usize;
        let mut j = 0usize;
        let mut count = 624usize.max(key.len());
        while count > 0 {
            let previous = self.state[i - 1];
            let j_u32 = u32::try_from(j).expect("MT key index is bounded");
            self.state[i] = (self.state[i] ^ (previous ^ (previous >> 30)).wrapping_mul(1_664_525))
                .wrapping_add(key[j])
                .wrapping_add(j_u32);
            i += 1;
            j += 1;
            if i >= 624 {
                self.state[0] = self.state[623];
                i = 1;
            }
            if j >= key.len() {
                j = 0;
            }
            count -= 1;
        }
        count = 623;
        while count > 0 {
            let previous = self.state[i - 1];
            let i_u32 = u32::try_from(i).expect("MT state index is bounded");
            self.state[i] = (self.state[i]
                ^ (previous ^ (previous >> 30)).wrapping_mul(1_566_083_941))
            .wrapping_sub(i_u32);
            i += 1;
            if i >= 624 {
                self.state[0] = self.state[623];
                i = 1;
            }
            count -= 1;
        }
        self.state[0] = 0x8000_0000;
        self.index = 624;
    }

    fn gen_u32(&mut self) -> u32 {
        if self.index >= 624 {
            for index in 0..624 {
                let value = (self.state[index] & 0x8000_0000)
                    | (self.state[(index + 1) % 624] & 0x7fff_ffff);
                let mut next = self.state[(index + 397) % 624] ^ (value >> 1);
                if value & 1 != 0 {
                    next ^= 0x9908_b0df;
                }
                self.state[index] = next;
            }
            self.index = 0;
        }
        let mut value = self.state[self.index];
        self.index += 1;
        value ^= value >> 11;
        value ^= (value << 7) & 0x9d2c_5680;
        value ^= (value << 15) & 0xefc6_0000;
        value ^= value >> 18;
        value
    }

    fn getrandbits(&mut self, bits: u32) -> u32 {
        if bits == 0 {
            0
        } else {
            self.gen_u32() >> (32 - bits)
        }
    }

    fn randbelow(&mut self, upper: usize) -> usize {
        let bits = usize::BITS - upper.leading_zeros();
        loop {
            let value = usize::try_from(self.getrandbits(bits)).expect("u32 fits in usize");
            if value < upper {
                return value;
            }
        }
    }
}

fn median(values: &[f64]) -> f64 {
    let mut ordered = values.to_vec();
    ordered.sort_by(|left, right| left.partial_cmp(right).unwrap_or(Ordering::Equal));
    let middle = ordered.len() / 2;
    if ordered.len() % 2 == 0 {
        (ordered[middle - 1] + ordered[middle]) / 2.0
    } else {
        ordered[middle]
    }
}

fn bootstrap_ci(values: &[f64]) -> Option<[f64; 2]> {
    if values.is_empty() {
        return None;
    }
    let mut random = PythonRandom::new(1_337);
    let mut medians = Vec::with_capacity(10_000);
    let mut sample = Vec::with_capacity(values.len());
    for _ in 0..10_000 {
        sample.clear();
        for _ in values {
            sample.push(values[random.randbelow(values.len())]);
        }
        medians.push(median(&sample));
    }
    medians.sort_by(|left, right| left.partial_cmp(right).unwrap_or(Ordering::Equal));
    Some([medians[250], medians[9_750]])
}

fn signal_rows(value: &Value) -> Result<Vec<SignalRun>, String> {
    let rows = match value {
        Value::Array(rows) => rows,
        Value::Object(object) => object
            .get("results")
            .and_then(Value::as_array)
            .ok_or_else(|| "PROVIDER_BILLED_RESULTS_NOT_LIST".to_owned())?,
        _ => return Err("PROVIDER_BILLED_RESULTS_NOT_LIST".to_owned()),
    };
    Ok(rows.iter().map(SignalRun::from_value).collect::<Vec<_>>())
}

fn build_signal_index(rows: &[SignalRun]) -> SignalIndex {
    let mut index = SignalIndex {
        rows: HashMap::new(),
        order: Vec::new(),
    };
    for row in rows {
        let key = (
            row.task_id.clone(),
            row.repetition,
            row.cache_mode.clone(),
            row.arm_id.clone(),
        );
        match index.rows.entry(key.clone()) {
            Entry::Vacant(entry) => {
                index.order.push(key);
                entry.insert(row.clone());
            }
            Entry::Occupied(mut entry) => {
                entry.insert(row.clone());
            }
        }
    }
    index
}

fn collect_provider_pairs(
    index: &SignalIndex,
    baseline: &str,
    candidate: &str,
) -> ProviderPairStats {
    let mut stats = ProviderPairStats {
        ratios: Vec::new(),
        observed_pairs: Vec::new(),
        invalid_pairs: Vec::new(),
        quality: BTreeMap::from([(baseline.to_owned(), 0), (candidate.to_owned(), 0)]),
        total: BTreeMap::from([(baseline.to_owned(), 0), (candidate.to_owned(), 0)]),
    };
    for key in &index.order {
        let Some(base) = index.rows.get(key) else {
            continue;
        };
        if base.arm_id != baseline {
            continue;
        }
        let candidate_key = (
            base.task_id.clone(),
            base.repetition,
            base.cache_mode.clone(),
            candidate.to_owned(),
        );
        let candidate_row = index.rows.get(&candidate_key);
        increment(&mut stats.total, baseline, 1);
        increment(
            &mut stats.quality,
            baseline,
            i64::from(base.success.is_enabled()),
        );
        if let Some(row) = candidate_row {
            increment(&mut stats.total, candidate, 1);
            increment(
                &mut stats.quality,
                candidate,
                i64::from(row.success.is_enabled()),
            );
        }
        add_pair_result(base, candidate_row, &mut stats);
    }
    stats
}

fn increment(values: &mut BTreeMap<String, i64>, key: &str, amount: i64) {
    *values.entry(key.to_owned()).or_default() += amount;
}

fn add_pair_result(base: &SignalRun, candidate: Option<&SignalRun>, stats: &mut ProviderPairStats) {
    let Some(candidate) = candidate else {
        stats
            .invalid_pairs
            .push(invalid_pair(base, "missing-candidate"));
        return;
    };
    let equal_work =
        base.verified_work.partial_cmp(&candidate.verified_work) == Some(Ordering::Equal);
    if !base.success.is_enabled() || !candidate.success.is_enabled() || !equal_work {
        stats
            .invalid_pairs
            .push(invalid_pair(base, "unequal-verified-work"));
        return;
    }
    let (Some(base_cost), Some(candidate_cost)) = (base.quota_cost, candidate.quota_cost) else {
        stats
            .invalid_pairs
            .push(invalid_pair(base, "quota-unavailable"));
        return;
    };
    if candidate_cost <= 0.0 {
        stats
            .invalid_pairs
            .push(invalid_pair(base, "quota-unavailable"));
        return;
    }
    stats.ratios.push(base_cost / candidate_cost);
    stats.observed_pairs.push(Flag::from_bool(
        base.provider_observed.is_enabled() && candidate.provider_observed.is_enabled(),
    ));
}

fn invalid_pair(base: &SignalRun, reason: &str) -> Value {
    json!({
        "task": base.task_id,
        "repetition": base.repetition,
        "cache": base.cache_mode,
        "reason": reason,
    })
}

fn pass_rate(stats: &ProviderPairStats, arm: &str) -> f64 {
    let total = stats.total.get(arm).copied().unwrap_or(0);
    if total == 0 {
        return 0.0;
    }
    i64_as_f64(stats.quality.get(arm).copied().unwrap_or(0)) / i64_as_f64(total)
}

fn evaluate_provider_billed(
    value: &Value,
    baseline: &str,
    candidate: &str,
) -> Result<Value, String> {
    let rows = signal_rows(value)?;
    let index = build_signal_index(&rows);
    let mut stats = collect_provider_pairs(&index, baseline, candidate);
    stats
        .ratios
        .sort_by(|left, right| left.partial_cmp(right).unwrap_or(Ordering::Equal));
    let ci = bootstrap_ci(&stats.ratios);
    let ratio_median = stats.ratios.get(stats.ratios.len() / 2).copied();
    let pass_rates = BTreeMap::from([
        (baseline.to_owned(), pass_rate(&stats, baseline)),
        (candidate.to_owned(), pass_rate(&stats, candidate)),
    ]);
    let candidate_clean = !rows
        .iter()
        .filter(|row| row.arm_id == candidate)
        .any(|row| row.security_regressions != 0 || row.verifier_skips != 0);
    let observed_count = stats
        .observed_pairs
        .iter()
        .filter(|flag| flag.is_enabled())
        .count();
    let claimable = stats.ratios.len() >= 10
        && ci.is_some_and(|interval| interval[0] > 1.0)
        && pass_rates[candidate] >= pass_rates[baseline]
        && observed_count == stats.ratios.len()
        && candidate_clean;
    Ok(provider_billed_value(
        baseline,
        candidate,
        &rows,
        stats,
        ci,
        ratio_median,
        pass_rates,
        observed_count,
        claimable,
    ))
}

fn provider_billed_value(
    baseline: &str,
    candidate: &str,
    rows: &[SignalRun],
    stats: ProviderPairStats,
    confidence_interval: Option<[f64; 2]>,
    median_ratio: Option<f64>,
    pass_rates: BTreeMap<String, f64>,
    observed_count: usize,
    claimable: bool,
) -> Value {
    json!({
        "baseline": baseline,
        "candidate": candidate,
        "valid_pairs": stats.ratios.len(),
        "invalid_pairs": stats.invalid_pairs,
        "provider_observed_pairs": observed_count,
        "provider_unobserved_pairs": stats.observed_pairs.len() - observed_count,
        "median_efficiency_ratio": median_ratio,
        "confidence_interval_95": confidence_interval,
        "pass_rates": pass_rates,
        "claimable_superiority": claimable,
        "claim": if claimable { "SUPERIORITY_PROVEN" } else { "NOT_PROVEN" },
        "provider_observed_runs": rows.iter().filter(|row| row.provider_observed.is_enabled()).count(),
        "total_runs": rows.len(),
        "fail_closed": true,
    })
}

pub fn execute(action: &str, arguments: &[String]) -> Result<ProofDecision, String> {
    let source = command_path(arguments, action)?;
    let document = load_json_argument(source)?;
    let value = match action {
        "maturity" => evaluate_maturity(&document)?,
        "provider-billed" => {
            let baseline = option_value(arguments, "--baseline", "plain-host");
            let candidate = option_value(arguments, "--candidate", "syntavra-minimal");
            evaluate_provider_billed(&document, &baseline, &candidate)?
        }
        _ => return Err("PROOF_MATURITY_ACTION_INVALID".to_owned()),
    };
    let ok = match action {
        "maturity" => value.get("ok").and_then(Value::as_bool) == Some(true),
        "provider-billed" => {
            value.get("claimable_superiority").and_then(Value::as_bool) == Some(true)
        }
        _ => false,
    };
    Ok(ProofDecision {
        value,
        exit_code: if ok { 0 } else { 4 },
    })
}

#[cfg(test)]
mod tests {
    use super::{bootstrap_ci, evaluate_maturity, PythonRandom};
    use serde_json::json;

    #[test]
    fn python_random_seed_1337_starts_with_reference_choices() {
        let mut random = PythonRandom::new(1_337);
        let choices = (0..8).map(|_| random.randbelow(10)).collect::<Vec<_>>();
        assert_eq!(choices, vec![9, 8, 5, 9, 9, 2, 5, 6]);
    }

    #[test]
    fn constant_bootstrap_interval_is_exact() {
        assert_eq!(bootstrap_ci(&[2.0; 10]), Some([2.0, 2.0]));
    }

    #[test]
    fn empty_maturity_document_fails_closed() {
        let value = evaluate_maturity(&json!({})).expect("valid object");
        assert_eq!(value["ok"], false);
        assert_eq!(value["metrics"]["days"], 0.0);
    }
}
