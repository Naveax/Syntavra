#![forbid(unsafe_code)]

use std::cmp::Ordering;
use std::collections::{BTreeMap, BTreeSet, HashMap};
use std::fs;
use std::path::Path;
use std::time::{SystemTime, UNIX_EPOCH};

use serde_json::{json, Value};

const VERSION: &str = "0.0.1";
const CHANNEL: &str = "pre-release";

const MINIMUM_DAYS: f64 = 90.0;
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

#[derive(Clone)]
struct OnboardingReceipt {
    observed_at: String,
    user_hash: String,
    repository_hash: String,
    integration_id: String,
    operating_system: String,
    install_wall_time_ms: f64,
    success: bool,
    rollback_verified: bool,
    doctor_passed: bool,
    synthetic: bool,
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
    source_verified: bool,
    synthetic: bool,
}

#[derive(Clone)]
struct ReleaseReceipt {
    published_at: String,
    version: String,
    channel: String,
    signed: bool,
    provenance: bool,
    source_verified: bool,
    synthetic: bool,
}

#[derive(Clone)]
struct SignalRun {
    task_id: String,
    arm_id: String,
    repetition: i64,
    success: bool,
    verified_work: f64,
    quota_cost: Option<f64>,
    security_regressions: i64,
    verifier_skips: i64,
    cache_mode: String,
    provider_observed: bool,
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
                        number
                            .is_finite()
                            .then(|| number.trunc().to_string().parse::<i64>().ok())
                            .flatten()
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

impl OnboardingReceipt {
    fn from_value(value: &Value) -> Self {
        Self {
            observed_at: string_value(value.get("observed_at"), ""),
            user_hash: string_value(value.get("user_hash"), ""),
            repository_hash: string_value(value.get("repository_hash"), ""),
            integration_id: string_value(value.get("integration_id"), ""),
            operating_system: string_value(value.get("operating_system"), ""),
            install_wall_time_ms: float_value(value.get("install_wall_time_ms"), -1.0),
            success: bool_value(value.get("success"), false),
            rollback_verified: bool_value(value.get("rollback_verified"), false),
            doctor_passed: bool_value(value.get("doctor_passed"), false),
            synthetic: bool_value(value.get("synthetic"), true),
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
            source_verified: bool_value(value.get("source_verified"), false),
            synthetic: bool_value(value.get("synthetic"), true),
        }
    }
}

impl ReleaseReceipt {
    fn from_value(value: &Value) -> Self {
        Self {
            published_at: string_value(value.get("published_at"), ""),
            version: string_value(value.get("version"), ""),
            channel: string_value(value.get("channel"), ""),
            signed: bool_value(value.get("signed"), false),
            provenance: bool_value(value.get("provenance"), false),
            source_verified: bool_value(value.get("source_verified"), false),
            synthetic: bool_value(value.get("synthetic"), true),
        }
    }
}

impl SignalRun {
    fn from_value(value: &Value) -> Self {
        Self {
            task_id: string_value(value.get("task_id"), ""),
            arm_id: string_value(value.get("arm_id"), ""),
            repetition: integer_value(value.get("repetition"), 0),
            success: bool_value(value.get("success"), false),
            verified_work: float_value(value.get("verified_work"), 0.0),
            quota_cost: value
                .get("quota_cost")
                .filter(|item| !item.is_null())
                .map(|item| float_value(Some(item), 0.0)),
            security_regressions: integer_value(value.get("security_regressions"), 0),
            verifier_skips: integer_value(value.get("verifier_skips"), 0),
            cache_mode: string_value(value.get("cache_mode"), ""),
            provider_observed: bool_value(value.get("provider_observed"), false),
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
    (text.len() == 2)
        .then(|| text.parse::<i64>().ok())
        .flatten()
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
    let offset = if zone.is_empty() {
        0.0
    } else {
        let sign = if zone.starts_with('-') { -1.0 } else { 1.0 };
        let zone = &zone[1..];
        let (hours, minutes) = zone.split_once(':')?;
        sign * ((hours.parse::<f64>().ok()? * 60.0 + minutes.parse::<f64>().ok()?) * 60.0)
    };
    Some(
        civil_days(year, month, day) as f64 * 86_400.0
            + hour as f64 * 3_600.0
            + minute as f64 * 60.0
            + second
            - offset,
    )
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
    Some(sum / values.len() as f64)
}

fn python_round_nonnegative(value: f64) -> usize {
    let floor = value.floor();
    let fraction = value - floor;
    if fraction < 0.5 {
        floor as usize
    } else if fraction > 0.5 {
        floor as usize + 1
    } else {
        let floor = floor as usize;
        floor + usize::from(floor % 2 == 1)
    }
}

fn percentile(values: &[f64], percentile: f64) -> f64 {
    if values.is_empty() {
        return 0.0;
    }
    let mut ordered = values.to_vec();
    ordered.sort_by(|left, right| left.partial_cmp(right).unwrap_or(Ordering::Equal));
    let raw = (ordered.len() - 1) as f64 * percentile;
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

fn maturity_document(
    value: &Value,
) -> Result<
    (
        Vec<OnboardingReceipt>,
        Vec<DistributionReceipt>,
        Vec<ReleaseReceipt>,
    ),
    String,
> {
    let object = value
        .as_object()
        .ok_or_else(|| "MATURITY_DOCUMENT_NOT_OBJECT".to_owned())?;
    let onboarding = object_rows(object.get("onboarding"))
        .into_iter()
        .map(OnboardingReceipt::from_value)
        .collect();
    let distributions = object_rows(object.get("distributions"))
        .into_iter()
        .map(DistributionReceipt::from_value)
        .collect();
    let releases = object_rows(object.get("releases"))
        .into_iter()
        .map(ReleaseReceipt::from_value)
        .collect();
    Ok((onboarding, distributions, releases))
}

fn evaluate_maturity(value: &Value) -> Result<Value, String> {
    let (onboarding, distributions, releases) = maturity_document(value)?;
    let mut reasons = Vec::<String>::new();
    if onboarding.iter().any(|row| row.synthetic)
        || distributions.iter().any(|row| row.synthetic)
        || releases.iter().any(|row| row.synthetic)
    {
        reasons.push("synthetic-receipts-present".to_owned());
    }

    let live_onboarding = onboarding
        .iter()
        .filter_map(|row| {
            (!row.synthetic && row.version == VERSION && row.channel == CHANNEL)
                .then(|| parse_datetime(&row.observed_at).map(|time| (row, time)))
                .flatten()
        })
        .collect::<Vec<_>>();
    if live_onboarding.len() < MINIMUM_ONBOARDING_RECEIPTS {
        reasons.push("insufficient-onboarding-receipts".to_owned());
    }
    let users = live_onboarding
        .iter()
        .filter_map(|(row, _)| (!row.user_hash.is_empty()).then_some(row.user_hash.as_str()))
        .collect::<BTreeSet<_>>();
    let repositories = live_onboarding
        .iter()
        .filter_map(|(row, _)| {
            (!row.repository_hash.is_empty()).then_some(row.repository_hash.as_str())
        })
        .collect::<BTreeSet<_>>();
    let integrations = live_onboarding
        .iter()
        .filter_map(|(row, _)| {
            (!row.integration_id.is_empty()).then_some(row.integration_id.as_str())
        })
        .collect::<BTreeSet<_>>();
    let operating_systems = live_onboarding
        .iter()
        .filter_map(|(row, _)| {
            (!row.operating_system.is_empty()).then_some(row.operating_system.as_str())
        })
        .collect::<BTreeSet<_>>();
    if users.len() < MINIMUM_USERS {
        reasons.push("insufficient-users".to_owned());
    }
    if repositories.len() < MINIMUM_REPOSITORIES {
        reasons.push("insufficient-repositories".to_owned());
    }
    if integrations.len() < MINIMUM_INTEGRATIONS {
        reasons.push("insufficient-live-integrations".to_owned());
    }
    if operating_systems.len() < MINIMUM_OPERATING_SYSTEMS {
        reasons.push("insufficient-operating-system-coverage".to_owned());
    }

    let denominator = live_onboarding.len().max(1) as f64;
    let success = live_onboarding
        .iter()
        .filter(|(row, _)| row.success && row.doctor_passed)
        .count() as f64
        / denominator;
    let rollback = live_onboarding
        .iter()
        .filter(|(row, _)| row.rollback_verified)
        .count() as f64
        / denominator;
    let install_times = live_onboarding
        .iter()
        .filter_map(|(row, _)| {
            (row.install_wall_time_ms >= 0.0).then_some(row.install_wall_time_ms)
        })
        .collect::<Vec<_>>();
    let p95_install_ms = percentile(&install_times, 0.95);
    if success < MINIMUM_ONBOARDING_SUCCESS {
        reasons.push("onboarding-success-target-missed".to_owned());
    }
    if rollback < MINIMUM_ONBOARDING_SUCCESS {
        reasons.push("rollback-verification-target-missed".to_owned());
    }
    if p95_install_ms > MAXIMUM_P95_INSTALL_SECONDS * 1_000.0 {
        reasons.push("installation-speed-target-missed".to_owned());
    }

    let verified_distributions = distributions
        .iter()
        .filter_map(|row| {
            (!row.synthetic && row.source_verified && row.version == VERSION)
                .then(|| parse_datetime(&row.observed_at).map(|time| (row, time)))
                .flatten()
        })
        .collect::<Vec<_>>();
    let channels = verified_distributions
        .iter()
        .filter_map(|(row, _)| (!row.channel_name.is_empty()).then_some(row.channel_name.as_str()))
        .collect::<BTreeSet<_>>();
    let public_downloads = verified_distributions
        .iter()
        .map(|(row, _)| row.downloads.max(0))
        .sum::<i64>();
    let unique_installations = verified_distributions
        .iter()
        .map(|(row, _)| row.unique_installations.max(0))
        .sum::<i64>();
    if channels.len() < MINIMUM_DISTRIBUTION_CHANNELS {
        reasons.push("insufficient-distribution-channels".to_owned());
    }
    if public_downloads < MINIMUM_PUBLIC_DOWNLOADS {
        reasons.push("insufficient-public-downloads".to_owned());
    }
    if unique_installations < MINIMUM_UNIQUE_INSTALLATIONS {
        reasons.push("insufficient-unique-installations".to_owned());
    }

    let mut verified_releases = releases
        .iter()
        .filter_map(|row| {
            (!row.synthetic
                && row.source_verified
                && row.signed
                && row.provenance
                && row.version == VERSION
                && row.channel == CHANNEL)
                .then(|| parse_datetime(&row.published_at).map(|time| (row, time)))
                .flatten()
        })
        .collect::<Vec<_>>();
    verified_releases
        .sort_by(|left, right| left.1.partial_cmp(&right.1).unwrap_or(Ordering::Equal));
    if verified_releases.len() < MINIMUM_VERIFIED_RELEASES {
        reasons.push("insufficient-verified-releases".to_owned());
    }
    let max_gap_days = verified_releases
        .windows(2)
        .map(|window| (window[1].1 - window[0].1) / 86_400.0)
        .fold(0.0, f64::max);
    if max_gap_days > MAXIMUM_RELEASE_GAP_DAYS {
        reasons.push("release-cadence-gap-too-large".to_owned());
    }

    let earliest = live_onboarding
        .iter()
        .map(|(_, time)| *time)
        .chain(verified_distributions.iter().map(|(_, time)| *time))
        .chain(verified_releases.iter().map(|(_, time)| *time))
        .min_by(|left, right| left.partial_cmp(right).unwrap_or(Ordering::Equal));
    let days = earliest.map_or(0.0, |time| (current_epoch_seconds() - time) / 86_400.0);
    if days < MINIMUM_DAYS {
        reasons.push("insufficient-operation-window".to_owned());
    }

    let reasons = reasons
        .into_iter()
        .collect::<BTreeSet<_>>()
        .into_iter()
        .collect::<Vec<_>>();
    let ok = reasons.is_empty();
    Ok(json!({
        "ok": ok,
        "claim": if ok { "PUBLIC_PRODUCT_MATURITY_VERIFIED" } else { "PUBLIC_PRODUCT_MATURITY_NOT_PROVEN" },
        "version": VERSION,
        "channel": CHANNEL,
        "reasons": reasons,
        "metrics": {
            "days": days,
            "onboarding_receipts": live_onboarding.len(),
            "users": users.len(),
            "repositories": repositories.len(),
            "live_integrations": integrations.len(),
            "operating_systems": operating_systems.len(),
            "onboarding_success": success,
            "rollback_verified": rollback,
            "mean_install_wall_time_ms": precise_mean(&install_times),
            "p95_install_wall_time_ms": (!install_times.is_empty()).then_some(p95_install_ms),
            "distribution_channels": channels.len(),
            "public_downloads": public_downloads,
            "unique_installations": unique_installations,
            "verified_releases": verified_releases.len(),
            "maximum_release_gap_days": max_gap_days,
        },
        "requirements": {
            "minimum_days": MINIMUM_DAYS as i64,
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
    }))
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
            self.state[index] = 1_812_433_253u32
                .wrapping_mul(self.state[index - 1] ^ (self.state[index - 1] >> 30))
                .wrapping_add(index as u32);
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
            self.state[i] = (self.state[i] ^ (previous ^ (previous >> 30)).wrapping_mul(1_664_525))
                .wrapping_add(key[j])
                .wrapping_add(j as u32);
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
            self.state[i] = (self.state[i]
                ^ (previous ^ (previous >> 30)).wrapping_mul(1_566_083_941))
            .wrapping_sub(i as u32);
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
            let value = self.getrandbits(bits) as usize;
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

fn evaluate_provider_billed(
    value: &Value,
    baseline: &str,
    candidate: &str,
) -> Result<Value, String> {
    let rows = signal_rows(value)?;
    let mut keyed = HashMap::<(String, i64, String, String), SignalRun>::new();
    let mut order = Vec::<(String, i64, String, String)>::new();
    for row in &rows {
        let key = (
            row.task_id.clone(),
            row.repetition,
            row.cache_mode.clone(),
            row.arm_id.clone(),
        );
        if !keyed.contains_key(&key) {
            order.push(key.clone());
        }
        keyed.insert(key, row.clone());
    }

    let mut ratios = Vec::<f64>::new();
    let mut observed_pairs = Vec::<bool>::new();
    let mut invalid = Vec::<Value>::new();
    let mut quality = BTreeMap::from([(baseline.to_owned(), 0i64), (candidate.to_owned(), 0i64)]);
    let mut total = BTreeMap::from([(baseline.to_owned(), 0i64), (candidate.to_owned(), 0i64)]);
    for key in order {
        let Some(base) = keyed.get(&key) else {
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
        let candidate_row = keyed.get(&candidate_key);
        *total.get_mut(baseline).expect("baseline initialized") += 1;
        *quality.get_mut(baseline).expect("baseline initialized") += i64::from(base.success);
        if let Some(row) = candidate_row {
            *total.get_mut(candidate).expect("candidate initialized") += 1;
            *quality.get_mut(candidate).expect("candidate initialized") += i64::from(row.success);
        }
        let Some(row) = candidate_row else {
            invalid.push(json!({
                "task": base.task_id,
                "repetition": base.repetition,
                "cache": base.cache_mode,
                "reason": "missing-candidate",
            }));
            continue;
        };
        if !base.success || !row.success || base.verified_work != row.verified_work {
            invalid.push(json!({
                "task": base.task_id,
                "repetition": base.repetition,
                "cache": base.cache_mode,
                "reason": "unequal-verified-work",
            }));
            continue;
        }
        let Some(base_cost) = base.quota_cost else {
            invalid.push(json!({
                "task": base.task_id,
                "repetition": base.repetition,
                "cache": base.cache_mode,
                "reason": "quota-unavailable",
            }));
            continue;
        };
        let Some(candidate_cost) = row.quota_cost else {
            invalid.push(json!({
                "task": base.task_id,
                "repetition": base.repetition,
                "cache": base.cache_mode,
                "reason": "quota-unavailable",
            }));
            continue;
        };
        if candidate_cost <= 0.0 {
            invalid.push(json!({
                "task": base.task_id,
                "repetition": base.repetition,
                "cache": base.cache_mode,
                "reason": "quota-unavailable",
            }));
            continue;
        }
        ratios.push(base_cost / candidate_cost);
        observed_pairs.push(base.provider_observed && row.provider_observed);
    }
    ratios.sort_by(|left, right| left.partial_cmp(right).unwrap_or(Ordering::Equal));
    let ci = bootstrap_ci(&ratios);
    let ratio_median = ratios.get(ratios.len() / 2).copied();
    let pass_rates = BTreeMap::from([
        (
            baseline.to_owned(),
            if total[baseline] == 0 {
                0.0
            } else {
                quality[baseline] as f64 / total[baseline] as f64
            },
        ),
        (
            candidate.to_owned(),
            if total[candidate] == 0 {
                0.0
            } else {
                quality[candidate] as f64 / total[candidate] as f64
            },
        ),
    ]);
    let candidate_clean = !rows
        .iter()
        .filter(|row| row.arm_id == candidate)
        .any(|row| row.security_regressions != 0 || row.verifier_skips != 0);
    let claimable = ratios.len() >= 10
        && ci.is_some_and(|interval| interval[0] > 1.0)
        && pass_rates[candidate] >= pass_rates[baseline]
        && observed_pairs.len() == ratios.len()
        && observed_pairs.iter().all(|observed| *observed)
        && candidate_clean;
    Ok(json!({
        "baseline": baseline,
        "candidate": candidate,
        "valid_pairs": ratios.len(),
        "invalid_pairs": invalid,
        "provider_observed_pairs": observed_pairs.iter().filter(|value| **value).count(),
        "provider_unobserved_pairs": observed_pairs.len() - observed_pairs.iter().filter(|value| **value).count(),
        "median_efficiency_ratio": ratio_median,
        "confidence_interval_95": ci,
        "pass_rates": pass_rates,
        "claimable_superiority": claimable,
        "claim": if claimable { "SUPERIORITY_PROVEN" } else { "NOT_PROVEN" },
        "provider_observed_runs": rows.iter().filter(|row| row.provider_observed).count(),
        "total_runs": rows.len(),
        "fail_closed": true,
    }))
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
