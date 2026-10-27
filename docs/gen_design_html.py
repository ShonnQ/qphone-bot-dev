#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""把 docs/详细设计.md 渲染成 docs/详细设计.html（预览窗口睇用）。

用法：python3 docs/gen_design_html.py
依赖：markdown（`python3 -m pip install --user markdown`）
改完 md 之后跑一次即可；HTML 唔手改。

主题：ShonnQ Corp 公司全景同源（科技简约 · 暗底 + 薄荷青 #2ee6c9 + PingFang SC）。
"""

import re
from pathlib import Path

import markdown

DOCS = Path(__file__).resolve().parent
SRC = DOCS / "详细设计.md"
OUT = DOCS / "详细设计.html"

CSS = """
:root{
  color-scheme:dark;
  --bg:#05070a; --bg2:#070a0d; --surface:rgba(255,255,255,.024);
  --fg:#e8eef4; --soft:#8695a3; --dim:#5f707f;
  --line:rgba(255,255,255,.09); --line2:rgba(255,255,255,.055);
  --acc:#2ee6c9; --acc-dim:rgba(46,230,201,.16); --acc-soft:rgba(46,230,201,.06);
  --code:#0a1015; --ok:#3ddc97;
}
*{box-sizing:border-box}
html{-webkit-text-size-adjust:100%;scroll-behavior:smooth}
body{
  margin:0;color:var(--fg);
  font:15px/1.78 -apple-system,"PingFang SC","Helvetica Neue","Hiragino Sans GB",system-ui,sans-serif;
  padding:0 44px 96px;
  background-color:var(--bg);
  background-image:
    radial-gradient(980px 520px at 12% -14%, rgba(46,230,201,.10), transparent 62%),
    radial-gradient(900px 480px at 92% -10%, rgba(58,120,255,.09), transparent 60%),
    linear-gradient(#06090d,#05070a 45%,#04060a);
  background-attachment:fixed;
}
/* 极淡 HUD 网格 */
body::before{
  content:"";position:fixed;inset:0;pointer-events:none;z-index:0;
  background-image:
    linear-gradient(rgba(255,255,255,.014) 1px,transparent 1px),
    linear-gradient(90deg,rgba(255,255,255,.014) 1px,transparent 1px);
  background-size:34px 34px;
  mask-image:radial-gradient(1200px 900px at 50% 0%,#000 40%,transparent 92%);
}
/* 顶部一线光 */
body::after{
  content:"";position:fixed;left:0;right:0;top:0;height:1px;z-index:2;pointer-events:none;
  background:linear-gradient(90deg,transparent,rgba(46,230,201,.55),rgba(120,180,255,.35),transparent);
}
body>*{position:relative;z-index:1}
.wrap{max-width:1000px;margin:0 auto}

/* ── 头部 ── */
.hero{position:relative;padding:52px 0 22px;text-align:center;border-bottom:1px solid var(--line2)}
.hero .kicker{
  font:600 10.5px/1 ui-monospace,SFMono-Regular,Menlo,monospace;letter-spacing:.28em;
  text-transform:uppercase;color:var(--acc);opacity:.85;margin-bottom:18px;
}
h1{
  font-size:30px;line-height:1.3;margin:0;letter-spacing:.4px;font-weight:650;
  background:linear-gradient(180deg,#ffffff,#c9dbd8 68%,#93b7b2);
  -webkit-background-clip:text;background-clip:text;color:transparent;
}
.docmeta{
  display:flex;gap:9px;flex-wrap:wrap;justify-content:center;margin:20px 0 0;
  font:11.5px/1 ui-monospace,SFMono-Regular,Menlo,monospace;color:var(--soft);
}
.docmeta span{
  background:rgba(255,255,255,.03);border:1px solid var(--line2);border-radius:999px;
  padding:6px 11px;backdrop-filter:blur(6px);
}
.docmeta span:first-child{color:#bfe9e1;border-color:var(--acc-dim)}

h2{
  font-size:19px;margin:46px 0 16px;padding:0 0 10px;letter-spacing:.2px;
  border-bottom:1px solid var(--line);position:relative;
}
h2::after{
  content:"";position:absolute;left:0;bottom:-1px;width:56px;height:1px;
  background:linear-gradient(90deg,var(--acc),transparent);
}
h3{font-size:16px;margin:30px 0 10px;color:#dceaf0;letter-spacing:.2px}
h4{font-size:13.5px;margin:24px 0 8px;color:var(--acc);letter-spacing:.02em}

p{margin:10px 0}
strong{font-weight:650;color:#ffffff}
em{color:#cfe6e2;font-style:normal}

blockquote{
  margin:14px 0;padding:12px 16px;border-left:2px solid var(--acc);
  background:linear-gradient(90deg,var(--acc-soft),transparent 78%);
  color:#a9bcc4;border-radius:0 10px 10px 0;font-size:13.4px;
}
blockquote p{margin:4px 0}
blockquote code{background:rgba(46,230,201,.08);border-color:var(--acc-dim);color:#bfe9e1}

ul,ol{margin:10px 0 10px 22px;padding:0}
li{margin:5px 0}
li::marker{color:var(--acc);opacity:.7}

code{
  font:.88em/1.55 ui-monospace,SFMono-Regular,Menlo,monospace;
  background:var(--code);border:1px solid var(--line2);border-radius:5px;
  padding:1.5px 5px;color:#9fe0d4;white-space:nowrap;
}
pre{
  background:linear-gradient(#080d11,#060a0e);border:1px solid var(--line2);
  border-radius:12px;padding:14px 16px;overflow:auto;margin:14px 0;
  box-shadow:inset 0 1px 0 rgba(255,255,255,.03);
}
pre code{background:none;border:0;padding:0;font-size:12.5px;line-height:1.62;color:#b9d6d0;white-space:pre}

table{
  border-collapse:separate;border-spacing:0;width:100%;margin:16px 0;font-size:13.2px;
  border:1px solid var(--line2);border-radius:12px;overflow:hidden;
}
th,td{border-bottom:1px solid var(--line2);padding:9px 11px;text-align:left;vertical-align:top}
th{
  background:rgba(46,230,201,.055);font-weight:650;white-space:nowrap;color:#d8f2ec;
  font-size:12.6px;letter-spacing:.02em;
}
tbody tr{transition:background .16s ease}
tbody tr:hover{background:rgba(46,230,201,.035)}
tbody tr:last-child td{border-bottom:0}
td code{font-size:12.2px}

hr{border:0;border-top:1px solid var(--line);margin:38px 0}
a{color:var(--acc);text-decoration:none;border-bottom:1px dotted rgba(46,230,201,.45)}
a:hover{border-bottom-color:var(--acc)}
::selection{background:rgba(46,230,201,.24);color:#fff}

.nowrap{white-space:nowrap}

/* 架构总览：深色图卡，图内自带配色 */
.archfig{
  background:linear-gradient(#0a1017,#070b10);border:1px solid var(--line2);
  border-radius:16px;padding:16px 12px;overflow-x:auto;margin:16px 0 20px;
  box-shadow:0 24px 60px -40px rgba(46,230,201,.35);
}
.archfig svg{display:block;max-width:100%;height:auto;margin:0 auto}

/* 附：细软滚动条 */
::-webkit-scrollbar{width:10px;height:10px}
::-webkit-scrollbar-thumb{background:rgba(255,255,255,.12);border-radius:8px}
::-webkit-scrollbar-thumb:hover{background:rgba(46,230,201,.35)}
::-webkit-scrollbar-track{background:transparent}

@media (max-width:720px){
  body{padding:0 18px 64px}
  h1{font-size:24px}
  table{font-size:12.6px}
}
@media print{
  body{background:#fff;color:#111}
  body::before,body::after{display:none}
  h1{color:#111;-webkit-text-fill-color:#111}
  th{background:#f3f6f8;color:#111}
  td,th{border-color:#d5dce2}
}
"""


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
<style>{CSS}</style>
</head>
<body><div class="wrap">
{body}
</div></body></html>
"""
    OUT.write_text(html, encoding="utf-8")
    print(f"写出 {OUT}  {len(html)} B  版本 {ver}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
