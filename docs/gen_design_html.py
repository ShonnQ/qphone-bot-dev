#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""把 docs/详细设计.md 渲染成 docs/详细设计.html（预览窗口睇用）。

用法：python3 docs/gen_design_html.py
依赖：markdown（`python3 -m pip install --user markdown`）
改完 md 之后跑一次即可；HTML 唔手改。
"""

import re
from pathlib import Path

import markdown

DOCS = Path(__file__).resolve().parent
SRC = DOCS / "详细设计.md"
OUT = DOCS / "详细设计.html"

CSS = """
:root{
  --bg:#ffffff; --fg:#0f172a; --soft:#4d5b73; --line:rgba(15,23,42,.14);
  --line2:rgba(15,23,42,.07); --code:#f6f8fc; --acc:#0369a1; --ok:#047857;
}
*{box-sizing:border-box}
html{-webkit-text-size-adjust:100%}
body{
  margin:0; background:var(--bg); color:var(--fg);
  font:15px/1.75 -apple-system,"SF Pro Text","PingFang SC","Hiragino Sans GB",system-ui,sans-serif;
  padding:36px 44px 80px;
}
.wrap{max-width:1000px;margin:0 auto}
h1{font-size:27px;line-height:1.35;margin:0 0 6px;letter-spacing:.2px}
h1 + blockquote{margin-top:10px}
h2{font-size:19px;margin:38px 0 14px;padding-bottom:7px;border-bottom:1px solid var(--line)}
h3{font-size:16.5px;margin:28px 0 10px;color:var(--fg)}
h4{font-size:14px;margin:22px 0 8px;color:var(--acc)}
p{margin:9px 0}
blockquote{margin:12px 0;padding:10px 14px;border-left:3px solid var(--acc);
  background:var(--code);color:var(--soft);border-radius:0 8px 8px 0;font-size:13.5px}
blockquote p{margin:3px 0}
ul,ol{margin:9px 0 9px 22px;padding:0}
li{margin:4px 0}
code{font:.9em/1.5 ui-monospace,SFMono-Regular,Menlo,monospace;
  background:var(--code);border:1px solid var(--line2);border-radius:5px;padding:1px 5px}
pre{background:var(--code);border:1px solid var(--line2);border-radius:10px;
  padding:12px 14px;overflow:auto}
pre code{background:none;border:0;padding:0;font-size:12.6px;line-height:1.6}
table{border-collapse:collapse;width:100%;margin:14px 0;font-size:13.4px}
th,td{border:1px solid var(--line2);padding:7px 10px;text-align:left;vertical-align:top}
th{background:var(--code);font-weight:700;white-space:nowrap}
tbody tr:nth-child(even) td{background:#fcfdff}
hr{border:0;border-top:1px solid var(--line);margin:34px 0}
a{color:var(--acc)}
strong{font-weight:700}
.meta{font:11.5px/1 ui-monospace,Menlo,monospace;color:var(--soft);
  display:flex;gap:10px;flex-wrap:wrap;margin:14px 0 0}
.meta span{background:var(--code);border:1px solid var(--line2);border-radius:999px;padding:4px 9px}
.nowrap{white-space:nowrap}
/* 架构总览：深色图卡，图内自带配色，唔跟亮色主题 */
.archfig{background:#0b1220;border:1px solid rgba(15,23,42,.16);border-radius:16px;
  padding:14px 12px;overflow-x:auto;margin:14px 0 18px}
.archfig svg{display:block;max-width:100%;height:auto;margin:0 auto}
"""


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

    # 标题去掉首个 H1（保留正文 h1 视觉），并把「> 版本」元信息做成 chip
    html = f"""<!doctype html>
<html lang="zh-Hant">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>QPhone 详细设计说明书 {ver}</title>
<style>{CSS}</style>
</head>
<body><div class="wrap">
<div class="meta"><span>QPhone Bot · 详细设计</span><span>版本 {ver}</span><span>渲染自 详细设计.md</span></div>
{body}
</div></body></html>
"""
    OUT.write_text(html, encoding="utf-8")
    print(f"写出 {OUT}  {len(html)} B  版本 {ver}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
