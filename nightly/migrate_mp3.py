"""Migrate finished songs without stopping inference or removing live FLAC URLs."""
import argparse
import json
from pathlib import Path
import sys
import time
import urllib.request
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from nightly.audio import convert

STATE=ROOT/'evidence/mp3-migration.json'

def run(watch=False):
    deadline=time.monotonic()+24*3600
    while True:
        tracks=[]; errors=[]
        for path in sorted((ROOT/'studio-data').glob('*/job.json')):
            job=json.loads(path.read_text())
            if job['status'] not in ('completed','partial'): continue
            folder=path.parent/'output'
            if not (folder/'audio.flac').exists() and not (folder/'audio.mp3').exists(): continue
            try:
                record=convert(folder)
                tracks.append(dict(id=job['id'],**record))
            except Exception as exc: errors.append(dict(id=job['id'],error=str(exc)))
        # The old service has not loaded MP3 support yet. Keep its FLAC paths
        # working until the existing finalizer deploys at the end of the batch.
        try:
            with urllib.request.urlopen('http://127.0.0.1:8766/api/library',timeout=15) as r: catalog=json.load(r)
            urls={t['id']:t.get('audio_url') for t in catalog['tracks']}
            for track in tracks:
                expected=f"/media/{track['id']}/audio.mp3"
                if urls.get(track['id']) != expected: continue
                req=urllib.request.Request('http://127.0.0.1:8766'+expected,headers={'Range':'bytes=0-2'})
                with urllib.request.urlopen(req,timeout=15) as r:
                    if r.status != 206 or r.headers.get_content_type() != 'audio/mpeg' or len(r.read()) != 3:
                        raise RuntimeError('MP3 HTTP Range 验证失败，保留源文件')
                convert(ROOT/'studio-data'/track['id']/'output',delete_source=True)
        except Exception as exc: errors.append(dict(service_error=str(exc)))
        retained=[t['id'] for t in tracks if (ROOT/'studio-data'/t['id']/'output/audio.flac').exists()]
        batch_path=ROOT/'nightly-data/2026-10-21/state.json'
        batch=json.loads(batch_path.read_text())
        done=batch['status'] in ('completed','partial','failed') and not retained and not errors
        result=dict(status='completed' if done else 'waiting_for_idle_deployment' if retained and not errors else 'running',
                    converted=len(tracks),flac_retained=len(retained),original_bytes=sum(t['source_bytes'] for t in tracks),
                    mp3_bytes=sum(t['mp3_bytes'] for t in tracks),errors=errors,updated_at=time.time(),songs=tracks)
        pending=STATE.with_suffix('.tmp');pending.write_text(json.dumps(result,ensure_ascii=False,indent=2)+'\n');pending.replace(STATE)
        print(json.dumps({k:v for k,v in result.items() if k!='songs'},ensure_ascii=False),flush=True)
        if done or not watch: return
        if time.monotonic()>deadline: raise RuntimeError('MP3 迁移等待超过24小时；未删除未验证的源文件')
        time.sleep(60)

if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--watch',action='store_true');a=p.parse_args();run(a.watch)
