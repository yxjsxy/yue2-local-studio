"""Run only the supplied local experiment with a hard subprocess time budget."""
import argparse
import json
import subprocess
import time
from pathlib import Path

parser = argparse.ArgumentParser()
parser.add_argument("--timeout", type=int, required=True)
parser.add_argument("--log", required=True)
parser.add_argument("command", nargs=argparse.REMAINDER)
args = parser.parse_args()
command = args.command[1:] if args.command[:1] == ["--"] else args.command
if not command or args.timeout <= 0:
    parser.error("positive timeout and command required")
log = Path(args.log)
log.parent.mkdir(parents=True, exist_ok=True)
start = time.time()
try:
    with log.open("w") as stream:
        result = subprocess.run(command, stdout=stream, stderr=subprocess.STDOUT,
                                timeout=args.timeout)
    receipt = {"status": "exited", "returncode": result.returncode}
except subprocess.TimeoutExpired:
    receipt = {"status": "timeout", "returncode": 124}
receipt.update(elapsed_seconds=time.time() - start, limit_seconds=args.timeout,
               command=command)
log.with_suffix(".receipt.json").write_text(json.dumps(receipt, indent=2))
print(json.dumps(receipt), flush=True)
raise SystemExit(receipt["returncode"])
