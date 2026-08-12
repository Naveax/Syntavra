#!/usr/bin/env python3
from __future__ import annotations

import argparse
import hashlib
import json
import os
import sqlite3
import subprocess
import sys
import tempfile
import traceback
from pathlib import Path
from typing import Any

from syntavra_runtime.host_adapters import KNOWN_HOSTS
from tools import report_missing_native_public_routes as public_surface
from tools import report_python_public_execution_contract as execution_contract

FIXTURE_RELATIVE = Path("contracts/python/setup-host-reference-v1.json")


def _head(repo: Path) -> str:
    proc = subprocess.run(["git", "rev-parse", "HEAD"], cwd=repo, text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=False)
    return proc.stdout.strip() if proc.returncode == 0 else ""


def _run(repo: Path, project: Path, state: Path, argv: list[str], *, home: Path | None = None) -> dict[str, Any]:
    env = os.environ.copy()
    env.update({"PYTHONPATH": str(repo), "PYTHONIOENCODING": "utf-8", "PYTHONUTF8": "1"})
    if home is not None:
        env["HOME"] = str(home)
        env["USERPROFILE"] = str(home)
        env["CODEX_HOME"] = str(home / ".codex")
    env.pop("SYNTAVRA_BULK_PARITY_PROBE", None)
    proc = subprocess.run(
        [sys.executable, "-m", "syntavra_runtime.engine_entry", "--engine", "python", "--project", str(project), "--state-root", str(state), *argv],
        cwd=repo, env=env, text=True, encoding="utf-8", errors="replace",
        stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=90, check=False,
    )
    try:
        value = json.loads(proc.stdout) if proc.stdout.strip() else None
    except json.JSONDecodeError:
        value = None
    return {"exit": proc.returncode, "stdout": proc.stdout, "stderr": proc.stderr, "value": value}


def _json(label: str, result: dict[str, Any], exit_code: int = 0) -> Any:
    if result["exit"] != exit_code or result["stderr"]:
        raise AssertionError(f"{label}: expected clean exit {exit_code}, got {result}")
    if not isinstance(result["value"], (dict, list)):
        raise AssertionError(f"{label}: expected JSON object/list stdout, got {result}")
    return result["value"]


def _error4(label: str, result: dict[str, Any]) -> dict[str, Any]:
    value = _json(label, result, 4)
    if not isinstance(value, dict) or value.get("ok") is not False or value.get("error", {}).get("code") != "PYTHON_PUBLIC_COMMAND_FAILED":
        raise AssertionError(f"{label}: public error envelope drift: {value}")
    return value


def _routes(fixture: dict[str, Any]) -> dict[str, Any]:
    expected = fixture["public_routes"]
    observed = sorted(route for route in public_surface.python_public_route_sources() if route in set(expected))
    if observed != expected:
        raise AssertionError(f"setup/host route inventory drift: observed={observed}, missing={sorted(set(expected)-set(observed))}")
    execution = {row["route"]: row for row in execution_contract.route_execution_manifest()}
    owners = {}
    for route in expected:
        row = execution.get(route)
        if not row or len(row["entrypoints"]) != 1 or row["unknown_sources"]:
            raise AssertionError(f"setup/host execution ownership drift: {row}")
        owners[route] = row["entrypoint"]
    return {"routes": expected, "route_count": len(expected), "route_sha256": public_surface._digest(expected), "ownership": owners}


