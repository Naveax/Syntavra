from __future__ import annotations

import json
import math
import re
import sqlite3
import os
import subprocess
import tempfile
import time
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Callable, Iterable, Mapping, Sequence

from .notifications import NotificationFeed
from .util import canonical_json, sha256_bytes


_TOKEN_RE = re.compile(r"[\w.-]+", re.UNICODE)


def _tokens(text: str) -> list[str]: return [item.casefold() for item in _TOKEN_RE.findall(text)]


def _embedding(text: str, dimensions: int = 128) -> list[float]:
    values=[0.0]*dimensions
    for token in _tokens(text):
        digest=bytes.fromhex(sha256_bytes(token.encode("utf-8")))
        index=int.from_bytes(digest[:4],"big")%dimensions
        sign=-1.0 if digest[4]&1 else 1.0
        values[index]+=sign*(1+math.log1p(len(token)))
    norm=math.sqrt(sum(value*value for value in values)) or 1.0
    return [value/norm for value in values]


def _cosine(left: Sequence[float], right: Sequence[float]) -> float: return sum(a*b for a,b in zip(left,right))


@dataclass(frozen=True)
class MemoryObservation:
    observation_id: str
    text: str
    kind: str
    importance: float
    confidence: float
    validity: float
    reuse_count: int
    success_count: int
    failure_count: int
    created_at: float
    updated_at: float
    source_hash: str
    metadata: dict[str, Any]

    @property
    def roi(self) -> float:
        evidence=(self.success_count+1)/(self.failure_count+1)
        return self.importance*self.confidence*self.validity*evidence*math.log2(2+self.reuse_count)


