#![forbid(unsafe_code)]
#![allow(clippy::too_many_lines, clippy::cast_precision_loss)]

use std::collections::{BTreeMap, BTreeSet};
use std::fs;
use std::path::Path;

use serde_json::{json, Map, Value};
use syntavra_core::sha256_hex;

const AXES: &[&str] = &["R","C","O","T","P","V","X","H","S","F"];
const CRITICAL: &[&str] = &["R","C","O","T","V"];
const REQUIRED_CONTROLS: &[&str] = &[
    "same_prompt","same_model","same_reasoning","same_repository","same_verifier",
    "same_permissions","same_timeout","balanced_cache","no_artificial_sleep","no_meaningless_duplication",
];

pub(crate) fn supports(command:&[String])->bool{
    matches!(command,[root,action] if (root=="prove"&&action=="integrations")||(root=="benchmark"&&action=="compare"))
}

pub(crate) struct ProofDecision{pub(crate)value:Value,pub(crate)exit_code:u8}

pub(crate) fn execute(command:&[String],arguments:&[String])->Result<Option<ProofDecision>,String>{
    if !supports(command){return Ok(None)}
    match command{
        [root,action] if root=="prove"&&action=="integrations"=>{
            let path=positional_after(arguments,"prove","integrations",0,&["--integration"])?;
            let integration=option_value(arguments,"--integration")?;
            let value=prove_integrations(Path::new(path),integration.as_deref())?;
            let code=if value["ok"].as_bool().unwrap_or(false){0}else{4};
            Ok(Some(ProofDecision{value,exit_code:code}))
        }
        [root,action] if root=="benchmark"&&action=="compare"=>{
            let baseline=required_option(arguments,"--baseline")?;
            let syntavra=required_option(arguments,"--syntavra")?;
            let config=required_option(arguments,"--config")?;
            let tier=required_option(arguments,"--tier")?;
            let value=benchmark_compare(Path::new(&baseline),Path::new(&syntavra),Path::new(&config),&tier)?;
            if let Some(output)=option_value(arguments,"--output")?{
                if let Some(parent)=Path::new(&output).parent(){fs::create_dir_all(parent).map_err(|error|format!("BENCHMARK_OUTPUT_PARENT_FAILED:{error}"))?;}
                fs::write(&output,serde_json::to_vec_pretty(&sort_json(&value)).map_err(|error|format!("BENCHMARK_OUTPUT_JSON_FAILED:{error}"))?).map_err(|error|format!("BENCHMARK_OUTPUT_WRITE_FAILED:{error}"))?;
            }
            let code=if value["claim"]["status"].as_str()==Some("PASS"){0}else{3};
            Ok(Some(ProofDecision{value,exit_code:code}))
        }
        _=>Ok(None),
    }
}

