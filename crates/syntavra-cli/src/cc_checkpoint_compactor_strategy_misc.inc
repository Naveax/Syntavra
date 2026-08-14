fn cc_strategy_search(lines:&[String])->Vec<String>{
    let errors=cc_strategy_errors(lines);let mut grouped=std::collections::HashMap::<String,Vec<String>>::new();let mut order=Vec::<String>::new();
    for line in lines {if line.trim().is_empty(){continue} let key=if let Some((before,_))=line.split_once(':'){before.to_owned()}else{let p=Path::new(line).parent().unwrap_or_else(||Path::new("."));let s=p.to_string_lossy().to_string();if s.is_empty(){".".to_owned()}else{s}};if !grouped.contains_key(&key){order.push(key.clone())}grouped.entry(key).or_default().push(line.clone());}
    let total=grouped.values().map(Vec::len).sum::<usize>();let mut out=vec![format!("results={total} groups={}",grouped.len())];for key in order.into_iter().take(50){if let Some(values)=grouped.get(&key){out.push(format!("[{key}] {}",values.len()));out.extend(values.iter().take(5).cloned());}}out.extend(errors.iter().take(24).cloned());out
}
fn cc_strategy_lint(lines:&[String])->Vec<String>{
    let errors=cc_strategy_errors(lines);let Some(lr)=cc_strategy_location_re() else{return Vec::new()};let Some(dr)=regex::Regex::new(r"(?i)\b(warning|error|note):").ok() else{return Vec::new()};let Some(lead)=regex::Regex::new(r"^\S+\.(?:py|js|jsx|ts|tsx|rs|go):\d+").ok() else{return Vec::new()};let Some(sr)=regex::Regex::new(r"(?i)\b(found|checked|fixed|violations?|problems?)\b").ok() else{return Vec::new()};
    let diagnostics=cc_strategy_dedup(lines.iter().filter(|l|lr.is_match(l)||dr.is_match(l)||lead.is_match(l)).cloned());let summaries=cc_strategy_dedup(lines.iter().filter(|l|sr.is_match(l)).cloned());let mut out=diagnostics.into_iter().take(120).collect::<Vec<_>>();out.extend(summaries.iter().skip(summaries.len().saturating_sub(20)).cloned());out.extend(errors.iter().take(24).cloned());out
}
fn cc_strategy_docker_build(lines:&[String])->Vec<String>{
    let errors=cc_strategy_errors(lines);let Some(re)=regex::Regex::new(r"(?i)(^#\d+|exporting|writing image|naming to|cached|done|warning|error|failed)").ok() else{return Vec::new()};let selected=cc_strategy_dedup(lines.iter().filter(|l|re.is_match(l)).cloned());let mut out=cc_strategy_head_tail(&selected,80,30);out.extend(errors.iter().take(32).cloned());out
}
fn cc_strategy_table(lines:&[String])->Vec<String>{let errors=cc_strategy_errors(lines);let nonempty=lines.iter().filter(|l|!l.trim().is_empty()).cloned().collect::<Vec<_>>();let mut out=cc_strategy_head_tail(&nonempty,35,15);out.extend(errors.iter().take(24).cloned());out}
fn cc_strategy_head_tail_selector(lines:&[String])->Vec<String>{let errors=cc_strategy_errors(lines);let nonempty=lines.iter().filter(|l|!l.trim().is_empty()).cloned().collect::<Vec<_>>();let mut out=cc_strategy_head_tail(&nonempty,30,20);out.extend(errors.iter().take(32).cloned());out}
fn cc_strategy_splitlines(value:&str)->Vec<String>{
    let chars=value.chars().collect::<Vec<_>>();if chars.is_empty(){return Vec::new()}let mut out=Vec::new();let mut current=String::new();let mut i=0usize;let mut ended=false;
    while i<chars.len(){let ch=chars[i];let boundary=matches!(ch,'\n'|'\r'|'\u{000B}'|'\u{000C}'|'\u{001C}'|'\u{001D}'|'\u{001E}'|'\u{0085}'|'\u{2028}'|'\u{2029}');if boundary{out.push(std::mem::take(&mut current));if ch=='\r'&&i+1<chars.len()&&chars[i+1]=='\n'{i+=1}ended=true}else{current.push(ch);ended=false}i+=1}if !ended{out.push(current)}out
}
