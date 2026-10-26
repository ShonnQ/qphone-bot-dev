#!/usr/bin/env python3
"""QPhone 控制程序 · 第一部最小闭环

只做一件事：把人一句交代，变成云机上的一次 adb 操作，并老实讲出结果。
本版无 agent（Mobilerun 留第二版）；只用本机 adb 连 127.0.0.1:5555。

用法：
    ./qphone.py status
    ./qphone.py start | stop
    ./qphone.py install /path/app.apk
    ./qphone.py launch com.android.settings
    ./qphone.py click 540 1200
    ./qphone.py shot [out.png]
    ./qphone.py apps                 # 列已装第三方包

    --json   结构化输出（给客户端接线用）

环境变量：
    QPHONE_ADB         adb 可执行路径（默认自动找）
    QPHONE_TARGET      云机地址（默认 127.0.0.1:5555）
    QPHONE_CONTAINER   redroid 容器名（默认自动找第一个 redroid 容器）

退出码：0 做成；1 失败（stderr 讲原因）；2 用法错。
"""

from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

TARGET = os.environ.get("QPHONE_TARGET", "127.0.0.1:5555")
CONTAINER_HINT = os.environ.get("QPHONE_CONTAINER", "")

ADB_CANDIDATES = [
    os.environ.get("QPHONE_ADB", ""),
    "adb",
    str(Path.home() / "Android/Sdk/platform-tools/adb"),
    "/usr/lib/android-sdk/platform-tools/adb",
    "/opt/android-sdk/platform-tools/adb",
]


class Fail(Exception):
    """交代做唔到。原因要讲人话。"""


def adb_path() -> str:
    for c in ADB_CANDIDATES:
        if not c:
            continue
        p = shutil.which(c) if "/" not in c else (c if os.access(c, os.X_OK) else "")
        if p:
            return p
    raise Fail("本机搵唔到 adb。装 platform-tools，或设 QPHONE_ADB 指去可执行文件。")


def run(cmd: list[str], timeout: int = 60) -> subprocess.CompletedProcess:
    try:
        return subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
    except FileNotFoundError:
        raise Fail(f"搵唔到命令：{cmd[0]}")
    except subprocess.TimeoutExpired:
        raise Fail(f"命令超时（{timeout}s）：{' '.join(cmd[:3])}…")


def adb(*args: str, timeout: int = 60) -> str:
    cp = run([adb_path(), "-s", TARGET, *args], timeout=timeout)
    out = (cp.stdout or "").strip()
    err = (cp.stderr or "").strip()
    if cp.returncode != 0:
        raise Fail(f"adb {' '.join(args[:2])} 失败：{err or out or f'退出码 {cp.returncode}'}")
    return out


def listed_devices() -> list[str]:
    cp = run([adb_path(), "devices"], timeout=20)
    out = []
    for line in (cp.stdout or "").splitlines()[1:]:
        parts = line.split()
        if len(parts) >= 2 and parts[1] == "device":
            out.append(parts[0])
    return out


def connect() -> None:
    # 本地模拟器（emulator-5554）本来就已经挂喺 adb devices，唔使 adb connect
    if TARGET.startswith("emulator-") or TARGET in listed_devices():
        return
    cp = run([adb_path(), "connect", TARGET], timeout=20)
    text = f"{cp.stdout}\n{cp.stderr}".lower()
    if "unable to connect" in text or "failed" in text or "refused" in text:
        raise Fail(f"连唔到云机 {TARGET}：{text.strip().splitlines()[0] if text.strip() else '无回应'}")


def boot_completed() -> bool:
    try:
        return adb("shell", "getprop", "sys.boot_completed", timeout=20).strip() == "1"
    except Fail:
        return False


def container() -> str:
    if CONTAINER_HINT:
        return CONTAINER_HINT
    cp = run(["docker", "ps", "--format", "{{.Names}}\t{{.Image}}"], timeout=20)
    for line in (cp.stdout or "").splitlines():
        name, _, image = line.partition("\t")
        if "redroid" in image or "redroid" in name:
            return name.strip()
    raise Fail("搵唔到跑紧嘅 redroid 容器。用 docker ps 睇，或设 QPHONE_CONTAINER。")


