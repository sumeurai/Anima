"""
anima-web local server.

Usage:
    python app.py
    python app.py --port 8080
    python app.py --host 127.0.0.1 --port 3000

Environment variables (or .env file):
    ANIMA_PORT          - Server port (default: 19000)
    ANIMA_HOST          - Bind address (default: 0.0.0.0)
    ANIMA_API_BASE_URL  - Remote API base URL
    ANIMA_WS_URL        - Remote WebSocket URL
    OPENCLAW_API_URL    - OpenClaw chat completions endpoint
    OPENCLAW_TOKEN      - OpenClaw bearer token
    OPENCLAW_DEFAULT_AGENT - Default OpenClaw agent name
    OPENCLAW_SSH_HOST   - SSH host for remote OpenClaw (e.g. agent@10.10.5.24)
    OPENCLAW_WORKSPACE_BASE - Base dir for agent workspaces (default: ~/.openclaw)
"""

import argparse
import json
import os
import re
import sys
from datetime import datetime
from pathlib import Path

import requests as http_requests
from flask import Flask, send_from_directory, Response, request

ROOT_DIR = Path(__file__).resolve().parent
PROJECT_DIR = ROOT_DIR.parent
DIST_DIR = PROJECT_DIR / "dist"

# ---------------------------------------------------------------------------
# .env loader (no extra dependency)
# ---------------------------------------------------------------------------

