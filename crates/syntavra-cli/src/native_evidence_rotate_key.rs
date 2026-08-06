#![forbid(unsafe_code)]

use std::path::Path;

use serde_json::Value;

use super::native_evidence_store::NativeEvidenceStore;

pub fn supports(command: &[String]) -> bool {
    matches!(command, [root, action] if root == "evidence" && action == "rotate-key")
}

pub fn execute(project_root: &Path, state_root: &Path) -> Result<Value, String> {
    let project_id =
        super::state_snapshot_contract::project_id_for_root(&project_root.to_string_lossy())?;
    let mut evidence = NativeEvidenceStore::open(state_root, &project_id)?;
    evidence.rotate_key(true)
}

#[cfg(test)]
mod tests {
    use super::supports;

    #[test]
    fn routes_evidence_rotate_key_only() {
        assert!(supports(&[
            "evidence".to_owned(),
            "rotate-key".to_owned()
        ]));
        assert!(!supports(&[
            "evidence".to_owned(),
            "get".to_owned()
        ]));
        assert!(!supports(&[
            "evidence".to_owned(),
            "rotate-key".to_owned(),
            "extra".to_owned()
        ]));
    }
}
