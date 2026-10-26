#!/usr/bin/env python3
"""QPhone /turn 服务 —— 客户端薄壳与控制程序之间那一层。

壳只认四种事件：status / progress / needs_human / result。
执行交 Mobilerun（手机原生 agent），本服务只负责收交代、翻译进展、回结果。

    POST /turn   {"text": "打开设置，睇下 Android 版本"}
                 → text/event-stream：
                     event: status      {"state": "running", "device": "..."}
                     event: progress    {"line": "Step 3/8 ..."}
                     event: needs_human {"reason": "..."}     （失败／卡住才有）
                     event: result      {"success": true, "message": "..."}

    GET  /status → 云机开/关/可否操作（同 qphone.py status）
    GET  /health → {"ok": true}

零第三方依赖，Python 3.9+。环境变量：
    QPHONE_BIND      监听地址（默认 127.0.0.1，绑 0.0.0.0 才给容器访问）
    QPHONE_PORT      端口（默认 8777）
    QPHONE_TARGET    云机 adb 地址（默认 localhost:5556 = 本地 redroid 13 容器）
    MOBILERUN        mobilerun 可执行路径（默认自动找）
    QPHONE_MODEL     模型（默认 workbuddy/deepseek-v4.1-flash）
    QPHONE_LLM_URL   OpenAI 兼容端点（默认 http://127.0.0.1:7863/v1）
    QPHONE_STEPS     步数上限（默认 20）
"""

from __future__ import annotations

import json
import os
import queue
import re
import shutil
import subprocess
import sys
import threading
import time
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

HERE = Path(__file__).resolve().parent
import layers   # L0 / L1a / L1b 执行层（同目录）
BIND = os.environ.get("QPHONE_BIND", "127.0.0.1")
PORT = int(os.environ.get("QPHONE_PORT", "8777"))
TARGET = os.environ.get("QPHONE_TARGET", "localhost:5556")
HOME_AFTER = os.environ.get("QPHONE_HOME_AFTER", "1").lower() not in ("0", "false", "no", "off")
ADB = os.environ.get("QPHONE_ADB", os.path.expanduser("~/Library/Android/sdk/platform-tools/adb"))
MODEL = os.environ.get("QPHONE_MODEL", "grok-4.7")
PROVIDER = os.environ.get("QPHONE_PROVIDER", "DeepSeek")  # 用 DeepSeek 档配自定 base_url；
                                                            # XAI 档会强制打 api.x.ai，唔受 api_base 控制
LLM_URL = os.environ.get("QPHONE_LLM_URL", "http://127.0.0.1:8645/v1")  # hermes proxy → xAI OAuth（token 自动续）
STEPS = os.environ.get("QPHONE_STEPS", "20")
TRAJ_ROOT = Path(os.environ.get("QPHONE_TRAJ", str(Path.home() / "trajectories")))
TRAJ_DIRS: dict[str, Path] = {}      # id → 实际目录（Mobilerun 按 cwd 落，位置会变）


def traj_path(tid: str) -> Path | None:
    """轨迹目录解析：本次会话记录过 → ~/trajectories → 当前 cwd 下 trajectories。"""
    if tid in TRAJ_DIRS and TRAJ_DIRS[tid].is_dir():
        return TRAJ_DIRS[tid]
    for base in (TRAJ_ROOT, Path.cwd() / "trajectories"):
        d = base / tid
        if d.is_dir():
            TRAJ_DIRS[tid] = d
            return d
    return None


# ---- 实时“而家边个组件喺做嘢”状态 ----
STATE = {"phase": "idle", "step": 0, "of": 0, "thought": "", "action": "",
         "component": "", "ts": 0.0}
_ACT = {"in_think": False, "buf": []}
_ACT_LOCK = threading.Lock()
LAST_RESULT = {"ok": None, "msg": "", "ts": 0.0}

# 执行层用量（每单用咗边层就 +1，会话级累计），result 事件带埋畀壳画占比
LAYER_STATS = {"L0": 0, "L1a": 0, "L1b": 0, "L2": 0}
_LAYER_LOCK = threading.Lock()


def layer_bump(k: str):
    with _LAYER_LOCK:
        if k in LAYER_STATS:
            LAYER_STATS[k] += 1
        return dict(LAYER_STATS), sum(LAYER_STATS.values())   # 对话模式要知上一单做到未

# ---- 会话历史（刷新 / 断线都唔会消失）----
HISTORY: list[dict] = []
HIST_LOCK = threading.Lock()
CURRENT_TASK = {"id": ""}


def hist_add(kind: str, **kw) -> dict:
    with HIST_LOCK:
        item = {"kind": kind, "ts": time.time(), **kw}
        HISTORY.append(item)
        del HISTORY[:-300]
        return item


def hist_task_update(tid: str, **kw):
    if not tid:
        return None
    with HIST_LOCK:
        for it in reversed(HISTORY):
            if it.get("id") == tid:
                it.update(kw)
                return it
    return None


def _pub(q, phase: str, **kw) -> None:
    with _ACT_LOCK:
        st = dict(STATE)
        st.update({k: v for k, v in kw.items()})
        st["phase"] = phase
        st["ts"] = time.time()
        STATE.clear()
        STATE.update(st)
        snap = dict(st)
    if CURRENT_TASK["id"] and phase in ("step", "think", "act", "done", "human"):
        hist_task_update(CURRENT_TASK["id"], phase="running" if phase in ("step", "think", "act") else phase,
                         step=snap.get("step") or 0, of=snap.get("of") or 0,
                         detail=(snap.get("action") or snap.get("thought") or "")[:120])
    if q is not None:
        try:
            q.put(("activity", snap))
        except Exception:
            pass


def _track(line: str, q) -> None:
    """由 Mobilerun 输出行推断：第几步 · 模型谂紧咩 · Portal 做紧咩。"""
    s = line.strip()
    m = re.match(r"🔄\s*Step (\d+)/(\d+)", s)
    if m:
        _ACT["in_think"], _ACT["buf"] = False, []
        _pub(q, "step", step=int(m.group(1)), of=int(m.group(2)), thought="", action="")
        return
    if s == "FastAgent response:":
        _ACT["in_think"], _ACT["buf"] = True, []
        _pub(q, "think", thought="")
        return
    if s.startswith("💡 Tool results"):
        _ACT["in_think"] = False
        _pub(q, "act", thought=" ".join(_ACT["buf"])[:220])
        return
    if _ACT["in_think"]:
        if s and not s.startswith("Attempt "):
            clean = s.split("<function_calls>")[0].strip()   # 模型会把工具调用 XML 夹埋喺思考里
            if clean and not clean.startswith("<"):
                _ACT["buf"].append(clean)
            _pub(q, "think", thought=" ".join(_ACT["buf"])[:220])
        return
    m = re.match(r"Coordinates:\s*\((\d+),\s*(\d+)\)", s)
    if m:
        _pub(q, "act", action=f"点 ({m.group(1)}, {m.group(2)})")
        return
    m = re.match(r"Starting app (\S+)", s)
    if m:
        _pub(q, "act", action=f"开 {m.group(1)}")
        return
    if s.startswith(("Pressed ", "Swiped", "Typed ", "Long pressed", "Clicked")):
        _pub(q, "act", action=s[:90])
        return


PROXY_URL = os.environ.get("QPHONE_PROXY_URL", "http://127.0.0.1:8645")


