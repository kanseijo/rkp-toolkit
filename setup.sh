#!/data/data/com.termux/files/usr/bin/sh
# ============================================================================
# setup.sh —— RKP 工具链环境自动配置
#             Automatic environment setup for the RKP toolkit
#
# 做的事 / What it does:
#   1. 检测当前网络环境下最快的 Termux 软件源（国内 + 国外）
#      Detect the fastest Termux mirror for the current network (CN + global)
#   2. 备份并设置 sources.list
#      Back up and write sources.list
#   3. 安装依赖（python、cryptography、root-repo）
#      Install dependencies (python, cryptography, root-repo)
#   4. 自检 / self-check
#
# 用法 / Usage:
#   sh setup.sh              # 自动测速并配置 / auto-benchmark and configure
#   sh setup.sh --cn         # 只用国内源 / prefer mainland China mirrors
#   sh setup.sh --global     # 只用国外源 / prefer global mirrors
#   sh setup.sh --mirror URL # 指定源 / use a specific mirror
#   sh setup.sh --dry-run    # 只测速不修改 / benchmark only, change nothing\n  sh setup.sh --repair     # 清理 apt 锁和旧索引 / clear apt locks
#   sh setup.sh --restore    # 恢复备份 / restore the backup
# ============================================================================

set -e

PREFIX_DIR="${PREFIX:-/data/data/com.termux/files/usr}"
SOURCES="$PREFIX_DIR/etc/apt/sources.list"
BACKUP="$SOURCES.rkp.bak"
STAMP=$(date +%Y%m%d-%H%M%S)

# ---- 配色 / colours ----
if [ -t 1 ]; then
    R=$(printf '\033[31m'); G=$(printf '\033[32m'); Y=$(printf '\033[33m')
    B=$(printf '\033[34m'); D=$(printf '\033[2m');  N=$(printf '\033[0m')
else
    R=; G=; Y=; B=; D=; N=
fi

say()  { printf '%s[i]%s %s\n' "$B" "$N" "$*"; }
ok()   { printf '%s[OK]%s %s\n' "$G" "$N" "$*"; }
warn() { printf '%s[!]%s %s\n' "$Y" "$N" "$*"; }
err()  { printf '%s[X]%s %s\n' "$R" "$N" "$*" >&2; }
dim()  { printf '%s    %s%s\n' "$D" "$*" "$N"; }

die() { err "$*"; exit 1; }

# 清理 apt 的锁和陈旧索引 / clear apt locks and stale indexes
repair_apt() {
    say "修复 apt 状态 / repairing apt state"
    rm -f "$PREFIX_DIR/var/lib/apt/lists/lock" 2>/dev/null || true
    rm -f "$PREFIX_DIR/var/cache/apt/archives/lock" 2>/dev/null || true
    rm -f "$PREFIX_DIR/var/lib/dpkg/lock" 2>/dev/null || true
    mkdir -p "$PREFIX_DIR/var/lib/apt/lists/partial" 2>/dev/null || true
    rm -rf "$PREFIX_DIR/var/lib/apt/lists/"*_Packages 2>/dev/null || true
    rm -rf "$PREFIX_DIR/var/lib/apt/lists/partial/"* 2>/dev/null || true
    ok "已清理锁与旧索引 / locks and stale indexes cleared"
}

# ---- 参数解析 / argument parsing ----
MODE=auto
FORCED_MIRROR=""
DRY_RUN=0
DO_RESTORE=0
DO_REPAIR=0

while [ $# -gt 0 ]; do
    case "$1" in
        --cn)       MODE=cn ;;
        --global)   MODE=global ;;
        --mirror)   shift; FORCED_MIRROR="$1" ;;
        --dry-run)  DRY_RUN=1 ;;
        --repair)   DO_REPAIR=1 ;;
        --restore)  DO_RESTORE=1 ;;
        -h|--help)
            sed -n '3,21p' "$0" | sed 's/^# \{0,1\}//'
            exit 0 ;;
        *) die "未知参数 / unknown option: $1" ;;
    esac
    shift
done

# ============================================================================
# 恢复备份 / restore backup
# ============================================================================
# ============================================================================
# 修复 apt 状态 / repair apt state
# ============================================================================
if [ "$DO_REPAIR" = 1 ]; then
    repair_apt
    echo
fi

