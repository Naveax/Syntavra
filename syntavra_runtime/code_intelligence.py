from __future__ import annotations

import ast
import collections
import hashlib
import json
import math
import re
import subprocess
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence

from .util import canonical_json, sha256_bytes


_CODE_SUFFIXES = {".py", ".js", ".jsx", ".ts", ".tsx", ".rs", ".go", ".java", ".cs", ".rb", ".php", ".lua", ".luau", ".c", ".h", ".cpp", ".hpp"}
_TEST_MARKERS = ("test", "tests", "spec", "__tests__")


@dataclass
class SymbolNode:
    id: str
    name: str
    qualified_name: str
    kind: str
    path: str
    line: int
    end_line: int
    language: str
    bases: tuple[str, ...] = ()
    calls: tuple[str, ...] = ()
    imports: tuple[str, ...] = ()
    body_hash: str = ""
    complexity: int = 1
    exported: bool = False


@dataclass(frozen=True)
class GraphEdge:
    source: str
    target: str
    kind: str


@dataclass
class CodeGraph:
    symbols: dict[str, SymbolNode] = field(default_factory=dict)
    edges: list[GraphEdge] = field(default_factory=list)
    files: dict[str, dict[str, Any]] = field(default_factory=dict)

    def adjacency(self, *, kinds: set[str] | None = None) -> dict[str, set[str]]:
        graph: dict[str, set[str]] = {key: set() for key in self.symbols}
        for edge in self.edges:
            if kinds is None or edge.kind in kinds:
                graph.setdefault(edge.source, set()).add(edge.target)
        return graph

    def reverse(self, *, kinds: set[str] | None = None) -> dict[str, set[str]]:
        graph: dict[str, set[str]] = {key: set() for key in self.symbols}
        for edge in self.edges:
            if kinds is None or edge.kind in kinds:
                graph.setdefault(edge.target, set()).add(edge.source)
        return graph


class _PythonVisitor(ast.NodeVisitor):
    def __init__(self, path: str, source: str):
        self.path = path
        self.source_lines = source.splitlines()
        self.scope: list[str] = []
        self.symbols: list[SymbolNode] = []
        self.current: SymbolNode | None = None
        self.imports: set[str] = set()

    @staticmethod
    def _name(node: ast.AST) -> str:
        if isinstance(node, ast.Name): return node.id
        if isinstance(node, ast.Attribute): return f"{_PythonVisitor._name(node.value)}.{node.attr}".strip(".")
        if isinstance(node, ast.Subscript): return _PythonVisitor._name(node.value)
        return ""

    def visit_Import(self, node: ast.Import) -> None:
        self.imports.update(alias.name for alias in node.names)

    def visit_ImportFrom(self, node: ast.ImportFrom) -> None:
        module = node.module or ""
        self.imports.update(f"{module}.{alias.name}".strip(".") for alias in node.names)

    def _add(self, node: ast.AST, name: str, kind: str, bases: Sequence[str] = ()) -> SymbolNode:
        qualified = ".".join([*self.scope, name])
        start = int(getattr(node, "lineno", 1))
        end = int(getattr(node, "end_lineno", start))
        body = "\n".join(self.source_lines[start - 1:end])
        complexity = 1 + sum(isinstance(child, (ast.If, ast.For, ast.AsyncFor, ast.While, ast.Try, ast.BoolOp, ast.Match, ast.comprehension)) for child in ast.walk(node))
        item = SymbolNode(
            id=f"{self.path}:{qualified}:{start}", name=name, qualified_name=qualified,
            kind=kind, path=self.path, line=start, end_line=end, language="python",
            bases=tuple(base for base in bases if base), imports=tuple(sorted(self.imports)),
            body_hash=sha256_bytes(re.sub(r"\s+", " ", body).strip().encode("utf-8")),
            complexity=complexity, exported=not name.startswith("_"),
        )
        self.symbols.append(item)
        return item

    def _visit_callable(self, node: ast.AST, name: str, kind: str) -> None:
        item = self._add(node, name, kind)
        previous = self.current
        self.current = item
        self.scope.append(name)
        calls: set[str] = set()
        for child in ast.walk(node):
            if isinstance(child, ast.Call):
                value = self._name(child.func)
                if value: calls.add(value)
        item.calls = tuple(sorted(calls))
        self.generic_visit(node)
        self.scope.pop()
        self.current = previous

    def visit_FunctionDef(self, node: ast.FunctionDef) -> None: self._visit_callable(node, node.name, "function" if not self.scope else "method")
    def visit_AsyncFunctionDef(self, node: ast.AsyncFunctionDef) -> None: self._visit_callable(node, node.name, "async-function" if not self.scope else "async-method")

    def visit_ClassDef(self, node: ast.ClassDef) -> None:
        item = self._add(node, node.name, "class", [self._name(base) for base in node.bases])
        previous = self.current
        self.current = item
        self.scope.append(node.name)
        self.generic_visit(node)
        self.scope.pop()
        self.current = previous