def _proxy_up() -> bool:
    import socket
    try:
        host, port = PROXY_URL.split("//")[-1].split(":")
        with socket.create_connection((host, int(port)), timeout=0.6):
            return True
    except Exception:
        return False


def llm_route() -> tuple[str, str, str]:
    """(provider, base_url, key)。QPHONE_LLM_URL 显式指定就直用（例如 Qbridge 本机）；否则 proxy → xAI。"""
    if os.environ.get("QPHONE_LLM_URL"):
        return PROVIDER, LLM_URL, os.environ.get("QPHONE_LLM_KEY", "local")
    if _proxy_up():
        return "DeepSeek", PROXY_URL + "/v1", "local-proxy"
    return "XAI", "https://api.x.ai/v1", _xai_token()


# ─────────────────────────────── 意图判别（chat / task / unclear）
TASK_WORDS = ("打开", "开一", "开启", "开下", "帮我开", "关掉", "关闭", "装", "安装", "下载", "卸载",
              "搜", "查找", "搜索", "点击", "点一下", "点开", "点佢", "按一下", "滑", "输入", "发送",
              "发条", "发个", "截图", "录屏", "返桌面", "返出去", "返首页",
              "设成", "设置成", "改成", "调成", "登录", "登入", "重启", "关机", "开机", "拍", "播",
              "暂停", "返回", "回桌面", "拨", "打电话", "复制", "粘贴", "删掉", "删除", "上传",
              # 记录 / 备忘 / 写入类（2026-10-23 补：呢类原来被判成 chat，直接推卸）
              "记入", "记低", "记落", "记录", "记住", "记事", "写低", "写落", "写字", "写入",
              "存入", "存落", "存档", "加入", "新增", "新建", "建个", "开个", "备忘", "笔记",
              "填", "整", "搞", "帮我", "帮手", "同我", "麻烦你")
CHAT_WORDS = ("你好", "哈佬", "hello", "hi", "咩", "什么", "点解", "为什么", "分别", "有冇", "系咪",
              "边个", "几多", "多少", "你觉得", "解释", "讲讲", "介绍", "可以吗", "?", "？", "点样")

SYS_CHAT = ("你系 QPhone（云手机助理）嘅对话模式，讲粤语书面语，简洁直接，最多三句。"
            "你而家冇开手机（唔会截图、唔会点屏），所以唔好应承「已经帮你做咗」。"
            "如果用户其实系想叫你操作手机：唔好叫佢重讲一次，直接答「呢句我可以照做，按『当任务跑』我就开跑」；"
            "如果佢问嘅系事实或者聊天，就正常答。")


def classify(text: str) -> dict:
    """规则判别：action 词 vs 问句词，打和就判唔清（唔靠模型，零成本零延迟）。"""
    s = (text or "").strip()
    low = s.lower()
    task = sum(1 for w in TASK_WORDS if w in s)
    chat = sum(1 for w in CHAT_WORDS if (w in low if w.isascii() else w in s))
    if not s:
        return {"mode": "chat", "why": "空", "task": 0, "chat": 0}
    if task and not chat:
        return {"mode": "task", "why": f"动作词 {task}", "task": task, "chat": chat}
    if chat and not task:
        return {"mode": "chat", "why": f"问句 {chat}", "task": task, "chat": chat}
    if task and chat:
        return {"mode": "unclear", "why": "又有动作词又有问句", "task": task, "chat": chat}
    if len(s) <= 4:
        return {"mode": "unclear", "why": "太短，唔知想点", "task": 0, "chat": 0}
    # 冇任何命中：唔可以用 chat 冒认（会变成「我唔会操作手机」式推卸），一律反问
    return {"mode": "unclear", "why": "冇识别到动作词", "task": 0, "chat": 0}


def chat_context() -> str:
    """对话模式嘅事实底：设备 + 当前状态 + 上一单结果（唔加截图，慳钱）。"""
    st = dict(STATE)
    phase = {"chat": "对话中", "idle": "空闲", "step": "执行中", "think": "模型谂紧",
             "act": "执行中", "turn": "派发中", "done": "做完", "human": "等人处理"}.get(st.get("phase"), st.get("phase") or "空闲")
    bits = [f"设备：{TARGET}（在线）",
            f"当前状态：{phase}",
            f"步数：{st.get('step', 0)}/{st.get('of', 0) or STEPS}"]
    if LAST_RESULT["ts"]:
        age = int(time.time() - LAST_RESULT["ts"])
        bits.append(f"上一单任务：{'成功' if LAST_RESULT['ok'] else '未做到'} · {LAST_RESULT['msg'][:120]}（{age} 秒前）")
    if st.get("action"):
        bits.append(f"最后一个动作：{st['action'][:80]}")
    return "；".join(bits)


def answer_chat(text: str, timeout: float = 40.0) -> str:
    """直接问模型（唔经 Mobilerun、唔睇屏），但带设备与任务上下文。"""
    import urllib.request
    provider, base, key = llm_route()
    sysmsg = SYS_CHAT + "\n\n已知事实（只可引用，唔准编）：" + chat_context()
    payload = {"model": MODEL, "temperature": 0.5, "max_tokens": 600,
               "messages": [{"role": "system", "content": sysmsg},
                            {"role": "user", "content": text}]}
    req = urllib.request.Request(
        base.rstrip("/") + "/chat/completions",
        data=json.dumps(payload).encode(),
        headers={"Content-Type": "application/json", "Authorization": f"Bearer {key}"})
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))   # 绕过 Clash
    with opener.open(req, timeout=timeout) as r:
        data = json.loads(r.read().decode("utf-8", "ignore"))
    return (data["choices"][0]["message"].get("content") or "").strip()


def _xai_token() -> str:
    try:
        a = json.loads((Path.home() / ".hermes" / "auth.json").read_text(encoding="utf-8"))
        return a["providers"]["xai-oauth"]["tokens"]["access_token"]
    except Exception:
        return os.environ.get("XAI_API_KEY", "")


def llm_key() -> str:
    """Mobilerun 要嘅 API key。经 hermes proxy 时随便填；直连 xAI 才要 oauth token。"""
    if PROVIDER.upper() == "XAI" and _proxy_up():
        return "local-proxy"
    if PROVIDER.upper() == "XAI":
        try:
            a = json.loads((Path.home() / ".hermes" / "auth.json").read_text(encoding="utf-8"))
            tok = a["providers"]["xai-oauth"]["tokens"]["access_token"]
            if tok:
                return tok
        except Exception:
            pass
        return os.environ.get("XAI_API_KEY", "")
    return os.environ.get("XAI_API_KEY") or os.environ.get("HERMES_CUSTOM_QBRIDGE_API_KEY", "")


def mobilerun_bin() -> str:
    for c in (os.environ.get("MOBILERUN", ""), "mobilerun", str(Path.home() / ".local/bin/mobilerun")):
        if not c:
            continue
        p = shutil.which(c) if "/" not in c else (c if os.access(c, os.X_OK) else "")
        if p:
            return p
    raise RuntimeError("搵唔到 mobilerun。`uv tool install mobilerun` 或设 MOBILERUN。")


def qphone_status() -> dict:
    """直接用 qphone.py 嘅 status，唔重复写一份。"""
    try:
        cp = subprocess.run(
            [sys.executable, str(HERE / "qphone.py"), "--json", "status"],
            capture_output=True, text=True, timeout=60, env=os.environ.copy(),
        )
        return json.loads(cp.stdout.strip() or "{}")
    except Exception as e:  # noqa: BLE001 - status 永远要回得话
        return {"ok": False, "error": str(e)}