if [ "$DO_RESTORE" = 1 ]; then
    if [ -f "$BACKUP" ]; then
        cp "$BACKUP" "$SOURCES"
        ok "已恢复备份 / restored: $SOURCES"
    else
        warn "没有备份 / no backup found at $BACKUP"
    fi
    exit 0
fi

command -v curl >/dev/null 2>&1 || die "需要 curl，请先 apt install curl / curl is required"

# ---- 平台检查 / platform guard ----
# 这个脚本只针对 Termux。在普通 Linux 上跑会改错 apt 配置。
# This script targets Termux only; on a normal Linux box it would rewrite the
# wrong apt config, so refuse unless explicitly overridden.
if [ ! -d "$PREFIX_DIR" ] && [ "${RKP_SETUP_FORCE:-0}" != "1" ]; then
    err "这不像 Termux 环境 / this does not look like Termux"
    dim "PREFIX=$PREFIX_DIR 不存在 / does not exist"
    echo
    dim "如果你确实想在别处运行，加 / to force anyway, set:"
    dim "  RKP_SETUP_FORCE=1 sh setup.sh ..."
    exit 1
fi

# ---- 确认是 Termux 的 apt（而非 Debian）----
# Make sure this is Termux's apt, not Debian's.
if command -v apt >/dev/null 2>&1; then
    _apt_bin=$(command -v apt)
    case "$_apt_bin" in
        *termux*|*com.termux*) : ;;
        *)
            if [ "${RKP_SETUP_FORCE:-0}" != "1" ]; then
                warn "apt 位于 $_apt_bin，可能不是 Termux 的 / may not be Termux's apt"
                dim "继续前请确认 / confirm before continuing, or set RKP_SETUP_FORCE=1"
                exit 1
            fi
            ;;
    esac
fi

# ============================================================================
# 1. 镜像列表 / mirror catalogue
#
# 每个条目 / each entry: "地区标签|显示名|基础 URL|说明"
# 基础 URL 指向 apt 根，下面会分别拼 termux-main / termux-root
# The base URL points at the apt root; termux-main / termux-root are appended.
# ============================================================================
MIRRORS="
cn|清华 TUNA|https://mirrors.tuna.tsinghua.edu.cn/termux/apt|
cn|北大 PKU|https://mirrors.pku.edu.cn/termux/apt|
cn|北外 BFSU|https://mirrors.bfsu.edu.cn/termux/apt|
cn|南大 NJU|https://mirror.nju.edu.cn/termux/apt|
cn|山大 SDU|https://mirrors.sdu.edu.cn/termux/apt|
cn|华中科大 HUST|https://mirrors.hust.edu.cn/termux/apt|
cn|中科大 USTC|https://mirrors.ustc.edu.cn/termux/apt|
global|官方 Cloudflare|https://packages-cf.termux.dev/apt|
global|官方源站|https://packages.termux.dev/apt|
"

