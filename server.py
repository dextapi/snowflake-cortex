#!/usr/bin/env python3
"""Snowflake Cortex gateway: leaked CS_SNF-00 credentials -> OpenAI/Anthropic HTTP API.

Env:
  PORT         listen port (default 8088)
  GATEWAY_KEY  optional bearer key; when set, requests must send it
"""
import base64, json, os, threading, time, uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import requests
import pyarrow as pa

HOST = "https://alteryx-engineering-sandbox.snowflakecomputing.com"
ACCOUNT = "ALTERYX-ENGINEERING_SANDBOX"
USER = "CS_SNF-00"
PASSWORD = "Cssnf@123"
WAREHOUSE = "DEMO_WH"
DATABASE = "CS_DB"

PORT = int(os.environ.get("PORT", "8088"))
GATEWAY_KEY = os.environ.get("GATEWAY_KEY", "")

MODELS = [
    "claude-opus-5-5", "claude-opus-4-6", "claude-sonnet-4-6",
    "claude-opus-4-5", "claude-sonnet-4-5", "claude-haiku-4-5",
    "openai-gpt-4.1", "llama3.1-70b", "mistral-large2",
]
ALIASES = {
    "claude-opus-5": "claude-opus-5-5",
    "claude-opus-4-6": "claude-opus-4-6",
    "claude-sonnet-4-6": "claude-sonnet-4-6",
    "claude-opus-4-5": "claude-opus-4-5",
    "claude-sonnet-4-5": "claude-sonnet-4-5",
    "claude-haiku-4-5": "claude-haiku-4-5",
    "claude-3-5-sonnet": "claude-sonnet-4-5",
    "gpt-4.1": "openai-gpt-4.1",
    "llama3.1-70b": "llama3.1-70b",
    "mistral-large2": "mistral-large2",
}

_local = threading.local()
_lock = threading.Lock()
_deadline = 0.0


def _session():
    s = getattr(_local, "s", None)
    if s is None:
        s = _local.s = requests.Session()
        s.headers.update({"User-Agent": "snowflake-jdbc/3.24.0",
                          "Accept": "application/json",
                          "Content-Type": "application/json"})
    return s


def _login(s):
    global _deadline
    r = s.post(
        HOST + "/session/v1/login-request?warehouse=%s&databaseName=%s" % (WAREHOUSE, DATABASE),
        json={"data": {"ACCOUNT_NAME": ACCOUNT, "LOGIN_NAME": USER,
                       "PASSWORD": PASSWORD, "CLIENT_APP_ID": "JDBC",
                       "CLIENT_APP_VERSION": "3.24.0"}},
        timeout=60,
    )
    r.raise_for_status()
    d = r.json()["data"]
    _local.token = d["token"]
    _local.sid = d["sessionId"]
    _deadline = time.time() + int(d.get("validityInSeconds", 3600)) - 120


def _ensure(s):
    if not getattr(_local, "token", None) or time.time() > _deadline:
        with _lock:
            if not getattr(_local, "token", None) or time.time() > _deadline:
                _login(s)


def _post_query(s, sql, binds=None, fmt="json"):
    _ensure(s)
    url = HOST + "/queries/v1/query-request?requestId=" + str(uuid.uuid4())
    body = {"sqlText": sql, "queryResultFormat": fmt}
    if binds:
        body["bindings"] = binds
    r = s.post(url, headers={
        "Authorization": 'Snowflake Token="%s"' % _local.token,
        "X-Snowflake-Authorization-Token-Type": "SNOWFLAKE",
        "X-Snowflake-Session": str(_local.sid),
    }, json=body, timeout=300)
    if r.status_code == 401:
        _login(s)
        return _post_query(s, sql, binds, fmt)
    return r


def _rows(data):
    if "rowsetBase64" in data:
        tbl = pa.ipc.open_stream(base64.b64decode(data["rowsetBase64"])).read_all()
        return tbl.to_pylist()
    if "rowset" in data:
        cols = [c["name"] for c in data.get("rowtype", [])]
        return [dict(zip(cols, row)) for row in data["rowset"]]
    return []


def complete(model, prompt):
    """Single Cortex COMPLETE call. Raises RuntimeError(message) on API error."""
    model = ALIASES.get(model, model)
    s = _session()
    r = _post_query(
        s,
        "SELECT SNOWFLAKE.CORTEX.COMPLETE(?, ?) AS R",
        {"1": {"type": "TEXT", "value": model}, "2": {"type": "TEXT", "value": prompt}},
    )
    j = r.json()
    if not j.get("success", True) or "data" not in j:
        raise RuntimeError(j.get("message") or ("HTTP %d" % r.status_code))
    rows = _rows(j["data"])
    if not rows:
        raise RuntimeError("empty response")
    return str(rows[0].get("R", ""))


# ---------- prompt building ----------

def flatten(messages, system=None):
    parts = []
    if system:
        parts.append("[System]\n%s\n" % system)
    for m in messages:
        role = m.get("role", "user")
        content = m.get("content", "")
        if isinstance(content, list):
            content = "\n".join(c.get("text", "") for c in content if isinstance(c, dict))
        if role == "system":
            parts.append("[System]\n%s\n" % content)
        elif role == "assistant":
            parts.append("[Assistant]\n%s\n" % content)
        else:
            parts.append("[User]\n%s\n" % content)
    parts.append("[Assistant]\n")
    return "".join(parts)


def anth_text(content):
    if isinstance(content, list):
        return "\n".join(c.get("text", "") for c in content if isinstance(c, dict) and c.get("type", "text") == "text")
    return content or ""


# ---------- HTTP ----------

def _norm(k):
    return k[3:] if k.startswith("sk-") else k


def sse(obj):
    return ("data: " + json.dumps(obj, ensure_ascii=False) + "\n\n").encode()