def run_agent(text: str, q: "queue.Queue[tuple[str, dict]]") -> None:
    """跑 Mobilerun，把输出翻译成壳认嘅事件。"""
    started = time.time()
    route = llm_route()
    _pub(q, "turn", step=0, of=0, thought="", action="")   # 每单清零，唔累计上一单步数

    # ── 先试确定性三层（L0 → L1a → L1b）；全唔中才落 L2 视觉 ──
    try:
        tr = layers.route(text, emit=lambda ev, data: q.put((ev, data)))
    except Exception as e:  # noqa: BLE001
        tr = None
        q.put(("progress", {"line": f"L0–L1b 路由异常：{e}"}))
    if tr and (tr.get("ok") or tr.get("final")):
        ok = bool(tr.get("ok"))
        detail = f"{tr['layer']} · {tr['label']} · {tr['ms']} ms" + (f" · {tr['why']}" if tr.get("why") else "")
        q.put(("progress", {"line": detail}))
        _pub(q, "step", step=1, of=1, action=f"{tr['layer']} {tr['label']}")
        LAST_RESULT.update(ok=ok, msg=detail, ts=time.time())
        hist_task_update(CURRENT_TASK["id"], phase="done" if ok else "human", msg=detail[:300])
        _pub(q, "done" if ok else "human", action="")
        st, tot = layer_bump(tr["layer"])
        q.put(("result", {"success": ok,
                          "message": f"{tr['label']}（{tr['layer']}，{tr['ms']} ms）" if ok else detail,
                          "layer_stats": st, "layer_total": tot}))
        q.put(("_end", {}))          # L0/L1 已直接落到目标态，唔按 home 复位
        return
    # 落 L2 之前讲清每层点解唔接（老板要知点解成日 L2）
    why = "；".join(f"{a['layer']} {a.get('why') or '未命中'}"
                    for a in (getattr(layers, "LAST_ATTEMPTS", None) or []) if not a.get("ok")) \
          or "L0/L1a/L1b 未命中"
    q.put(("progress", {"line": "执行层未接得住，转 L2 视觉：" + why}))
    q.put(("layer", {"layer": "L2", "label": "转视觉（Mobilerun）", "cmds": ["mobilerun run"],
                     "ms": 0, "ok": True, "why": why}))
    lstats, ltot = layer_bump("L2")
    try:
        cmd = [
            mobilerun_bin(), "run", text,
            "--device", TARGET,
            "--provider", route[0],
            "--model", MODEL,
            "--api_base", route[1],
            "--vision", "--steps", STEPS,
            "--save-trajectory", "step",       # 出截图 + 轨迹，落轨迹页
        ]
        env = os.environ.copy()
        env.setdefault("DEEPSEEK_API_KEY", env.get("HERMES_CUSTOM_QBRIDGE_API_KEY", ""))
        env["XAI_API_KEY"] = route[2]
        env["DEEPSEEK_API_KEY"] = route[2]
        env["OPENAI_API_KEY"] = route[2]
        proc = subprocess.Popen(
            cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
            text=True, bufsize=1, env=env,
        )
        success, message, collecting = False, "", False
        traj_dir = ""
        assert proc.stdout is not None
        for line in proc.stdout:
            line = line.rstrip("\n")
            if not line.strip():
                continue
            _track(line, q)
            m_tr = re.search(r"Trajectory (?:saved|folder):\s*(/\S+)", line)
            if m_tr:
                traj_dir = m_tr.group(1).rstrip("。.,")
                continue
            if "🎉 Goal achieved" in line:
                success, collecting = True, True
                message = line.split("Goal achieved:", 1)[-1].strip()
                continue
            if collecting:
                bare = line.strip()
                if bare.startswith("/"):          # 轨迹路径单独一行
                    traj_dir = bare.rstrip("。.,")
                    continue
                if "📁" in line:      # 轨迹行夹埋喺结果后面，唔好收进 message
                    m2 = re.search(r"(/\S+)", line)
                    if m2:
                        traj_dir = m2.group(1).rstrip("。.,")
                    continue
                # CLI 会把结果按终端宽度折行，续行要接返埋
                message = (message + line.strip()) if message else line.strip()
                continue
            if line.strip().startswith("/") and "trajectories" in line:
                traj_dir = line.strip().rstrip("。.,")
                continue
            if "❌" in line or "Goal failed" in line:
                message = line.replace("❌", "").strip()
                continue
            # 工具调用 XML 对壳冇意义，唔好推出去
            if line.lstrip().startswith(("<", "1.", "2.", "3.", "4.", "5.", "6.", "7.", "8.", "9.")):
                continue
            q.put(("progress", {"line": line[:400]}))
        proc.wait(timeout=30)
        tid = ""
        if traj_dir:
            tp = Path(traj_dir)
            if tp.is_dir():
                TRAJ_DIRS[tp.name] = tp
                tid = tp.name
        if not tid:
            tid = latest_trajectory(started)
        if tid:
            q.put(("trajectory", {"id": tid}))
        LAST_RESULT.update(ok=bool(success), msg=message or "", ts=time.time())
        hist_task_update(CURRENT_TASK["id"], phase="done" if success else "human",
                         msg=(message or "")[:300])
        if success:
            _pub(q, "done", action="")
            q.put(("result", {"success": True, "message": message or "做完",
                              "layer_stats": lstats, "layer_total": ltot}))
        else:
            _pub(q, "human", action="")
            q.put(("needs_human", {"reason": message or "agent 未做到，需要人睇屏"}))
            q.put(("result", {"success": False, "message": message or "未做到",
                              "layer_stats": lstats, "layer_total": ltot}))
        if HOME_AFTER:
            press_home()                     # 收尾：任务完就返桌面，唔留低上个 app
    except Exception as e:  # noqa: BLE001
        q.put(("needs_human", {"reason": f"控制程序出错：{e}"}))
        q.put(("result", {"success": False, "message": str(e),
                          "layer_stats": dict(LAYER_STATS), "layer_total": sum(LAYER_STATS.values())}))
    finally:
        q.put(("_end", {}))


def latest_trajectory(since: float) -> str:
    """搵本次跑出嘅轨迹目录（Mobilerun 写去 ~/trajectories）。"""
    try:
        cands = [d for d in TRAJ_ROOT.iterdir() if d.is_dir() and d.stat().st_mtime >= since - 1]
    except (FileNotFoundError, NotADirectoryError):
        return ""
    return max(cands, key=lambda d: d.stat().st_mtime).name if cands else ""


def parse_action(code: str) -> tuple[str, str]:
    """Mobilerun 嘅 function-call 文本 → (工具名, 人话动作)。"""
    m = re.search(r'<invoke name="([^"]+)"', code or "")
    name = m.group(1) if m else "?"
    kv = {k: v.strip() for k, v in re.findall(r'<parameter name="([^"]+)">(.*?)</parameter>', code or "", re.S)}
    if name == "click":
        if "x" in kv and "y" in kv:
            return name, f"点 ({kv['x']}, {kv['y']})"
        if "index" in kv:
            return name, f"点元素 #{kv['index']}"
        return name, "点一下"
    if name == "swipe":
        a, b = kv.get("coordinate") or "\u003f", kv.get("coordinate2") or "\u003f"
        return name, f"滑 {a} → {b}"
    if name == "long_press" and "index" in kv:
        return name, f"长按元素 #{kv['index']}"
    if name == "open_app":
        return name, f"开 {kv.get('text') or kv.get('package') or kv.get('app_name') or '?'}"
    if name == "system_button":
        return name, f"按键 {kv.get('button','?')}"
    if name == "type":
        return name, f"输入「{(kv.get('text') or '')[:30]}」"
    if name == "complete":
        return name, "收工"
    return name, name + (":" + json.dumps(kv, ensure_ascii=False)[:70] if kv else "")


