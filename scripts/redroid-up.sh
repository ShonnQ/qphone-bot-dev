#!/usr/bin/env bash
# 起本地 redroid 13（arm64）云机：colima VM 内 Docker
# 用法：
#   ./scripts/redroid-up.sh            起一台，端口 5555
#   ./scripts/redroid-up.sh down       停掉
#   ./scripts/redroid-up.sh status     睇状态
# 前置：colima 已起（colima status），VM 内核已装 linux-modules-extra（binder_linux.ko）
set -uo pipefail

NAME="${REDROID_NAME:-qphone-redroid}"
PORT="${REDROID_PORT:-5556}"   # 5555 默认留给 AVD 模拟器，撞端口
DATA="${REDROID_DATA:-$HOME/redroid13-data}"
IMAGE="${REDROID_IMAGE:-redroid/redroid:13.0.0-latest}"
WIDTH="${REDROID_WIDTH:-720}"
HEIGHT="${REDROID_HEIGHT:-1280}"
DPI="${REDROID_DPI:-320}"
ADB="$HOME/Library/Android/sdk/platform-tools/adb"
COLIMA="${COLIMA_BIN:-$HOME/.local/bin/colima}"

say() { printf '\n\033[1m%s\033[0m\n' "$*"; }
die() { printf '\033[31m%s\033[0m\n' "$*" >&2; exit 1; }

vm() { "$COLIMA" ssh -- bash -c "$*"; }

ensure_binder() {
  say "1/4 检查 VM binder"
  local k
  k="$(vm uname -r | tr -d '\r')" || die "colima 未起，先 colima start"

  if vm "test -e /dev/binder && test -e /dev/hwbinder"; then
    echo "  /dev/binder 已在"
    return
  fi

  # 6.8 内核 binder 是模块，靠 binderfs 产设备节点
  vm sudo modprobe binder_linux devices='"binder,hwbinder,vndbinder"' 2>&1 | tail -2
  vm sudo bash -c 'mkdir -p /dev/binderfs; mountpoint -q /dev/binderfs || mount -t binder binder /dev/binderfs' 2>&1 | tail -2
  vm sudo bash -c 'for d in binder hwbinder vndbinder; do [ -e /dev/binderfs/$d ] && ln -sf /dev/binderfs/$d /dev/$d; done' 2>&1 | tail -2

  if vm "test -e /dev/binder && test -e /dev/hwbinder"; then
    echo "  binder 就绪（内核 $k，binderfs 已挂 /dev/binderfs）"
  else
    vm "lsmod | grep binder" || true
    vm "cat /proc/filesystems | grep binder" || true
    die "binder 起唔到：内核 $k。检查 linux-modules-extra 有冇装"
  fi
}

case "${1:-up}" in
  down)
    say "停 ${NAME}"
    "$COLIMA" nerdctl stop "$NAME" 2>/dev/null || docker stop "$NAME" 2>/dev/null || true
    "$ADB" disconnect "localhost:$PORT" >/dev/null 2>&1 || true
    echo "  已停"
    exit 0
    ;;
  status)
    "$ADB" devices | grep -E "127.0.0.1:$PORT|localhost:$PORT" || echo "  adb 未见 localhost:$PORT"
    docker ps --filter "name=$NAME" --format '  {{.Names}}  {{.Status}}  {{.Ports}}' 2>/dev/null
    exit 0
    ;;
  up) ;;
  *) die "用法：$0 [up|down|status]" ;;
esac

ensure_binder

say "2/4 起容器 ${IMAGE}（${WIDTH}x${HEIGHT} dpi${DPI}）"
mkdir -p "$DATA"
docker rm -f "$NAME" >/dev/null 2>&1 || true
docker run -d --rm --privileged \
  --platform linux/arm64 \
  -v "$DATA:/data" \
  -p "$PORT:5555" \
  --name "$NAME" \
  "$IMAGE" \
  androidboot.redroid_width="$WIDTH" \
  androidboot.redroid_height="$HEIGHT" \
  androidboot.redroid_dpi="$DPI" \
  androidboot.use_memfd=true \
  androidboot.redroid_gpu_mode=guest 2>&1 | tail -2

say "3/4 等开机（最多 120s）"
for i in $(seq 1 40); do
  if "$ADB" connect "localhost:$PORT" 2>&1 | grep -q "connected"; then
    b="$("$ADB" -s "localhost:$PORT" shell getprop sys.boot_completed 2>/dev/null | tr -d '\r')"
    [ "$b" = "1" ] && echo "  开机完成（第 $i 次探测）" && break
  fi
  sleep 3
done

say "4/4 验证"
docker ps --filter "name=$NAME" --format '{{.Names}} {{.Status}}' | grep -q "$NAME" || die "容器 $NAME 未起（docker logs $NAME）"
"$ADB" devices | grep "localhost:$PORT" || die "adb 连唔到 localhost:$PORT（docker logs $NAME 睇）"
MODEL="$("$ADB" -s "localhost:$PORT" shell getprop ro.product.model 2>/dev/null | tr -d '\r')"
echo "  设备型号：$MODEL（AVD 会系 sdk_gphone64_arm64，撞端口就错）"
[ "$MODEL" = "sdk_gphone64_arm64" ] && die "localhost:$PORT 其实系 AVD 模拟器，换 REDROID_PORT 再试"
"$ADB" -s "localhost:$PORT" shell "getprop ro.build.version.release; getprop ro.build.version.sdk; getprop ro.product.cpu.abi" | tr -d '\r'
echo
echo "起好。指向它："
echo "  QPHONE_TARGET=localhost:$PORT python3 serve.py"
