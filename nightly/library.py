"""Complete song catalog with durable favorites and removal records."""
import json
import re
import hashlib
from nightly.audio import preferred_audio, audio_filename
from urllib.parse import quote
from nightly.progress import read_progress
from functools import lru_cache
from pathlib import Path

GENRES = {"folk": "温柔民谣", "jazz": "轻松爵士", "rap": "中文说唱", "pop": "明亮流行"}
VOICES = {"male": "男声", "female": "女声"}


@lru_cache(maxsize=512)
def audio_hash(path, size, modified):
    with open(path, 'rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


def validate_metadata(value):
    if not isinstance(value, dict):
        raise ValueError("歌曲信息必须是对象")
    result = {}
    for key, limit in {"title": 80, "batch": 60, "slot": 30, "genre": 20, "voice": 20, "description": 300}.items():
        if key in value:
            if not isinstance(value[key], str) or len(value[key]) > limit:
                raise ValueError("无效歌曲信息：" + key)
            result[key] = value[key]
    return result


def catalog(root, data, jobs):
    from nightly.collection import read_state, is_deleted, deleted_slots
    collection = read_state(data)
    tracks = []
    for job in sorted(jobs.values(), key=lambda j: j["created_at"], reverse=True):
        if is_deleted(collection, job):continue
        meta = job.get("metadata", {})
        req = job.get("request", {})
        first = next((s for s in req.get("lyrics", "").splitlines() if s.strip() and not s.startswith("[")), "未命名作品")
        audio = preferred_audio(data / job["id"] / "output")
        playable = job["status"] in {"completed", "partial"} and audio is not None
        if job["status"] in {"completed", "partial", "cancelled"} and not playable:
            continue
        tracks.append(dict(id=job["id"], title=meta.get("title", first[:22]), batch=meta.get("batch", "历史创作"),
                           genre=meta.get("genre", ""), voice=meta.get("voice", ""), description=meta.get("description", ""),
                           lyrics=req.get("lyrics", ""), style=job.get("user_style", req.get("style", "")),
                           status=job["status"], error=job.get("error"), duration=job.get("metrics", {}).get("duration_seconds"),
                           audio_url=f"/media/{job['id']}/{audio.name}" if playable else None,
                           download_url=f"/download/{job['id']}/{quote(audio_filename(job, audio.suffix), safe='')}" if playable else None,
                           created_at=job["created_at"], voice_verified=False, progress=read_progress(job, data / job["id"])))
    batches = []
    for path in sorted((root / "nightly-data").glob("*/manifest.json"), reverse=True):
        try:
            manifest = json.loads(path.read_text())
            state_path = path.with_name("state.json")
            state = json.loads(state_path.read_text()) if state_path.exists() else {}
            batches.append({"id": manifest["batch"], "status": state.get("status", "prepared"),
                            "completed": state.get("completed", 0), "total": len(manifest["songs"]),
                            "error": state.get("error"), "updated_at": state.get("updated_at"), "removed":len(deleted_slots(collection, manifest["batch"]))})
            for song in manifest["songs"]:
                if song["slot"] in deleted_slots(collection, manifest["batch"]):continue
                if any(t["batch"] == manifest["batch"] and t["genre"] == song["genre"] and t["voice"] == song["voice"] for t in tracks):
                    continue
                tracks.append(dict(id=manifest["batch"] + "-" + song["slot"], title=song["title"],
                                   batch=manifest["batch"], genre=song["genre"], voice=song["voice"],
                                   lyrics=song["lyrics"], style=song["style"], description=song.get("description", ""),
                                   status="pending", audio_url=None, duration=None, voice_verified=False))
        except (OSError, ValueError, KeyError):
            continue
    # Include early reproduction samples too. Published copies of an output are
    # deduplicated by actual audio SHA, cached until size/mtime change. Request
    # identity is insufficient: a short draft and a full render may share it.
    seen = set()
    for area in ('outputs', 'public'):
        for folder in sorted(p for p in (root / area).glob('*') if p.is_dir()):
            audio = preferred_audio(folder)
            if audio is None: continue
            if not re.fullmatch(r'[a-zA-Z0-9_-]+', audio.parent.name):
                continue
            folder = audio.parent
            try:
                req = json.loads((folder / 'request.json').read_text())
                result_path = folder / 'result.json'
                result = json.loads(result_path.read_text()) if result_path.exists() else {}
                stat = audio.stat()
                identity = audio_hash(str(audio), stat.st_size, stat.st_mtime_ns)
                if identity in seen: continue
                seen.add(identity)
                duration = result.get('audio_seconds')
                if duration is None:
                    import soundfile
                    duration = soundfile.info(str(audio)).duration
                first = next((s for s in req.get('lyrics', '').splitlines() if s and not s.startswith('[')), folder.name)
                tracks.append(dict(id='archive-'+area+'-'+folder.name, title=first[:22]+' · '+folder.name,
                                   batch='早期复现样例', genre='', voice='', lyrics=req.get('lyrics',''),
                                   style=req.get('style',''), status='partial' if any(result.get('truncated',{}).values()) else 'completed',
                                   duration=duration, audio_url=f'/archive/{area}/{folder.name}/{audio.name}', voice_verified=False,
                                   description='早期实验，可能使用短片段预算。'))
            except (OSError, ValueError, KeyError):
                continue
    slots = {f'{g}-{v}': i for i, (g, v) in enumerate((g, v) for g in GENRES for v in VOICES)}
    def order(t):
        date = t['batch'] if re.fullmatch(r'\d{4}-\d{2}-\d{2}', t['batch']) else ''
        return (0 if date else 1, -int(date.replace('-', '')) if date else 0,
                slots.get(t.get('genre','')+'-'+t.get('voice',''),99),
                0 if t.get('audio_url') else 1, -t.get('created_at',0))
    tracks = [t for t in tracks if t['id'] not in collection['deleted']]
    for track in tracks:track['favorite'] = track['id'] in collection['favorites']
    tracks.sort(key=order)
    return dict(tracks=tracks, batches=batches, genres=GENRES, voices=VOICES)