class MemoryIntelligenceStore:
    def __init__(self, path: Path, *, notification_feed: NotificationFeed | None = None):
        self.path=Path(path); self.path.parent.mkdir(parents=True,exist_ok=True); self.notifications=notification_feed
        self._init()

    def _db(self) -> sqlite3.Connection:
        db=sqlite3.connect(self.path); db.row_factory=sqlite3.Row; return db

    def _init(self) -> None:
        with self._db() as db:
            db.executescript("""
            CREATE TABLE IF NOT EXISTS observations(
              observation_id TEXT PRIMARY KEY,text TEXT NOT NULL,kind TEXT NOT NULL,
              importance REAL NOT NULL,confidence REAL NOT NULL,validity REAL NOT NULL,
              reuse_count INTEGER NOT NULL DEFAULT 0,success_count INTEGER NOT NULL DEFAULT 0,
              failure_count INTEGER NOT NULL DEFAULT 0,created_at REAL NOT NULL,updated_at REAL NOT NULL,
              source_hash TEXT NOT NULL,metadata_json TEXT NOT NULL,embedding_json TEXT
            );
            CREATE INDEX IF NOT EXISTS observations_kind_idx ON observations(kind);
            """)

    @staticmethod
    def _row(row: sqlite3.Row) -> MemoryObservation:
        return MemoryObservation(row["observation_id"],row["text"],row["kind"],float(row["importance"]),float(row["confidence"]),float(row["validity"]),int(row["reuse_count"]),int(row["success_count"]),int(row["failure_count"]),float(row["created_at"]),float(row["updated_at"]),row["source_hash"],json.loads(row["metadata_json"]))

    def add(self, text: str, *, kind: str="observation", importance: float=.5, confidence: float=.7, validity: float=1.0, metadata: Mapping[str,Any]|None=None, embed: bool=True) -> MemoryObservation:
        if not text.strip(): raise ValueError("memory text is required")
        now=time.time(); source_hash=sha256_bytes(text.strip().encode("utf-8")); observation_id=sha256_bytes(canonical_json({"text":text.strip(),"kind":kind,"source_hash":source_hash}))
        with self._db() as db:
            db.execute(
                """
                INSERT INTO observations(
                  observation_id,text,kind,importance,confidence,validity,reuse_count,success_count,
                  failure_count,created_at,updated_at,source_hash,metadata_json,embedding_json
                ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                ON CONFLICT(observation_id) DO UPDATE SET
                  importance=MAX(observations.importance,excluded.importance),
                  confidence=MAX(observations.confidence,excluded.confidence),
                  validity=MAX(observations.validity,excluded.validity),
                  updated_at=excluded.updated_at,
                  metadata_json=excluded.metadata_json,
                  embedding_json=COALESCE(observations.embedding_json,excluded.embedding_json)
                """,
                (observation_id,text.strip(),kind,max(0,min(1,float(importance))),max(0,min(1,float(confidence))),max(0,min(1,float(validity))),0,0,0,now,now,source_hash,json.dumps(dict(metadata or {}),ensure_ascii=False,sort_keys=True),json.dumps(_embedding(text)) if embed else None),
            )
            row=db.execute("SELECT * FROM observations WHERE observation_id=?",(observation_id,)).fetchone()
        item=self._row(row)
        if self.notifications and item.importance>=.9:
            self.notifications.record(channel="memory",severity="critical",title=f"Critical {kind}",body=text[:1000])
        return item


    @staticmethod
    def external_extractor(transcript: str) -> list[Mapping[str, Any]]:
        raw = os.environ.get("SYNTAVRA_MEMORY_EXTRACTOR_COMMAND_JSON", "")
        if not raw:
            raise RuntimeError("SYNTAVRA_MEMORY_EXTRACTOR_COMMAND_JSON is not configured")
        argv = json.loads(raw)
        if not isinstance(argv, list) or not argv or not all(isinstance(item, str) and item for item in argv):
            raise ValueError("memory extractor command must be a non-empty JSON argv array")
        with tempfile.TemporaryDirectory(prefix="syntavra-memory-") as td:
            root=Path(td); request=root/"request.json"; output=root/"result.json"
            request.write_text(json.dumps({"transcript":transcript},ensure_ascii=False),encoding="utf-8")
            command=[item.replace("{request}",str(request)).replace("{output}",str(output)) for item in argv]
            completed=subprocess.run(command,stdin=subprocess.DEVNULL,stdout=subprocess.DEVNULL,stderr=subprocess.PIPE,text=True,timeout=120,check=False)
            if completed.returncode != 0: raise RuntimeError(f"memory extractor failed: {completed.returncode}")
            value=json.loads(output.read_text(encoding="utf-8"))
            rows=value.get("observations",value) if isinstance(value,Mapping) else value
            if not isinstance(rows,list): raise ValueError("memory extractor result must be a list")
            return [row for row in rows if isinstance(row,Mapping)]

    def extract(self, transcript: str, *, extractor: Callable[[str], Iterable[Mapping[str,Any]]]|None=None) -> list[MemoryObservation]:
        if extractor:
            rows=list(extractor(transcript))
        elif os.environ.get("SYNTAVRA_MEMORY_EXTRACTOR_COMMAND_JSON"):
            rows=list(self.external_extractor(transcript))
        else:
            rows=[]
            patterns=(("decision",r"(?im)^\s*(?:decision|decided|we will|keep|use)\s*[:\-]?\s*(.+)$",.8),("failure",r"(?im)^\s*(?:root cause|failure|error cause)\s*[:\-]?\s*(.+)$",.75),("constraint",r"(?im)^\s*(?:constraint|must|never)\s*[:\-]?\s*(.+)$",.85),("preference",r"(?im)^\s*(?:preference|prefer)\s*[:\-]?\s*(.+)$",.65))
            for kind,pattern,importance in patterns:
                for match in re.finditer(pattern,transcript):
                    rows.append({"text":match.group(1).strip(),"kind":kind,"importance":importance,"confidence":.65,"metadata":{"extraction":"heuristic"}})
        return [self.add(str(row.get("text") or ""),kind=str(row.get("kind") or "observation"),importance=float(row.get("importance",.5)),confidence=float(row.get("confidence",.7)),validity=float(row.get("validity",1)),metadata=row.get("metadata") if isinstance(row.get("metadata"),Mapping) else {}) for row in rows if str(row.get("text") or "").strip()]

    def backfill_embeddings(self, *, limit: int=1000) -> dict[str,int]:
        with self._db() as db:
            rows=db.execute("SELECT observation_id,text FROM observations WHERE embedding_json IS NULL LIMIT ?",(limit,)).fetchall()
            for row in rows: db.execute("UPDATE observations SET embedding_json=?,updated_at=? WHERE observation_id=?",(json.dumps(_embedding(row["text"])),time.time(),row["observation_id"]))
        return {"embedded":len(rows),"remaining":self.stats()["missing_embeddings"]}

    def feedback(self, observation_id: str, *, success: bool, still_valid: bool=True) -> MemoryObservation:
        with self._db() as db:
            row=db.execute("SELECT * FROM observations WHERE observation_id=?",(observation_id,)).fetchone()
            if not row: raise KeyError(observation_id)
            success_count=int(row["success_count"])+(1 if success else 0); failure_count=int(row["failure_count"])+(0 if success else 1); validity=float(row["validity"]) if still_valid else 0.0
            db.execute("UPDATE observations SET reuse_count=reuse_count+1,success_count=?,failure_count=?,validity=?,updated_at=? WHERE observation_id=?",(success_count,failure_count,validity,time.time(),observation_id))
            updated=db.execute("SELECT * FROM observations WHERE observation_id=?",(observation_id,)).fetchone()
        return self._row(updated)

    def search(self, query: str, *, limit: int=20, include_invalid: bool=False) -> list[dict[str,Any]]:
        query_tokens=_tokens(query); qembed=_embedding(query)
        with self._db() as db: rows=db.execute("SELECT * FROM observations").fetchall()
        docs=[_tokens(row["text"]) for row in rows]; n=max(1,len(docs)); df={term:sum(term in doc for doc in docs) for term in set(query_tokens)}
        results=[]
        for row,doc in zip(rows,docs):
            item=self._row(row)
            if not include_invalid and item.validity<=0: continue
            bm25=0.0; length=max(1,len(doc)); avg=sum(len(value) for value in docs)/n if docs else 1
            counts={term:doc.count(term) for term in set(query_tokens)}
            for term in query_tokens:
                tf=counts.get(term,0); idf=math.log(1+(n-df.get(term,0)+.5)/(df.get(term,0)+.5)); bm25+=idf*(tf*2.2)/(tf+1.2*(1-.75+.75*length/max(1,avg))) if tf else 0
            embed=json.loads(row["embedding_json"]) if row["embedding_json"] else _embedding(row["text"])
            semantic=_cosine(qembed,embed); score=bm25*4+semantic*25+item.roi*5
            if score>0: results.append({"observation":asdict(item)|{"roi":item.roi},"bm25":bm25,"cosine":semantic,"score":score})
        return sorted(results,key=lambda row:(-row["score"],row["observation"]["observation_id"]))[:max(1,limit)]

    def ranked(self, *, limit: int=100) -> list[dict[str,Any]]:
        with self._db() as db: rows=db.execute("SELECT * FROM observations").fetchall()
        items=[self._row(row) for row in rows]
        return [asdict(item)|{"roi":item.roi} for item in sorted(items,key=lambda item:(-item.roi,item.observation_id))[:limit]]

    def export_jsonl(self, path: Path) -> dict[str,Any]:
        rows=self.ranked(limit=1_000_000); path=Path(path); path.parent.mkdir(parents=True,exist_ok=True)
        with path.open("w",encoding="utf-8") as handle:
            for row in rows: handle.write(json.dumps(row,ensure_ascii=False,sort_keys=True,separators=(",",":"))+"\n")
        return {"path":str(path),"observations":len(rows),"sha256":sha256_bytes(path.read_bytes())}

    def stats(self) -> dict[str,int]:
        with self._db() as db:
            return {"observations":int(db.execute("SELECT COUNT(*) FROM observations").fetchone()[0]),"valid":int(db.execute("SELECT COUNT(*) FROM observations WHERE validity>0").fetchone()[0]),"missing_embeddings":int(db.execute("SELECT COUNT(*) FROM observations WHERE embedding_json IS NULL").fetchone()[0])}