fn prove_integrations(path:&Path,integration_filter:Option<&str>)->Result<Value,String>{
    let raw=fs::read(path).map_err(|error|format!("LIVE_RECEIPT_READ_FAILED:{error}"))?;
    let value=serde_json::from_slice::<Value>(&raw).map_err(|error|format!("LIVE_RECEIPT_JSON_INVALID:{error}"))?;
    let rows=value.get("receipts").unwrap_or(&value).as_array().ok_or_else(||"receipt file must contain a list or {'receipts': [...]} object".to_owned())?;
    let rows=rows.iter().filter(|row|row.is_object()).filter(|row|integration_filter.is_none_or(|wanted|row["integration_id"].as_str()==Some(wanted))).cloned().collect::<Vec<_>>();
    let mut invalid=Vec::<Value>::new();
    let mut groups=BTreeMap::<String,Vec<Value>>::new();
    for row in &rows{
        let reasons=validate_live_receipt(row);
        if !reasons.is_empty(){invalid.push(json!({"receipt_id":row["receipt_id"].as_str().unwrap_or_default(),"reasons":reasons}));}
        groups.entry(row["integration_id"].as_str().unwrap_or_default().to_owned()).or_default().push(row.clone());
    }
    let mut certified=Vec::<String>::new();
    let mut pending=Map::new();
    for(id,items)in &groups{
        let valid=items.iter().filter(|row|validate_live_receipt(row).is_empty()).collect::<Vec<_>>();
        let mut reasons=Vec::<String>::new();
        if valid.len()<3{reasons.push("insufficient-live-receipts".to_owned());}
        let systems=valid.iter().filter_map(|row|row["operating_system"].as_str()).collect::<BTreeSet<_>>();
        if systems.len()<2{reasons.push("insufficient-operating-system-diversity".to_owned());}
        let harnesses=valid.iter().filter_map(|row|row["harness_commit"].as_str()).collect::<BTreeSet<_>>();
        if harnesses.len()!=1{reasons.push("harness-commit-not-pinned".to_owned());}
        if reasons.is_empty(){certified.push(id.clone());}else{pending.insert(id.clone(),serde_json::to_value(reasons).map_err(|error|format!("LIVE_PENDING_JSON_FAILED:{error}"))?);}
    }
    if let Some(id)=integration_filter{if !groups.contains_key(id){pending.insert(id.to_owned(),json!(["no-live-receipts"]));}}
    certified.sort();
    let ok=!certified.is_empty()&&invalid.is_empty()&&pending.is_empty();
    Ok(json!({
        "ok":ok,
        "claim":if ok{"LIVE_INTEGRATION_CERTIFIED"}else{"LIVE_INTEGRATION_CERTIFICATION_NOT_PROVEN"},
        "version":"0.0.1","channel":"pre-release","certified_integrations":certified,"pending":pending,"invalid":invalid,
        "metrics":{"receipts":rows.len(),"valid_receipts":rows.len().saturating_sub(invalid.len()),"integrations_observed":groups.len(),"integrations_certified":certified.len()},
        "requirements":{"minimum_receipts_per_integration":3,"minimum_operating_systems":2,"pinned_harness_commit":true,"external_non_synthetic":true}
    }))
}

fn validate_live_receipt(row:&Value)->Vec<String>{
    let mut reasons=Vec::<String>::new();
    let integration=row["integration_id"].as_str().unwrap_or_default();
    let family=row["family"].as_str().unwrap_or_default();
    match integration_family(integration){Some(expected) if expected!=family=>reasons.push("integration-family-mismatch".to_owned()),None=>reasons.push("unknown-integration".to_owned()),_=>{}}
    if !matches!(family,"provider"|"framework"|"host"){reasons.push("invalid-family".to_owned());}
    for(name,key)in[("receipt-id","receipt_id"),("integration-id","integration_id"),("observed-at","observed_at"),("adapter-version","adapter_version"),("operating-system","operating_system"),("runtime-version","runtime_version")]{if row[key].as_str().unwrap_or_default().is_empty(){reasons.push(format!("missing-{name}"));}}
    let observed=row["observed_at"].as_str().unwrap_or_default();
    if observed.is_empty()||!observed.contains('T'){reasons.push("invalid-observed-at".to_owned());}else if !(observed.ends_with('Z')||observed.rfind(['+','-']).is_some_and(|index|index>observed.find('T').unwrap_or(0))){reasons.push("observed-at-missing-timezone".to_owned());}
    if row["syntavra_version"].as_str()!=Some("0.0.1"){reasons.push("version-mismatch".to_owned());}
    if row["syntavra_channel"].as_str()!=Some("pre-release"){reasons.push("channel-mismatch".to_owned());}
    for(name,key)in[("environment-hash","environment_hash"),("config-hash","config_hash"),("artifact-hash","artifact_hash")]{if !lower_hex(row[key].as_str().unwrap_or_default(),64){reasons.push(format!("invalid-{name}"));}}
    if !lower_hex(row["harness_commit"].as_str().unwrap_or_default(),40){reasons.push("invalid-harness-commit".to_owned());}
    if !row["external"].as_bool().unwrap_or(false){reasons.push("not-external".to_owned());}
    if row["synthetic"].as_bool().unwrap_or(true){reasons.push("synthetic-receipt".to_owned());}
    let mut required=vec![("install-succeeded","install_succeeded"),("doctor-passed","doctor_passed"),("request-succeeded","request_succeeded"),("response-succeeded","response_succeeded"),("rollback-verified","rollback_verified")];
    if family=="provider"{required.push(("provider-usage-captured","provider_usage_captured"));required.push(("streaming-verified","streaming_verified"));}
    if family=="framework"{required.push(("provider-usage-captured","provider_usage_captured"));}
    if family=="host"{required.push(("tool-routing-verified","tool_routing_verified"));required.push(("session-continuity-verified","session_continuity_verified"));}
    for(name,key)in required{if !row[key].as_bool().unwrap_or(false){reasons.push(format!("{name}-required"));}}
    let mut seen=BTreeSet::new();reasons.retain(|reason|seen.insert(reason.clone()));reasons
}

