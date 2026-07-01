# -*- coding: utf-8 -*-
"""
claude-web-console —— 一个最小的本地网页操控台。

它在【本地】把 `claude` CLI 的无头流式模式包成 HTTP 服务：
  - POST /chat   调 `claude -p --output-format stream-json`，把消息以 SSE 流给前端
  - GET  /files  浏览工作目录下的文件/子目录
  - GET  /file   读取单个文件内容
所有接口用一个访问令牌(token)鉴权。对话进程、文件读写都在本机，
手机/浏览器只是远程界面。

配置（环境变量，均可选）：
  WORK_DIR            被操控的工作目录，默认 d:\\work\\AI_NovelGenerator-main
  WEB_CONSOLE_TOKEN   访问令牌；不设则启动时随机生成并打印
  CLAUDE_BIN          claude 可执行文件路径，默认自动探测
  HOST / PORT         监听地址，默认 0.0.0.0:8765
"""
import asyncio
import json
import os
import re
import secrets
import shutil
import sys

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import (
    FileResponse,
    JSONResponse,
    PlainTextResponse,
    StreamingResponse,
)

# ---------------------------------------------------------------- 配置 ----
HERE = os.path.dirname(os.path.abspath(__file__))


def _load_config() -> dict:
    """读取同目录下的 config.json（可选）。"""
    path = os.path.join(HERE, "config.json")
    if os.path.isfile(path):
        try:
            with open(path, encoding="utf-8") as f:
                return json.load(f) or {}
        except Exception as e:  # noqa: BLE001
            print(f"[warn] 读取 config.json 失败：{e}")
    return {}


_CFG = _load_config()


def cfg(key: str, default=None):
    """取配置，优先级：环境变量 > config.json > 默认值。"""
    env = os.environ.get(key.upper())
    if env not in (None, ""):
        return env
    val = _CFG.get(key)
    if val not in (None, ""):
        return val
    return default


WORK_DIR = os.path.abspath(cfg("work_dir", r"d:\work\AI_NovelGenerator-main"))
# 多项目：config 可配 work_dirs 白名单（可切换的工作目录列表）。
# 安全红线：前端只能从白名单里选，绝不接受任意路径当工作目录（否则＝任意目录跑 claude）。
# 每项可为字符串路径，或 {path, label}。未配 work_dirs 时退回单个 WORK_DIR。
def _norm_workdirs():
    raw = _CFG.get("work_dirs")
    items = []
    if isinstance(raw, list) and raw:
        for it in raw:
            if isinstance(it, str):
                p = os.path.abspath(it)
                items.append({"path": p, "label": os.path.basename(p) or p})
            elif isinstance(it, dict) and it.get("path"):
                p = os.path.abspath(it["path"])
                items.append({"path": p, "label": it.get("label") or os.path.basename(p) or p})
    if not items:
        items = [{"path": WORK_DIR, "label": os.path.basename(WORK_DIR) or WORK_DIR}]
    return items

WORK_DIRS = _norm_workdirs()
_ALLOWED_WORK_DIRS = {d["path"] for d in WORK_DIRS}
# 默认工作目录：config 的 work_dir 若在白名单里就用它，否则用列表第一个
DEFAULT_WORK_DIR = WORK_DIR if WORK_DIR in _ALLOWED_WORK_DIRS else WORK_DIRS[0]["path"]


def resolve_work_dir(req_dir) -> str:
    """把请求带来的 work_dir 校验成白名单内的绝对路径；空/非法则退回默认。"""
    if req_dir:
        p = os.path.abspath(str(req_dir))
        if p in _ALLOWED_WORK_DIRS:
            return p
        raise HTTPException(status_code=403, detail="work_dir not allowed")
    return DEFAULT_WORK_DIR
TOKEN = cfg("web_console_token") or secrets.token_urlsafe(18)
CLAUDE_BIN = cfg("claude_bin") or shutil.which("claude") or "claude"
HOST = cfg("host", "0.0.0.0")
PORT = int(cfg("port", 8765))

