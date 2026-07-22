from __future__ import annotations

import codecs
import json
import os
import re
from dataclasses import dataclass, field
from importlib import metadata as importlib_metadata
from pathlib import Path
from typing import Any, Iterable, Mapping, Protocol, runtime_checkable


@dataclass(frozen=True)
class LanguageDescriptor:
    """Declarative language identity independent of a parser implementation.

    A descriptor may come from the built-in registry, a repository manifest,
    a user manifest, or an installed Python entry point. Descriptors never
    imply exact semantic support on their own.
    """

    language_id: str
    suffixes: tuple[str, ...] = ()
    filenames: tuple[str, ...] = ()
    shebangs: tuple[str, ...] = ()
    aliases: tuple[str, ...] = ()
    capabilities: frozenset[str] = frozenset({"lexical"})
    source: str = "builtin"

    @classmethod
    def from_mapping(cls, value: Mapping[str, Any], *, source: str) -> "LanguageDescriptor":
        language_id = str(value.get("id") or value.get("language_id") or "").strip().casefold()
        if not language_id:
            raise ValueError("language descriptor requires a non-empty id")

        def values(name: str) -> tuple[str, ...]:
            raw = value.get(name, ())
            if isinstance(raw, str):
                raw = [raw]
            if not isinstance(raw, Iterable):
                raise ValueError(f"{name} must be a string or sequence")
            return tuple(str(item).strip() for item in raw if str(item).strip())

        capabilities = frozenset(item.casefold() for item in values("capabilities")) or frozenset({"lexical"})
        return cls(
            language_id=language_id,
            suffixes=tuple(item.casefold() if item.startswith(".") else f".{item.casefold()}" for item in values("suffixes")),
            filenames=tuple(item.casefold() for item in values("filenames")),
            shebangs=tuple(item.casefold() for item in values("shebangs")),
            aliases=tuple(item.casefold() for item in values("aliases")),
            capabilities=capabilities,
            source=source,
        )


@dataclass(frozen=True)
class LanguageDetection:
    language_id: str
    confidence: float
    evidence: str
    capability_level: str
    descriptor_source: str
    text_encoding: str | None
    binary: bool = False
    generated: bool = False
    minified: bool = False
    diagnostics: tuple[str, ...] = ()
    candidates: tuple[str, ...] = ()


@dataclass(frozen=True)
class LanguageParseResult:
    nodes: tuple[Mapping[str, Any], ...]
    edges: tuple[Mapping[str, Any], ...]
    capability_level: str
    evidence_source: str
    diagnostics: tuple[str, ...] = ()


@runtime_checkable
class LanguageAdapter(Protocol):
    language_ids: Iterable[str]
    capabilities: Iterable[str]

    def parse(self, *, path: str, text: str, evidence_ref: str) -> LanguageParseResult:
        ...


