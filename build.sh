#!/bin/sh
# 重新编译 rkp_bridge.jar / Rebuild rkp_bridge.jar
#
# 用法 / Usage:
#   sh build.sh <android.jar 或 stubs.jar> [r8.jar]
#
# 需要 / Requires:
#   - JDK（javac）/ a JDK with javac
#   - D8（r8.jar）/ D8 from r8.jar
set -e

STUBS="${1:?用法 / usage: sh build.sh <android.jar> [r8.jar]}"
R8="${2:-r8.jar}"

for f in "$STUBS" "$R8"; do
    [ -f "$f" ] || { echo "[X] 找不到 / not found: $f" >&2; exit 1; }
done

OUT=$(mktemp -d "${TMPDIR:-/tmp}/rkpbuild.XXXXXX")
trap 'rm -rf "$OUT"' EXIT
mkdir -p "$OUT/classes" "$OUT/dex"

echo "[i] 编译 Java / compiling"
javac -source 8 -target 8 -nowarn -cp "$STUBS" -d "$OUT/classes" src/*.java

echo "[i] 转 DEX / converting to DEX"
java -cp "$R8" com.android.tools.r8.D8 \
    --release --min-api 31 --lib "$STUBS" \
    --output "$OUT/dex" "$OUT"/classes/rkpb/*.class

echo "[i] 打包 jar / packaging jar"
python3 - "$OUT/dex/classes.dex" <<'PY'
import sys, zipfile
with zipfile.ZipFile('rkp_bridge.jar', 'w', zipfile.ZIP_DEFLATED) as z:
    z.write(sys.argv[1], 'classes.dex')
PY

echo "[OK] rkp_bridge.jar 已更新 / updated: $(wc -c < rkp_bridge.jar | tr -d ' ') bytes"
echo "     提交前确认 / confirm before committing: git status"