# ============================================================================
# 2. 测速 / benchmarking
#
# 只请求 Release 文件（几十 KB），改用它测真实连接延迟。
# Only the Release file is fetched (tens of KB); it reflects real latency.
# ============================================================================
bench_one() {
    # $1 = base url
    # 输出 / prints: "<毫秒> <http_code>"，失败返回空
    # main 仓库的 Release 路径 / Release path of the main repo
    _url="$1/termux-main/dists/stable/Release"
    _out=$(curl -s -o /dev/null \
                -w "%{http_code} %{time_total}" \
                -m 10 --connect-timeout 5 \
                "$_url" 2>/dev/null) || return 1
    _code=${_out%% *}
    _time=${_out##* }
    [ "$_code" = "200" ] || return 1
    # 秒 -> 毫秒（整数，避免依赖 bc）
    # seconds -> milliseconds as an integer, without needing bc
    _ms=$(awk "BEGIN{printf \"%d\", $_time*1000}")
    echo "$_ms"
}

say "正在测速 / benchmarking mirrors ..."
say "当前模式 / mode: $MODE"
echo

BEST_TAG=""
BEST_NAME=""
BEST_URL=""
BEST_MS=999999

# 临时结果文件（不依赖 bash 数组，兼容 sh）
# Temp file instead of a bash array, for POSIX sh compatibility.
RESULT_FILE=$(mktemp "${TMPDIR:-/tmp}/rkp_mirrors.XXXXXX") || die "mktemp 失败 / failed"

printf '%s%-4s %-18s %-10s %s%s\n' "$D" "" "镜像 / mirror" "延迟 / RTT" "URL" "$N"

echo "$MIRRORS" | while IFS='|' read -r tag name url _rest; do
    [ -z "$name" ] && continue

    # 模式过滤 / filter by mode
    if [ "$MODE" = "cn" ] && [ "$tag" != "cn" ]; then continue; fi
    if [ "$MODE" = "global" ] && [ "$tag" != "global" ]; then continue; fi

    ms=$(bench_one "$url") || {
        printf '     %s%s%s\n' "$name" "${R}  失败 / unreachable${N}" "$url"
        continue
    }

    # 名称部分按字节宽度补空格（UTF-8 下中文 3 字节，约等于 2 列）
    # Pad by byte width: in UTF-8 a CJK char is 3 bytes, roughly 2 columns.
    _blen=$(printf '%s' "$name" | wc -c | tr -d ' ')
    _pad=$(( 26 - _blen ))
    [ "$_pad" -lt 1 ] && _pad=1
    _sp=$(printf '%*s' "$_pad" '')
    printf '     %s%s%s%5s ms%s   %s\n' "$name" "$_sp" "$G" "$ms" "$N" "$url"
    echo "$ms|$tag|$name|$url" >> "$RESULT_FILE"
done

# 选出最快 / pick the fastest
if [ -s "$RESULT_FILE" ]; then
    # 按延迟升序取第一行 / take the lowest-latency line
    _best=$(sort -n "$RESULT_FILE" | head -1)
    BEST_MS=$(echo "$_best" | cut -d'|' -f1)
    BEST_TAG=$(echo "$_best" | cut -d'|' -f2)
    BEST_NAME=$(echo "$_best" | cut -d'|' -f3)
    BEST_URL=$(echo "$_best" | cut -d'|' -f4)
fi

echo

if [ -n "$FORCED_MIRROR" ]; then
    BEST_URL="$FORCED_MIRROR"
    BEST_NAME="手动指定 / manual"
    BEST_MS="?"
    ok "使用指定源 / using manual mirror: $BEST_URL"
elif [ -z "$BEST_URL" ]; then
    warn "所有镜像均不可用，保持现有配置 / no mirror reachable, keeping current config"
    rm -f "$RESULT_FILE"
    exit 1
else
    ok "最快 / fastest: $BEST_NAME (${BEST_MS} ms)"
fi

# 保留按延迟排序的完整候选表，供失败时逐个回退。
# Keep the full latency-sorted candidate list so we can fall back in order.
CAND_FILE=$(mktemp "${TMPDIR:-/tmp}/rkp_cand.XXXXXX") || die "mktemp 失败 / failed"
if [ -n "$FORCED_MIRROR" ]; then
    echo "?|manual|手动指定 / manual|$FORCED_MIRROR" > "$CAND_FILE"
elif [ -s "$RESULT_FILE" ]; then
    sort -n "$RESULT_FILE" > "$CAND_FILE"
fi
rm -f "$RESULT_FILE"

# ============================================================================
# 3. 写 sources.list / write sources.list
#
# Termux 的两个仓库结构不同 / the two Termux repos differ:
#   main : dists/stable  suite=stable  components=main
#   root : dists/root    suite=root    components=stable
#   x11  : dists/x11     suite=x11     components=stable
# ============================================================================
BEST_BASE="${BEST_URL%/}"

if [ "$DRY_RUN" = 1 ]; then
    echo
    say "预演模式，不修改任何文件 / dry run, nothing is written"
    echo
    say "将写入 $SOURCES 的内容 / would write:"
    cat <<EOF
deb $BEST_BASE/termux-main/ stable main
EOF
    exit 0
fi

echo
say "写入软件源并更新索引 / writing sources.list and updating index"

# 备份原配置（只备份一次，避免覆盖好配置）
# Back up once only, so a working config is never overwritten by a bad one.
if [ -f "$SOURCES" ] && [ ! -f "$BACKUP" ]; then
    # 只备份非本工具生成的配置；如果当前文件就是我们自己写的，
    # 它不是"原始配置"，备份它没有意义。
    # Only back up configs we did not generate. If the current file was written
    # by this script it is not the pristine config, so backing it up is useless.
    if grep -q "rkp-toolkit/setup.sh" "$SOURCES" 2>/dev/null; then
        dim "当前配置由本工具生成，不作为原始备份 / current config is ours, not backing it up"
    else
        cp "$SOURCES" "$BACKUP"
        ok "已备份 / backed up -> $BACKUP"
    fi
fi

ROOT_LIST="$PREFIX_DIR/etc/apt/sources.list.d/root.list"

# 把某个镜像写进 sources.list / write one mirror into sources.list
write_sources() {
    _base="${1%/}"
    _name="$2"
    _ms="$3"
    cat > "$SOURCES" <<EOF
# 由 rkp-toolkit/setup.sh 生成 / generated by rkp-toolkit setup.sh
# 时间 / time : $STAMP
# 镜像 / mirror: $_name ($_ms ms)
# 恢复原始配置 / restore original: sh setup.sh --restore

deb $_base/termux-main/ stable main
EOF
    echo "$_name|$_base|$STAMP" > "$SOURCES.rkp.current" 2>/dev/null || true

    # root-repo 的 drop-in 也要指向同一镜像 / point root-repo at the same mirror
    if [ -f "$ROOT_LIST" ]; then
        [ -f "$ROOT_LIST.rkp.bak" ] || cp "$ROOT_LIST" "$ROOT_LIST.rkp.bak" 2>/dev/null || true
        cat > "$ROOT_LIST" <<EOF
# 由 rkp-toolkit/setup.sh 生成 / generated by rkp-toolkit setup.sh
deb $_base/termux-root/ root stable
EOF
    fi
}

# 恢复到可用状态 / restore to a usable state
#
# 优先用原始备份；没有备份时退回 Termux 官方源，避免把用户留在坏配置上。
# Prefer the pristine backup; without one, fall back to the official Termux
# mirror so the user is never left on a broken config.
restore_sources() {
    if [ -f "$BACKUP" ]; then
        cp "$BACKUP" "$SOURCES"
        ok "已恢复原始配置 / original config restored"
    else
        cat > "$SOURCES" <<EOF
# 由 rkp-toolkit/setup.sh 生成 / generated by rkp-toolkit setup.sh
# 无原始备份，使用官方源 / no pristine backup, using the official mirror
deb https://packages.termux.dev/apt/termux-main/ stable main
EOF
        warn "没有原始备份，已设为官方源 / no backup, set to the official mirror"
    fi
    # root.list 也一并还原 / restore root.list too
    if [ -f "$ROOT_LIST.rkp.bak" ]; then
        cp "$ROOT_LIST.rkp.bak" "$ROOT_LIST"
    fi
}

# apt 输出日志（失败时会打印，否则永远不知道原因）
# Log of apt output, printed on failure; otherwise the reason stays hidden.
APT_LOG_DIR="${TMPDIR:-$PREFIX_DIR/tmp}"
mkdir -p "$APT_LOG_DIR" 2>/dev/null || APT_LOG_DIR="$PREFIX_DIR"
APT_LOG="$APT_LOG_DIR/rkp_apt.log"
# 日志建不出来就丢弃，不能让重定向本身把脚本搞挂
# If the log cannot be created, discard it; a failed redirect must not abort us.
: > "$APT_LOG" 2>/dev/null || APT_LOG=/dev/null

try_index() {
    if pkg update -y >>"$APT_LOG" 2>&1; then return 0; fi
    if apt update >>"$APT_LOG" 2>&1; then return 0; fi
    return 1
}

# 判断失败是否属于"本地问题"（换镜像也没用）
# Decide whether the failure is local, where switching mirrors cannot help.
local_failure_reason() {
    [ -s "$APT_LOG" ] || return 1
    if grep -qiE "could not get lock|unable to lock|is another process" "$APT_LOG"; then
        echo "lock"; return 0
    fi
    if grep -qiE "no space left|not enough space" "$APT_LOG"; then
        echo "disk"; return 0
    fi
    if grep -qiE "temporary failure resolving|could not resolve|name or service not known" "$APT_LOG"; then
        echo "dns"; return 0
    fi
    if grep -qiE "no_pubkey|is not signed|signature verification failed" "$APT_LOG"; then
        echo "keyring"; return 0
    fi
    return 1
}

# 打印 apt 的最后几行 / show the tail of the apt log
show_apt_error() {
    [ -s "$APT_LOG" ] || return 0
    echo
    warn "apt 输出 / apt output（最后 12 行 / last 12 lines）:"
    tail -12 "$APT_LOG" 2>/dev/null | while IFS= read -r _l; do
        printf '    %s\n' "$_l"
    done
}


# 依赖是否已经齐全（齐全时即使索引失败也能继续）
# Are the dependencies already present? If so we can continue even when the
# index refresh fails.
deps_ready() {
    command -v python3 >/dev/null 2>&1 || return 1
    python3 -c "import cryptography" >/dev/null 2>&1 || return 1
    return 0
}

INDEX_OK=0
TRIED=0
LOCAL_ISSUE=""

while IFS='|' read -r _cms _ctag _cname _curl; do
    [ -z "$_cname" ] && continue
    TRIED=$((TRIED + 1))

    say "尝试 / trying: $_cname"
    write_sources "$_curl" "$_cname" "$_cms"

    if try_index; then
        INDEX_OK=1
        BEST_NAME="$_cname"
        BEST_URL="$_curl"
        BEST_MS="$_cms"
        ok "索引已更新 / index updated（$_cname）"
        break
    fi

    # 本地问题（锁/DNS/签名/磁盘）换镜像也没用，立即停
    # A local problem (lock/DNS/signature/disk) is not fixed by another mirror.
    _local=$(local_failure_reason)
    if [ -n "$_local" ]; then
        LOCAL_ISSUE="$_local"
        warn "本地问题（$_local），停止换源 / local problem, not switching further"
        break
    fi

    warn "$_cname 更新失败，换下一个 / failed, trying the next mirror"
done < "$CAND_FILE"

rm -f "$CAND_FILE"

if [ "$INDEX_OK" != 1 ]; then
    echo
    if deps_ready; then
        # 依赖已齐，索引失败不影响本次任务
        # Dependencies are already satisfied, so a failed refresh is not fatal.
        warn "所有镜像索引都失败，但依赖已齐全，继续 / all mirrors failed but deps are present"
        restore_sources
    else
        if [ -n "$LOCAL_ISSUE" ]; then
            err "apt 失败原因是本地问题 / apt failed for a local reason: $LOCAL_ISSUE"
        else
            err "所有镜像都不行（试了 $TRIED 个）/ every mirror failed ($TRIED tried)"
        fi

        # 把 apt 的真实输出打出来，否则无从排查
        # Show apt's real output; otherwise there is nothing to diagnose.
        show_apt_error

        echo
        restore_sources
        echo

        case "$LOCAL_ISSUE" in
            lock)
                say "锁被占用 / a lock is held. 修复 / fix:"
                dim "sh setup.sh --repair"
                dim "或 / or: 关掉其他 pkg/apt 进程后重试"
                ;;
            disk)
                say "磁盘空间不足 / out of disk space:"
                dim "df -h \$PREFIX"
                dim "清理 / clean: apt clean"
                ;;
            dns)
                say "域名解析失败 / DNS resolution failed:"
                dim "检查网络 / check network, 或换 DNS"
                ;;
            keyring)
                say "GPG 签名校验失败 / signature check failed:"
                dim "termux-change-repo    # 官方工具通常能顺带修好"
                ;;
            *)
                say "可以手动排查 / investigate manually:"
                dim "sh setup.sh --repair       # 清理锁和旧索引 / clear locks"
                dim "sh setup.sh --global       # 再试国外源 / try global mirrors"
                dim "sh setup.sh --mirror URL   # 指定某个源 / force a mirror"
                dim "termux-change-repo         # Termux 官方换源工具 / official tool"
                dim "apt update                 # 手动看完整报错 / see full error"
                ;;
        esac
        echo
        dim "当前配置 / current config: $SOURCES"

        # 最后手段：用 pip 装 cryptography（不依赖 apt）
        # Last resort: install cryptography with pip, bypassing apt.
        if command -v python3 >/dev/null 2>&1 && command -v pip >/dev/null 2>&1; then
            echo
            say "尝试用 pip 直接安装 cryptography / trying pip instead of apt"
            if pip install --upgrade cryptography >/dev/null 2>&1 \
               && python3 -c "import cryptography" >/dev/null 2>&1; then
                ok "pip 安装成功，继续 / installed via pip, continuing"
                INDEX_OK=1
            else
                warn "pip 也不行 / pip failed too"
            fi
        fi

        [ "$INDEX_OK" = 1 ] || exit 1
    fi
