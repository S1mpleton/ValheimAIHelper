"""Run the packaged service without source/Python in its cwd; exercise real HTTP."""
import argparse
import json
import os
from pathlib import Path
import socket
import subprocess
import tempfile
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
                assert health["version"] == "0.3.1", health
                req = urllib.request.Request(base + "/api/status", headers={"Authorization": "Bearer " + token})
                with opener.open(req) as r: status = json.load(r)
                assert status["mode"] == "stop" and not status["keys_present"]["openai"]
                with opener.open(base + "/") as r: assert b"Valheim" in r.read()
                with opener.open(base + "/app.js") as r: assert r.status == 200
                print("PASS: frozen runtime starts without Python/source in cwd; HTTP/auth/static/status")
            finally:
                if child.poll() is None: child.terminate()
                child.wait(timeout=10)


if __name__ == "__main__": main()
