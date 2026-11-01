"""Real HTTP smoke test; no game, API credentials or paid model calls needed."""
import json
import os
from pathlib import Path
import socket
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request


def main():
    root = Path(__file__).resolve().parents[1]
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        port = sock.getsockname()[1]
    with tempfile.TemporaryDirectory(prefix="valheim-ai-smoke-") as directory:
        env = dict(os.environ)
        env["PYTHONPATH"] = str(root / "bridge/src") + os.pathsep + env.get("PYTHONPATH", "")
        process = subprocess.Popen(
            [sys.executable, "-m", "valheim_ai", "--port", str(port), "--data-dir", directory],
            cwd=root, env=env, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
        )
        try:
            # Explicitly direct only this loopback probe; never change the service's
            # inherited proxy/trust configuration for external API requests.
            opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
            base = f"http://127.0.0.1:{port}"
            def request(path, value=None, token=None):
                headers = {"Content-Type": "application/json"}
                if token:
                    headers["Authorization"] = "Bearer " + token
                req = urllib.request.Request(base + path, headers=headers,
                    data=json.dumps(value).encode() if value is not None else None)
                with opener.open(req, timeout=2) as r:
                    return r.read()
            for _ in range(50):
                if process.poll() is not None:
                    raise RuntimeError("Bridge exited before readiness")
                try:
                    assert json.loads(request("/health"))["status"] == "ok"
                    break
                except (urllib.error.URLError, TimeoutError):
                    time.sleep(.1)
            else:
                raise RuntimeError("Bridge failed to become ready")
            assert b"Valheim" in request("/")
            token = (Path(directory) / "bridge-token").read_text().strip()
            assert json.loads(request("/api/status", token=token))["mode"] == "stop"
            snapshot = {
                "session_id": "smoke", "sequence": 1,
                "actor": {"id": "bot", "name": "Test bot", "health": 25, "max_health": 25,
                    "stamina": 50, "position": {"x": 0, "y": 0, "z": 0}, "inventory": []},
                "entities": [{"id": "leader", "name": "Leader", "kind": "player", "distance": 5,
                    "position": {"x": 5, "y": 0, "z": 0}}], "leader_id": "leader",
            }
            assert json.loads(request("/api/decision", snapshot, token))["action"] == "wait"
            request("/api/command", {"mode": "follow"}, token)
            snapshot["sequence"] = 2
            following = json.loads(request("/api/decision", snapshot, token))
            assert following["action"] == "follow" and following["target_id"] == "leader"
            request("/api/command", {"mode": "stop"}, token)
            snapshot["sequence"] = 3
            assert json.loads(request("/api/decision", snapshot, token))["action"] == "wait"
            print("PASS: real bridge startup, dashboard, pairing, observation, follow, stop; zero model calls")
        finally:
            process.terminate()
            try:
                process.communicate(timeout=5)
            except subprocess.TimeoutExpired:
                process.kill()
                process.communicate()


if __name__ == "__main__":
    main()