fi

echo
say "安装依赖 / installing dependencies"

# ---- python ----
if command -v python3 >/dev/null 2>&1; then
    ok "python3 已存在 / already present: $(python3 -V 2>&1)"
else
    say "安装 python / installing python ..."
    pkg install -y python || die "python 安装失败 / python install failed"
    ok "python 已安装 / installed"
fi

# ---- cryptography ----
if python3 -c "import cryptography" >/dev/null 2>&1; then
    ok "cryptography 已存在 / already present: $(python3 -c 'import cryptography;print(cryptography.__version__)')"
else
    say "安装 cryptography / installing cryptography ..."
    # Termux 上必须用 pkg，不能用 pip（pip 装的轮子链接不到 libssl）
    # On Termux use pkg, not pip: pip wheels cannot link against Termux's libssl.
    if pkg install -y python-cryptography >/dev/null 2>&1; then
        ok "已通过 pkg 安装 / installed via pkg"
    else
        warn "pkg 里没有 python-cryptography，回退 pip / falling back to pip"
        pip install --upgrade cryptography \
            || die "cryptography 安装失败 / install failed"
    fi
fi

# ---- curl（测速和网络请求用）/ curl, used for benchmarking and requests ----
if command -v curl >/dev/null 2>&1; then
    :
else
    say "安装 curl / installing curl ..."
    pkg install -y curl >/dev/null 2>&1 || warn "curl 安装失败 / curl install failed"
