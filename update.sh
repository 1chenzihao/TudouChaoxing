#!/usr/bin/env bash
# 土豆刷学习通 · 一键热更新（macOS / Linux / Git Bash）
set -e
cd "$(dirname "$0")"
REPO="yyzbuhaohe/TudouChaoxing"
BRANCH="main"

echo "=============================================="
echo "  土豆刷学习通 · 一键更新"
echo "  目录: $(pwd)"
echo "=============================================="

[ -f main.py ] || { echo "[失败] 请在项目根目录运行"; exit 1; }
command -v python3 >/dev/null || command -v python >/dev/null || { echo "[失败] 没找到 python"; exit 1; }
PY=$(command -v python3 || command -v python)

STAMP=$(date +%Y%m%d-%H%M%S)
BACKUP="_backup_$STAMP"
mkdir -p "$BACKUP"
KEEP="config.ini cookies.txt cache.json retry_pending.json chaoxing.log run.log scan.log"
for f in $KEEP; do [ -f "$f" ] && cp "$f" "$BACKUP/"; done
echo "[OK] 已备份用户数据 -> $BACKUP"

if [ -d .git ]; then
  echo "方式：git pull"
  OLD=$(git rev-parse --short HEAD 2>/dev/null || echo "?")
  git fetch --all --quiet
  git reset --hard "origin/$BRANCH" --quiet
  NEW=$(git rev-parse --short HEAD)
  echo "[OK] 已更新 $OLD -> $NEW"
else
  echo "方式：下载最新 ZIP 覆盖"
  TMP=$(mktemp -d)
  URL="https://github.com/$REPO/archive/refs/heads/$BRANCH.zip"
  if command -v curl >/dev/null; then curl -fsSL "$URL" -o "$TMP/main.zip"; else wget -q "$URL" -O "$TMP/main.zip"; fi
  (command -v unzip >/dev/null && unzip -q "$TMP/main.zip" -d "$TMP") || "$PY" -c "import zipfile,sys;zipfile.ZipFile(sys.argv[1]).extractall(sys.argv[2])" "$TMP/main.zip" "$TMP"
  SRC=$(find "$TMP" -maxdepth 1 -type d -name "*TudouChaoxing*" | head -n 1)
  [ -n "$SRC" ] || { echo "[失败] 解压后没找到代码目录"; exit 1; }
  ( cd "$SRC" && find . -type f ) | while read -r rel; do
    rel=${rel#./}
    case "$rel" in
      config.ini|cookies.txt|cache.json|retry_pending.json|chaoxing.log|run.log|scan.log) continue;;
    esac
    mkdir -p "$(dirname "$rel")"
    cp "$SRC/$rel" "$rel"
  done
  echo "[OK] 代码已覆盖"
  rm -rf "$TMP"
fi

for f in $KEEP; do [ -f "$BACKUP/$f" ] && cp "$BACKUP/$f" "$f"; done
echo "[OK] config.ini 已还原"

echo "更新依赖"
"$PY" -m pip install -q -r requirements.txt || echo "[注意] 依赖安装有报错，看上面最后一行"

echo "=============================================="
echo "  更新完成，备份在 $BACKUP"
echo "  下一步: python main.py --solve-only   或   python main.py"
echo "=============================================="