# 模型：默认空 = 用 CLI 默认（claude-sonnet-4-6）。空 id 也代表「默认」。
DEFAULT_MODEL = cfg("model", "") or ""
# 前端下拉可选模型；config 里 "models" 可整体覆盖。用 CLI 接受的别名最省心。
MODELS = _CFG.get("models") or [
    {"id": "", "label": "默认 (Sonnet)"},
    {"id": "opus", "label": "Opus"},
    {"id": "sonnet", "label": "Sonnet"},
    {"id": "haiku", "label": "Haiku"},
]
# 只允许下拉里列出的（+默认）模型，避免把任意字符串塞给子进程
_ALLOWED_MODELS = {str(m.get("id", "")) for m in MODELS} | {DEFAULT_MODEL}

# 权限模式：前端可选，随 /chat 传来。空 = 用 CLI 默认（不加 --permission-mode）。
# 标签与 Claude Code 官方 VS Code 扩展一致（图形界面用全词标签，非终端状态栏 glyph 串）。
# 注：-p 无头模式下权限确认无法内联，所以有用的主要是 plan（只读规划）与
# acceptEdits/bypassPermissions（放行）；default 遇到需确认操作可能被 harness 直接拒。
DEFAULT_PERM_MODE = cfg("permission_mode", "") or ""
PERM_MODES = _CFG.get("permission_modes") or [
    {"id": "default", "label": "Ask before edits"},
    {"id": "acceptEdits", "label": "Edit automatically"},
    {"id": "plan", "label": "Plan mode"},
    {"id": "bypassPermissions", "label": "Bypass permissions"},
]
# CLI 实际接受的合法值（白名单，防止把任意字符串塞给子进程）
_ALLOWED_PERM_MODES = {
    "", "default", "plan", "acceptEdits", "auto", "dontAsk", "bypassPermissions",
}

# 鉴权模式：
#   subscription —— 走 Max/Claude 订阅额度（CLAUDE_CODE_OAUTH_TOKEN 或本机已登录凭据）
#   relay        —— 走第三方「中转站」的 Anthropic 兼容接口（自定义 base_url + 密钥，按量计费）
# 未显式设 auth_mode 时：填了 base_url 即视为 relay，否则 subscription。
# 两种模式互斥注入，避免凭据打架（同时给订阅 OAuth 和 API key 会被官方拒绝）。
_oauth = cfg("claude_code_oauth_token")
_base_url = cfg("base_url")
# 中转站密钥：auth_token 走 Authorization: Bearer（多数 claude-code 中转站用这个）；
# api_key 走 x-api-key。二选一，别同时填。
_relay_auth_token = cfg("auth_token")
_relay_api_key = cfg("api_key")

AUTH_MODE = (cfg("auth_mode", "") or "").strip().lower()
if not AUTH_MODE:
    AUTH_MODE = "relay" if _base_url else "subscription"

if AUTH_MODE == "relay":
    if _base_url:
        os.environ["ANTHROPIC_BASE_URL"] = _base_url
    if _relay_auth_token:
        os.environ["ANTHROPIC_AUTH_TOKEN"] = _relay_auth_token
    elif _relay_api_key:
        os.environ["ANTHROPIC_API_KEY"] = _relay_api_key
    # 别让本机订阅登录态/旧环境变量盖过中转站配置（官方优先级里 OAuth 排在最后，
    # 但显式清掉更稳，也避免 AUTH_TOKEN 与 API_KEY 同时存在被拒）
    os.environ.pop("CLAUDE_CODE_OAUTH_TOKEN", None)
    if _relay_auth_token:
        os.environ.pop("ANTHROPIC_API_KEY", None)
else:  # subscription
    if _oauth:
        os.environ["CLAUDE_CODE_OAUTH_TOKEN"] = _oauth

