"""FastAPI server for devopen.

Endpoints:
    GET  /health                          liveness
    POST /open    {repo, branch?}         Bearer token; streams the build log
    GET  /open?token=..&repo=..&branch=.. one-tap from a browser; streams the log

Only one open runs at a time: concurrent requests get 409 while a build is in
progress. The stream ends with a "[devopen] SUCCESS|FAILED" marker line.
"""

import asyncio
import queue
import secrets
import threading

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import StreamingResponse

from . import config
from .opener import DevopenError, open_repo

app = FastAPI(title="devopen")
_cfg = config.load()

_lock = threading.Lock()


def _authorized(request: Request, token: str) -> bool:
    expected = _cfg.get("token", "")
    if not expected:
        return False
    if token:
        return secrets.compare_digest(expected, token)
    header = request.headers.get("Authorization", "")
    if header.startswith("Bearer "):
        return secrets.compare_digest(expected, header[len("Bearer "):].strip())
    return False


def _stream_open(repo, branch):
    """Run the open flow in a thread; yield log lines as an async generator."""
    log_q = queue.Queue()
    done = threading.Event()
    result = {"exit_code": None, "error": None, "uri": None}

    def sink(line):
        log_q.put(line)

    def worker():
        try:
            result["uri"] = open_repo(repo, branch=branch, on_log=sink)
            result["exit_code"] = 0
        except DevopenError as e:
            result["exit_code"] = 1
            result["error"] = str(e)
            sink(f"[devopen] ERROR: {e}")
        except Exception as e:  # noqa: BLE001 - report anything
            result["exit_code"] = 2
            result["error"] = str(e)
            sink(f"[devopen] UNEXPECTED ERROR: {e}")
        finally:
            done.set()
            log_q.put(None)

    threading.Thread(target=worker, daemon=True).start()

    async def gen():
        try:
            while True:
                try:
                    line = log_q.get_nowait()
                except queue.Empty:
                    if done.is_set():
                        break
                    await asyncio.sleep(0.2)
                    continue
                if line is None:
                    break
                yield line + "\n"
            if result["exit_code"] == 0:
                yield f"[devopen] SUCCESS {result['uri']}\n"
            else:
                yield f"[devopen] FAILED: {result['error']}\n"
        finally:
            _lock.release()

    return gen()


def _start_open(request: Request, token, repo, branch):
    if not _authorized(request, token):
        raise HTTPException(status_code=401, detail="unauthorized")
    if not repo:
        raise HTTPException(status_code=400, detail="repo is required")
    if not _lock.acquire(blocking=False):
        raise HTTPException(status_code=409, detail="an open is already running")
    return StreamingResponse(_stream_open(repo, branch), media_type="text/plain")


@app.get("/health")
def health():
    return {"status": "ok", "service": "devopen"}


@app.post("/open")
async def open_post(request: Request, repo: str = None, branch: str = None, token: str = None):
    body = {}
    try:
        body = await request.json()
    except Exception:
        body = {}
    repo = repo or body.get("repo")
    branch = branch or body.get("branch")
    return _start_open(request, token, repo, branch)


@app.get("/open")
def open_get(request: Request, token: str = None, repo: str = None, branch: str = None):
    return _start_open(request, token, repo, branch)