def load_dotenv(path: Path) -> None:
    if not path.is_file():
        return
    with open(path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            m = re.match(r"^([A-Za-z_][A-Za-z0-9_]*)=(.*)$", line)
            if m:
                key, val = m.group(1), m.group(2).strip().strip("\"'")
                os.environ.setdefault(key, val)


load_dotenv(ROOT_DIR / ".env")

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

DEFAULT_PORT = 19000
DEFAULT_HOST = "0.0.0.0"
DEFAULT_API_BASE_URL = "https://sumi.sumeruai.com"
DEFAULT_WS_URL = ""

CONF_PORT = int(os.getenv("ANIMA_PORT", str(DEFAULT_PORT)))
CONF_HOST = os.getenv("ANIMA_HOST", DEFAULT_HOST)
CONF_API_BASE_URL = os.getenv("ANIMA_API_BASE_URL", DEFAULT_API_BASE_URL)
CONF_WS_URL = os.getenv("ANIMA_WS_URL", DEFAULT_WS_URL)

OPENCLAW_API_URL = os.getenv("OPENCLAW_API_URL", "")
OPENCLAW_TOKEN = os.getenv("OPENCLAW_TOKEN", "")
OPENCLAW_DEFAULT_AGENT = os.getenv("OPENCLAW_DEFAULT_AGENT", "main")
OPENCLAW_SSH_HOST = os.getenv("OPENCLAW_SSH_HOST", "")
OPENCLAW_WORKSPACE_BASE = os.getenv("OPENCLAW_WORKSPACE_BASE", "~/.openclaw")

VERSION = datetime.now().strftime("%Y%m%d_%H%M%S")

# ---------------------------------------------------------------------------
# Flask app
# ---------------------------------------------------------------------------

app = Flask(__name__, static_folder=None)


def _safe_log(msg: str) -> None:
    """Print to console without crashing on non-encodable characters (e.g. emoji on GBK terminals)."""
    try:
        sys.stdout.buffer.write((msg + "\n").encode("utf-8", errors="replace"))
        sys.stdout.buffer.flush()
    except Exception:
        pass


def _build_config_script() -> str:
    """Generate a <script> tag that sets window.BaseConfig before the Vue app loads."""
    config = {
        "apiBaseUrl": CONF_API_BASE_URL,
        "websocketUrl": CONF_WS_URL,
        "version": VERSION,
    }
    return f'<script>window.BaseConfig={json.dumps(config, ensure_ascii=False)};</script>'


def _inject_config(html: str) -> str:
    """Inject BaseConfig script into the HTML <head>."""
    tag = _build_config_script()
    if "<head>" in html:
        return html.replace("<head>", f"<head>\n    {tag}", 1)
    return tag + "\n" + html


_index_cache: str | None = None
_index_mtime: float = 0


def _get_index_html() -> str:
    global _index_cache, _index_mtime
    index_path = DIST_DIR / "index.html"
    if not index_path.is_file():
        return "<h1>dist/index.html not found. Run <code>npm run build</code> first.</h1>"
    mtime = index_path.stat().st_mtime
    if _index_cache is None or mtime != _index_mtime:
        raw = index_path.read_text(encoding="utf-8")
        _index_cache = _inject_config(raw)
        _index_mtime = mtime
    return _index_cache


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------

@app.route("/")
def index():
    return Response(_get_index_html(), mimetype="text/html")


@app.route("/<path:path>")
def static_or_fallback(path: str):
    file_path = DIST_DIR / path
    if file_path.is_file():
        return send_from_directory(DIST_DIR, path)
    # Vue Router history mode: return index.html for unmatched paths
    return Response(_get_index_html(), mimetype="text/html")


@app.route("/health")
def health():
    return {"status": "ok", "version": VERSION}


# ---------------------------------------------------------------------------
# OpenClaw CLI helpers
# ---------------------------------------------------------------------------

import subprocess
import shlex


def _run_openclaw_cmd(cmd: str, timeout: int = 30) -> tuple[int, str, str]:
    """Run an openclaw CLI command, locally or via SSH."""
    if OPENCLAW_SSH_HOST:
        full_cmd = ["ssh", "-o", "StrictHostKeyChecking=no", "-o", "ConnectTimeout=10",
                     OPENCLAW_SSH_HOST, cmd]
    else:
        full_cmd = cmd if isinstance(cmd, list) else cmd.split()

    print(f"[OpenClaw CMD] {' '.join(full_cmd) if isinstance(full_cmd, list) else full_cmd}", flush=True)
    try:
        result = subprocess.run(full_cmd, capture_output=True, text=True, timeout=timeout)
        print(f"[OpenClaw CMD] exit={result.returncode} stdout={result.stdout[:200]}", flush=True)
        if result.stderr.strip():
            print(f"[OpenClaw CMD] stderr={result.stderr[:200]}", flush=True)
        return result.returncode, result.stdout, result.stderr
    except subprocess.TimeoutExpired:
        return -1, "", "Command timed out"
    except FileNotFoundError as e:
        return -1, "", str(e)


def _write_remote_file(path: str, content: str) -> bool:
    """Write content to a file, locally or via SSH."""
    if OPENCLAW_SSH_HOST:
        escaped = content.replace("'", "'\\''")
        cmd = f"mkdir -p $(dirname {shlex.quote(path)}) && cat > {shlex.quote(path)} << 'ANIMA_EOF'\n{content}\nANIMA_EOF"
        code, _, stderr = _run_openclaw_cmd(cmd, timeout=15)
        if code != 0:
            print(f"[OpenClaw] write file failed: {stderr}", flush=True)
            return False
        return True
    else:
        try:
            p = Path(path).expanduser()
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text(content, encoding="utf-8")
            return True
        except OSError as e:
            print(f"[OpenClaw] write file failed: {e}", flush=True)
            return False


def _refresh_agents_json() -> list:
    """Re-fetch agent list from CLI and update agents.json cache."""
    if OPENCLAW_SSH_HOST:
        code, stdout, _ = _run_openclaw_cmd("openclaw agents list --json")
    else:
        code, stdout, _ = _run_openclaw_cmd("openclaw agents list --json")

    if code == 0 and stdout.strip():
        try:
            data = json.loads(stdout)
            if isinstance(data, list):
                agents_file = ROOT_DIR / "agents.json"
                agents_file.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
                print(f"[OpenClaw] agents.json updated: {len(data)} agents", flush=True)
                return data
        except json.JSONDecodeError:
            pass
    return _load_agents_from_file() or []


# ---------------------------------------------------------------------------
# OpenClaw agents list proxy
# ---------------------------------------------------------------------------

def _load_agents_from_cli() -> list | None:
    """Try running `openclaw agents list --json` locally."""
    import subprocess
    try:
        result = subprocess.run(
            ["openclaw", "agents", "list", "--json"],
            capture_output=True, text=True, timeout=10,
        )
        if result.returncode == 0 and result.stdout.strip():
            data = json.loads(result.stdout)
            if isinstance(data, list):
                print(f"[OpenClaw] agents from CLI: {len(data)} agents", flush=True)
                return data
    except (FileNotFoundError, subprocess.TimeoutExpired, json.JSONDecodeError):
        pass
    return None


def _load_agents_from_file() -> list | None:
    """Read agents from server/agents.json."""
    agents_file = ROOT_DIR / "agents.json"
    if not agents_file.is_file():
        return None
    try:
        data = json.loads(agents_file.read_text(encoding="utf-8"))
        if isinstance(data, list):
            print(f"[OpenClaw] agents from file: {len(data)} agents", flush=True)
            return data
    except (json.JSONDecodeError, OSError):
        pass
    return None


@app.route("/api/agents", methods=["GET", "OPTIONS"])
def openclaw_agents():
    if request.method == "OPTIONS":
        resp = Response("", status=204)
        resp.headers["Access-Control-Allow-Origin"] = "*"
        resp.headers["Access-Control-Allow-Headers"] = "Content-Type"
        resp.headers["Access-Control-Allow-Methods"] = "GET, OPTIONS"
        return resp

    agents = _load_agents_from_cli() or _load_agents_from_file() or []

    resp = Response(json.dumps(agents, ensure_ascii=False), mimetype="application/json")
    resp.headers["Access-Control-Allow-Origin"] = "*"
    return resp


@app.route("/api/agents/create", methods=["POST", "OPTIONS"])
def openclaw_agents_create():
    if request.method == "OPTIONS":
        resp = Response("", status=204)
        resp.headers["Access-Control-Allow-Origin"] = "*"
        resp.headers["Access-Control-Allow-Headers"] = "Content-Type"
        resp.headers["Access-Control-Allow-Methods"] = "POST, OPTIONS"
        return resp

    body = request.get_json(silent=True) or {}
    agent_id = body.get("id", "").strip()
    if not agent_id or not re.match(r"^[a-zA-Z0-9_-]+$", agent_id):
        return _json_error(400, "Invalid agent id (alphanumeric, hyphens, underscores only)")

    identity_name = body.get("identityName", "").strip()
    identity_emoji = body.get("identityEmoji", "").strip()
    persona = body.get("persona", "").strip()
    system_prompt = body.get("systemPrompt", "").strip()

    wb = OPENCLAW_WORKSPACE_BASE.rstrip("/")
    workspace = f"{wb}/workspace-{agent_id}"

    steps = []

    # Step 1: Create agent
    code, stdout, stderr = _run_openclaw_cmd(
        f"openclaw agents add {shlex.quote(agent_id)} "
        f"--workspace {shlex.quote(workspace)} --non-interactive"
    )
    if code != 0:
        if "already exists" in stderr.lower() or "already exists" in stdout.lower():
            steps.append({"step": "add", "status": "already_exists"})
        else:
            return _json_error(500, f"Failed to create agent: {stderr or stdout}")
    else:
        steps.append({"step": "add", "status": "ok"})

    # Step 2: Write IDENTITY.md
    if identity_name:
        identity_content = f"---\nname: {identity_name}\n"
        if identity_emoji:
            identity_content += f"emoji: {identity_emoji}\n"
        identity_content += "---\n"
        ok = _write_remote_file(f"{workspace}/IDENTITY.md", identity_content)
        steps.append({"step": "identity", "status": "ok" if ok else "failed"})

    # Step 3: Write SOUL.md
    if persona:
        soul_content = f"_{persona}_\n"
        ok = _write_remote_file(f"{workspace}/SOUL.md", soul_content)
        steps.append({"step": "soul", "status": "ok" if ok else "failed"})

    # Step 4: Write AGENTS.md (system prompt)
    if system_prompt:
        agents_content = f"{system_prompt}\n"
        ok = _write_remote_file(f"{workspace}/AGENTS.md", agents_content)
        steps.append({"step": "agents_md", "status": "ok" if ok else "failed"})

    # Step 5: Sync identity into OpenClaw config
    if identity_name:
        code, _, stderr = _run_openclaw_cmd(
            f"openclaw agents set-identity --agent {shlex.quote(agent_id)} "
            f"--workspace {shlex.quote(workspace)} --from-identity"
        )
        steps.append({"step": "set-identity", "status": "ok" if code == 0 else f"failed: {stderr[:100]}"})

    # Step 6: Refresh agents.json
    agents = _refresh_agents_json()

    result = {"code": 200, "msg": "ok", "data": {"id": agent_id, "steps": steps, "agents": agents}}
    resp = Response(json.dumps(result, ensure_ascii=False), mimetype="application/json")
    resp.headers["Access-Control-Allow-Origin"] = "*"
    return resp


@app.route("/api/agents/<agent_id>", methods=["DELETE", "OPTIONS"])
def openclaw_agents_delete(agent_id: str):
    if request.method == "OPTIONS":
        resp = Response("", status=204)
        resp.headers["Access-Control-Allow-Origin"] = "*"
        resp.headers["Access-Control-Allow-Headers"] = "Content-Type"
        resp.headers["Access-Control-Allow-Methods"] = "DELETE, OPTIONS"
        return resp

    if not agent_id or not re.match(r"^[a-zA-Z0-9_-]+$", agent_id):
        return _json_error(400, "Invalid agent id")

    code, stdout, stderr = _run_openclaw_cmd(
        f"openclaw agents remove {shlex.quote(agent_id)} --non-interactive"
    )
    if code != 0:
        return _json_error(500, f"Failed to remove agent: {stderr or stdout}")

    agents = _refresh_agents_json()

    result = {"code": 200, "msg": "ok", "data": {"id": agent_id, "agents": agents}}
    resp = Response(json.dumps(result, ensure_ascii=False), mimetype="application/json")
    resp.headers["Access-Control-Allow-Origin"] = "*"
    return resp


@app.route("/api/agents/sync", methods=["POST", "OPTIONS"])
def openclaw_agents_sync():
    """Force-refresh agents list from OpenClaw CLI."""
    if request.method == "OPTIONS":
        resp = Response("", status=204)
        resp.headers["Access-Control-Allow-Origin"] = "*"
        resp.headers["Access-Control-Allow-Headers"] = "Content-Type"
        resp.headers["Access-Control-Allow-Methods"] = "POST, OPTIONS"
        return resp

    agents = _refresh_agents_json()
    resp = Response(json.dumps(agents, ensure_ascii=False), mimetype="application/json")
    resp.headers["Access-Control-Allow-Origin"] = "*"
    return resp


def _json_error(status: int, msg: str):
    resp = Response(json.dumps({"code": status, "msg": msg}, ensure_ascii=False), status=status, mimetype="application/json")
    resp.headers["Access-Control-Allow-Origin"] = "*"
    return resp


# ---------------------------------------------------------------------------
# OpenClaw chat proxy
# ---------------------------------------------------------------------------

@app.route("/api/chat", methods=["POST", "OPTIONS"])
def openclaw_chat():
    if request.method == "OPTIONS":
        resp = Response("", status=204)
        resp.headers["Access-Control-Allow-Origin"] = "*"
        resp.headers["Access-Control-Allow-Headers"] = "Content-Type"
        resp.headers["Access-Control-Allow-Methods"] = "POST, OPTIONS"
        return resp

    if not OPENCLAW_API_URL:
        return {"code": 500, "msg": "OPENCLAW_API_URL not configured"}, 500

    body = request.get_json(silent=True) or {}
    messages = body.get("messages", [])
    agent_id = body.get("agentId", OPENCLAW_DEFAULT_AGENT)
    stream = body.get("stream", True)

    payload = {
        "model": f"openclaw:{agent_id}",
        "messages": messages,
        "stream": stream,
    }
    headers = {
        "Content-Type": "application/json",
        "Authorization": f"Bearer {OPENCLAW_TOKEN}",
    }

    import sys
    print(f"[OpenClaw] -> {OPENCLAW_API_URL}  model={payload['model']}  stream={stream}  msgs={len(messages)}", flush=True)
    sys.stderr.write(f"[OpenClaw] -> model={payload['model']} stream={stream} msgs={len(messages)}\n")
    sys.stderr.flush()

    try:
        upstream = http_requests.post(
            OPENCLAW_API_URL,
            json=payload,
            headers=headers,
            stream=stream,
            timeout=60,
        )
        upstream.raise_for_status()
    except http_requests.RequestException as exc:
        err_msg = f"[OpenClaw] ERROR: {exc}"
        print(err_msg, flush=True)
        sys.stderr.write(err_msg + "\n")
        sys.stderr.flush()
        return {"code": 502, "msg": f"OpenClaw upstream error: {exc}"}, 502

    print(f"[OpenClaw] <- {upstream.status_code}", flush=True)

    if stream:
        def generate():
            line_count = 0
            total_content = ""
            try:
                for raw_line in upstream.iter_lines():
                    if not raw_line:
                        yield "\n"
                        continue
                    decoded = raw_line.decode("utf-8", errors="replace")
                    line_count += 1
                    if decoded.startswith("data:"):
                        payload = decoded[5:].strip()
                        if payload and payload != "[DONE]":
                            try:
                                parsed = json.loads(payload)
                                delta = parsed.get("choices", [{}])[0].get("delta", {}).get("content", "")
                                if delta:
                                    total_content += delta
                            except (json.JSONDecodeError, IndexError, KeyError):
                                pass
                    yield decoded + "\n"
                yield "\n"
            except Exception as exc:
                _safe_log(f"[OpenClaw] stream error: {exc}")
            _safe_log(f"[OpenClaw] stream finished: {line_count} lines, content_len={len(total_content)}")
            if total_content:
                _safe_log(f"[OpenClaw] full_reply: {total_content[:200]}{'...' if len(total_content) > 200 else ''}")
        resp = Response(generate(), mimetype="text/event-stream")
        resp.headers["Cache-Control"] = "no-cache"
        resp.headers["X-Accel-Buffering"] = "no"
        resp.headers["Access-Control-Allow-Origin"] = "*"
        return resp
    else:
        data = upstream.json()
        resp = Response(json.dumps(data, ensure_ascii=False), mimetype="application/json")
        resp.headers["Access-Control-Allow-Origin"] = "*"
        return resp


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(description="anima-web local server")
    parser.add_argument("--host", default=CONF_HOST, help=f"Bind address (default: {CONF_HOST})")
    parser.add_argument("--port", type=int, default=CONF_PORT, help=f"Port (default: {CONF_PORT})")
    args = parser.parse_args()

    if not DIST_DIR.is_dir():
        print(f"\n[ERROR] dist directory not found: {DIST_DIR}")
        print("        Please build the frontend first:  npm run build\n")
        sys.exit(1)

    print()
    print("=" * 56)
    print("  anima-web")
    print("=" * 56)
    print(f"  Local:     http://127.0.0.1:{args.port}")
    if args.host == "0.0.0.0":
        print(f"  Network:   http://0.0.0.0:{args.port}")
    print(f"  API:       {CONF_API_BASE_URL}")
    if CONF_WS_URL:
        print(f"  WebSocket: {CONF_WS_URL}")
    if OPENCLAW_API_URL:
        print(f"  OpenClaw:  {OPENCLAW_API_URL}")
        print(f"  Agent:     {OPENCLAW_DEFAULT_AGENT}")
    print(f"  Dist:      {DIST_DIR}")
    print("=" * 56)
    print()

    app.run(host=args.host, port=args.port, debug=False)


if __name__ == "__main__":
    main()