def _bootstrap_setup(repo: Path, root: Path, fixture: dict[str, Any]) -> dict[str, Any]:
    project, state, home = root / "setup-project", root / "setup-state", root / "setup-home"
    project.mkdir(); state.mkdir(); home.mkdir(); (project / ".git").mkdir(); (project / ".codex").mkdir()
    dry = _json("setup dry-run", _run(repo, project, state, ["setup"], home=home))
    if dry.get("ok") is not True or dry.get("dry_run") is not True: raise AssertionError(f"setup dry-run drift: {dry}")
    if (state / "config.json").exists() or (project / ".agents" / "skills" / "syntavra").exists(): raise AssertionError("setup dry-run mutated the temporary target")
    plan = dry.get("plan") or {}
    if plan.get("mental_model") != fixture["setup_mental_model"] or plan.get("detected_hosts") != ["codex"] or plan.get("installable_hosts") != ["codex"]: raise AssertionError(f"setup detection/plan drift: {dry}")
    applied = _json("setup apply", _run(repo, project, state, ["setup", "--apply"], home=home))
    if applied.get("ok") is not True or applied.get("dry_run") is not False or applied.get("profile") != "minimal" or len(applied.get("host_transactions") or []) != 1: raise AssertionError(f"setup apply drift: {applied}")
    if not (state / "config.json").is_file() or not (state / "product.json").is_file() or not (project / ".codex" / "config.toml").is_file(): raise AssertionError("setup apply did not materialize isolated files")
    status = _json("status", _run(repo, project, state, ["status"], home=home)); doctor = _json("doctor", _run(repo, project, state, ["doctor"], home=home))
    if status.get("product") != "Syntavra" or doctor.get("ok") is not True or doctor.get("installed") is not True: raise AssertionError(f"status/doctor drift: {status} / {doctor}")
    if doctor.get("configured_hosts") != ["codex"] or doctor.get("runtime", {}).get("state") != "PRE_RELEASE_INSTALLED": raise AssertionError(f"doctor host/runtime drift: {doctor}")
    compat = _json("install default dry-run", _run(repo, project, state, ["install"], home=home))
    if compat.get("ok") is not True or compat.get("dry_run") is not True: raise AssertionError(f"compat install drift: {compat}")
    product_path = state / "product.json"; product_path.unlink(); diagnosis = _json("repair diagnosis", _run(repo, project, state, ["repair"], home=home)); repaired = _json("repair apply", _run(repo, project, state, ["repair", "--apply"], home=home))
    if diagnosis.get("apply") is not False or "syntavra repair --apply" not in (diagnosis.get("actions") or []) or repaired.get("ok") is not True or not product_path.is_file(): raise AssertionError(f"repair drift: {diagnosis} / {repaired}")
    upgraded = _json("upgrade", _run(repo, project, state, ["upgrade"], home=home))
    if upgraded.get("ok") is not True or upgraded.get("changed") is not False or upgraded.get("reason") != fixture["upgrade_reason"]: raise AssertionError(f"upgrade lock drift: {upgraded}")
    invalid_upgrade = _error4("upgrade invalid target", _run(repo, project, state, ["upgrade", "--target", "99.99.99"], home=home))
    wrapper = root / "wrappers" / "codex-wrapper"; wrapped = _json("wrap", _run(repo, project, state, ["wrap", "codex", "--output", str(wrapper)], home=home))
    if wrapped.get("ok") is not True or not wrapper.is_file() or "SYNTAVRA_HOST" not in wrapper.read_text(encoding="utf-8"): raise AssertionError(f"wrapper drift: {wrapped}")
    bad_wrapper = _error4("wrap unknown", _run(repo, project, state, ["wrap", "not-a-host", "--output", str(root / "bad-wrapper")], home=home))
    initialized = _json("init", _run(repo, project, state, ["--skill-root", str(repo / "skills" / "syntavra"), "--codex-home", str(home / ".codex"), "init", "fixture task"], home=home)); session = initialized.get("session") or {}; health = initialized.get("health") or {}; session_file = state / "sessions" / str(session.get("session_id")) / "session.json"
    if session.get("task") != "fixture task" or session.get("project") != str(project) or health.get("state") not in {"RUNTIME_ACTIVE", "RUNTIME_DEGRADED"} or not session_file.is_file(): raise AssertionError(f"init bootstrap drift: {initialized}")
    uninstall = _json("uninstall dry-run", _run(repo, project, state, ["--skill-root", str(repo / "skills" / "syntavra"), "uninstall", "--home", str(home), "--dry-run"], home=home))
    if uninstall.get("ok") is not True or uninstall.get("changes") != [] or uninstall.get("reason") != "not-installed": raise AssertionError(f"uninstall no-install drift: {uninstall}")
    return {"dry_run":{"ok":dry["ok"],"dry_run":dry["dry_run"],"detected_hosts":plan["detected_hosts"],"installable_hosts":plan["installable_hosts"],"mental_model":plan["mental_model"],"mutated_target":False},"applied":{"ok":applied["ok"],"dry_run":applied["dry_run"],"host_transaction_count":len(applied["host_transactions"]),"config_exists":(state/"config.json").is_file(),"codex_config_exists":(project/".codex"/"config.toml").is_file()},"doctor":{"ok":doctor["ok"],"installed":doctor["installed"],"configured_hosts":doctor["configured_hosts"],"runtime_state":doctor["runtime"]["state"]},"compat_install_default_dry_run":compat["dry_run"],"repair":{"diagnosed_action":"syntavra repair --apply" in diagnosis["actions"],"apply_ok":repaired["ok"]},"upgrade":{"changed":upgraded["changed"],"reason":upgraded["reason"],"invalid_target_exit":4,"invalid_target_error_type":invalid_upgrade["error"]["details"]["error"].split(":",1)[0]},"wrapper":{"created":wrapper.is_file(),"unknown_host_exit":4,"unknown_host_error_type":bad_wrapper["error"]["details"]["error"].split(":",1)[0]},"init":{"state":health["state"],"session_persisted":session_file.is_file()},"uninstall_not_installed":{"ok":uninstall["ok"],"reason":uninstall["reason"],"changes":uninstall["changes"]}}


