"""Loopback-only YuE2 studio. One real subprocess at a time; no shell execution."""
from nightly.audio import convert, preferred_audio, audio_filename
from nightly.collection import CollectionError, read_state, is_deleted, deleted_request, favorite, delete_track
from nightly.progress import read_progress
import argparse
import fcntl
import json
import mimetypes
import os
import re
import secrets
import signal
import subprocess
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlparse, quote

ROOT = Path(__file__).resolve().parent
DATA = (ROOT / os.environ.get("YUE2_STUDIO_DATA_DIR", "studio-data")).resolve()
if DATA.parent != ROOT.resolve():
    raise ValueError("Studio data must be a direct child directory of the project")
DATA.mkdir(exist_ok=True)
TOKEN = secrets.token_urlsafe(32)
LOCK = threading.RLock()
ACTIVE = None
PROCESS = None
STOPPING = False
JOBS = {}
FINAL = {"completed", "partial", "failed", "cancelled", "interrupted"}


def persist(job):
    p = DATA / job["id"] / "job.json"
    tmp = p.with_suffix(".tmp")
    tmp.write_text(json.dumps(job, ensure_ascii=False, indent=2))
    tmp.replace(p)


for path in DATA.glob("*/job.json"):
    try:
        job = json.loads(path.read_text())
        if not re.fullmatch(r"[a-f0-9]{16}", job["id"]):
            continue
        if job["status"] not in FINAL:
            job.update(status="interrupted", error="服务曾停止；请保留输入后重新生成。")
            persist(job)
        JOBS[job["id"]] = job
    except (ValueError, KeyError, OSError):
        continue


def validate(body):
    if not isinstance(body, dict):
        raise ValueError("输入必须是对象。")
    lyrics, style = body.get("lyrics"), body.get("style")
    if not isinstance(lyrics, str) or not 1 <= len(lyrics.strip()) <= 4000:
        raise ValueError("歌词需为1至4000个字符；完整歌曲请填写分段歌词。")
    if not isinstance(style, str) or not 1 <= len(style.strip()) <= 1000:
        raise ValueError("请填写1至1000个字符的音乐风格。")
    mode = body.get("mode", "full")
    if mode not in ("full", "melody", "off"):
        raise ValueError("请选择曲谱规划、旋律规划或直接生成。")
    seed = body.get("seed", 42)
    if type(seed) is not int or not 0 <= seed <= 2147483647:
        raise ValueError("随机种子需为0至2147483647的整数。")
    return dict(lyrics=lyrics.strip(), style=style.strip(), cot=mode, seed=seed)


def validate_options(body):
    target = body.get("target_seconds", 0)
    budget = body.get("max_audio_seconds", 360)
    score = body.get("max_abc_tokens", 4096)
    timeout = body.get("timeout_minutes", 60)
    if type(target) is not int or target not in (0, 60, 120, 180, 210, 240):
        raise ValueError("请选择有效的目标时长。")
    if type(budget) is not int or not 48 <= budget <= 360:
        raise ValueError("生成预算上限需为48至360秒（模型默认360秒）。")
    if target and budget < target + 15:
        raise ValueError("生成预算需比目标时长至少多15秒，为自然结尾留余量。")
    if type(score) is not int or not 4096 <= score <= 8192:
        raise ValueError("曲谱预算需为4096至8192。")
    if type(timeout) is not int or not 10 <= timeout <= 120:
        raise ValueError("运行保护时限需为10至120分钟。")
    return dict(target_seconds=target, max_audio_seconds=budget,
                max_abc_tokens=score, timeout_minutes=timeout)


def external_busy():
    path = ROOT / "evidence/inference.lock"
    path.parent.mkdir(exist_ok=True)
    with path.open("a") as handle:
        try:
            fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            return True
        fcntl.flock(handle, fcntl.LOCK_UN)
    return False