# 网络代理：让 claude 子进程走代理（需翻墙访问 Anthropic 时用，如 Clash 的 7890 端口）
# 全局/TUN 模式的 VPN 无需在此设置，整机流量已走代理。
_proxy = cfg("proxy")
if _proxy:
    os.environ.setdefault("HTTPS_PROXY", _proxy)
    os.environ.setdefault("HTTP_PROXY", _proxy)

# 微信推送（Server酱）：填了 sendkey 才启用。子进程跑完/出错时后端 POST 推送——
# 这样即便关掉浏览器、锁屏离开，电脑这边任务结束照样能在微信收到通知。
SERVERCHAN_SENDKEY = cfg("serverchan_sendkey") or ""


def _serverchan_url(sendkey: str) -> str:
    """按 sendkey 形态选对应 endpoint：
    - Server酱³：sendkey 形如 `sctp{uid}t...`，推送地址 https://<uid>.push.ft07.com/send/<key>.send
    - Turbo 版：其它（如 SCT 开头），推送地址 https://sctapi.ftqq.com/<key>.send
    """
    m = re.match(r"^sctp(\d+)t", sendkey)
    if m:
        return f"https://{m.group(1)}.push.ft07.com/send/{sendkey}.send"
    return f"https://sctapi.ftqq.com/{sendkey}.send"


def _push_serverchan(title: str, desp: str = "") -> None:
    """同步推送一条到 Server酱（在线程里调用，失败只记日志，绝不影响对话）。
    自动兼容 Server酱³（sctp 开头）与 Turbo 版。"""
    if not SERVERCHAN_SENDKEY:
        return
    import urllib.parse
    import urllib.request

    try:
        url = _serverchan_url(SERVERCHAN_SENDKEY)
        data = urllib.parse.urlencode(
            {"title": title[:100], "desp": (desp or "")[:2000]}
        ).encode("utf-8")
        # 走与 claude 子进程相同的代理（Server酱是公网）
        proxies = {}
        if _proxy:
            proxies = {"http": _proxy, "https": _proxy}
        opener = urllib.request.build_opener(urllib.request.ProxyHandler(proxies))
        req = urllib.request.Request(
            url,
            data=data,
            method="POST",
            # Server酱³ 的 push.ft07.com 套了 Cloudflare，默认的 Python-urllib UA
            # 会被风控以 403/1010 拦掉，带一个常规浏览器 UA 即可放行。
            headers={
                "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/124.0 Safari/537.36",
                "Content-Type": "application/x-www-form-urlencoded",
            },
        )
        with opener.open(req, timeout=10) as resp:
            resp.read()
    except Exception as e:  # noqa: BLE001
        print(f"[warn] Server酱推送失败：{e}")


async def push_async(title: str, desp: str = "") -> None:
    """异步包装：丢到默认线程池，不阻塞事件循环。"""
    if not SERVERCHAN_SENDKEY:
        return
    try:
        await asyncio.get_running_loop().run_in_executor(
            None, _push_serverchan, title, desp
        )
    except Exception:  # noqa: BLE001
        pass

MAX_FILE_BYTES = 2 * 1024 * 1024  # /file 单文件最多读 2MB

app = FastAPI(title="claude-web-console")


# ------------------------------------------------------------- 工具函数 ----
def check_auth(request: Request) -> None:
    """从 Authorization: Bearer 或 ?token= 取令牌并校验。"""
    auth = request.headers.get("authorization", "")
    if auth.lower().startswith("bearer "):
        token = auth[7:].strip()
    else:
        token = request.query_params.get("token", "")
    if not token or token != TOKEN:
        raise HTTPException(status_code=401, detail="unauthorized")


def safe_path(rel: str, base: str = None) -> str:
    """把相对路径解析到 base（工作目录）内，阻止 ../ 越权。base 缺省用 DEFAULT_WORK_DIR。"""
    base = base or DEFAULT_WORK_DIR
    rel = (rel or "").replace("\\", "/").lstrip("/")
    full = os.path.normpath(os.path.join(base, rel))
    # commonpath 在不同盘符时会抛异常，统一当作越权
    try:
        inside = os.path.commonpath([full, base]) == base
    except ValueError:
        inside = False
    if not inside:
        raise HTTPException(status_code=403, detail="path outside work dir")
    return full