fn integration_family(id:&str)->Option<&'static str>{
    const PROVIDERS:&[&str]=&["openai","anthropic","gemini","aws-bedrock","azure-openai","vertex-ai","openrouter","mistral","groq","cohere"];
    const FRAMEWORKS:&[&str]=&["openai-python","openai-node","anthropic-python","anthropic-node","google-genai","vercel-ai-sdk","litellm","langchain","langgraph","agno","strands","asgi","openclaw","mcp","openai-compatible"];
    const HOSTS:&[&str]=&["claude-code","codex","gemini-cli","vscode-copilot","jetbrains-copilot","cursor","windsurf","opencode","cline","roo-code","qwen-code","kiro","zed","pi","omp","openclaw","aider","continue"];
    if PROVIDERS.contains(&id){Some("provider")}else if FRAMEWORKS.contains(&id){Some("framework")}else if HOSTS.contains(&id){Some("host")}else{None}
}

fn benchmark_compare(baseline_path:&Path,syntavra_path:&Path,config_path:&Path,tier:&str)->Result<Value,String>{
    let baseline=load_result_rows(baseline_path)?;
    let syntavra=load_result_rows(syntavra_path)?;
    let config=load_object(config_path)?;
    let signal_by_rep=syntavra.iter().filter_map(|row|row["repetition"].as_i64().map(|rep|(rep,row))).collect::<BTreeMap<_,_>>();
    let mut valid=Vec::<(&Value,&Value)>::new();
    let mut invalid=Vec::<Value>::new();
    for base in &baseline{
        let rep=base["repetition"].as_i64().unwrap_or(-1);
        let Some(sig)=signal_by_rep.get(&rep).copied()else{invalid.push(json!({"repetition":rep,"reason":"missing-syntavra-arm"}));continue};
        if !same_identity(base,sig){invalid.push(json!({"repetition":rep,"reason":"paired-identity-mismatch"}));continue}
        if !base["success"].as_bool().unwrap_or(false)||!sig["success"].as_bool().unwrap_or(false)||(base["verified_work"].as_f64().unwrap_or(0.0)-sig["verified_work"].as_f64().unwrap_or(0.0)).abs()>f64::EPSILON{invalid.push(json!({"repetition":rep,"reason":"unequal-verified-work"}));continue}
        if base["verifier_skips"].as_i64().unwrap_or(0)!=0||sig["verifier_skips"].as_i64().unwrap_or(0)!=0{invalid.push(json!({"repetition":rep,"reason":"required-verifier-skipped"}));continue}
        valid.push((base,sig));
    }
    let controls=config["controls"].as_object().cloned().unwrap_or_default();
    let integrity=REQUIRED_CONTROLS.iter().map(|name|((*name).to_owned(),controls.get(*name).and_then(Value::as_bool).unwrap_or(false))).collect::<BTreeMap<_,_>>();
    let config_valid=integrity.values().all(|value|*value)&&matches!(tier,"1X"|"20X"|"30X"|"100X");
    let observed_rows=valid.iter().flat_map(|(base,sig)|[base["observed_axes"].clone(),sig["observed_axes"].clone()]).filter(|row|row.is_object()).collect::<Vec<_>>();
    let mut raw=BTreeMap::<String,f64>::new();
    for axis in AXES{let values=observed_rows.iter().filter_map(|row|row[*axis].as_f64()).collect::<Vec<_>>();raw.insert((*axis).to_owned(),values.into_iter().reduce(f64::min).unwrap_or(0.0));}
    let baseline_axes=config.get("observed_baseline").cloned().unwrap_or_else(default_observed_baseline);
    let difficulty=match evaluate_observed(tier,&raw,&baseline_axes,&integrity){Ok(value)=>value,Err(error)=>{invalid.push(json!({"repetition":Value::Null,"reason":format!("observed-difficulty-invalid:{error}")}));evaluate_configured(tier,&config["axes"],&integrity)}};
    let quota_available=!valid.is_empty()&&valid.iter().all(|(base,sig)|base["quota_cost"].as_f64().is_some_and(|v|v>0.0)&&sig["quota_cost"].as_f64().is_some_and(|v|v>0.0));
    let baseline_costs=valid.iter().map(|(base,_)|base["quota_cost"].as_f64().unwrap_or(0.0)).collect::<Vec<_>>();
    let syntavra_costs=valid.iter().map(|(_,sig)|sig["quota_cost"].as_f64().unwrap_or(0.0)).collect::<Vec<_>>();
    let security_regressions=valid.iter().map(|(_,sig)|sig["security_regressions"].as_i64().unwrap_or(0)).sum::<i64>();
    let integrity_violations=invalid.len()+usize::from(!config_valid);
    let claim=decide_claim(tier,&baseline_costs,&syntavra_costs,&difficulty,quota_available,security_regressions,integrity_violations);
    let token_ratios=valid.iter().map(|(base,sig)|{let left=token_total(base);let right=token_total(sig).max(1);left as f64/right as f64}).collect::<Vec<_>>();
    Ok(json!({
        "valid_pairs":valid.len(),"invalid_runs":invalid,"difficulty":difficulty,
        "diagnostics":{"token_ratios":token_ratios,"baseline_wait_calls":valid.iter().map(|(base,_)|base["wait_calls"].as_i64().unwrap_or(0)).sum::<i64>(),"syntavra_wait_calls":valid.iter().map(|(_,sig)|sig["wait_calls"].as_i64().unwrap_or(0)).sum::<i64>(),"quota_available":quota_available},
        "claim":claim
    }))
}

