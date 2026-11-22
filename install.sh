#!/bin/sh
# ============================================================================
# rkp-toolkit 一键安装脚本 / one-click installer
#
# 用法 / Usage:
#   bash <(curl -L https://raw.githubusercontent.com/kanseijo/rkp-toolkit/main/install.sh)
#
# 国内网络 / behind a slow link to GitHub:
#   bash <(curl -L https://gh-proxy.org/https://raw.githubusercontent.com/kanseijo/rkp-toolkit/main/install.sh)
#
# 可选环境变量 / Optional environment variables:
#   RKP_DIR=/path     安装到指定目录 / install into this directory (default: $HOME)
#   RKP_PROXY=1       强制走加速代理 / force the proxy
#   RKP_PROXY=0       禁用代理 / never use the proxy
#   RKP_KEEP=1        保留下载的 zip / keep the downloaded zip
# ============================================================================
set -u

REPO="kanseijo/rkp-toolkit"
BRANCH="main"
ZIP_NAME="rkp-toolkit.zip"
SUMS_NAME="SHA256SUMS"
DIR_NAME="rkp-toolkit"

GH="https://github.com"
RAW="https://raw.githubusercontent.com"
PROXY="https://gh-proxy.org/"

DEST="${RKP_DIR:-$HOME}"
PROXY_MODE="${RKP_PROXY:-auto}"
KEEP="${RKP_KEEP:-0}"

# 低于这个速度就走代理 / below this speed the proxy is used
SLOW_BPS=30000

# ----------------------------------------------------------------------------
# 输出 / output
# ----------------------------------------------------------------------------
c_r=''; c_g=''; c_y=''; c_0=''
if [ -t 1 ]; then
    c_r=$(printf '\033[31m'); c_g=$(printf '\033[32m')
    c_y=$(printf '\033[33m'); c_0=$(printf '\033[0m')
fi

say()  { printf '%s\n' "$*"; }
ok()   { printf '%s[OK]%s %s\n' "$c_g" "$c_0" "$*"; }
warn() { printf '%s[!]%s %s\n' "$c_y" "$c_0" "$*"; }
err()  { printf '%s[X]%s %s\n' "$c_r" "$c_0" "$*" >&2; }
die()  { err "$*"; exit 1; }

have() { command -v "$1" >/dev/null 2>&1; }

# 尝试用 pkg 装缺失的工具 / try installing a missing tool with pkg
ensure_tool() {
    _bin="$1"; _pkg="${2:-$1}"
    have "$_bin" && return 0
    if have pkg; then
        warn "缺少 / missing: $_bin — 尝试安装 / trying: pkg install $_pkg"
        pkg install -y "$_pkg" >/dev/null 2>&1 || true
    fi
    have "$_bin"
}

# ----------------------------------------------------------------------------
# 测速 / speed probe
#   下载前 256 KB 并测量速度，单位字节/秒；失败返回 0
#   Fetch the first 256 KB and measure bytes/sec; 0 on failure.
# ----------------------------------------------------------------------------
probe_bps() {
    _url="$1"
    _out=0
    if have curl; then
        # -L 必须加：release 资源会 302 到 objects.githubusercontent.com
        # -L is required: release assets 302 to objects.githubusercontent.com
        _out=$(curl -sSL -o /dev/null --max-time 12 -r 0-262143 \
                    -w '%{speed_download}' "$_url" 2>/dev/null) || _out=0
    elif have wget; then
        _tmp=$(mktemp "${TMPDIR:-/tmp}/rkp_probe.XXXXXX") || return 0
        _t0=$(date +%s)
        wget -q -O "$_tmp" --timeout=12 --tries=1 "$_url" 2>/dev/null || true
        _t1=$(date +%s)
        _sz=$(wc -c < "$_tmp" 2>/dev/null | tr -d ' ')
        _dt=$((_t1 - _t0)); [ "$_dt" -lt 1 ] && _dt=1
        _out=$(( ${_sz:-0} / _dt ))
        rm -f "$_tmp"
    fi
    # 转成整数 / coerce to an integer
    printf '%.0f' "${_out:-0}" 2>/dev/null || printf '0'
}

# ----------------------------------------------------------------------------
# 下载 / download
# ----------------------------------------------------------------------------
download() {
    _url="$1"; _dst="$2"
    if have curl; then
        curl -fL --retry 2 --connect-timeout 15 -o "$_dst" "$_url"
    else
        wget -O "$_dst" --timeout=30 --tries=2 "$_url"
    fi
}

# ----------------------------------------------------------------------------
# 开始 / start
# ----------------------------------------------------------------------------
say ""
say "=========================================="
say " rkp-toolkit 一键安装 / one-click installer"
say "=========================================="
say ""

ensure_tool curl || ensure_tool wget || die "需要 curl 或 wget / curl or wget required"
ensure_tool unzip  || die "需要 unzip / unzip required"

