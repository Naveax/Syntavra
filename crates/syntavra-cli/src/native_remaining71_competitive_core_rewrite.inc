#[derive(Clone, Copy)]
struct RewriteRegistration {
    name: &'static str,
    executable: &'static str,
    argument_pattern: &'static str,
    append: &'static [&'static str],
    replace: &'static [(&'static str, &'static str)],
    blocked: &'static [&'static str],
    reason: &'static str,
}

#[derive(Clone, Copy)]
enum CompactorStrategy { GitStatus, GitDiff, GitLog, Search, Table, Test, Lint, JsonOrTable, DockerBuild, HeadTail }

#[derive(Clone, Copy)]
struct CompactorRegistration {
    name: &'static str,
    executables: &'static [&'static str],
    argument_pattern: Option<&'static str>,
    strategy: CompactorStrategy,
}

include!("cc_rewrite_1.inc");
include!("cc_rewrite_2.inc");
include!("cc_rewrite_3.inc");
include!("cc_rewrite_4.inc");
include!("cc_rewrite_5.inc");
include!("cc_rewrite_6.inc");
include!("cc_rewrite_7.inc");
include!("cc_rewrite_8.inc");

const REWRITE_RULE_GROUPS: &[&[RewriteRegistration]] = &[
    REWRITE_RULES_1,
    REWRITE_RULES_2,
    REWRITE_RULES_3,
    REWRITE_RULES_4,
    REWRITE_RULES_5,
    REWRITE_RULES_6,
    REWRITE_RULES_7,
    REWRITE_RULES_8
];

const REWRITE_RULE_COUNT: usize = REWRITE_RULES_1.len() + REWRITE_RULES_2.len() + REWRITE_RULES_3.len() + REWRITE_RULES_4.len() + REWRITE_RULES_5.len() + REWRITE_RULES_6.len() + REWRITE_RULES_7.len() + REWRITE_RULES_8.len();