fn load_result_rows(path:&Path)->Result<Vec<Value>,String>{let value=load_object_or_array(path)?;let rows=if let Some(array)=value.as_array(){array.clone()}else{value["results"].as_array().cloned().unwrap_or_default()};Ok(rows.into_iter().filter(Value::is_object).collect())}
fn load_object(path:&Path)->Result<Value,String>{let value=load_object_or_array(path)?;if !value.is_object(){return Err("BENCHMARK_CONFIG_NOT_OBJECT".to_owned())}Ok(value)}
fn load_object_or_array(path:&Path)->Result<Value,String>{serde_json::from_slice(&fs::read(path).map_err(|error|format!("BENCHMARK_READ_FAILED:{error}"))?).map_err(|error|format!("BENCHMARK_JSON_INVALID:{error}"))}
fn same_identity(left:&Value,right:&Value)->bool{["repository_tree","model","reasoning","prompt_hash","verifier_hash","cache_mode","permissions_hash","workload_hash"].iter().all(|key|left[*key]==right[*key])&&number_equal(&left["timeout_seconds"],&right["timeout_seconds"])}
fn number_equal(left:&Value,right:&Value)->bool{match(left.as_f64(),right.as_f64()){(Some(a),Some(b))=>(a-b).abs()<=f64::EPSILON,_=>left==right}}
fn token_total(row:&Value)->i64{row["fresh_input_tokens"].as_i64().unwrap_or(0)+row["output_tokens"].as_i64().unwrap_or(0)+row["reasoning_tokens"].as_i64().unwrap_or(0)}
fn default_observed_baseline()->Value{json!({"R":50.0,"C":4.0,"O":1_000_000.0,"T":1.0,"P":1.0,"V":1.0,"X":1000.0,"H":10.0,"S":1.0,"F":1.0})}

