from __future__ import annotations

import ast
import re
import time
from pathlib import Path
from typing import Any

from .state import StateDB
from .util import sha256_file

SOURCE_SUFFIXES={".py",".js",".jsx",".ts",".tsx",".rs",".go",".java",".cs",".c",".h",".cpp",".hpp",".rb",".php"}
IGNORE_PARTS={".git",".signalcore","node_modules",".venv","venv","dist","build","target","__pycache__"}
GENERIC_DEF=re.compile(r"(?m)^\s*(?:def|class|fn|function|func|interface|trait|struct|enum)\s+([A-Za-z_$][\w$]*)")
GENERIC_CALL=re.compile(r"\b([A-Za-z_$][\w$]*)\s*\(")
GENERIC_IMPORT=re.compile(r"(?m)^\s*(?:from\s+([\w.]+)\s+import|import\s+([\w.]+)|use\s+([\w:]+)|require\(['\"]([^'\"]+))")

class StructuralIndex:
    def __init__(self,path:Path,*,repository_root:Path,repository_id:str):
        self.root=repository_root.resolve(strict=True); self.repository_id=repository_id; self.state=StateDB(path)
        with self.state.transaction(immediate=True) as db:
            db.executescript("""CREATE TABLE IF NOT EXISTS structural_files(path TEXT PRIMARY KEY,content_hash TEXT NOT NULL,language TEXT NOT NULL,indexed_at REAL NOT NULL);CREATE TABLE IF NOT EXISTS structural_symbols(symbol_id INTEGER PRIMARY KEY AUTOINCREMENT,path TEXT NOT NULL,name TEXT NOT NULL,kind TEXT NOT NULL,line INTEGER NOT NULL,UNIQUE(path,name,kind,line));CREATE INDEX IF NOT EXISTS structural_symbol_name_idx ON structural_symbols(name);CREATE TABLE IF NOT EXISTS structural_edges(source_path TEXT NOT NULL,source_symbol TEXT NOT NULL,edge_type TEXT NOT NULL,target TEXT NOT NULL,line INTEGER NOT NULL,confidence REAL NOT NULL,UNIQUE(source_path,source_symbol,edge_type,target,line));CREATE INDEX IF NOT EXISTS structural_edge_target_idx ON structural_edges(target,edge_type);""")
    def _paths(self):
        output=[]
        for path in self.root.rglob("*"):
            if not path.is_file() or path.suffix.casefold() not in SOURCE_SUFFIXES: continue
            relative=path.relative_to(self.root)
            if any(part in IGNORE_PARTS for part in relative.parts): continue
            output.append(path)
        return sorted(output)
    def index(self):
        current={str(path.relative_to(self.root)).replace("\\","/"):path for path in self._paths()}; changed=0; reused=0
        with self.state.read() as db: known={row["path"]:row["content_hash"] for row in db.execute("SELECT path,content_hash FROM structural_files")}
        for relative,path in current.items():
            digest=sha256_file(path)
            if known.get(relative)==digest: reused+=1; continue
            self._index_file(relative,path,digest); changed+=1
        removed=set(known)-set(current)
        if removed:
            with self.state.transaction(immediate=True) as db:
                for relative in removed: db.execute("DELETE FROM structural_edges WHERE source_path=?",(relative,)); db.execute("DELETE FROM structural_symbols WHERE path=?",(relative,)); db.execute("DELETE FROM structural_files WHERE path=?",(relative,))
        return {"changed":changed,"reused":reused,"removed":len(removed),"total":len(current)}
    def _index_file(self,relative,path,digest):
        text=path.read_text(encoding="utf-8",errors="replace"); symbols=[]; edges=[]; language=path.suffix.casefold().lstrip(".")
        if path.suffix.casefold()==".py":
            try:
                tree=ast.parse(text,filename=relative); parents=["<module>"]
                class Visitor(ast.NodeVisitor):
                    def visit_ClassDef(self,node): symbols.append((node.name,"class",node.lineno)); parents.append(node.name); self.generic_visit(node); parents.pop()
                    def visit_FunctionDef(self,node): symbols.append((node.name,"function",node.lineno)); parents.append(node.name); self.generic_visit(node); parents.pop()
                    visit_AsyncFunctionDef=visit_FunctionDef
                    def visit_Call(self,node):
                        target=node.func.id if isinstance(node.func,ast.Name) else node.func.attr if isinstance(node.func,ast.Attribute) else None
                        if target: edges.append((parents[-1],"calls",target,node.lineno,0.95))
                        self.generic_visit(node)
                    def visit_Import(self,node):
                        for alias in node.names: edges.append((parents[-1],"imports",alias.name,node.lineno,1.0))
                    def visit_ImportFrom(self,node):
                        if node.module: edges.append((parents[-1],"imports",node.module,node.lineno,1.0))
                Visitor().visit(tree)
            except SyntaxError: pass
        if not symbols:
            for match in GENERIC_DEF.finditer(text): symbols.append((match.group(1),"symbol",text.count("\n",0,match.start())+1))
        for match in GENERIC_CALL.finditer(text):
            target=match.group(1)
            if target not in {"if","for","while","switch","return"}: edges.append(("<file>","calls",target,text.count("\n",0,match.start())+1,0.5))
        for match in GENERIC_IMPORT.finditer(text):
            target=next((group for group in match.groups() if group),None)
            if target: edges.append(("<file>","imports",target,text.count("\n",0,match.start())+1,0.7))
        with self.state.transaction(immediate=True) as db:
            db.execute("DELETE FROM structural_edges WHERE source_path=?",(relative,)); db.execute("DELETE FROM structural_symbols WHERE path=?",(relative,)); db.executemany("INSERT OR IGNORE INTO structural_symbols(path,name,kind,line) VALUES(?,?,?,?)",[(relative,*item) for item in symbols]); db.executemany("INSERT OR IGNORE INTO structural_edges(source_path,source_symbol,edge_type,target,line,confidence) VALUES(?,?,?,?,?,?)",[(relative,*item) for item in edges]); db.execute("INSERT INTO structural_files(path,content_hash,language,indexed_at) VALUES(?,?,?,?) ON CONFLICT(path) DO UPDATE SET content_hash=excluded.content_hash,language=excluded.language,indexed_at=excluded.indexed_at",(relative,digest,language,time.time()))
    def inspect_symbol(self,query,*,limit=20):
        with self.state.read() as db: rows=[dict(row) for row in db.execute("SELECT path,name,kind,line FROM structural_symbols WHERE name LIKE ? ORDER BY CASE WHEN name=? THEN 0 ELSE 1 END,path,line LIMIT ?",(f"%{query}%",query,limit))]
        return {"query":query,"symbols":rows}
    def inspect_impact(self,query,*,max_depth=3):
        with self.state.read() as db: direct=[dict(row) for row in db.execute("SELECT source_path,source_symbol,edge_type,target,line,confidence FROM structural_edges WHERE target=? ORDER BY confidence DESC,source_path,line",(query,))]; definitions=[dict(row) for row in db.execute("SELECT path,name,kind,line FROM structural_symbols WHERE name=?",(query,))]
        affected=sorted({row["source_path"] for row in direct}|{row["path"] for row in definitions}); return {"query":query,"definitions":definitions,"direct_references":direct,"affected_paths":affected,"affected_tests":[p for p in affected if "test" in p.casefold()],"max_depth":max_depth}
