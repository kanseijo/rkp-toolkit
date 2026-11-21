#!/usr/bin/env python3
"""
rkp_toolkit.py —— 一站式 RKP 签发工具链
              One-stop RKP provisioning toolkit

用法（设备上，Termux + root）/ Usage (on device, Termux + root):
    python3 rkp_toolkit.py all
    # 产出 ./rkp_out/keybox.xml
    # produces ./rkp_out/keybox.xml

分步 / Step by step:
    python3 rkp_toolkit.py check      # 环境自检 / self-check environment
    python3 rkp_toolkit.py probe      # 探测 RKP HAL / probe the RKP HAL
    python3 rkp_toolkit.py issue      # 申请并拿到证书链 / request and fetch chain
    python3 rkp_toolkit.py keybox     # 转成 keybox.xml / convert to keybox.xml
    python3 rkp_toolkit.py verify     # 只验证已有产物 / verify existing output

设计原则 / Design principles:
  * 单文件，只依赖 cryptography / single file, only depends on cryptography
  * 设备侧需要 rkp_bridge.jar（同目录），用 app_process 调 AIDL
    / the device needs rkp_bridge.jar (same directory); it calls AIDL via app_process
  * 所有中间产物留在 --workdir，可重跑
    / all intermediates stay in --workdir so runs can be repeated

协议流程 / Protocol flow:
    A. POST /v1:fetchEekChain  -> challenge + EEK 链 / EEK chain
    B. app_process 调 / calls generateCertificateRequest(V2) -> CSR + 私钥 / key
    C. POST /v1:signCertificates -> Google 签发的证书链 / chain signed by Google
    D. 打包成 keybox.xml（SEC1 私钥 + 叶子在前）
       / package into keybox.xml (SEC1 key + leaf first)
"""
import argparse
import atexit
import base64
import hashlib
import json
import os
import re
import shlex
import shutil
import struct
import subprocess
import sys
import time
import urllib.error
import urllib.request

# ---------------------------------------------------------------- 常量 / Constants

SERVER = "https://remoteprovisioning.googleapis.com"
# A13 的 RKP binder 服务名 / the A13 RKP binder service name
A13_RKP_SERVICE = "android.security.remoteprovisioning"
# HAL 层服务名，A13 / A14 都一样 / the HAL-level name, identical on A13 and A14
HAL_SERVICE = "android.hardware.security.keymint.IRemotelyProvisionedComponent/default"
RPC_PREFIX = "/v1"   # gRPC 风格路径: /v1:fetchEekChain
DEFAULT_FP = ("google/sdk_gphone64_arm64/emu64a:13/TQ3A.230805.001/10316531:"
              "userdebug/dev-keys")

# app_process 候选路径（不同 ROM 位置不同）
# Candidate app_process paths (they differ between ROMs)
APP_PROCESS_CANDIDATES = [
    "/system/bin/app_process64",
    "/system/bin/app_process",
    "/apex/com.android.art/bin/app_process64",
    "/apex/com.android.art/bin/app_process",
]

# RKP HAL 的 AIDL 类打包在系统 APK 里
# The RKP HAL's AIDL classes are packaged inside a system APK
RKP_APK_CANDIDATES = [
    "/apex/com.android.rkpd/priv-app/rkpdapp/rkpdapp.apk",
]

# ---------------------------------------------------------------- 工具函数 / Utilities

def say(msg, kind="i"):
    mark = {"i": "[i]", "ok": "[OK]", "warn": "[!]", "err": "[X]"}.get(kind, "[i]")
    print(f"{mark} {msg}", flush=True)


def die(msg):
    say(msg, "err")
    sys.exit(1)


def find_su():
    """Termux 自带一个假 su（只打印提示），必须找真正的。
    Termux ships a fake su (it only prints a notice), so find the real one.
    """
    if os.geteuid() == 0:
        return None
    for c in ["/system/bin/su", "/system/xbin/su", "/debug_ramdisk/su", "/sbin/su",
              "/data/adb/ksu/bin/su", "/data/adb/magisk/su"]:
        if os.path.isfile(c) and os.access(c, os.X_OK):
            return c
    p = shutil.which("su")
    if p and not p.startswith("/data/data/com.termux"):
        return p
    return None


# 上一次 run_root 的 stderr（供失败时打印诊断）
# stderr of the last run_root call (printed for diagnostics on failure)
LAST_STDERR = ""
LAST_RC = 0


def run_root(cmd, timeout=60):
    """以 root 执行命令。已经是 root 就直接跑。
    Run a command as root. If already root, run it directly.

    副作用：把 stderr 和退出码存到 LAST_STDERR / LAST_RC。
    Side effect: stores stderr and the exit code in LAST_STDERR / LAST_RC.
    """
    global LAST_STDERR, LAST_RC
    su = find_su()
    if su is None:
        if os.geteuid() != 0:
            die("需要 root，但找不到真正的 su / need root but no real su found")
        r = subprocess.run(["/system/bin/sh", "-c", cmd],
                           capture_output=True, text=True, timeout=timeout)
    else:
        r = subprocess.run([su, "-c", cmd], capture_output=True, text=True, timeout=timeout)
    LAST_STDERR = (r.stderr or "").strip()
    LAST_RC = r.returncode
    return (r.stdout or "").strip()


def find_app_process():
    for p in APP_PROCESS_CANDIDATES:
        if os.path.isfile(p):
            return p
    return None