def read_trajectory(tid: str) -> dict:
    d = traj_path(tid)
    if d is None:
        raise FileNotFoundError(tid)
    traj = json.loads((d / "trajectory.json").read_text(encoding="utf-8"))
    steps, n = [], 0
    for e in traj:
        t = e.get("type")
        if t == "FastAgentResponseEvent":
            name, human = parse_action(e.get("code", ""))
            steps.append({"i": n, "thought": (e.get("thought") or "").strip(),
                          "tool": name, "action": human, "result": "", "ok": None,
                          "usage": e.get("usage") or {}})
            n += 1
        elif t == "ToolExecutionEvent" and steps:
            steps[-1]["result"] = e.get("summary") or e.get("tool_name", "")
            steps[-1]["ok"] = e.get("success")
        elif t == "FastAgentEndEvent":
            steps.append({"end": True, "success": e.get("success"), "reason": e.get("reason", "")})
    shots = sorted(p.name for p in (d / "screenshots").glob("*.png")) if (d / "screenshots").is_dir() else []
    gif = (d / "screenshots" / "trajectory.gif").is_file()
    return {"steps": steps, "shots": shots, "gif": gif, "dir": str(d)}


REPLAY_CSS = """
  :root{color-scheme:light dark;--bg:#fff;--fg:#0f1419;--soft:#46505a;--bd:#cbd3da;--card:#f6f8fa;
        --accent:#0b6b7d;--ok:#15803d;--no:#b3261e;--warn:#a15c07;--hl:rgba(11,107,125,.16)}
  @media (prefers-color-scheme:dark){:root{--bg:#14171b;--fg:#f4f6f8;--soft:#c3ccd6;--bd:#4f5862;
        --card:#1b1f24;--accent:#38e0f5;--ok:#4ade80;--no:#fb7185;--warn:#fbbf24;--hl:rgba(56,224,245,.18)}}
  *{box-sizing:border-box}
  html,body{background:var(--bg)}
  body{margin:0;padding:20px 22px 40px;color:var(--fg);font:16px/1.65 -apple-system,"PingFang SC",sans-serif}
  h1{font-size:20px;margin:0 0 8px}
  .top{display:flex;gap:8px;align-items:center;flex-wrap:wrap;margin-bottom:14px}
  .pill{border:1px solid var(--bd);border-radius:999px;padding:1px 10px;font:13px ui-monospace,Menlo,monospace}
  .pill.ok{color:var(--ok);border-color:var(--ok)} .pill.no{color:var(--no);border-color:var(--no)}

  /* 组件图 */
  .graph{border:1px solid var(--bd);border-radius:14px;background:var(--card);padding:14px 16px;margin-bottom:16px}
  .graph .cap{font-size:13px;color:var(--soft);margin-bottom:10px}
  .g{display:flex;gap:6px;align-items:center;flex-wrap:wrap}
  .node{border:1px solid var(--bd);border-radius:10px;padding:6px 11px;min-width:92px;text-align:center;
        background:var(--bg);transition:.18s}
  .node b{display:block;font-size:14.5px;font-weight:700;line-height:1.35}
  .node small{display:block;color:var(--soft);font:12px ui-monospace,Menlo,monospace}
  .node.hot{border-color:var(--accent);background:var(--hl);box-shadow:0 0 0 2px var(--hl)}
  .arw{color:var(--soft);font-size:15px}
  .side{display:flex;gap:6px;align-items:center;margin-top:8px;padding-left:132px}
  .side .node{min-width:auto;font-size:13px}

  /* 步骤卡 */
  .step{display:flex;gap:15px;border:1px solid var(--bd);border-radius:14px;background:var(--card);
        padding:13px;margin-bottom:11px;align-items:flex-start;transition:.18s;scroll-margin-top:10px}
  .step.active{border-color:var(--accent);box-shadow:0 0 0 3px var(--hl)}
  .shotbox{position:relative;flex:0 0 132px;line-height:0}
  .shotbox img{width:132px;border-radius:8px;border:1px solid var(--bd);display:block}
  .shotbox .box{position:absolute;border:2px solid var(--accent);background:var(--hl);border-radius:4px;
        box-shadow:0 0 0 1px rgba(0,0,0,.25);pointer-events:none}
  .shotbox .none{width:132px;height:234px;border:1px dashed var(--bd);border-radius:8px;
        display:flex;align-items:center;justify-content:center;font-size:12px;color:var(--soft)}
  .body{flex:1;min-width:0}
  .num{font:12.5px ui-monospace,Menlo,monospace;color:var(--soft);margin-bottom:3px}
  .path{display:flex;gap:5px;align-items:center;flex-wrap:wrap;margin:0 0 8px}
  .p{font:12.5px/1.5 ui-monospace,Menlo,monospace;color:var(--soft);border:1px solid var(--bd);
     border-radius:6px;padding:0 6px;opacity:.55}
  .p.on{opacity:1;color:var(--accent);border-color:var(--accent);font-weight:700;max-width:230px;
        overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
  .p.done{opacity:.9;color:var(--fg)}
  .think{font-size:15px;margin:0 0 7px}
  .chip{display:inline-block;font:13px/1.6 ui-monospace,Menlo,monospace;border:1px solid var(--bd);
        border-radius:7px;padding:1px 8px;margin:0 7px 5px 0}
  .chip.act{border-color:var(--accent);color:var(--accent)}
  .chip.ok{color:var(--ok);border-color:var(--ok)} .chip.no{color:var(--no);border-color:var(--no)}
  .res{font-size:13.5px;color:var(--soft);white-space:pre-wrap}
  .end{border:1px solid var(--bd);border-left:4px solid var(--ok);border-radius:0 12px 12px 0;
       padding:11px 15px;margin-top:14px;font-size:15.5px}
  .end.no{border-left-color:var(--no)}
  .empty{padding:22px;border:1px dashed var(--bd);border-radius:12px;color:var(--soft)}
  .bar{position:sticky;top:0;z-index:5;background:var(--bg);padding:6px 0 10px;display:flex;gap:10px;align-items:center}
  .bar button{border:1px solid var(--accent);background:transparent;color:var(--accent);border-radius:9px;
        padding:3px 12px;font-size:14px;font-weight:700;cursor:pointer}
  .bar .now{font:13px ui-monospace,Menlo,monospace;color:var(--soft)}
"""


def _flat_ui(d: Path, k: int) -> dict:
    """ui_states/000k.json 摊平 → {index: {text, cls, bounds}}"""
    f = d / "ui_states" / f"{k:04d}.json"
    if not f.is_file():
        return {}
    try:
        tree = json.loads(f.read_text(encoding="utf-8"))
    except Exception:
        return {}
    out: dict = {}

    def walk(nodes):
        for n in nodes or []:
            out[str(n.get("index"))] = {"text": n.get("text") or "", "cls": n.get("className") or "",
                                        "bounds": n.get("bounds") or ""}
            walk(n.get("children"))

    walk(tree)
    return out


