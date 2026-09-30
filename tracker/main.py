"""
llama.izdrail.com tracker - transparent gateway proxy + query log UI.

Sits in front of the llama.cpp inference engine, forwards
every request byte-transparently (streaming included), and logs query/response
pairs into SQLite. / and all non-/track paths proxy to llama-server, whose
built-in chat web UI is therefore served at /. A small web UI under /track
lets Stefan browse, search and reuse past queries and outputs.

Inference passthrough preserves the gateway's current behavior: the API is
reachable today with no auth, and the separate proposal to gate it was parked
by the owner pending his decision. This module therefore defaults to OPEN
passthrough so existing callers do not break, and ships an opt-in token gate
(TRACKER_API_TOKEN) that host can enable only if/when the owner asks for it.
Only /track/* (UI + tracking API) is behind the login.
"""
import hashlib
import hmac
import json
import os
import sqlite3
import time
from contextlib import asynccontextmanager

import httpx
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import (HTMLResponse, JSONResponse, PlainTextResponse,
                               StreamingResponse)

# --- config (all via env, set by host, never committed) -------------------
UPSTREAM = os.environ.get("TRACKER_UPSTREAM", "http://llama-cpp:8080").rstrip("/")
DB_PATH = os.environ.get("TRACKER_DB_PATH", "/data/tracker.db")
MAX_LOG_MB = int(os.environ.get("TRACKER_MAX_LOG_MB", "2048"))
MAX_BODY_KB = int(os.environ.get("TRACKER_MAX_BODY_KB", "256"))
API_TOKEN = os.environ.get("TRACKER_API_TOKEN", "")  # blank = open (current behavior)
AUTH_USER = os.environ.get("AUTH_USERNAME", "")
AUTH_PASS = os.environ.get("AUTH_PASSWORD", "")
SECRET = os.environ.get("SESSION_SECRET") or hashlib.sha256(
    f"{AUTH_USER}:{AUTH_PASS}".encode()).hexdigest()
COOKIE_NAME = "tracker_session"
SESSION_TTL = 12 * 3600
LOCKOUT_TRIES = 5
LOCKOUT_WINDOW = 15 * 60

HOP_BY_HOP = {"connection", "keep-alive", "proxy-authenticate",
              "proxy-authorization", "te", "trailers", "transfer-encoding",
              "upgrade", "host", "content-length", "accept-encoding", "cookie"}

SCHEMA = """
CREATE TABLE IF NOT EXISTS entries (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  ts REAL NOT NULL,
  method TEXT NOT NULL,
  path TEXT NOT NULL,
  model TEXT,
  status INTEGER,
  latency_ms INTEGER,
  error TEXT,
  request_body TEXT,
  response_body TEXT,
  prompt_text TEXT,
  output_text TEXT,
  prompt_tokens INTEGER,
  completion_tokens INTEGER,
  rerun_of INTEGER,
  truncated INTEGER DEFAULT 0
);
CREATE INDEX IF NOT EXISTS idx_entries_ts ON entries(ts DESC);
CREATE INDEX IF NOT EXISTS idx_entries_model ON entries(model);
CREATE INDEX IF NOT EXISTS idx_entries_path ON entries(path);
"""

client: httpx.AsyncClient
_failed_logins: dict[str, list[float]] = {}


def db() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    return conn


@asynccontextmanager
async def lifespan(app: FastAPI):
    global client
    os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)
    with db() as conn:
        conn.executescript(SCHEMA)
    client = httpx.AsyncClient(timeout=httpx.Timeout(600.0, connect=10.0))
    yield
    await client.aclose()


app = FastAPI(title="llama tracker", lifespan=lifespan)


# --- auth ------------------------------------------------------------------
def _sign(payload: str) -> str:
    return hmac.new(SECRET.encode(), payload.encode(), hashlib.sha256).hexdigest()


def _make_cookie() -> str:
    exp = str(int(time.time()) + SESSION_TTL)
    return f"{exp}.{_sign(exp)}"


def _check_cookie(request: Request) -> bool:
    if not AUTH_USER or not AUTH_PASS:
        return False
    raw = request.cookies.get(COOKIE_NAME, "")
    try:
        exp, sig = raw.split(".", 1)
    except ValueError:
        return False
    return hmac.compare_digest(sig, _sign(exp)) and int(exp) > time.time()


