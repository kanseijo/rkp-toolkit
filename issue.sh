#!/system/bin/sh
# 在设备上一键完成 RKP 签发，产出 keybox.xml
# One-shot RKP provisioning on the device, producing keybox.xml
#
# 用法 / Usage: sh issue.sh [输出目录 / output directory]
set -e

DIR=$(cd "$(dirname "$0")" && pwd)
OUT="${1:-$HOME/rkp_out}"
TP=/data/data/com.termux/files/usr

# ---- 依赖缺失时自动调用 setup.sh ----
# Auto-run setup.sh when a dependency is missing
need_setup=0
command -v python3 >/dev/null 2>&1 || need_setup=1
if [ "$need_setup" = 0 ] && ! python3 -c "import cryptography" >/dev/null 2>&1; then
    need_setup=1
fi

if [ "$need_setup" = 1 ]; then
    if [ -f "$DIR/setup.sh" ]; then
        echo "[i] 依赖不全，先运行环境配置 / dependencies missing, running setup"
        echo
        sh "$DIR/setup.sh" || {
            echo
            echo "[X] 环境配置失败 / setup failed"
            echo "    可手动重试 / retry manually: sh setup.sh"
            exit 1
        }
        echo
    else
        echo "[X] 找不到 python3 或 cryptography，且没有 setup.sh"
        echo "[X] python3 or cryptography missing, and setup.sh is absent"
        echo "    请手动安装 / install manually:"
        echo "      pkg install python python-cryptography"
        exit 1
    fi
fi

# ---- 定位 Termux python（su 会丢环境变量）----
# Locate Termux's python (su drops the environment variables)
PYB=""
for p in $TP/bin/python3 $TP/bin/python3.14 $TP/bin/python3.13 $TP/bin/python3.12 \
         /usr/bin/python3 python3; do
    if command -v "$p" >/dev/null 2>&1 || [ -x "$p" ]; then PYB="$p"; break; fi
done
[ -z "$PYB" ] && {
    echo "[X] 找不到 python3 / python3 not found"
    echo "    运行 / run: sh setup.sh"
    exit 1
}

PYVER=$(ls -d $TP/lib/python3.* 2>/dev/null | head -1 | sed 's|.*/||')
[ -z "$PYVER" ] && PYVER=python3.14

# ---- 找真正的 su ----
# Find the real su (Termux ships a fake one that only prints a notice)
if [ "$(id -u)" = "0" ]; then
    SU=""
else
    SU=""
    for c in /system/bin/su /system/xbin/su /debug_ramdisk/su /sbin/su \
             /data/adb/ksu/bin/su /data/adb/magisk/su; do
        [ -x "$c" ] && { SU="$c"; break; }
    done
    [ -z "$SU" ] && {
        echo "[X] 找不到真正的 su / no real su found"
        exit 1
    }
fi

echo "=========================================="
echo " RKP 签发工具链 / provisioning toolkit"
echo "=========================================="
echo "  python : $PYB"
echo "  su     : ${SU:-<已是 root / already root>}"
echo "  临时目录 / work : $OUT"
echo "  归档 / archive  : $OUT/$(date +%Y-%m-%d)_NNNN"
echo

# ---- Termux 版本检查 / Termux version check ----
# v0.118.3 以下（特别是 Google Play 的 0.118.0）换源后无法更新索引。
# Below v0.118.3 (especially Play's 0.118.0) the index update fails after
# switching mirrors.
TERMUX_MIN="0.118.3"
if [ -n "$TERMUX_VERSION" ]; then
    if [ "$TERMUX_VERSION" != "$TERMUX_MIN" ] && \
       [ "$(printf '%s\n%s\n' "$TERMUX_MIN" "$TERMUX_VERSION" | sort -V | tail -1)" = "$TERMUX_MIN" ]; then
        echo "[!] Termux $TERMUX_VERSION 过旧 / too old（需要 / need >= $TERMUX_MIN）"
        echo "    旧版换源后可能无法更新索引 / old builds may fail to update indexes"
        echo "    下载 / download:"
        echo "      https://github.com/termux/termux-app/releases/"
        echo "      https://f-droid.org/zh/packages/com.termux/"
        echo "      https://f-droid.org/en/packages/com.termux/"
        echo "    （不阻断，继续执行 / not fatal, continuing）"
        echo
    fi
fi

# 普通用户能做的事先做（网络、CBOR、打包）
# Do the non-root work first (network, CBOR, packaging)
env PYTHONHOME=$TP PYTHONPATH=$TP/lib/$PYVER \
    "$PYB" "$DIR/rkp_toolkit.py" all --workdir "$OUT" -o "$OUT/keybox.xml"

# 归档信息由 rkp_toolkit.py 打印（成功和失败都会打印），这里不再重复。
# The archive summary is printed by rkp_toolkit.py (on success and failure),
# so there is nothing to repeat here.
