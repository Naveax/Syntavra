from __future__ import annotations

import json
import os
import signal
import subprocess
import sys
import time
import uuid
from pathlib import Path
from typing import Iterable

from .evidence import EvidenceStore
from .models import JobRecord, ProcessResult
from .output_firewall import summarize
from .state import StateDB
from .util import atomic_write_json, canonical_json, sha256_bytes

FINAL_STATES = {"COMPLETED", "FAILED", "CANCELLED", "TIMED_OUT", "ORPHANED"}

def _job_from_row(row: dict) -> JobRecord:
    return JobRecord(job_id=row["job_id"],state=row["state"],argv=tuple(json.loads(row["argv_json"])),cwd=row["cwd"],created_at=row["created_at"],started_at=row["started_at"],completed_at=row["completed_at"],pid=row["pid"],exit_code=row["exit_code"],timed_out=bool(row["timed_out"]),cancelled=bool(row["cancelled"]),summary=row["summary"],evidence_handle=row["evidence_handle"],error=row["error"])

class ProcessBroker:
    def __init__(self, root: Path, evidence: EvidenceStore, *, heartbeat_interval: float = 5.0):
        self.root=root; self.root.mkdir(parents=True,exist_ok=True); self.db=StateDB(root/"broker.sqlite3"); self.evidence=evidence; self.heartbeat_interval=max(0.1,heartbeat_interval); self.completions=root/"completions.jsonl"; self._workers={}
    @staticmethod
    def _environment_hash(env=None):
        source=env if env is not None else os.environ; keys=("PATH","PATHEXT","PYTHONPATH","VIRTUAL_ENV","CONDA_PREFIX","OS","PROCESSOR_ARCHITECTURE"); return sha256_bytes(canonical_json({k:source.get(k,"") for k in keys}))
    def _new_job(self,argv,*,cwd,timeout,repository_tree,env):
        if not argv or any("\x00" in part for part in argv): raise ValueError("invalid argv")
        job_id=uuid.uuid4().hex; job_dir=self.root/"jobs"/job_id; job_dir.mkdir(parents=True,exist_ok=False); now=time.time()
        values={"job_id":job_id,"state":"ACCEPTED","argv_json":json.dumps(argv,ensure_ascii=False),"cwd":str(cwd),"created_at":now,"started_at":None,"completed_at":None,"pid":None,"exit_code":None,"timed_out":0,"cancelled":0,"summary":"","evidence_handle":"","error":"","timeout_seconds":timeout,"stdout_path":str(job_dir/"stdout.log"),"stderr_path":str(job_dir/"stderr.log"),"repository_tree":repository_tree,"environment_hash":self._environment_hash(env)}
        self.db.upsert_job(values); atomic_write_json(job_dir/"request.json",{"job_id":job_id,"argv":argv,"cwd":str(cwd),"timeout":timeout,"repository_tree":repository_tree,"environment_hash":values["environment_hash"]}); return _job_from_row(self.db.job(job_id) or values)
    def submit(self,argv:Iterable[str],*,cwd:Path,timeout:float=1200.0,repository_tree:str="unknown",env=None):
        cwd=cwd.resolve(strict=True); command=tuple(str(v) for v in argv); job=self._new_job(command,cwd=cwd,timeout=timeout,repository_tree=repository_tree,env=env)
        worker=[sys.executable,"-m","signalcore_runtime.broker_worker","--root",str(self.root),"--job-id",job.job_id]; worker_env=dict(os.environ); package_root=str(Path(__file__).resolve().parents[1]); worker_env["PYTHONPATH"]=package_root+(os.pathsep+worker_env["PYTHONPATH"] if worker_env.get("PYTHONPATH") else "")
        kwargs={"cwd":str(cwd),"env":worker_env,"stdin":subprocess.DEVNULL,"stdout":subprocess.DEVNULL,"stderr":subprocess.DEVNULL,"close_fds":True}
        if os.name=="nt": kwargs["creationflags"]=subprocess.CREATE_NEW_PROCESS_GROUP|subprocess.DETACHED_PROCESS
        else: kwargs["start_new_session"]=True
        process=subprocess.Popen(worker,**kwargs); self._workers[job.job_id]=process; self.db.update_job(job.job_id,state="QUEUED",pid=process.pid); return self.show(job.job_id)
    def run(self,argv:Iterable[str],*,cwd:Path,timeout:float=1200.0,cancel_file:Path|None=None,repository_tree:str="unknown",env=None):
        cwd=cwd.resolve(strict=True); command=tuple(str(v) for v in argv); job=self._new_job(command,cwd=cwd,timeout=timeout,repository_tree=repository_tree,env=env); return self._execute(job.job_id,env=env,cancel_file=cancel_file)
    def _execute(self,job_id,*,env=None,cancel_file=None):
        row=self.db.job(job_id)
        if not row: raise KeyError(job_id)
        argv=tuple(json.loads(row["argv_json"])); stdout_path=Path(row["stdout_path"]); stderr_path=Path(row["stderr_path"]); stdout_path.parent.mkdir(parents=True,exist_ok=True); started=time.time(); creation={}
        if os.name=="nt": creation["creationflags"]=subprocess.CREATE_NEW_PROCESS_GROUP
        else: creation["start_new_session"]=True
        with stdout_path.open("wb") as stdout, stderr_path.open("wb") as stderr:
            process=subprocess.Popen(argv,cwd=row["cwd"],env=env,stdout=stdout,stderr=stderr,stdin=subprocess.DEVNULL,**creation); self.db.update_job(job_id,state="RUNNING",pid=process.pid,started_at=started); timed_out=False; cancelled=False; deadline=started+float(row["timeout_seconds"]); heartbeat=self.root/"jobs"/job_id/"heartbeat.json"
            while process.poll() is None:
                now=time.time(); atomic_write_json(heartbeat,{"job_id":job_id,"pid":process.pid,"state":"RUNNING","updated_at":now})
                if cancel_file and cancel_file.exists(): cancelled=True; self._terminate_tree(process.pid); break
                if now>=deadline: timed_out=True; self._terminate_tree(process.pid); break
                time.sleep(min(self.heartbeat_interval,max(0.05,deadline-now)))
            try: exit_code=process.wait(timeout=10.0)
            except subprocess.TimeoutExpired: self._kill_tree(process.pid); exit_code=process.wait(timeout=10.0)
        completed=time.time(); firewall=summarize(argv,stdout_path=stdout_path,stderr_path=stderr_path,exit_code=exit_code,duration_seconds=completed-started,evidence=self.evidence)
        state="TIMED_OUT" if timed_out else "CANCELLED" if cancelled else "COMPLETED" if exit_code==0 else "FAILED"
        self.db.update_job(job_id,state=state,completed_at=completed,exit_code=exit_code,timed_out=int(timed_out),cancelled=int(cancelled),summary=firewall.summary,evidence_handle=firewall.evidence_handle); self.db.append_completion(self.completions,{"job_id":job_id,"state":state,"exit_code":exit_code,"completed_at":completed,"evidence_handle":firewall.evidence_handle})
        return ProcessResult(job_id,exit_code,completed-started,timed_out,cancelled,firewall.summary,firewall.evidence_handle,stdout_path.stat().st_size,stderr_path.stat().st_size)
    @staticmethod
    def _terminate_tree(pid):
        try:
            if os.name=="nt": subprocess.run(["taskkill","/PID",str(pid),"/T"],stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL,timeout=10)
            else: os.killpg(pid,signal.SIGTERM)
        except (OSError,subprocess.SubprocessError): pass
    @staticmethod
    def _kill_tree(pid):
        try:
            if os.name=="nt": subprocess.run(["taskkill","/PID",str(pid),"/T","/F"],stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL,timeout=10)
            else: os.killpg(pid,signal.SIGKILL)
        except (OSError,subprocess.SubprocessError): pass
    def show(self,job_id):
        row=self.db.job(job_id)
        if not row: raise KeyError(job_id)
        record=_job_from_row(row); worker=self._workers.get(job_id)
        if worker is not None and record.state in FINAL_STATES:
            try: worker.wait(timeout=1.0)
            except subprocess.TimeoutExpired: pass
            if worker.poll() is not None: self._workers.pop(job_id,None)
        return record
    def list_jobs(self,*,states=(),limit=100): return [_job_from_row(row) for row in self.db.jobs(states=states,limit=limit)]
    def cancel(self,job_id):
        job=self.show(job_id)
        if job.state in FINAL_STATES: return job
        marker=self.root/"jobs"/job_id/"cancel"; marker.touch(exist_ok=True)
        if job.pid: self._terminate_tree(job.pid)
        self.db.update_job(job_id,cancelled=1); return self.show(job_id)
    def recover(self):
        orphaned=[]
        for job in self.list_jobs(states=("QUEUED","RUNNING"),limit=10000):
            if not job.pid or not self._pid_alive(job.pid): self.db.update_job(job.job_id,state="ORPHANED",completed_at=time.time(),error="worker or process disappeared"); orphaned.append(self.show(job.job_id))
        return orphaned
    @staticmethod
    def _pid_alive(pid):
        try:
            if os.name=="nt": return str(pid) in subprocess.run(["tasklist","/FI",f"PID eq {pid}"],capture_output=True,text=True,timeout=5).stdout
            os.kill(pid,0); return True
        except (OSError,subprocess.SubprocessError): return False
