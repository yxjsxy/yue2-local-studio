"""Fetch pinned generation models and verify weight bytes and SHA-256."""
import argparse
import hashlib
import json
from pathlib import Path


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--verify-only", action="store_true")
    args = parser.parse_args()
    root = Path(__file__).resolve().parent
    models = json.loads((root / "models.lock.json").read_text())
    if not args.verify_only:
        from huggingface_hub import snapshot_download
    for model in models:
        destination = root / "models" / model["directory"]
        if not args.verify_only:
            snapshot_download(
                repo_id=model["repo_id"],
                revision=model["revision"],
                local_dir=str(destination),
                allow_patterns=["*.json", "*.txt", "*.model", "*.safetensors",
                                "*.py", "*.md", "LICENSE*"],
                max_workers=2,
            )
        for name, expected in model["weights"].items():
            path = destination / name
            if not path.is_file() or path.stat().st_size != expected["bytes"]:
                raise SystemExit(f"Missing or incorrect size: {path}")
            with path.open("rb") as handle:
                digest = hashlib.file_digest(handle, "sha256").hexdigest()
            if digest != expected["sha256"]:
                raise SystemExit(f"SHA-256 mismatch: {path}")
        if not args.verify_only:
            (destination / "download_revision.json").write_text(
                json.dumps({"revision": model["revision"]}) + "\n")
        print(f"Verified: {model['repo_id']} @ {model['revision']}")


if __name__ == "__main__":
    main()
