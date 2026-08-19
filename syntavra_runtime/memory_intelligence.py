from __future__ import annotations

import json
import math
import re
import sqlite3
import os
import subprocess
import tempfile
import time
from contextlib import contextmanager
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Callable, Iterable, Iterator, Mapping, Sequence

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

    @contextmanager
    def _connection(self) -> Iterator[sqlite3.Connection]:
        db = self._db()
        try:
            with db:
                yield db
        finally:
            db.close()

    def _init(self) -> None:
        with self._connection() as db:
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
        with self._connection() as db:
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
        with self._connection() as db:
            rows=db.execute("SELECT observation_id,text FROM observations WHERE embedding_json IS NULL LIMIT ?",(limit,)).fetchall()
            for row in rows: db.execute("UPDATE observations SET embedding_json=?,updated_at=? WHERE observation_id=?",(json.dumps(_embedding(row["text"])),time.time(),row["observation_id"]))
        return {"embedded":len(rows),"remaining":self.stats()["missing_embeddings"]}

    def feedback(self, observation_id: str, *, success: bool, still_valid: bool=True) -> MemoryObservation:
        with self._connection() as db:
            row=db.execute("SELECT * FROM observations WHERE observation_id=?",(observation_id,)).fetchone()
            if not row: raise KeyError(observation_id)
            success_count=int(row["success_count"])+(1 if success else 0); failure_count=int(row["failure_count"])+(0 if success else 1); validity=float(row["validity"]) if still_valid else 0.0
            db.execute("UPDATE observations SET reuse_count=reuse_count+1,success_count=?,failure_count=?,validity=?,updated_at=? WHERE observation_id=?",(success_count,failure_count,validity,time.time(),observation_id))
            updated=db.execute("SELECT * FROM observations WHERE observation_id=?",(observation_id,)).fetchone()
        return self._row(updated)

    def search(self, query: str, *, limit: int=20, include_invalid: bool=False) -> list[dict[str,Any]]:
        query_tokens=_tokens(query); qembed=_embedding(query)
        with self._connection() as db: rows=db.execute("SELECT * FROM observations").fetchall()
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
        with self._connection() as db: rows=db.execute("SELECT * FROM observations").fetchall()
        items=[self._row(row) for row in rows]
        return [asdict(item)|{"roi":item.roi} for item in sorted(items,key=lambda item:(-item.roi,item.observation_id))[:limit]]

    def export_jsonl(self, path: Path) -> dict[str,Any]:
        rows=self.ranked(limit=1_000_000); path=Path(path); path.parent.mkdir(parents=True,exist_ok=True)
        with path.open("w",encoding="utf-8") as handle:
            for row in rows: handle.write(json.dumps(row,ensure_ascii=False,sort_keys=True,separators=(",",":"))+"\n")
        return {"path":str(path),"observations":len(rows),"sha256":sha256_bytes(path.read_bytes())}

    def stats(self) -> dict[str,int]:
        with self._connection() as db:
            return {"observations":int(db.execute("SELECT COUNT(*) FROM observations").fetchone()[0]),"valid":int(db.execute("SELECT COUNT(*) FROM observations WHERE validity>0").fetchone()[0]),"missing_embeddings":int(db.execute("SELECT COUNT(*) FROM observations WHERE embedding_json IS NULL").fetchone()[0])}


MEMORY_RETRIEVAL_KINDS = frozenset({"episodic", "semantic", "procedural", "project", "user", "temporal"})
_MEMORY_QUERY_EXPANSIONS = {
    "decision": ("chosen", "decided", "constraint"),
    "failure": ("error", "root-cause", "regression"),
    "procedure": ("steps", "workflow", "runbook"),
    "project": ("repository", "codebase", "workspace"),
    "user": ("preference", "constraint"),
    "time": ("temporal", "recent", "timeline"),
}


