"""Local YuE2 experiment with explicit limits and recoverable stage outputs."""
import argparse
import fcntl
import json
import subprocess
import sys
import time
import traceback
from pathlib import Path

import numpy as np
import psutil
import torch
from yue2 import YuE2Pipeline
from yue2.pipeline import SongResult
from yue2.protocol import SongRequest, chunk_ranges
from yue2.storage import identity, collect_hashes, write_json, verify_result

parser = argparse.ArgumentParser()
parser.add_argument("--request", required=True)
parser.add_argument("--output", required=True)
parser.add_argument("--abc-file")
parser.add_argument("--max-seconds", type=int, default=3600)
parser.add_argument("--max-semantic-tokens", type=int, default=9000)
parser.add_argument("--max-abc-tokens", type=int, default=4096)
parser.add_argument("--target-seconds", type=int, default=0)
parser.add_argument("--attention", choices=["stable", "native"], default="stable")
args = parser.parse_args()
root = Path(__file__).resolve().parent
out = root / args.output
out.mkdir(parents=True, exist_ok=False)
started = time.time()
deadline = started + args.max_seconds
request_data = json.loads((root / args.request).read_text())
if args.abc_file:
    request_data["abc"] = (root / args.abc_file).read_text()
request = SongRequest(**request_data)
process = psutil.Process()
samples = []
counts = {}


def record(stage):
    item = {"stage": stage, "elapsed_seconds": time.time() - started,
            "rss_bytes": process.memory_info().rss,
            "available_memory_bytes": psutil.virtual_memory().available,
            "swap_used_bytes": psutil.swap_memory().used}
    if torch.backends.mps.is_available():
        item["mps_allocated_bytes"] = torch.mps.current_allocated_memory()
        item["mps_driver_bytes"] = torch.mps.driver_allocated_memory()
    samples.append(item)
    (out / "progress.json").write_text(json.dumps(samples, indent=2))
    print(json.dumps(item), flush=True)


def token(phase, value):
    counts[phase] = counts.get(phase, 0) + 1
    if counts[phase] % 250 == 0 and torch.backends.mps.is_available():
        torch.mps.empty_cache()  # Release unused temporary buffers, never the live KV context.
    if counts[phase] % 50 == 0:
        record(f"{phase}:{counts[phase]}")


def cancelled():
    return time.time() >= deadline


abc_sampling = {"max_tokens": args.max_abc_tokens}
if not 200 <= args.max_semantic_tokens <= 9000:
    raise ValueError("Semantic budget must be 200–9000 (native default 9000)")
if not 32 <= args.max_abc_tokens <= 8192:
    raise ValueError("Score budget must be 32–8192; context is checked by the model")
if args.target_seconds not in (0, 60, 120, 180, 210, 240):
    raise ValueError("Unsupported duration target")
