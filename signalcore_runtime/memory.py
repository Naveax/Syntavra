from __future__ import annotations

import json
import sqlite3
import time
import uuid
from dataclasses import dataclass, asdict
from pathlib import Path
from typing import Any, Iterable

from .state import StateDB

@dataclass(frozen=True)
class MemoryRecord:
    memory_id:str; memory_class:str; text:str; confidence:float; provenance:dict[str,Any]; created_at:float; superseded_by:str|None=None

class PersistentMemory:
    def __init__(self,path:Path,*,project_id:str,user_id:str="default"):
        self.state=StateDB(path); self.project_id=project_id; self.user_id=user_id; self.fts_available=False
        with self.state.transaction(immediate=True) as db:
            db.executescript("""CREATE TABLE IF NOT EXISTS memories(memory_id TEXT PRIMARY KEY,project_id TEXT NOT NULL,user_id TEXT NOT NULL,memory_class TEXT NOT NULL,text TEXT NOT NULL,confidence REAL NOT NULL,provenance_json TEXT NOT NULL,content_hash TEXT NOT NULL,created_at REAL NOT NULL,superseded_by TEXT,FOREIGN KEY(superseded_by) REFERENCES memories(memory_id));CREATE INDEX IF NOT EXISTS memories_scope_idx ON memories(project_id,user_id,memory_class,created_at DESC);""")
            try: db.execute("CREATE VIRTUAL TABLE IF NOT EXISTS memories_fts USING fts5(memory_id UNINDEXED, text, tokenize='unicode61')"); db.execute("INSERT INTO memories_fts(memories_fts) VALUES('integrity-check')"); self.fts_available=True
            except sqlite3.OperationalError: self.fts_available=False
    def add(self,memory_class,text,*,confidence=1.0,provenance=None):
        from .util import sha256_bytes
        clean=text.strip()
        if not clean: raise ValueError("memory text cannot be empty")
        if not 0<=confidence<=1: raise ValueError("confidence out of range")
        digest=sha256_bytes(clean.encode())
        with self.state.transaction(immediate=True) as db:
            existing=db.execute("SELECT * FROM memories WHERE project_id=? AND user_id=? AND memory_class=? AND content_hash=? AND superseded_by IS NULL",(self.project_id,self.user_id,memory_class,digest)).fetchone()
            if existing: return self._record(existing)
            memory_id=uuid.uuid4().hex; created=time.time(); db.execute("INSERT INTO memories VALUES(?,?,?,?,?,?,?,?,?,NULL)",(memory_id,self.project_id,self.user_id,memory_class,clean,confidence,json.dumps(provenance or {},ensure_ascii=False,sort_keys=True),digest,created))
            if self.fts_available: db.execute("INSERT INTO memories_fts(memory_id,text) VALUES(?,?)",(memory_id,clean))
            row=db.execute("SELECT * FROM memories WHERE memory_id=?",(memory_id,)).fetchone()
        return self._record(row)
    def supersede(self,old_id,new_id):
        with self.state.transaction(immediate=True) as db:
            if not db.execute("SELECT 1 FROM memories WHERE memory_id=? AND project_id=? AND user_id=?",(new_id,self.project_id,self.user_id)).fetchone(): raise KeyError(new_id)
            if db.execute("UPDATE memories SET superseded_by=? WHERE memory_id=? AND project_id=? AND user_id=?",(new_id,old_id,self.project_id,self.user_id)).rowcount!=1: raise KeyError(old_id)
    def search(self,query,*,limit=10,memory_classes=(),include_superseded=False):
        classes=tuple(memory_classes); params=[self.project_id,self.user_id]; clauses=["m.project_id=?","m.user_id=?"]
        if not include_superseded: clauses.append("m.superseded_by IS NULL")
        if classes: clauses.append(f"m.memory_class IN ({','.join('?' for _ in classes)})"); params.extend(classes)
        mode="LEXICAL_ONLY"; rows=[]
        with self.state.read() as db:
            if self.fts_available and query.strip():
                try: rows=db.execute("SELECT m.*,bm25(memories_fts) AS rank FROM memories_fts JOIN memories m ON m.memory_id=memories_fts.memory_id WHERE memories_fts MATCH ? AND "+" AND ".join(clauses)+" ORDER BY rank LIMIT ?",[query,*params,limit]).fetchall(); mode="FTS5"
                except sqlite3.OperationalError: mode="LEXICAL_DEGRADED"
            if not rows: rows=db.execute("SELECT m.*,0 AS rank FROM memories m WHERE m.text LIKE ? AND "+" AND ".join(clauses)+" ORDER BY m.created_at DESC LIMIT ?",[f"%{query}%",*params,limit]).fetchall()
        return {"mode":mode,"results":[asdict(self._record(row)) for row in rows]}
    @staticmethod
    def _record(row): return MemoryRecord(row["memory_id"],row["memory_class"],row["text"],float(row["confidence"]),json.loads(row["provenance_json"]),float(row["created_at"]),row["superseded_by"])
