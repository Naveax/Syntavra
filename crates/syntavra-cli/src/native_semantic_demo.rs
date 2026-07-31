#![forbid(unsafe_code)]

use std::collections::BTreeSet;

use serde_json::{json, Value};

#[derive(Clone, Copy)]
struct DemoNode {
    node_id: &'static str,
    kind: &'static str,
    qualified_name: &'static str,
    path: &'static str,
    start_line: u64,
    end_line: u64,
    language: &'static str,
    evidence_ref: &'static str,
    change_frequency: f64,
    ownership: &'static [&'static str],
}

const AUTH_OWNERSHIP: [&str; 1] = ["security"];
const EMPTY_OWNERSHIP: [&str; 0] = [];
const AUTH: DemoNode = DemoNode {
    node_id: "auth",
    kind: "function",
    qualified_name: "auth.refresh",
    path: "src/auth.py",
    start_line: 10,
    end_line: 40,
    language: "python",
    evidence_ref: "syntavra://evidence/auth",
    change_frequency: 0.9,
    ownership: &AUTH_OWNERSHIP,
};
const TEST: DemoNode = DemoNode {
    node_id: "test",
    kind: "test",
    qualified_name: "test_auth_refresh",
    path: "tests/test_auth.py",
    start_line: 5,
    end_line: 30,
    language: "python",
    evidence_ref: "syntavra://evidence/test",
    change_frequency: 0.0,
    ownership: &EMPTY_OWNERSHIP,
};
const NODES: [DemoNode; 2] = [AUTH, TEST];

fn add_token(values: &mut BTreeSet<String>, token: &str) {
    let normalized = token.to_ascii_lowercase();
    if normalized.len() > 1 {
        values.insert(normalized.clone());
    }
    for part in normalized.split(['.', '_', '/', ':', '-']) {
        if part.len() > 1 {
            values.insert(part.to_owned());
        }
    }
}

fn tokens(value: &str) -> BTreeSet<String> {
    let mut values = BTreeSet::new();
    let mut current = String::new();
    for character in value.chars() {
        if character.is_ascii_alphanumeric() || matches!(character, '_' | '.' | '/' | ':' | '-') {
            current.push(character);
        } else if !current.is_empty() {
            add_token(&mut values, &current);
            current.clear();
        }
    }
    if !current.is_empty() {
        add_token(&mut values, &current);
    }
    values
}

fn node_json(node: DemoNode) -> Value {
    json!({
        "node_id": node.node_id,
        "kind": node.kind,
        "qualified_name": node.qualified_name,
        "path": node.path,
        "start_line": node.start_line,
        "end_line": node.end_line,
        "language": node.language,
        "evidence_ref": node.evidence_ref,
        "change_frequency": node.change_frequency,
        "ownership": node.ownership,
        "metadata": {},
    })
}

fn query_argument(arguments: &[String]) -> Result<&str, String> {
    arguments
        .windows(3)
        .find(|window| {
            matches!(window[0].as_str(), "semantic-demo" | "structural-v2")
                && window[1] == "demo"
        })
        .map(|window| window[2].as_str())
        .ok_or_else(|| "SEMANTIC_DEMO_QUERY_MISSING".to_owned())
}

fn edge_counts(node_id: &str) -> (u64, u64) {
    match node_id {
        "auth" => (1, 0),
        "test" => (0, 1),
        _ => (0, 0),
    }
}

fn query_results(query: &str) -> Vec<Value> {
    let query_terms = tokens(query);
    let lowered_query = query.to_ascii_lowercase();
    let mut rows = Vec::new();

    for node in NODES {
        let corpus = tokens(&format!(
            "{} {} {} {}",
            node.qualified_name, node.path, node.kind, node.language
        ));
        let matched = query_terms
            .intersection(&corpus)
            .cloned()
            .collect::<Vec<_>>();
        if !query_terms.is_empty() && matched.is_empty() {
            continue;
        }
        let lexical = matched.len() as f64 / query_terms.len().max(1) as f64;
        let exact = f64::from(
            node.qualified_name
                .to_ascii_lowercase()
                .contains(&lowered_query),
        );
        let (inbound, outbound) = edge_counts(node.node_id);
        let centrality = ((inbound + outbound) as f64).ln_1p() / 5.0;
        let change = node.change_frequency.clamp(0.0, 1.0);
        let score = lexical * 50.0 + exact * 20.0 + centrality * 8.0 + change * 7.0;
        let mut reasons = Vec::new();
        if !matched.is_empty() {
            reasons.push("lexical");
        }
        if exact != 0.0 {
            reasons.push("exact-qualified-name");
        }
        if centrality != 0.0 {
            reasons.push("graph-centrality");
        }
        if change != 0.0 {
            reasons.push("change-frequency");
        }
        rows.push((score, node, json!({
            "node": node_json(node),
            "score": score,
            "matched_terms": matched,
            "inbound_edges": inbound,
            "outbound_edges": outbound,
            "reasons": reasons,
        })));
    }

    rows.sort_by(|left, right| {
        right
            .0
            .total_cmp(&left.0)
            .then_with(|| left.1.path.cmp(right.1.path))
            .then_with(|| left.1.start_line.cmp(&right.1.start_line))
            .then_with(|| left.1.node_id.cmp(right.1.node_id))
    });
    rows.into_iter().map(|(_, _, value)| value).collect()
}

fn impact() -> Value {
    json!({
        "root": "auth",
        "impacted": [node_json(AUTH), node_json(TEST)],
        "affected_tests": [node_json(TEST)],
        "exact_evidence_complete": true,
    })
}

pub fn execute(arguments: &[String]) -> Result<Value, String> {
    let query = query_argument(arguments)?;
    Ok(json!({
        "results": query_results(query),
        "impact": impact(),
    }))
}

#[cfg(test)]
mod tests {
    use super::execute;

    #[test]
    fn auth_query_returns_auth_first() {
        let arguments = vec![
            "semantic-demo".to_owned(),
            "demo".to_owned(),
            "auth".to_owned(),
        ];
        let value = execute(&arguments).expect("semantic demo");
        assert_eq!(value["results"][0]["node"]["node_id"], "auth");
        assert_eq!(value["impact"]["affected_tests"][0]["node_id"], "test");
    }
}