def _bounds_box(b: str) -> tuple[float, float, float, float] | None:
    try:
        x1, y1, x2, y2 = [float(v) for v in b.split(",")]
    except Exception:
        return None
    if x2 <= x1 or y2 <= y1:
        return None
    return x1 / 720 * 100, y1 / 1600 * 100, (x2 - x1) / 720 * 100, (y2 - y1) / 1600 * 100


def _label(el: dict) -> str:
    t = (el.get("text") or "").strip()
    if t and not t.startswith("com.") and ":" not in t:
        return t
    cls = el.get("cls") or ""
    return (t or cls.split(".")[-1] or "?")


def read_trajectory(tid: str) -> dict:
    d = traj_path(tid)
    if d is None:
        raise FileNotFoundError(tid)
    traj = json.loads((d / "trajectory.json").read_text(encoding="utf-8"))
    steps, n = [], 0
    for e in traj:
        t = e.get("type")
        if t == "FastAgentResponseEvent":
            name, human = parse_action(e.get("code", ""))
            kv = {k: v.strip() for k, v in re.findall(
                r'<parameter name="([^"]+)">(.*?)</parameter>', e.get("code", "") or "", re.S)}
            steps.append({"i": n, "thought": (e.get("thought") or "").strip(), "tool": name,
                          "action": human, "result": "", "ok": None, "kv": kv,
                          "usage": e.get("usage") or {}})
            n += 1
        elif t == "ToolExecutionEvent" and steps:
            steps[-1]["result"] = e.get("summary") or e.get("tool_name", "")
            steps[-1]["ok"] = e.get("success")
        elif t == "FastAgentEndEvent":
            steps.append({"end": True, "success": e.get("success"), "reason": e.get("reason", "")})

    # 每步：对应截图 + 目标组件（由 ui_states 查 index → 文字 + 框）
    k = 0
    for s in steps:
        if s.get("end"):
            continue
        s["shot"] = f"{k:04d}.png"
        idx = (s.get("kv") or {}).get("index")
        el = _flat_ui(d, k).get(str(idx)) if idx is not None else None
        if el:
            s["comp"] = _label(el)
            s["box"] = _bounds_box(el["bounds"])
        else:
            s["comp"] = {"system_button": "系统键", "swipe": "手势", "open_app": "桌面图标",
                         "type": "输入框", "complete": "收工"}.get(s["tool"], s["tool"])
            s["box"] = None
        k += 1

    shots = sorted(p.name for p in (d / "screenshots").glob("*.png")) if (d / "screenshots").is_dir() else []
    gif = (d / "screenshots" / "trajectory.gif").is_file()
    return {"steps": steps, "shots": shots, "gif": gif, "dir": str(d)}


def render_replay(tid: str) -> str:
    data = read_trajectory(tid)
    steps = data["steps"]
    ok = next((s.get("success") for s in reversed(steps) if s.get("end")), None)
    real = [s for s in steps if not s.get("end")]
    n_steps = len(real)

    # 各节点被行经嘅步数
    cnt = {"壳": n_steps, "turn": n_steps, "mobilerun": n_steps, "portal": n_steps, "model": n_steps}

    out = ['<!DOCTYPE html><html lang="zh-Hans"><head><meta charset="utf-8">',
           '<meta name="viewport" content="width=device-width, initial-scale=1">',
           f'<title>过程图 · {tid}</title><style>{REPLAY_CSS}</style></head><body>',
           '<h1>过程图</h1><div class="top">',
           f'<span class="pill">{tid}</span><span class="pill">{n_steps} 步</span>']
    out.append('<span class="pill ok">✅ 做完</span>' if ok is True else
               ('<span class="pill no">❌ 未做到</span>' if ok is False else ''))
    out.append('</div>')

    # ---- 组件图 ----
    def node(key, title, sub, hot=False):
        cls = "node hot" if hot else "node"
        return f'<div class="{cls}"><b>{title}</b><small>{sub}</small></div>'

    out.append('<div class="graph"><div class="cap">一次交代走过嘅组件（数字 = 行经几次）</div><div class="g">')
    out.append(node("人", "你", "讲一句"))
    out.append('<span class="arw">→</span>')
    out.append(node("壳", "壳", f"{cnt['壳']} 步"))
    out.append('<span class="arw">→</span>')
    out.append(node("turn", "/turn", f"{cnt['turn']} 步"))
    out.append('<span class="arw">→</span>')
    out.append(node("mobilerun", "Mobilerun", f"{cnt['mobilerun']} 步"))
    out.append('<span class="arw">→</span>')
    out.append(node("portal", "Portal APK", f"{cnt['portal']} 步"))
    out.append('<span class="arw">→</span>')
    out.append('<div class="node" id="uinode"><b id="uiname">云机 UI</b><small>被点嘅组件</small></div>')
    out.append('</div><div class="side">')
    out.append(node("model", "模型 · qwen3.8-flash", f"{cnt['model']} 步思考"))
    out.append('<span class="arw">↑↓</span>')
    out.append('<span style="font-size:13px;color:var(--soft)">Mobilerun 每一步都问模型：睇咩、点边个</span>')
    out.append('</div></div>')

    # ---- 播控 ----
    out.append('<div class="bar"><button id="pp">▶ 逐步播</button>'
               '<button id="pv">◀</button><button id="nx">▶</button>'
               '<span class="now" id="now">揾步 1 / %d</span></div>' % max(n_steps, 1))

    if not n_steps:
        out.append('<div class="empty">呢条轨迹冇记录到 agent 步骤（可能一开始就失败）。</div>')

    for s in steps:
        if s.get("end"):
            cls = "end" if s.get("success") else "end no"
            out.append(f'<div class="{cls}">{s.get("reason") or ""}</div>')
            continue
        box = ""
        if s.get("box"):
            x, y, w, h = s["box"]
            box = f'<div class="box" style="left:{x:.2f}%;top:{y:.2f}%;width:{w:.2f}%;height:{h:.2f}%"></div>'
        img = (f'<img src="/traj/{tid}/screenshots/{s["shot"]}" alt="step {s["i"]+1}">{box}'
               if s["shot"] in data["shots"] else '<div class="none">冇截图</div>')
        okchip = ('<span class="chip ok">✅ 成功</span>' if s["ok"] is True else
                  ('<span class="chip no">❌ 失败</span>' if s["ok"] is False else ""))
        comp = s.get("comp") or "?"
        out.append(
            f'<div class="step" id="s{s["i"]}" data-comp="{comp}"><div class="shotbox">{img}</div><div class="body">'
            f'<div class="num">STEP {s["i"]+1} / {n_steps}</div>'
            f'<div class="path">'
            f'<span class="p done">你</span><span class="p done">壳</span><span class="p done">/turn</span>'
            f'<span class="p done">Mobilerun</span><span class="p done">Portal</span>'
            f'<span class="p on">{comp}</span></div>'
            f'<div class="think">{s["thought"] or "（冇思考输出）"}</div>'
            f'<span class="chip act">{s["action"]}</span>{okchip}'
            + (f'<div class="res">{s["result"]}</div>' if s["result"] else "")
            + '</div></div>'
        )

    out.append("""<script>
const steps=[...document.querySelectorAll('.step')];
const now=document.getElementById('now');
let i=-1, timer=null;
function focusStep(k){
  if(!steps.length) return;
  i=Math.max(0, Math.min(steps.length-1, k));
  steps.forEach((el,j)=>el.classList.toggle('active', j===i));
  steps[i].scrollIntoView({block:'center', behavior:'smooth'});
  now.textContent='揾步 '+(i+1)+' / '+steps.length;
  const un=document.getElementById('uiname'), unode=document.getElementById('uinode');
  if(un&&unode){ un.textContent=steps[i].dataset.comp||'云机 UI'; unode.classList.add('hot'); }
}
document.getElementById('nx').onclick=()=>focusStep(i+1);
document.getElementById('pv').onclick=()=>focusStep(i-1);
document.getElementById('pp').onclick=e=>{
  if(timer){clearInterval(timer);timer=null;e.target.textContent='▶ 逐步播';return;}
  e.target.textContent='⏸ 停';
  focusStep(i<0?0:i);
  timer=setInterval(()=>{ if(i>=steps.length-1){clearInterval(timer);timer=null;
      document.getElementById('pp').textContent='▶ 逐步播';return;} focusStep(i+1); },1600);
};
if(steps.length) focusStep(0);
</script>""")
    out.append('</body></html>')
    return "".join(out)


