"""One LLM-written manifest -> checked, resumable local serial generation.

No LLM calls during inference. The scheduled agent supplies fresh lyrics once/day.
"""
import argparse
import contextlib
import fcntl
import hashlib
import importlib.util
import io
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import time
import urllib.error
import urllib.request
from datetime import datetime
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
# 本地回环请求绝不能走会话级代理：HTTP_PROXY 属于启动本进程的 WorkBuddy 会话，
# 会话结束后代理消亡，裸 urlopen 会把 127.0.0.1 的请求发进死代理，报 Connection
# refused 并把健康的服务误判为宕机（10-31/11-01/11-02 三次整批停摆的根因）。
os.environ.setdefault('NO_PROXY', '127.0.0.1,localhost')
os.environ.setdefault('no_proxy', '127.0.0.1,localhost')
_direct = urllib.request.build_opener(urllib.request.ProxyHandler({}))
from nightly.collection import read_state, deleted_slots, deleted_request
SKILL = Path(os.environ.get('YUE2_LYRIC_SKILL', str(ROOT / 'skills/cn-lyric-writer')))
BASE = 'http://127.0.0.1:8766'
COMBINATIONS = {f'{g}-{v}' for g in ('folk','jazz','rap','pop') for v in ('male','female')}


def atomic(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_suffix('.tmp')
    temp.write_text(json.dumps(data, ensure_ascii=False, indent=2))
    temp.replace(path)


def load_manifest(path):
    m = json.loads(path.read_text())
    if not re.fullmatch(r'\d{4}-\d{2}-\d{2}', m.get('batch', '')):
        raise ValueError('batch 必须为北京时间 YYYY-MM-DD')
    datetime.strptime(m['batch'], '%Y-%m-%d')
    if path.parent != ROOT / 'nightly-data' / m['batch']:
        raise ValueError('清单必须保存在 nightly-data/YYYY-MM-DD/manifest.json')
    if len(m['songs']) != 8 or {s['slot'] for s in m['songs']} != COMBINATIONS:
        raise ValueError('需要四种曲风 × 男女声，且每种组合恰好一首')
    titles, texts = set(), set()
    for s in m['songs']:
        if s['slot'] != s['genre'] + '-' + s['voice']:
            raise ValueError('slot 与曲风/声线不一致')
        for field, maximum in [('title',80), ('lyrics',4000), ('style',850)]:
            if not isinstance(s.get(field),str) or not 1 <= len(s[field]) <= maximum:
                raise ValueError('无效字段：' + field)
        if s['title'] in titles or s['lyrics'] in texts:
            raise ValueError('同批次不能重复标题或歌词')
        titles.add(s['title']); texts.add(s['lyrics'])
        if not s.get('editorial_review') or not s.get('fingerprint'):
            raise ValueError('缺少风格指纹或歌词人工审稿记录')
        if s.get('mode', 'full') not in ('full','melody','off'):
            raise ValueError('无效模式')
        if s.get('target_seconds',210) not in (180,210,240):
            raise ValueError('交付完整歌曲，目标仅允许 180/210/240 秒')
        if not isinstance(s.get('seed'),int) or not 0 <= s['seed'] <= 2147483647:
            raise ValueError('无效随机种子')
    return m


def validate(path):
    m = load_manifest(path)
    spec = importlib.util.spec_from_file_location('lyric_checks', SKILL / 'scripts/check_lyrics.py')
    checker = importlib.util.module_from_spec(spec); spec.loader.exec_module(checker)
    audits, errors = [], []
    for song in m['songs']:
        text = song['lyrics']
        capture = io.StringIO()
        with contextlib.redirect_stdout(capture):
            code = checker.report(text)
        rhymes = checker.rhyme_audit(text, checker.load_level1(), checker.load_pinyin())
        blocks = checker.split_blocks(text)
        lines = [line for _, rows in blocks for line in rows]
        bad_rhyme = [r['name'] for r in rhymes if r['main_n'] != r['total']]
        bad_end = [e['char'] for r in rhymes for e in r['rhyme_lines'] if e['particle'] or e['poly'] or e['char'] not in checker.load_level1()]
        bad_length = [line for line in lines if not 7 <= len(checker.CJK.findall(line)) <= 14]
        invalid = code != 0 or bad_rhyme or bad_end or bad_length or len(lines) < 32
        audit = dict(slot=song['slot'], passed=not invalid, rhyme_errors=bad_rhyme,
                     ending_errors=bad_end, line_length_errors=bad_length, line_count=len(lines),
                     report=capture.getvalue())
        audits.append(audit)
        (path.parent / (song['slot'] + '.txt')).write_text(text)
        (path.parent / (song['slot'] + '.audit.txt')).write_text(capture.getvalue())
        if invalid: errors.append(song['slot'])
    result = dict(passed=not errors, errors=errors, audits=audits,
                  manifest_sha256=hashlib.sha256(path.read_bytes()).hexdigest(), checked_at=time.time())
    atomic(path.parent / 'validation.json', result)
    if errors: raise ValueError('歌词检查未通过：' + ', '.join(errors) + '；请查看 validation.json 和 *.audit.txt')
    return m


def api(path, body=None, tries=8, timeout=30):
    """本地服务瞬时连接拒绝不应终止整批：只重试连接层，HTTP 错误照原样抛出。

    退避序列覆盖约 4 分钟的短时抖动；单曲预算 90 分钟，这个等待是可以接受的。
    """
    backoff = (2, 4, 8, 15, 30, 60, 60, 60)
    headers = {}
    if body is not None:
        token = api('/api/state')['token']
        headers = {'Content-Type':'application/json','Origin':BASE,'X-Studio-Token':token}
    data = json.dumps(body).encode() if body is not None else None
    for attempt in range(tries):
        try:
            req = urllib.request.Request(BASE + path, data=data, headers=headers)
            with _direct.open(req, timeout=timeout) as r: return json.load(r)
        except urllib.error.HTTPError:
            raise
        except (urllib.error.URLError, OSError):
            if attempt + 1 >= tries: raise
            delay = backoff[min(attempt, len(backoff) - 1)]
            print(json.dumps({'event':'api_retry','path':path,'attempt':attempt+1,'sleep':delay},
                             ensure_ascii=False), flush=True)
            time.sleep(delay)


def service_alive():
    try:
        return 'token' in api('/api/state', tries=1, timeout=10)
    except Exception:
        return False


def ensure_service(stage=''):
    """提交/汇总前确认本地服务可达；不可达就用既有 ctl 幂等拉起，不打断正在生成的任务。"""
    if service_alive(): return True
    print(json.dumps({'event':'service_down','stage':stage}, ensure_ascii=False), flush=True)
    subprocess.run([sys.executable, str(ROOT/'studio_ctl.py'), 'start'], check=False,
                   stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    deadline = time.monotonic() + 300
    while time.monotonic() < deadline:
        if service_alive():
            print(json.dumps({'event':'service_recovered','stage':stage}, ensure_ascii=False), flush=True)
            return True
        time.sleep(5)
    print(json.dumps({'event':'service_unreachable','stage':stage}, ensure_ascii=False), flush=True)
    return False


def local_batch_results(batch):
    """本机任务目录中的成品记录；服务不可用时的汇总降级数据源。"""
    rows = {}
    for job_path in (ROOT/'studio-data').glob('*/job.json'):
        try: d = json.loads(job_path.read_text())
        except Exception: continue
        meta = d.get('metadata') or {}
        if meta.get('batch') != batch: continue
        slot = meta.get('slot')
        if not slot: continue
        duration = (d.get('metrics') or {}).get('duration_seconds')
        storage = d.get('storage') or {}
        usable = d.get('status') == 'completed' and str(storage.get('codec')) == 'mp3' and bool(duration)
        candidate = dict(status=d.get('status'), duration=round(duration, 1) if duration else None, usable=usable)
        current = rows.get(slot)
        if current is None or (usable and not current['usable']) or (
                not current['usable'] and candidate['status'] == 'completed'):
            rows[slot] = candidate
    return rows


def summarize(folder, m, state, results=None, note=''):
    """汇总只用于记录，绝不允许终止整批。服务不可用时降级读本机任务记录。"""
    results = dict(results or {})
    tracks = None
    try:
        tracks = [t for t in api('/api/library')['tracks'] if t.get('batch') == m['batch']]
    except Exception as exc:
        note = (note + '；' if note else '') + '合集接口暂不可用（' + str(exc) + '），本次汇总以本机任务记录为准'
    local = local_batch_results(m['batch'])
    from nightly.collection import read_state, deleted_slots
    try:
        removed = deleted_slots(read_state(ROOT/'studio-data'), m['batch'])
    except Exception:
        removed = state.get('removed') or 0
    rows = ['# 午夜八首 · ' + m['batch'], '', '四种曲风各一首男声目标与一首女声目标。声线、歌词演唱准确度尚未听验；标签表示创作目标。', '',
            '合集：http://127.0.0.1:8766/library', '', '|歌曲|组合|结果|实际时长|', '|---|---|---|---|']
    completed = 0
    for song in m['songs']:
        genre, voice, slot = song['genre'], song['voice'], song['slot']
        track = next((t for t in (tracks or []) if t.get('genre') == genre and t.get('voice') == voice), None)
        status = (track or {}).get('status')
        duration = (track or {}).get('duration')
        info = local.get(slot) or results.get(slot) or {}
        # Count slots, never count a failed retry as an extra song.
        if info.get('usable') or (info.get('status') == 'completed' and info.get('duration')):
            status, duration = 'completed', info.get('duration') or duration
        elif not status and info.get('status'):
            status, duration = info.get('status'), info.get('duration')
        if status == 'completed' and duration: completed += 1
        rows.append(f"|{song['title']}|{genre} / {voice}|{status or 'pending'}|{round(duration,1) if duration else '—'} 秒|")
    state['completed'] = completed
    state['removed'] = len(removed) if isinstance(removed, (list, tuple, set)) else int(removed or 0)
    state['updated_at'] = time.time()
    atomic(folder / 'state.json', state)
    if removed: rows += ['', '用户已永久删除 '+str(len(removed))+' 首，保留删除记录，不恢复音频。']
    if note: rows += ['', note]
    if state.get('error'): rows += ['', state['error']]
    rows += ['', '音频由本机 YuE2 连续生成，未用循环、拼接或变速补足时长。目标约 3–4 分钟，实际时长可能偏离。',
             '歌词按 cn-lyric-writer 检查；静态检查不等于实际发音或音乐质量验收。']
    (folder / 'REPORT.md').write_text('\n'.join(rows))


def run(path):
    m = validate(path)
    folder = path.parent
    batch_lock = (folder/'worker.lock').open('a')
    try: fcntl.flock(batch_lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError: return print('当天批次已在运行或等待；未重复启动。')
    lock = (ROOT / 'nightly-data/worker.lock').open('a')
    previous = folder/'state.json'
    if previous.exists() and json.loads(previous.read_text()).get('status') == 'completed':
        return print('此日期已经完成，保留八首成品。')
    waiting_since = time.monotonic()
    while True:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB); break
        except BlockingIOError:
            if (folder/'STOP').exists() or time.monotonic()-waiting_since > 24*3600:
                atomic(previous,dict(status='failed',error='批次排队已停止或超过24小时',updated_at=time.time(),completed=0))
                return
            atomic(previous,dict(status='waiting',pid=os.getpid(),updated_at=time.time(),completed=0))
            time.sleep(30)
    frozen = folder/'submitted-manifest.sha256'
    digest = hashlib.sha256(path.read_bytes()).hexdigest()
    if frozen.exists() and frozen.read_text().strip() != digest:
        raise ValueError('批次提交后清单不可更改；请保留原清单，另建日期批次')
    frozen.write_text(digest)
    subprocess.run([sys.executable, str(ROOT/'studio_ctl.py'), 'start'], check=True, stdout=subprocess.DEVNULL)
    state = {'status':'running','started_at':time.time(),'completed':0,'pid':os.getpid()}
    deadline = time.monotonic() + 14*3600
    keepawake = subprocess.Popen(['/usr/bin/caffeinate','-i','-w',str(os.getpid())])
    results, notes = {}, []
    try:
        for song in m['songs']:
            slot = song['slot']
            job = None
            try:
                if not ensure_service('提交前 ' + slot):
                    results[slot] = dict(status='error', duration=None, usable=False)
                    notes.append(f'{slot}：本地服务不可用且未能恢复，跳过并继续下一首')
                    continue
                for attempt in range(2):
                    if time.monotonic() > deadline: raise RuntimeError('批次超过 14 小时，停止提交新歌曲')
                    if (folder/'STOP').exists(): raise RuntimeError('收到 STOP 文件，停止提交后续歌曲')
                    if shutil.disk_usage(ROOT).free < 12*1024**3: raise RuntimeError('磁盘可用空间低于 12 GiB，保留结果并停止')
                    key = f"{m['batch']}-{slot}-{attempt}"
                    payload = dict(lyrics=song['lyrics'], style=song['style'], mode=song.get('mode','full'),
                                   seed=(song['seed'] + attempt*7919) % 2147483648, target_seconds=song.get('target_seconds',210),
                                   max_audio_seconds=360, max_abc_tokens=8192, timeout_minutes=90, client_key=key,
                                   metadata={k:song[k] for k in ('title','genre','voice','slot')})
                    payload['metadata'].update(batch=m['batch'],description=song.get('description',''))
                    while True:
                        try:
                            job = api('/api/jobs', payload)['job']; break
                        except urllib.error.HTTPError as e:
                            if e.code == 410:
                                job = {'id':'deleted-'+slot, 'status':'deleted'};break
                            if e.code != 409: raise
                            if time.monotonic() > deadline: raise RuntimeError('等待本地生成资源超时')
                            time.sleep(20)
                    state.update(current=slot, attempt=attempt+1, job_id=job['id'])
                    summarize(folder,m,state,results,'；'.join(notes))
                    while job['status'] not in {'completed','partial','failed','cancelled','interrupted','deleted'}:
                        if (folder/'STOP').exists() or time.monotonic() > deadline:
                            api('/api/jobs/'+job['id']+'/cancel',{})
                            raise RuntimeError('批次停止，已取消当前歌曲并保留结果')
                        time.sleep(20)
                        try:
                            job = json.loads((ROOT/'studio-data'/job['id']/'job.json').read_text())
                        except FileNotFoundError:
                            from nightly.collection import read_state, deleted_request
                            if deleted_request(read_state(ROOT/'studio-data'), key, payload['metadata']):
                                job = {'id':job['id'], 'status':'deleted'}
                            else:raise
                    summarize(folder,m,state,results,'；'.join(notes))
                    print(json.dumps({'slot':slot,'status':job['status'],'job_id':job['id']},ensure_ascii=False),flush=True)
                    if job['status'] == 'completed': break
                    if job['status'] in ('cancelled','deleted'): break
                try:
                    final = json.loads((ROOT/'studio-data'/str(job['id'])/'job.json').read_text())
                except Exception:
                    final = job
                duration = (final.get('metrics') or {}).get('duration_seconds')
                storage = final.get('storage') or {}
                results[slot] = dict(status=final.get('status'),
                                     duration=round(duration,1) if duration else None,
                                     usable=final.get('status')=='completed' and str(storage.get('codec'))=='mp3' and bool(duration))
                ensure_service('完成后 ' + slot)
            except RuntimeError:
                raise
            except Exception as exc:
                results[slot] = dict(status='error', duration=None, usable=False)
                notes.append(f'{slot}：{exc}；已记录并继续下一首')
                print(json.dumps({'slot':slot,'event':'song_error','error':str(exc)},ensure_ascii=False),flush=True)
            finally:
                try: summarize(folder,m,state,results,'；'.join(notes))
                except Exception: pass
                time.sleep(10)
        done = sum(1 for r in results.values() if r.get('usable'))
        state.pop('error', None)
        state['status'] = 'completed' if done + (state.get('removed') or 0) >= 8 else 'partial'
        summarize(folder,m,state,results,'；'.join(notes))
    except Exception as exc:
        state.update(status='failed', error=str(exc),updated_at=time.time())
        atomic(folder/'state.json',state)
        try: summarize(folder,m,state,results,'；'.join(notes))
        except Exception: pass
        raise
    finally:
        keepawake.terminate()


def supervise(path, rounds=6):
    """守护式跑批：单轮异常退出不让整批停在半路。

    client_key 幂等，重复运行只会补跑未完成的歌曲，不会重跑已完成的成品。
    """
    m = load_manifest(path)
    folder = path.parent
    started = time.monotonic()
    for index in range(rounds):
        previous = folder / 'state.json'
        if previous.exists() and json.loads(previous.read_text()).get('status') == 'completed':
            return print('此日期已经完成，保留八首成品。')
        if (folder / 'STOP').exists():
            return print('存在 STOP 文件，按用户意图停止，不自动运行。')
        if time.monotonic() - started > 14 * 3600:
            return print('已超过整批 14 小时保护，保留结果并停止。')
        keepawake = subprocess.Popen(['/usr/bin/caffeinate', '-i', '-w', str(os.getpid())])
        try:
            with (folder / 'worker.log').open('ab') as log:
                log.write(('\n=== supervise 第 %d 轮 %s ===\n' % (
                    index + 1, datetime.now(ZoneInfo('Asia/Shanghai')).isoformat(timespec='seconds'))).encode())
                subprocess.run([sys.executable, '-u', str(Path(__file__).resolve()), 'run', '--manifest', str(path)],
                               cwd=ROOT, stdin=subprocess.DEVNULL, stdout=log, stderr=subprocess.STDOUT, check=False)
        finally:
            keepawake.terminate()
        status = json.loads(previous.read_text()).get('status') if previous.exists() else None
        print(json.dumps({'round': index + 1, 'status': status}, ensure_ascii=False), flush=True)
        if status in ('completed', 'partial'):
            return
        time.sleep(60)
    print('已达守护重试上限，保留已完成歌曲与失败记录，不再自动追加。')


def main():
    p=argparse.ArgumentParser();p.add_argument('action',choices=['validate','start','run','supervise','status']);p.add_argument('--manifest',type=Path,required=True);args=p.parse_args()
    path=args.manifest.resolve()
    if args.action=='validate':
        m=validate(path); print(json.dumps({'passed':True,'batch':m['batch'],'songs':8}))
    elif args.action=='run': run(path)
    elif args.action=='supervise': supervise(path)
    elif args.action=='status':
        print((path.parent/'state.json').read_text() if (path.parent/'state.json').exists() else '{"status":"not_started"}')
    else:
        validate(path)
        with (path.parent/'worker.log').open('ab') as log:
            child=subprocess.Popen([sys.executable,'-u',str(Path(__file__).resolve()),'supervise','--manifest',str(path)],cwd=ROOT,
                                   stdin=subprocess.DEVNULL,stdout=log,stderr=subprocess.STDOUT,start_new_session=True)
        print(json.dumps({'worker_pid':child.pid,'batch':path.parent.name,'library':BASE+'/library'}))

if __name__=='__main__': main()
