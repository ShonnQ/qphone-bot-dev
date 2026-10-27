#!/usr/bin/env python3
"""分配 FR-/PR- 产品单号并开 GitHub Issue（QPhone Bot）。

用法：
  ./scripts/new-ticket.sh fr "接管场景分类"
  ./scripts/new-ticket.sh pr "步骤日志中途停更"
  ./scripts/new-ticket.sh --dry-run fr "只睇单号"
  ./scripts/new-ticket.sh --body-file /tmp/fr.md fr "标题"

单号以 Issue 标题为准；GitHub 原生 #N 只系链接（Issue / PR 共用计数器）。
PR- 系 Problem Report（缺陷），唔系 GitHub Pull Request。
"""
from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
INDEX = ROOT / "tickets" / "INDEX.md"
REPO = "ShonnQ/qphone-bot-dev"
ID_RE = re.compile(r"\b(FR|PR)-(\d{8})-(\d+)\b")

INDEX_HEAD = (
    "# 产品单账本\n\n"
    "| 单号 | GitHub | 标题 | 状态 | PR |\n|---|---|---|---|---|\n"
)


def run(args: list[str], check: bool = True) -> subprocess.CompletedProcess:
    return subprocess.run(args, cwd=ROOT, check=check, text=True, capture_output=True)


def today() -> str:
    out = run(["date", "+%Y%m%d"])
    return out.stdout.strip() or date.today().strftime("%Y%m%d")


def ids_from_index() -> list[tuple[str, str, int]]:
    if not INDEX.exists():
        return []
    return [(m.group(1), m.group(2), int(m.group(3))) for m in ID_RE.finditer(INDEX.read_text(encoding="utf-8"))]


def ids_from_github() -> list[tuple[str, str, int]]:
    out = run(["gh", "issue", "list", "--repo", REPO, "--state", "all",
               "--limit", "200", "--json", "number,title"])
    found = []
    for item in json.loads(out.stdout or "[]"):
        found += [(m.group(1), m.group(2), int(m.group(3))) for m in ID_RE.finditer(item.get("title") or "")]
    return found


def next_n(prefix: str, day: str) -> int:
    n = 0
    for p, d, i in ids_from_index() + ids_from_github():
        if p == prefix and d == day:
            n = max(n, i)
    return n + 1


def append_index(ticket_id: str, gh_num: str, title: str) -> None:
    INDEX.parent.mkdir(parents=True, exist_ok=True)
    if not INDEX.exists():
        INDEX.write_text(INDEX_HEAD, encoding="utf-8")
    text = INDEX.read_text(encoding="utf-8")
    if not text.endswith("\n"):
        text += "\n"
    INDEX.write_text(text + f"| {ticket_id} | #{gh_num} | {title} | open | |\n", encoding="utf-8")


def default_body(kind: str, ticket_id: str) -> str:
    if kind == "fr":
        return (
            f"产品单：`{ticket_id}`\n\n"
            "## 背景\n\n（为咩要做；老板原话 / 实测现象）\n\n"
            "## 做成怎样\n\n（范围与目标行为，唔写实现细节）\n\n"
            "## 影响层\n\n- [ ] L0 确定性命令\n- [ ] L1a 无障碍树\n- [ ] L1b WebView CDP\n- [ ] L2 视觉兜底\n- [ ] 壳 UI / 文档\n\n"
            "## 验收\n\n- [ ] \n\n"
            "## 实测证据\n\n（命中命令 + 输出 / trajectory id / 截图绝对路径）\n\n"
            "## 版本\n\n（shell / serve / 详细设计 版本号）\n"
        )
    return (
        f"产品单：`{ticket_id}`（Problem Report，不是 GitHub Pull Request）\n\n"
        "## 现象\n\n（实测见到乜；版本 / 载具 / 复现步骤）\n\n"
        "## 期望\n\n\n"
        "## 影响层\n\n- [ ] L0\n- [ ] L1a\n- [ ] L1b\n- [ ] L2\n- [ ] 壳 UI / 文档\n\n"
        "## 验收\n\n- [ ] 现象不再出现\n- [ ] 无回归（L0/L1 命中率、步数唔变差）\n\n"
        "## 实测证据\n\n（trajectory id / 命令输出 / 日志行）\n"
    )


def main() -> int:
    p = argparse.ArgumentParser(description="Open a QPhone FR/PR GitHub issue with a dated ticket id")
    p.add_argument("--dry-run", action="store_true")
    p.add_argument("--body-file", help="Markdown file for the issue body")
    p.add_argument("kind", choices=["fr", "pr", "FR", "PR"])
    p.add_argument("title", nargs="+", help="Short title without the ticket id")
    args = p.parse_args()
    kind = args.kind.lower()
    prefix = kind.upper()
    title = " ".join(args.title).strip()
    if not title:
        print("need a title", file=sys.stderr)
        return 2
    if ID_RE.search(title):
        print("title already contains a ticket id — pass the short title only", file=sys.stderr)
        return 2

    day = today()
    ticket_id = f"{prefix}-{day}-{next_n(prefix, day)}"
    full_title = f"{ticket_id} {title}"
    label = "enhancement" if kind == "fr" else "bug"
    body = Path(args.body_file).read_text(encoding="utf-8") if args.body_file else default_body(kind, ticket_id)

    print(f"id={ticket_id}\ntitle={full_title}\nlabel={label}")
    if args.dry_run:
        return 0

    try:
        out = run(["gh", "issue", "create", "--repo", REPO, "--title", full_title,
                   "--label", label, "--body", body])
    except subprocess.CalledProcessError as e:
        print(e.stderr or e.stdout, file=sys.stderr)
        return e.returncode or 1
    url = (out.stdout or "").strip()
    print(url)
    m = re.search(r"/issues/(\d+)", url)
    gh_num = m.group(1) if m else "?"
    append_index(ticket_id, gh_num, title)
    print(f"index={INDEX} github=#{gh_num}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