def run_job(job):
    global ACTIVE, PROCESS
    folder = DATA / job["id"]
    request_path = folder / "input.json"
    output = folder / "output"
    options = job.get("options", validate_options({}))
    timeout = options["timeout_minutes"] * 60
    cmd = [str(ROOT / ".venv/bin/python"), "-u", str(ROOT / "run_song.py"),
           "--request", str(request_path.relative_to(ROOT)),
           "--output", str(output.relative_to(ROOT)), "--max-seconds", str(timeout),
           "--max-semantic-tokens", str(options["max_audio_seconds"] * 25),
           "--max-abc-tokens", str(options["max_abc_tokens"]),
           "--target-seconds", str(options["target_seconds"])]
    started = time.monotonic()
    proc = None
    try:
        with (folder / "run.log").open("wb") as log:
            with LOCK:
                if job.get("cancel_requested") or STOPPING:
                    job.update(status="cancelled", finished_at=time.time())
                    persist(job)
                    return
                proc = subprocess.Popen(cmd, cwd=ROOT, stdin=subprocess.DEVNULL,
                                        stdout=log, stderr=subprocess.STDOUT,
                                        start_new_session=True)
                PROCESS = proc
                job.update(status="running", started_at=time.time())
                persist(job)
            while proc.poll() is None:
                with LOCK:
                    cancel = job.get("cancel_requested") or STOPPING
                if cancel or time.monotonic() - started > timeout + 30:
                    os.killpg(proc.pid, signal.SIGTERM)
                    try:
                        proc.wait(timeout=5)
                    except subprocess.TimeoutExpired:
                        os.killpg(proc.pid, signal.SIGKILL)
                        proc.wait()
                    with LOCK:
                        job.update(status="cancelled" if cancel else "failed",
                                   error=None if cancel else f"超过{options['timeout_minutes']}分钟运行保护时限，已停止并保留阶段结果。")
                    break
                time.sleep(0.5)
            should_convert = False
            with LOCK:
                if job["status"] == "running":
                    metrics_path = output / "signal_metrics.json"
                    if proc.returncode == 0 and metrics_path.exists() and (output / "audio.flac").exists():
                        metrics = json.loads(metrics_path.read_text())
                        if not metrics["finite"]:
                            raise ValueError("输出含无效数值。")
                        job["metrics"] = metrics
                        should_convert = True
                    else:
                        message = f"生成进程退出（代码{proc.returncode}）。"
                        if (output / "failure.json").exists():
                            failure = json.loads((output / "failure.json").read_text())
                            message += failure.get("message", "")[:600]
                        job.update(status="failed", error=message + " 已保留阶段结果；请根据错误调整设置后重试。")
            if should_convert:
                try:
                    storage = convert(output, delete_source=True)
                    with LOCK: job["storage"] = storage
                except Exception as exc:
                    with LOCK: job["storage_warning"] = "MP3 转码失败，保留 FLAC：" + str(exc)[:300]
                with LOCK:
                    job["status"] = "partial" if any(metrics["truncated"].values()) else "completed"
            with LOCK:
                job.update(finished_at=time.time(), exit_code=proc.returncode)
                persist(job)
    except Exception as exc:
        with LOCK:
            job.update(status="failed", error=str(exc)[:800], finished_at=time.time())
            persist(job)
    finally:
        if proc is not None and proc.poll() is None:
            os.killpg(proc.pid, signal.SIGKILL)
            proc.wait()
        with LOCK:
            PROCESS = None
            ACTIVE = None


def public(job):
    result = dict(job)
    result["progress"] = read_progress(job, DATA / job["id"])
    output = DATA / job["id"] / "output"
    result["elapsed_seconds"] = round((job.get("finished_at") or time.time()) - job.get("started_at", job["created_at"]))
    result["stage"] = "准备模型"
    try:
        progress = json.loads((output / "progress.json").read_text())
        result["stage"] = progress[-1]["stage"]
    except (OSError, ValueError, IndexError):
        pass
    try:
        inspection = json.loads((output / "score-inspection.txt").read_text())
        result["plan_nominal_seconds"] = inspection.get("nominal_duration_seconds")
    except (OSError, ValueError):
        pass
    # Log messages reflect real native synthesis progress; do not invent percentages.
    try:
        with (DATA / job["id"] / "run.log").open("rb") as stream:
            stream.seek(0, 2)
            size = stream.tell()
            stream.seek(max(0, size - 2000))
            lines = stream.read().decode(errors="replace").splitlines()
        native = [line for line in lines if "[YuE2]" in line]
        result["native_progress"] = native[-1] if native else ""
    except OSError:
        result["native_progress"] = ""
    if job["status"] in {"completed", "partial"}:
        audio = preferred_audio(output)
        result["audio_url"] = f"/media/{job['id']}/{audio.name}" if audio else None
        result["download_url"] = f"/download/{job['id']}/{quote(audio_filename(job, audio.suffix), safe='')}" if audio else None
        result["score_url"] = f"/media/{job['id']}/score.abc" if (output / "score.abc").exists() else None
    return result