fn evaluate_observed(tier:&str,raw:&BTreeMap<String,f64>,baseline:&Value,integrity:&BTreeMap<String,bool>)->Result<Value,String>{let mut factors=BTreeMap::new();for axis in AXES{let value=*raw.get(*axis).unwrap_or(&0.0);let unit=baseline[*axis].as_f64().unwrap_or(0.0);if value<=0.0||unit<=0.0||!value.is_finite()||!unit.is_finite(){return Err(format!("invalid observed axis: {axis}"))}factors.insert((*axis).to_owned(),value/unit);}Ok(score_axes(tier,&factors,integrity,true))}
fn evaluate_configured(tier:&str,axes:&Value,integrity:&BTreeMap<String,bool>)->Value{let values=AXES.iter().map(|axis|((*axis).to_owned(),axes[*axis].as_f64().unwrap_or(0.0))).collect::<BTreeMap<_,_>>();let mut result=score_axes(tier,&values,integrity,true);result["observed"]=Value::Bool(false);result}
fn score_axes(tier:&str,axes:&BTreeMap<String,f64>,integrity:&BTreeMap<String,bool>,observed:bool)->Value{
    let rules=match tier{"20X"=>(20.0,5,5.0,3,10.0,2.0),"30X"=>(30.0,6,7.5,3,15.0,3.0),"100X"=>(100.0,7,20.0,4,50.0,5.0),_=>(0.0,0,0.0,0,0.0,0.0)};
    let mut errors=Vec::<String>::new();let mut safe=BTreeMap::new();for axis in AXES{let value=*axes.get(*axis).unwrap_or(&0.0);if value<=0.0||!value.is_finite(){errors.push(format!("invalid-axis:{axis}"));}safe.insert((*axis).to_owned(),value.clamp(0.01,1000.0));}
    let geometric=(safe.values().map(|value|value.ln()).sum::<f64>()/safe.len() as f64).exp();let harmonic=safe.len() as f64/safe.values().map(|value|1.0/value).sum::<f64>();let critical_floor=CRITICAL.iter().map(|axis|safe[*axis]).fold(f64::INFINITY,f64::min);let score=if tier=="1X"{1.0}else{geometric*(harmonic/geometric).powf(0.35)*(critical_floor/geometric.max(0.01)).sqrt().add(0.5).min(1.5)};
    let mut checks=Map::new();checks.insert("score".to_owned(),Value::Bool(score>=rules.0));checks.insert("multi_axis_participation".to_owned(),Value::Bool(safe.values().filter(|value|**value>=rules.2).count()>=rules.1));checks.insert("critical_high".to_owned(),Value::Bool(CRITICAL.iter().filter(|axis|safe[**axis]>=rules.4).count()>=rules.3));checks.insert("critical_floor".to_owned(),Value::Bool(CRITICAL.iter().all(|axis|safe[*axis]>=rules.5)));checks.insert("observed_measurement".to_owned(),Value::Bool(observed||tier=="1X"));for(name,passed)in integrity{checks.insert(format!("integrity:{name}"),Value::Bool(*passed));if !passed{errors.push(format!("integrity-failed:{name}"));}}if !observed&&tier!="1X"{errors.push("difficulty-is-configured-not-observed".to_owned());}let qualified=errors.is_empty()&&checks.values().all(|value|value.as_bool().unwrap_or(false));json!({"tier":tier,"score":score,"axes":axes,"checks":checks,"qualified":qualified,"integrity_errors":errors,"observed":observed})
}

