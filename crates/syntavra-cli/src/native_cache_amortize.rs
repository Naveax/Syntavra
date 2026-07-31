#![forbid(unsafe_code)]

use serde_json::{json, Value};

#[derive(Debug, Clone, Copy, PartialEq)]
struct Inputs {
    cache_write_tokens: f64,
    cache_read_tokens: f64,
    uncached_input_tokens: f64,
    requests: f64,
}

fn flag_code(flag: &str) -> String {
    flag.trim_start_matches('-')
        .replace('-', "_")
        .to_ascii_uppercase()
}

fn parse_integer_as_f64(arguments: &[String], flag: &str) -> Result<f64, String> {
    let code = flag_code(flag);
    let index = arguments
        .iter()
        .position(|value| value == flag)
        .ok_or_else(|| format!("CACHE_AMORTIZE_{code}_MISSING"))?;
    let raw = arguments
        .get(index + 1)
        .ok_or_else(|| format!("CACHE_AMORTIZE_{code}_VALUE_MISSING"))?;
    raw.parse::<i64>()
        .map_err(|_| format!("CACHE_AMORTIZE_{code}_INVALID"))?;
    raw.parse::<f64>()
        .map_err(|_| format!("CACHE_AMORTIZE_{code}_INVALID"))
}

fn parse(arguments: &[String]) -> Result<Inputs, String> {
    Ok(Inputs {
        cache_write_tokens: parse_integer_as_f64(arguments, "--write")?,
        cache_read_tokens: parse_integer_as_f64(arguments, "--read")?,
        uncached_input_tokens: parse_integer_as_f64(arguments, "--uncached")?,
        requests: parse_integer_as_f64(arguments, "--requests")?,
    })
}

fn amortization(inputs: Inputs) -> Value {
    let requests = inputs.requests.max(1.0);
    let write_multiplier = 1.25_f64;
    let read_multiplier = 0.1_f64;
    let baseline = inputs.uncached_input_tokens * requests;
    let optimized = inputs.cache_write_tokens * write_multiplier
        + inputs.cache_read_tokens * read_multiplier * (requests - 1.0).max(0.0);
    let saved = (baseline - optimized).max(0.0);
    let savings_ratio = if baseline == 0.0 {
        0.0
    } else {
        ((baseline - optimized) / baseline).max(0.0)
    };
    let denominator =
        (inputs.uncached_input_tokens - inputs.cache_read_tokens * read_multiplier).max(1.0);
    json!({
        "baseline_equivalent": baseline,
        "optimized_equivalent": optimized,
        "saved_equivalent": saved,
        "savings_ratio": savings_ratio,
        "break_even_requests": (inputs.cache_write_tokens * write_multiplier) / denominator,
    })
}

pub fn execute(arguments: &[String]) -> Result<Value, String> {
    parse(arguments).map(amortization)
}

#[cfg(test)]
mod tests {
    use super::{amortization, parse, Inputs};

    fn assert_close(actual: f64, expected: f64) {
        assert!((actual - expected).abs() <= f64::EPSILON);
    }

    #[test]
    fn matches_reference_example() {
        let value = amortization(Inputs {
            cache_write_tokens: 100.0,
            cache_read_tokens: 0.0,
            uncached_input_tokens: 1000.0,
            requests: 1.0,
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
        assert_close(inputs.cache_write_tokens, 100.0);
        assert_close(inputs.cache_read_tokens, 20.0);
        assert_close(inputs.uncached_input_tokens, 1000.0);
        assert_close(inputs.requests, 3.0);
    }
}