# This is intentionally broad, but it is not the universality mechanism.
# Universality comes from repository/user/plugin discovery plus the unknown-text
# fallback. New languages do not require a Syntavra release.
_BUILTIN_DESCRIPTORS: tuple[LanguageDescriptor, ...] = (
    LanguageDescriptor("python", (".py", ".pyi", ".pyw"), shebangs=("python",), capabilities=frozenset({"lexical", "syntax", "semantic"})),
    LanguageDescriptor("javascript", (".js", ".jsx", ".mjs", ".cjs"), shebangs=("node", "deno")),
    LanguageDescriptor("typescript", (".ts", ".tsx", ".mts", ".cts"), shebangs=("deno", "bun")),
    LanguageDescriptor("rust", (".rs",)),
    LanguageDescriptor("go", (".go",)),
    LanguageDescriptor("java", (".java",)),
    LanguageDescriptor("kotlin", (".kt", ".kts")),
    LanguageDescriptor("scala", (".scala", ".sc")),
    LanguageDescriptor("csharp", (".cs", ".csx")),
    LanguageDescriptor("fsharp", (".fs", ".fsi", ".fsx")),
    LanguageDescriptor("visual-basic", (".vb",)),
    LanguageDescriptor("c", (".c", ".h")),
    LanguageDescriptor("cpp", (".cc", ".cpp", ".cxx", ".h", ".hh", ".hpp", ".hxx", ".ixx", ".mpp")),
    LanguageDescriptor("objective-c", (".m", ".mm")),
    LanguageDescriptor("swift", (".swift",)),
    LanguageDescriptor("zig", (".zig",)),
    LanguageDescriptor("d", (".d", ".di")),
    LanguageDescriptor("dart", (".dart",)),
    LanguageDescriptor("ruby", (".rb", ".rake", ".gemspec"), filenames=("rakefile", "gemfile"), shebangs=("ruby",)),
    LanguageDescriptor("php", (".php", ".php3", ".php4", ".php5", ".phtml"), shebangs=("php",)),
    LanguageDescriptor("perl", (".pl", ".pm", ".t"), shebangs=("perl",)),
    LanguageDescriptor("raku", (".raku", ".rakumod", ".rakutest"), shebangs=("raku", "perl6")),
    LanguageDescriptor("lua", (".lua",), shebangs=("lua",)),
    LanguageDescriptor("luau", (".luau",)),
    LanguageDescriptor("r", (".r", ".rmd", ".qmd"), shebangs=("rscript",)),
    LanguageDescriptor("julia", (".jl",), shebangs=("julia",)),
    LanguageDescriptor("matlab", (".m", ".matlab"), filenames=("contents.m",)),
    LanguageDescriptor("octave", (".m", ".octave"), shebangs=("octave",)),
    LanguageDescriptor("haskell", (".hs", ".lhs")),
    LanguageDescriptor("elm", (".elm",)),
    LanguageDescriptor("purescript", (".purs",)),
    LanguageDescriptor("ocaml", (".ml", ".mli")),
    LanguageDescriptor("reason", (".re", ".rei")),
    LanguageDescriptor("erlang", (".erl", ".hrl", ".escript"), shebangs=("escript",)),
    LanguageDescriptor("elixir", (".ex", ".exs"), filenames=("mix.exs",), shebangs=("elixir",)),
    LanguageDescriptor("clojure", (".clj", ".cljs", ".cljc", ".edn")),
    LanguageDescriptor("common-lisp", (".lisp", ".lsp", ".cl")),
    LanguageDescriptor("scheme", (".scm", ".ss", ".sld")),
    LanguageDescriptor("racket", (".rkt", ".rktd", ".rktl")),
    LanguageDescriptor("solidity", (".sol",)),
    LanguageDescriptor("vyper", (".vy",)),
    LanguageDescriptor("move", (".move",)),
    LanguageDescriptor("cairo", (".cairo",)),
    LanguageDescriptor("shell", (".sh", ".bash", ".zsh", ".ksh"), filenames=("bashrc", "zshrc"), shebangs=("sh", "bash", "zsh", "ksh", "dash")),
    LanguageDescriptor("fish", (".fish",), shebangs=("fish",)),
    LanguageDescriptor("powershell", (".ps1", ".psm1", ".psd1"), shebangs=("pwsh", "powershell")),
    LanguageDescriptor("batch", (".bat", ".cmd")),
    LanguageDescriptor("nushell", (".nu",), shebangs=("nu",)),
    LanguageDescriptor("sql", (".sql", ".ddl", ".dml")),
    LanguageDescriptor("graphql", (".graphql", ".gql")),
    LanguageDescriptor("html", (".html", ".htm", ".xhtml")),
    LanguageDescriptor("css", (".css",)),
    LanguageDescriptor("scss", (".scss",)),
    LanguageDescriptor("sass", (".sass",)),
    LanguageDescriptor("less", (".less",)),
    LanguageDescriptor("vue", (".vue",)),
    LanguageDescriptor("svelte", (".svelte",)),
    LanguageDescriptor("astro", (".astro",)),
    LanguageDescriptor("webassembly-text", (".wat", ".wast")),
    LanguageDescriptor("assembly", (".asm", ".s", ".inc")),
    LanguageDescriptor("llvm-ir", (".ll",)),
    LanguageDescriptor("cuda", (".cu", ".cuh")),
    LanguageDescriptor("opencl", (".cl",)),
    LanguageDescriptor("verilog", (".v", ".vh")),
    LanguageDescriptor("systemverilog", (".sv", ".svh")),
    LanguageDescriptor("vhdl", (".vhd", ".vhdl")),
    LanguageDescriptor("tcl", (".tcl",), shebangs=("tclsh", "wish")),
    LanguageDescriptor("awk", (".awk",), shebangs=("awk", "gawk")),
    LanguageDescriptor("make", (".mk",), filenames=("makefile", "gnumakefile")),
    LanguageDescriptor("cmake", (".cmake",), filenames=("cmakelists.txt",)),
    LanguageDescriptor("ninja", (".ninja",)),
    LanguageDescriptor("meson", filenames=("meson.build", "meson_options.txt")),
    LanguageDescriptor("bazel", (".bzl",), filenames=("build", "build.bazel", "workspace", "workspace.bazel", "module.bazel")),
    LanguageDescriptor("dockerfile", filenames=("dockerfile",)),
    LanguageDescriptor("nix", (".nix",)),
    LanguageDescriptor("terraform", (".tf", ".tfvars")),
    LanguageDescriptor("hcl", (".hcl",)),
    LanguageDescriptor("cue", (".cue",)),
    LanguageDescriptor("rego", (".rego",)),
    LanguageDescriptor("protobuf", (".proto",)),
    LanguageDescriptor("thrift", (".thrift",)),
    LanguageDescriptor("capnp", (".capnp",)),
    LanguageDescriptor("flatbuffers", (".fbs",)),
    LanguageDescriptor("ada", (".adb", ".ads")),
    LanguageDescriptor("fortran", (".f", ".for", ".f77", ".f90", ".f95", ".f03", ".f08")),
    LanguageDescriptor("cobol", (".cob", ".cbl", ".cpy")),
    LanguageDescriptor("pascal", (".pas", ".pp", ".inc")),
    LanguageDescriptor("nim", (".nim", ".nims", ".nimble")),
    LanguageDescriptor("crystal", (".cr",), shebangs=("crystal",)),
    LanguageDescriptor("groovy", (".groovy", ".gradle"), shebangs=("groovy",)),
    LanguageDescriptor("smalltalk", (".st",)),
    LanguageDescriptor("prolog", (".pro", ".prolog", ".plg")),
    LanguageDescriptor("apex", (".cls", ".trigger")),
    LanguageDescriptor("gdscript", (".gd",)),
    LanguageDescriptor("renpy", (".rpy",)),
    LanguageDescriptor("qsharp", (".qs",)),
    LanguageDescriptor("lean", (".lean",)),
    LanguageDescriptor("coq", (".v",)),
    LanguageDescriptor("agda", (".agda", ".lagda")),
    LanguageDescriptor("idris", (".idr", ".lidr")),
    LanguageDescriptor("json", (".json", ".jsonc", ".json5")),
    LanguageDescriptor("yaml", (".yaml", ".yml")),
    LanguageDescriptor("toml", (".toml",)),
    LanguageDescriptor("xml", (".xml", ".xsd", ".xsl", ".xslt", ".svg")),
    LanguageDescriptor("ini", (".ini", ".cfg", ".conf", ".properties")),
    LanguageDescriptor("markdown", (".md", ".mdx", ".markdown")),
)