fn decide_claim(tier:&str,baseline:&[f64],syntavra:&[f64],difficulty:&Value,quota_available:bool,security_regressions:i64,integrity_violations:usize)->Value{
    let mut reasons=Vec::<String>::new();if !quota_available{reasons.push("actual-quota-unavailable".to_owned());}if baseline.len()!=syntavra.len()||baseline.is_empty(){reasons.push("invalid-paired-sample-count".to_owned());}if baseline.len()<10{reasons.push(format!("insufficient-valid-pairs:{}<10",baseline.len()));}
    let ratios=baseline.iter().zip(syntavra).filter_map(|(base,signal)|(*base>0.0&&*signal>0.0).then_some(*base / *signal)).collect::<Vec<_>>();if ratios.len()!=baseline.len(){reasons.push("nonpositive-cost".to_owned());}let median=median(&ratios);let geometric=(!ratios.is_empty()).then(||(ratios.iter().map(|value|value.ln()).sum::<f64>()/ratios.len() as f64).exp());let ci=confidence_interval_conservative(&ratios);
    if !difficulty["observed"].as_bool().unwrap_or(false){reasons.push("difficulty-not-observed".to_owned());}if !difficulty["qualified"].as_bool().unwrap_or(false){reasons.push("difficulty-not-qualified".to_owned());}if median.is_none_or(|value|value<5.0){reasons.push("median-below-5x".to_owned());}if geometric.is_none_or(|value|value<5.0){reasons.push("geometric-mean-below-5x".to_owned());}if ci.is_none_or(|(low,_)|low<5.0){reasons.push("confidence-lower-bound-below-5x".to_owned());}if security_regressions!=0{reasons.push("security-regression".to_owned());}if integrity_violations!=0{reasons.push("benchmark-integrity-violation".to_owned());}reasons.sort();reasons.dedup();let claim=if reasons.is_empty(){match tier{"20X"=>"5X_20X_QUALIFIED","30X"=>"5X_30X_ENDURANCE_QUALIFIED","100X"=>"5X_100X_ABSOLUTE_QUALIFIED",_=>"5X_BASELINE_PROVEN"}}else{"5X_NOT_PROVEN"};let payload=json!({"tier":tier,"difficulty":difficulty,"baseline":baseline,"syntavra":syntavra,"ratios":ratios,"median":median,"geometric":geometric,"ci":ci,"minimum_pairs":10,"reasons":reasons});json!({"claim":claim,"status":if claim=="5X_NOT_PROVEN"{"NOT_PROVEN"}else{"PASS"},"difficulty_score":difficulty["score"],"median_ratio":median,"geometric_mean_ratio":geometric,"confidence_interval_95":ci,"reasons":reasons,"evidence_receipt":format!("sha256:{}",sha256_hex(canonical_json(&payload).unwrap_or_default().as_bytes()))})
}
fn median(values:&[f64])->Option<f64>{if values.is_empty(){return None}let mut rows=values.to_vec();rows.sort_by(|a,b|a.partial_cmp(b).unwrap_or(std::cmp::Ordering::Equal));let middle=rows.len()/2;Some(if rows.len()%2==0{(rows[middle-1]+rows[middle])/2.0}else{rows[middle]})}
fn confidence_interval_conservative(values:&[f64])->Option<(f64,f64)>{if values.is_empty(){return None}let mut rows=values.to_vec();rows.sort_by(|a,b|a.partial_cmp(b).unwrap_or(std::cmp::Ordering::Equal));let low_index=((rows.len() as f64)*0.025).floor() as usize;let high_index=(((rows.len() as f64)*0.975).ceil() as usize).saturating_sub(1).min(rows.len()-1);Some((rows[low_index.min(rows.len()-1)],rows[high_index]))}

fn lower_hex(value:&str,len:usize)->bool{value.len()==len&&value.bytes().all(|byte|byte.is_ascii_digit()||(b'a'..=b'f').contains(&byte))}
fn canonical_json(value:&Value)->Result<String,String>{serde_json::to_string(&sort_json(value)).map_err(|error|format!("PROOF_JSON_SERIALIZE_FAILED:{error}"))}
fn sort_json(value:&Value)->Value{match value{Value::Object(map)=>{let mut keys=map.keys().collect::<Vec<_>>();keys.sort_unstable();let mut output=Map::new();for key in keys{output.insert(key.clone(),sort_json(&map[key]));}Value::Object(output)},Value::Array(rows)=>Value::Array(rows.iter().map(sort_json).collect()),_=>value.clone()}}
fn has_flag(arguments:&[String],flag:&str)->bool{arguments.iter().any(|value|value==flag)}
fn required_option(arguments:&[String],flag:&str)->Result<String,String>{option_value(arguments,flag)?.ok_or_else(||format!("{flag}_REQUIRED"))}
fn option_value(arguments:&[String],flag:&str)->Result<Option<String>,String>{let mut output=None;let mut index=0usize;while index<arguments.len(){let current=&arguments[index];let found=if current==flag{index+=1;Some(arguments.get(index).ok_or_else(||format!("{flag}_VALUE_MISSING"))?.clone())}else{current.strip_prefix(flag).and_then(|tail|tail.strip_prefix('=')).map(str::to_owned)};if let Some(found)=found{if output.is_some(){return Err(format!("{flag}_DUPLICATE"))}output=Some(found);}index+=1;}Ok(output)}
fn positional_after<'a>(arguments:&'a[String],root:&str,action:&str,position:usize,value_flags:&[&str])->Result<&'a str,String>{let start=arguments.windows(2).position(|row|row[0]==root&&row[1]==action).map(|index|index+2).ok_or_else(||format!("PROOF_ACTION_NOT_FOUND:{root}:{action}"))?;let mut values=Vec::new();let mut index=start;while index<arguments.len(){if value_flags.contains(&arguments[index].as_str()){index+=2;continue}if arguments[index].starts_with("--"){index+=1;continue}values.push(arguments[index].as_str());index+=1;}values.get(position).copied().ok_or_else(||format!("PROOF_POSITIONAL_MISSING:{root}:{action}:{position}"))}
