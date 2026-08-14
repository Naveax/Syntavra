#![forbid(unsafe_code)]

use std::sync::OnceLock;

use tree_sitter::{Language, Parser};

/// Native Rust implementation of Syntavra's public
/// `tree-sitter-language-pack` adapter contract. The implementation
/// aggregates Rust grammar crates and never executes Python at runtime.
pub(super) const ADAPTER_LANGUAGES: &[&str] = &[
    "bash",
    "c",
    "c_sharp",
    "cpp",
    "csharp",
    "dart",
    "elixir",
    "erlang",
    "fish",
    "fsharp",
    "go",
    "haskell",
    "java",
    "javascript",
    "julia",
    "kotlin",
    "lua",
    "ocaml",
    "php",
    "powershell",
    "r",
    "ruby",
    "rust",
    "scala",
    "solidity",
    "svelte",
    "swift",
    "typescript",
    "vue",
    "zig",
];

static AVAILABLE_LANGUAGES: OnceLock<Vec<&'static str>> = OnceLock::new();

fn parser_ready(language: Language) -> bool {
    let mut parser = Parser::new();
    parser.set_language(&language).is_ok() && parser.parse("\n", None).is_some()
}

fn probe_languages() -> Vec<&'static str> {
    let mut available = Vec::with_capacity(ADAPTER_LANGUAGES.len());
    if parser_ready(tree_sitter_bash::LANGUAGE.into()) {
        available.push("bash");
    }
    if parser_ready(tree_sitter_c::LANGUAGE.into()) {
        available.push("c");
    }
    let csharp_ready = parser_ready(tree_sitter_c_sharp::LANGUAGE.into());
    if csharp_ready {
        available.push("c_sharp");
    }
    if parser_ready(tree_sitter_cpp::LANGUAGE.into()) {
        available.push("cpp");
    }
    if csharp_ready {
        available.push("csharp");
    }
    if parser_ready(tree_sitter_dart_orchard::LANGUAGE.into()) {
        available.push("dart");
    }
    if parser_ready(tree_sitter_elixir::LANGUAGE.into()) {
        available.push("elixir");
    }
    if parser_ready(tree_sitter_erlang::LANGUAGE.into()) {
        available.push("erlang");
    }
    if parser_ready(tree_sitter_fish::language()) {
        available.push("fish");
    }
    if parser_ready(tree_sitter_fsharp::LANGUAGE_FSHARP.into()) {
        available.push("fsharp");
    }
    if parser_ready(tree_sitter_go::LANGUAGE.into()) {
        available.push("go");
    }
    if parser_ready(tree_sitter_haskell::LANGUAGE.into()) {
        available.push("haskell");
    }
    if parser_ready(tree_sitter_java::LANGUAGE.into()) {
        available.push("java");
    }
    if parser_ready(tree_sitter_javascript::LANGUAGE.into()) {
        available.push("javascript");
    }
    if parser_ready(tree_sitter_julia::LANGUAGE.into()) {
        available.push("julia");
    }
    if parser_ready(tree_sitter_kotlin_ng::LANGUAGE.into()) {
        available.push("kotlin");
    }
    if parser_ready(tree_sitter_lua::LANGUAGE.into()) {
        available.push("lua");
    }
    if parser_ready(tree_sitter_ocaml::LANGUAGE_OCAML.into()) {
        available.push("ocaml");
    }
    if parser_ready(tree_sitter_php::LANGUAGE_PHP.into()) {
        available.push("php");
    }
    if parser_ready(tree_sitter_powershell::LANGUAGE.into()) {
        available.push("powershell");
    }
    if parser_ready(tree_sitter_r::LANGUAGE.into()) {
        available.push("r");
    }
    if parser_ready(tree_sitter_ruby::LANGUAGE.into()) {
        available.push("ruby");
    }
    if parser_ready(tree_sitter_rust::LANGUAGE.into()) {
        available.push("rust");
    }
    if parser_ready(tree_sitter_scala::LANGUAGE.into()) {
        available.push("scala");
    }
    if parser_ready(tree_sitter_solidity::LANGUAGE.into()) {
        available.push("solidity");
    }
    if parser_ready(tree_sitter_svelte_ng::LANGUAGE.into()) {
        available.push("svelte");
    }
    if parser_ready(tree_sitter_swift::LANGUAGE.into()) {
        available.push("swift");
    }
    if parser_ready(tree_sitter_typescript::LANGUAGE_TYPESCRIPT.into()) {
        available.push("typescript");
    }
    if parser_ready(tree_sitter_vue_next::LANGUAGE.into()) {
        available.push("vue");
    }
    if parser_ready(tree_sitter_zig::LANGUAGE.into()) {
        available.push("zig");
    }
    available
}

pub(super) fn available_languages() -> &'static [&'static str] {
    AVAILABLE_LANGUAGES.get_or_init(probe_languages).as_slice()
}

pub(super) fn installed() -> bool {
    available_languages() == ADAPTER_LANGUAGES
}

pub(super) fn manifest_languages(include_c_sharp_alias: bool) -> Vec<&'static str> {
    available_languages()
        .iter()
        .copied()
        .filter(|language| include_c_sharp_alias || *language != "c_sharp")
        .collect()
}