semantic_sampling = {"max_tokens": args.max_semantic_tokens}
(out / "experiment.json").write_text(json.dumps({
    "request": request_data, "device": "mps", "backend": "torch-eager",
    "abc_sampling": abc_sampling, "semantic_sampling": semantic_sampling,
    "decoder_device": "cpu",
    "semantic_audio_cap_seconds": args.max_semantic_tokens / 25, "ode_steps": 32,
    "target_seconds": args.target_seconds,
    "note": "One complete request, one continuous semantic sequence. Native 9000-token budget by default; EOS remains enabled. No looping, concatenation or time stretching.",
}, ensure_ascii=False, indent=2))
try:
    lock_path = root / "evidence/inference.lock"
    lock_path.parent.mkdir(exist_ok=True)
    inference_lock = lock_path.open("a")
    try:
        fcntl.flock(inference_lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        raise RuntimeError("Another local generation is running; wait or cancel it first")
    if not torch.backends.mps.is_available():
        raise RuntimeError("This experiment requires verified MPS")
    torch.set_num_threads(6)
    torch.mps.set_per_process_memory_fraction(0.80)
    attention_adaptation = None
    if args.attention == "stable":
        from mps_attention import install
        attention_adaptation = install()
    record("start")
    with YuE2Pipeline.from_pretrained(
        root / "models/YuE2-3B", vae=root / "models/YuE2-Vae",
        device="mps", backend="torch-eager", local_files_only=True,
        memory_budget_gib=16, vae_core_frames=256, offload_ar=True,
        progress=True,
    ) as pipe:
        config = pipe.effective_config(request, abc_sampling, semantic_sampling)
        config["vae_device"] = "cpu"
        config["local_attention_adaptation"] = attention_adaptation
        config["mps_unused_cache_release_tokens"] = 250
        request_id = identity({"request": request.to_dict(), "config": config, "weights": pipe.weights})
        record("resolved")
        plan = pipe.plan(request=request, abc_sampling=abc_sampling,
                         cancelled=cancelled, on_token=token)
        plan.save(out / "plan")
        record("planned")
        if plan.truncated:
            raise RuntimeError("Score hit token limit; retained plan, refused to render incomplete score")
        if request.cot != "off":
            check = subprocess.run([sys.executable, str(root / "upstream/skills/yue2-music/scripts/abc_tools.py"),
                                    "inspect", str(out / "plan/score.abc")], capture_output=True, text=True, timeout=30)
            (out / "score-inspection.txt").write_text(check.stdout + check.stderr)
            if check.returncode:
                raise RuntimeError("Generated score failed structural validation; original plan retained. Try another seed or direct-audio mode. " + check.stderr[:300])
        semantic = pipe.generate_semantic(plan, sampling=semantic_sampling,
                                          cancelled=cancelled, on_token=token)
        np.save(out / "semantic_checkpoint.npy", np.asarray(semantic.tokens, dtype=np.int32))
        record("semantic_done")
        acoustic_chunks = chunk_ranges(len(semantic.tokens), len(plan.prefix))
        (out / "continuity.json").write_text(json.dumps({"semantic_calls": 1, "semantic_tokens": len(semantic.tokens), "prefix_tokens": len(plan.prefix), "acoustic_chunks": acoustic_chunks, "postprocessing": "none"}, indent=2))
        if len(acoustic_chunks) != 1:
            raise RuntimeError("Score and lyrics leave insufficient context for one acoustic sequence; retained semantic checkpoint, refused independent acoustic chunks")
        t = time.perf_counter()
        latents = pipe.synthesize(semantic, cancelled=cancelled)
        nar_seconds = time.perf_counter() - t
        np.save(out / "latent_checkpoint.npy", latents)
        record("synthesized")
        if cancelled():
            raise TimeoutError("Experiment budget reached before decoding")
        t = time.perf_counter()
        # Article's deployment screenshot explicitly describes GPU generation
        # plus CPU decoding as its Mac compatibility adaptation.
        generation_device = pipe.device
        pipe.device = torch.device("cpu")
        try:
            audio = pipe.decode(latents)
        finally:
            pipe.device = generation_device
        vae_seconds = time.perf_counter() - t
        timing = {"abc": plan.timing, "semantic": semantic.timing,
                  "nar_seconds": nar_seconds, "vae_seconds": vae_seconds,
                  "load": pipe.load_timing, "e2e_seconds": time.time() - started}
        result = SongResult(audio, 48000, semantic, latents, config,
                            pipe.weights, timing, request_id)
        result.save_artifacts(out)
        record("complete")
        metrics = {
            "duration_seconds": len(audio) / 48000,
            "sample_rate": 48000, "channels": int(audio.shape[1]),
            "finite": bool(np.isfinite(audio).all()),
            "peak": float(np.abs(audio).max()),
            "rms": float(np.sqrt(np.mean(audio.astype(np.float64) ** 2))),
            "truncated": result.truncated,
            "measured_e2e_seconds": time.time() - started,
            "max_sampled_rss_bytes": max(x["rss_bytes"] for x in samples),
            "max_sampled_mps_driver_bytes": max(x.get("mps_driver_bytes", 0) for x in samples),
            "target_seconds": args.target_seconds,
            "target_delta_seconds": len(audio) / 48000 - args.target_seconds if args.target_seconds else None,
            "duration_target_met": abs(len(audio) / 48000 - args.target_seconds) <= 15 if args.target_seconds else None,
            "three_to_four_minutes": 180 <= len(audio) / 48000 <= 240,
            "semantic_calls": 1, "acoustic_chunks": len(acoustic_chunks),
            "quality_note": "Signal and continuous-generation checks only; no human listening or ASR quality claim.",
        }
        (out / "signal_metrics.json").write_text(json.dumps(metrics, indent=2))
        # Final progress and local metrics are written after native save_artifacts.
        # Seal the manifest only once those files have reached their final state.
        manifest = json.loads((out / "result.json").read_text())
        manifest["artifacts"] = collect_hashes(out)
        write_json(out / "result.json", manifest)
        verify_result(out, expected_identity=request_id)
        print(json.dumps(metrics, indent=2), flush=True)
except Exception as exc:
    record("failed")
    (out / "failure.json").write_text(json.dumps({
        "type": type(exc).__name__, "message": str(exc),
        "traceback": traceback.format_exc(), "elapsed_seconds": time.time() - started,
    }, ensure_ascii=False, indent=2))
    raise
