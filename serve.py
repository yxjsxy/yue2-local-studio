"""Start/stop the local, read-only listening bundle with process identity checks."""
import argparse
import json
import socket
import subprocess
import sys
import time
import urllib.request
from pathlib import Path
import psutil

root = Path(__file__).resolve().parent
state = root / "evidence/server.json"
parser = argparse.ArgumentParser()
parser.add_argument("action", choices=["start", "stop", "status"])
args = parser.parse_args()

def existing():
    if not state.exists():
        return None, None
    data = json.loads(state.read_text())
    try:
        proc = psutil.Process(data["pid"])
        command = proc.cmdline()
        if (abs(proc.create_time() - data["process_create_time"]) < 0.01
                and "http.server" in command and str(root / "public") in command):
            return proc, data
    except (psutil.NoSuchProcess, psutil.AccessDenied):
        pass
    return None, data

proc, data = existing()
if args.action == "stop":
    if proc:
        proc.terminate()
        proc.wait(timeout=10)
    print(json.dumps({"running": False, "stopped_verified_process": bool(proc)}))
elif args.action == "status":
    print(json.dumps({"running": bool(proc), "server": data}, indent=2))
elif proc:
    print(json.dumps(data, indent=2))
else:
    if not (root / "public/index.html").exists():
        raise SystemExit("Create the listening bundle before starting the server")
    for port in range(8765, 8785):
        with socket.socket() as probe:
            try:
                probe.bind(("127.0.0.1", port))
                break
            except OSError:
                continue
    else:
        raise SystemExit("No available port in 8765–8784")
    command = [sys.executable, "-m", "http.server", str(port), "--bind", "127.0.0.1",
               "--directory", str(root / "public")]
    with (root / "evidence/server.log").open("ab") as log:
        child = subprocess.Popen(command, stdin=subprocess.DEVNULL, stdout=log,
                                 stderr=subprocess.STDOUT, start_new_session=True)
    data = {"pid": child.pid, "process_create_time": psutil.Process(child.pid).create_time(),
            "url": f"http://127.0.0.1:{port}/", "command": command,
            "scope": "read-only playback, loopback only; no model inference endpoint"}
    state.write_text(json.dumps(data, indent=2))
    for _ in range(30):
        if child.poll() is not None:
            raise SystemExit("Server exited; inspect evidence/server.log")
        try:
            with urllib.request.urlopen(data["url"], timeout=1) as response:
                if response.status == 200:
                    print(json.dumps(data, indent=2))
                    break
        except OSError:
            time.sleep(0.1)
    else:
        child.terminate()
        raise SystemExit("Server did not become ready")