# ─────────────────────────────── 实时视频流（方案 B：H.264 → WebCodecs）
# 设备只允许一路 screenrecord，所以单路编码 + 广播畀所有浏览器。
# 每帧格式：[4B 长度][1B 类型 1=IDR 2=普通帧 3=参数集][4B 时间戳 ms][payload]
STREAM = {"clients": [], "proc": None, "thread": None, "lock": threading.Lock(), "param": b""}
STREAM_IDLE_STOP = 8.0     # 冇客户端咁久就停编码，省 CPU


def _nal_split(buf: bytes):
    """Annex-B 切 NAL：返回 (完整 NAL 列表, 未收齐嘅尾巴)。"""
    starts, i, n = [], 0, len(buf)
    while i < n - 3:
        if buf[i] == 0 and buf[i + 1] == 0 and buf[i + 2] == 1:
            starts.append((i, 3)); i += 3
        elif buf[i] == 0 and buf[i + 1] == 0 and buf[i + 2] == 0 and i + 3 < n and buf[i + 3] == 1:
            starts.append((i, 4)); i += 4
        else:
            i += 1
    if len(starts) < 2:
        return [], buf
    out = []
    for k in range(len(starts) - 1):
        pos, hdr = starts[k]
        nxt = starts[k + 1][0]
        nal = buf[pos + hdr:nxt]
        if nal:
            out.append(nal)
    return out, buf[starts[-1][0]:]


def _encoder_loop():
    """一路 screenrecord，解析成帧广播；到期（180s）自动重开。"""
    idle_since = None
    while True:
        with STREAM["lock"]:
            if not STREAM["clients"]:
                if idle_since is None:
                    idle_since = time.time()
                elif time.time() - idle_since > STREAM_IDLE_STOP:
                    STREAM["thread"] = None
                    return
            else:
                idle_since = None
        if not STREAM["clients"]:
            time.sleep(0.2); continue

        t0 = time.time()
        proc = subprocess.Popen(
            [ADB, "-s", TARGET, "shell", "screenrecord",
             "--output-format=h264", "--size", "720x1280",
             "--bit-rate", "6000000", "--time-limit", "180", "-"],
            stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, bufsize=0)
        STREAM["proc"] = proc
        buf = b""
        try:
            while True:
                chunk = proc.stdout.read(65536)
                if not chunk:
                    break
                buf += chunk
                nals, buf = _nal_split(buf)
                if not nals:
                    continue
                ts = int((time.time() - t0) * 1000) & 0xFFFFFFFF
                payloads = []
                for nal in nals:
                    t = nal[0] & 0x1F
                    kind = 3 if t in (7, 8, 9) else (1 if t == 5 else (2 if t in (1, 2) else 0))
                    if not kind:
                        continue
                    payloads.append(len(nal).to_bytes(4, "big") + bytes([kind])
                                    + ts.to_bytes(4, "big") + nal)
                if not payloads:
                    continue
                blob = b"".join(payloads)
                if any(p[4] == 1 for p in payloads):     # 出过 IDR，前端解码器先算「热」
                    STREAM["warm"] = True
                with STREAM["lock"]:
                    for q in list(STREAM["clients"]):
                        try:
                            q.put_nowait(blob)
                        except queue.Full:
                            pass
        except Exception:  # noqa: BLE001
            pass
        finally:
            try:
                proc.kill()
            except Exception:  # noqa: BLE001
                pass
            STREAM["proc"] = None
        time.sleep(0.15)


def stream_h264(handler, fresh: bool = False) -> None:
    """订阅广播：每个客户端一条队列，编码器一路。fresh=1 强制重开（拿新 SPS+IDR）。"""
    q: "queue.Queue[bytes]" = queue.Queue(maxsize=240)
    with STREAM["lock"]:
        first = not STREAM["clients"]
        STREAM["clients"].append(q)
        warm = STREAM.get("warm", False)
        # 编码器已经跑到中途：重开一路，令新客户端由 SPS + IDR 开始（否则解唔到）
        if STREAM["proc"] is not None and (first or warm):
            try:
                STREAM["proc"].kill()
            except Exception:  # noqa: BLE001
                pass
        if fresh and STREAM["proc"] is not None:
            try:
                STREAM["proc"].kill()
            except Exception:  # noqa: BLE001
                pass
        STREAM["warm"] = False
        if STREAM["thread"] is None or not STREAM["thread"].is_alive():
            STREAM["thread"] = threading.Thread(target=_encoder_loop, daemon=True)
            STREAM["thread"].start()
    handler.send_response(200)
    handler._cors()
    handler.send_header("Content-Type", "application/octet-stream")
    handler.send_header("Cache-Control", "no-store")
    handler.end_headers()
    try:
        while True:
            blob = q.get(timeout=15)
            handler.wfile.write(blob)
            handler.wfile.flush()
    except (queue.Empty, BrokenPipeError, ConnectionResetError):
        pass
    finally:
        with STREAM["lock"]:
            if q in STREAM["clients"]:
                STREAM["clients"].remove(q)


# ─────────────────────────────── 帧缓存（启动优化）
FRAME = {"png": b"", "ts": 0.0, "last_req": 0.0, "err": ""}
FRAME_FILE = Path(os.environ.get("QPHONE_FRAME",
                 os.path.expanduser("~/.hermes/cache/scratch/qphone_frame.png")))
FRAME_LOCK = threading.Lock()
FRAME_EVT = threading.Event()   # 有请求就即刻唤醒抓帧


KEYMAP = {"back": "4", "home": "3", "appswitch": "5", "menu": "82",
          "enter": "66", "del": "67", "power": "26", "wakeup": "224", "sleep": "223"}