def build_claude_cmd(extra: list[str]) -> list[str]:
    """构造 claude 命令；Windows 上 .cmd 需经 cmd /c 运行。"""
    base = [CLAUDE_BIN, *extra]
    if os.name == "nt" and CLAUDE_BIN.lower().endswith((".cmd", ".bat")):
        return ["cmd", "/c", *base]
    return base


# ---------------------------------------------------------------- 路由 ----
@app.get("/")
def index():
    return FileResponse(os.path.join(HERE, "index.html"))


@app.get("/api/info")
def info(request: Request):
    check_auth(request)
    return {
        "work_dir": DEFAULT_WORK_DIR,
        "work_dirs": WORK_DIRS,
        "claude": CLAUDE_BIN,
        "models": MODELS,
        "default_model": DEFAULT_MODEL,
        "perm_modes": PERM_MODES,
        "default_perm_mode": DEFAULT_PERM_MODE,
        "wechat_push": bool(SERVERCHAN_SENDKEY),
    }


@app.get("/files")
def files(request: Request, path: str = "", work_dir: str = ""):
    check_auth(request)
    base = resolve_work_dir(work_dir)
    full = safe_path(path, base)
    if not os.path.isdir(full):
        raise HTTPException(status_code=404, detail="not a directory")
    entries = []
    with os.scandir(full) as it:
        for e in it:
            try:
                is_dir = e.is_dir()
                size = e.stat().st_size if e.is_file() else 0
            except OSError:
                continue
            entries.append({"name": e.name, "is_dir": is_dir, "size": size})
    entries.sort(key=lambda x: (not x["is_dir"], x["name"].lower()))
    rel = os.path.relpath(full, base).replace("\\", "/")
    return {"path": "" if rel == "." else rel, "entries": entries}


@app.get("/file")
def read_file(request: Request, path: str, work_dir: str = ""):
    check_auth(request)
    base = resolve_work_dir(work_dir)
    full = safe_path(path, base)
    if not os.path.isfile(full):
        raise HTTPException(status_code=404, detail="not a file")
    size = os.path.getsize(full)
    with open(full, "rb") as f:
        raw = f.read(MAX_FILE_BYTES + 1)
    truncated = len(raw) > MAX_FILE_BYTES
    raw = raw[:MAX_FILE_BYTES]
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError:
        return JSONResponse({"binary": True, "size": size})
    return JSONResponse(
        {"binary": False, "size": size, "truncated": truncated, "content": text}
    )


# ------------------------------------------------- 历史会话（claude CLI 落盘）----
# claude 把每个目录的对话按 session 存成 jsonl：
#   ~/.claude/projects/<把工作目录绝对路径里非字母数字字符全替成 - 的编码>/<sid>.jsonl
# 这里只读、不写——写仍由 claude 子进程在 --resume 时完成。
_SID_RE = re.compile(r"^[A-Za-z0-9-]+$")
_SESSION_MAX_EVENTS = 1000  # 单会话回放最多返回的气泡/工具事件数


def _sessions_dir(base: str = None) -> str:
    enc = re.sub(r"[^a-zA-Z0-9]", "-", base or DEFAULT_WORK_DIR)
    return os.path.join(os.path.expanduser("~"), ".claude", "projects", enc)


def _extract_text(content) -> str:
    """从 message.content 里抽出纯文本（content 可能是 str 或 block 列表）。"""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return "\n".join(
            b.get("text", "")
            for b in content
            if isinstance(b, dict) and b.get("type") == "text"
        )
    return ""


