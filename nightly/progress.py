"""Real stage counters only; generation budgets are never progress totals."""
import json
import re
from pathlib import Path


def summarize(stage='', native='', direct=False):
    p=dict(phase='loading',text='正在加载本地模型',current=None,total=None,unit='',completed=[])
    score='曲谱规划已跳过（直接生成）' if direct else '曲谱已完成'
    def phase(name,text,completed): p.update(phase=name,text=text,completed=completed)
    if stage.startswith('abc:') or 'Planning score' in native:
        m=re.search(r'Planning score: (\d+) tokens',native)
        count=int(m[1]) if m else int(stage.split(':')[1]) if stage.startswith('abc:') else 0
        phase('score',f'正在创作曲谱 · 已生成 {count} 个 token',['模型已加载'])
    if stage=='planned' or stage.startswith('semantic:') or 'Generating song' in native:
        m=re.search(r'Generating song: (\d+) tokens',native)
        count=int(m[1]) if m else int(stage.split(':')[1]) if stage.startswith('semantic:') else 0
        phase('semantic',f'正在生成演唱与编配 · 已生成约 {count/25:.0f} 秒音乐序列（非成品时长）',[score])
    if stage=='semantic_done' or 'Synthesizing audio' in native:
        phase('synthesis','正在合成音频',[score,'演唱与编配序列已完成'])
        m=re.search(r'Synthesizing audio: (\d+)/(\d+) steps',native)
        if m: p.update(current=int(m[1]),total=int(m[2]),unit='步',text=f'正在合成音频 · {m[1]} / {m[2]} 步')
    if stage=='synthesized' or 'Decoding audio' in native or 'Loading audio decoder' in native:
        phase('decode','正在加载音频解码器' if 'Loading audio decoder' in native else '正在解码音频',[score,'音频合成已完成'])
        m=re.search(r'Decoding audio: (\d+)/(\d+) chunks',native)
        if m: p.update(current=int(m[1]),total=int(m[2]),unit='块',text=f'正在解码音频 · {m[1]} / {m[2]} 块')
    if stage=='complete': phase('save','正在保存并校验原始音频',[score,'音频合成与解码已完成'])
    return p


def read_progress(job,folder):
    output=Path(folder)/'output'; stage=''; native=''
    if job['status'] in ('running','queued'):
        try: stage=json.loads((output/'progress.json').read_text())[-1]['stage']
        except (OSError,ValueError,KeyError,IndexError): pass
        try:
            with (Path(folder)/'run.log').open('rb') as f:
                f.seek(0,2); f.seek(max(0,f.tell()-8192));lines=f.read().decode(errors='replace').splitlines()
            native=next((line for line in reversed(lines) if '[YuE2]' in line),'')
        except OSError: pass
    p=summarize(stage,native,job.get('request',{}).get('cot')=='off')
    if job['status']=='queued': p.update(phase='queued',text='等待本机模型开始运行',completed=[])
    if job['status'] in ('completed','partial'):
        mp3=(output/'audio.mp3').exists()
        p.update(phase='done',text=('生成片段已保存' if job['status']=='partial' else '歌曲已完成')+(' · MP3 320 kbps' if mp3 else ' · FLAC'),completed=['音频已生成'],current=None,total=None)
        if (output/'mp3-storage.json').exists() and (output/'audio.flac').exists():
            p.update(phase='ready',text='MP3 已验证 · 等待播放器切换后清理 FLAC')
    try:
        storage=json.loads((output/'storage-progress.json').read_text())
        p.update(storage)
        p['completed']=['音频合成与解码已完成']
    except (OSError,ValueError): pass
    if job.get('storage_warning'): p.update(phase='storage_failed',text=job['storage_warning'],current=None,total=None)
    if job['status'] in ('failed','cancelled','interrupted'):
        p.update(phase=job['status'],text={'failed':'生成失败','cancelled':'已取消','interrupted':'生成已中断'}[job['status']],current=None,total=None)
    return p
