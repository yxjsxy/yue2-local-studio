"""Manage the YuE2 interactive service, identifying only this project's process."""
import argparse
import json
import subprocess
import sys
import time
import urllib.request
from pathlib import Path
import psutil

root = Path(__file__).resolve().parent
state = root / "evidence/studio-server.json"
parser = argparse.ArgumentParser()
parser.add_argument("action", choices=["start", "stop", "status"])
args = parser.parse_args()
proc = None
data = json.loads(state.read_text()) if state.exists() else None
if data:
    try:
        candidate = psutil.Process(data["pid"])
        if (str(root / "studio_server.py") in candidate.cmdline()
                and abs(candidate.create_time() - data["process_create_time"]) < .01):
            proc = candidate
    except (psutil.NoSuchProcess, psutil.AccessDenied):
        pass
if args.action == "status":
    print(json.dumps({"running": bool(proc), "server": data}, indent=2))
elif args.action == "stop":
    if proc:
        proc.terminate()
        proc.wait(timeout=12)
    print(json.dumps({"running": False, "stopped_verified_process": bool(proc)}))
elif proc:
    print(json.dumps(data, indent=2))
else:
    command = [sys.executable, "-u", str(root / "studio_server.py"), "--port", "8766"]
    with (root / "evidence/studio-server.log").open("ab") as log:
        child = subprocess.Popen(command, cwd=root, stdin=subprocess.DEVNULL,
                                 stdout=log, stderr=subprocess.STDOUT, start_new_session=True)
    data = {"pid": child.pid, "process_create_time": psutil.Process(child.pid).create_time(),
            "url": "http://127.0.0.1:8766/", "command": command}
    state.write_text(json.dumps(data, indent=2))
    for _ in range(40):
        if child.poll() is not None:
            raise SystemExit("服务未启动，请检查端口8766是否占用及 evidence/studio-server.log。")
        try:
            with urllib.request.urlopen(data["url"] + "api/state", timeout=1) as r:
                payload = json.loads(r.read())
                if r.status == 200 and "token" in payload:
                    print(json.dumps(data, indent=2))
                    break
        except OSError:
            time.sleep(.1)
    else:
        child.terminate()
        raise SystemExit("服务启动超时。")
