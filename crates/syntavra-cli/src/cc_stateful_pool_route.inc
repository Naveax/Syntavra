fn cc_pool_route_action(arguments:&[String],project:&Path,state_root:&Path)->Result<Value,String>{
    let start=cc_stateful_action_index(arguments,"provider-pool").ok_or_else(||"ACTION_NOT_FOUND:provider-pool".to_owned())?;
    let task=arguments.get(start+1).cloned().unwrap_or_default();
    let source=arguments.get(start+3).cloned().unwrap_or_default();
    let models=if Path::new(&source).is_file(){cc_parse_python_json(&fs::read_to_string(&source).map_err(|e|format!("POOL_MODELS_READ_FAILED:{e}"))?).map_err(|e|format!("POOL_MODELS_JSON_FAILED:{e}"))?}else{cc_parse_python_json(&source).map_err(|e|format!("POOL_MODELS_JSON_FAILED:{e}"))?};
    let models=models.as_array().ok_or_else(||"route requires model JSON list/path as value".to_owned())?;
    let pool_command=vec!["run".to_owned(),"provider-pool".to_owned()];
    let list_args=vec!["run".to_owned(),"provider-pool".to_owned(),"list".to_owned()];
    let pool=native_remaining71_competitive::execute(&pool_command,&list_args,project,state_root)?.ok_or_else(||"POOL_LIST_NATIVE_MISSING".to_owned())?;
    let accounts=pool["accounts"].as_array().cloned().unwrap_or_default();
    let candidates=cc_pool_candidates_from_receipt(&accounts,models,cc_stateful_now()?);
    let changed=cc_stateful_option_i64(arguments,"--changed-files",0)?;
    let tokens=cc_stateful_option_i64(arguments,"--tokens",0)?;
    let command=vec!["run".to_owned(),"provider-route".to_owned()];
    let args=vec!["run".to_owned(),"provider-route".to_owned(),task,serde_json::to_string(&candidates).map_err(|e|format!("POOL_CANDIDATES_JSON_FAILED:{e}"))?,"--changed-files".to_owned(),changed.to_string(),"--tokens".to_owned(),tokens.to_string()];
    native_remaining71_competitive::execute(&command,&args,project,state_root)?.ok_or_else(||"POOL_ROUTE_NATIVE_MISSING".to_owned())
}