def _host_detection(repo: Path, root: Path, fixture: dict[str, Any]) -> dict[str, Any]:
    project, state, home = root / "host-project", root / "host-state", root / "host-home"; project.mkdir(); state.mkdir(); home.mkdir(); (project/".git").mkdir(); (project/".codex").mkdir(); (home/".claude").mkdir()
    implicit=_json("host implicit",_run(repo,project,state,["host"],home=home)); codex=_json("host codex",_run(repo,project,state,["host","negotiate","codex"],home=home)); claude=_json("host claude",_run(repo,project,state,["host","negotiate","claude-code"],home=home))
    if implicit!=codex or codex.get("capabilities",{}).get("host")!="codex" or claude.get("capabilities",{}).get("host")!="claude-code": raise AssertionError(f"host negotiation drift: {implicit} / {codex} / {claude}")
    detected=_json("host detect",_run(repo,project,state,["host","detect","--home",str(home)],home=home)); rows=detected.get("hosts") or []; by_host={row.get("host"):row for row in rows if isinstance(row,dict)}
    for name in fixture["selected_hosts"]:
        if name not in by_host: raise AssertionError(f"host detection missing {name}: {detected}")
    if not by_host["codex"].get("project_markers") or not by_host["claude-code"].get("user_markers"): raise AssertionError(f"host marker detection drift: {detected}")
    capabilities=_json("host capabilities",_run(repo,project,state,["host","capabilities"],home=home))
    coverage = capabilities.get("coverage") if isinstance(capabilities, dict) else None
    host_map = capabilities.get("hosts") if isinstance(capabilities, dict) else None
    if not isinstance(capabilities.get("platform"),str) or not isinstance(coverage,dict) or not isinstance(host_map,dict): raise AssertionError(f"host capability registry schema drift: {capabilities}")
    if sorted(capabilities) != ["coverage", "hosts", "platform"]: raise AssertionError(f"host capability top-level key drift: {sorted(capabilities)}")
    if coverage.get("hosts") != len(host_map) or len(host_map) != len(KNOWN_HOSTS): raise AssertionError(f"host capability count drift: {coverage} / {len(host_map)} / {len(KNOWN_HOSTS)}")
    if coverage.get("claim_boundary") != "registry coverage is implementation coverage, not live host certification": raise AssertionError(f"host capability claim-boundary drift: {coverage}")
    if host_map.get("codex",{}).get("host") != "codex" or host_map.get("claude-code",{}).get("host") != "claude-code": raise AssertionError(f"selected host capability drift: {host_map.get('codex')} / {host_map.get('claude-code')}")
    return {"known_host_count":len(KNOWN_HOSTS),"known_host_sha256":hashlib.sha256("\n".join(sorted(KNOWN_HOSTS)).encode()).hexdigest(),"implicit_codex_equal":True,"codex":{"mode":codex["mode"],"enforced":codex["enforced"],"host":codex["capabilities"]["host"]},"claude_code":{"mode":claude["mode"],"enforced":claude["enforced"],"host":claude["capabilities"]["host"]},"detected_selected":{"codex_project_markers":list(by_host["codex"].get("project_markers") or []),"claude_user_markers":list(by_host["claude-code"].get("user_markers") or [])},"capability_schema_keys":sorted(capabilities),"capability_registry":{"host_count":len(host_map),"controlled_hosts":coverage.get("controlled_hosts"),"verified_hosts":coverage.get("verified_hosts"),"stream_capture_hosts":coverage.get("stream_capture_hosts"),"coverage":coverage.get("coverage"),"tiers":coverage.get("tiers"),"claim_boundary":coverage.get("claim_boundary")},"platform_dynamic":capabilities["platform"]}