def require_auth(request: Request):
    if not _check_cookie(request):
        raise HTTPException(status_code=401, detail="login required")


def _locked_out(ip: str) -> bool:
    now = time.time()
    tries = [t for t in _failed_logins.get(ip, []) if now - t < LOCKOUT_WINDOW]
    _failed_logins[ip] = tries
    return len(tries) >= LOCKOUT_TRIES


# --- text extraction (best effort, OpenAI + Ollama native) -----------------
def extract_prompt(path: str, body: dict) -> str:
    try:
        if isinstance(body.get("messages"), list):
            return "\n".join(f"{m.get('role', '?')}: {m.get('content', '')}"
                             for m in body["messages"])
        if isinstance(body.get("prompt"), str):
            return body["prompt"]
        if isinstance(body.get("input"), str):
            return body["input"]
    except Exception:
        pass
    return ""


def extract_output(path: str, body_text: str, is_sse: bool) -> tuple[str, dict]:
    """Return (output_text, usage_dict). Handles plain JSON and SSE streams."""
    usage: dict = {}
    if is_sse:
        out_parts = []
        for line in body_text.splitlines():
            line = line.strip()
            if not line.startswith("data:"):
                continue
            data = line[5:].strip()
            if data == "[DONE]":
                continue
            try:
                obj = json.loads(data)
            except Exception:
                continue
            if "usage" in obj and obj["usage"]:
                usage = obj["usage"]
            for choice in obj.get("choices", []) or []:
                delta = choice.get("delta") or choice.get("message") or {}
                if isinstance(delta.get("content"), str):
                    out_parts.append(delta["content"])
                elif isinstance(choice.get("text"), str):
                    out_parts.append(choice["text"])
            if isinstance(obj.get("response"), str):  # ollama native stream
                out_parts.append(obj["response"])
        return "".join(out_parts), usage
    try:
        obj = json.loads(body_text)
    except Exception:
        return body_text, usage
    usage = obj.get("usage") or {}
    out_parts = []
    for choice in obj.get("choices", []) or []:
        msg = choice.get("message") or {}
        if isinstance(msg.get("content"), str):
            out_parts.append(msg["content"])
        elif isinstance(choice.get("text"), str):
            out_parts.append(choice["text"])
    if isinstance(obj.get("response"), str):
        out_parts.append(obj["response"])
    if isinstance(obj.get("message"), dict) and isinstance(obj["message"].get("content"), str):
        out_parts.append(obj["message"]["content"])
    return "".join(out_parts), usage


# --- logging ----------------------------------------------------------------
def log_entry(*, method: str, path: str, request_body: bytes,
              response_text: str, status: int, latency_ms: int,
              error: str = "", rerun_of: int | None = None,
              is_sse: bool = False):
    truncated = 0
    cap = MAX_BODY_KB * 1024
    req_text = request_body.decode("utf-8", "replace")
    if len(req_text) > cap:
        req_text, truncated = req_text[:cap] + "\n...[truncated]", 1
    if len(response_text) > cap:
        response_text, truncated = response_text[:cap] + "\n...[truncated]", 1

    model, prompt = None, ""
    try:
        req_json = json.loads(req_text)
        model = req_json.get("model")
        prompt = extract_prompt(path, req_json)
    except Exception:
        pass
    output, usage = extract_output(path, response_text, is_sse)

    try:
        with db() as conn:
            conn.execute(
                "INSERT INTO entries (ts, method, path, model, status, latency_ms,"
                " error, request_body, response_body, prompt_text, output_text,"
                " prompt_tokens, completion_tokens, rerun_of, truncated)"
                " VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (time.time(), method, path, model, status, latency_ms, error,
                 req_text, response_text, prompt, output,
                 usage.get("prompt_tokens"), usage.get("completion_tokens"),
                 rerun_of, truncated))
        _prune()
    except Exception as exc:  # logging must never break inference traffic
        print(f"tracker: log write failed: {exc}", flush=True)


