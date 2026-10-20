"""Local YuE2 experiment with explicit limits and recoverable stage outputs."""
import argparse
import json
import time
import traceback
from pathlib import Path

import numpy as np
import psutil
import torch
from yue2 import YuE2Pipeline
from yue2.pipeline import SongResult
from yue2.protocol import SongRequest
from yue2.storage import identity

parser = argparse.ArgumentParser()
parser.add_argument("--request", required=True)
parser.add_argument("--output", required=True)
parser.add_argument("--abc-file")
parser.add_argument("--max-seconds", type=int, default=1200)
parser.add_argument("--max-semantic-tokens", type=int, default=1000)
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
    if counts[phase] % 50 == 0:
        record(f"{phase}:{counts[phase]}")


def cancelled():
    return time.time() >= deadline


abc_sampling = {"max_tokens": 1536}
if not 1 <= args.max_semantic_tokens <= 3000:
    raise ValueError("This bounded reproduction allows 1–3000 semantic tokens")
semantic_sampling = {"max_tokens": args.max_semantic_tokens}
(out / "experiment.json").write_text(json.dumps({
    "request": request_data, "device": "mps", "backend": "torch-eager",
    "abc_sampling": abc_sampling, "semantic_sampling": semantic_sampling,
    "decoder_device": "cpu",
    "semantic_audio_cap_seconds": args.max_semantic_tokens / 25, "ode_steps": 32,
    "note": "Bounded short-song experiment, full-size unquantized weights; token-limit termination is reported as truncation.",
}, ensure_ascii=False, indent=2))
try:
    if not torch.backends.mps.is_available():
        raise RuntimeError("This experiment requires verified MPS")
    torch.set_num_threads(6)
    torch.mps.set_per_process_memory_fraction(0.80)
    record("start")
    with YuE2Pipeline.from_pretrained(
        root / "models/YuE2-3B", vae=root / "models/YuE2-Vae",
        device="mps", backend="torch-eager", local_files_only=True,
        memory_budget_gib=16, vae_core_frames=256, offload_ar=True,
        progress=True,
    ) as pipe:
        config = pipe.effective_config(request, abc_sampling, semantic_sampling)
        config["vae_device"] = "cpu"
        request_id = identity({"request": request.to_dict(), "config": config, "weights": pipe.weights})
        record("resolved")
        plan = pipe.plan(request=request, abc_sampling=abc_sampling,
                         cancelled=cancelled, on_token=token)
        plan.save(out / "plan")
        record("planned")
        if plan.truncated:
            raise RuntimeError("Score hit token limit; retained plan, refused to render incomplete score")
        semantic = pipe.generate_semantic(plan, sampling=semantic_sampling,
                                          cancelled=cancelled, on_token=token)
        np.save(out / "semantic_checkpoint.npy", np.asarray(semantic.tokens, dtype=np.int32))
        record("semantic_done")
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
            "quality_note": "Signal checks only; no human listening or ASR quality claim.",
        }
        (out / "signal_metrics.json").write_text(json.dumps(metrics, indent=2))
        print(json.dumps(metrics, indent=2), flush=True)
except Exception as exc:
    record("failed")
    (out / "failure.json").write_text(json.dumps({
        "type": type(exc).__name__, "message": str(exc),
        "traceback": traceback.format_exc(), "elapsed_seconds": time.time() - started,
    }, ensure_ascii=False, indent=2))
    raise