def _fabric_install(repo: Path, root: Path) -> dict[str, Any]:
    project,state,home=root/"fabric-project",root/"fabric-state",root/"fabric-home"; project.mkdir();state.mkdir();home.mkdir();(project/".git").mkdir(); skill_root=repo/"skills"/"syntavra"; common=["--skill-root",str(skill_root),"--home",str(home)]
    plan=_json("fabric plan",_run(repo,project,state,["fabric","platform-plan","--host-name","codex"],home=home))
    if plan.get("host")!="codex" or plan.get("scope")!="project" or plan.get("project")!=str(project): raise AssertionError(f"fabric plan drift: {plan}")
    dry=_json("fabric dry",_run(repo,project,state,["fabric","install","codex",*common,"--dry-run"],home=home))
    if dry.get("status")!="dry-run" or dry.get("verification",{}).get("dry_run") is not True: raise AssertionError(f"fabric dry-run drift: {dry}")
    if (project/".codex"/"config.toml").exists() or (project/".agents"/"skills"/"syntavra").exists(): raise AssertionError("fabric dry-run mutated target")
    applied=_json("fabric apply",_run(repo,project,state,["fabric","install","codex",*common],home=home)); txid=str(applied.get("transaction_id") or "")
    if applied.get("status")!="applied" or applied.get("verification",{}).get("ok") is not True or not txid.startswith("host-"): raise AssertionError(f"fabric apply drift: {applied}")
    verified=_json("fabric verify",_run(repo,project,state,["fabric","verify-install","codex",*common],home=home));
    if verified.get("ok") is not True or verified.get("reasons")!=[]: raise AssertionError(f"fabric verify drift: {verified}")
    transactions=_json("fabric installations",_run(repo,project,state,["fabric","installations","--host-name","codex",*common],home=home))
    if not isinstance(transactions,list) or len(transactions)!=1 or transactions[0].get("transaction_id")!=txid or transactions[0].get("status")!="applied": raise AssertionError(f"fabric listing drift: {transactions}")
    rolled=_json("fabric rollback",_run(repo,project,state,["fabric","rollback-install",txid,*common],home=home))
    if rolled.get("status")!="rolled-back" or rolled.get("verification")!={"ok":True,"rolled_back":True}: raise AssertionError(f"fabric rollback drift: {rolled}")
    after=_json("fabric verify after rollback",_run(repo,project,state,["fabric","verify-install","codex",*common],home=home),3)
    if after.get("ok") is not False or not {"missing-config","missing-skill"}.issubset(set(after.get("reasons") or [])): raise AssertionError(f"fabric post-rollback verify drift: {after}")
    rolled_again=_json("fabric rollback idempotent",_run(repo,project,state,["fabric","rollback-install",txid,*common],home=home)); missing=_error4("fabric rollback unknown",_run(repo,project,state,["fabric","rollback-install","host-missing",*common],home=home)); unsupported=_error4("fabric unsupported",_run(repo,project,state,["fabric","install","not-a-host",*common,"--dry-run"],home=home)); doctor=_json("fabric doctor",_run(repo,project,state,["fabric","doctor"],home=home))
    with sqlite3.connect(state/"host-installations.sqlite3") as db: row=db.execute("SELECT status FROM host_install_transactions WHERE transaction_id=?",(txid,)).fetchone()
    if row is None or row[0]!="rolled-back": raise AssertionError("fabric durable rollback state drift")
    return {"plan":{"host":plan["host"],"scope":plan["scope"],"mode":plan["mode"],"verified_adapter":plan["verified_adapter"]},"dry_run":{"status":dry["status"],"change_count":len(dry.get("changes") or []),"mutated_target":False},"apply":{"status":applied["status"],"verification_ok":applied["verification"]["ok"],"transaction_id_shape":txid.startswith("host-")},"verify":{"ok":verified["ok"],"reasons":verified["reasons"]},"transactions":{"count":len(transactions),"status":transactions[0]["status"]},"rollback":{"status":rolled["status"],"durable_status":row[0],"idempotent_status":rolled_again["status"]},"post_rollback_verify":{"exit":3,"ok":after["ok"],"reasons":after["reasons"]},"negative":{"missing_rollback_exit":4,"missing_rollback_error_type":missing["error"]["details"]["error"].split(":",1)[0],"unsupported_host_exit":4,"unsupported_host_error_type":unsupported["error"]["details"]["error"].split(":",1)[0]},"doctor_keys":sorted(doctor)}