fi

# ---- root ----
echo
if [ "$(id -u)" = "0" ]; then
    ok "已是 root / already root"
else
    _su=""
    for c in /system/bin/su /system/xbin/su /debug_ramdisk/su /sbin/su \
             /data/adb/ksu/bin/su /data/adb/magisk/su; do
        [ -x "$c" ] && { _su="$c"; break; }
    done
    if [ -n "$_su" ]; then
        ok "root 可用 / available: $_su"
    else
        warn "找不到真正的 su / no real su found"
        dim "本工具引导阶段不需要 root，但生成 CSR 时必须要有。"
        dim "Root is not needed to set up, but is required to generate the CSR."
        dim "请安装 Magisk / KernelSU / APatch 后重试。"
        dim "Install Magisk, KernelSU or APatch and retry."
    fi
fi

# ============================================================================
# 5. 自检 / self-check
# ============================================================================
echo
say "环境自检 / self-check"

_ok=1

if python3 -c "import cryptography" >/dev/null 2>&1; then
    ok "cryptography $(python3 -c 'import cryptography;print(cryptography.__version__)')"
else
    err "cryptography 不可用 / unavailable"
    _ok=0
fi

if command -v curl >/dev/null 2>&1; then
    ok "curl $(curl --version 2>/dev/null | head -1 | awk '{print $2}')"