# 中文应用名对唔到包名（包名永远系英文），所以先过一层别名表。
# 只放第一部要验证嘅常用几个，唔好写成一本字典。
ALIASES = {
    "设置": "settings", "設定": "settings", "设置页": "settings",
    "相机": "camera", "相机": "camera", "图库": "gallery", "相册": "gallery",
    "时钟": "clock", "日历": "calendar", "计算器": "calculator",
    "文件管理": "documentsui", "浏览器": "chrome", "电话": "dialer", "短信": "messaging",
}


def _installed(third_party_only: bool) -> list[str]:
    args = ["shell", "cmd", "package", "list", "packages"] + (["-3"] if third_party_only else [])
    out = adb(*args, timeout=30)
    return [ln.split(":", 1)[1].strip() for ln in out.splitlines() if ln.startswith("package:")]


def resolve_package(word: str) -> str:
    """应用名 → 包名。包名直用；中文名过别名表；再按包名尾段优先配。"""
    if re.fullmatch(r"[A-Za-z][\w.]*\.[\w.]+", word):
        if adb("shell", "pm", "list", "packages", word).strip():
            return word

    kw = ALIASES.get(word.strip(), word).strip().lower()

    for pool in (_installed(True), _installed(False)):
        hits = [p for p in pool if kw in p.lower()]
        if not hits:
            continue
        # 包名尾段命中（com.android.settings 对 settings）优先过子包（.settings.intelligence）
        tail = [p for p in hits if p.lower().rsplit(".", 1)[-1].startswith(kw)]
        pick = tail or hits
        # 同一族里取最短嗰个：com.android.settings > com.android.settings.intelligence
        pick.sort(key=len)
        if len(pick) == 1 or len(pick[0]) < len(pick[1]):
            return pick[0]
        raise Fail(f"「{word}」对到几个应用，讲清楚边个：{', '.join(pick[:5])}")

    raise Fail(f"云机上搵唔到「{word}」。先 `qphone.py apps` 睇已装应用（系统应用要试英文名，例如 settings）。")


# ── 动作 ────────────────────────────────────────────────────────────────


def cmd_status(_: argparse.Namespace) -> dict:
    state = {"target": TARGET, "reachable": False, "booted": False, "note": ""}
    try:
        connect()
        state["reachable"] = True
    except Fail as e:
        state["note"] = str(e)
        return state
    if not boot_completed():
        state["note"] = "adb 通，系统未开机完成（起紧或卡住）"
        return state
    state["booted"] = True
    state["sdk"] = adb("shell", "getprop", "ro.build.version.sdk", timeout=20).strip()
    state["serial"] = adb("shell", "getprop", "ro.serialno", timeout=20).strip()
    state["note"] = "可以操作"
    return state


def cmd_start(_: argparse.Namespace) -> dict:
    name = container()
    cp = run(["docker", "start", name], timeout=120)
    if cp.returncode != 0:
        raise Fail(f"起容器 {name} 失败：{(cp.stderr or cp.stdout).strip()}")
    return {"container": name, "started": True}


def cmd_stop(_: argparse.Namespace) -> dict:
    name = container()
    cp = run(["docker", "stop", name], timeout=180)
    if cp.returncode != 0:
        raise Fail(f"停容器 {name} 失败：{(cp.stderr or cp.stdout).strip()}")
    return {"container": name, "stopped": True}


def cmd_install(ns: argparse.Namespace) -> dict:
    apk = Path(ns.apk).expanduser()
    if not apk.is_file():
        raise Fail(f"搵唔到安装包：{apk}")
    out = adb("install", "-r", str(apk), timeout=600)
    if "Success" not in out:
        raise Fail(f"装唔到：{out}")
    return {"apk": str(apk), "installed": True}


def foreground() -> str:
    """前台 Activity。不同 ROM 落点唔同，三个来源依次试。"""
    for args, pat in (
        (("shell", "dumpsys", "window", "windows"), r"mCurrentFocus=Window\{\S+ \S+ ([^}\s]+)"),
        (("shell", "dumpsys", "window"), r"mCurrentFocus=Window\{\S+ \S+ ([^}\s]+)"),
        (("shell", "dumpsys", "activity", "activities"), r"mResumedActivity:.*?([\w.]+/[\w.$]+)"),
    ):
        try:
            m = re.search(pat, adb(*args, timeout=30))
        except Fail:
            continue
        if m:
            return m.group(1)
    return ""


