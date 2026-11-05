"""Verified MP3 storage; never discard the source before a successful decode."""
import fcntl
import hashlib
import json
import re
import shutil
import subprocess
import threading
import time
from pathlib import Path
import soundfile as sf


def audio_filename(job, suffix):
    first = next((line.strip() for line in job.get('request', {}).get('lyrics', '').splitlines()
                  if line.strip() and not line.strip().startswith('[')), '未命名歌曲')
    title = job.get('metadata', {}).get('title') or first[:22]
    title = re.sub(r'[\\/:*?"<>|\x00-\x1f\x7f]', '-', title).strip()[:100] or '未命名歌曲'
    return '小Rei-' + title + suffix


def preferred_audio(folder):
    for name in ('audio.mp3', 'audio.flac'):
        p = folder / name
        if p.is_file(): return p
    return None


def digest(path):
    with path.open('rb') as f: return hashlib.file_digest(f, 'sha256').hexdigest()


def executable(name):
    result = shutil.which(name) or '/opt/homebrew/bin/' + name
    if not Path(result).is_file(): raise RuntimeError(name + ' 未安装，保留原音频')
    return result


def storage_progress(folder, phase, text, current=None, total=None):
    value=dict(phase=phase,text=text,current=current,total=total,unit='秒' if total else '',updated_at=time.time())
    path=Path(folder)/'storage-progress.json';temp=path.with_suffix('.tmp')
    temp.write_text(json.dumps(value,ensure_ascii=False)+'\n');temp.replace(path)


def encode(source, temp, duration):
    folder=source.parent
    storage_progress(folder,'mp3',f'正在转为 MP3 · 0.0 / {duration:.1f} 秒',0,duration)
    command=[executable('ffmpeg'),'-v','error','-nostdin','-y','-i',str(source),'-map','0:a:0',
             '-c:a','libmp3lame','-b:a','320k','-threads','1','-nostats','-stats_period','0.25','-progress','pipe:1',str(temp)]
    with (folder/'mp3-encode.log').open('w') as error_log:
        with subprocess.Popen(command,stdout=subprocess.PIPE,stderr=error_log,text=True) as process:
            timer=threading.Timer(600,process.kill);timer.daemon=True;timer.start()
            try:
                for line in process.stdout:
                    key,_,value=line.strip().partition('=')
                    if key=='out_time_us':
                        try: current=min(duration,max(0,float(value)/1000000))
                        except ValueError: continue
                        storage_progress(folder,'mp3',f'正在转为 MP3 · {current:.1f} / {duration:.1f} 秒（{current/duration*100:.0f}%）',current,duration)
                code=process.wait()
                if code: raise subprocess.CalledProcessError(code,command)
            finally:
                timer.cancel()
                if process.poll() is None: process.kill();process.wait()


def convert(folder, delete_source=False):
    try: return _convert(folder,delete_source)
    except Exception as exc:
        storage_progress(folder,'storage_failed','MP3 转码或校验失败，保留原件：'+str(exc)[:200])
        raise


def _convert(folder, delete_source=False):
    folder = Path(folder)
    with (folder / '.mp3.lock').open('a') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        source, target = folder / 'audio.flac', folder / 'audio.mp3'
        receipt = folder / 'mp3-storage.json'
        if not source.exists():
            if target.exists() and receipt.exists():
                record = json.loads(receipt.read_text())
                if digest(target) == record['mp3_sha256']: return record
            raise RuntimeError('缺少可验证的音频源')
        original = digest(source)
        record = json.loads(receipt.read_text()) if receipt.exists() else {}
        if not (target.exists() and record.get('source_sha256') == original and record.get('mp3_sha256') == digest(target)):
            info = sf.info(str(source))
            temp = folder / 'audio.encoding.mp3'
            try:
                encode(source,temp,info.duration)
                storage_progress(folder,'verify','MP3 转码完成 · 正在完整解码校验')
                subprocess.run([executable('ffmpeg'), '-v', 'error', '-xerror', '-nostdin', '-i', str(temp), '-f', 'null', '-'], check=True, timeout=600)
                probe = json.loads(subprocess.check_output([executable('ffprobe'), '-v', 'error', '-show_streams', '-show_format', '-of', 'json', str(temp)], timeout=30))
                stream = probe['streams'][0]; duration = float(probe['format']['duration'])
                if stream['codec_name'] != 'mp3' or stream['channels'] != info.channels or int(stream['sample_rate']) != info.samplerate or abs(duration-info.duration) > .15:
                    raise RuntimeError('MP3 格式或时长校验失败，保留 FLAC')
                record = dict(codec='mp3', bitrate=320000, channels=info.channels, sample_rate=info.samplerate,
                              source_duration=info.duration, mp3_duration=duration, source_bytes=source.stat().st_size,
                              mp3_bytes=temp.stat().st_size, source_sha256=original, mp3_sha256=digest(temp), decode_verified=True)
                temp.replace(target)
                pending = receipt.with_suffix('.tmp'); pending.write_text(json.dumps(record, indent=2)+'\n'); pending.replace(receipt)
            finally:
                temp.unlink(missing_ok=True)
        storage_progress(folder,'ready','MP3 已验证 · 等待播放器切换后清理 FLAC')
        if delete_source:
            storage_progress(folder,'cleanup','MP3 已验证 · 正在清理原 FLAC')
            if digest(source) != record['source_sha256'] or digest(target) != record['mp3_sha256']:
                raise RuntimeError('音频在验证后发生改变，保留源文件')
            source.unlink()
            storage_progress(folder,'done','MP3 已保存 · 320 kbps · 原 FLAC 已清理')
        return record