else
    err "curl 不可用 / unavailable"
    _ok=0
fi

# 到 Google RKP 服务的连通性 / reachability of the Google RKP service
say "测试 RKP 服务器连通性 / testing RKP server reachability"
_rtt=$(curl -s -o /dev/null -w "%{http_code} %{time_total}" -m 12 \
       -X POST \
       -H "Content-Type: application/cbor" \
       --data-binary @/dev/null \
       "https://remoteprovisioning.googleapis.com/v1:fetchEekChain" 2>/dev/null) || _rtt=""
if [ -n "$_rtt" ]; then
    _code=${_rtt%% *}; _t=${_rtt##* }
    # 400/403 都说明网络通（只是请求体不合法）
    # A 400/403 still means the network path works (only the body was invalid).
    case "$_code" in
        2*|4*) ok "RKP 服务器可达 / reachable (HTTP $_code, ${_t}s)" ;;
        *)     warn "RKP 服务器返回 HTTP $_code，网络可能受限 / network may be restricted" ;;
    esac
else
    warn "无法连接 RKP 服务器 / cannot reach the RKP server"
    dim "检查网络或代理 / check your network or proxy"
fi

echo
if [ "$_ok" = 1 ]; then
    ok "环境就绪 / environment ready"
    echo
    say "下一步 / next step:"
    dim "sh issue.sh          # 申请证书并生成 keybox.xml"
else
    err "还有依赖缺失，请检查上面的错误 / some dependencies are missing"
    exit 1
fi