def _prune():
    try:
        if os.path.getsize(DB_PATH) < MAX_LOG_MB * 1024 * 1024:
            return
        with db() as conn:
            cutoff = conn.execute(
                "SELECT id FROM entries ORDER BY id DESC LIMIT 1 OFFSET "
                "(SELECT MAX(1, COUNT(*) * 9 / 10) FROM entries)").fetchone()
            if cutoff:
                conn.execute("DELETE FROM entries WHERE id <= ?", (cutoff[0],))
    except Exception as exc:
        print(f"tracker: prune failed: {exc}", flush=True)


# --- tracking API (login required) ------------------------------------------
@app.get("/track/health")
def health():
    # Public for Coolify healthchecks; deliberately discloses nothing.
    return {"status": "ok"}


@app.post("/track/api/login")
async def login(request: Request):
    ip = request.client.host if request.client else "?"
    if not AUTH_USER or not AUTH_PASS:
        raise HTTPException(status_code=503, detail="auth not configured")
    if _locked_out(ip):
        raise HTTPException(status_code=429, detail="too many attempts, wait 15 minutes")
    form = await request.json()
    if (hmac.compare_digest(form.get("username", ""), AUTH_USER)
            and hmac.compare_digest(form.get("password", ""), AUTH_PASS)):
        _failed_logins.pop(ip, None)
        resp = JSONResponse({"ok": True})
        resp.set_cookie(COOKIE_NAME, _make_cookie(), max_age=SESSION_TTL,
                        httponly=True, samesite="lax", secure=True)
        return resp
    _failed_logins.setdefault(ip, []).append(time.time())
    raise HTTPException(status_code=401, detail="bad credentials")


@app.post("/track/api/logout")
def logout():
    resp = JSONResponse({"ok": True})
    resp.delete_cookie(COOKIE_NAME)
    return resp


@app.get("/track/api/entries")
def list_entries(request: Request, q: str = "", model: str = "",
                 path: str = "", status: int = 0, page: int = 1,
                 per_page: int = 50):
    require_auth(request)
    where, args = ["1=1"], []
    if q:
        where.append("(prompt_text LIKE ? OR output_text LIKE ?)")
        args += [f"%{q}%", f"%{q}%"]
    if model:
        where.append("model = ?")
        args.append(model)
    if path:
        where.append("path LIKE ?")
        args.append(f"{path}%")
    if status:
        where.append("status = ?")
        args.append(status)
    sql_where = " AND ".join(where)
    per_page = max(1, min(per_page, 200))
    page = max(1, page)
    with db() as conn:
        total = conn.execute(f"SELECT COUNT(*) c FROM entries WHERE {sql_where}",
                             args).fetchone()["c"]
        rows = conn.execute(
            f"SELECT id, ts, method, path, model, status, latency_ms, error,"
            f" substr(prompt_text,1,200) prompt_snip, substr(output_text,1,200) output_snip,"
            f" prompt_tokens, completion_tokens, rerun_of, truncated"
            f" FROM entries WHERE {sql_where} ORDER BY id DESC LIMIT ? OFFSET ?",
            args + [per_page, (page - 1) * per_page]).fetchall()
        models = [r[0] for r in conn.execute(
            "SELECT DISTINCT model FROM entries WHERE model IS NOT NULL"
            " ORDER BY model").fetchall()]
    return {"total": total, "page": page, "per_page": per_page, "models": models,
            "entries": [dict(r) for r in rows]}


@app.get("/track/api/entries/{entry_id}")
def get_entry(entry_id: int, request: Request):
    require_auth(request)
    with db() as conn:
        row = conn.execute("SELECT * FROM entries WHERE id = ?",
                           (entry_id,)).fetchone()
    if not row:
        raise HTTPException(status_code=404)
    return dict(row)


@app.post("/track/api/entries/{entry_id}/rerun")
async def rerun_entry(entry_id: int, request: Request):
    require_auth(request)
    with db() as conn:
        row = conn.execute("SELECT * FROM entries WHERE id = ?",
                           (entry_id,)).fetchone()
    if not row:
        raise HTTPException(status_code=404)
    body = row["request_body"].encode()
    started = time.time()
    try:
        upstream = await client.request(row["method"], f"{UPSTREAM}/{row['path']}",
                                        content=body,
                                        headers={"content-type": "application/json"})
        resp_text = upstream.text
        status = upstream.status_code
        error = ""
    except httpx.HTTPError as exc:
        resp_text, status, error = "", 502, f"upstream unreachable: {exc}"
    latency = int((time.time() - started) * 1000)
    is_sse = "data:" in resp_text[:2000]
    log_entry(method=row["method"], path=row["path"], request_body=body,
              response_text=resp_text, status=status, latency_ms=latency,
              error=error, rerun_of=entry_id, is_sse=is_sse)
    with db() as conn:
        new_id = conn.execute("SELECT MAX(id) m FROM entries").fetchone()["m"]
    return {"ok": not error, "new_entry_id": new_id, "status": status,
            "latency_ms": latency, "error": error}