_MODEL_LANGUAGE_RE = re.compile(r"(?:^|\s)(?:ft|filetype|mode)\s*[=:]\s*([A-Za-z0-9_+.-]+)", re.IGNORECASE)
_SHEBANG_RE = re.compile(r"^#!\s*(?:/usr/bin/env\s+)?([^\s/]+)(?:\s|$)")
_GENERATED_RE = re.compile(r"(?i)(?:generated (?:file|code)|do not edit|auto[- ]generated|machine generated)")
_CONTENT_PROBES: dict[str, tuple[re.Pattern[str], ...]] = {
    "objective-c": (re.compile(r"(?m)^\s*#import\s+[<\"]"), re.compile(r"(?m)^\s*@(?:interface|implementation|protocol|end)\b")),
    "matlab": (re.compile(r"(?m)^\s*function\s+(?:\[[^]]+\]\s*=\s*)?[A-Za-z_]"), re.compile(r"(?m)^\s*%")),
    "octave": (re.compile(r"(?m)^\s*(?:pkg\s+load|endfunction|unwind_protect)\b"),),
    "opencl": (re.compile(r"\b__(?:kernel|global|local|constant)\b"), re.compile(r"\bget_global_id\s*\(")),
    "common-lisp": (re.compile(r"(?im)^\s*\((?:defun|defmacro|defclass|defpackage|in-package)\b"),),
    "verilog": (re.compile(r"(?im)^\s*(?:module|endmodule|always(?:_ff|_comb)?|wire|reg)\b"),),
    "coq": (re.compile(r"(?im)^\s*(?:Theorem|Lemma|Definition|Inductive|Fixpoint|Proof|Qed)\b"),),
    "assembly": (re.compile(r"(?im)^\s*(?:section|global|extern|mov|push|pop|jmp|call|ldr|str|addi)\b"),),
    "pascal": (re.compile(r"(?im)^\s*(?:program|unit|interface|implementation|procedure|function)\b"), re.compile(r"(?im)\bbegin\b.*\bend\.")),
    "c": (re.compile(r"(?m)^\s*#include\s+[<\"](?:stdio|stdlib|string|stdint)\.h"),),
    "cpp": (re.compile(r"(?m)^\s*#include\s+[<\"](?:iostream|vector|string|memory|algorithm)>"), re.compile(r"\b(?:namespace|template|constexpr|std::)\b")),
}


