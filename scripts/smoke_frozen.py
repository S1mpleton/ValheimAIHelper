"""Run the packaged service without source/Python in its cwd; exercise real HTTP."""
import argparse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import os
from pathlib import Path
import socket
import subprocess
import tempfile
import threading
import time
import urllib.request


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("executable", type=Path)
    args = parser.parse_args()
    executable = args.executable.resolve()
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        port = listener.getsockname()[1]
    with tempfile.TemporaryDirectory(prefix="valheim-frozen-") as tmp:
        data = Path(tmp)
        token = "frozen-smoke-not-a-real-api-key-0123456789"
        (data / "bridge-token").write_text(token + "\n", encoding="utf-8")
        # Windows also verifies the parent-lifetime watcher can open the actual owner.
        argv = [str(executable), "--port", str(port), "--data-dir", str(data)]
        if os.name == "nt": argv += ["--parent-pid", str(os.getpid())]
        with (data / "service.log").open("wb") as log:
            child = subprocess.Popen(argv, cwd=data, stdout=log, stderr=subprocess.STDOUT)
            try:
                # loopback test must work without a corporate HTTP proxy
                opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
                base = f"http://127.0.0.1:{port}"
                deadline = time.monotonic() + 30
                while True:
                    if child.poll() is not None: raise RuntimeError((data / "service.log").read_text(errors="replace"))
                    try:
                        with opener.open(base + "/health", timeout=1) as r: health = json.load(r)
                        break
                    except OSError:
                        if time.monotonic() > deadline: raise
                        time.sleep(.2)
                assert health["version"] == "0.7.0", health
                req = urllib.request.Request(base + "/api/status", headers={"Authorization": "Bearer " + token})
                with opener.open(req) as r: status = json.load(r)
                assert status["mode"] == "stop" and not status["keys_present"]["openai"]
                chat = urllib.request.Request(base + "/api/chat", data=json.dumps({
                    "actor_name":"Бъерн", "speaker":"Лидер", "text":"Привет"
                }).encode(), headers={"Authorization":"Bearer " + token,"Content-Type":"application/json"})
                with opener.open(chat) as r: assert not json.load(r)["handled"]
                with opener.open(base + "/") as r: assert b"Valheim" in r.read()
                with opener.open(base + "/app.js") as r: assert r.status == 200
                # Exercise the bundled native Ollama adapter through real HTTP.
                # A deterministic local stand-in keeps CI independent of models/GPU.
                requests=[]
                class LocalModel(BaseHTTPRequestHandler):
                    def log_message(self,*args): pass
                    def respond(self,value):
                        raw=json.dumps(value).encode()
                        self.send_response(200); self.send_header("Content-Type","application/json")
                        self.send_header("Content-Length",str(len(raw))); self.end_headers(); self.wfile.write(raw)
                    def do_GET(self):
                        assert self.path in ("/api/tags","/api/ps") and self.headers.get("Authorization") is None
                        requests.append(self.path); self.respond({"models":[{"name":"smoke-agent:local"}] if self.path=="/api/tags" else []})
                    def do_POST(self):
                        body=json.loads(self.rfile.read(int(self.headers.get("Content-Length","0"))))
                        assert self.headers.get("Authorization") is None
                        requests.append(self.path)
                        if self.path=="/api/show": self.respond({"capabilities":["completion"],"thinking":{"values":[False]}})
                        else:
                            assert self.path=="/api/chat" and body["think"] is False and body["stream"] is False
                            self.respond({"done":True,"message":{"content":json.dumps({"steps":[{"action":"wait","target_id":"","duration":1,"say":"OK"}]})}})
                def post(path,value):
                    request=urllib.request.Request(base+path,data=json.dumps(value).encode(),headers={"Authorization":"Bearer "+token,"Content-Type":"application/json"})
                    with opener.open(request,timeout=10) as response: return json.load(response)
                with ThreadingHTTPServer(("127.0.0.1",0),LocalModel) as server:
                    worker=threading.Thread(target=server.serve_forever,daemon=True);worker.start()
                    try:
                        local_url=f"http://127.0.0.1:{server.server_port}"
                        assert post("/api/provider",{"provider":"ollama","model":"smoke-agent:local","ollama_url":local_url})["ok"]
                        assert post("/api/models/ollama",{})["models"]==["smoke-agent:local"]
                        assert post("/api/check/ollama",{"model":"smoke-agent:local"})["ok"]
                        assert requests==["/api/ps","/api/tags","/api/show","/api/chat"]
                        def get(path):
                            with opener.open(urllib.request.Request(base+path,headers={"Authorization":"Bearer "+token}),timeout=10) as r:return json.load(r)
                        dialogue=get("/api/dialogue");id=dialogue["entries"][0]["id"]
                        assert get(f"/api/dialogue/{id}?part=request")["part"]=="request"
                        assert '"steps"' in get(f"/api/dialogue/{id}?part=response")["text"]
                        assert "Valheim AI Helper" in get(f"/api/dialogue/{id}/export")["text"]
                        assert post("/api/dialogue/clear",{})["ok"] and not get("/api/dialogue")["entries"]
                    finally:
                        server.shutdown();worker.join(timeout=3)
                print("PASS: frozen runtime starts without Python/source in cwd; HTTP/auth/static/status/ordinary-chat")
                print("PASS: bundled Ollama adapter -> local HTTP models/show/chat -> validated JSON plan, no key, think=false")
            finally:
                if child.poll() is None: child.terminate()
                child.wait(timeout=10)


if __name__ == "__main__": main()
