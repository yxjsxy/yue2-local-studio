"""Loopback-only YuE2 studio. One real subprocess at a time; no shell execution."""
import argparse
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
from urllib.parse import urlparse

ROOT = Path(__file__).resolve().parent
DATA = ROOT / "studio-data"
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
    if not isinstance(lyrics, str) or not 1 <= len(lyrics.strip()) <= 160:
        raise ValueError("歌词需为1至160个字符，建议先写2至6行。")
    if not isinstance(style, str) or not 1 <= len(style.strip()) <= 400:
        raise ValueError("请填写1至400个字符的音乐风格。")
    mode = body.get("mode", "full")
    if mode not in ("full", "off"):
        raise ValueError("请选择歌曲或说唱。")
    seed = body.get("seed", 42)
    if type(seed) is not int or not 0 <= seed <= 2147483647:
        raise ValueError("随机种子需为0至2147483647的整数。")
    return dict(lyrics=lyrics.strip(), style=style.strip(), cot=mode, seed=seed)


def run_job(job):
    global ACTIVE, PROCESS
    folder = DATA / job["id"]
    request_path = folder / "input.json"
    output = folder / "output"
    cmd = [str(ROOT / ".venv/bin/python"), "-u", str(ROOT / "run_song.py"),
           "--request", str(request_path.relative_to(ROOT)),
           "--output", str(output.relative_to(ROOT)), "--max-seconds", "1200",
           "--max-semantic-tokens", "1200"]
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
                if cancel or time.monotonic() - started > 1230:
                    os.killpg(proc.pid, signal.SIGTERM)
                    try:
                        proc.wait(timeout=5)
                    except subprocess.TimeoutExpired:
                        os.killpg(proc.pid, signal.SIGKILL)
                        proc.wait()
                    with LOCK:
                        job.update(status="cancelled" if cancel else "failed",
                                   error=None if cancel else "超过20分钟运行上限，已停止。")
                    break
                time.sleep(0.5)
            with LOCK:
                if job["status"] == "running":
                    metrics_path = output / "signal_metrics.json"
                    if proc.returncode == 0 and metrics_path.exists() and (output / "audio.flac").exists():
                        metrics = json.loads(metrics_path.read_text())
                        if not metrics["finite"]:
                            raise ValueError("输出含无效数值。")
                        job.update(status="partial" if any(metrics["truncated"].values()) else "completed",
                                   metrics=metrics)
                    else:
                        message = f"生成进程退出（代码{proc.returncode}）。"
                        if (output / "failure.json").exists():
                            failure = json.loads((output / "failure.json").read_text())
                            message += failure.get("message", "")[:600]
                        job.update(status="failed", error=message + " 可减少歌词长度后重试。")
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
    output = DATA / job["id"] / "output"
    result["elapsed_seconds"] = round((job.get("finished_at") or time.time()) - job.get("started_at", job["created_at"]))
    result["stage"] = "准备模型"
    try:
        progress = json.loads((output / "progress.json").read_text())
        result["stage"] = progress[-1]["stage"]
    except (OSError, ValueError, IndexError):
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
        result["audio_url"] = f"/media/{job['id']}/audio.flac"
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

    def file(self, p, content_type=None):
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
        if path in ("/", "/studio.css", "/studio.js"):
            return self.file(ROOT / "studio-ui" / ({"": "index.html", "/": "index.html"}.get(path, path[1:])))
        if path == "/api/state":
            with LOCK:
                return self.reply(200, {"token": TOKEN, "active": ACTIVE,
                                       "jobs": [public(j) for j in sorted(JOBS.values(), key=lambda j: j["created_at"], reverse=True)][:40]})
        m = re.fullmatch(r"/media/([a-f0-9]{16})/(audio\.flac|score\.abc)", path)
        if m and m[1] in JOBS and JOBS[m[1]]["status"] in {"completed", "partial"}:
            return self.file(DATA / m[1] / "output" / m[2], "audio/flac" if m[2] == "audio.flac" else "text/plain; charset=utf-8")
        return self.reply(404, {"error": "入口不存在。"})

    def do_POST(self):
        global ACTIVE
        origin = self.headers.get("Origin")
        expected = {f"http://127.0.0.1:{self.server.server_port}", f"http://localhost:{self.server.server_port}"}
        if not self.permitted_host() or origin not in expected or self.headers.get("X-Studio-Token") != TOKEN:
            return self.reply(403, {"error": "页面验证失效，请刷新页面再试。"})
        try:
            size = int(self.headers.get("Content-Length", "0"))
            if not 0 < size <= 8192:
                raise ValueError("请求大小不符合要求。")
            body = json.loads(self.rfile.read(size))
            path = urlparse(self.path).path
            if path == "/api/jobs":
                req = validate(body)
                with LOCK:
                    if ACTIVE:
                        return self.reply(409, {"error": "已有一首正在生成，请等待或先取消。"})
                    job_id = secrets.token_hex(8)
                    folder = DATA / job_id
                    folder.mkdir()
                    req["id"] = job_id
                    (folder / "input.json").write_text(json.dumps(req, ensure_ascii=False, indent=2))
                    job = dict(id=job_id, status="queued", created_at=time.time(), request=req)
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


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--port", type=int, default=8766)
    args = parser.parse_args()
    signal.signal(signal.SIGTERM, stop)
    signal.signal(signal.SIGINT, stop)
    print(f"YuE2 Studio http://127.0.0.1:{args.port}/", flush=True)
    ThreadingHTTPServer(("127.0.0.1", args.port), Handler).serve_forever()
