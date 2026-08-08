#![forbid(unsafe_code)]

use std::cmp::Ordering;
use std::collections::BTreeSet;

use super::model::{integer_as_f64, ContextItem, PackInput};

fn float_order(left: f64, right: f64) -> Ordering {
    left.partial_cmp(&right).unwrap_or(Ordering::Equal)
}

fn dependency_closure(
    item_id: &str,
    input: &PackInput,
    selected: &BTreeSet<String>,
) -> Result<BTreeSet<String>, String> {
    let mut required = BTreeSet::<String>::new();
    let mut stack = vec![item_id.to_owned()];
    while let Some(current) = stack.pop() {
        if selected.contains(&current) || required.contains(&current) {
            continue;
        }
        let item = input
            .by_id
            .get(&current)
            .ok_or_else(|| format!("missing context dependency: {current}"))?;
        required.insert(current);
        stack.extend(item.dependencies.iter().cloned());
    }
    Ok(required)
}

pub(super) fn required_selection(input: &PackInput) -> Result<(BTreeSet<String>, i64), String> {
    let required_ids = input
        .by_id
        .values()
        .filter(|item| item.mandatory || input.roles.contains(&item.role))
        .map(|item| item.item_id.clone())
        .collect::<BTreeSet<_>>();
    let mut expanded = BTreeSet::<String>::new();
    for item_id in required_ids {
        expanded.extend(dependency_closure(&item_id, input, &expanded)?);
    }
    let cost = expanded
        .iter()
        .map(|item_id| input.by_id[item_id].tokens.max(0))
        .sum();
    Ok((expanded, cost))
}

fn marginal(
    item_id: &str,
    input: &PackInput,
    selected: &BTreeSet<String>,
) -> Result<(f64, i64, BTreeSet<String>), String> {
    let closure = dependency_closure(item_id, input, selected)?;
    let cost = closure
        .iter()
        .map(|value| input.by_id[value].tokens.max(0))
        .sum();
    let utility = closure
        .iter()
        .map(|value| {
            let item = &input.by_id[value];
            item.utility.max(0.0) * item.confidence.clamp(0.0, 1.0)
        })
        .sum();
    Ok((utility, cost, closure))
}

pub(super) fn greedy_selection(
    input: &PackInput,
    selected: &mut BTreeSet<String>,
    used: &mut i64,
) -> Result<(), String> {
    let mut candidates = input
        .by_id
        .keys()
        .filter(|item_id| !selected.contains(*item_id))
        .cloned()
        .collect::<Vec<_>>();
    while !candidates.is_empty() {
        let mut scored = Vec::new();
        for item_id in &candidates {
            let (utility, cost, closure) = marginal(item_id, input, selected)?;
            let density = utility / integer_as_f64(cost.max(1))?;
            scored.push((density, utility, cost, item_id.clone(), closure));
        }
        scored.sort_by(|left, right| {
            float_order(right.0, left.0)
                .then_with(|| float_order(right.1, left.1))
                .then_with(|| left.2.cmp(&right.2))
                .then_with(|| left.3.cmp(&right.3))
        });
        let Some((_, _, cost, _, closure)) = scored
            .into_iter()
            .find(|(_, _, cost, _, _)| *cost <= input.budget - *used)
        else {
            break;
        };
        selected.extend(closure);
        *used += cost;
        candidates.retain(|item_id| !selected.contains(item_id));
    }
    Ok(())
}

fn optional_density(item: &ContextItem) -> Result<f64, String> {
    Ok(item.utility * item.confidence / integer_as_f64(item.tokens.max(1))?)
}

fn depended_on(outgoing: &ContextItem, input: &PackInput, selected: &BTreeSet<String>) -> bool {
    selected.iter().any(|item_id| {
        item_id != &outgoing.item_id
            && input.by_id[item_id]
                .dependencies
                .contains(&outgoing.item_id)
    })
}

pub(super) fn replacement_selection(
    input: &PackInput,
    expanded: &BTreeSet<String>,
    selected: &mut BTreeSet<String>,
    used: &mut i64,
) -> Result<(), String> {
    let mut optional = selected
        .difference(expanded)
        .map(|item_id| input.by_id[item_id].clone())
        .collect::<Vec<_>>();
    optional.sort_by(|left, right| {
        let left_density = optional_density(left).unwrap_or(0.0);
        let right_density = optional_density(right).unwrap_or(0.0);
        float_order(left_density, right_density).then_with(|| left.item_id.cmp(&right.item_id))
    });
    let mut dropped = input
        .by_id
        .values()
        .filter(|item| !selected.contains(&item.item_id))
        .cloned()
        .collect::<Vec<_>>();
    dropped.sort_by(|left, right| {
        float_order(
            right.utility * right.confidence,
            left.utility * left.confidence,
        )
        .then_with(|| left.item_id.cmp(&right.item_id))
    });
    for incoming in dropped {
        let (utility, cost, closure) = marginal(&incoming.item_id, input, selected)?;
        for outgoing in &optional {
            if !selected.contains(&outgoing.item_id)
                || utility <= outgoing.utility * outgoing.confidence
            {
                continue;
            }
            if cost <= input.budget - *used + outgoing.tokens
                && !depended_on(outgoing, input, selected)
            {
                selected.remove(&outgoing.item_id);
                selected.extend(closure);
                *used = *used - outgoing.tokens + cost;
                break;
            }
        }
    }
    Ok(())
}