def device_input(path: str, p: dict) -> dict:
    """壳嘅「手」：tap / swipe / key。做完即刻抓一帧，令壳即刻见到反应。"""
    t0 = time.time()
    if path == "/device/tap":
        x, y = int(p.get("x", -1)), int(p.get("y", -1))
        if not (0 <= x < 8192 and 0 <= y < 8192):
            raise ValueError("坐标唔对（要设备像素）")
        cmd = [ADB, "-s", TARGET, "shell", "input", "tap", str(x), str(y)]
    elif path == "/device/swipe":
        x1, y1 = int(p.get("x1", -1)), int(p.get("y1", -1))
        x2, y2 = int(p.get("x2", -1)), int(p.get("y2", -1))
        ms = max(30, min(4000, int(p.get("ms", 220))))
        if min(x1, y1, x2, y2) < 0:
            raise ValueError("滑动要四个坐标 x1 y1 x2 y2")
        cmd = [ADB, "-s", TARGET, "shell", "input", "swipe",
               str(x1), str(y1), str(x2), str(y2), str(ms)]
    elif path == "/device/key":
        k = str(p.get("key", "")).strip().lower()
        code = KEYMAP.get(k) or (k if k.isdigit() else "")
        if not code:
            raise ValueError("唔识呢个键 %r；可用：%s" % (k, " / ".join(KEYMAP)))
        cmd = [ADB, "-s", TARGET, "shell", "input", "keyevent", str(code)]
    else:
        raise ValueError("唔识嘅输入：%s" % path)

    cp = subprocess.run(cmd, capture_output=True, timeout=20)
    if cp.returncode != 0:
        msg = (cp.stderr or cp.stdout).decode("utf-8", "ignore").strip()[:160]
        raise RuntimeError(msg or "adb 执行失败")
    if p.get("frame", True):
        try:
            grab_frame()
        except Exception:  # noqa: BLE001
            pass
    return {"ok": True, "input": path.rsplit("/", 1)[-1], "ms": int((time.time() - t0) * 1000)}


def press_home(delay: float = 0.8) -> bool:
    """收尾复位：按一次 HOME，顺手刷新一帧。"""
    try:
        time.sleep(delay)
        subprocess.run([ADB, "-s", TARGET, "shell", "input", "keyevent", "3"],
                       capture_output=True, timeout=10)
        print(f"[{time.strftime('%H:%M:%S')}] HOME 收尾（完单复位）", flush=True)
        time.sleep(0.5)
        try:
            grab_frame()
        except Exception:  # noqa: BLE001
            pass
        return True
    except Exception:  # noqa: BLE001
        return False


def grab_frame(timeout: float = 12.0) -> bytes:
    """抓一帧 PNG（唯一一处 adb screencap）。"""
    png = subprocess.run([ADB, "-s", TARGET, "exec-out", "screencap", "-p"],
                         capture_output=True, timeout=timeout)
    body = png.stdout
    if not body.startswith(b"\x89PNG"):
        raise RuntimeError("screencap 冇返 PNG： " + (png.stderr or b"")[:120].decode("utf-8", "ignore"))
    with FRAME_LOCK:
        FRAME["png"], FRAME["ts"], FRAME["err"] = body, time.time(), ""
    try:                                  # 落盘：下次冷启可以即刻出旧帧，唔等 adb
        FRAME_FILE.parent.mkdir(parents=True, exist_ok=True)
        tmp = FRAME_FILE.with_suffix(".tmp")
        tmp.write_bytes(body)
        tmp.replace(FRAME_FILE)
    except Exception:
        pass
    return body


def seed_frame_from_disk() -> None:
    """启动时载入上一帧，令首屏唔使等 1.3s 抓图。"""
    try:
        if FRAME_FILE.exists():
            body = FRAME_FILE.read_bytes()
            if body.startswith(b"\x89PNG"):
                with FRAME_LOCK:
                    FRAME["png"], FRAME["ts"] = body, FRAME_FILE.stat().st_mtime
                print(f"预热：载入旧帧 {len(body)}B", flush=True)
    except Exception as e:
        print(f"预热失败（唔影响）：{e}", flush=True)


def frame_worker() -> None:
    """后台保帧：页面有人睇就密抓，冇人睇就懒抓；跑任务时加密。"""
    while True:
        idle = time.time() - FRAME["last_req"]
        running = STATE.get("phase") in ("think", "act", "step")
        if running:
            interval = 1.8          # 跑任务时 1.8s（同 Portal 截图错开）
        elif idle < 20:
            interval = 1.5
        elif idle < 120:
            interval = 4.0
        elif idle < 900:
            interval = 20.0
        else:
            FRAME_EVT.wait(30.0)
            FRAME_EVT.clear()
            continue
        try:
            grab_frame()
        except Exception as e:
            with FRAME_LOCK:
                FRAME["err"] = str(e)[:160]
        FRAME_EVT.wait(interval)      # 有人开页面会即时醒来，唔使等满 interval
        FRAME_EVT.clear()


# ── 执行层触发条件（/layers 对外，畀人核对「点解走呢层」）
LAYERS_DOC = {
    "order": ["L0", "L1a", "L1b", "L2"],
    "rules": {
        "L0": {"触发": "正则命中能力表（打开/关/查有无装/下载安装查重/音量/亮度/开关/截图/点坐标/滑动/ASCII 输入/查前台…）",
               "执行": "adb shell 直连：am start -W -n / input keyevent / settings put / pm list packages",
               "断言": "topResumedActivity 变到目标包、settings get 读回一致、pm list 有命中"},
        "L1a": {"触发": "正则「动词 + 1~12 字目标」，动词表 = 点击|点开|点|按下|按|选择|选|打开|进入|查找|找|搜|搜索|查看|睇|看|填|输入|tap|click",
                "执行": "agent-device snapshot -i → label/identifier 精确→包含→去空格→中英映射命中控件 → input tap 控件中心",
                "断言": "前台包变咗或快照 hash 变咗；搵唔到控件即 miss（why=无障碍树冇呢个控件）"},
        "L1b": {"触发": "同时满足：① 句子里有「动词+目标」② /proc/net/unix 有 @webview_devtools_remote_<pid>",
                "执行": "adb forward → GET /json 取 page → WebSocket Runtime.evaluate 找文字元素 .click() / 填值",
                "断言": "JS 返回 ok:true；冇 socket 即 miss（why=冇 WebView 调试 socket）"},
        "L2": {"触发": "上面三层全部 miss",
               "执行": "mobilerun run（截图 + 多模态模型），--steps 20",
               "断言": "Mobilerun 自己判；失败出「要人」"},
    },
}


