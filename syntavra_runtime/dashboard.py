from __future__ import annotations

import json
import mimetypes
import threading
import time
import urllib.parse
import webbrowser
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, Callable

from .agent_config_auditor import AgentConfigAuditor
from .background_workers import BackgroundIntelligenceWorker
from .memory_intelligence import MemoryIntelligenceStore
from .notifications import NotificationFeed
from .optimization_modes import OptimizationModeStore, SavingsLedger, render_statusline
from .prompt_cache_optimizer import PromptCacheOptimizer


INDEX_HTML = """<!doctype html><html><head><meta charset=utf-8><meta name=viewport content='width=device-width,initial-scale=1'><link rel=manifest href=/manifest.webmanifest><title>Syntavra Dashboard</title><style>body{font:15px system-ui;margin:0;background:#111;color:#eee}header{padding:20px;background:#191919;position:sticky;top:0}main{display:grid;grid-template-columns:repeat(auto-fit,minmax(280px,1fr));gap:16px;padding:16px}.card{background:#1d1d1d;border:1px solid #333;border-radius:12px;padding:16px}pre{white-space:pre-wrap;word-break:break-word}button{padding:8px 12px;margin:4px;border-radius:8px;border:1px solid #555;background:#292929;color:#fff}</style></head><body><header><h1>Syntavra 0.0.1</h1><div id=badge>loading…</div></header><main><section class=card><h2>Savings</h2><pre id=savings></pre></section><section class=card><h2>Cache</h2><pre id=cache></pre></section><section class=card><h2>Memory</h2><pre id=memory></pre></section><section class=card><h2>Notifications</h2><pre id=notifications></pre></section><section class=card><h2>Agent Config Audit</h2><pre id=audit></pre></section></main><script>if('serviceWorker'in navigator)navigator.serviceWorker.register('/sw.js');async function load(id,url){let r=await fetch(url);document.getElementById(id).textContent=JSON.stringify(await r.json(),null,2)}async function all(){let s=await(await fetch('/api/status')).json();document.getElementById('badge').textContent=s.statusline;for(let x of [['savings','/api/savings'],['cache','/api/cache'],['memory','/api/memory'],['notifications','/api/notifications'],['audit','/api/config-audit']])load(...x)}all();setInterval(all,5000)</script></body></html>"""
MANIFEST = {"name":"Syntavra Dashboard","short_name":"Syntavra","start_url":"/","display":"standalone","background_color":"#111111","theme_color":"#191919"}
SERVICE_WORKER = "self.addEventListener('install',e=>self.skipWaiting());self.addEventListener('fetch',e=>e.respondWith(fetch(e.request).catch(()=>new Response('offline',{status:503}))));"


class LocalDashboard:
    def __init__(self, *, project: Path, state_root: Path):
        self.project=Path(project).resolve(strict=True); self.state_root=Path(state_root)
        self.memory=MemoryIntelligenceStore(self.state_root/"memory-intelligence.sqlite3",notification_feed=NotificationFeed(self.state_root))

    def snapshot(self) -> dict[str,Any]:
        return {"statusline":render_statusline(self.state_root),"mode":OptimizationModeStore(self.state_root).manifest(),"savings":SavingsLedger(self.state_root).summary(),"cache":PromptCacheOptimizer(self.state_root).health(),"memory":self.memory.stats(),"worker":BackgroundIntelligenceWorker(project=self.project,state_root=self.state_root).status(),"notifications":len(NotificationFeed(self.state_root).recent()),"timestamp":time.time()}

    def handler(self) -> type[BaseHTTPRequestHandler]:
        dashboard=self
        class Handler(BaseHTTPRequestHandler):
            def _json(self,value:Any,status:int=200)->None:
                data=json.dumps(value,ensure_ascii=False,sort_keys=True,default=str).encode("utf-8"); self.send_response(status); self.send_header("Content-Type","application/json; charset=utf-8"); self.send_header("Content-Length",str(len(data))); self.send_header("Cache-Control","no-store"); self.end_headers(); self.wfile.write(data)
            def _text(self,value:str,content_type:str="text/html; charset=utf-8")->None:
                data=value.encode("utf-8"); self.send_response(200); self.send_header("Content-Type",content_type); self.send_header("Content-Length",str(len(data))); self.end_headers(); self.wfile.write(data)
            def do_GET(self)->None:
                path=urllib.parse.urlparse(self.path).path
                if path=="/": return self._text(INDEX_HTML)
                if path=="/manifest.webmanifest": return self._json(MANIFEST)
                if path=="/sw.js": return self._text(SERVICE_WORKER,"application/javascript; charset=utf-8")
                if path=="/api/status": return self._json(dashboard.snapshot())
                if path=="/api/savings": return self._json(SavingsLedger(dashboard.state_root).summary())
                if path=="/api/cache": return self._json(PromptCacheOptimizer(dashboard.state_root).health())
                if path=="/api/memory": return self._json({"stats":dashboard.memory.stats(),"ranked":dashboard.memory.ranked(limit=100)})
                if path=="/api/notifications": return self._json({"events":NotificationFeed(dashboard.state_root).recent(limit=100)})
                if path=="/api/config-audit": return self._json(AgentConfigAuditor(dashboard.project).audit())
                return self._json({"error":"not found"},404)
            def log_message(self,format:str,*args:Any)->None: return
        return Handler

    def serve(self, *, host: str="127.0.0.1", port: int=8788, open_browser: bool=False) -> ThreadingHTTPServer:
        if host not in {"127.0.0.1","localhost","::1"}: raise ValueError("dashboard is local-only")
        server=ThreadingHTTPServer((host,port),self.handler())
        if open_browser: webbrowser.open(f"http://{host}:{server.server_port}/")
        server.serve_forever(); return server

    def start_background(self, *, host: str="127.0.0.1", port: int=0) -> tuple[ThreadingHTTPServer,threading.Thread]:
        server=ThreadingHTTPServer((host,port),self.handler()); thread=threading.Thread(target=server.serve_forever,daemon=True,name="syntavra-dashboard"); thread.start(); return server,thread