class LanguageRegistry:
    """Extensible language discovery with an always-available safe fallback."""

    def __init__(self, *, discover_entry_points: bool = True) -> None:
        self._descriptors: dict[str, LanguageDescriptor] = {}
        self._suffixes: dict[str, list[str]] = {}
        self._filenames: dict[str, list[str]] = {}
        self._shebangs: dict[str, list[str]] = {}
        self._adapters: dict[str, LanguageAdapter] = {}
        self.diagnostics: list[str] = []
        for descriptor in _BUILTIN_DESCRIPTORS:
            self.register_descriptor(descriptor)
        if discover_entry_points:
            self.discover_entry_points()

    def register_descriptor(self, descriptor: LanguageDescriptor) -> None:
        language_id = descriptor.language_id.casefold()
        self._descriptors[language_id] = descriptor

        def add(index: dict[str, list[str]], key: str) -> None:
            bucket = index.setdefault(key.casefold(), [])
            if language_id in bucket:
                bucket.remove(language_id)
            if descriptor.source.startswith("manifest:"):
                bucket.insert(0, language_id)
            else:
                bucket.append(language_id)

        for suffix in descriptor.suffixes:
            add(self._suffixes, suffix)
        for filename in descriptor.filenames:
            add(self._filenames, filename)
        for token in descriptor.shebangs:
            add(self._shebangs, token)
        for alias in descriptor.aliases:
            self._descriptors[alias.casefold()] = descriptor

    def register_adapter(self, adapter: LanguageAdapter) -> None:
        if not isinstance(adapter, LanguageAdapter):
            raise TypeError("language adapter does not satisfy the LanguageAdapter protocol")
        for language_id in adapter.language_ids:
            self._adapters[str(language_id).casefold()] = adapter

    def adapter_for(self, language_id: str) -> LanguageAdapter | None:
        return self._adapters.get(language_id.casefold())

    def discover_entry_points(self) -> None:
        if os.environ.get("SYNTAVRA_ALLOW_LANGUAGE_PLUGINS", "").casefold() not in {"1", "true", "yes"}:
            self.diagnostics.append("entry-point-discovery-disabled: explicit SYNTAVRA_ALLOW_LANGUAGE_PLUGINS authorization required")
            return
        try:
            points = importlib_metadata.entry_points()
            selected = points.select(group="syntavra.languages") if hasattr(points, "select") else points.get("syntavra.languages", ())
        except Exception as error:  # discovery must not break repository indexing
            self.diagnostics.append(f"entry-point-discovery: {type(error).__name__}: {error}")
            return
        for point in selected:
            try:
                value = point.load()
                value = value() if callable(value) and not isinstance(value, type) else value
                self._register_plugin_value(value, source=f"entry-point:{point.name}")
            except Exception as error:
                self.diagnostics.append(f"entry-point:{point.name}: {type(error).__name__}: {error}")

    def _register_plugin_value(self, value: Any, *, source: str) -> None:
        if isinstance(value, LanguageDescriptor):
            self.register_descriptor(value)
            return
        if isinstance(value, Mapping):
            if "languages" in value:
                for item in value["languages"]:
                    self.register_descriptor(LanguageDescriptor.from_mapping(item, source=source))
            else:
                self.register_descriptor(LanguageDescriptor.from_mapping(value, source=source))
            return
        if isinstance(value, LanguageAdapter):
            self.register_adapter(value)
            return
        if isinstance(value, Iterable) and not isinstance(value, (str, bytes)):
            for item in value:
                self._register_plugin_value(item, source=source)
            return
        raise TypeError(f"unsupported language plugin value: {type(value).__name__}")

    def discover_manifests(self, root: Path | None = None) -> None:
        paths: list[Path] = []
        if root is not None:
            paths.append(root / ".syntavra" / "languages")
        configured = os.environ.get("SYNTAVRA_LANGUAGE_PATH", "")
        paths.extend(Path(item).expanduser() for item in configured.split(os.pathsep) if item.strip())
        paths.append(Path.home() / ".syntavra" / "languages")

        seen: set[Path] = set()
        for directory in paths:
            try:
                directory = directory.resolve()
            except OSError:
                continue
            if directory in seen or not directory.is_dir():
                continue
            seen.add(directory)
            for manifest in sorted(directory.glob("*.json")):
                try:
                    payload = json.loads(manifest.read_text(encoding="utf-8"))
                    self._register_plugin_value(payload, source=f"manifest:{manifest}")
                except Exception as error:
                    self.diagnostics.append(f"manifest:{manifest}: {type(error).__name__}: {error}")

    @staticmethod
    def decode_text(data: bytes) -> tuple[str | None, str | None, bool]:
        if not data:
            return "", "utf-8", False
        for bom, encoding in (
            (codecs.BOM_UTF8, "utf-8-sig"),
            (codecs.BOM_UTF32_LE, "utf-32-le"),
            (codecs.BOM_UTF32_BE, "utf-32-be"),
            (codecs.BOM_UTF16_LE, "utf-16-le"),
            (codecs.BOM_UTF16_BE, "utf-16-be"),
        ):
            if data.startswith(bom):
                try:
                    return data.decode(encoding), encoding, False
                except UnicodeDecodeError:
                    break
        try:
            return data.decode("utf-8"), "utf-8", False
        except UnicodeDecodeError:
            pass
        sample = data[:8192]
        control = sum(byte < 9 or 13 < byte < 32 for byte in sample)
        if b"\x00" in sample or control / max(1, len(sample)) > 0.08:
            return None, None, True
        try:
            return data.decode("utf-8", errors="replace"), "utf-8-replace", False
        except Exception:
            return None, None, True

    @staticmethod
    def _multi_suffixes(path: Path) -> list[str]:
        suffixes = [suffix.casefold() for suffix in path.suffixes]
        return ["".join(suffixes[index:]) for index in range(len(suffixes))]

    def _candidate_descriptors(self, language_ids: Iterable[str]) -> list[LanguageDescriptor]:
        values: dict[str, LanguageDescriptor] = {}
        for language_id in language_ids:
            descriptor = self._descriptors.get(language_id.casefold())
            if descriptor is not None:
                values[descriptor.language_id] = descriptor
        return list(values.values())

    @staticmethod
    def _probe_score(language_id: str, text: str) -> int:
        return sum(1 for pattern in _CONTENT_PROBES.get(language_id, ()) if pattern.search(text[:256_000]))

    def _resolve_detection(
        self,
        language_ids: Iterable[str],
        *,
        evidence: str,
        encoding: str | None,
        text: str,
    ) -> LanguageDetection:
        descriptors = self._candidate_descriptors(language_ids)
        if not descriptors:
            raise ValueError("language candidate set is empty")
        if len(descriptors) == 1:
            return self._detection(descriptors[0], 1.0 if evidence == "filename" else 0.99, evidence, encoding, text)

        manifest_descriptors = [item for item in descriptors if item.source.startswith("manifest:")]
        if len(manifest_descriptors) == 1:
            return self._detection(manifest_descriptors[0], 0.995, f"{evidence}:manifest-override", encoding, text)

        scores = {item.language_id: self._probe_score(item.language_id, text) for item in descriptors}
        ranked = sorted(scores.items(), key=lambda item: (-item[1], item[0]))
        if ranked and ranked[0][1] > 0 and (len(ranked) == 1 or ranked[0][1] > ranked[1][1]):
            descriptor = next(item for item in descriptors if item.language_id == ranked[0][0])
            confidence = min(0.97, 0.72 + ranked[0][1] * 0.1)
            return self._detection(descriptor, confidence, f"{evidence}:content-probe", encoding, text)

        candidates = tuple(sorted(item.language_id for item in descriptors))
        return LanguageDetection(
            language_id="ambiguous:" + "|".join(candidates),
            confidence=0.4,
            evidence=f"{evidence}:ambiguous",
            capability_level="lexical",
            descriptor_source="ambiguous",
            text_encoding=encoding,
            generated=bool(_GENERATED_RE.search(text[:4096])),
            minified=self._is_minified(text),
            diagnostics=("Multiple languages share this identifier; exact semantic claims are disabled until stronger evidence or an adapter is available.",),
            candidates=candidates,
        )

    def detect(self, path: Path, data: bytes) -> LanguageDetection:
        text, encoding, binary = self.decode_text(data)
        if binary or text is None:
            return LanguageDetection("binary", 1.0, "binary-probe", "none", "builtin", None, binary=True)

        filename = path.name.casefold()
        candidates = self._filenames.get(filename, [])
        if candidates:
            return self._resolve_detection(candidates, evidence="filename", encoding=encoding, text=text)

        for suffix in self._multi_suffixes(path):
            candidates = self._suffixes.get(suffix, [])
            if candidates:
                return self._resolve_detection(candidates, evidence=f"suffix:{suffix}", encoding=encoding, text=text)

        first_line = text.splitlines()[0] if text.splitlines() else ""
        match = _SHEBANG_RE.match(first_line)
        if match:
            executable = match.group(1).casefold()
            for token, language_ids in self._shebangs.items():
                if token in executable:
                    descriptor = self._descriptors[language_ids[0]]
                    return self._detection(descriptor, 0.98, f"shebang:{executable}", encoding, text)

        modeline_window = "\n".join(text.splitlines()[:5] + text.splitlines()[-5:])
        match = _MODEL_LANGUAGE_RE.search(modeline_window)
        if match:
            hinted = match.group(1).casefold()
            descriptor = self._descriptors.get(hinted)
            if descriptor:
                return self._detection(descriptor, 0.9, f"modeline:{hinted}", encoding, text)

        suffix = path.suffix.casefold().lstrip(".")
        fallback_id = f"unknown:{suffix}" if suffix else "unknown:text"
        return LanguageDetection(
            language_id=fallback_id,
            confidence=0.35,
            evidence="text-fallback",
            capability_level="lexical",
            descriptor_source="fallback",
            text_encoding=encoding,
            generated=bool(_GENERATED_RE.search(text[:4096])),
            minified=self._is_minified(text),
            diagnostics=("No registered grammar or descriptor; exact semantic claims are disabled.",),
            candidates=(),
        )

    @staticmethod
    def _is_minified(text: str) -> bool:
        lines = text.splitlines()
        if not lines:
            return False
        longest = max(len(line) for line in lines)
        average = sum(len(line) for line in lines) / len(lines)
        return longest > 10_000 or (len(lines) < 8 and average > 1_500)

    @staticmethod
    def _detection(
        descriptor: LanguageDescriptor,
        confidence: float,
        evidence: str,
        encoding: str | None,
        text: str,
    ) -> LanguageDetection:
        capability = "semantic" if "semantic" in descriptor.capabilities else "syntax" if "syntax" in descriptor.capabilities else "lexical"
        return LanguageDetection(
            language_id=descriptor.language_id,
            confidence=confidence,
            evidence=evidence,
            capability_level=capability,
            descriptor_source=descriptor.source,
            text_encoding=encoding,
            generated=bool(_GENERATED_RE.search(text[:4096])),
            minified=LanguageRegistry._is_minified(text),
            candidates=(descriptor.language_id,),
        )

    def inventory(self) -> dict[str, Any]:
        unique = {descriptor.language_id: descriptor for descriptor in self._descriptors.values()}
        return {
            "registered_languages": len(unique),
            "languages": sorted(unique),
            "adapters": sorted(self._adapters),
            "diagnostics": list(self.diagnostics),
            "entry_point_plugins_authorized": os.environ.get("SYNTAVRA_ALLOW_LANGUAGE_PLUGINS", "").casefold() in {"1", "true", "yes"},
            "universal_text_fallback": True,
        }


__all__ = [
    "LanguageAdapter",
    "LanguageDescriptor",
    "LanguageDetection",
    "LanguageParseResult",
    "LanguageRegistry",
]