def _updates(repo: Path, root: Path) -> dict[str, Any]:
    project,state,home=root/"update-project",root/"update-state",root/"update-home"; project.mkdir();state.mkdir();home.mkdir();(project/".git").mkdir(); b1,b2=b"syntavra-update-fixture-v1\n",b"syntavra-update-fixture-v2\n"; p1,p2=project/"update-v1.bin",project/"update-v2.bin";p1.write_bytes(b1);p2.write_bytes(b2);h1,h2=hashlib.sha256(b1).hexdigest(),hashlib.sha256(b2).hexdigest()
    def artifact(path:Path,digest:str)->str:return json.dumps({"platform":"fixture","architecture":"fixture","filename":path.name,"sha256":digest,"size":path.stat().st_size},separators=(",",":"))
    first=_json("update first",_run(repo,project,state,["run","update-install",p1.name,artifact(p1,h1),"--name","fixture-tool"],home=home)); second=_json("update second",_run(repo,project,state,["run","update-install",p2.name,artifact(p2,h2),"--name","fixture-tool"],home=home))
    if first.get("ok") is not True or first.get("installed_sha256")!=h1 or second.get("ok") is not True or second.get("previous")!=h1 or second.get("installed_sha256")!=h2: raise AssertionError(f"update install drift: {first} / {second}")
    rolled=_json("update rollback",_run(repo,project,state,["run","update-rollback","--name","fixture-tool","--sha256",h1],home=home));target=Path(rolled.get("target",""))
    if rolled.get("ok") is not True or rolled.get("sha256")!=h1 or not target.is_file() or target.read_bytes()!=b1: raise AssertionError(f"update rollback drift: {rolled}")
    no_backup=_json("update no backup",_run(repo,project,state,["run","update-rollback","--name","missing-tool"],home=home),3)
    if no_backup!={"ok":False,"reason":"no matching backup"}: raise AssertionError(f"update missing-backup drift: {no_backup}")
    malformed=_error4("update malformed",_run(repo,project,state,["run","update-install",p1.name,"{}","--name","bad-tool"],home=home));bad=_json("update bad checksum",_run(repo,project,state,["run","update-install",p1.name,artifact(p1,"0"*64),"--name","bad-checksum"],home=home),3)
    if bad.get("ok") is not False or bad.get("status") not in {"failed","rolled-back"} or "checksum mismatch" not in str(bad.get("detail")): raise AssertionError(f"update checksum drift: {bad}")
    return {"first":{"ok":first["ok"],"status":first["status"],"installed_sha256":first["installed_sha256"],"previous":first["previous"]},"second":{"ok":second["ok"],"status":second["status"],"installed_sha256":second["installed_sha256"],"previous":second["previous"]},"rollback":{"ok":rolled["ok"],"sha256":rolled["sha256"],"exact_restore":target.read_bytes()==b1},"missing_backup":{"exit":3,"value":no_backup},"malformed_artifact":{"exit":4,"error_type":malformed["error"]["details"]["error"].split(":",1)[0]},"bad_checksum":{"exit":3,"ok":bad["ok"],"status":bad["status"],"detail_has_checksum_mismatch":"checksum mismatch" in bad["detail"]},"target_under_temp_root":str(target).startswith(str(root))}


def certify(repo: Path) -> dict[str, Any]:
    fixture_path=repo/FIXTURE_RELATIVE;fixture=json.loads(fixture_path.read_text(encoding="utf-8"))
    with tempfile.TemporaryDirectory(prefix="syntavra-python-setup-host-") as directory:
        root=Path(directory); result={"routes":_routes(fixture),"bootstrap_setup":_bootstrap_setup(repo,root,fixture),"host_detection":_host_detection(repo,root,fixture),"fabric_install":_fabric_install(repo,root),"updates":_updates(repo,root)}
    return {"ok":True,"schema_version":1,"family":"setup-host","engine":"python","exact_head":_head(repo),"fixture":str(FIXTURE_RELATIVE).replace("\\","/"),"fixture_sha256":hashlib.sha256(fixture_path.read_bytes()).hexdigest(),**result,"exit_policy":fixture["exit_policy"],"safety":fixture["safety"],"ownership_notes":fixture["ownership_notes"],"nondeterministic_fields":["bootstrap session_id and started_at","install/update transaction and receipt identifiers","install/update timestamps and wall_time_ms","host capability platform field","temporary project/home/state paths"]}


def main()->int:
    parser=argparse.ArgumentParser(description="Certify Python setup/repair/host reference behavior");parser.add_argument("--repo",default=str(Path(__file__).resolve().parents[1]));parser.add_argument("--output");args=parser.parse_args();repo=Path(args.repo).resolve(strict=True)
    try:result=certify(repo)
    except Exception as exc:result={"ok":False,"schema_version":1,"family":"setup-host","engine":"python","exact_head":_head(repo),"error":f"{type(exc).__name__}: {exc}","traceback":traceback.format_exc()}
    rendered=json.dumps(result,ensure_ascii=False,indent=2,sort_keys=True,default=str)
    if args.output:Path(args.output).write_text(rendered+"\n",encoding="utf-8")
    print(rendered);return 0 if result["ok"] else 2


if __name__=="__main__":raise SystemExit(main())