def _iter_msgs(path: str):
    """逐行产出主线（非 sidechain）的 user/assistant 消息 dict。"""
    with open(path, encoding="utf-8") as f:
        for ln in f:
            ln = ln.strip()
            if not ln:
                continue
            try:
                o = json.loads(ln)
            except Exception:  # noqa: BLE001
                continue
            if o.get("isSidechain"):
                continue
            if o.get("type") not in ("user", "assistant"):
                continue
            m = o.get("message")
            if isinstance(m, dict):
                yield m


def _clean_summary(txt: str) -> str:
    """剥掉 claude 注入的系统包裹（<ide_opened_file>、caveat、<command-*> 等），
    取出首行像样的人话；整段都是包裹则返回空串。"""
    for raw in txt.splitlines():
        s = raw.strip()
        if not s or s.startswith("<") or s.startswith("Caveat:"):
            continue
        return s[:120]
    return ""


def _summarize_session(path: str):
    """扫 jsonl：摘要优先取首条「像样」的用户消息（跳过系统注入包裹），
    取不到再退回首条任意用户文本；同时数消息条数。"""
    first_any, first_clean, count = "", "", 0
    try:
        for m in _iter_msgs(path):
            count += 1
            if m.get("role") == "user":
                txt = _extract_text(m.get("content")).strip()
                if txt:
                    if not first_any:
                        first_any = txt[:120]
                    if not first_clean:
                        first_clean = _clean_summary(txt)
    except OSError:
        pass
    return (first_clean or first_any), count


@app.get("/sessions")
def sessions(request: Request, work_dir: str = ""):
    check_auth(request)
    d = _sessions_dir(resolve_work_dir(work_dir))
    out = []
    if os.path.isdir(d):
        for name in os.listdir(d):
            if not name.endswith(".jsonl"):
                continue
            full = os.path.join(d, name)
            try:
                st = os.stat(full)
            except OSError:
                continue
            summary, count = _summarize_session(full)
            out.append(
                {
                    "id": name[:-6],
                    "mtime": st.st_mtime,
                    "size": st.st_size,
                    "summary": summary,
                    "count": count,
                }
            )
    out.sort(key=lambda x: x["mtime"], reverse=True)
    return {"dir": d, "sessions": out}


@app.get("/session/{sid}")
def session_detail(request: Request, sid: str, work_dir: str = ""):
    check_auth(request)
    if not _SID_RE.match(sid):
        raise HTTPException(status_code=400, detail="bad session id")
    path = os.path.join(_sessions_dir(resolve_work_dir(work_dir)), sid + ".jsonl")
    if not os.path.isfile(path):
        raise HTTPException(status_code=404, detail="no such session")
    events, truncated = [], False
    for m in _iter_msgs(path):
        role = m.get("role")
        content = m.get("content")
        if isinstance(content, str):
            blocks = [{"type": "text", "text": content}]
        elif isinstance(content, list):
            blocks = content
        else:
            blocks = []
        for b in blocks:
            if not isinstance(b, dict):
                continue
            bt = b.get("type")
            if bt == "text":
                txt = (b.get("text") or "").strip()
                if txt:
                    events.append({"kind": role, "text": txt})
            elif bt == "thinking":
                txt = (b.get("thinking") or "").strip()
                if txt:
                    events.append({"kind": "thinking", "text": txt})
            elif bt == "tool_use":
                events.append({
                    "kind": "tool",
                    "id": b.get("id", ""),
                    "name": b.get("name", ""),
                    "input": b.get("input") or {},
                })
            elif bt == "tool_result":
                rc = b.get("content")
                if isinstance(rc, list):
                    rc = "\n".join(
                        x.get("text", "") for x in rc
                        if isinstance(x, dict) and x.get("type") == "text"
                    )
                elif not isinstance(rc, str):
                    rc = json.dumps(rc, ensure_ascii=False) if rc is not None else ""
                events.append({
                    "kind": "tool_result",
                    "id": b.get("tool_use_id", ""),
                    "text": (rc or "")[:4000],
                    "is_error": bool(b.get("is_error")),
                })
            if len(events) >= _SESSION_MAX_EVENTS:
                truncated = True
                break
        if truncated:
            break
    return {"id": sid, "events": events, "truncated": truncated}


