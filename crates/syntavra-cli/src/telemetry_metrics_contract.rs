use serde_json::json;

pub fn telemetry_metrics_json(output_format: &str) -> Result<String, String> {
    let value = match output_format {
        "json" => json!({
            "format": "json",
            "metrics": {
                "counters": [],
                "gauges": [],
                "histograms": [],
            },
        }),
        "prometheus" => json!({
            "format": "prometheus",
            "text": "",
        }),
        _ => return Err("TELEMETRY_METRICS_FORMAT_INVALID".to_owned()),
    };
    serde_json::to_string(&value).map_err(|_| "TELEMETRY_METRICS_JSON_FAILED".to_owned())
}

#[cfg(test)]
mod tests {
    use super::telemetry_metrics_json;

    #[test]
    fn json_result_is_deterministic() {
        let value = telemetry_metrics_json("json").expect("json telemetry result");
        assert_eq!(value, telemetry_metrics_json("json").unwrap());
        assert!(value.contains("\"counters\":[]"));
    }

    #[test]
    fn prometheus_result_is_empty() {
        assert_eq!(
            telemetry_metrics_json("prometheus").unwrap(),
            "{\"format\":\"prometheus\",\"text\":\"\"}"
        );
    }

    #[test]
    fn invalid_format_is_rejected() {
        assert_eq!(
            telemetry_metrics_json("other").unwrap_err(),
            "TELEMETRY_METRICS_FORMAT_INVALID"
        );
    }
}