def cmd_launch(ns: argparse.Namespace) -> dict:
    pkg = resolve_package(ns.app)
    adb("shell", "monkey", "-p", pkg, "-c", "android.intent.category.LAUNCHER", "1", timeout=60)
    return {"package": pkg, "foreground": foreground()}


def cmd_click(ns: argparse.Namespace) -> dict:
    adb("shell", "input", "tap", str(ns.x), str(ns.y))
    return {"tapped": [ns.x, ns.y]}


def cmd_shot(ns: argparse.Namespace) -> dict:
    out = Path(ns.out).expanduser()
    out.parent.mkdir(parents=True, exist_ok=True)
    cp = subprocess.run(
        [adb_path(), "-s", TARGET, "exec-out", "screencap", "-p"],
        capture_output=True, timeout=60,
    )
    if cp.returncode != 0 or not cp.stdout:
        raise Fail(f"截图失败：{(cp.stderr or b'').decode(errors='replace').strip()}")
    out.write_bytes(cp.stdout)
    return {"png": str(out), "bytes": len(cp.stdout)}


def cmd_apps(_: argparse.Namespace) -> dict:
    listing = adb("shell", "cmd", "package", "list", "packages", "-3", timeout=30)
    pkgs = sorted(ln.split(":", 1)[1].strip() for ln in listing.splitlines() if ln.startswith("package:"))
    return {"count": len(pkgs), "packages": pkgs}


# ── 输出 ────────────────────────────────────────────────────────────────

HUMAN = {
    "status": lambda r: (
        f"云机 {r['target']}：{r['note']}"
        + (f"（Android SDK {r['sdk']}）" if r.get("sdk") else "")
    ),
    "start": lambda r: f"容器 {r['container']} 已起。等系统开机完成再 `status` 确认。",
    "stop": lambda r: f"容器 {r['container']} 已停。",
    "install": lambda r: f"装好：{Path(r['apk']).name}",
    "launch": lambda r: f"已开 {r['package']}" + (f"，前台 {r['foreground']}" if r["foreground"] else "（前台未知，用 shot 睇）"),
    "click": lambda r: f"已点 ({r['tapped'][0]}, {r['tapped'][1]})",
    "shot": lambda r: f"截图 {r['png']}（{r['bytes']} 字节）",
    "apps": lambda r: f"第三方应用 {r['count']} 个：\n" + "\n".join(f"  {p}" for p in r["packages"]),
}


def main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser(prog="qphone", description="QPhone 控制程序（第一部）")
    ap.add_argument("--json", action="store_true", help="结构化输出")
    sub = ap.add_subparsers(dest="cmd", required=True)

    sub.add_parser("status", help="云机开/关/可否操作").set_defaults(fn=cmd_status)
    sub.add_parser("start", help="起 redroid 容器").set_defaults(fn=cmd_start)
    sub.add_parser("stop", help="停 redroid 容器").set_defaults(fn=cmd_stop)
    sub.add_parser("apps", help="列已装第三方应用").set_defaults(fn=cmd_apps)

    p = sub.add_parser("install", help="装安装包")
    p.add_argument("apk")
    p.set_defaults(fn=cmd_install)

    p = sub.add_parser("launch", help="开应用（包名或名字）")
    p.add_argument("app")
    p.set_defaults(fn=cmd_launch)

    p = sub.add_parser("click", help="点一下屏幕")
    p.add_argument("x", type=int)
    p.add_argument("y", type=int)
    p.set_defaults(fn=cmd_click)

    p = sub.add_parser("shot", help="截当前画面")
    p.add_argument("out", nargs="?", default="qphone-shot.png")
    p.set_defaults(fn=cmd_shot)

    ns = ap.parse_args(argv)
    try:
        result = ns.fn(ns)
    except Fail as e:
        print(f"× {e}", file=sys.stderr)
        if ns.json:
            print(json.dumps({"ok": False, "error": str(e)}, ensure_ascii=False))
        return 1
    if ns.json:
        print(json.dumps({"ok": True, "cmd": ns.cmd, **result}, ensure_ascii=False))
    else:
        print(HUMAN[ns.cmd](result))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