ZIP_URL="$GH/$REPO/releases/latest/download/$ZIP_NAME"
SUMS_URL="$GH/$REPO/releases/latest/download/$SUMS_NAME"

# ---- 1. 测速并决定是否走代理 / probe and decide on the proxy ----
CHOSEN=""
case "$PROXY_MODE" in
    1|yes|on)
        CHOSEN="$PROXY"
        ok "已强制使用加速 / proxy forced"
        ;;
    0|no|off)
        CHOSEN=""
        ok "已禁用代理 / proxy disabled"
        ;;
    *)
        say "[i] 检测 GitHub 访问速度 / probing GitHub..."
        BPS=$(probe_bps "$ZIP_URL")
        say "    直连速度 / direct speed: $BPS B/s"
        if [ "${BPS:-0}" -ge "$SLOW_BPS" ]; then
            ok "直连够快，直接下载 / direct is fast enough"
            CHOSEN=""
        else
            warn "直连过慢（< ${SLOW_BPS} B/s），改用加速 / too slow, using proxy"
            CHOSEN="$PROXY"
        fi
        ;;
esac

FINAL_URL="${CHOSEN}${ZIP_URL}"
FINAL_SUMS="${CHOSEN}${SUMS_URL}"

# ---- 2. 下载 / download ----
say ""
say "[i] 下载 / downloading:"
say "    $FINAL_URL"

TMPD=$(mktemp -d "${TMPDIR:-/tmp}/rkp_install.XXXXXX") || die "mktemp 失败 / failed"
trap 'rm -rf "$TMPD"' EXIT

ZIP_PATH="$TMPD/$ZIP_NAME"

if ! download "$FINAL_URL" "$ZIP_PATH"; then
    err "下载失败 / download failed"
    if [ -n "$CHOSEN" ]; then
        warn "代理也失败，试试直连 / proxy failed too, retrying direct"
        download "$ZIP_URL" "$ZIP_PATH" || die "两种方式都失败 / both failed"
    else
        warn "直连失败，试试加速 / direct failed, retrying with proxy"
        download "${PROXY}${ZIP_URL}" "$ZIP_PATH" || die "两种方式都失败 / both failed"
    fi
fi

[ -s "$ZIP_PATH" ] || die "下载的文件为空 / downloaded file is empty"
ok "已下载 / downloaded: $(wc -c < "$ZIP_PATH" | tr -d ' ') bytes"

# ---- 3. 校验（可选）/ verify (optional) ----
if download "$FINAL_SUMS" "$TMPD/$SUMS_NAME" 2>/dev/null && have sha256sum; then
    if ( cd "$TMPD" && sha256sum -c "$SUMS_NAME" >/dev/null 2>&1 ); then
        ok "SHA256 校验通过 / checksum OK"
    else
        warn "校验未通过，继续安装 / checksum mismatch, continuing"
    fi
else
    warn "跳过校验 / checksum skipped"
fi

# ---- 4. 解压（覆盖冲突）/ unpack, overwriting conflicts ----
mkdir -p "$DEST" || die "无法创建目录 / cannot create: $DEST"

say ""
say "[i] 解压到 / unpacking into: $DEST"
if ! ( cd "$DEST" && unzip -q -o "$ZIP_PATH" ); then
    die "解压失败 / unzip failed"
fi

TARGET="$DEST/$DIR_NAME"
[ -d "$TARGET" ] || die "解压后找不到 $DIR_NAME / $DIR_NAME missing after unpack"
ok "已安装到 / installed at: $TARGET"

# ---- 5. 收尾 / finish ----
if [ "$KEEP" != "1" ]; then
    rm -f "$ZIP_PATH"
fi

say ""
say "=========================================="
say " 安装完成 / installation complete"
say "=========================================="
say ""
say " 目录 / directory:"
say "   $TARGET"
say ""
say " 下一步，复制这一行执行 / next, copy and run this single line:"
say ""
# 安装在家目录时给出最短的形式；否则给出绝对路径
# Shortest form when installed in $HOME, absolute path otherwise.
if [ "$DEST" = "$HOME" ]; then
    say "   cd ~ && cd $DIR_NAME && sh issue.sh"
else
    say "   cd $TARGET && sh issue.sh"
fi
say ""
say "------------------------------------------"
say " 提示 / notes"
say "------------------------------------------"
say " · 需要 root，首次运行会自动配置依赖"
say "   root is required; dependencies are set up on first run"
say " · 产物在批次目录里 / output lands in a dated batch folder:"
say "   ~/rkp_out/YYYY-MM-DD_NNNN/keybox.xml"
say " · keybox.xml 含私钥，切勿公开 / contains a private key, never publish it"
say " · 刷过 TEE 的设备很可能失败 / reflashed TEE usually breaks RKP"
say ""