def find_rkp_a13_service():
    """Android 13 上没有独立的 RKP APK。

    Android 13 has no standalone RKP APK.

    A13 的 keystore2 自己向 servicemanager 注册了
    "android.security.remoteprovisioning"（见 AOSP
    system/security/keystore2/src/keystore2_main.rs:37 的
    REMOTE_PROVISIONING_SERVICE_NAME），RKP 的 binder 就在这个服务里，
    不来自任何 APK。A14+ 才拆成 rkpdapp APK + RKPD 模块。

    A13's keystore2 registers "android.security.remoteprovisioning" with the
    servicemanager itself (AOSP
    system/security/keystore2/src/keystore2_main.rs:37). The RKP binder lives in
    that service, not in any APK. Only A14+ split it into the rkpdapp APK plus
    the RKPD mainline module.

    返回服务名或在错误时返回 None。
    Returns the service name, or None on error.
    """
    # su -c 的 PATH 往往很精简，service 可能不在里面，所以用绝对路径并容错。
    # A su shell usually has a minimal PATH, so refer to `service` by absolute
    # path and tolerate failures.
    candidates = [
        "/system/bin/service list",
        "service list",
        "/system/bin/dumpsys -l",   # 静态服务列表，也能看到名字
    ]
    for c in candidates:
        try:
            out = run_root(c + " 2>/dev/null", timeout=25)
        except Exception:
            continue
        if out and A13_RKP_SERVICE in out:
            return A13_RKP_SERVICE

    # 退一步：直接问 servicemanager 里有没有 HAL 服务本身。
    # service / vndservice 的 check 输出固定是
    # "Service <name>: found" 或 "Service <name>: not found"
    # （AOSP frameworks/native/cmds/service/service.cpp）。
    # Fall back to asking the servicemanager directly. The check output is
    # always "Service <name>: found" or "Service <name>: not found".
    #
    # vndservice 走 /dev/vndbinder，vendor AIDL HAL 可能只在那里注册。
    # vndservice talks to /dev/vndbinder, where vendor AIDL HALs may register.
    for tool in ("/system/bin/service", "/system/bin/vndservice"):
        try:
            out = run_root(f"{tool} check {HAL_SERVICE} 2>&1", timeout=25)
        except Exception:
            continue
        low = (out or "").lower()
        if "not found" in low:
            continue
        if ": found" in low:
            return HAL_SERVICE
    return None


def probe_rkp_backend():
    """收集 RKP 后端诊断信息（自检失败时打印）。
    Collect RKP backend diagnostics (printed when the self-check fails).
    """
    info = []

    # APEX 里有没有 rkpd（A14+）
    # Is rkpd present in the APEX set (A14+)?
    try:
        apex = run_root("ls /apex 2>/dev/null", timeout=20)
    except Exception:
        apex = ""
    rkpd_apex = [x for x in apex.splitlines() if "rkpd" in x.lower()]
    info.append(("A14+ APEX", rkpd_apex or "无 / none"))

    # 服务列表里跟 RKP 有关的项
    # RKP-related entries in the service list
    svc_hits = []
    for c in ("/system/bin/service list", "service list", "/system/bin/dumpsys -l"):
        try:
            out = run_root(c + " 2>/dev/null", timeout=25)
        except Exception:
            continue
        for line in (out or "").splitlines():
            low = line.lower()
            if any(k in low for k in ("remotepro", "rkp", "keymint")):
                svc_hits.append(line.strip())
        if svc_hits:
            break
    info.append(("服务 / services", svc_hits or "无匹配 / no match"))

    # HAL 服务本身在不在（两个 binder 域都查）
    # Is the HAL service present? Check both binder domains.
    for tool in ("/system/bin/service", "/system/bin/vndservice"):
        try:
            raw = run_root(f"{tool} check {HAL_SERVICE} 2>&1", timeout=25)
        except Exception:
            raw = ""
        label = "HAL check (binder)" if tool.endswith("/service") else "HAL check (vndbinder)"
        info.append((label, raw.strip()[:200] or "无输出 / no output"))

    # keystore2 在不在跑
    # Is keystore2 running?
    try:
        ps = run_root("ps -A 2>/dev/null | grep -i keystore2 | grep -v grep", timeout=20)
    except Exception:
        ps = ""
    info.append(("keystore2", ps.strip().splitlines()[:2] or "未运行 / not running"))

    return info


def find_rkp_apk():
    """定位包含 IRemotelyProvisionedComponent 的 APK。
    Locate the APK that contains IRemotelyProvisionedComponent.
    """
    out = run_root("ls -d /apex/com.android.rkpd*/priv-app/* 2>/dev/null")
    for line in out.splitlines():
        line = line.strip()
        if not line:
            continue
        apks = run_root(f"ls {line}/*.apk 2>/dev/null")
        for a in apks.splitlines():
            a = a.strip()
            if a:
                return a
    return None


