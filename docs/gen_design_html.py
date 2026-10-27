#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""把 docs/详细设计.md 渲染成 docs/详细设计.html（预览窗口睇用）。

用法：python3 docs/gen_design_html.py
依赖：markdown（`python3 -m pip install --user markdown`）
改完 md 之后跑一次即可；HTML 唔手改。

主题：**ShonnQ Corp 默认视觉主题**（科技简约 · 暗底星空 · 薄荷青 #2ee6c9）
     正本 CSS：~/目标/事业/设计稿/shonnq-theme.css —— 公司所有文档共用，只喺嗰边改。
"""

import re
from pathlib import Path

import markdown

DOCS = Path(__file__).resolve().parent
SRC = DOCS / "详细设计.md"
OUT = DOCS / "详细设计.html"


def _star_uri(n: int = 220, size: int = 800) -> str:
    """同公司全景同一套星场参数：800×800 tile、220 粒、r .5–1.5、opacity .2–.95。"""
    import random
    import urllib.parse
    rnd = random.Random(20261023)
    radii = [0.5, 0.5, 0.6, 0.7, 0.9, 1.1, 1.5]
    dots = []
    for _ in range(n):
        dots.append(
            f'<circle cx="{rnd.uniform(0, size):.1f}" cy="{rnd.uniform(0, size):.1f}" '
            f'r="{rnd.choice(radii)}" fill="#fff" opacity="{rnd.uniform(.2, .95):.2f}"/>'
        )
    svg = (f'<svg xmlns="http://www.w3.org/2000/svg" width="{size}" height="{size}">'
           + "".join(dots) + "</svg>")
    return "data:image/svg+xml," + urllib.parse.quote(svg, safe="")

THEME_COMPANY = Path.home() / "目标" / "事业" / "设计稿" / "shonnq-theme.css"   # 公司正本
THEME_VENDOR = DOCS / "theme" / "shonnq-theme.css"                        # 仓库副本（正本唔在时用）

FALLBACK_CSS = """
:root{color-scheme:dark;--bg:#05070a;--fg:#e8eef4;--soft:#8695a3;--line2:rgba(255,255,255,.055);
  --acc:#2ee6c9;--acc-dim:rgba(46,230,201,.16);--code:#0a1015}
body{margin:0;color:var(--fg);background:var(--bg);padding:0 44px 96px;
  font:15px/1.78 -apple-system,"PingFang SC",system-ui,sans-serif}
.wrap{max-width:1000px;margin:0 auto}
h1{font-size:30px;text-align:center;margin:44px 0 0}
"""  # 主题文件唔在时嘅兜底，保证唔会变返白底黑字


def load_theme() -> str:
    """读公司默认主题（正本 shonnq-theme.css），@STARS@ 换成星场 data-URI。"""
    for cand in (THEME_COMPANY, THEME_VENDOR):
        if cand.is_file():
            css = cand.read_text(encoding="utf-8")
            break
    else:
        css = FALLBACK_CSS
        print("⚠️  未找到主题文件（公司正本 / 仓库副本），用兜底 CSS")
    return css.replace("@STARS@", _star_uri())


def _hero(body: str) -> str:
    """首个 H1 + 其后 blockquote（版本/日期/依据）→ 居中头部。"""
    m = re.match(r"\s*<h1>(.*?)</h1>\s*(?:<blockquote>(.*?)</blockquote>)?", body, re.S)
    if not m:
        return body
    title, quote = m.group(1), m.group(2) or ""
    chips = []
    for p in re.findall(r"<p>(.*?)</p>", quote, re.S):
        txt = p.strip()
        if txt:
            chips.append(f"<span>{txt}</span>")
    meta = "\n  ".join(chips) if chips else ""
    hero = (
        '<header class="hero">\n'
        '  <div class="kicker">ShonnQ Corp · 详细设计</div>\n'
        f"  <h1>{title}</h1>\n"
        + (f'  <div class="docmeta">{meta}</div>\n' if meta else "")
        + "</header>\n"
    )
    return hero + body[m.end():]


def main() -> int:
    src = SRC.read_text(encoding="utf-8")
    ver = re.search(r"^> 版本：(\S+)", src, re.M)
    ver = ver.group(1) if ver else "—"
    body = markdown.markdown(
        src,
        extensions=["tables", "fenced_code", "sane_lists", "attr_list", "nl2br"],
        output_format="html5",
    )
    # 日期唔好断行（变更历史表最后一列）
    body = re.sub(r"(?<=>)20\d\d-\d\d-\d\d(?=<)", r'<span class="nowrap">\g<0></span>', body)
    body = _hero(body)

    html = f"""<!doctype html>
<html lang="zh-Hant">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>QPhone Bot 详细设计 {ver}</title>
<style>{load_theme()}</style>
</head>
<body>
<div class="cosmos" aria-hidden="true"><div class="sun"></div><div class="beam"></div><div class="beam-core"></div></div>
<div class="wrap">
{body}
</div></body></html>
"""
    OUT.write_text(html, encoding="utf-8")
    print(f"写出 {OUT}  {len(html)} B  版本 {ver}  主题 {THEME_COMPANY.name}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