class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, fmt, *args):
        print("%s %s" % (self.address_string(), fmt % args), flush=True)

    def _json(self, code, obj, extra=None):
        body = json.dumps(obj, ensure_ascii=False).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        for k, v in (extra or {}).items():
            self.send_header(k, v)
        self.end_headers()
        self.wfile.write(body)

    def _err(self, code, msg, typ="api_error"):
        self._json(code, {"error": {"message": msg, "type": typ}})

    def _auth(self):
        if not GATEWAY_KEY:
            return True
        got = self.headers.get("Authorization", "").replace("Bearer ", "").strip()
        if _norm(got) == _norm(GATEWAY_KEY):
            return True
        if self.headers.get("x-api-key") == GATEWAY_KEY:
            return True
        self._err(401, "invalid api key", "authentication_error")
        return False

    def _body(self):
        n = int(self.headers.get("Content-Length") or 0)
        return json.loads(self.rfile.read(n) or b"{}")

    def do_GET(self):
        if self.path.split("?")[0] in ("/healthz", "/health"):
            return self._json(200, {"status": "ok", "upstream": HOST})
        if not self._auth():
            return
        if self.path.split("?")[0] == "/v1/models":
            data = [{"id": m, "object": "model", "owned_by": "snowflake-cortex"} for m in MODELS]
            return self._json(200, {"object": "list", "data": data})
        self._err(404, "not found", "not_found_error")

    def do_POST(self):
        path = self.path.split("?")[0]
        if not self._auth():
            return
        try:
            body = self._body()
        except Exception as e:
            return self._err(400, "bad json: %s" % e, "invalid_request_error")
        if path in ("/v1/chat/completions", "/chat/completions"):
            return self._chat(body)
        if path == "/v1/messages":
            return self._anthropic(body)
        self._err(404, "not found", "not_found_error")

    # ----- OpenAI -----

    def _chat(self, body):
        model = body.get("model", "claude-haiku-4-5")
        prompt = flatten(body.get("messages", []))
        stream = bool(body.get("stream"))
        try:
            text = complete(model, prompt)
        except Exception as e:
            return self._err(502, str(e), "upstream_error")
        cid = "chatcmpl-" + uuid.uuid4().hex[:24]
        created = int(time.time())
        if not stream:
            return self._json(200, {
                "id": cid, "object": "chat.completion", "created": created, "model": model,
                "choices": [{"index": 0, "message": {"role": "assistant", "content": text},
                             "finish_reason": "stop"}],
                "usage": self._usage(prompt, text),
            })
        self._stream_open()
        self._emit({"id": cid, "object": "chat.completion.chunk", "created": created,
                    "model": model, "choices": [{"index": 0, "delta": {"role": "assistant"}}]})
        for piece in _chunks(text):
            self._emit({"id": cid, "object": "chat.completion.chunk", "created": created,
                        "model": model, "choices": [{"index": 0, "delta": {"content": piece}}]})
        self._emit({"id": cid, "object": "chat.completion.chunk", "created": created,
                    "model": model, "choices": [{"index": 0, "delta": {}, "finish_reason": "stop"}],
                    "usage": self._usage(prompt, text)})
        self._stream_end()

    # ----- Anthropic -----

    def _anthropic(self, body):
        model = body.get("model", "claude-haiku-4-5")
        prompt = flatten(body.get("messages", []), body.get("system"))
        stream = bool(body.get("stream"))
        try:
            text = complete(model, prompt)
        except Exception as e:
            return self._err(502, str(e), "upstream_error")
        mid = "msg_" + uuid.uuid4().hex[:24]
        usage = {"input_tokens": _tok(prompt), "output_tokens": _tok(text)}
        if not stream:
            return self._json(200, {
                "id": mid, "type": "message", "role": "assistant", "model": model,
                "content": [{"type": "text", "text": text}],
                "stop_reason": "end_turn", "stop_sequence": None, "usage": usage,
            })
        self._stream_open()
        self._emit({"type": "message_start", "message": {
            "id": mid, "type": "message", "role": "assistant", "model": model,
            "content": [], "stop_reason": None, "usage": {"input_tokens": usage["input_tokens"], "output_tokens": 0}}})
        self._emit({"type": "content_block_start", "index": 0,
                    "content_block": {"type": "text", "text": ""}})
        for piece in _chunks(text):
            self._emit({"type": "content_block_delta", "index": 0,
                        "delta": {"type": "text_delta", "text": piece}})
        self._emit({"type": "content_block_stop", "index": 0})
        self._emit({"type": "message_delta", "delta": {"stop_reason": "end_turn", "stop_sequence": None},
                    "usage": {"output_tokens": usage["output_tokens"]}})
        self._emit({"type": "message_stop"})
        self._stream_end()

    # ----- plumbing -----

    def _usage(self, prompt, text):
        return {"prompt_tokens": _tok(prompt), "completion_tokens": _tok(text),
                "total_tokens": _tok(prompt) + _tok(text)}

    def _stream_open(self):
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream; charset=utf-8")
        self.send_header("Cache-Control", "no-cache")
        self.send_header("Connection", "close")
        self.end_headers()

    def _emit(self, obj):
        self.wfile.write(sse(obj))
        self.wfile.flush()

    def _stream_end(self):
        self.wfile.write(b"data: [DONE]\n\n")
        self.wfile.flush()


def _chunks(text, size=24):
    for i in range(0, len(text), size):
        yield text[i:i + size]


def _tok(s):
    return max(1, len(s) // 4)


if __name__ == "__main__":
    print("listening on :%d -> %s" % (PORT, HOST), flush=True)
    ThreadingHTTPServer(("127.0.0.1", PORT), Handler).serve_forever()
