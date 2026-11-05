"""One-run delivery audit and idle-only service update; never pauses a queue.

Used for the initial rollout. No scheduler, GPT call, or future song generation.
"""
import argparse
import fcntl
import hashlib
import json
from pathlib import Path
import subprocess
import sys
import time
import urllib.request

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


def finish(folder):
    end=time.monotonic()+24*3600
    while time.monotonic()<end:
        state=json.loads((folder/'state.json').read_text())
        if state['status'] in ('completed','partial','failed'): break
        time.sleep(30)
    else: raise RuntimeError('批次交付等待超过24小时；未停止任何进程')
    import soundfile as sf
    import numpy as np
    results=[]
    for path in (ROOT/'studio-data').glob('*/job.json'):
        job=json.loads(path.read_text())
        if job.get('metadata',{}).get('batch')!=folder.name: continue
        from nightly.audio import preferred_audio
        audio=preferred_audio(path.parent/'output')
        entry={'id':job['id'],'title':job.get('metadata',{}).get('title'), 'status':job['status']}
        if audio is not None and audio.exists() and job['status'] in ('completed','partial'):
            info=sf.info(audio); finite=True; energy=0; count=0
            for block in sf.blocks(audio,blocksize=65536,dtype='float32',always_2d=True):
                finite=finite and bool(np.isfinite(block).all()); energy+=float(np.square(block.astype('float64')).sum());count+=block.size
            with audio.open('rb') as f:digest=hashlib.file_digest(f,'sha256').hexdigest()
            entry.update(duration_seconds=info.duration,channels=info.channels,sample_rate=info.samplerate,
                         finite=finite,rms=(energy/count)**.5 if count else 0,sha256=digest,
                         voice_verified=False,lyrics_singing_verified=False)
        results.append(entry)
    (folder/'audio-audit.json').write_text(json.dumps(results,ensure_ascii=False,indent=2))
    # Hold both submission and inference locks, then verify idle. A currently
    # generating user job always wins; we wait, never terminate it to deploy.
    worker=(ROOT/'nightly-data/worker.lock').open('a')
    inference=(ROOT/'evidence/inference.lock').open('a')
    while time.monotonic()<end:
        try:
            fcntl.flock(worker,fcntl.LOCK_EX|fcntl.LOCK_NB)
            fcntl.flock(inference,fcntl.LOCK_EX|fcntl.LOCK_NB)
            with urllib.request.urlopen('http://127.0.0.1:8766/api/state',timeout=10) as r:s=json.load(r)
            if not s['active']:
                subprocess.run([sys.executable,str(ROOT/'studio_ctl.py'),'stop'],check=True)
                subprocess.run([sys.executable,str(ROOT/'studio_ctl.py'),'start'],check=True)
                (folder/'delivery.json').write_text(json.dumps({'status':'ready','library':'http://127.0.0.1:8766/library','verified_audio':len(results),'deployed_at':time.time()},indent=2))
                print('Delivery ready; archive catalog loaded at idle',flush=True)
                return
        except BlockingIOError: pass
        finally:
            fcntl.flock(inference,fcntl.LOCK_UN);fcntl.flock(worker,fcntl.LOCK_UN)
        time.sleep(30)
    raise RuntimeError('生成结果已保存，服务持续忙碌，未执行重启')


if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--batch',required=True);a=p.parse_args()
    import re
    if not re.fullmatch(r'\d{4}-\d{2}-\d{2}',a.batch):raise ValueError('invalid batch')
    finish(ROOT/'nightly-data'/a.batch)
