#!/usr/bin/env bash
# 开一张产品单：FR-YYYYMMDD-N（功能）或 PR-YYYYMMDD-N（缺陷 / Problem Report）
# 例：
#   ./scripts/new-ticket.sh fr "接管场景分类"
#   ./scripts/new-ticket.sh pr "步骤日志中途停更"
#   ./scripts/new-ticket.sh --dry-run fr "只睇单号"
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
exec python3 "$ROOT/scripts/new-ticket.py" "$@"