class Handler(BaseHTTPRequestHandler):
    def permitted_host(self):
        return self.headers.get("Host") in {f"127.0.0.1:{self.server.server_port}", f"localhost:{self.server.server_port}"}

    def reply(self, status, data):
        raw = json.dumps(data, ensure_ascii=False).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(raw)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.end_headers()
        self.wfile.write(raw)

    def file(self, p, content_type=None, filename=None, attachment=False):
        if not p.is_file():
            return self.reply(404, {"error": "文件尚未生成。"})
        size = p.stat().st_size
        start, end, status = 0, size - 1, 200
        rng = self.headers.get("Range")
        if rng:
            m = re.fullmatch(r"bytes=(\d+)-(\d*)", rng)
            if not m:
                return self.reply(416, {"error": "不支持的音频范围。"})
            start = int(m[1])
            end = min(int(m[2]) if m[2] else end, end)
            if start > end:
                return self.reply(416, {"error": "音频范围越界。"})
            status = 206
        self.send_response(status)
        self.send_header("Content-Type", content_type or mimetypes.guess_type(p.name)[0] or "application/octet-stream")
        self.send_header("Content-Length", str(end - start + 1))
        self.send_header("Accept-Ranges", "bytes")
        if filename:
            disposition = "attachment" if attachment else "inline"
            fallback = "Rei-song" + p.suffix
            self.send_header("Content-Disposition", f"{disposition}; filename=\"{fallback}\"; filename*=UTF-8''{quote(filename, safe='')}")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Security-Policy", "default-src 'self'; style-src 'self'; script-src 'self'; media-src 'self'; frame-ancestors 'none'")
        if status == 206:
            self.send_header("Content-Range", f"bytes {start}-{end}/{size}")
        self.end_headers()
        with p.open("rb") as stream:
            stream.seek(start)
            left = end - start + 1
            while left:
                chunk = stream.read(min(left, 65536))
                if not chunk:
                    break
                self.wfile.write(chunk)
                left -= len(chunk)

    def do_GET(self):
        if not self.permitted_host():
            return self.reply(403, {"error": "仅允许本机访问。"})
        path = urlparse(self.path).path
        if path in ("/library", "/library.css", "/library.js"):
            return self.file(ROOT / "studio-ui" / ("library.html" if path == "/library" else path[1:]))
        if path == "/api/library":
            from nightly.library import catalog
            with LOCK:
                return self.reply(200, catalog(ROOT, DATA, JOBS))
        if path in ("/", "/studio.css", "/studio.js"):
            return self.file(ROOT / "studio-ui" / ({"": "index.html", "/": "index.html"}.get(path, path[1:])))
        if path == "/api/state":
            collection = read_state(DATA)
            with LOCK:
                return self.reply(200, {"token": TOKEN, "active": ACTIVE, "external_busy": external_busy(),
                                       "limits": {"lyrics":4000,"style":1000,"max_audio_seconds":360},
                                       "jobs": [public(j) for j in sorted(JOBS.values(), key=lambda j: j["created_at"], reverse=True) if not is_deleted(collection, j)][:40]})
        m = re.fullmatch(r"/download/([a-f0-9]{16})/[^/]+", path)
        if m and m[1] in JOBS and not is_deleted(read_state(DATA), JOBS[m[1]]) and JOBS[m[1]]["status"] in {"completed", "partial"}:
            audio = preferred_audio(DATA / m[1] / "output")
            if audio is None: return self.reply(404, {"error": "音频文件不存在。"})
            return self.file(audio, "audio/mpeg" if audio.suffix == ".mp3" else "audio/flac",
                             filename=audio_filename(JOBS[m[1]], audio.suffix), attachment=True)
        m = re.fullmatch(r"/media/([a-f0-9]{16})/(audio\.flac|audio\.mp3|score\.abc)", path)
        if m and m[1] in JOBS and not is_deleted(read_state(DATA), JOBS[m[1]]) and JOBS[m[1]]["status"] in {"completed", "partial"}:
            return self.file(DATA / m[1] / "output" / m[2], "audio/mpeg" if m[2] == "audio.mp3" else "audio/flac" if m[2] == "audio.flac" else "text/plain; charset=utf-8", filename=audio_filename(JOBS[m[1]], Path(m[2]).suffix) if m[2].startswith("audio.") else None)
        m = re.fullmatch(r"/archive/(outputs|public)/([a-zA-Z0-9_-]+)/(audio\.flac|audio\.mp3)", path)
        if m:
            return self.file(ROOT / m[1] / m[2] / m[3], "audio/mpeg" if m[3] == "audio.mp3" else "audio/flac")
        return self.reply(404, {"error": "入口不存在。"})

    def do_POST(self):
        global ACTIVE
        origin = self.headers.get("Origin")
        expected = {f"http://127.0.0.1:{self.server.server_port}", f"http://localhost:{self.server.server_port}"}
        if not self.permitted_host() or origin not in expected or self.headers.get("X-Studio-Token") != TOKEN:
            return self.reply(403, {"error": "页面验证失效，请刷新页面再试。"})
        try:
            size = int(self.headers.get("Content-Length", "0"))
            if not 0 < size <= 32768:
                raise ValueError("请求大小不符合要求。")
            body = json.loads(self.rfile.read(size))
            if not isinstance(body, dict):raise ValueError("输入必须是对象。")
            path = urlparse(self.path).path
            mutation = re.fullmatch(r"/api/library/tracks/([a-zA-Z0-9_-]+)/(favorite|delete)", path)
            if mutation:
                with LOCK:
                    if mutation[2] == 'favorite':
                        result = favorite(ROOT, DATA, JOBS, mutation[1], body.get('favorite'))
                    else:
                        result = delete_track(ROOT, DATA, JOBS, mutation[1], body.get('confirm_title'))
                return self.reply(200, result)
            if path == "/api/jobs":
                req = validate(body)
                options = validate_options(body)
                from nightly.library import validate_metadata
                metadata = validate_metadata(body.get("metadata", {}))
                client_key = body.get("client_key")
                if client_key is not None and (not isinstance(client_key, str) or not re.fullmatch(r"[a-zA-Z0-9_-]{1,120}", client_key)):
                    raise ValueError("无效的批次标识。")
                user_style = req["style"]
                if options["target_seconds"]:
                    req["style"] += f", complete song approximately {options['target_seconds']} seconds, continuous musical development, recurring chorus melody, natural resolved ending"
                with LOCK:
                    if deleted_request(read_state(DATA), client_key, metadata):
                        return self.reply(410, {"error":"此歌曲已由你永久删除，不会重新生成。", "status":"deleted"})
                    if client_key:
                        existing = next((j for j in JOBS.values() if j.get("client_key") == client_key), None)
                        if existing:
                            if (any(existing["request"].get(k) != req.get(k) for k in ("lyrics", "cot", "seed"))
                                    or existing["user_style"] != user_style or existing["options"] != options):
                                return self.reply(409, {"error": "该批次标识已对应其他输入。"})
                            return self.reply(200, {"job": public(existing)})
                    if ACTIVE or external_busy():
                        return self.reply(409, {"error": "已有一首正在生成，请等待或先取消。"})
                    job_id = secrets.token_hex(8)
                    folder = DATA / job_id
                    folder.mkdir()
                    req["id"] = job_id
                    (folder / "input.json").write_text(json.dumps(req, ensure_ascii=False, indent=2))
                    job = dict(id=job_id, status="queued", created_at=time.time(), request=req, options=options, user_style=user_style, metadata=metadata, client_key=client_key)
                    JOBS[job_id] = job
                    ACTIVE = job_id
                    persist(job)
                    threading.Thread(target=run_job, args=(job,), daemon=True).start()
                    return self.reply(202, {"job": public(job)})
            m = re.fullmatch(r"/api/jobs/([a-f0-9]{16})/cancel", path)
            if m:
                with LOCK:
                    job = JOBS.get(m[1])
                    if not job:
                        return self.reply(404, {"error": "任务不存在。"})
                    if job["status"] not in FINAL:
                        job["cancel_requested"] = True
                        persist(job)
                    return self.reply(200, {"job": public(job)})
            return self.reply(404, {"error": "入口不存在。"})
        except CollectionError as exc:
            return self.reply(exc.status, {"error": str(exc)})
        except OSError:
            return self.reply(500, {"error":"本地文件操作未完成，请检查磁盘权限后重试。删除记录已保留。"})
        except (ValueError, TypeError, UnicodeError) as exc:
            return self.reply(400, {"error": str(exc)})


def stop(signum, frame):
    global STOPPING
    STOPPING = True
    with LOCK:
        if PROCESS and PROCESS.poll() is None:
            os.killpg(PROCESS.pid, signal.SIGTERM)
            try:
                PROCESS.wait(timeout=5)
            except subprocess.TimeoutExpired:
                os.killpg(PROCESS.pid, signal.SIGKILL)
                PROCESS.wait()
        if ACTIVE:
            JOBS[ACTIVE].update(status="interrupted", finished_at=time.time(),
                                error="服务已停止，请重新生成。")
            persist(JOBS[ACTIVE])
    raise SystemExit(0)


class StudioHTTPServer(ThreadingHTTPServer):
    # Browsers reconnect several resources together, especially after wake-up.
    # The Python 3.11 default backlog of five drops these small local bursts.
    request_queue_size = 64


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--port", type=int, default=8766)
    args = parser.parse_args()
    signal.signal(signal.SIGTERM, stop)
    signal.signal(signal.SIGINT, stop)
    print(f"YuE2 Studio http://127.0.0.1:{args.port}/", flush=True)
    StudioHTTPServer(("127.0.0.1", args.port), Handler).serve_forever()
