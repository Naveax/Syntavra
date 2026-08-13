include!("cc_checkpoint_rewrite.inc");
include!("cc_checkpoint_transcript.inc");

fn cc_checkpoint_supports(command: &[String]) -> bool {
    matches!(command, [root, action] if root == "run" && matches!(action.as_str(), "rewrite" | "transcript-mine"))
}

fn cc_checkpoint_execute(command: &[String], arguments: &[String]) -> Result<Option<Value>, String> {
    if !cc_checkpoint_supports(command) { return Ok(None); }
    match command[1].as_str() {
        "rewrite" => cc_rewrite_action(arguments).map(Some),
        "transcript-mine" => cc_transcript_mine(arguments).map(Some),
        _ => Ok(None),
    }
}
