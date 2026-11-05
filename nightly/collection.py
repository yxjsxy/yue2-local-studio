"""Persistent favorites and irreversible, path-bounded song cleanup."""
from contextlib import contextmanager
import fcntl
import json
import os
from pathlib import Path
import re
import shutil
import time


class CollectionError(ValueError):
    def __init__(self, status, message):
        super().__init__(message)
        self.status = status


def read_state(data):
    path = data / 'library-state.json'
    if not path.exists():
        return {'version': 1, 'favorites': {}, 'deleted': {}}
    value = json.loads(path.read_text())
    if not isinstance(value.get('favorites'), dict) or not isinstance(value.get('deleted'), dict):
        raise ValueError('收藏数据无法读取，请保留 library-state.json 后检查文件。')
    return value


@contextmanager
def edit_state(data):
    with (data / '.library-state.lock').open('a') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        value = read_state(data)
        yield value
        temp = data / 'library-state.json.tmp'
        with temp.open('w') as stream:
            json.dump(value, stream, ensure_ascii=False, indent=2)
            stream.flush()
            os.fsync(stream.fileno())
        temp.replace(data / 'library-state.json')


def deleted_slots(state, batch):
    return {record['slot'] for record in state['deleted'].values()
            if record.get('batch') == batch and record.get('slot')}


def is_deleted(state, job):
    meta = job.get('metadata', {})
    return (job['id'] in state['deleted'] or bool(meta.get('batch') and meta.get('slot')
            and meta['slot'] in deleted_slots(state, meta['batch'])))


def deleted_request(state, client_key, metadata):
    if metadata.get('batch') and metadata.get('slot') in deleted_slots(state, metadata['batch']):
        return True
    return bool(client_key and any(client_key in r.get('client_keys', []) for r in state['deleted'].values()))


def favorite(root, data, jobs, track_id, value):
    from nightly.library import catalog
    if type(value) is not bool:
        raise CollectionError(400, '喜欢状态必须为 true 或 false。')
    track = next((t for t in catalog(root, data, jobs)['tracks'] if t['id'] == track_id), None)
    if not track:
        raise CollectionError(404, '这首歌已不存在。')
    if not track.get('audio_url'):
        raise CollectionError(409, '请等待歌曲生成完成后再加入喜欢。')
    with edit_state(data) as state:
        if value:
            state['favorites'][track_id] = time.time()
        else:
            state['favorites'].pop(track_id, None)
    return {'id': track_id, 'favorite': value}


def safe_folder(root, data, area, name):
    if area not in (data.name, 'outputs', 'public'):
        raise CollectionError(400, '无效的歌曲目录。')
    pattern = r'[a-f0-9]{16}' if area == data.name else r'[a-zA-Z0-9_-]+'
    if not isinstance(name, str) or not re.fullmatch(pattern, name):
        raise CollectionError(400, '无效的歌曲标识。')
    base = root / area
    folder = base / name
    if base.is_symlink() or folder.is_symlink() or base.resolve().parent != root.resolve() or folder.resolve().parent != base.resolve():
        raise CollectionError(400, '歌曲目录包含不允许的路径链接。')
    return folder


def folder_bytes(folder):
    return sum(p.lstat().st_size for p in folder.rglob('*') if p.is_file() and not p.is_symlink()) if folder.exists() else 0


def delete_track(root, data, jobs, track_id, confirm_title):
    from nightly.library import catalog, audio_hash
    from nightly.audio import preferred_audio
    state = read_state(data)
    record = state['deleted'].get(track_id)
    if record is None:
        track = next((t for t in catalog(root, data, jobs)['tracks'] if t['id'] == track_id), None)
        if not track:
            raise CollectionError(404, '这首歌已不存在。')
        if not track.get('audio_url') or track.get('status') not in ('completed', 'partial'):
            raise CollectionError(409, '只能删除已经生成的歌曲，正在生成的任务请在工作台取消。')
        if confirm_title != track['title']:
            raise CollectionError(409, '歌曲信息已变化，请重新确认歌名后删除。')
        batch, slot = track.get('batch'), None
        targets, removed_jobs, client_keys = [], [], []
        if re.fullmatch(r'[a-f0-9]{16}', track_id):
            original = jobs[track_id]
            slot = original.get('metadata', {}).get('slot')
            for key, job in jobs.items():
                meta = job.get('metadata', {})
                if key != track_id and not (slot and meta.get('batch') == batch and meta.get('slot') == slot):
                    continue
                if job['status'] not in ('completed', 'partial', 'failed', 'interrupted', 'cancelled'):
                    raise CollectionError(409, '此歌曲还有正在执行的生成任务，请完成后再删除。')
                targets.append([data.name, key]);removed_jobs.append(key)
                if job.get('client_key'):client_keys.append(job['client_key'])
            audio = preferred_audio(safe_folder(root, data, data.name, track_id) / 'output')
        else:
            match = re.fullmatch(r'archive-(outputs|public)-([a-zA-Z0-9_-]+)', track_id)
            if not match:
                raise CollectionError(400, '无效的歌曲标识。')
            targets.append([match[1], match[2]])
            audio = preferred_audio(safe_folder(root, data, match[1], match[2]))
        # An archive may have published copies hidden by catalog deduplication.
        # Remove these exact audio copies too; never match just lyrics or title.
        identity = None
        if audio:
            if audio.is_symlink() or audio.resolve().parent != audio.parent.resolve():
                raise CollectionError(400, '音频文件包含不允许的路径链接。')
            stat = audio.stat();identity = audio_hash(str(audio), stat.st_size, stat.st_mtime_ns)
        if identity:
            for area in ('outputs', 'public'):
                for candidate in (root / area).glob('*'):
                    if not candidate.is_dir() or candidate.is_symlink() or not re.fullmatch(r'[a-zA-Z0-9_-]+', candidate.name):continue
                    copy = preferred_audio(candidate)
                    if copy is None or copy.is_symlink():continue
                    stat = copy.stat()
                    if audio_hash(str(copy), stat.st_size, stat.st_mtime_ns) == identity:
                        targets.append([area, candidate.name])
        targets = [list(t) for t in dict.fromkeys(tuple(t) for t in targets)]
        folders = [safe_folder(root, data, *target) for target in targets]
        record = dict(title=track['title'], batch=batch, slot=slot, client_keys=client_keys,
                      job_ids=removed_jobs, folders=targets, bytes=sum(folder_bytes(f) for f in folders),
                      deleted_at=time.time(), cleanup_complete=False)
        # Persist intent first, so a crash cannot resurrect a removed nightly slot.
        with edit_state(data) as state:
            state['deleted'][track_id] = record
            for key in removed_jobs + [track_id]:state['favorites'].pop(key, None)
    elif confirm_title != record['title']:
        raise CollectionError(409, '请确认原歌曲名称后再重试。')
    for area, name in record['folders']:
        folder = safe_folder(root, data, area, name)
        if folder.exists():shutil.rmtree(folder)
    with edit_state(data) as state:
        state['deleted'][track_id]['cleanup_complete'] = True
    for key in record.get('job_ids', []):jobs.pop(key, None)
    return {'id': track_id, 'deleted': True, 'removed_bytes': record['bytes']}
