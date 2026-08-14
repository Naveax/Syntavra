fn cc_pool_candidates_from_receipt(accounts:&[Value],models:&[Value],now:f64)->Vec<Value>{
    let mut output=Vec::<Value>::new();
    for model in models{
        let provider=model["provider"].as_str().unwrap_or_default().to_lowercase();
        let model_name=model["model"].as_str().unwrap_or_default();
        for account in accounts{
            if account["provider"].as_str()!=Some(provider.as_str())||account["disabled"].as_bool().unwrap_or(false){continue}
            let allow=account["model_allowlist"].as_array().cloned().unwrap_or_default();
            if !allow.is_empty()&&!allow.iter().any(|v|v.as_str()==Some(model_name)){continue}
            let reset=account["quota_reset_at"].as_f64().unwrap_or(0.0);
            let quota=if reset>0.0&&reset<=now{1.0}else{account["quota_remaining"].as_f64().unwrap_or(1.0)};
            let subscription=account["subscription"].as_bool().unwrap_or(false);
            let latency=account["latency_ewma_ms"].as_f64().unwrap_or(0.0);
            output.push(serde_json::json!({
                "provider":provider,"model":model_name,
                "available":account["circuit_open_until"].as_f64().unwrap_or(0.0)<=now,
                "quota_remaining":quota,"rate_limited_until":account["rate_limited_until"].as_f64().unwrap_or(0.0),
                "input_cost_per_million":if subscription{0.0}else{model["input_cost_per_million"].as_f64().unwrap_or(0.0)},
                "output_cost_per_million":if subscription{0.0}else{model["output_cost_per_million"].as_f64().unwrap_or(0.0)},
                "latency_ms":if latency>0.0{latency}else{model["latency_ms"].as_f64().unwrap_or(0.0)},
                "quality":model["quality"].as_f64().unwrap_or(0.5)*(0.75+0.25*account["health_ratio"].as_f64().unwrap_or(1.0)),
                "max_complexity":model["max_complexity"].as_str().unwrap_or("reasoning"),
                "context_window":model["context_window"].as_i64().unwrap_or(0),
                "account":account["account"],"subscription":subscription,"priority":account["priority"]
            }));
        }
    }
    output
}
