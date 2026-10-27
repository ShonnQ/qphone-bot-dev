#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""QPhone 执行层：L0 确定性 / L1a 无障碍结构 / L1b WebView CDP。

设计原则
- 只做「一句话 → 一组确定命令」，命中就跑，唔命中就返回 None 交下一层，最后才 L2 视觉。
- 每层都要回一个 trace（layer / label / cmds / ms / ok / why），壳靠它标记走咗边层。
- 断言用设备真状态（topResumedActivity / settings get / 快照 diff），唔靠命令 exit code。
"""

from __future__ import annotations

import base64
import hashlib
import json
import os
import re
import shutil
import socket
import struct
import subprocess
import time
import urllib.request
from urllib.parse import quote

ADB = os.environ.get("QPHONE_ADB", os.path.expanduser("~/Library/Android/sdk/platform-tools/adb"))
TARGET = os.environ.get("QPHONE_TARGET", "localhost:5556")
SERIAL = TARGET


# ─────────────────────────────── 基础命令
def adb(*args: str, timeout: float = 25.0, binary: bool = False):
    cmd = [ADB, "-s", TARGET] + list(args)
    cp = subprocess.run(cmd, capture_output=True, timeout=timeout)
    if binary:
        return cp.returncode, cp.stdout
    return cp.returncode, cp.stdout.decode("utf-8", "replace")


def sh(cmd: str, timeout: float = 25.0) -> str:
    return adb("shell", cmd, timeout=timeout)[1]


def now_ms() -> int:
    return int(time.time() * 1000)


def trace(layer: str, label: str, cmds: list[str], t0: float, ok: bool, why: str = "") -> dict:
    return {"layer": layer, "label": label, "cmds": cmds,
            "ms": now_ms() - int(t0 * 1000), "ok": ok, "why": why}


# ─────────────────────────────── 设备状态读取
def foreground() -> str:
    """当前前台：com.pkg/.Activity（读唔到就空串）。"""
    out = sh("dumpsys activity activities | grep -m1 topResumedActivity")
    m = re.search(r"u0 ([\w\.]+)/([\w\.\$]+)", out)
    return f"{m.group(1)}/{m.group(2)}" if m else ""


def fg_package() -> str:
    return foreground().split("/")[0]


def screen_size() -> tuple[int, int]:
    out = sh("wm size")
    m = re.search(r"(\d+)x(\d+)", out)
    return (int(m.group(1)), int(m.group(2))) if m else (720, 1280)


# ─────────────────────────────── L0：确定性能力表
# app 别名 → 包名。粤语叫法尽量齐，搵唔到就 L0 miss（交 L1a/L2）。
APP_ALIASES = {
    "设置": "com.android.settings", "设定": "com.android.settings", "設定": "com.android.settings",
    "相机": "com.android.camera2", "相机app": "com.android.camera2", "影相": "com.android.camera2",
    "时钟": "com.android.deskclock", "闹钟": "com.android.deskclock", "钟": "com.android.deskclock",
    "联系人": "com.android.contacts", "通讯录": "com.android.contacts",
    "电话": "com.android.dialer", "拨号": "com.android.dialer",
    "文件": "com.android.documentsui", "档案": "com.android.documentsui", "文件管理": "com.android.documentsui",
    "图库": "com.android.gallery3d", "相册": "com.android.gallery3d", "相簿": "com.android.gallery3d",
    "浏览器": "org.chromium.webview_shell", "网页": "org.chromium.webview_shell",
    "日历": "com.android.calendar", "计算器": "com.android.calculator2",
    "短信": "com.android.messaging", "信息": "com.android.messaging",
    "设置页": "com.android.settings", "portal": "com.mobilerun.portal",
    "qphone": "com.mobilerun.portal", "助手": "com.mobilerun.portal",
}

# 常见第三方 app（包名固定，查有无装好准；唔喺表、又唔喺机上一律当「唔识」）
APP_EXTRA = {
    "印象笔记": "com.yinxiang", "evernote": "com.evernote",     # 国行包名系 com.yinxiang
    "应用宝": "com.tencent.android.qqdownloader", "腾讯应用宝": "com.tencent.android.qqdownloader",
    "qqdownload": "com.tencent.android.qqdownloader",
    "微信": "com.tencent.mm", "wechat": "com.tencent.mm",
    "企业微信": "com.tencent.wework", "qq": "com.tencent.mobileqq",
    "支付宝": "com.eg.android.AlipayGphone", "淘宝": "com.taobao.taobao",
    "闲鱼": "com.taobao.idlefish", "抖音": "com.ss.android.ugc.aweme",
    "快手": "com.smile.gifmaker", "小红书": "com.xingin.xhs",
    "微博": "com.sina.weibo", "哔哩哔哩": "tv.danmaku.bili", "b站": "tv.danmaku.bili",
    "网易云音乐": "com.netease.cloudmusic", "qq音乐": "com.tencent.qqmusic",
    "高德地图": "com.autonavi.minimap", "百度地图": "com.baidu.BaiduMap",
    "钉钉": "com.alibaba.android.rimet", "飞书": "com.ss.android.lark",
    "chrome": "com.android.chrome", "迅雷": "com.xunlei.downloadprovider",
    # 2026-10-27 补：老板实际讲过、之前查唔到嘅几个（「卸载掉360浏览器」走咗 L2 就系因为呢个缺）
    "360浏览器": "com.qihoo.browser", "360": "com.qihoo.browser", "奇虎360": "com.qihoo.browser",
    "百度网盘": "com.baidu.netdisk", "百度云盘": "com.baidu.netdisk", "百度云": "com.baidu.netdisk",
    "腾讯视频": "com.tencent.qqlive", "爱奇艺": "com.qiyi.video", "优酷": "com.youku.phone",
}
APP_ALIASES.update(APP_EXTRA)

_INSTALLED = {"at": 0.0, "pkgs": set()}


def third_party() -> list:
    """已装第三方 app（-3）。"""
    return sorted(l.split(":", 1)[-1].strip() for l in sh("pm list packages -3").splitlines() if ":" in l)


def installed(force: bool = False) -> set:
    """已装包名集合（缓存 60 s）。"""
    if not force and _INSTALLED["pkgs"] and time.time() - _INSTALLED["at"] < 60:
        return _INSTALLED["pkgs"]
    out = sh("pm list packages")
    pk = {l.split(":", 1)[-1].strip() for l in out.splitlines() if ":" in l}
    _INSTALLED.update(at=time.time(), pkgs=pk)
    return pk


NOISE = ("帮我", "同我", "麻烦你", "请", "唔该", "我想", "我要", "即刻", "一下", "下", "啦", "吖", "呀", "吓",
         "吧", "啊", "嘛", "咯", "囉")      # 句尾语气词：唔清走会令「卸载360浏览器吧」解析唔出 app 名


def clean(text: str) -> str:
    t = text.strip().strip("。！!？?，,")
    for w in NOISE:
        t = t.replace(w, "")
    return t.strip()


def resolve_package(word: str) -> str:
    """别名表 → 包名精确匹配 → 包名 token 模糊。搵唔到返空串。"""
    w = word.strip().lower().replace(" ", "")
    if not w:
        return ""
    for k in sorted(APP_ALIASES, key=len, reverse=True):
        if w == k or (w.startswith(k) and len(w) - len(k) <= 2):
            return APP_ALIASES[k]
    pkgs = [l.split(":", 1)[-1].strip() for l in sh("pm list packages -3").splitlines()]
    pkgs += [l.split(":", 1)[-1].strip() for l in sh("pm list packages").splitlines()]
    for p in pkgs:
        if w and w in p.lower():
            return p
    return ""


def set_brightness(level: int) -> bool:
    sh("settings put system screen_brightness_mode 0")
    sh(f"settings put system screen_brightness {max(0, min(255, level))}")
    got = sh("settings get system screen_brightness").strip()
    return got.isdigit() and int(got) == max(0, min(255, level))


def volume(op: str, arg: str = "") -> bool:
    if arg.isdigit():
        sh(f"cmd media_session volume --stream 3 --set {arg}")
    elif op in ("raise", "up", "大"):
        for _ in range(3):
            sh("cmd media_session volume --stream 3 --adj raise")
    elif op in ("lower", "down", "细", "小"):
        for _ in range(3):
            sh("cmd media_session volume --stream 3 --adj lower")
    elif op in ("mute", "静音"):
        sh("cmd media_session volume --stream 3 --set 0")
    return True


def keyevent(code: int) -> bool:
    return adb("shell", "input", "keyevent", str(code))[0] == 0


def tap(x: int, y: int) -> bool:
    return adb("shell", "input", "tap", str(x), str(y))[0] == 0


def swipe(x1, y1, x2, y2, ms=300) -> bool:
    return adb("shell", "input", "swipe", str(x1), str(y1), str(x2), str(y2), str(ms))[0] == 0


def open_app(pkg: str) -> tuple[bool, str, str]:
    """redroid 上 monkey 唔启得动（冇输出），要用 resolve-activity + am start -W。"""
    t0 = time.time()
    brief = [l.strip() for l in sh(f"cmd package resolve-activity --brief {pkg}").splitlines() if "/" in l]
    comp = brief[-1] if brief else ""
    if comp:
        adb("shell", "am", "start", "-W", "-n", comp, timeout=25)
    else:
        adb("shell", "monkey", "-p", pkg, "-c", "android.intent.category.LAUNCHER", "1", timeout=20)
    for _ in range(18):                      # 等前台转过去，最多 ~3.6s
        time.sleep(0.2)
        if fg_package() == pkg:
            return True, f"{int((time.time()-t0)*1000)}ms 到前台", f"am start -W -n {comp}"
    return False, f"前台仍系 {fg_package() or '未知'}", f"am start -W -n {comp}"


def l0_try(text: str) -> dict | None:
    """命中就返回 {layer,label,cmds,ok,why,ms}；唔命中返回 None。"""
    raw = text.strip()
    t = clean(raw)
    t0 = time.time()

    # 1b) 查有冇装某 app（L0 直接查包表，唔使开设置翻页）
    m = re.search(r"([^\s，。？！]{1,12})\s*(?:装咗未|装左未|装咗没|装了吗|装咗冇|有冇装|有无装|装咗未呀)", t)
    if m:                                                   # 「百度网盘装咗未」：app 名喺前面
        name = re.sub(r"^(?:帮我|同我|帮手|睇|看|查|问|试|打开|开)+", "", m.group(1).strip())
        name = re.sub(r"(app|应用|应用程式|程式)$", "", name, flags=re.I).strip()
        pkg = resolve_package(name)
        if pkg and pkg in installed():
            return trace("L0", f"查有冇装 {name}", [f"pm list packages {pkg}"], t0, True, f"有装：{pkg}")
        if pkg:
            return trace("L0", f"查有冇装 {name}", [f"pm list packages {pkg}"], t0, True, f"冇装：{pkg}")
        hit = [p for p in installed() if name.lower() and name.lower() in p.lower()]
        if hit:
            return trace("L0", f"查有冇装 {name}", ["pm list packages"], t0, True, f"有装：{hit[0]}")
        third = third_party()
        return trace("L0", f"查有冇装 {name}", ["pm list packages -3"], t0, True,
                     f"包表冇同名词条；机上第三方 app 有 {len(third)} 个：" + "、".join(third[:6]))

    m = re.search(r"(?:有无|有冇|有没有|是否)(?:装|安装|安装咗)?\s*([^\s，。？！]{1,12})", t, re.I)
    if m:
        name = re.sub(r"(app|应用|应用程式|程式)$", "", m.group(1).strip(), flags=re.I)
        pkg = resolve_package(name)
        if pkg and pkg in installed():
            return trace("L0", f"查有冇装 {name}", [f"pm list packages {pkg}"], t0, True, f"有装：{pkg}")
        if pkg:
            return trace("L0", f"查有冇装 {name}", [f"pm list packages {pkg}"], t0, True, f"冇装：{pkg}")
        hit = [p for p in installed() if name.lower() and name.lower() in p.lower()]
        if hit:
            return trace("L0", f"查有冇装 {name}", ["pm list packages"], t0, True, f"有装：{hit[0]}")
        third = third_party()
        return trace("L0", f"查有冇装 {name}", ["pm list packages -3"], t0, True,
                     f"包表冇同名词条；机上第三方 app 有 {len(third)} 个：" + "、".join(third[:6]))

    # 1) 打开 app
    m = re.match(r"^(?:打开|开|启动|开启|进入|去|用|睇|看|run|open|launch)\s*(?:个|一?下)?\s*(.+?)(?:app|应用|应用程式)?$", t, re.I)
    if m:
        name = m.group(1).strip()
        # 「去<app>做X」：先用最长别名前缀拆出 app 名 + 剩余意图
        pkg, rest = "", ""
        for k in sorted(APP_ALIASES, key=len, reverse=True):
            if name == k:
                pkg, rest = APP_ALIASES[k], ""
                break
            if name.startswith(k) and len(name) > len(k):
                pkg = APP_ALIASES[k]
                rest = name[len(k):].lstrip("下 ").strip()
                name = k
                break
        if not pkg:
            pkg = resolve_package(name)
        if pkg and pkg not in installed():
            tr = trace("L0", f"打开 {name}", [f"pm list packages {pkg}"], t0, False, f"未安装 {pkg}")
            tr["final"] = True          # 确定结论：唔需要再试 L1/L2
            return tr
        if pkg:
            ok, why, cmd = open_app(pkg)
            tr = trace("L0", f"打开 {name}", [cmd], t0, ok, why or pkg)
            if ok and rest and len(rest) >= 2:
                tr["continue"] = rest            # 先开 app，剩余意图交下一轮
            return tr

    # 1c) 下载 / 安装 X：已装就直答唔使再装（唔好白开应用宝）
    m = re.search(r"(?:下载|安装|装)\s*(?:一个|个)?\s*([^\s，。？！]{1,14})", t)
    if m:
        name = re.sub(r"(app|应用|应用程式|程式)$", "", m.group(1).strip(), flags=re.I)
        pkg = resolve_package(name)
        if pkg and pkg in installed():
            tr = trace("L0", f"{name} 已装", [f"pm list packages {pkg}"], t0, True,
                       f"已经装咗：{pkg}，唔使再下载")
            tr["final"] = True
            return tr

    # 2) 桌面 / 返回
    if re.search(r"(回|返|去).{0,3}(桌面|主页|首页|home)", t, re.I):
        ok = keyevent(3)
        return trace("L0", "回桌面", ["input keyevent 3"], t0, ok)
    if re.match(r"^(返回|后退|back|退返)$", t, re.I):
        ok = keyevent(4)
        return trace("L0", "返回", ["input keyevent 4"], t0, ok)

    # 3) 关 app / 清数据 / 卸载（「掉 / 咗」同句尾语气词都要食得落：「卸载掉360浏览器吧」）
    m = re.match(r"^(?:关掉|关闭|杀掉|force.?stop|结束)\s*(?:掉|咗|了)?\s*(.+)$", t) or re.match(r"^关\s*(.+)$", t)
    if m:
        pkg = resolve_package(m.group(1))
        if pkg:
            sh(f"am force-stop {pkg}")
            ok = fg_package() != pkg
            return trace("L0", f"关 {m.group(1)}", [f"am force-stop {pkg}"], t0, ok)
    m = re.match(r"^(?:清除?|清空)\s*(.+?)\s*(?:的)?数据$", t)
    if m:
        pkg = resolve_package(m.group(1))
        if pkg:
            out = sh(f"pm clear {pkg}")
            return trace("L0", f"清 {m.group(1)} 数据", [f"pm clear {pkg}"], t0, "Success" in out, out.strip()[:60])
    m = re.match(r"^(?:卸载|卸掉|删掉|删除|移除|uninstall)\s*(?:掉|咗|了|甩)?\s*(.+)$", t, re.I)
    if m:
        name = re.sub(r"(app|应用|应用程式|程式)$", "", m.group(1).strip(), flags=re.I).strip()
        if name in ("这个", "呢个", "它", "佢", "当前", "现在", "前面", "呢只", "这只"):
            return None                 # 指代唔明确：交 L1/L2 睇屏幕做
        pkg = resolve_package(name)
        if not pkg:
            tr = trace("L0", f"卸载 {name}", ["pm list packages -3"], t0, False,
                       f"包表冇「{name}」；机上第三方 app 有 {len(third_party())} 个：" + "、".join(third_party()[:6]))
            tr["final"] = True          # 卸载系确定性动作：查唔到就即刻报，唔好落 L2 点几十秒
            return tr
        if pkg not in installed(force=True):
            tr = trace("L0", f"卸载 {name}", [f"pm list packages {pkg}"], t0, True,
                       f"{pkg} 未安装，冇嘢可卸载")
            tr["final"] = True          # 确定结论，唔好白跑 L1/L2
            return tr
        out = sh(f"pm uninstall {pkg}")
        ok = "Success" in out
        if ok:
            _INSTALLED["pkgs"].discard(pkg)          # 清缓存，否则 60 秒内仍报「已装」
        return trace("L0", f"卸载 {name}", [f"pm uninstall {pkg}"], t0, ok, out.strip()[:60] or pkg)

    # 4) 音量 / 亮度
    m = re.match(r"^音量\s*(调大|大|加|调细|细|小|减|静音|\d+)$", t)
    if m:
        a = m.group(1)
        volume(a, a)
        return trace("L0", f"音量 {a}", ["cmd media_session volume"], t0, True)
    m = re.match(r"^(?:调|设|改|设成)?(?:屏幕)?亮度\s*(?:调到|调成|设成|改成|到)?\s*(\d{1,3})$", t)
    if m:
        ok = set_brightness(int(m.group(1)))
        return trace("L0", f"亮度 {m.group(1)}", ["settings put screen_brightness"], t0, ok)

    # 5) 开关 Wi-Fi / 蓝牙 / 飞行
    m = re.match(r"^(打开|开启|开|关闭|关掉|关)\s*(wifi|wi-fi|无线|蓝牙|飞行模式)$", t, re.I)
    if m:
        on = m.group(1) in ("打开", "开启", "开")
        what = m.group(2).lower()
        if what in ("wifi", "wi-fi", "无线"):
            sh(f"svc wifi {'enable' if on else 'disable'}")
            want = "enabled" if on else "disabled"
            got = sh("cmd wifi status").lower()
            ok = want in got or ("enable" in got) == on
            return trace("L0", f"{'开' if on else '关'} Wi-Fi", [f"svc wifi"], t0, ok, got.split("\n")[0][:60])
        if what == "蓝牙":
            sh(f"cmd bluetooth_manager {'enable' if on else 'disable'}")
            got = sh("settings get global bluetooth_on").strip()
            return trace("L0", f"{'开' if on else '关'} 蓝牙", ["cmd bluetooth_manager"], t0, got == ("1" if on else "0"), f"bluetooth_on={got}")
        sh(f"cmd connectivity airplane-mode {'enable' if on else 'disable'}")
        got = sh("settings get global airplane_mode_on").strip()
        return trace("L0", f"{'开' if on else '关'} 飞行模式", ["cmd connectivity airplane-mode"], t0, got == ("1" if on else "0"), f"airplane_mode_on={got}")

    # 6) 截图 / 录屏
    if re.search(r"(截图|截屏|screenshot)", t, re.I):
        p = f"/sdcard/qphone-{int(time.time())}.png"
        sh(f"screencap -p {p}")
        size = sh(f"ls -l {p}").split()
        ok = len(size) > 4
        return trace("L0", "截图", [f"screencap -p {p}"], t0, ok, p)

    # 7) 装包 / 列包 / 属性
    m = re.match(r"^(?:安装|装)\s*(\S+\.apk|\S+)$", t)
    if m:
        path = m.group(1)
        if path.endswith(".apk"):
            out = adb("shell", "pm", "install", "-r", path, timeout=90)[1]
            ok = "Success" in out
            return trace("L0", f"装 {path.split('/')[-1]}", [f"pm install -r {path}"], t0, ok, out.strip()[:80])
    if re.search(r"(列|睇|有咩|有什么).{0,2}(包|app|应用)", t):
        out = sh("pm list packages -3")
        return trace("L0", "列出已装应用", ["pm list packages -3"], t0, bool(out.strip()), f"{len(out.splitlines())} 个")
    m = re.match(r"^(?:属性|getprop)\s*(\S+)$", t)
    if m:
        out = sh(f"getprop {m.group(1)}").strip()
        return trace("L0", f"读属性 {m.group(1)}", [f"getprop {m.group(1)}"], t0, bool(out), out[:60])

    # 8) 开网址
    m = re.match(r"^(?:打开|开|去)\s*((?:https?://)\S+)$", t, re.I)
    if m:
        url = m.group(1)
        adb("shell", "am", "start", "-a", "android.intent.action.VIEW", "-d", url, timeout=25)
        ok = bool(fg_package())
        return trace("L0", f"开网址 {url[:40]}", ["am start -a VIEW"], t0, ok, fg_package())

    # 9) 点坐标 / 滑动 / 按键 / ASCII 输入
    m = re.match(r"^(?:点|点击|tap)\s*[（(]?\s*(\d{1,4})\s*[,，\s]\s*(\d{1,4})", t)
    if m:
        ok = tap(int(m.group(1)), int(m.group(2)))
        return trace("L0", f"点 ({m.group(1)}, {m.group(2)})", ["input tap"], t0, ok)
    m = re.match(r"^(?:滑|滑动|swipe)\s*[（(]?\s*(\d{1,4})\s*[,，\s]\s*(\d{1,4})\s*[)）,，\s]+\s*(\d{1,4})\s*[,，\s]\s*(\d{1,4})", t)
    if m:
        ok = swipe(*[int(x) for x in m.groups()])
        return trace("L0", "滑动", ["input swipe"], t0, ok)
    m = re.match(r"^(?:输入|打|type)\s+([\x20-\x7e]{1,120})$", t, re.I)
    if m:
        adb("shell", "input", "text", m.group(1).replace(" ", "%s"))
        return trace("L0", f"输入 {m.group(1)[:20]}", ["input text"], t0, True)

    # 10) 查前台
    if re.search(r"前台|(而家|现在|当前).{0,2}(系咩|什么|边个|哪个)|(系咩|什么).{0,2}(app|应用|界面)", t, re.I):
        fg = foreground()
        return trace("L0", "查前台", ["dumpsys activity activities"], t0, bool(fg), fg or "读唔到")

    return None


# ─────────────────────────────── L1a：无障碍结构（agent-device 快照 + 语义动作）
_AD_CACHE = {"nodes": [], "hash": "", "at": 0.0}


def snapshot(force: bool = False, ttl: float = 0.5) -> dict:
    """agent-device 快照（暖 0.12s）。同一次任务内短 TTL 缓存。"""
    if not force and _AD_CACHE["nodes"] and time.time() - _AD_CACHE["at"] < ttl:
        return {"nodes": _AD_CACHE["nodes"], "cached": True}
    try:
        cp = subprocess.run(["agent-device", "snapshot", "-i", "--serial", TARGET, "--json"],
                            capture_output=True, text=True, timeout=40)
        data = json.loads(cp.stdout or "{}")
        nodes = (data.get("data") or {}).get("nodes") or []
    except Exception:                                     # noqa: BLE001
        nodes = []
    if nodes:
        h = hashlib.md5(json.dumps(nodes, sort_keys=True, ensure_ascii=False).encode()).hexdigest()
        _AD_CACHE.update(nodes=nodes, hash=h, at=time.time())
    return {"nodes": nodes, "hash": _AD_CACHE["hash"], "cached": False}



# 设备 a11y 树系英文（AOSP 默认 en-US），用户讲中文 → 需要一层常见词映射。
# 只放「点得到」嘅高频项；命中唔到就 L1a miss，交 L2，唔硬猜。
I18N = {
    "网络和互联网": ("network", "internet"), "网络": ("network",), "互联网": ("internet",),
    "wifi": ("wi-fi", "wifi"), "蓝牙": ("bluetooth",), "已连接设备": ("connected devices",),
    "连接设备": ("connected devices",), "应用": ("apps",), "应用程式": ("apps",),
    "通知": ("notifications",), "电池": ("battery",), "存储": ("storage",), "储存": ("storage",),
    "声音": ("sound",), "显示": ("display",), "位置": ("location",), "安全": ("security",),
    "隐私": ("privacy",), "系统": ("system",), "关于手机": ("about phone",), "关于": ("about",),
    "语言和输入法": ("languages", "input"), "语言": ("languages",), "输入法": ("input",),
    "无障碍": ("accessibility",), "数字健康": ("digital wellbeing",), "用户": ("users",),
    "账号": ("accounts",), "账户": ("accounts",), "搜索": ("search",), "设置": ("settings",),
    "返回": ("back",), "主页": ("home",), "桌面": ("home",), "多任务": ("overview", "recent"),
    "确定": ("ok", "confirm", "yes"), "取消": ("cancel",), "保存": ("save",), "完成": ("done", "finish"),
    "下一步": ("next",), "继续": ("continue",), "允许": ("allow",), "拒绝": ("deny", "don't allow"),
    "相机": ("camera",), "时钟": ("clock",), "闹钟": ("alarm",), "图库": ("gallery", "photos"),
    "相册": ("gallery", "photos"), "联系人": ("contacts",), "电话": ("phone", "dialer"),
    "文件": ("files",), "日历": ("calendar",), "计算器": ("calculator",), "短信": ("messages", "sms"),
    "浏览器": ("browser",), "网页": ("browser", "webview"), "设置页": ("settings",),
    "列表": ("list",), "更多": ("more",), "菜单": ("menu",), "刷新": ("refresh", "reload"),
    "闹钟": ("alarm", "clock"), "时钟": ("clock",), "闹钟音量": ("alarm volume",),
    "免打扰": ("do not disturb",), "应用宝": ("app store", "appstore", "应用宝"),
    "分享": ("share",), "编辑": ("edit",), "删除": ("delete",), "新增": ("add", "new"),
}


def _norm(x: str) -> str:
    return re.sub(r"[\s_\-&/·\u00a0]+", " ", (x or "").lower()).strip()


def needle_variants(needle: str) -> list[str]:
    """把一个中文/英文目标扩成多个可比字符串（含内置 i18n 映射）。"""
    n = needle.strip().lower()
    out = [n]
    for zh, ens in I18N.items():
        if zh in n:
            out.extend(ens)
    return [x for x in dict.fromkeys(out) if x]


def _node_text(n: dict) -> str:
    return " ".join(str(n.get(k) or "") for k in ("label", "identifier", "value", "hint")).lower()


def find_node(nodes: list[dict], needle: str, editable: bool = False) -> dict | None:
    """语义命中：精确 → 包含 → 中文↔英文映射 → 去空格模糊。优先 hittable + 可见。"""
    if not needle.strip():
        return None
    cands = [n for n in nodes if n.get("hittable", True) and n.get("visibleToUser", True)
             and not n.get("systemBar")]
    if editable:
        cands = [n for n in cands if n.get("editable")] or cands
    variants = [v.lower() for v in needle_variants(needle)]
    texts = [(n, _norm(_node_text(n))) for n in cands]
    for v in variants:                                  # 先精确
        for n, t in texts:
            if t == _norm(v):
                return n
    for v in variants:                                  # 再包含
        for n, t in texts:
            if _norm(v) and _norm(v) in t:
                return n
    for v in variants:                                  # 最后去空格
        vv = _norm(v).replace(" ", "")
        for n, t in texts:
            if vv and vv in t.replace(" ", ""):
                return n
    return None


def node_center(n: dict) -> tuple[int, int] | None:
    r = n.get("rect") or {}
    if not r:
        return None
    return int(r["x"] + r["width"] / 2), int(r["y"] + r["height"] / 2)


# 「点『设置』」「按确定」「点击 相机」之类：带引号或动词 + 短名词
L1A_VERBS = r"(?:点击|点开|点|按下|按|选择|选|打开|进入|查找|找|搜|搜索|查看|睇|看|填|输入|tap|click)"


def l1a_try(text: str) -> dict | None:
    t = clean(text)
    m = re.search(L1A_VERBS + r"\s*[「『\"“']?([^」』\"”'\s，。]{1,12})[」』\"”']?", t, re.I)
    if not m:
        return None
    target = m.group(1).strip()
    if not target or re.fullmatch(r"\d+", target):
        return None
    t0 = time.time()
    snap = snapshot()
    if not snap["nodes"]:
        return trace("L1a", f"找「{target}」", ["agent-device snapshot"], t0, False, "快照为空")
    node = find_node(snap["nodes"], target)
    if not node:
        return {"layer": "L1a", "label": f"找「{target}」", "cmds": ["agent-device snapshot"],
                "ms": now_ms() - int(t0 * 1000), "ok": False, "why": "无障碍树冇呢个控件", "miss": True}
    c = node_center(node)
    if not c:
        return trace("L1a", f"找「{target}」", ["snapshot"], t0, False, "控件冇 bounds", )
    before_fg, before_hash = fg_package(), snap["hash"]
    tap(*c)
    time.sleep(0.45)
    after = snapshot(force=True)
    ok = fg_package() != before_fg or after["hash"] != before_hash
    why = f"{node.get('label') or node.get('identifier') or node.get('type')} @({c[0]},{c[1]})"
    return trace("L1a", f"点「{target}」", ["agent-device snapshot", "input tap"], t0, ok, why)


def l1a_snapshot_lines(limit: int = 8) -> list[str]:
    """畀壳显示「无障碍树睇到咩」（调试用）。"""
    nodes = snapshot(force=True)["nodes"]
    out = []
    for n in nodes[:limit]:
        lab = n.get("label") or n.get("identifier") or n.get("type")
        if lab:
            out.append(str(lab)[:28])
    return out


# ─────────────────────────────── L1b：WebView CDP
# 只有 App 自己开咗 setWebContentsDebuggingEnabled 先有 `@webview_devtools_remote_*` socket。
# 实测本机 redroid：系统 WebView shell 出 socket，Portal 冇 → 冇 socket 就 L1b miss，交 L2。
def webview_sockets() -> list[str]:
    out = sh("cat /proc/net/unix")
    return sorted(set(re.findall(r"@(webview_devtools_remote_\d+)", out)))


def _free_port() -> int:
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    p = s.getsockname()[1]
    s.close()
    return p


class _WS:
    """够用嘅 WebSocket 客户端（CDP 只需要文本帧）。"""

    def __init__(self, host: str, port: int, path: str, timeout: float = 8.0):
        self.s = socket.create_connection((host, port), timeout=timeout)
        key = base64.b64encode(os.urandom(16)).decode()
        req = (f"GET {path} HTTP/1.1\r\nHost: {host}:{port}\r\nUpgrade: websocket\r\n"
               f"Connection: Upgrade\r\nSec-WebSocket-Key: {key}\r\nSec-WebSocket-Version: 13\r\n\r\n")
        self.s.sendall(req.encode())
        buf = b""
        while b"\r\n\r\n" not in buf:
            chunk = self.s.recv(4096)
            if not chunk:
                raise RuntimeError("CDP 握手失败")
            buf += chunk
        if b"101" not in buf.split(b"\r\n")[0]:
            raise RuntimeError("CDP 拒绝升级：" + buf.split(b"\r\n")[0].decode("latin1"))

    def send(self, text: str) -> None:
        data = text.encode()
        hdr = bytearray([0x81])
        n = len(data)
        if n < 126:
            hdr.append(0x80 | n)
        elif n < 65536:
            hdr.append(0x80 | 126); hdr += struct.pack(">H", n)
        else:
            hdr.append(0x80 | 127); hdr += struct.pack(">Q", n)
        mask = os.urandom(4)
        hdr += mask
        self.s.sendall(bytes(hdr) + bytes(b ^ mask[i % 4] for i, b in enumerate(data)))

    def _read(self, n: int) -> bytes:
        buf = b""
        while len(buf) < n:
            chunk = self.s.recv(n - len(buf))
            if not chunk:
                raise RuntimeError("CDP 连接断咗")
            buf += chunk
        return buf

    def recv(self) -> str:
        while True:
            b1, b2 = self._read(2)
            op = b1 & 0x0F
            ln = b2 & 0x7F
            if ln == 126:
                ln = struct.unpack(">H", self._read(2))[0]
            elif ln == 127:
                ln = struct.unpack(">Q", self._read(8))[0]
            payload = self._read(ln)
            if op == 1:
                return payload.decode("utf-8", "replace")
            if op == 8:
                raise RuntimeError("CDP 关闭")

    def close(self) -> None:
        try:
            self.s.close()
        except Exception:  # noqa: BLE001
            pass


_CDP = {"port": 0, "sock": "", "ws": None, "tid": ""}


def fg_webview_sockets() -> list[str]:
    """只留前台 app 嘅 WebView socket（socket 名尾巴就系 app 嘅 pid）。"""
    socks = webview_sockets()
    if not socks:
        return []
    pkg = fg_package()
    try:
        pids = sh(f"pidof {pkg}").split() if pkg else []
    except Exception:                                     # noqa: BLE001
        pids = []
    mine = [s for s in socks if s.rsplit("_", 1)[-1] in pids]
    return mine or socks


def _webview_forwards() -> list[tuple[str, int]]:
    """现有 webview 相关 adb forward 列表：[(socket 名, 本地端口)]。

    注意同一条 socket 可能有多条 forward（旧版本积落嘅），所以返 list 唔系 dict。
    """
    raw = adb("forward", "--list")[1] or ""
    out = raw.decode("utf-8", "replace") if isinstance(raw, bytes) else raw
    rows: list[tuple[str, int]] = []
    for line in out.splitlines():
        parts = line.split()
        if len(parts) < 3 or "webview_devtools_remote" not in parts[2]:
            continue
        try:
            rows.append((parts[2].split(":", 1)[1], int(parts[1].split(":")[1])))
        except (IndexError, ValueError):
            continue
    return rows


def cleanup_webview_forwards(keep_sock: str = "", keep_port: int = 0) -> int:
    """每个 socket 只留一条 forward（keep_sock 保留 keep_port），其余全删。"""
    rows = _webview_forwards()
    live = set(webview_sockets())                 # socket 冇咗（app 死咗）就连 forward 都唔留
    keepmap: dict[str, int] = {}
    for sock, port in rows:
        if sock in keepmap:
            continue
        keepmap[sock] = keep_port if (sock == keep_sock and keep_port) else port
    n = 0
    for sock, port in rows:
        if port != keepmap.get(sock) or (live and sock not in live):
            adb("forward", "--remove", f"tcp:{port}")
            n += 1
    return n


def cdp_targets() -> list[dict]:
    """起／复用 adb forward + 拉 /json 目标表。冇 WebView 就返空。

    坑：socket 名带 app pid，app 一重启就变；旧版每次都新建 forward 唔删，
    实测积到 11 条（同一 socket 都开几条）→ 现在「一个 socket 一条」+ 换 socket 就清旧。
    """
    socks = fg_webview_sockets()
    if not socks:
        cleanup_webview_forwards()
        _CDP.update(port=0, sock="")
        return []
    sock = socks[0]
    rows = [r for r in _webview_forwards() if r[0] == sock]
    cleanup_webview_forwards(keep_sock=sock, keep_port=rows[0][1] if rows else 0)
    port = rows[0][1] if rows else _free_port()
    if not rows:
        adb("forward", f"tcp:{port}", f"localabstract:{sock}")
    _CDP.update(port=port, sock=sock, ws=None)
    try:
        with urllib.request.urlopen(f"http://127.0.0.1:{port}/json", timeout=5) as r:
            return json.loads(r.read().decode() or "[]")
    except Exception:                                     # noqa: BLE001
        adb("forward", "--remove", f"tcp:{port}")
        _CDP.update(port=0, sock="")
        return []


def cdp_eval(expr: str, timeout: float = 8.0) -> dict:
    """喺 WebView 主 frame 跑一段 JS，返 {ok, value|error}。"""
    targets = [t for t in cdp_targets() if t.get("type") == "page" and not t.get("url", "").startswith("devtools")]
    if not targets:
        return {"ok": False, "error": "冇 WebView 页面目标"}
    url = targets[0].get("webSocketDebuggerUrl", "")
    path = "/" + url.split("/", 3)[-1] if "://" in url else url
    ws = _WS("127.0.0.1", _CDP["port"], path, timeout=timeout)
    try:
        ws.send(json.dumps({"id": 1, "method": "Runtime.evaluate",
                            "params": {"expression": expr, "returnByValue": True, "awaitPromise": True}}))
        deadline = time.time() + timeout
        while time.time() < deadline:
            msg = json.loads(ws.recv())
            if msg.get("id") == 1:
                res = msg.get("result", {})
                if "exceptionDetails" in res:
                    return {"ok": False, "error": str(res["exceptionDetails"].get("text"))[:160]}
                return {"ok": True, "value": (res.get("result") or {}).get("value")}
        return {"ok": False, "error": "CDP 超时"}
    finally:
        ws.close()


CLICK_JS = """(() => {
  const want = %s;
  const els = Array.from(document.querySelectorAll(
    'a,button,input,textarea,select,[role=button],[role=link],[contenteditable=true],label,li,span,div'));
  const txt = e => ((e.innerText || e.value || e.getAttribute('aria-label') || '') + '').trim().toLowerCase();
  let el = els.find(e => txt(e) === want) || els.find(e => txt(e).includes(want));
  if(!el) return {ok:false, reason:'页面冇呢个文字'};
  el.scrollIntoView({block:'center'});
  el.click();
  return {ok:true, tag:el.tagName, text:txt(el).slice(0,40)};
})()"""

FILL_JS = """(() => {
  const want = %s, val = %s;
  const els = Array.from(document.querySelectorAll('input,textarea,[contenteditable=true]'));
  const el = els.find(e => ((e.getAttribute('aria-label')||e.placeholder||e.name||'') + '').toLowerCase().includes(want))
          || els[0];
  if(!el) return {ok:false, reason:'冇输入框'};
  el.focus();
  if('value' in el){ el.value = val; } else { el.textContent = val; }
  el.dispatchEvent(new Event('input', {bubbles:true}));
  el.dispatchEvent(new Event('change', {bubbles:true}));
  return {ok:true, field:(el.getAttribute('aria-label')||el.placeholder||el.name||el.tagName)};
})()"""


def l1b_try(text: str) -> dict | None:
    """只在「有 WebView 调试 socket」+ 句子里出现网页动作时尝试。"""
    t = clean(text)
    if not webview_sockets():
        return None
    m = re.search(L1A_VERBS + r"\s*[「『\"“']?([^」』\"”'\s，。]{1,12})[」』\"”']?", t, re.I)
    t0 = time.time()
    if m:
        target = m.group(1)
        r = cdp_eval(CLICK_JS % json.dumps(target.lower(), ensure_ascii=False))
        val = r.get("value") or {}
        ok = bool(r.get("ok") and isinstance(val, dict) and val.get("ok"))
        bits = [str(val.get("tag") or ""), str(val.get("text") or "")] if isinstance(val, dict) else []
        why = (" ".join(x for x in bits if x).strip()
               or (val.get("reason") if isinstance(val, dict) else None)
               or r.get("error") or "页面搵唔到呢个文字")
        return trace("L1b", f"网页点「{target}」", ["CDP Runtime.evaluate click"], t0, ok, why[:80])
    m = re.search(r"(?:输入|填写|打)\s*[「『\"“']?([^」』\"”']{1,60})[」』\"”']?\s*(?:落|入|去|到)", t)
    if m:
        val = m.group(1)
        r = cdp_eval(FILL_JS % ('""', json.dumps(val, ensure_ascii=False)))
        ok = bool(r.get("ok") and (r.get("value") or {}).get("ok"))
        return trace("L1b", f"网页填「{val[:12]}」", ["CDP Runtime.evaluate fill"], t0, ok,
                     ((r.get("value") or {}).get("field") or r.get("error", ""))[:60])
    return None


# ─────────────────────────────── 路由器：L0 → L1a → L1b（→ 交 L2）
LAST_ATTEMPTS: list[dict] = []


def route(text: str, emit=None) -> dict | None:
    """逐层试；成功就返该层 trace，全部唔得返 None（调用方转 L2）。

    特殊：L0 若只做到「打开 <app>」而句子仲有剩余意图（例如「去应用宝下载快手」），
    会带 continue 字段再跑一轮，令后面嘅 L1a/L1b/L2 从已经开好嘅界面上继续。
    """
    chain = [("L0", l0_try), ("L1a", l1a_try), ("L1b", l1b_try)]
    LAST_ATTEMPTS.clear()
    note = ""
    for _ in range(2):                                  # 最多两轮（前缀 + 实质）
        tr = _route_once(text, chain, emit)
        if tr and tr.get("continue"):
            note = (note + "；" if note else "") + tr["label"]
            text = tr["continue"]
            if not text:
                return tr
            continue
        if tr and note:
            tr["note"] = note
        return tr
    return None


def _route_once(text: str, chain, emit):
    def fire(tr: dict) -> None:
        if emit:
            try:
                emit("layer", {"layer": tr["layer"], "label": tr["label"],
                               "cmds": tr["cmds"], "ms": tr["ms"],
                               "ok": tr["ok"], "why": tr.get("why", "")})
            except Exception:                              # noqa: BLE001
                pass

    last = None
    for name, fn in chain:
        t0 = time.time()
        try:
            tr = fn(text)
        except Exception as e:                             # noqa: BLE001
            tr = {"layer": name, "label": "异常", "cmds": [], "ms": now_ms() - int(t0 * 1000),
                  "ok": False, "why": str(e)[:120]}
        if tr is None:
            LAST_ATTEMPTS.append({"layer": name, "ok": False,
                                  "why": {"L0": "能力表冇呢句",
                                          "L1a": "冇「动作+目标」可对号入座",
                                          "L1b": "冇 WebView 调试 socket / 页面冇呢个文字"}.get(name, "未命中")})
            if emit:
                try:
                    emit("layer", {"layer": name, "label": "未命中", "cmds": [], "ms": 0,
                                   "ok": False, "skip": True, "why": "能力表冇呢句"})
                except Exception:                          # noqa: BLE001
                    pass
            continue
        fire(tr)
        LAST_ATTEMPTS.append({"layer": name, "ok": bool(tr.get("ok")), "why": tr.get("why", "")})
        if tr.get("continue"):
            return tr
        if tr.get("ok") or tr.get("final"):
            return tr
        last = tr
    # 三层规则全 miss → S 层语义解析（规则命中嘅单唔会行到呢度，一毫秒都唔多付）
    if SEM_ENABLED:
        try:
            trs = sem_try(text)
        except Exception as e:                               # noqa: BLE001
            trs = None
            SEM_LAST.update(why=f"语义层异常：{str(e)[:80]}")
        if trs:
            fire(trs)
            LAST_ATTEMPTS.append({"layer": trs.get("layer", "S"), "ok": bool(trs.get("ok")),
                                  "why": "语义：" + (trs.get("why") or "")})
            return trs
        LAST_ATTEMPTS.append({"layer": "S", "ok": False,
                              "why": SEM_LAST.get("why") or "语义层冇派到执行器（交 L2）"})
    return last if last and last.get("ok") else None


# ─────────────────────────────── S 层：语义解析（四层规则全 miss 才触发）
# 定位：规则先跑，一毫秒都唔多付；只有规则食唔落嘅讲法，才问一次模型，拿「意图 + 槽位」，
#      再派去 L0 / L1a / L1b 执行。模型只讲「边个、做乜」，命令一律本地模板拼（唔准出 shell）。
SEM_MODEL = os.environ.get("QPHONE_SEM_MODEL", "traework/DeepSeek-V4-Pro-Official")
SEM_FALLBACK = os.environ.get("QPHONE_SEM_FALLBACK", "traework/DeepSeek-V4-Flash-Official")
SEM_TIMEOUT = float(os.environ.get("QPHONE_SEM_TIMEOUT", "8"))
SEM_FALLBACK_TIMEOUT = float(os.environ.get("QPHONE_SEM_FALLBACK_TIMEOUT", "4"))
SEM_MIN_CONF = float(os.environ.get("QPHONE_SEM_MIN_CONF", "0.6"))
SEM_CACHE_TTL = float(os.environ.get("QPHONE_SEM_CACHE_TTL", "86400"))
SEM_ENABLED = os.environ.get("QPHONE_SEM", "1") not in ("0", "false", "False")
SEM_URL = (os.environ.get("QPHONE_LLM_URL") or "http://127.0.0.1:7863/v1").rstrip("/")
SEM_KEY = os.environ.get("QPHONE_LLM_KEY", "local")
SEM_DIR = os.path.expanduser("~/.qphone")
SEM_CACHE = os.path.join(SEM_DIR, "sem-cache.json")
SEM_LEARN = os.path.join(SEM_DIR, "alias-learned.json")

SEM_INTENTS = ("open_app", "check_installed", "uninstall", "force_stop", "clear_data",
               "back", "home", "screenshot", "tap", "input", "search", "scroll", "unknown")

SEM_LAST: dict = {}
SEM_STATS = {"cache_hit": 0, "llm": 0, "timeout": 0, "bad_json": 0, "out_of_list": 0, "low_conf": 0, "learned": 0}

_SEM_SYS = (
    "你系手机命令解析器。用户讲一句粤语/普通话，你只输出一行 JSON，唔准解释、唔准加代码块：\n"
    '{"intent":"open_app|check_installed|uninstall|force_stop|clear_data|back|home|screenshot|tap|input|search|scroll|unknown",'
    '"app":"","target":"","text":"","confidence":0.0}\n'
    "规则：app 填中文应用名（原话点讲就点填，唔好翻译）；tap 填要点的控件文字；"
    "input/search 填要打入去的文字；讲唔清就 intent=unknown、confidence 低。"
)


def _sem_http(text: str, model: str, timeout: float) -> str:
    """一次 OpenAI 兼容调用（绕开环境代理），返模型原文。"""
    payload = json.dumps({"model": model,
                          "messages": [{"role": "system", "content": _SEM_SYS},
                                       {"role": "user", "content": text}],
                          "max_tokens": 120, "temperature": 0}).encode()
    req = urllib.request.Request(SEM_URL + "/chat/completions", data=payload,
                                 headers={"Content-Type": "application/json",
                                          "Authorization": "Bearer " + (SEM_KEY or "local")})
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    with opener.open(req, timeout=timeout) as r:
        d = json.loads(r.read())
    return (d.get("choices") or [{}])[0].get("message", {}).get("content") or ""


def _sem_cache_get(text: str):
    try:
        if not os.path.isfile(SEM_CACHE):
            return None
        d = json.load(open(SEM_CACHE, encoding="utf-8"))
        hit = d.get(text)
        if hit and time.time() - float(hit.get("ts") or 0) < SEM_CACHE_TTL:
            return hit.get("parsed")
    except Exception:                                        # noqa: BLE001
        return None
    return None


def _sem_cache_put(text: str, parsed: dict) -> None:
    try:
        os.makedirs(SEM_DIR, exist_ok=True)
        d = {}
        if os.path.isfile(SEM_CACHE):
            d = json.load(open(SEM_CACHE, encoding="utf-8"))
        d[text] = {"ts": time.time(), "parsed": parsed}
        if len(d) > 200:                                     # 只留最近 200 句
            for k in sorted(d, key=lambda x: d[x].get("ts", 0))[:-200]:
                d.pop(k, None)
        json.dump(d, open(SEM_CACHE, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    except Exception:                                        # noqa: BLE001
        pass


def _learn_alias(name: str, pkg: str) -> None:
    """语义命中并执行成功 → 记住「中文名 → 包名」，下次规则直接命中，0 模型。"""
    name, pkg = (name or "").strip(), (pkg or "").strip()
    if not name or not pkg:
        return
    try:
        os.makedirs(SEM_DIR, exist_ok=True)
        d = {}
        if os.path.isfile(SEM_LEARN):
            d = json.load(open(SEM_LEARN, encoding="utf-8"))
        if d.get(name) == pkg:
            return
        d[name] = pkg
        json.dump(d, open(SEM_LEARN, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
        APP_ALIASES[name] = pkg                              # 即时生效
        SEM_STATS["learned"] += 1
    except Exception:                                        # noqa: BLE001
        pass


def _load_learned() -> None:
    try:
        if os.path.isfile(SEM_LEARN):
            for k, v in (json.load(open(SEM_LEARN, encoding="utf-8")) or {}).items():
                if isinstance(k, str) and isinstance(v, str) and v:
                    APP_ALIASES.setdefault(k, v)
    except Exception:                                        # noqa: BLE001
        pass


def sem_parse(text: str):
    """规则解析：主模型超时就降 Flash 补一刀；坏 JSON / 低置信 一律唔赌。"""
    hit = _sem_cache_get(text)
    if hit:
        SEM_STATS["cache_hit"] += 1
        SEM_LAST.update(intent=hit.get("intent"), app=hit.get("app"), target=hit.get("target"),
                        confidence=hit.get("confidence"), model="cache", ms=0, why="命中句级缓存")
        return hit
    for model, to in ((SEM_MODEL, SEM_TIMEOUT), (SEM_FALLBACK, SEM_FALLBACK_TIMEOUT)):
        if not model:
            continue
        t0 = time.time()
        try:
            raw = _sem_http(text, model, to)
        except Exception as e:                               # noqa: BLE001
            SEM_STATS["timeout"] += 1
            SEM_LAST.update(model=model, ms=now_ms() - int(t0 * 1000), why=f"{model} 调唔通：{str(e)[:60]}")
            continue
        ms = now_ms() - int(t0 * 1000)
        m = re.search(r"\{.*\}", raw, re.S)
        if not m:
            SEM_STATS["bad_json"] += 1
            SEM_LAST.update(model=model, ms=ms, why=f"{model} 冇吐 JSON")
            continue
        try:
            p = json.loads(m.group(0))
        except json.JSONDecodeError:
            SEM_STATS["bad_json"] += 1
            SEM_LAST.update(model=model, ms=ms, why=f"{model} JSON 解唔到")
            continue
        intent = str(p.get("intent") or "").strip().lower()
        try:
            conf = float(p.get("confidence") or 0)
        except (TypeError, ValueError):
            conf = 0.0
        parsed = {"intent": intent, "app": str(p.get("app") or "").strip(),
                  "target": str(p.get("target") or "").strip(), "text": str(p.get("text") or "").strip(),
                  "confidence": conf}
        SEM_STATS["llm"] += 1
        if intent not in SEM_INTENTS:
            SEM_STATS["out_of_list"] += 1
            SEM_LAST.update(model=model, ms=ms, why=f"intent「{intent}」唔在白名单", **parsed)
            continue
        if conf < SEM_MIN_CONF:
            SEM_STATS["low_conf"] += 1
            SEM_LAST.update(model=model, ms=ms, why=f"置信 {conf:.2f} < {SEM_MIN_CONF}", **parsed)
            continue
        SEM_LAST.update(model=model, ms=ms, why="解析成功", **parsed)
        _sem_cache_put(text, parsed)
        return parsed
    return None


_SEM_TEXT = {"open_app": "打开{app}", "check_installed": "有无装{app}", "uninstall": "卸载{app}",
             "force_stop": "关掉{app}", "clear_data": "清{app}数据", "back": "返回",
             "home": "回桌面", "screenshot": "截图"}


def sem_execute(p: dict):
    """把语义结果派去 L0 / L1a / L1b 嘅既有执行器；派唔到就返 None（交 L2）。"""
    intent = (p or {}).get("intent") or ""
    app, target, txt = p.get("app") or "", p.get("target") or "", p.get("text") or ""
    t0 = time.time()

    if intent in _SEM_TEXT:
        phrase = _SEM_TEXT[intent].format(app=app)
        tr = l0_try(phrase)
        if tr and tr.get("ok") and app:
            _learn_alias(app, resolve_package(app))
        if tr:
            tr["semantic"] = {"intent": intent, "app": app, "confidence": p.get("confidence"),
                              "from": phrase}
            return tr
        return None

    if intent == "tap" and target:
        tries = []
        if webview_sockets():
            t1 = l1b_try("点" + target)                       # 网页优先（41–56 ms）
            if t1 and t1.get("ok"):
                t1["semantic"] = {"intent": intent, "target": target, "confidence": p.get("confidence")}
                return t1
            if t1:
                tries.append(t1)
        t2 = l1a_try("点" + target)                          # 原生控件（无障碍树）
        if t2 and t2.get("ok"):
            t2["semantic"] = {"intent": intent, "target": target, "confidence": p.get("confidence")}
            return t2
        if t2:
            tries.append(t2)
        best = tries[-1] if tries else None                   # 两层都唔得：返最后一个 miss（why 最具体）
        if best:
            best["semantic"] = {"intent": intent, "target": target, "confidence": p.get("confidence")}
        return best

    if intent in ("input", "search"):
        if not txt:
            SEM_LAST["why"] = "input/search 冇 text"
            return None
        tr = l1b_try(f"输入「{txt}」落") if webview_sockets() else None
        if tr:
            tr["semantic"] = {"intent": intent, "text": txt, "confidence": p.get("confidence")}
            return tr
        SEM_LAST["why"] = f"{intent} 要 WebView socket / IME（本版未做 IME）"
        return None

    SEM_LAST["why"] = f"intent「{intent}」本版冇执行器（交 L2）"
    return None


def sem_try(text: str):
    """S 层入口：解析 + 执行；返 trace（带 semantic 字段 + 实际执行层），派唔到返 None。"""
    if not SEM_ENABLED:
        return None
    p = sem_parse(text)
    if not p:
        return None
    tr = sem_execute(p)
    if not tr:
        return None
    tr["label"] = f"语义→{tr.get('layer')} · {tr.get('label')}"
    tr["why"] = (tr.get("why") or "") + f"｜语义 {p.get('intent')} {p.get('app') or p.get('target') or p.get('text') or ''}".strip()
    return tr


def semantic_status() -> dict:
    return {"enabled": SEM_ENABLED, "model": SEM_MODEL, "fallback": SEM_FALLBACK,
            "timeout_s": SEM_TIMEOUT, "fallback_timeout_s": SEM_FALLBACK_TIMEOUT,
            "min_conf": SEM_MIN_CONF, "cache_ttl_s": SEM_CACHE_TTL,
            "learned_aliases": _learned_count(),
            "stats": dict(SEM_STATS), "last": dict(SEM_LAST)}


def _learned_count() -> int:
    try:
        return len(json.load(open(SEM_LEARN, encoding="utf-8")) or {})
    except Exception:                                        # noqa: BLE001
        return 0


_load_learned()
