#!/usr/bin/env bash
# 构建后端 sidecar（PyInstaller）并同步到 Tauri 的 externalBin 与 release 目录。
# 被 frontend/src-tauri/tauri.conf.json 的 beforeBuildCommand 调用；也可手动跑：
#   bash backend/build_sidecar.sh
#
# 背景：tauri.conf.json 的 externalBin 指向 binaries/scripthub-server-<triple>，
# 但 Tauri 自己不会构建它——不跑这个脚本，打出来的壳会带着旧 sidecar（踩过）。
set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"       # backend/
ROOT="$(dirname "$HERE")"                                   # 仓库根
TB="$ROOT/frontend/src-tauri"
PYINSTALLER="$HERE/.venv/bin/pyinstaller"

[ -x "$PYINSTALLER" ] || { echo "[sidecar] ❌ 找不到 $PYINSTALLER"; exit 1; }
[ -f "$HERE/scripthub-server.spec" ] || { echo "[sidecar] ❌ 找不到 spec"; exit 1; }

echo "[sidecar] 构建中（PyInstaller）…"
cd "$HERE"
rm -rf build
"$PYINSTALLER" scripthub-server.spec --noconfirm 2>&1 | tail -3
[ -f dist/scripthub-server ] || { echo "[sidecar] ❌ 构建失败：dist/scripthub-server 不存在"; exit 1; }

# 1) externalBin：目标三件套逐个检查，**缺失也 copy**（此前只覆盖已有文件——binaries/ 里
#    只有 linux-gnu 占位时，msvc 的 .exe 不会被同步 → cargo 报 resource path doesn't exist）。
#    按平台出正确的产物名：Windows triple 要 .exe（linux 上 dist/ 是无后缀 ELF，直接改名拷贝）。
mkdir -p "$TB/binaries"
TRIPLES="x86_64-unknown-linux-gnu x86_64-pc-windows-msvc aarch64-apple-darwin"
for triple in $TRIPLES; do
  case "$triple" in
    *windows*) dst="$TB/binaries/scripthub-server-$triple.exe" ; src="$HERE/dist/scripthub-server.exe" ;;
    *)         dst="$TB/binaries/scripthub-server-$triple"    ; src="$HERE/dist/scripthub-server" ;;
  esac
  # 跨 triple 的产物本机不存在（Linux 构建没有 .exe）→ 跳过该占位，已有旧占位保留。
  # 只有当前平台的产物必须拷成功，否则 cargo 阶段必炸。
  if [ ! -f "$src" ]; then
    echo "[sidecar] 跳过 $triple（本机无 $(basename "$src")）"
    continue
  fi
  cp -f "$src" "$dst"
done

# 2) release 目录：开发时壳直接从同目录读 sidecar（不是 Tauri 打包路径）。
#    目标目录此时可能还不存在（首次构建），忽略失败。
cp -f "$HERE/dist/scripthub-server" "$TB/target/release/scripthub-server" 2>/dev/null \
  && echo "[sidecar] 已同步 target/release/" \
  || echo "[sidecar] (target/release 不存在，跳过——首次构建后自动会有)"

chmod +x "$TB/binaries"/scripthub-server-* 2>/dev/null || true
echo "[sidecar] ✅ 完成：$(stat -c %s "$HERE/dist/scripthub-server" 2>/dev/null || echo '?') bytes"
