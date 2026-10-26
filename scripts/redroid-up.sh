#!/usr/bin/env bash
# 起本地 redroid 13 云机（colima VM 内 Docker，arm64）
#   ./scripts/redroid-up.sh            起一台（默认端口 5556）
#   ./scripts/redroid-up.sh down       停掉
#   ./scripts/redroid-up.sh status     睇状态
#
# 实测要点（2026-10-26，colima Ubuntu 24.04 / 内核 6.8.0-117-generic）：
#   1) 必须用 _64only 镜像：普通 13.0.0 带 32 位 vendor 二进制，
#      arm64 VM 上 init 自检失败（Exec format error）→ 容器 Exited (129)、日志空白
#   2) binder 走 binderfs：modprobe 只令 /proc/filesystems 出现 binder，
#      唔会自动出 /dev/binder，要 mount -t binder binder /dev/binderfs
#   3) binderfs 节点默认 600 → 容器内服务开 /dev/binder 得 Permission denied，要 chmod 666
#   4) 节点要用 -v 绑入容器：用 --device 会变 ENXIO
#   5) /data 一定要放 VM 自己嘅 ext4（Docker 命名卷），唔可以挂宿主目录：
#      宿主目录经 colima 嘅 virtiofs，Android vold 做唔到 ext4 project quota，
#      StorageManager 会一路报 "Inappropriate ioctl for device" / 存储有问题
set -uo pipefail

NAME="${REDROID_NAME:-qphone-redroid}"
PORT="${REDROID_PORT:-5556}"   # 5555 默认留给 AVD 模拟器，撞端口
DATA_VOLUME="${REDROID_DATA_VOLUME:-qphone-data}"   # 命名卷：数据落 VM 嘅 ext4
DATA_DIR="${REDROID_DATA:-}"                        # 有值就改用宿主目录（会经 virtiofs，Android 会报存储问题）
IMAGE="${REDROID_IMAGE:-redroid/redroid:13.0.0_64only-latest}"
WIDTH="${REDROID_WIDTH:-720}"
HEIGHT="${REDROID_HEIGHT:-1280}"
DPI="${REDROID_DPI:-320}"
ADB="$HOME/Library/Android/sdk/platform-tools/adb"
COLIMA="${COLIMA_BIN:-$HOME/.local/bin/colima}"

say() { printf '\n\033[1m%s\033[0m\n' "$*"; }
die() { printf '\033[31m%s\033[0m\n' "$*" >&2; exit 1; }

vm() { "$COLIMA" ssh -- bash -c "$*"; }

ensure_binder() {
  say "1/4 检查 VM binder + binderfs"
  local k
  k="$(vm uname -r | tr -d '\r')" || die "colima 未起，先 colima start"

  if ! vm "test -e /dev/binderfs/binder"; then
    vm "sudo modprobe binder_linux devices='binder,hwbinder,vndbinder'" 2>&1 | tail -2
    vm "sudo bash -c 'mkdir -p /dev/binderfs; mountpoint -q /dev/binderfs || mount -t binder binder /dev/binderfs'" 2>&1 | tail -2
  fi
  # 每次都给 666：binderfs 新节点默认 600，容器内非 root 打唔开
  vm "sudo chmod 666 /dev/binderfs/binder /dev/binderfs/hwbinder /dev/binderfs/vndbinder" 2>&1 | tail -2

  if vm "test -e /dev/binderfs/binder && test -e /dev/binderfs/hwbinder"; then
    echo "  binder 就绪（内核 ${k}，binderfs @ /dev/binderfs）"
  else
    vm "lsmod | grep binder" || true
    vm "cat /proc/filesystems | grep binder" || true
    die "binder 起唔到：内核 ${k}。检查 linux-modules-extra 有冇装"
  fi
}