@app.get("/track/api/stats")
def stats(request: Request):
    require_auth(request)
    with db() as conn:
        total = conn.execute("SELECT COUNT(*) c FROM entries").fetchone()["c"]
        today = conn.execute(
            "SELECT COUNT(*) c FROM entries WHERE ts > ?",
            (time.time() - 86400,)).fetchone()["c"]
        by_model = [dict(r) for r in conn.execute(
            "SELECT COALESCE(model,'?') model, COUNT(*) n FROM entries"
            " GROUP BY model ORDER BY n DESC LIMIT 10").fetchall()]
        by_path = [dict(r) for r in conn.execute(
            "SELECT path, COUNT(*) n FROM entries GROUP BY path"
            " ORDER BY n DESC LIMIT 10").fetchall()]
    size = os.path.getsize(DB_PATH) if os.path.exists(DB_PATH) else 0
    return {"total": total, "last_24h": today, "by_model": by_model,
            "by_path": by_path, "db_bytes": size, "upstream": UPSTREAM}


# --- UI ---------------------------------------------------------------------
with open(os.path.join(os.path.dirname(__file__), "ui.html"),
          encoding="utf-8") as _f:
    UI_HTML = _f.read()


@app.get("/track", response_class=HTMLResponse)
@app.get("/track/", response_class=HTMLResponse)
def ui():
    return HTMLResponse(UI_HTML)


@app.exception_handler(404)
async def not_found(request: Request, exc):
    if request.url.path.startswith("/track"):
        return PlainTextResponse("not found", status_code=404)
    return JSONResponse({"error": "not found"}, status_code=404)


# --- proxy ------------------------------------------------------------------
@app.api_route("/{path:path}",
               methods=["GET", "POST", "PUT", "PATCH", "DELETE", "HEAD", "OPTIONS"])
async def proxy(path: str, request: Request):
    if path.startswith("track/") or path.startswith("track"):
        raise HTTPException(status_code=404)
    if API_TOKEN:
        auth = request.headers.get("authorization", "")
        if auth != f"Bearer {API_TOKEN}":
            return JSONResponse({"error": "unauthorized"}, status_code=401)
    body = await request.body()
    headers = {k: v for k, v in request.headers.items()
               if k.lower() not in HOP_BY_HOP}
    started = time.time()
    try:
        req = client.build_request(request.method, f"{UPSTREAM}/{path}",
                                   params=request.query_params,
                                   content=body, headers=headers)
        upstream = await client.send(req, stream=True)
    except httpx.HTTPError as exc:
        latency = int((time.time() - started) * 1000)
        log_qs = str(request.query_params)
        log_entry(method=request.method,
                  path=f"{path}?{log_qs}" if log_qs else path,
                  request_body=body, response_text="", status=502,
                  latency_ms=latency, error=f"upstream unreachable: {exc}")
        return JSONResponse({"error": f"upstream unreachable: {exc}"},
                            status_code=502)

    qs = str(request.query_params)
    log_path = f"{path}?{qs}" if qs else path
    is_sse = "text/event-stream" in upstream.headers.get("content-type", "")
    resp_headers = {k: v for k, v in upstream.headers.items()
                    if k.lower() not in HOP_BY_HOP}
    chunks: list[bytes] = []

    async def relay():
        try:
            async for chunk in upstream.aiter_raw():
                chunks.append(chunk)
                yield chunk
        finally:
            await upstream.aclose()
            latency = int((time.time() - started) * 1000)
            log_entry(method=request.method, path=log_path, request_body=body,
                      response_text=b"".join(chunks).decode("utf-8", "replace"),
                      status=upstream.status_code, latency_ms=latency,
                      is_sse=is_sse)

    return StreamingResponse(relay(), status_code=upstream.status_code,
                             headers=resp_headers)