@app.post("/chat")
async def chat(request: Request):
    check_auth(request)
    data = await request.json()
    prompt = (data.get("prompt") or "").strip()
    session_id = data.get("session_id")
    images = data.get("images") or []  # [{media_type, data(base64)}]，手机端拍照/截图
    if not prompt and not images:
        raise HTTPException(status_code=400, detail="empty prompt")

    model = (data.get("model") or "").strip() or DEFAULT_MODEL
    if model and model not in _ALLOWED_MODELS:
        raise HTTPException(status_code=400, detail="unknown model")

    perm_mode = (data.get("permission_mode") or "").strip() or DEFAULT_PERM_MODE
    if perm_mode and perm_mode not in _ALLOWED_PERM_MODES:
        raise HTTPException(status_code=400, detail="unknown permission mode")

    work_dir = resolve_work_dir(data.get("work_dir"))  # 白名单校验，非法直接 403

    # 校验图片：只收 base64 的常见图片类型，限制数量与体积（防止塞爆子进程 stdin）
    clean_images = []
    if images:
        if not isinstance(images, list) or len(images) > 8:
            raise HTTPException(status_code=400, detail="too many images (max 8)")
        for im in images:
            mt = (im or {}).get("media_type", "")
            b64 = (im or {}).get("data", "")
            if mt not in ("image/png", "image/jpeg", "image/gif", "image/webp"):
                raise HTTPException(status_code=400, detail="bad image media_type")
            if not isinstance(b64, str) or len(b64) > 8 * 1024 * 1024:  # 单图 base64 ≤ 8MB
                raise HTTPException(status_code=400, detail="image too large")
            clean_images.append({"media_type": mt, "data": b64})

    # 有图 → 走 stream-json 输入模式（图片以 base64 块随消息传入，进程不自退，靠 result 收尾）；
    # 纯文本 → 沿用稳定的 -p 文本 stdin 链路（关闭 stdin 后进程自然退出）。
    use_stream_input = bool(clean_images)

    extra = [
        "-p",
        "--output-format", "stream-json",
        "--verbose",                  # stream-json 在 -p 模式下必须配 --verbose
        "--include-partial-messages", # 逐字增量
    ]
    if use_stream_input:
        extra += ["--input-format", "stream-json"]
    if model:
        extra += ["--model", model]
    if perm_mode:
        extra += ["--permission-mode", perm_mode]
    if session_id:
        extra += ["--resume", str(session_id)]
    cmd = build_claude_cmd(extra)

    async def gen():
        proc = await asyncio.create_subprocess_exec(
            *cmd,
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            cwd=work_dir,
            limit=16 * 1024 * 1024,  # 单行 JSON 可能很大（工具结果 / 图片块）
        )
        # prompt 经 stdin 传入，避开 Windows 命令行转义问题
        if use_stream_input:
            # stream-json 输入：一条 user 消息，content 含 image 块 + text 块
            content = [
                {"type": "image",
                 "source": {"type": "base64", "media_type": im["media_type"], "data": im["data"]}}
                for im in clean_images
            ]
            if prompt:
                content.append({"type": "text", "text": prompt})
            envelope = {"type": "user", "message": {"role": "user", "content": content}}
            proc.stdin.write((json.dumps(envelope) + "\n").encode("utf-8"))
        else:
            proc.stdin.write(prompt.encode("utf-8"))
        await proc.stdin.drain()
        proc.stdin.close()

        stderr_buf: list[str] = []

        async def drain_stderr():
            async for line in proc.stderr:
                stderr_buf.append(line.decode("utf-8", "replace"))

        err_task = asyncio.create_task(drain_stderr())
        final_text = ""        # 末条 result 的文本，用作推送摘要
        completed = False      # 子进程是否自然跑完（区分客户端中断）
        try:
            async for line in proc.stdout:
                s = line.decode("utf-8", "replace").rstrip("\r\n")
                if s.strip():
                    saw_result = False
                    # 顺带抓 result 文本（推送摘要用），解析失败不影响转发
                    if '"type"' in s and '"result"' in s:
                        try:
                            o = json.loads(s)
                            if o.get("type") == "result":
                                saw_result = True
                                if o.get("result"):
                                    final_text = str(o["result"])
                        except Exception:  # noqa: BLE001
                            pass
                    yield f"data: {s}\n\n"
                    # stream-json 输入模式下进程不会自退：一轮的 result 到达即主动收尾
                    if saw_result and use_stream_input:
                        break
            # —— 正常结束：补发收尾事件（仅此路径 yield，避免在 finally 中 yield）——
            completed = True
            if use_stream_input:
                # 主动结束子进程（stream-json 模式不会自退）
                if proc.returncode is None:
                    try:
                        proc.kill()
                    except ProcessLookupError:
                        pass
            await proc.wait()
            await err_task
            if proc.returncode not in (0, None) and not use_stream_input:
                payload = json.dumps(
                    {
                        "type": "console_error",
                        "returncode": proc.returncode,
                        "stderr": "".join(stderr_buf)[-4000:],
                    }
                )
                yield f"data: {payload}\n\n"
            yield 'data: {"type": "console_done"}\n\n'
        finally:
            # 任何退出（含客户端中断「停止生成」/断线）都确保杀掉子进程，不留孤儿
            if proc.returncode is None:
                try:
                    proc.kill()
                except ProcessLookupError:
                    pass
                await proc.wait()
            if not err_task.done():
                err_task.cancel()
            # 微信推送：仅子进程自然跑完才推（客户端中断/断线不推）。
            # stream-json 输入模式下我们在 result 后主动 kill，returncode 非 0 属正常，
            # completed=True 即代表一轮成功结束，据此判定成功而非看 returncode。
            if completed and SERVERCHAN_SENDKEY:
                ok = use_stream_input or proc.returncode in (0, None)
                if ok:
                    await push_async("✅ Claude 回复完成", final_text or "（无文本输出）")
                else:
                    await push_async(
                        "⚠️ Claude 执行出错",
                        f"returncode={proc.returncode}\n\n"
                        + "".join(stderr_buf)[-1500:],
                    )

    return StreamingResponse(
        gen(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


# ---------------------------------------------------------------- 启动 ----
def main():
    # Windows 上 asyncio 子进程需要 Proactor 事件循环
    if os.name == "nt":
        asyncio.set_event_loop_policy(asyncio.WindowsProactorEventLoopPolicy())

    import uvicorn

    if AUTH_MODE == "relay":
        _auth_desc = f"中转站 relay（base_url={_base_url or '?'}）"
    else:
        _auth_desc = "订阅 subscription（Max/已登录凭据）"

    print("=" * 60)
    print(" claude-web-console")
    if len(WORK_DIRS) > 1:
        print(f"   工作目录        : {len(WORK_DIRS)} 个可选，默认 {DEFAULT_WORK_DIR}")
        for d in WORK_DIRS:
            print(f"       - {d['label']}: {d['path']}")
    else:
        print(f"   工作目录 WORK_DIR : {DEFAULT_WORK_DIR}")
    print(f"   claude          : {CLAUDE_BIN}")
    print(f"   鉴权模式 AUTH    : {_auth_desc}")
    print(f"   微信推送        : {'已启用 (Server酱)' if SERVERCHAN_SENDKEY else '未配置'}")
    print(f"   监听            : http://{HOST}:{PORT}")
    print(f"   访问令牌 TOKEN   : {TOKEN}")
    print("   手机打开（同一网络 / Tailscale）:")
    print(f"     http://<本机IP>:{PORT}/?token={TOKEN}")
    print("=" * 60)
    uvicorn.run(app, host=HOST, port=PORT, log_level="info")


if __name__ == "__main__":
    main()