@dataclass(frozen=True)
class MemoryScope:
    project_id: str
    user_id: str = ""
    session_id: str = ""

    def __post_init__(self) -> None:
        if not self.project_id.strip():
            raise ValueError("project_id is required")
        object.__setattr__(self, "project_id", self.project_id.strip())
        object.__setattr__(self, "user_id", self.user_id.strip())
        object.__setattr__(self, "session_id", self.session_id.strip())

    def as_dict(self) -> dict[str, str]:
        return {
            "project_id": self.project_id,
            "user_id": self.user_id,
            "session_id": self.session_id,
        }


class MemoryRetrievalV1:
    """Scoped, provenance-aware retrieval over the existing memory/session authorities."""

    def __init__(self, store: MemoryIntelligenceStore, *, session_memory: Any | None = None):
        self.store = store
        self.session_memory = session_memory
        with self.store._connection() as db:
            db.executescript(
                """
                CREATE TABLE IF NOT EXISTS memory_retrieval_records(
                  memory_id TEXT PRIMARY KEY,
                  observation_id TEXT NOT NULL,
                  memory_kind TEXT NOT NULL,
                  scope_json TEXT NOT NULL,
                  provenance_json TEXT NOT NULL,
                  supersedes_json TEXT NOT NULL,
                  conflicts_json TEXT NOT NULL,
                  state TEXT NOT NULL,
                  created_at REAL NOT NULL,
                  updated_at REAL NOT NULL
                );
                CREATE INDEX IF NOT EXISTS memory_retrieval_observation_idx
                  ON memory_retrieval_records(observation_id);
                CREATE INDEX IF NOT EXISTS memory_retrieval_kind_state_idx
                  ON memory_retrieval_records(memory_kind,state);
                """
            )

    @staticmethod
    def _refs(values: Sequence[str]) -> tuple[str, ...]:
        return tuple(sorted({str(value).strip() for value in values if str(value).strip()}))

    @staticmethod
    def _scope_matches(candidate: Mapping[str, Any], requested: MemoryScope) -> bool:
        if str(candidate.get("project_id") or "") != requested.project_id:
            return False
        candidate_user = str(candidate.get("user_id") or "")
        candidate_session = str(candidate.get("session_id") or "")
        if requested.user_id:
            if candidate_user not in {"", requested.user_id}:
                return False
        elif candidate_user:
            return False
        if requested.session_id:
            if candidate_session not in {"", requested.session_id}:
                return False
        elif candidate_session:
            return False
        return True

    def _record(self, memory_id: str) -> dict[str, Any]:
        with self.store._connection() as db:
            row = db.execute(
                """
                SELECT r.*, o.text, o.importance, o.confidence, o.validity, o.source_hash,
                       o.created_at AS observation_created_at, o.updated_at AS observation_updated_at
                FROM memory_retrieval_records r
                JOIN observations o ON o.observation_id = r.observation_id
                WHERE r.memory_id = ?
                """,
                (memory_id,),
            ).fetchone()
        if row is None:
            raise KeyError(memory_id)
        value = dict(row)
        for key in ("scope_json", "provenance_json", "supersedes_json", "conflicts_json"):
            value[key.removesuffix("_json")] = json.loads(value.pop(key))
        return value

    def remember(
        self,
        text: str,
        *,
        kind: str,
        scope: MemoryScope,
        provenance_refs: Sequence[str],
        supersedes: Sequence[str] = (),
        conflicts_with: Sequence[str] = (),
        importance: float = 0.5,
        confidence: float = 0.7,
        validity: float = 1.0,
        metadata: Mapping[str, Any] | None = None,
    ) -> dict[str, Any]:
        normalized_kind = str(kind).strip().casefold()
        if normalized_kind not in MEMORY_RETRIEVAL_KINDS:
            raise ValueError(f"unsupported memory kind: {kind}")
        provenance = self._refs(provenance_refs)
        if not provenance:
            raise ValueError("memory provenance_refs are required")
        supersedes_refs = self._refs(supersedes)
        conflicts = self._refs(conflicts_with)
        if set(supersedes_refs) & set(conflicts):
            raise ValueError("a memory cannot both supersede and conflict with the same record")
        for related in (*supersedes_refs, *conflicts):
            self._record(related)

        observation = self.store.add(
            text,
            kind=normalized_kind,
            importance=importance,
            confidence=confidence,
            validity=validity,
            metadata=dict(metadata or {}),
        )
        basis = {
            "schema_version": 1,
            "observation_id": observation.observation_id,
            "memory_kind": normalized_kind,
            "scope": scope.as_dict(),
            "provenance_refs": list(provenance),
        }
        memory_id = sha256_bytes(canonical_json(basis))
        now = time.time()
        with self.store._connection() as db:
            db.execute(
                """
                INSERT INTO memory_retrieval_records(
                  memory_id,observation_id,memory_kind,scope_json,provenance_json,
                  supersedes_json,conflicts_json,state,created_at,updated_at
                ) VALUES(?,?,?,?,?,?,?,?,?,?)
                ON CONFLICT(memory_id) DO UPDATE SET
                  supersedes_json=excluded.supersedes_json,
                  conflicts_json=excluded.conflicts_json,
                  state='active',
                  updated_at=excluded.updated_at
                """,
                (
                    memory_id,
                    observation.observation_id,
                    normalized_kind,
                    json.dumps(scope.as_dict(), ensure_ascii=False, sort_keys=True),
                    json.dumps(list(provenance), ensure_ascii=False, sort_keys=True),
                    json.dumps(list(supersedes_refs), ensure_ascii=False, sort_keys=True),
                    json.dumps(list(conflicts), ensure_ascii=False, sort_keys=True),
                    "active",
                    now,
                    now,
                ),
            )
            for old_id in supersedes_refs:
                db.execute(
                    "UPDATE memory_retrieval_records SET state='superseded',updated_at=? WHERE memory_id=?",
                    (now, old_id),
                )
            for conflict_id in conflicts:
                row = db.execute(
                    "SELECT conflicts_json FROM memory_retrieval_records WHERE memory_id=?",
                    (conflict_id,),
                ).fetchone()
                existing = set(json.loads(row["conflicts_json"])) if row else set()
                existing.add(memory_id)
                db.execute(
                    "UPDATE memory_retrieval_records SET conflicts_json=?,updated_at=? WHERE memory_id=?",
                    (json.dumps(sorted(existing)), now, conflict_id),
                )
        return self.recover(memory_id)

    def forget(self, memory_id: str, *, reason: str) -> dict[str, Any]:
        if not reason.strip():
            raise ValueError("forget reason is required")
        self._record(memory_id)
        now = time.time()
        with self.store._connection() as db:
            db.execute(
                "UPDATE memory_retrieval_records SET state='forgotten',updated_at=? WHERE memory_id=?",
                (now, memory_id),
            )
        recovered = self.recover(memory_id)
        recovered["forget_reason"] = reason.strip()
        return recovered

    def consolidate(
        self,
        memory_ids: Sequence[str],
        text: str,
        *,
        kind: str,
        scope: MemoryScope,
        provenance_refs: Sequence[str] = (),
        importance: float = 0.7,
        confidence: float = 0.8,
    ) -> dict[str, Any]:
        parents = self._refs(memory_ids)
        if len(parents) < 2:
            raise ValueError("consolidation requires at least two distinct memories")
        for memory_id in parents:
            self._record(memory_id)
        provenance = self._refs((*provenance_refs, *(f"memory:{memory_id}" for memory_id in parents)))
        result = self.remember(
            text,
            kind=kind,
            scope=scope,
            provenance_refs=provenance,
            supersedes=parents,
            importance=importance,
            confidence=confidence,
            metadata={"consolidated_from": list(parents)},
        )
        result["consolidated_from"] = list(parents)
        return result

    def recover(self, memory_id: str) -> dict[str, Any]:
        record = self._record(memory_id)
        exact = str(record["text"])
        expected = sha256_bytes(exact.strip().encode("utf-8"))
        if expected != record["source_hash"]:
            raise RuntimeError("memory source hash mismatch")
        return {
            "schema_version": 1,
            "memory_id": record["memory_id"],
            "observation_id": record["observation_id"],
            "kind": record["memory_kind"],
            "scope": record["scope"],
            "provenance_refs": record["provenance"],
            "supersedes": record["supersedes"],
            "conflicts_with": record["conflicts"],
            "state": record["state"],
            "text": exact,
            "source_hash": record["source_hash"],
            "importance": float(record["importance"]),
            "confidence": float(record["confidence"]),
            "validity": float(record["validity"]),
            "exact_recovery": True,
        }

    @staticmethod
    def _expanded_query(query: str) -> str:
        terms = _tokens(query)
        additions: set[str] = set()
        for term in terms:
            additions.update(_MEMORY_QUERY_EXPANSIONS.get(term, ()))
        return " ".join([query.strip(), *sorted(additions)]).strip()

    def retrieve(
        self,
        query: str,
        *,
        scope: MemoryScope,
        limit: int = 12,
        include_session: bool = True,
    ) -> dict[str, Any]:
        if not query.strip():
            raise ValueError("memory query is required")
        bounded_limit = max(1, min(100, int(limit)))
        expanded = self._expanded_query(query)
        base = self.store.search(expanded, limit=max(64, bounded_limit * 8), include_invalid=True)
        by_observation = {
            row["observation"]["observation_id"]: row
            for row in base
        }
        observation_ids = tuple(by_observation)
        records: list[dict[str, Any]] = []
        if observation_ids:
            placeholders = ",".join("?" for _ in observation_ids)
            with self.store._connection() as db:
                rows = db.execute(
                    f"""
                    SELECT * FROM memory_retrieval_records
                    WHERE state='active' AND observation_id IN ({placeholders})
                    """,
                    observation_ids,
                ).fetchall()
            records = [dict(row) for row in rows]

        latest = max((float(row["updated_at"]) for row in records), default=0.0)
        ranked: list[dict[str, Any]] = []
        for record in records:
            candidate_scope = json.loads(record["scope_json"])
            if not self._scope_matches(candidate_scope, scope):
                continue
            base_row = by_observation[record["observation_id"]]
            observation = base_row["observation"]
            recency = 0.0
            if latest:
                age = max(0.0, latest - float(record["updated_at"]))
                recency = max(0.0, 1.0 - age / (30.0 * 86400.0))
            conflict_refs = json.loads(record["conflicts_json"])
            score = (
                float(base_row["score"])
                + float(observation["importance"]) * 5.0
                + float(observation["confidence"]) * 3.0
                + float(observation["validity"]) * 2.0
                + recency * 4.0
            )
            ranked.append(
                {
                    "memory_id": record["memory_id"],
                    "kind": record["memory_kind"],
                    "scope": candidate_scope,
                    "score": score,
                    "bm25": float(base_row["bm25"]),
                    "cosine": float(base_row["cosine"]),
                    "recency": recency,
                    "conflicts_with": conflict_refs,
                    "preview": str(observation["text"])[:700],
                    "source_hash": observation["source_hash"],
                    "recovery_handle": f"memory:{record['memory_id']}",
                }
            )
        ranked.sort(key=lambda row: (-row["score"], row["memory_id"]))
        selected = ranked[:bounded_limit]

        session_result: dict[str, Any] | None = None
        if include_session and self.session_memory is not None and scope.session_id:
            if getattr(self.session_memory, "project_id", None) != scope.project_id:
                raise RuntimeError("session memory project scope mismatch")
            verification = self.session_memory.verify(scope.session_id)
            if verification.get("ok") is not True:
                raise RuntimeError("session memory chain verification failed")
            session_result = self.session_memory.retrieve(scope.session_id, query, limit=bounded_limit)
            if session_result.get("exact_recovery") is not True:
                raise RuntimeError("session memory exact recovery failed")

        receipt_basis = {
            "schema_version": 1,
            "query": query.strip(),
            "expanded_query": expanded,
            "scope": scope.as_dict(),
            "memory_results": [
                {
                    "memory_id": row["memory_id"],
                    "source_hash": row["source_hash"],
                    "conflicts_with": sorted(row["conflicts_with"]),
                }
                for row in selected
            ],
            "session_results": [
                {
                    "type": row.get("type"),
                    "sequence": row.get("sequence"),
                    "summary_id": row.get("summary_id"),
                    "event_hash": row.get("event_hash"),
                }
                for row in (session_result or {}).get("results", [])
            ],
        }
        return {
            "schema_version": 1,
            "claim": "MEMORY_RETRIEVAL_V1",
            "ok": True,
            "query": query.strip(),
            "expanded_query": expanded,
            "scope": scope.as_dict(),
            "results": selected,
            "session": session_result,
            "exact_recovery": True,
            "receipt_hash": sha256_bytes(canonical_json(receipt_basis)),
            "receipt": receipt_basis,
        }

    def timeline(self, *, scope: MemoryScope, limit: int = 100) -> list[dict[str, Any]]:
        with self.store._connection() as db:
            rows = db.execute(
                "SELECT memory_id,scope_json,state,created_at,updated_at FROM memory_retrieval_records ORDER BY created_at,memory_id"
            ).fetchall()
        result = []
        for row in rows:
            candidate_scope = json.loads(row["scope_json"])
            if not self._scope_matches(candidate_scope, scope):
                continue
            result.append(
                {
                    "memory_id": row["memory_id"],
                    "scope": candidate_scope,
                    "state": row["state"],
                    "created_at": float(row["created_at"]),
                    "updated_at": float(row["updated_at"]),
                }
            )
        return result[-max(1, min(1000, int(limit))):]

    def handoff(self, memory_ids: Sequence[str], *, scope: MemoryScope) -> dict[str, Any]:
        ids = self._refs(memory_ids)
        recovered = [self.recover(memory_id) for memory_id in ids]
        for item in recovered:
            if not self._scope_matches(item["scope"], scope):
                raise RuntimeError("memory handoff scope mismatch")
        basis = {
            "schema_version": 1,
            "scope": scope.as_dict(),
            "memory_refs": [
                {
                    "memory_id": item["memory_id"],
                    "source_hash": item["source_hash"],
                    "state": item["state"],
                }
                for item in recovered
            ],
        }
        return {
            "schema_version": 1,
            "claim": "MEMORY_RETRIEVAL_HANDOFF_V1",
            "ok": True,
            "scope": scope.as_dict(),
            "recovery_handles": [f"memory:{item['memory_id']}" for item in recovered],
            "receipt_hash": sha256_bytes(canonical_json(basis)),
            "exact_recovery": True,
        }

    @staticmethod
    def status() -> dict[str, Any]:
        return {
            "schema_version": 1,
            "claim": "MEMORY_RETRIEVAL_V1",
            "memory_intelligence_store_reused": True,
            "session_memory_authority_reused": True,
            "new_persistent_database": False,
            "same_sqlite_relation_table": True,
            "memory_kinds": sorted(MEMORY_RETRIEVAL_KINDS),
            "hybrid_bm25_vector": True,
            "deterministic_query_expansion": True,
            "provenance": True,
            "conflict_preservation": True,
            "supersession": True,
            "consolidation": True,
            "forgetting_without_deletion": True,
            "scoped_retrieval": True,
            "exact_recovery": True,
            "cross_agent_handoff_receipts": True,
        }