def http_get(url, timeout=30):
    req = urllib.request.Request(url, headers={"User-Agent": "rkp-toolkit/1.0"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.read()


def http_post(url, data, timeout=60):
    req = urllib.request.Request(url, data=data or b"",
                                 headers={"Content-Type": "application/cbor",
                                          "User-Agent": "rkp-toolkit/1.0"},
                                 method="POST")
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.read()


# ---------------------------------------------------------------- CBOR（最小集 / minimal subset）

def cbor_head(major, n):
    if n < 24:
        return bytes([(major << 5) | n])
    if n < 0x100:
        return bytes([(major << 5) | 24, n])
    if n < 0x10000:
        return bytes([(major << 5) | 25]) + struct.pack(">H", n)
    if n < 0x100000000:
        return bytes([(major << 5) | 26]) + struct.pack(">I", n)
    return bytes([(major << 5) | 27]) + struct.pack(">Q", n)


def cbor_dumps(o):
    if o is True:
        return b"\xf5"
    if o is False:
        return b"\xf4"
    if o is None:
        return b"\xf6"
    if isinstance(o, int):
        return cbor_head(0, o) if o >= 0 else cbor_head(1, -1 - o)
    if isinstance(o, bytes):
        return cbor_head(2, len(o)) + o
    if isinstance(o, str):
        b = o.encode()
        return cbor_head(3, len(b)) + b
    if isinstance(o, (list, tuple)):
        return cbor_head(4, len(o)) + b"".join(cbor_dumps(x) for x in o)
    if isinstance(o, dict):
        out = cbor_head(5, len(o))
        for k, v in o.items():
            out += cbor_dumps(k) + cbor_dumps(v)
        return out
    raise TypeError(f"无法编码 {type(o)}")


def _head(b, off):
    ib = b[off]
    major, ai = ib >> 5, ib & 0x1F
    off += 1
    if ai < 24:
        return major, ai, off
    if ai == 24:
        return major, b[off], off + 1
    if ai == 25:
        return major, int.from_bytes(b[off:off+2], "big"), off + 2
    if ai == 26:
        return major, int.from_bytes(b[off:off+4], "big"), off + 4
    if ai == 27:
        return major, int.from_bytes(b[off:off+8], "big"), off + 8
    if ai == 31:
        return major, None, off
    raise ValueError(f"CBOR ai={ai} 不支持")


def cbor_loads(b, off=0):
    major, val, off = _head(b, off)
    if major == 0:
        return val, off
    if major == 1:
        return -1 - val, off
    if major == 2:
        if val is None:
            out = b""
            while b[off] != 0xFF:
                c, off = cbor_loads(b, off)
                out += c
            return out, off + 1
        return b[off:off+val], off + val
    if major == 3:
        if val is None:
            out = ""
            while b[off] != 0xFF:
                c, off = cbor_loads(b, off)
                out += c
            return out, off + 1
        return b[off:off+val].decode("utf-8", "replace"), off + val
    if major == 4:
        items = []
        if val is None:
            while b[off] != 0xFF:
                c, off = cbor_loads(b, off)
                items.append(c)
            return items, off + 1
        for _ in range(val):
            c, off = cbor_loads(b, off)
            items.append(c)
        return items, off
    if major == 5:
        m = {}
        if val is None:
            while b[off] != 0xFF:
                k, off = cbor_loads(b, off)
                v, off = cbor_loads(b, off)
                m[k] = v
            return m, off + 1
        for _ in range(val):
            k, off = cbor_loads(b, off)
            v, off = cbor_loads(b, off)
            m[k] = v
        return m, off
    if major == 6:
        c, off = cbor_loads(b, off)
        return c, off
    if major == 7:
        if val == 20:
            return False, off
        if val == 21:
            return True, off
        if val in (22, 23):
            return None, off
    raise ValueError(f"CBOR major={major} val={val} 不支持")


# ---------------------------------------------------------------- 证书工具 / Certificate helpers

def der_certs(blob):
    """切出一段 DER 流里所有顶层 SEQUENCE。
    Split a DER stream into all of its top-level SEQUENCEs.
    """
    out, off, n = [], 0, len(blob)
    while off < n:
        if blob[off] != 0x30:
            break
        start = off
        off += 1
        first = blob[off]
        off += 1
        if first & 0x80:
            k = first & 0x7F
            length = int.from_bytes(blob[off:off+k], "big")
            off += k
        else:
            length = first
        out.append(blob[start:off+length])
        off += length
    return out


def collect_from_cbor(obj, out):
    if isinstance(obj, bytes):
        if obj[:1] == b"\x30":
            out.extend(der_certs(obj))
    elif isinstance(obj, list):
        for x in obj:
            collect_from_cbor(x, out)


def load_certs(blob):
    from cryptography import x509
    from cryptography.hazmat.backends import default_backend
    ders = []
    if blob[:1] != b"\x30":
        try:
            obj, _ = cbor_loads(blob, 0)
            collect_from_cbor(obj, ders)
        except Exception:
            ders = []
    if not ders:
        ders = der_certs(blob)
    certs = []
    for d in ders:
        try:
            certs.append(x509.load_der_x509_certificate(d, default_backend()))
        except Exception:
            pass
    return certs


def order_chain(certs):
    """叶子 -> 根。 / leaf -> root."""
    if len(certs) <= 1:
        return certs
    issuer_set = {c.issuer.public_bytes() for c in certs}
    leaves = [c for c in certs if c.subject.public_bytes() not in issuer_set]
    start = leaves[0] if len(leaves) == 1 else certs[0]
    ordered, remaining = [start], [c for c in certs if c is not start]
    while remaining:
        cur = ordered[-1]
        nxt = next((c for c in remaining
                    if c.subject.public_bytes() == cur.issuer.public_bytes()), None)
        if nxt is None:
            break
        ordered.append(nxt)
        remaining.remove(nxt)
    ordered.extend(remaining)
    return ordered


# ---------------------------------------------------------------- 时间兼容
# Datetime compatibility

def _cert_time(cert, which):
    """取证书的生效/失效时间，返回带时区的 datetime。
    Return a certificate's validity bound as a timezone-aware datetime.

    cryptography 42+ 提供 not_valid_before_utc / not_valid_after_utc，
    旧版本只有 naive 的 not_valid_before / not_valid_after（用它们会触发
    CryptographyDeprecationWarning）。这里优先用新属性，并兼容旧版本。

    cryptography 42+ exposes not_valid_before_utc / not_valid_after_utc; older
    versions only have the naive variants, which emit a deprecation warning.
    Prefer the new attributes and stay compatible with the old ones.

    which: "before" 或 "after" / either "before" or "after"
    """
    import datetime as _dt

    utc_attr = f"not_valid_{which}_utc"
    naive_attr = f"not_valid_{which}"

    v = getattr(cert, utc_attr, None)
    if v is None:
        v = getattr(cert, naive_attr)
    if v.tzinfo is None:
        v = v.replace(tzinfo=_dt.timezone.utc)
    return v


# ---------------------------------------------------------------- 各阶段 / Stages

def stage_check():
    say("环境自检 / self-check")
    problems = []

    # Python 解释器 / interpreter
    say(f"Python {sys.version.split()[0]}")

    # cryptography 库 / library
    try:
        import cryptography
        say(f"cryptography {cryptography.__version__}", "ok")
    except ImportError:
        problems.append("缺少 cryptography / missing cryptography\n"
                        "    运行 / run: sh setup.sh")

    # root 权限 / root access
    su = find_su()
    if os.geteuid() == 0:
        say("root: 已是 root / already root", "ok")
    elif su:
        say(f"root: {su}", "ok")
    else:
        problems.append("找不到真正的 su（Termux 自带的 su 是假的）")

    # app_process
    ap = find_app_process()
    if ap:
        say(f"app_process: {ap}", "ok")
    else:
        problems.append("找不到 app_process")

    # jar
    jar = os.path.join(os.path.dirname(os.path.abspath(__file__)), "rkp_bridge.jar")
    if os.path.isfile(jar):
        say(f"rkp_bridge.jar: {os.path.getsize(jar)} bytes", "ok")
    else:
        problems.append(f"缺少 {jar}")

    # RKP 后端：A14+ 是 APK，A13 是 keystore2 内建服务
    # RKP backend: an APK on A14+, a keystore2 built-in service on A13
    if not problems:
        apk = find_rkp_apk()
        if apk:
            say(f"RKP APK: {apk}", "ok")
        else:
            svc = find_rkp_a13_service()
            if svc:
                say(f"RKP 服务 / service: {svc}（Android 13 keystore2 内建 / "
                    f"built into keystore2）", "ok")
            else:
                problems.append(
                    "找不到 RKP 后端 / no RKP backend found\n"
                    "    已检查 / checked:\n"
                    "      - APK: /apex/com.android.rkpd*/priv-app/*/*.apk (A14+)\n"
                    f"      - 服务 / service: {A13_RKP_SERVICE} (A13)\n"
                    f"      - HAL: {HAL_SERVICE}\n"
                    "\n"
                    "    —— 实际探测结果 / actual probe results ——")
                # 附上真实探测数据，避免只报"没找到"而无从下手
                # Attach real probe data instead of just "not found".
                for label, val in probe_rkp_backend():
                    if isinstance(val, list):
                        val = "\n        ".join(val) if val else "空 / empty"
                    problems.append(f"    {label}: {val}")

    print()
    if problems:
        for p in problems:
            say(p, "err")
        return False
    say("自检通过 / self-check passed", "ok")
    return True


def stage_probe(workdir):
    say("探测 RKP HAL / probing the RKP HAL")
    jar = os.path.join(os.path.dirname(os.path.abspath(__file__)), "rkp_bridge.jar")
    apk = find_rkp_apk()
    ap = find_app_process()
    if not (apk and ap):
        die("缺少 APK 或 app_process，先跑 check / missing APK or app_process; run check first")

    cp = f"{jar}:{apk}"
    cmd = (f"CLASSPATH={cp} {ap} /system/bin --nice-name=rkp_probe rkpb.RkpProbe"
           if os.geteuid() == 0
           else f"CLASSPATH={cp} {ap} /system/bin --nice-name=rkp_probe rkpb.RkpProbe")
    out = run_root(cmd, timeout=60)
    print(out)
    if not out:
        die("探测无输出 / probe produced no output")
    return out


def _assemble_v2(payload, challenge):
    """把 HAL v1/v2 的三件套组装成 Android 13 的 CSR。
    Assemble the three HAL v1/v2 pieces into an Android 13 CSR.

    格式见 rkp_csr.build_csr_v2（对齐 AOSP
    rkp_factory_extraction_tool.cpp:96-115 的 composeCertificateRequest）。
    See rkp_csr.build_csr_v2, which mirrors AOSP
    rkp_factory_extraction_tool.cpp:96-115 composeCertificateRequest().
    """
    import rkp_csr

    off = 0
    parts = []
    for _ in range(3):
        if off + 4 > len(payload):
            die("HAL 返回值长度前缀损坏 / HAL output has a corrupt length prefix")
        n = struct.unpack(">I", payload[off:off+4])[0]
        off += 4
        parts.append(payload[off:off+n])
        off += n
    mac, dev_info, prot_data = parts

    try:
        return rkp_csr.build_csr_v2(challenge, dev_info, prot_data, mac)
    except Exception as e:
        die(f"CSR 组装失败 / CSR assembly failed: {e}")


def stage_issue(workdir, fingerprint):
    say("A. fetchEekChain（取 challenge / get challenge）")
    url = f"{SERVER}{RPC_PREFIX}:fetchEekChain"
    payload = cbor_dumps({"fingerprint": fingerprint, "id": 42})
    try:
        reply = http_post(url, payload)
    except urllib.error.HTTPError as e:
        die(f"fetchEekChain 失败 / failed HTTP {e.code}: {e.read()[:200]}")
    except Exception as e:
        die(f"fetchEekChain 失败 / failed: {e}")

    # 响应是 [ [ [curve, [cert...]], ... ], challenge, extra? ]
    # Response shape: [ [ [curve, [cert...]], ... ], challenge, extra? ]
    obj, _ = cbor_loads(reply, 0)
    if not isinstance(obj, list) or len(obj) < 2:
        die(f"fetchEekChain 返回格式异常 / unexpected response: {type(obj)}")
    chains, challenge = obj[0], obj[1]
    if isinstance(challenge, str):
        challenge = challenge.encode()
    if not isinstance(challenge, (bytes, bytearray)) or not challenge:
        die("challenge 为空 / challenge is empty")
    say(f"challenge {len(challenge)} bytes", "ok")

    # 保存每条 EEK 链（Android 12/13 需要传给 HAL）
    # Save each EEK chain (Android 12/13 must pass it to the HAL)
    eek = {}
    for entry in chains if isinstance(chains, list) else []:
        if isinstance(entry, list) and len(entry) >= 2:
            curve, certs = entry[0], entry[1]
            if curve in (1, 2) and certs:
                eek[curve] = cbor_dumps(certs)   # 单个 CBOR 数组，不是拼接
    if eek:
        say(f"EEK 链 / chain: curve {sorted(eek)}（{'P-256' if 1 in eek else ''}"
            f"{' X25519' if 2 in eek else ''}）")

    os.makedirs(workdir, exist_ok=True)
    with open(os.path.join(workdir, "challenge.bin"), "wb") as f:
        f.write(challenge)
    for curve, blob in eek.items():
        with open(os.path.join(workdir, f"eek_{curve}.bin"), "wb") as f:
            f.write(blob)

    say("B. 设备 TEE 生成 CSR / TEE generates the CSR")
    jar = os.path.join(os.path.dirname(os.path.abspath(__file__)), "rkp_bridge.jar")
    apk = find_rkp_apk()
    ap = find_app_process()
    cp = f"{jar}:{apk}"
    # bridge 的输入: [u32]challenge [u32]eekP256 [u32]eekX25519
    # Bridge input layout: [u32]challenge [u32]eekP256 [u32]eekX25519
    # Android 12/13（HAL v1/v2）必须给 EEK；Android 14+ 会忽略它
    # Android 12/13 (HAL v1/v2) requires EEK; Android 14+ ignores it
    def _read(name):
        p = os.path.join(workdir, name)
        return open(p, "rb").read() if os.path.isfile(p) else b""

    eek_p256 = _read("eek_1.bin")
    eek_x25519 = _read("eek_2.bin")
    buf = b""
    for item in (challenge, eek_p256, eek_x25519):
        buf += struct.pack(">I", len(item)) + item
    csr_in = os.path.join(workdir, "in.bin")
    with open(csr_in, "wb") as f:
        f.write(buf)
    if eek_p256 or eek_x25519:
        say(f"传给 HAL / passed to HAL: challenge {len(challenge)}B, "
            f"EEK P-256 {len(eek_p256)}B, X25519 {len(eek_x25519)}B")

    # bridge 只接受一个参数：工作目录（它在目录里读写 in.bin / out.bin）
    # The bridge takes a single argument: the work directory (it reads in.bin
    # and writes out.bin inside it).
    out_path = os.path.join(workdir, "out.bin")
    if os.path.isfile(out_path):
        os.remove(out_path)          # 避免读到上次的残留 / avoid stale output
    # 把调用者的 uid/gid 传给 bridge，让它把 root 创建的文件交还回来。
    # Pass the caller's uid/gid so the bridge hands root-created files back to us.
    # 否则 key.der 属 root，普通 Termux 用户读不了。
    # Otherwise key.der belongs to root and the unprivileged user cannot read it.
    try:
        _uid, _gid = os.getuid(), os.getgid()
    except Exception:
        _uid, _gid = 0, 0
    cmd = (f"RKP_CALLER_UID={_uid} RKP_CALLER_GID={_gid} "
           f"CLASSPATH={cp} {ap} /system/bin --nice-name=rkp_bridge rkpb.RkpBridge "
           f"{workdir}")
    # 保留 bridge 的 stdout：它记录了 HAL 版本和实际走的分支，
    # 出问题时这是最关键的线索。
    # Keep the bridge's stdout: it records the HAL version and which path ran,
    # which is the most useful clue when something fails.
    bridge_out = run_root(cmd, timeout=120)
    _dbg("bridge stdout", bridge_out)
    if LAST_STDERR:
        _dbg("bridge stderr", LAST_STDERR)

    if not os.path.isfile(out_path) or os.path.getsize(out_path) == 0:
        detail = ""
        if LAST_STDERR:
            detail = f"\n    bridge stderr:\n    " + LAST_STDERR.replace("\n", "\n    ")
        if LAST_RC:
            detail += f"\n    bridge exit code: {LAST_RC}"
        die("CSR 生成失败 / CSR generation failed"
            + detail
            + "\n    排查 / debug: su -c \"logcat -d -t 300 | grep -a -i rkp\"")
    raw = open(out_path, "rb").read()
    # 输出格式 / output layout:
    #   [i32 status] [u32 len]method [u32 len]csr   —— 全部是大端 / all big-endian
    #
    # 注意 status 是 Java 的 writeInt，占 **4 字节**，不是 1 字节。
    # Note: status is Java's writeInt, i.e. **4 bytes**, not 1 byte.
    if len(raw) < 4:
        die("CSR 输出为空或损坏 / CSR output is empty or truncated")
    status = struct.unpack(">i", raw[0:4])[0]
    if status != 0:
        # err() 的载荷是 [i32 status][i32 len][msg]，把真实异常抓出来。
        # The err() payload is [i32 status][i32 len][msg]; surface the real
        # exception instead of guessing from the code.
        detail = ""
        try:
            mlen = struct.unpack(">I", raw[4:8])[0]
            detail = raw[8:8 + mlen].decode("utf-8", "replace").strip()
        except Exception:
            pass

        hint = {
            1: "设备没有 RKP HAL / no RKP HAL",
            2: "HAL 调用抛异常 / the HAL call threw",
            3: "generateCertificateRequest 抛异常 / it threw",
            4: "HAL 版本不支持 / unsupported HAL version",
        }.get(status, "未知错误 / unknown")

        print()
        say(f"CSR 生成失败 / CSR generation failed（status={status}）", "err")
        if bridge_out:
            print("    bridge 输出 / bridge output:")
            for line in bridge_out.splitlines():
                print(f"      {line}")
        if detail:
            print(f"    真实报错 / actual error:")
            for line in detail.splitlines():
                print(f"      {line}")
        else:
            print(f"    类型 / kind: {hint}")

        # 自动抓 logcat 里的 Java 堆栈，省去手动排查
        # Pull the Java stack from logcat automatically.
        try:
            log = run_root(
                "logcat -d -t 400 2>/dev/null | grep -a -i rkp_bridge | tail -25",
                timeout=25)
        except Exception:
            log = ""
        if log:
            print()
            say("logcat 中的 bridge 输出 / bridge output from logcat")
            for line in log.splitlines():
                print(f"      {line}")

        # 收集 TEE / 启动状态，验证是否因刷机导致 RKP 被拒
        # Collect TEE / boot state to test whether reflashing is why RKP fails.
        say("收集设备状态 / collecting device state ...")
        try:
            _dbg("设备与启动状态 / device and boot state", collect_tee_state())
        except Exception as e:
            _dbg("设备状态收集失败 / device state collection failed", str(e))

        dbg = _dump_debug(workdir)
        if dbg:
            print()
            say(f"诊断已写入 / diagnostics saved: {os.path.basename(dbg)}", "ok")

        print()
        print("    更多排查 / more debug:")
        print(f'      su -c "logcat -d | grep -a -iE \'keymint|rkp|tee\' | tail -40"')
        sys.exit(1)
    try:
        off = 4
        mlen = struct.unpack(">I", raw[off:off+4])[0]
        off += 4
        method = raw[off:off+mlen].decode(errors="replace")
        off += mlen
        clen = struct.unpack(">I", raw[off:off+4])[0]
        off += 4
        csr = raw[off:off+clen]
    except Exception as e:
        die(f"CSR 输出格式异常 / bad CSR layout ({len(raw)} bytes): {e}")
    if not csr:
        die("CSR 为空 / CSR is empty")

    if method == "v1":
        # Android 12/13（HAL v1/v2）：HAL 只给三件套，CSR 由客户端组装。
        # Android 12/13 (HAL v1/v2): the HAL returns only three pieces;
        # the client assembles the CSR.
        # 这里把 [macLen]mac [devLen]dev [protLen]prot 组装成服务器要的 4 元素数组。
        # Here we turn [macLen]mac [devLen]dev [protLen]prot into the
        # 4-element array the server expects.
        csr = _assemble_v2(csr, challenge)
        say(f"CSR {len(csr)} bytes，协议 / protocol V1（Android 12/13，客户端组装 / client-assembled）", "ok")
    else:
        say(f"CSR {len(csr)} bytes，协议 / protocol {method.upper()}", "ok")

    # 发送前先本地校验结构完整性 / validate structure before sending
    try:
        import rkp_csr
        _ok, _consumed, _total = rkp_csr.check_complete(csr)
        if _ok:
            say(f"CSR 结构完整 / structure complete ({_total} bytes)", "ok")
        else:
            say(f"CSR 可能有问题 / possible issue: "
                f"{rkp_csr.describe_truncation(csr)}", "warn")
            say("仍会继续发送，但服务器可能拒绝 / sending anyway, the server may reject", "warn")
    except Exception:
        pass

    with open(os.path.join(workdir, "csr.bin"), "wb") as f:
        f.write(csr)

    say("C. signCertificates（Google 签发 / signed by Google）")
    ch_b64 = base64.urlsafe_b64encode(challenge).rstrip(b"=").decode()
    url = f"{SERVER}{RPC_PREFIX}:signCertificates?challenge={ch_b64}"
    try:
        reply = http_post(url, csr)
    except urllib.error.HTTPError as e:
        body = e.read()[:400]
        if e.code == 403:
            die("signCertificates 被拒 (403)。常见原因：\n"
                "    * challenge 已过期或已被使用（每次申请必须用新 challenge）\n"
                "    * CSR 里的设备信息不被服务器接受\n"
                f"    * 服务器响应: {body}")
        if e.code == 444:
            die("signCertificates 被拒 (444)：设备未在 Google 注册 RKP\n"
                "    rejected (444): the device is not registered for RKP with Google")
        # 400 多半是 CSR 结构问题，附上本地诊断
        # A 400 is usually a CSR structure problem; attach a local diagnosis.
        local = ""
        try:
            import rkp_csr
            local = rkp_csr.describe_truncation(csr)
        except Exception:
            pass
        detail = ""
        if b"Unexpected end of stream" in body:
            detail = ("\n    含义 / meaning: 服务器认为 CSR 不完整（读取中途到达末尾）"
                      "\n    the server thinks the CSR is truncated")
        elif b"deserialization failure" in body:
            detail = ("\n    含义 / meaning: CSR 的 CBOR 结构与服务器期望不符"
                      "\n    the CSR's CBOR structure does not match what the server expects")
        die(f"signCertificates 失败 / failed HTTP {e.code}"
            + (f"\n    本地完整性 / local completeness: {local}" if local else "")
            + detail
            + f"\n    服务器响应 / server response: {body}")
    except Exception as e:
        die(f"signCertificates 失败: {e}")

    path = os.path.join(workdir, "certs.der")
    with open(path, "wb") as f:
        f.write(reply)
    say(f"证书链 {len(reply)} bytes -> {path}", "ok")
    return path


# 诊断信息收集缓冲：失败时写进 workdir/debug.txt，方便直接发出来而不必截图。
# Diagnostic buffer: written to workdir/debug.txt so the user can send a file
# instead of screen-capturing output.
_DEBUG_LINES = []


def _dbg(label, value):
    if not value:
        return
    _DEBUG_LINES.append("%s:\n%s" % (label, value))


def _dump_debug(workdir):
    """把收集到的诊断写入文件 / write collected diagnostics to a file."""
    if not _DEBUG_LINES:
        return None
    try:
        path = os.path.join(workdir, "debug.txt")
        with open(path, "w", encoding="utf-8") as f:
            f.write("rkp-toolkit debug\n")
            f.write("time: %s\n" % time.strftime("%Y-%m-%d %H:%M:%S"))
            f.write("python: %s\n" % sys.version.split()[0])
            f.write("=" * 60 + "\n\n")
            for blk in _DEBUG_LINES:
                f.write(blk + "\n\n")
        return path
    except Exception:
        return None


def collect_tee_state():
    """收集 TEE / 启动状态 / 固件版本信息，用于判断 RKP 为何失败。
    Collect TEE, boot-state and firmware info to explain RKP failures.

    RKP 的 DICE 链包含 verified boot 状态；如果设备刷过机、解锁过
    bootloader、或者 TEE 被重刷，很多实现会直接拒绝生成 CSR。

    The RKP DICE chain embeds the verified-boot state. On devices that were
    reflashed, bootloader-unlocked or had their TEE rewritten, many
    implementations refuse to generate a CSR at all.
    """
    props = [
        ("系统版本 / system", "ro.build.version.release"),
        ("SDK", "ro.build.version.sdk"),
        ("机型 / model", "ro.product.model"),
        ("厂商固件版本 / vendor build", "ro.vendor.build.version.release"),
        ("厂商指纹 / vendor fp", "ro.vendor.build.fingerprint"),
        ("verifiedbootstate", "ro.boot.verifiedbootstate"),
        ("flash.locked", "ro.boot.flash.locked"),
        ("vbmeta.device_state", "ro.boot.vbmeta.device_state"),
        ("veritymode", "ro.boot.veritymode"),
    ]
    lines = []
    for label, prop in props:
        try:
            v = run_root("getprop " + prop + " 2>/dev/null", timeout=15).strip()
        except Exception:
            v = ""
        lines.append(f"  {label:32s} {prop:28s} = {v or '<空 / empty>'}")

    # VINTF 里声明的 RKP 版本
    # The RKP version declared in the VINTF manifest
    try:
        vintf = run_root(
            "grep -A6 IRemotelyProvisionedComponent "
            "/vendor/etc/vintf/manifest.xml 2>/dev/null", timeout=20)
    except Exception:
        vintf = ""
    if vintf:
        lines.append("")
        lines.append("  VINTF 声明 / VINTF manifest:")
        for l in vintf.splitlines()[:20]:
            lines.append("    " + l)
    else:
        lines.append("")
        lines.append("  VINTF: 未找到 IRemotelyProvisionedComponent 声明")

    return "\n".join(lines)


def _ensure_readable(path):
    """确保当前用户可以读取 path，必要时通过 su 改所有者。
    Make sure the current user can read path, fixing ownership via su if needed.

    bridge 以 root 运行，它创建的 key.der / out.bin 默认属 root 且权限 600。
    The bridge runs as root, so key.der / out.bin default to root:root with 0600.
    """
    if not os.path.isfile(path):
        return path
    if os.access(path, os.R_OK):
        return path

    # 尝试把所有者改成本用户 / try to chown it to the current user
    try:
        uid, gid = os.getuid(), os.getgid()
    except Exception:
        return path

    say(f"修正文件所有者 / fixing ownership: {os.path.basename(path)}")
    run_root(f"chown {uid}:{gid} {shlex.quote(path)} 2>/dev/null")
    run_root(f"chmod 600 {shlex.quote(path)} 2>/dev/null")

    if os.access(path, os.R_OK):
        say(f"{os.path.basename(path)} 现在可读 / now readable", "ok")
        return path

    # 改所有者失败时，退而求其次读一份副本 / fall back to reading a copy
    alt = path + ".copy"
    run_root(f"cat {shlex.quote(path)} > {shlex.quote(alt)} && "
             f"chown {uid}:{gid} {shlex.quote(alt)} && chmod 600 {shlex.quote(alt)}")
    if os.path.isfile(alt) and os.access(alt, os.R_OK):
        say(f"已复制出可读副本 / copied to {os.path.basename(alt)}", "ok")
        return alt

    die(f"无法读取 {path}\n"
        f"    cannot read {path}\n"
        f"    手动修复 / fix manually: su -c \"chown {uid}:{gid} {path}\"")


def stage_keybox(workdir, out):
    from cryptography import x509
    from cryptography.hazmat.backends import default_backend
    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.primitives.asymmetric import ec

    chain_path = os.path.join(workdir, "certs.der")
    key_path = os.path.join(workdir, "key.der")
    if not os.path.isfile(chain_path):
        die(f"缺少 {chain_path}，先跑 issue")
    if not os.path.isfile(key_path):
        die(f"缺少 {key_path}（TEE 生成的私钥），先跑 issue")

    say("D. 打包 keybox.xml / packaging keybox.xml")

    # 私钥是 root 身份的 bridge 创建的，可能读不了。
    # The private key was created by the root-privileged bridge and may be unreadable.
    key_path = _ensure_readable(key_path)
    chain_path = _ensure_readable(chain_path)

    certs = order_chain(load_certs(open(chain_path, "rb").read()))
    if not certs:
        die("证书链解析为空 / certificate chain parsed as empty")

    raw = open(key_path, "rb").read()
    try:
        key = serialization.load_der_private_key(raw, password=None,
                                                 backend=default_backend())
    except Exception as e:
        if b"-----BEGIN" in raw:
            key = serialization.load_pem_private_key(raw, password=None,
                                                     backend=default_backend())
        else:
            die(f"私钥解析失败: {e}")

    if not isinstance(key, ec.EllipticCurvePrivateKey):
        die(f"私钥不是 EC（{type(key).__name__}）")
    if key.curve.name != "secp256r1":
        die(f"曲线是 {key.curve.name}，需要 P-256")

    # 验证 / Verify
    ok = True
    if key.public_key().public_numbers() != certs[0].public_key().public_numbers():
        say("私钥与 certs[0] 公钥不匹配 / key does not match certs[0]", "err")
        ok = False
    for i in range(len(certs) - 1):
        if certs[i].issuer != certs[i+1].subject:
            say(f"[{i}] 签发者衔接错误 / issuer mismatch", "err")
            ok = False
            continue
        try:
            certs[i+1].public_key().verify(
                certs[i].signature, certs[i].tbs_certificate_bytes,
                ec.ECDSA(certs[i].signature_hash_algorithm))
        except Exception as e:
            say(f"[{i}] 验签失败 / signature check failed: {e}", "err")
            ok = False
    if not ok:
        die("验证未通过，不输出 keybox")

    say("链序与验签全部通过 / chain order and signatures OK", "ok")

    # SEC1 是 TEESimulator 硬性要求
    # SEC1 is a hard requirement of TEESimulator
    key_pem = key.private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.TraditionalOpenSSL,
        encryption_algorithm=serialization.NoEncryption()).decode().strip()

    L = ['<?xml version="1.0"?>', "<AndroidAttestation>",
         "    <NumberOfKeyboxes>1</NumberOfKeyboxes>",
         '    <Keybox DeviceID="rkp-issued">',
         '        <Key algorithm="ecdsa">',
         '            <PrivateKey format="pem">',
         key_pem,
         "            </PrivateKey>",
         "            <CertificateChain>"]
    for c in certs:
        L.append('                <Certificate format="pem">')
        L.append(c.public_bytes(serialization.Encoding.PEM).decode().strip())
        L.append("                </Certificate>")
    L += ["            </CertificateChain>",
          "        </Key>",
          "    </Keybox>",
          "</AndroidAttestation>"]
    xml = "\n".join(L) + "\n"

    os.makedirs(os.path.dirname(os.path.abspath(out)) or ".", exist_ok=True)
    with open(out, "w", encoding="utf-8") as f:
        f.write(xml)

    data = xml.encode()
    print()
    say(f"已写入 {out}", "ok")
    print(f"    大小: {len(data)} bytes")
    print(f"    md5 : {hashlib.md5(data).hexdigest()}")
    print(f"    私钥 / key: SEC1（BoringSSL 要求 / required）")
    print(f"    证书 / certs: {len(certs)} 张 / items，叶子在前 / leaf first")
    for i, c in enumerate(certs):
        print(f"      [{i}] {c.subject.rfc4514_string()}")
    leaf = certs[0]
    import datetime as _dt
    _nb = _cert_time(leaf, "before")
    _na = _cert_time(leaf, "after")
    print(f"    叶子有效期 / leaf validity: {_nb:%Y-%m-%d %H:%M:%S} -> {_na:%Y-%m-%d %H:%M:%S} UTC")
    now = _dt.datetime.now(_dt.timezone.utc)
    days = (_na - now).days
    if days < 0:
        say(f"警告：该证书已过期 {-days} 天，需要重新签发\n"
            f"WARNING: certificate expired {-days} days ago; re-issue required", "err")
    elif days < 7:
        say(f"警告：该证书仅剩 {days} 天，建议尽快重新签发\n"
            f"WARNING: only {days} days left; re-issue soon", "warn")
    else:
        say(f"该证书剩余 {days} 天有效期 / {days} days left on this certificate")
    return out


# ---------------------------------------------------------------- 归档
# Archiving
#
# 每次运行结束（成功或失败）都把工作目录里的产物移进一个带日期和批次的文件夹，
# 例如 rkp_out/2026-11-21_0001/、rkp_out/2026-11-21_0002/。
# 工作目录本身只当临时区，归档后清空。
#
# After every run (successful or not) the artifacts in the work directory are
# moved into a dated batch folder such as rkp_out/2026-11-21_0001/. The work
# directory itself acts as scratch space and is emptied by archiving.
#
# 批次号只扫描**当天**的目录，避免去读历史日期的文件夹。
# Batch numbers are computed by scanning **today's** directories only, so older
# days are never read.

_ARCHIVE = {"enabled": False, "workdir": None, "out": None, "ok": False}


def _next_batch_dir(root):
    """在工作目录下算出当天下一个批次目录路径。
    Work out the next batch directory path for today under root.
    """
    today = time.strftime("%Y-%m-%d")
    pat = re.compile(r"^" + re.escape(today) + r"_(\d{4})$")

    nums = []
    try:
        for name in os.listdir(root):
            m = pat.match(name)
            if m and os.path.isdir(os.path.join(root, name)):
                nums.append(int(m.group(1)))
    except OSError:
        pass

    n = (max(nums) + 1) if nums else 1
    while True:
        path = os.path.join(root, "%s_%04d" % (today, n))
        if not os.path.exists(path):
            return path
        n += 1


def archive_workdir(workdir):
    """把 workdir 里所有文件移入当天批次目录。
    Move every file in workdir into today's batch directory.

    返回 / returns: (目录 / directory, 移动的文件名列表 / moved names)
    没有任何文件时返回 (None, []) / (None, []) when there is nothing to move.
    """
    if not workdir or not os.path.isdir(workdir):
        return None, []

    try:
        names = [n for n in os.listdir(workdir)
                 if os.path.isfile(os.path.join(workdir, n))]
    except OSError:
        return None, []

    if not names:
        return None, []

    dest = _next_batch_dir(workdir)
    try:
        os.makedirs(dest, exist_ok=True)
    except OSError as e:
        say(f"无法创建归档目录 / cannot create archive dir: {e}", "warn")
        return None, []

    moved = []
    for n in sorted(names):
        try:
            shutil.move(os.path.join(workdir, n), os.path.join(dest, n))
            moved.append(n)
        except Exception as e:
            say(f"归档失败 / failed to archive {n}: {e}", "warn")

    return (dest, moved) if moved else (None, [])


def _archive_at_exit():
    """退出时归档（成功与失败都执行）。
    Archive on exit; runs on success and on failure alike.
    """
    if not _ARCHIVE["enabled"]:
        return

    dest, moved = archive_workdir(_ARCHIVE["workdir"])
    if dest is None:
        return

    print()
    print("=" * 42)
    if _ARCHIVE["ok"]:
        print(" 归档 / archived")
    else:
        print(" 归档（本次失败）/ archived (this run failed)")
    print("=" * 42)
    print(" 目录 / directory:")
    print("   %s" % dest)
    print(" 文件 / files: %d" % len(moved))

    # keybox.xml 的最终位置 / where keybox.xml ended up
    kb = "keybox.xml"
    if kb in moved:
        print(" keybox.xml:")
        print("   %s" % os.path.join(dest, kb))
        try:
            size = os.path.getsize(os.path.join(dest, kb))
            with open(os.path.join(dest, kb), "rb") as f:
                md5 = hashlib.md5(f.read()).hexdigest()
            print(" 大小 / size: %d bytes" % size)
            print(" md5: %s" % md5)
        except OSError:
            pass
    print("=" * 42)


def stage_verify(path):
    from cryptography import x509
    from cryptography.hazmat.backends import default_backend
    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.primitives.asymmetric import ec

    say(f"验证 {path}")
    if not os.path.isfile(path):
        die(f"文件不存在: {path}")
    raw = open(path, encoding="utf-8", errors="replace").read()
    km = re.search(r"<PrivateKey[^>]*>(.*?)</PrivateKey>", raw, re.S)
    cm = re.findall(r"<Certificate[^>]*>(.*?)</Certificate>", raw, re.S)
    if not km or not cm:
        die("XML 结构不完整")

    key_pem = km.group(1).strip()
    if "BEGIN EC PRIVATE KEY" not in key_pem:
        say("私钥不是 SEC1（TEESimulator 会 -30000）\n"
            "private key is not SEC1 (TEESimulator returns -30000)", "err")
        return False
    say("私钥格式 SEC1 / private key is SEC1", "ok")

    key = serialization.load_pem_private_key(key_pem.encode(), password=None,
                                             backend=default_backend())
    certs = [x509.load_pem_x509_certificate(c.strip().encode(), default_backend())
             for c in cm]
    say(f"{len(certs)} 张证书 / certificates")

    ok = True
    if key.public_key().public_numbers() != certs[0].public_key().public_numbers():
        say("私钥与 certs[0] 不匹配 / key does not match certs[0]", "err")
        ok = False
    else:
        say("私钥与 certs[0] 匹配 / key matches certs[0]", "ok")

    for i in range(len(certs) - 1):
        if certs[i].issuer != certs[i+1].subject:
            say(f"[{i}] issuer != [{i+1}] subject", "err")
            ok = False
    for i in range(len(certs) - 1):
        try:
            certs[i+1].public_key().verify(
                certs[i].signature, certs[i].tbs_certificate_bytes,
                ec.ECDSA(certs[i].signature_hash_algorithm))
        except Exception as e:
            say(f"[{i}] 验签失败: {e}", "err")
            ok = False

    if certs[-1].subject != certs[-1].issuer:
        say("末级不是自签名根 / last cert is not a self-signed root", "err")
        ok = False

    if ok:
        say("全部通过", "ok")
    return ok


# ---------------------------------------------------------------- main

def _enable_archive(workdir):
    """启用退出时归档 / enable archiving at exit."""
    _ARCHIVE["enabled"] = True
    _ARCHIVE["workdir"] = workdir
    atexit.register(_archive_at_exit)


def main():
    ap = argparse.ArgumentParser(description="RKP 签发工具链")
    ap.add_argument("cmd", nargs="?", default="all",
                    choices=["all", "check", "probe", "issue", "keybox", "verify"])
    ap.add_argument("--workdir", default="rkp_out")
    ap.add_argument("-o", "--out", default=None, help="输出 keybox.xml 路径")
    ap.add_argument("--fingerprint", default=DEFAULT_FP)
    ap.add_argument("--no-archive", action="store_true",
                    help="不归档，产物留在原目录 / keep artifacts in place")
    a = ap.parse_args()

    out = a.out or os.path.join(a.workdir, "keybox.xml")

    if a.cmd == "check":
        sys.exit(0 if stage_check() else 1)

    if a.cmd == "probe":
        stage_probe(a.workdir)
        return

    if a.cmd == "verify":
        sys.exit(0 if stage_verify(out) else 1)

    # 以下三个命令会产生文件，结束后归档
    # The commands below produce artifacts, so archive when they finish.
    if not a.no_archive:
        _enable_archive(a.workdir)

    if a.cmd == "issue":
        stage_issue(a.workdir, a.fingerprint)
        return

    if a.cmd == "keybox":
        stage_keybox(a.workdir, out)
        _ARCHIVE["ok"] = True
        return

    # all（全部阶段 / every stage）
    if not stage_check():
        die("自检未通过，先解决问题")
    print()
    stage_issue(a.workdir, a.fingerprint)
    print()
    stage_keybox(a.workdir, out)
    _ARCHIVE["ok"] = True


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        sys.exit(130)
