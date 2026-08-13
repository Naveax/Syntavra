fn cc_code_intel_index(project:&Path,state_root:&Path)->Result<rusqlite::Connection,String>{
    let command=vec!["inspect".to_owned(),"stats".to_owned()];
    let arguments=command.clone();
    native_structural::execute(&command,&arguments,project,state_root)?;
    let connection=rusqlite::Connection::open(state_root.join("structural.sqlite3")).map_err(|e|format!("CODE_INTEL_DB_OPEN_FAILED:{e}"))?;
    connection.execute("DELETE FROM structural_edges WHERE edge_type IN ('reads','writes')",[]).map_err(|e|format!("CODE_INTEL_EDGE_FILTER_FAILED:{e}"))?;
    Ok(connection)
}

fn cc_code_intel_symbol(project:&Path,row:&rusqlite::Row<'_>)->rusqlite::Result<Value>{
    let path:String=row.get(0)?;let name:String=row.get(1)?;let qualified:String=row.get(2)?;let kind:String=row.get(3)?;let line:i64=row.get(4)?;let end_line:i64=row.get(5)?;let parser:String=row.get(6)?;let confidence:f64=row.get(7)?;
    let source=fs::read_to_string(project.join(&path)).unwrap_or_default();let lines=source.lines().collect::<Vec<_>>();let start=(line.max(1)-1)as usize;let end=(end_line.max(line))as usize;
    let snippet=lines.get(start..end.min(lines.len())).unwrap_or(&[]).join("\n");
    let normalized=snippet.split_whitespace().collect::<Vec<_>>().join(" ");
    let body_hash=syntavra_core::sha256_hex(normalized.as_bytes());
    let complexity=1+regex::Regex::new(r"\b(?:if|for|while|try|match|and|or)\b|&&|\|\|").ok().map_or(0,|re|re.find_iter(&snippet).count());
    Ok(serde_json::json!({
        "id":format!("{path}:{qualified}:{line}"),"name":name,"qualified_name":qualified,"kind":kind,"path":path,
        "line":line,"end_line":end_line,"language":"python","bases":[],"calls":[],"imports":[],"body_hash":body_hash,
        "complexity":complexity,"exported":!name.starts_with('_'),"parser_backend":parser,"parse_confidence":confidence
    }))
}

fn cc_code_intel_symbols(project:&Path,connection:&rusqlite::Connection)->Result<Vec<Value>,String>{
    let mut statement=connection.prepare("SELECT path,name,qualified_name,kind,line,end_line,parser,confidence FROM structural_symbols ORDER BY path,line,qualified_name").map_err(|e|format!("CODE_INTEL_SYMBOL_PREPARE_FAILED:{e}"))?;
    let rows=statement.query_map([],|row|cc_code_intel_symbol(project,row)).map_err(|e|format!("CODE_INTEL_SYMBOL_QUERY_FAILED:{e}"))?
        .collect::<Result<Vec<_>,_>>().map_err(|e|format!("CODE_INTEL_SYMBOL_ROW_FAILED:{e}"))?;
    Ok(rows)
}

fn cc_code_intel_counts(connection:&rusqlite::Connection)->Result<(i64,i64,i64),String>{
    let files=connection.query_row("SELECT COUNT(*) FROM structural_files",[],|r|r.get(0)).map_err(|e|format!("CODE_INTEL_FILE_COUNT_FAILED:{e}"))?;
    let symbols=connection.query_row("SELECT COUNT(*) FROM structural_symbols",[],|r|r.get(0)).map_err(|e|format!("CODE_INTEL_SYMBOL_COUNT_FAILED:{e}"))?;
    let edges=connection.query_row("SELECT COUNT(*) FROM structural_edges WHERE edge_type!='defines'",[],|r|r.get(0)).map_err(|e|format!("CODE_INTEL_EDGE_COUNT_FAILED:{e}"))?;
    Ok((files,symbols,edges))
}
