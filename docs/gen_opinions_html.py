#!/usr/bin/env python3
"""意见收集表：md → html（深色，可直接入预览窗）。用法: python3 gen_opinions_html.py"""
import pathlib, html as H

DOCS = pathlib.Path(__file__).parent
MD = DOCS / "意见收集-2026-10-23.md"
HTML = DOCS / "意见收集-2026-10-23.html"

CSS = """
:root{color-scheme:dark;--bg:#080a0e;--panel:#10151c;--fg:#eef2f7;--soft:#94a3b8;--line:rgba(255,255,255,.12);--acc:#7dd3fc}
*{box-sizing:border-box}
body{margin:0 auto;max-width:1280px;padding:24px 20px 60px;background:var(--bg);color:var(--fg);
 font:15px/1.7 -apple-system,BlinkMacSystemFont,"PingFang SC","Microsoft YaHei",sans-serif}
h1{font-size:21px;margin:0 0 6px}
h2{font-size:16px;margin:26px 0 8px;color:var(--acc);letter-spacing:.3px}
h3{font-size:14px;margin:18px 0 6px}
p{margin:6px 0}p.li{margin:3px 0 3px 16px}
blockquote{margin:10px 0;padding:8px 14px;border-left:3px solid var(--acc);
 background:rgba(125,211,252,.08);font-size:14px;color:var(--soft)}
table{width:100%;border-collapse:collapse;margin:8px 0 4px;font-size:13.5px}
th,td{border:1px solid var(--line);padding:7px 9px;text-align:left;vertical-align:top}
th{background:rgba(125,211,252,.09);font-weight:600;white-space:nowrap}
td:first-child{white-space:nowrap;color:var(--acc);font-family:ui-monospace,Menlo,monospace;font-size:12.5px}
td:last-child{white-space:nowrap}
"""

def md_to_html(md: str) -> str:
    out, lines, i = [], md.split("\n"), 0
    while i < len(lines):
        ln = lines[i]
        if ln.startswith("#"):
            lvl = len(ln) - len(ln.lstrip("#"))
            out.append(f"<h{lvl}>{H.escape(ln[lvl:].strip())}</h{lvl}>")
        elif ln.strip().startswith("|"):
            rows = []
            while i < len(lines) and lines[i].strip().startswith("|"):
                rows.append([c.strip() for c in lines[i].strip().strip("|").split("|")]); i += 1
            head, rest = rows[0], rows[2:]
            tb = ["<table><thead><tr>"] + [f"<th>{H.escape(c)}</th>" for c in head] + ["</tr></thead><tbody>"]
            for r in rest:
                tb.append("<tr>" + "".join(f"<td>{H.escape(c)}</td>" for c in r) + "</tr>")
            tb.append("</tbody></table>")
            out.append("".join(tb)); continue
        elif ln.strip().startswith(">"):
            out.append(f"<blockquote>{H.escape(ln.strip()[1:].strip())}</blockquote>")
        elif ln.strip().startswith("- "):
            out.append(f"<p class='li'>{H.escape(ln.strip()[2:])}</p>")
        elif ln.strip():
            out.append(f"<p>{H.escape(ln.strip())}</p>")
        i += 1
    return "\n".join(out)

if __name__ == "__main__":
    body = md_to_html(MD.read_text(encoding="utf-8"))
    HTML.write_text(
        '<!DOCTYPE html><html lang="zh-Hans"><head><meta charset="utf-8">'
        "<title>QPhone 壳 UI · 意见收集 2026-10-23</title><style>" + CSS + "</style></head><body>\n"
        + body + "\n</body></html>", encoding="utf-8")
    print("生成:", HTML.name, HTML.stat().st_size, "字节")
