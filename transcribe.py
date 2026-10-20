"""Transcribe a local generated song with pinned SheetSage2/MERT2 models."""
import json
import os
import resource
import sys
import time
import traceback
from pathlib import Path

root = Path(__file__).resolve().parent
os.environ["HF_HOME"] = str(root / ".hf-cache")
os.environ["HF_HUB_OFFLINE"] = "1"
os.environ["TOKENIZERS_PARALLELISM"] = "false"
import torch
from transformers import AutoModel

source = root / sys.argv[1]
out = root / sys.argv[2]
out.mkdir(parents=True, exist_ok=False)
start = time.monotonic()
torch.set_num_threads(8)
events = []


def progress(event):
    event = dict(event, elapsed_seconds=time.monotonic() - start)
    events.append(event)
    if event.get("stage") != "decoding" or event.get("tokens", 0) % 100 == 0:
        print(json.dumps(event), flush=True)
        (out / "progress.json").write_text(json.dumps(events[-100:], indent=2))


try:
    progress({"stage": "loading"})
    model = AutoModel.from_pretrained(
        root / "models/SheetSage2", trust_remote_code=True,
        base_model_path=root / "models/MERT-v2-FullSong",
        local_files_only=True, torch_dtype=torch.float32,
    ).eval().to("cpu")
    progress({"stage": "loaded"})
    result = model.transcribe(
        str(source), output_dir=out / "transcription", dtype="fp32",
        prompts=("timestamp", "downbeat_meter", "structure", "key", "melody_full"),
        melody_only=True, progress=progress,
    )
    if not result.get("abc") or result.get("abc_error"):
        raise RuntimeError(f"Unusable score: {result.get('abc_error')}")
    (out / "experiment_summary.json").write_text(json.dumps({
        "status": "complete", "source": str(source),
        "elapsed_seconds": time.monotonic() - start,
        "max_rss_bytes": resource.getrusage(resource.RUSAGE_SELF).ru_maxrss,
        "device": "cpu", "dtype": "float32", "warnings": result.get("warnings"),
        "melody_only": True, "input_cropped": False,
        "quality_note": "A valid transcription is not proof of exact musical accuracy.",
    }, ensure_ascii=False, indent=2))
    progress({"stage": "complete"})
except Exception as exc:
    (out / "failure.json").write_text(json.dumps({
        "type": type(exc).__name__, "message": str(exc),
        "traceback": traceback.format_exc(), "elapsed_seconds": time.monotonic() - start,
    }, ensure_ascii=False, indent=2))
    raise