class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"
    server_version = "qphone-turn/0.1"

    def log_message(self, fmt, *args):  # 静默认，唔刷终端
        if os.environ.get("QPHONE_DEBUG"):
            sys.stderr.write("%s - %s\n" % (self.address_string(), fmt % args))

    def _cors(self) -> None:
        # 壳可能系 file:// 或另一个端口，放行全部来源；本服务只绑本机
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")

    def do_OPTIONS(self) -> None:  # noqa: N802
        self.send_response(204)
        self._cors()
        self.send_header("Content-Length", "0")
        self.end_headers()

    def _send_json(self, obj: dict, code: int = 200) -> None:
        body = json.dumps(obj, ensure_ascii=False).encode()
        self.send_response(code)
        self._cors()
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self) -> None:  # noqa: N802
        path = urlparse(self.path).path          # 忽略 query，免得 /?v=3 变 404
        if path in ("/", "/shell", "/shell.html"):
            f = HERE / "shell.html"
            if not f.is_file():
                return self._send_json({"ok": False, "error": "冇 shell.html"}, 404)
            body = f.read_bytes()
            self.send_response(200)
            self._cors()
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Cache-Control", "no-store, must-revalidate")  # 改完即时生效，唔使换 v 号
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return
        if path == "/stream":
            return stream_h264(self, fresh="fresh=1" in (self.path or ""))
        if path in ("/screen.png", "/screen"):
            FRAME["last_req"] = time.time()
            FRAME_EVT.set()
            try:
                with FRAME_LOCK:
                    body, ts = FRAME["png"], FRAME["ts"]
                if not body:                       # 冷启第一帧：后备同步抓
                    body = grab_frame()
                    ts = time.time()
                self.send_response(200)
                self._cors()
                self.send_header("Content-Type", "image/png")
                self.send_header("Cache-Control", "no-store, max-age=0")
                self.send_header("X-Frame-Age", f"{time.time() - ts:.2f}")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)
            except Exception as e:
                self._send_json({"ok": False, "error": str(e)}, 503)
            return
        if path.startswith("/replay"):
            q = parse_qs(urlparse(self.path).query)
            tid = (q.get("id") or [""])[0]
            if not re.fullmatch(r"[\w.-]+", tid or ""):
                return self._send_json({"ok": False, "error": "要 ?id=<轨迹 id>"}, 400)
            try:
                body = render_replay(tid).encode()
            except FileNotFoundError:
                return self._send_json({"ok": False, "error": f"搵唔到轨迹 {tid}"}, 404)
            self.send_response(200)
            self._cors()
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return
        if path.startswith("/traj/"):
            m = re.fullmatch(r"/traj/([\w.-]+)/(screenshots|ui_states)/([\w.-]+)", path)
            if not m:
                return self._send_json({"ok": False, "error": "路径唔对"}, 400)
            f = None
            td = traj_path(m.group(1))
            if td is not None:
                f = td / m.group(2) / m.group(3)
            if f is None or not f.is_file():
                return self._send_json({"ok": False, "error": "冇呢个文件"}, 404)
            body = f.read_bytes()
            ctype = "image/png" if f.suffix == ".png" else (
                "image/gif" if f.suffix == ".gif" else "application/json; charset=utf-8")
            self.send_response(200)
            self._cors()
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return
        if path == "/state":
            return self._send_json(dict(STATE))
        if path == "/history":
            with HIST_LOCK:
                items = [dict(x) for x in HISTORY]
            return self._send_json({"items": items, "state": dict(STATE),
                                    "model": MODEL, "of": int(STEPS)})
        if path == "/health":
            return self._send_json({"ok": True, "target": TARGET, "model": MODEL, "provider": PROVIDER})
        if path == "/layers":
            d = dict(LAYERS_DOC)
            d["last_attempts"] = getattr(layers, "LAST_ATTEMPTS", [])
            try:
                d["webview_sockets"] = layers.webview_sockets()
            except Exception:                              # noqa: BLE001
                d["webview_sockets"] = []
            return self._send_json(d)
        if path == "/status":
            return self._send_json(qphone_status())
        self._send_json({"ok": False, "error": "唔识呢条路径"}, 404)

    def do_POST(self) -> None:  # noqa: N802
        if self.path == "/device/home":
            ok = press_home(0.0)
            return self._send_json({"ok": ok})
        if self.path in ("/device/tap", "/device/swipe", "/device/key"):
            length = int(self.headers.get("Content-Length") or 0)
            try:
                p = json.loads(self.rfile.read(length) or b"{}")
            except json.JSONDecodeError:
                return self._send_json({"ok": False, "error": "body 要系 JSON"}, 400)
            try:
                return self._send_json(device_input(self.path, p))
            except ValueError as e:
                return self._send_json({"ok": False, "error": str(e)}, 400)
            except Exception as e:  # noqa: BLE001
                return self._send_json({"ok": False, "error": str(e)}, 500)
        if self.path == "/history/clear":
            with HIST_LOCK:
                n = len(HISTORY); HISTORY.clear()
            CURRENT_TASK["id"] = ""
            LAST_RESULT.update(ok=None, msg="", ts=0.0)
            return self._send_json({"ok": True, "cleared": n})
        if self.path != "/turn":
            return self._send_json({"ok": False, "error": "唔识呢条路径"}, 404)
        length = int(self.headers.get("Content-Length") or 0)
        try:
            payload = json.loads(self.rfile.read(length) or b"{}")
        except json.JSONDecodeError:
            return self._send_json({"ok": False, "error": "body 要系 JSON"}, 400)
        text = (payload.get("text") or "").strip()
        if not text:
            return self._send_json({"ok": False, "error": "text 唔可以为空"}, 400)
        mode = (payload.get("mode") or "auto").strip().lower()   # auto | task | chat

        self.send_response(200)
        self._cors()
        self.send_header("Content-Type", "text/event-stream; charset=utf-8")
        self.send_header("Cache-Control", "no-cache")
        self.send_header("Connection", "close")
        self.end_headers()

        KIND = {"activity": "task", "status": "status", "progress": "task", "result": "task",
                "needs_human": "human", "trajectory": "task", "chat": "chat",
                "clarify": "unclear", "intent": "meta", "layer": "task"}

        alive = {"ok": True}

        def emit(event: str, data: dict) -> None:
            if not alive["ok"]:
                return
            data = dict(data)
            data.setdefault("kind", KIND.get(event, event))
            try:
                self.wfile.write(f"event: {event}\ndata: {json.dumps(data, ensure_ascii=False)}\n\n".encode())
                self.wfile.flush()
            except (BrokenPipeError, ConnectionResetError):
                alive["ok"] = False        # 前端刷新/关页：任务照跑完，靠 /history 补            




        hist_add("me", text=text)
        verdict = classify(text) if mode == "auto" else {"mode": mode, "why": "用户指定"}
        emit("intent", {"mode": verdict["mode"], "why": verdict.get("why", ""), "text": text})

        # ── 对话：唔起 Mobilerun，直接问模型 ──
        if verdict["mode"] == "chat":
            _pub(None, "chat")
            try:
                reply = answer_chat(text)
                hist_add("chat", text=reply)
                emit("chat", {"text": reply})
                _pub(None, "idle")
            except Exception as e:  # noqa: BLE001
                emit("needs_human", {"reason": f"对话模型出错：{e}"})
                emit("result", {"success": False, "message": str(e)})
            return

        # ── 判唔清：反问，等用户拣 ──
        if verdict["mode"] == "unclear":
            _pub(None, "idle")
            hist_add("chat", text="想我帮你做，定系问下？", options=["当任务跑", "就系问下"])
            emit("clarify", {"question": "想我帮你做，定系问下？",
                             "options": [{"label": "当任务跑", "mode": "task"},
                                         {"label": "就系问下", "mode": "chat"}]})
            return

        st = qphone_status()
        emit("status", {"state": "running" if st.get("booted") else "not_ready",
                        "device": TARGET, "note": st.get("note", "")})
        if not st.get("booted"):
            emit("needs_human", {"reason": st.get("note") or "云机未就绪"})
            emit("result", {"success": False, "message": "云机未就绪，唔开工"})
            return

        tid = uuid.uuid4().hex[:8]
        CURRENT_TASK["id"] = tid
        hist_add("task", id=tid, text=text, phase="running", step=0, of=int(STEPS), msg="", detail="")
        emit("task_started", {"id": tid, "of": int(STEPS)})

        q: "queue.Queue[tuple[str, dict]]" = queue.Queue()
        threading.Thread(target=run_agent, args=(text, q), daemon=True).start()
        while True:
            event, data = q.get()
            if event == "_end":
                break
            emit(event, data)


def main() -> int:
    srv = ThreadingHTTPServer((BIND, PORT), Handler)
    seed_frame_from_disk()                                      # 先用上一帧顶上
    threading.Thread(target=frame_worker, daemon=True).start()   # 启动即预抓，首屏唔等 adb
    print(f"qphone /turn 服务起咗：http://{BIND}:{PORT}  device={TARGET}  model={MODEL}", flush=True)
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        print("\n收工", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