case "${1:-up}" in
  down)
    say "停 ${NAME}"
    docker rm -f "$NAME" >/dev/null 2>&1 || true
    "$ADB" disconnect "localhost:$PORT" >/dev/null 2>&1 || true
    echo "  已停"
    exit 0
    ;;
  status)
    docker ps -a --filter "name=$NAME" --format '  {{.Names}} | {{.Status}} | {{.Ports}}' 2>/dev/null
    "$ADB" devices | grep -E "127.0.0.1:$PORT|localhost:$PORT" || echo "  adb 未见 localhost:$PORT"
    exit 0
    ;;
  up) ;;
  *) die "用法：$0 [up|down|status]" ;;
esac

ensure_binder

say "2/4 起容器 ${IMAGE}（${WIDTH}x${HEIGHT} dpi${DPI}）"
if [ -n "$DATA_DIR" ]; then
  echo "  ⚠ 用宿主目录 ${DATA_DIR}（经 virtiofs，Android 会报存储问题；只作调试）"
  mkdir -p "$DATA_DIR"
  DATA_ARG="$DATA_DIR:/data"
else
  docker volume create "$DATA_VOLUME" >/dev/null 2>&1 || true
  echo "  /data → Docker 卷 ${DATA_VOLUME}（VM 内 ext4）"
  DATA_ARG="$DATA_VOLUME:/data"
fi
docker rm -f "$NAME" >/dev/null 2>&1 || true
docker run -d --privileged --platform linux/arm64 \
  -v /dev/binderfs:/dev/binderfs \
  -v /dev/binderfs/binder:/dev/binder \
  -v /dev/binderfs/hwbinder:/dev/hwbinder \
  -v /dev/binderfs/vndbinder:/dev/vndbinder \
  -v "$DATA_ARG" \
  -p "$PORT:5555" \
  --name "$NAME" \
  "$IMAGE" \
  androidboot.redroid_width="$WIDTH" \
  androidboot.redroid_height="$HEIGHT" \
  androidboot.redroid_dpi="$DPI" \
  androidboot.use_memfd=true >/dev/null 2>&1

sleep 5
docker ps --filter "name=$NAME" --format '{{.Names}} {{.Status}}' | grep -q "${NAME}" \
  || { vm "sudo dmesg | grep -iE 'Abort message|reboot_on_failure' | tail -5"; die "容器 ${NAME} 起唔到（上面系内核日志）"; }

say "3/4 等开机（最多 150s）"
ok=0
for _ in $(seq 1 30); do
  "$ADB" connect "localhost:$PORT" >/dev/null 2>&1
  b="$("$ADB" -s "localhost:$PORT" shell getprop sys.boot_completed 2>/dev/null | tr -d '\r')"
  [ "$b" = "1" ] && ok=1 && break
  sleep 5
done
[ "$ok" = "1" ] || die "adb 连到但开机未完成（docker logs ${NAME} / dmesg 睇）"

say "4/4 验证"
MODEL="$("$ADB" -s "localhost:$PORT" shell getprop ro.product.model 2>/dev/null | tr -d '\r')"
echo "  设备型号：${MODEL}"
[ "$MODEL" = "sdk_gphone64_arm64" ] && die "localhost:${PORT} 其实系 AVD 模拟器，换 REDROID_PORT 再试"
"$ADB" -s "localhost:$PORT" shell "getprop ro.build.version.release; getprop ro.build.version.sdk; getprop ro.product.cpu.abi" | tr -d '\r'
DFS="$("$ADB" -s "localhost:$PORT" shell "mount | grep -m1 ' /data '" 2>/dev/null | tr -d '\r')"
echo "  /data：${DFS:-未知}"
case "$DFS" in
  *ext4*|*f2fs*) ;;
  *) echo "  ⚠ /data 唔系 ext4/f2fs（多数系 virtiofs），Android 会报存储问题 —— 参考脚本头第 5 条" ;;
esac
echo
echo "起好。指向它："
echo "  QPHONE_TARGET=localhost:$PORT python3 serve.py"
