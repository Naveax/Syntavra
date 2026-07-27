#![forbid(unsafe_code)]

pub const PRODUCT_NAME: &str = "Syntavra";
pub const PRODUCT_VERSION: &str = "0.0.1";
pub const RELEASE_CHANNEL: &str = "pre-release";
pub const ENGINE_NAME: &str = "rust";
pub const ENGINE_STABILITY: &str = "experimental";
pub const CONTRACT_VERSION: u32 = 1;

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub struct Capability {
    pub name: &'static str,
    pub maturity: &'static str,
    pub mutation: &'static str,
}

pub const ENGINE_CAPABILITIES: &[Capability] = &[
    Capability {
        name: "config.resolve",
        maturity: "preview",
        mutation: "read-only",
    },
    Capability {
        name: "engine.capabilities",
        maturity: "preview",
        mutation: "read-only",
    },
    Capability {
        name: "engine.contract-hash",
        maturity: "preview",
        mutation: "read-only",
    },
    Capability {
        name: "receipt.inspect",
        maturity: "preview",
        mutation: "read-only",
    },
    Capability {
        name: "state.broker-snapshot",
        maturity: "preview",
        mutation: "read-only",
    },
    Capability {
        name: "state.inspect",
        maturity: "preview",
        mutation: "read-only",
    },
    Capability {
        name: "state.layout",
        maturity: "preview",
        mutation: "read-only",
    },
    Capability {
        name: "status",
        maturity: "preview",
        mutation: "read-only",
    },
    Capability {
        name: "version",
        maturity: "preview",
        mutation: "read-only",
    },
];

/// Canonical descriptor for the first dual-engine contract generation.
///
/// This string is deliberately independent from serialization libraries so the
/// Python and Rust implementations can hash the exact same UTF-8 bytes.
pub const CONTRACT_DESCRIPTOR: &str = concat!(
    "product=Syntavra\n",
    "product_version=0.0.1\n",
    "release_channel=pre-release\n",
    "contract_version=1\n",
    "engine=rust\n",
    "engine_stability=experimental\n",
    "capability=config.resolve|preview|read-only\n",
    "capability=engine.capabilities|preview|read-only\n",
    "capability=engine.contract-hash|preview|read-only\n",
    "capability=receipt.inspect|preview|read-only\n",
    "capability=state.broker-snapshot|preview|read-only\n",
    "capability=state.inspect|preview|read-only\n",
    "capability=state.layout|preview|read-only\n",
    "capability=status|preview|read-only\n",
    "capability=version|preview|read-only\n",
);

#[must_use]
pub fn capabilities_json() -> String {
    let rows = ENGINE_CAPABILITIES
        .iter()
        .map(|capability| {
            format!(
                concat!(
                    "{{\"name\":\"{}\",",
                    "\"maturity\":\"{}\",",
                    "\"mutation\":\"{}\"}}"
                ),
                capability.name, capability.maturity, capability.mutation
            )
        })
        .collect::<Vec<_>>()
        .join(",");

    format!(
        concat!(
            "{{\"product\":\"{}\",",
            "\"product_version\":\"{}\",",
            "\"release_channel\":\"{}\",",
            "\"engine\":\"{}\",",
            "\"engine_stability\":\"{}\",",
            "\"contract_version\":{},",
            "\"capabilities\":[{}]}}"
        ),
        PRODUCT_NAME,
        PRODUCT_VERSION,
        RELEASE_CHANNEL,
        ENGINE_NAME,
        ENGINE_STABILITY,
        CONTRACT_VERSION,
        rows
    )
}

#[cfg(test)]
mod tests {
    use super::{capabilities_json, CONTRACT_DESCRIPTOR, ENGINE_CAPABILITIES};

    #[test]
    fn descriptor_is_newline_terminated() {
        assert!(CONTRACT_DESCRIPTOR.ends_with('\n'));
    }

    #[test]
    fn capabilities_are_sorted() {
        let names = ENGINE_CAPABILITIES
            .iter()
            .map(|item| item.name)
            .collect::<Vec<_>>();
        let mut sorted = names.clone();
        sorted.sort_unstable();
        assert_eq!(names, sorted);
    }

    #[test]
    fn capabilities_json_is_deterministic() {
        assert_eq!(capabilities_json(), capabilities_json());
        assert!(capabilities_json().contains("\"contract_version\":1"));
        assert!(capabilities_json().contains("\"name\":\"config.resolve\""));
        assert!(capabilities_json().contains("\"name\":\"receipt.inspect\""));
        assert!(capabilities_json().contains("\"name\":\"state.broker-snapshot\""));
        assert!(capabilities_json().contains("\"name\":\"state.inspect\""));
        assert!(capabilities_json().contains("\"name\":\"state.layout\""));
        assert!(capabilities_json().contains("\"name\":\"status\""));
    }
}
