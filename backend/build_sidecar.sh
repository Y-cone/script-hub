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

# 1) externalBin：按现有文件名覆盖（Tauri 要求 <name>-<target-triple>）。
#    用通配匹配现存文件，这样跨平台（linux-gnu / msvc / apple-darwin）都不用改脚本。
mkdir -p "$TB/binaries"
shopt -s nullglob
found=0
for f in "$TB/binaries"/scripthub-server-*; do
  case "$f" in
    *.exe) cp -f "$HERE"/dist/scripthub-server.exe "$f" 2>/dev/null && found=1 ;;
    *)     cp -f "$HERE/dist/scripthub-server" "$f" && found=1 ;;
  esac
done
shopt -u nullglob
[ "$found" -eq 1 ] || echo "[sidecar] ⚠️ binaries/ 下没有 scripthub-server-* ，跳过 externalBin 同步"

# 2) release 目录：开发时壳直接从同目录读 sidecar（不是 Tauri 打包路径）。
#    目标目录此时可能还不存在（首次构建），忽略失败。
cp -f "$HERE/dist/scripthub-server" "$TB/target/release/scripthub-server" 2>/dev/null \
  && echo "[sidecar] 已同步 target/release/" \
  || echo "[sidecar] (target/release 不存在，跳过——首次构建后自动会有)"

chmod +x "$TB/binaries"/scripthub-server-* 2>/dev/null || true
echo "[sidecar] ✅ 完成：$(stat -c %s "$HERE/dist/scripthub-server" 2>/dev/null || echo '?') bytes"
