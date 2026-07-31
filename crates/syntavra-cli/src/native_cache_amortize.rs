#![forbid(unsafe_code)]

use serde_json::{json, Value};

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
struct Inputs {
    cache_write_tokens: i64,
    cache_read_tokens: i64,
    uncached_input_tokens: i64,
    requests: i64,
}

fn parse_i64(arguments: &[String], flag: &str) -> Result<i64, String> {
    let index = arguments
        .iter()
        .position(|value| value == flag)
        .ok_or_else(|| {
            format!(
                "CACHE_AMORTIZE_{}_MISSING",
                flag.trim_start_matches('-')
                    .replace('-', "_")
                    .to_ascii_uppercase()
            )
        })?;
    arguments
        .get(index + 1)
        .ok_or_else(|| {
            format!(
                "CACHE_AMORTIZE_{}_VALUE_MISSING",
                flag.trim_start_matches('-')
                    .replace('-', "_")
                    .to_ascii_uppercase()
            )
        })?
        .parse::<i64>()
        .map_err(|_| {
            format!(
                "CACHE_AMORTIZE_{}_INVALID",
                flag.trim_start_matches('-')
                    .replace('-', "_")
                    .to_ascii_uppercase()
            )
        })
}

fn parse(arguments: &[String]) -> Result<Inputs, String> {
    Ok(Inputs {
        cache_write_tokens: parse_i64(arguments, "--write")?,
        cache_read_tokens: parse_i64(arguments, "--read")?,
        uncached_input_tokens: parse_i64(arguments, "--uncached")?,
        requests: parse_i64(arguments, "--requests")?,
    })
}

fn amortization(inputs: Inputs) -> Value {
    let requests = inputs.requests.max(1) as f64;
    let cache_write_tokens = inputs.cache_write_tokens as f64;
    let cache_read_tokens = inputs.cache_read_tokens as f64;
    let uncached_input_tokens = inputs.uncached_input_tokens as f64;
    let write_multiplier = 1.25_f64;
    let read_multiplier = 0.1_f64;
    let baseline = uncached_input_tokens * requests;
    let optimized = cache_write_tokens * write_multiplier
        + cache_read_tokens * read_multiplier * (requests - 1.0).max(0.0);
    let saved = (baseline - optimized).max(0.0);
    let savings_ratio = if baseline != 0.0 {
        ((baseline - optimized) / baseline).max(0.0)
    } else {
        0.0
    };
    let denominator = (uncached_input_tokens - cache_read_tokens * read_multiplier).max(1.0);
    json!({
        "baseline_equivalent": baseline,
        "optimized_equivalent": optimized,
        "saved_equivalent": saved,
        "savings_ratio": savings_ratio,
        "break_even_requests": (cache_write_tokens * write_multiplier) / denominator,
    })
}

pub fn execute(arguments: &[String]) -> Result<Value, String> {
    parse(arguments).map(amortization)
}

#[cfg(test)]
mod tests {
    use super::{amortization, parse, Inputs};

    #[test]
    fn matches_reference_example() {
        let value = amortization(Inputs {
            cache_write_tokens: 100,
            cache_read_tokens: 0,
            uncached_input_tokens: 1000,
            requests: 1,
        });
        assert_eq!(value["baseline_equivalent"], 1000.0);
        assert_eq!(value["optimized_equivalent"], 125.0);
        assert_eq!(value["saved_equivalent"], 875.0);
        assert_eq!(value["savings_ratio"], 0.875);
        assert_eq!(value["break_even_requests"], 0.125);
    }

    #[test]
    fn parses_required_flags() {
        let arguments = [
            "run",
            "cache-amortize",
            "--write",
            "100",
            "--read",
            "20",
            "--uncached",
            "1000",
            "--requests",
            "3",
        ]
        .into_iter()
        .map(str::to_owned)
        .collect::<Vec<_>>();
        let inputs = parse(&arguments).expect("parse");
        assert_eq!(inputs.cache_write_tokens, 100);
        assert_eq!(inputs.cache_read_tokens, 20);
        assert_eq!(inputs.uncached_input_tokens, 1000);
        assert_eq!(inputs.requests, 3);
    }
}