_GENERIC_DECL = re.compile(
    r"(?m)^\s*(?:export\s+|pub\s+|public\s+|private\s+|protected\s+|static\s+|async\s+)*"
    r"(?:(class|interface|trait|struct|enum|function|func|fn|def|module)\s+)?"
    r"([A-Za-z_$][A-Za-z0-9_$]*)\s*(?:<[^>]+>)?\s*(?:\([^;{}]*\)|\{|extends\s+[A-Za-z0-9_$.]+)"
)
_GENERIC_CALL = re.compile(r"\b([A-Za-z_$][A-Za-z0-9_$.]*)\s*\(")
_GENERIC_IMPORT = re.compile(r"(?m)^\s*(?:import|from|use|require\s*\()\s*[\"']?([A-Za-z0-9_./:@-]+)")
_CLASS_EXTENDS = re.compile(r"\b(?:extends|implements|:)\s+([A-Za-z_$][A-Za-z0-9_$., <>]*)")


class CodeIntelligenceIndex:
    def __init__(self, project: Path):
        self.project = Path(project).resolve(strict=True)
        self.graph = CodeGraph()

    @staticmethod
    def _language(path: Path) -> str:
        return {".js": "javascript", ".jsx": "javascript", ".ts": "typescript", ".tsx": "typescript", ".rs": "rust", ".go": "go", ".java": "java", ".cs": "csharp", ".rb": "ruby", ".php": "php", ".lua": "lua", ".luau": "luau", ".c": "c", ".h": "c", ".cpp": "cpp", ".hpp": "cpp"}.get(path.suffix.casefold(), "unknown")

    def _files(self) -> Iterable[Path]:
        for path in self.project.rglob("*"):
            if path.is_file() and path.suffix.casefold() in _CODE_SUFFIXES and not any(part in {".git", ".syntavra", "node_modules", "dist", "build", ".venv", "venv"} for part in path.parts):
                yield path

    def _generic_symbols(self, relative: str, source: str, language: str) -> list[SymbolNode]:
        lines = source.splitlines()
        imports = tuple(sorted(set(_GENERIC_IMPORT.findall(source))))
        rows: list[SymbolNode] = []
        for match in _GENERIC_DECL.finditer(source):
            kind = match.group(1) or "callable"
            name = match.group(2)
            line = source.count("\n", 0, match.start()) + 1
            end = min(len(lines), line + 80)
            snippet = "\n".join(lines[line - 1:end])
            call_names = tuple(sorted(set(value for value in _GENERIC_CALL.findall(snippet) if value != name)))
            bases_match = _CLASS_EXTENDS.search(match.group(0))
            bases = tuple(part.strip().split("<", 1)[0] for part in bases_match.group(1).split(",")) if bases_match else ()
            rows.append(SymbolNode(
                id=f"{relative}:{name}:{line}", name=name, qualified_name=name, kind=kind,
                path=relative, line=line, end_line=end, language=language, bases=bases,
                calls=call_names, imports=imports,
                body_hash=sha256_bytes(re.sub(r"\s+", " ", snippet).strip().encode("utf-8")),
                complexity=1 + len(re.findall(r"\b(?:if|for|while|switch|match|catch|except|&&|\|\|)\b", snippet)),
                exported=bool(re.search(r"\b(?:export|pub|public)\b", match.group(0))) or not name.startswith("_"),
            ))
        return rows

    def build(self) -> CodeGraph:
        graph = CodeGraph()
        for path in self._files():
            relative = path.relative_to(self.project).as_posix()
            source = path.read_text(encoding="utf-8", errors="replace")
            language = "python" if path.suffix.casefold() == ".py" else self._language(path)
            try:
                if language == "python":
                    visitor = _PythonVisitor(relative, source)
                    visitor.visit(ast.parse(source, filename=relative))
                    symbols = visitor.symbols
                else:
                    symbols = self._generic_symbols(relative, source, language)
            except (SyntaxError, ValueError):
                symbols = self._generic_symbols(relative, source, language)
            graph.files[relative] = {"language": language, "bytes": len(source.encode("utf-8")), "symbols": [item.id for item in symbols]}
            for item in symbols:
                graph.symbols[item.id] = item
        by_name: dict[str, list[str]] = collections.defaultdict(list)
        for item in graph.symbols.values():
            by_name[item.name].append(item.id)
            by_name[item.qualified_name].append(item.id)
        module_symbols: dict[str, list[str]] = collections.defaultdict(list)
        for target in graph.symbols.values():
            stem = target.path.removesuffix(Path(target.path).suffix)
            module_symbols[stem].append(target.id)
        module_names = tuple(module_symbols)
        for item in graph.symbols.values():
            for call in item.calls:
                short = call.rsplit(".", 1)[-1]
                for target in by_name.get(call, by_name.get(short, ())):
                    if target != item.id:
                        graph.edges.append(GraphEdge(item.id, target, "call"))
            for base in item.bases:
                short = base.rsplit(".", 1)[-1]
                for target in by_name.get(base, by_name.get(short, ())):
                    if target != item.id:
                        graph.edges.append(GraphEdge(item.id, target, "inherits"))
            for imported in item.imports:
                imported_path = imported.replace(".", "/")
                for module in module_names:
                    if module.endswith(imported_path):
                        for target_id in module_symbols[module]:
                            if target_id != item.id:
                                graph.edges.append(GraphEdge(item.id, target_id, "import"))
        self.graph = graph
        return graph

    def _ensure(self) -> CodeGraph:
        return self.graph if self.graph.symbols else self.build()

    def resolve(self, query: str) -> list[SymbolNode]:
        graph = self._ensure()
        q = query.casefold()
        return sorted((item for item in graph.symbols.values() if q in item.name.casefold() or q in item.qualified_name.casefold() or q in item.id.casefold()), key=lambda item: (item.name.casefold() != q, item.path, item.line))

    def call_hierarchy(self, query: str, *, depth: int = 3) -> dict[str, Any]:
        graph = self._ensure(); targets = self.resolve(query)
        adjacency = graph.adjacency(kinds={"call"}); reverse = graph.reverse(kinds={"call"})
        def expand(seed: str, edges: Mapping[str, set[str]]) -> list[dict[str, Any]]:
            result=[]; frontier=[(seed,0)]; seen={seed}
            while frontier:
                node,d=frontier.pop(0)
                if d>=depth: continue
                for nxt in sorted(edges.get(node,())):
                    if nxt in seen: continue
                    seen.add(nxt); item=graph.symbols[nxt]
                    result.append({"depth":d+1,"id":nxt,"name":item.qualified_name,"path":item.path,"line":item.line})
                    frontier.append((nxt,d+1))
            return result
        return {"query":query,"matches":[{"symbol":asdict(item),"callers":expand(item.id,reverse),"callees":expand(item.id,adjacency)} for item in targets[:20]]}

    def class_hierarchy(self, query: str, *, depth: int = 5) -> dict[str, Any]:
        graph=self._ensure(); adjacency=graph.adjacency(kinds={"inherits"}); reverse=graph.reverse(kinds={"inherits"})
        return {"query":query,"matches":[{"symbol":asdict(item),"bases":[asdict(graph.symbols[x]) for x in sorted(adjacency.get(item.id,()))],"subclasses":[asdict(graph.symbols[x]) for x in sorted(reverse.get(item.id,()))]} for item in self.resolve(query) if item.kind in {"class","interface","trait","struct"}]}

    def pagerank(self, *, iterations: int = 30, damping: float = 0.85) -> dict[str, float]:
        graph=self._ensure(); adjacency=graph.adjacency(kinds={"call","import","inherits"}); nodes=sorted(graph.symbols)
        if not nodes: return {}
        ranks={node:1/len(nodes) for node in nodes}
        reverse=graph.reverse(kinds={"call","import","inherits"})
        for _ in range(iterations):
            next_ranks={node:(1-damping)/len(nodes) for node in nodes}
            sink=sum(ranks[node] for node in nodes if not adjacency.get(node))
            for node in nodes:
                next_ranks[node]+=damping*sink/len(nodes)
                for source in reverse.get(node,()):
                    next_ranks[node]+=damping*ranks[source]/max(1,len(adjacency[source]))
            ranks=next_ranks
        return dict(sorted(ranks.items(), key=lambda item:(-item[1],item[0])))

    def dead_code(self) -> list[dict[str, Any]]:
        graph=self._ensure(); inbound=graph.reverse(kinds={"call","import","inherits"}); rows=[]
        for item in graph.symbols.values():
            is_test=any(marker in Path(item.path).parts or marker in Path(item.path).name.casefold() for marker in _TEST_MARKERS)
            if not inbound.get(item.id) and not item.exported and not is_test and item.name not in {"main","__init__"}:
                rows.append({"symbol":asdict(item),"reason":"private symbol has no inbound code-graph edges"})
        return sorted(rows,key=lambda row:(row["symbol"]["path"],row["symbol"]["line"]))

    def untested_symbols(self) -> list[dict[str, Any]]:
        graph=self._ensure(); test_text="\n".join((self.project/path).read_text(encoding="utf-8",errors="replace") for path in graph.files if any(marker in Path(path).parts or marker in Path(path).name.casefold() for marker in _TEST_MARKERS))
        return [{"symbol":asdict(item),"reason":"symbol name not referenced by discovered tests"} for item in graph.symbols.values() if item.exported and item.kind not in {"class"} and item.name not in test_text and not any(marker in Path(item.path).parts or marker in Path(item.path).name.casefold() for marker in _TEST_MARKERS)]

    def _git(self, *args: str) -> str:
        try:
            result=subprocess.run(["git",*args],cwd=self.project,stdout=subprocess.PIPE,stderr=subprocess.DEVNULL,text=True,timeout=30,check=False)
            return result.stdout
        except (OSError,subprocess.SubprocessError): return ""

    def churn(self) -> dict[str, dict[str, int]]:
        output=self._git("log","--max-count=500","--numstat","--format=")
        rows: dict[str,dict[str,int]]={}
        for line in output.splitlines():
            parts=line.split("\t")
            if len(parts)!=3 or not parts[0].isdigit() or not parts[1].isdigit(): continue
            item=rows.setdefault(parts[2],{"added":0,"deleted":0,"changes":0})
            item["added"]+=int(parts[0]); item["deleted"]+=int(parts[1]); item["changes"]+=1
        return rows

    def hotspots(self) -> list[dict[str, Any]]:
        graph=self._ensure(); churn=self.churn(); ranks=self.pagerank(); rows=[]
        for item in graph.symbols.values():
            file_churn=churn.get(item.path,{"added":0,"deleted":0,"changes":0})
            score=item.complexity*math.log2(2+file_churn["added"]+file_churn["deleted"])*(1+ranks.get(item.id,0)*100)
            rows.append({"symbol":asdict(item),"churn":file_churn,"pagerank":ranks.get(item.id,0),"hotspot_score":score})
        return sorted(rows,key=lambda row:(-row["hotspot_score"],row["symbol"]["id"]))

    def cycles(self) -> list[list[str]]:
        graph=self._ensure(); adjacency=graph.adjacency(kinds={"call","import","inherits"})
        index=0; stack=[]; indices={}; low={}; on=set(); result=[]
        def visit(node: str) -> None:
            nonlocal index
            indices[node]=low[node]=index; index+=1; stack.append(node); on.add(node)
            for nxt in adjacency.get(node,()):
                if nxt not in indices: visit(nxt); low[node]=min(low[node],low[nxt])
                elif nxt in on: low[node]=min(low[node],indices[nxt])
            if low[node]==indices[node]:
                component=[]
                while True:
                    item=stack.pop(); on.remove(item); component.append(item)
                    if item==node: break
                if len(component)>1 or node in adjacency.get(node,()): result.append(sorted(component))
        for node in sorted(adjacency):
            if node not in indices: visit(node)
        return sorted(result,key=lambda row:(-len(row),row))

    def coupling(self) -> list[dict[str, Any]]:
        graph=self._ensure(); outgoing=graph.adjacency(kinds={"call","import","inherits"}); incoming=graph.reverse(kinds={"call","import","inherits"}); rows=[]
        for node,item in graph.symbols.items():
            ca=len(incoming.get(node,())); ce=len(outgoing.get(node,())); total=ca+ce
            rows.append({"symbol":asdict(item),"afferent":ca,"efferent":ce,"instability":ce/total if total else 0.0,"coupling":total})
        return sorted(rows,key=lambda row:(-row["coupling"],row["symbol"]["id"]))

    def module_boundaries(self) -> dict[str, Any]:
        graph=self._ensure(); modules: dict[str,dict[str,Any]]={}
        for item in graph.symbols.values():
            parts=Path(item.path).parts; module="/".join(parts[:2]) if len(parts)>1 else parts[0]
            row=modules.setdefault(module,{"module":module,"symbols":0,"files":set(),"outbound":set(),"inbound":set()})
            row["symbols"]+=1; row["files"].add(item.path)
        symbol_module={item.id:("/".join(Path(item.path).parts[:2]) if len(Path(item.path).parts)>1 else Path(item.path).parts[0]) for item in graph.symbols.values()}
        for edge in graph.edges:
            left=symbol_module.get(edge.source); right=symbol_module.get(edge.target)
            if left and right and left!=right: modules[left]["outbound"].add(right); modules[right]["inbound"].add(left)
        rendered=[]
        for row in modules.values():
            rendered.append({"module":row["module"],"symbols":row["symbols"],"files":sorted(row["files"]),"outbound":sorted(row["outbound"]),"inbound":sorted(row["inbound"]),"boundary_strength":1/(1+len(row["outbound"])+len(row["inbound"]))})
        return {"modules":sorted(rendered,key=lambda row:row["module"]),"method":"directory-plus-cross-edge tectonic map"}

    def signal_chain(self, query: str, *, depth: int = 6) -> dict[str, Any]:
        hierarchy=self.call_hierarchy(query,depth=depth)
        return {"query":query,"chains":[{"entry":match["symbol"],"downstream":match["callees"]} for match in hierarchy["matches"]]}

    def duplicates(self) -> list[dict[str, Any]]:
        graph=self._ensure(); groups: dict[str,list[SymbolNode]]=collections.defaultdict(list)
        for item in graph.symbols.values():
            if item.end_line-item.line>=2: groups[item.body_hash].append(item)
        return [{"body_hash":key,"symbols":[asdict(item) for item in rows]} for key,rows in groups.items() if len(rows)>1]

    def provenance(self, query: str, *, limit: int = 20) -> dict[str, Any]:
        matches=self.resolve(query); rows=[]
        for item in matches[:10]:
            output=self._git("log",f"-n{limit}","--format=%H%x09%ad%x09%an%x09%s","--date=iso-strict","--",item.path)
            commits=[]
            for line in output.splitlines():
                parts=line.split("\t",3)
                if len(parts)==4: commits.append({"commit":parts[0],"date":parts[1],"author":parts[2],"subject":parts[3]})
            rows.append({"symbol":asdict(item),"commits":commits})
        return {"query":query,"matches":rows}

    def pr_risk(self, changed_paths: Sequence[str]) -> dict[str, Any]:
        graph=self._ensure(); ranks=self.pagerank(); hotspot={row["symbol"]["id"]:row["hotspot_score"] for row in self.hotspots()}; rows=[]
        for path in changed_paths:
            symbols=[item for item in graph.symbols.values() if item.path==path]
            score=sum(ranks.get(item.id,0)*100+math.log2(1+hotspot.get(item.id,0)) for item in symbols)
            rows.append({"path":path,"symbols":len(symbols),"risk_score":score,"central_symbols":[item.qualified_name for item in sorted(symbols,key=lambda item:-ranks.get(item.id,0))[:5]]})
        total=sum(row["risk_score"] for row in rows)
        return {"changed_paths":list(changed_paths),"risk_score":total,"risk":"high" if total>=40 else "medium" if total>=15 else "low","files":sorted(rows,key=lambda row:-row["risk_score"])}

    def delete_safe(self, query: str) -> dict[str, Any]:
        graph=self._ensure(); reverse=graph.reverse(kinds={"call","import","inherits"}); matches=self.resolve(query); rows=[]
        for item in matches:
            inbound=[asdict(graph.symbols[node]) for node in sorted(reverse.get(item.id,()))]
            rows.append({"symbol":asdict(item),"safe":not inbound and not item.exported,"inbound":inbound,"required_actions":[] if not inbound else ["update or remove inbound references","run affected tests"]})
        return {"query":query,"results":rows,"safe":bool(rows) and all(row["safe"] for row in rows)}

    def refactor_plan(self, query: str, *, target_name: str = "") -> dict[str, Any]:
        graph=self._ensure(); hierarchy=self.call_hierarchy(query,depth=4); matches=self.resolve(query); impacted=set()
        for match in hierarchy["matches"]:
            impacted.update(row["path"] for row in [*match["callers"],*match["callees"]])
            impacted.add(match["symbol"]["path"])
        steps=["freeze current behavior with focused tests","update symbol definition and public contract","update callers and inheritance edges","run targeted tests and static analysis","run repository-wide verification","verify no stale agent-config references"]
        return {"query":query,"target_name":target_name or query,"symbols":[asdict(item) for item in matches],"impacted_paths":sorted(impacted),"steps":steps,"delete_preflight":self.delete_safe(query)}

    def anti_patterns(self) -> list[dict[str, Any]]:
        patterns=(
            ("hardcoded-secret",re.compile(r"(?i)(?:api[_-]?key|password|secret)\s*[:=]\s*[\"'][^\"']{8,}"),"critical"),
            ("shell-true",re.compile(r"subprocess\.(?:run|Popen).*shell\s*=\s*True|exec\s*\(|eval\s*\("),"high"),
            ("empty-catch",re.compile(r"(?s)except\s*(?:Exception)?\s*:\s*(?:pass|continue)\b|catch\s*\([^)]*\)\s*\{\s*\}"),"medium"),
            ("todo-security",re.compile(r"(?i)TODO.*(?:auth|security|secret|permission)"),"medium"),
            ("unbounded-retry",re.compile(r"while\s+True\s*:|for\s*\(\s*;;\s*\)"),"medium"),
        )
        rows=[]
        for path in self._files():
            relative=path.relative_to(self.project).as_posix(); text=path.read_text(encoding="utf-8",errors="replace")
            for name,pattern,severity in patterns:
                for match in pattern.finditer(text):
                    rows.append({"kind":name,"severity":severity,"path":relative,"line":text.count("\n",0,match.start())+1,"snippet":match.group(0)[:240]})
        return rows

    def cross_repo_contracts(self, other_projects: Sequence[Path]) -> dict[str, Any]:
        own=self._ensure(); own_exports={item.name:asdict(item) for item in own.symbols.values() if item.exported}
        rows=[]
        for path in other_projects:
            other=CodeIntelligenceIndex(path); graph=other.build(); imports={name for item in graph.symbols.values() for name in item.imports}; shared=sorted(set(own_exports)&{value.rsplit(".",1)[-1] for value in imports})
            rows.append({"repository":str(Path(path).resolve()),"shared_contracts":[own_exports[name] for name in shared]})
        body={"source_repository":str(self.project),"repositories":rows}; body["contract_hash"]=sha256_bytes(canonical_json(body)); return body

    def report(self) -> dict[str, Any]:
        graph=self._ensure(); ranks=self.pagerank()
        return {"files":len(graph.files),"symbols":len(graph.symbols),"edges":len(graph.edges),"top_symbols":[{"symbol":asdict(graph.symbols[key]),"pagerank":value} for key,value in list(ranks.items())[:30]],"cycles":self.cycles(),"dead_code":self.dead_code(),"untested":self.untested_symbols(),"hotspots":self.hotspots()[:50],"coupling":self.coupling()[:50],"module_boundaries":self.module_boundaries(),"duplicates":self.duplicates(),"anti_patterns":self.anti_patterns()}
