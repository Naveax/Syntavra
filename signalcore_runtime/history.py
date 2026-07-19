from __future__ import annotations

import json
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .state import StateDB
from .util import canonical_json,sha256_bytes

@dataclass(frozen=True)
class HistoryEvent:
    seq:int; event_type:str; payload:dict[str,Any]; previous_hash:str; event_hash:str; created_at:float

class ImmutableHistory:
    def __init__(self,path:Path,*,session_id:str):
        self.state=StateDB(path); self.session_id=session_id
        with self.state.transaction(immediate=True) as db:
            db.executescript("""CREATE TABLE IF NOT EXISTS history_events(session_id TEXT NOT NULL,seq INTEGER NOT NULL,event_type TEXT NOT NULL,payload_json TEXT NOT NULL,previous_hash TEXT NOT NULL,event_hash TEXT NOT NULL,created_at REAL NOT NULL,PRIMARY KEY(session_id,seq),UNIQUE(session_id,event_hash));CREATE TABLE IF NOT EXISTS summary_nodes(summary_id TEXT PRIMARY KEY,session_id TEXT NOT NULL,content TEXT NOT NULL,parent_ids_json TEXT NOT NULL,source_event_seqs_json TEXT NOT NULL,created_at REAL NOT NULL);""")
    def append(self,event_type,payload):
        with self.state.transaction(immediate=True) as db:
            row=db.execute("SELECT seq,event_hash FROM history_events WHERE session_id=? ORDER BY seq DESC LIMIT 1",(self.session_id,)).fetchone(); seq=int(row["seq"])+1 if row else 1; previous=row["event_hash"] if row else "0"*64; created=time.time(); material={"session_id":self.session_id,"seq":seq,"event_type":event_type,"payload":payload,"previous_hash":previous,"created_at":created}; digest=sha256_bytes(canonical_json(material)); db.execute("INSERT INTO history_events VALUES(?,?,?,?,?,?,?)",(self.session_id,seq,event_type,json.dumps(payload,ensure_ascii=False,sort_keys=True),previous,digest,created))
        return HistoryEvent(seq,event_type,payload,previous,digest,created)
    def verify_chain(self):
        previous="0"*64
        with self.state.read() as db: rows=db.execute("SELECT * FROM history_events WHERE session_id=? ORDER BY seq",(self.session_id,)).fetchall()
        for expected,row in enumerate(rows,1):
            payload=json.loads(row["payload_json"]); material={"session_id":self.session_id,"seq":expected,"event_type":row["event_type"],"payload":payload,"previous_hash":previous,"created_at":row["created_at"]}; digest=sha256_bytes(canonical_json(material))
            if row["seq"]!=expected or row["previous_hash"]!=previous or row["event_hash"]!=digest: return False
            previous=digest
        return True
    def create_summary(self,content,*,parent_ids,source_event_seqs):
        payload={"session_id":self.session_id,"content":content,"parent_ids":parent_ids,"source_event_seqs":source_event_seqs}; summary_id="sum_"+sha256_bytes(canonical_json(payload))[:16]
        with self.state.transaction(immediate=True) as db: db.execute("INSERT OR IGNORE INTO summary_nodes VALUES(?,?,?,?,?,?)",(summary_id,self.session_id,content,json.dumps(parent_ids),json.dumps(source_event_seqs),time.time()))
        return summary_id
    def expand_summary(self,summary_id):
        with self.state.read() as db:
            row=db.execute("SELECT * FROM summary_nodes WHERE summary_id=? AND session_id=?",(summary_id,self.session_id)).fetchone()
            if not row: raise KeyError(summary_id)
            seqs=json.loads(row["source_event_seqs_json"]); events=[dict(item) for item in db.execute(f"SELECT * FROM history_events WHERE session_id=? AND seq IN ({','.join('?' for _ in seqs)}) ORDER BY seq",[self.session_id,*seqs])] if seqs else []
        for event in events: event["payload"]=json.loads(event.pop("payload_json"))
        return {"summary_id":summary_id,"content":row["content"],"parents":json.loads(row["parent_ids_json"]),"events":events}
