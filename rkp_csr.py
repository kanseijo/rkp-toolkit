#!/usr/bin/env python3
"""
rkp_csr.py —— Android 13 (HAL v2) CSR 组装 / Android 13 (HAL v2) CSR assembly

权威来源 / Authoritative source:
    AOSP `system/security/provisioner/rkp_factory_extraction_tool.cpp:96-115`
    (android13-release), `composeCertificateRequest()`:

        Array macedKeysToSign = Array()
            .add(std::vector<uint8_t>(0))   // empty protected headers as bstr
            .add(Map())                     // empty unprotected headers
            .add(Null())                    // nil for the payload
            .add(keysToSignMac);            // MAC as returned from the HAL

        Array deviceInfo =
            Array().add(EncodedItem(verifiedDeviceInfo.deviceInfo)).add(Map());

        Array certificateRequest = Array()
            .add(std::move(deviceInfo))     // [ DeviceInfo, {} ]
            .add(challenge)
            .add(EncodedItem(protectedData.protectedData))
            .add(std::move(macedKeysToSign));

所以 A13 的 CSR 是 4 元素 CBOR 数组 / So the A13 CSR is a 4-element CBOR array:

    [
        [ VerifiedDeviceInfo(CBOR), {} ],      <- 已验证在前，空 map 在后 / verified first
        challenge : bytes,                     <- 服务器给的挑战 / challenge from server
        ProtectedData : COSE_Encrypt,          <- 加密的 BCC / encrypted BCC
        MacedKeysToSign : COSE_Mac0 [ bstr(""), {}, nil, mac ]
    ]

关键点（容易写错的）/ Key points (easy to get wrong):
  * DeviceInfo 数组是 [已验证, 未验证空 map]，不是 [空, 已验证]
    / The DeviceInfo pair is [verified, {}], NOT [{}, verified]
  * MacedKeysToSign 的 payload 是 nil，protected 是空 bstr，tag 用 HAL 返回的 mac
    / MacedKeysToSign has nil payload, empty bstr protected header, tag = HAL's mac
  * 顶层没有 version 字段（那是 v3 才有）
    / There is no top-level version field (that exists only in v3)

注意 / Note: AOSP 的 `.add(std::vector<uint8_t>(0))` 生成的是空 bstr (0x40)，
不是空数组 (0x80) / generates an empty bstr (0x40), not an empty array (0x80).
"""


# ---------------------------------------------------------------- CBOR 编码
# CBOR encoding helpers

def _head(major, n):
    """编码 CBOR 头部 / Encode a CBOR head (major type + length)."""
    if n < 24:
        return bytes([(major << 5) | n])
    if n < 0x100:
        return bytes([(major << 5) | 24, n])
    if n < 0x10000:
        return bytes([(major << 5) | 25]) + n.to_bytes(2, "big")
    if n < 0x100000000:
        return bytes([(major << 5) | 26]) + n.to_bytes(4, "big")
    return bytes([(major << 5) | 27]) + n.to_bytes(8, "big")


class Raw:
    """已编码好的 CBOR 片段，原样嵌入。
    An already-encoded CBOR fragment, embedded verbatim.

    用于把 HAL 返回的 DeviceInfo / ProtectedData 直接拼进外层数组。
    Used to splice the HAL-returned DeviceInfo / ProtectedData into the outer array.

    必须原样嵌入而不是先解码再编码：HAL 返回的 map 已是规范顺序，
    重编码可能破坏顺序，服务器会以 "Device info is not canonical" 拒绝。
    Must be embedded verbatim rather than decode-then-re-encode: the map returned
    by the HAL is already in canonical order, and re-encoding may break that order,
    which makes the server reject it with "Device info is not canonical".
    """
    __slots__ = ("data",)

    def __init__(self, data):
        self.data = bytes(data)


def dumps(o):
    """最小 CBOR 编码器 / Minimal CBOR encoder。

    只覆盖 RKP 需要的类型 / Covers only the types RKP needs:
    bool / None / int / bytes / str / list / dict / Raw
    """
    if o is True:
        return b"\xf5"
    if o is False:
        return b"\xf4"
    if o is None:
        return b"\xf6"
    if isinstance(o, Raw):
        return o.data
    if isinstance(o, int):
        return _head(0, o) if o >= 0 else _head(1, -1 - o)
    if isinstance(o, (bytes, bytearray)):
        return _head(2, len(o)) + bytes(o)
    if isinstance(o, str):
        b = o.encode()
        return _head(3, len(b)) + b
    if isinstance(o, (list, tuple)):
        return _head(4, len(o)) + b"".join(dumps(x) for x in o)
    if isinstance(o, dict):
        out = _head(5, len(o))
        for k, v in o.items():
            out += dumps(k) + dumps(v)
        return out
    raise TypeError(f"无法编码 / cannot encode {type(o)}")


# ---------------------------------------------------------------- 组装
# Assembly

def build_csr_v2(challenge, device_info, protected_data, mac):
    """组装 Android 13（HAL v2）的 CSR。
    Build the Android 13 (HAL v2) CSR.

    参数 / Parameters:
        challenge      : bytes，服务器给的（32..64 字节）
                         / bytes from the server (32..64 bytes)
        device_info    : bytes，HAL 返回的 DeviceInfo.deviceInfo（已 CBOR 编码）
                         / HAL DeviceInfo.deviceInfo, already CBOR-encoded
        protected_data : bytes，HAL 返回的 ProtectedData.protectedData（COSE_Encrypt）
                         / HAL ProtectedData.protectedData (COSE_Encrypt)
        mac            : bytes，HAL 返回值（32 字节 HMAC）
                         / HAL return value (32-byte HMAC)

    返回 / Returns: bytes，可直接 POST 到 signCertificates 的 CSR
                    / bytes, ready to POST to signCertificates
    """
    # 输入校验 / Input validation
    if not challenge:
        raise ValueError("challenge 为空 / challenge is empty")
    if len(challenge) > 64:
        raise ValueError(f"challenge 过长 / challenge too long ({len(challenge)} > 64)")
    if len(mac) != 32:
        raise ValueError(f"MAC 应为 32 字节 / MAC must be 32 bytes, got {len(mac)}")
    if not device_info:
        raise ValueError("deviceInfo 为空 / deviceInfo is empty")
    if not protected_data:
        raise ValueError("protectedData 为空 / protectedData is empty")

    # MacedKeysToSign = COSE_Mac0: [ protected(空 bstr), {}, nil, mac ]
    # MacedKeysToSign = COSE_Mac0: [ protected(empty bstr), {}, nil, mac ]
    maced_keys = [
        b"",        # 空 bstr (0x40) / empty bstr
        {},         # 空 map (0xa0) / empty map
        None,       # nil (0xf6) / nil
        mac,        # HAL 返回的 MAC / MAC from the HAL
    ]

    # DeviceInfo = [ VerifiedDeviceInfo, {} ]
    # 注意方向：已验证在前 / note the order: verified first
    dev_pair = [
        Raw(device_info),
        {},
    ]

    # 顶层 4 元组 / top-level 4-tuple
    csr = [
        dev_pair,
        challenge,
        Raw(protected_data),
        maced_keys,
    ]
    return dumps(csr)


# ---------------------------------------------------------------- 校验
# Validation

def check_complete(blob):
    """检查一段 CSR 是否被完整消费（没有截断也没有多余字节）。
    Check that a CSR is fully consumed (neither truncated nor padded).

    服务器报 "Unexpected end of stream" 通常就是 CSR 被截断了。
    The server's "Unexpected end of stream" usually means the CSR got truncated.

    返回 / returns: (是否完整 / complete, 实际消耗长度 / consumed, 总长 / total)
    """
    total = len(blob)
    if total == 0:
        return False, 0, 0
    consumed = _skip(blob, 0)
    if consumed < 0:
        return False, -1, total
    return consumed == total, consumed, total


def describe_truncation(blob):
    """给出人类可读的诊断，用于报错信息。
    Produce a human-readable diagnosis for error messages.
    """
    ok, consumed, total = check_complete(blob)
    if ok:
        return "完整 / complete"
    if consumed < 0:
        return f"CBOR 结构损坏（解析在 {total} 字节处中断）/ corrupt CBOR"
    if consumed > total:
        return (f"截断：应需 {consumed} 字节，实际只有 {total} 字节"
                f" / truncated: need {consumed}, have {total}")
    return (f"尾部有多余 {total - consumed} 字节"
            f" / trailing {total - consumed} bytes")

def looks_like_v2_csr(blob):
    """粗判 CSR 是否为 v2 格式（顶层 4 元素数组且第 1 项是数组）。
    Rough check whether a CSR is v2 (4-element array with an array first).

    hwtrust 就是用这个判断 v2/v3 的 / This is exactly how hwtrust tells v2 from v3:
        Value::Array(device_info) => v2
        Value::Integer(i)         => v3
    """
    items = _decode_array(blob)
    if not items or len(items) < 4:
        return False
    # 第一项应是 array（0x8x）/ first item must be an array (0x8x)
    return (items[0][0] >> 5) == 4


# ---------------------------------------------------------------- CBOR 扫描
# CBOR scanning (for tests and validation)

def _decode_array(b):
    """解析顶层 CBOR 数组，返回各元素的原始编码。
    Parse a top-level CBOR array, returning each element's raw encoding.
    """
    if not b or (b[0] >> 5) != 4:
        return None
    ai = b[0] & 0x1F
    off = 1
    if ai < 24:
        n = ai
    elif ai == 24:
        n = b[1]
        off = 2
    else:
        return None

    items = []
    for _ in range(n):
        start = off
        off = _skip(b, off)
        if off < 0:
            return None
        items.append(b[start:off])
    return items


def _skip(b, off):
    """跳过 b[off] 处的一个完整 CBOR 项，返回结束位置。
    Skip one complete CBOR item at b[off], returning the end offset.
    """
    if off >= len(b):
        return -1
    ib = b[off]
    major = ib >> 5
    ai = ib & 0x1F
    off += 1
    if ai < 24:
        val = ai
    elif ai == 24:
        val = b[off]; off += 1
    elif ai == 25:
        val = int.from_bytes(b[off:off+2], "big"); off += 2
    elif ai == 26:
        val = int.from_bytes(b[off:off+4], "big"); off += 4
    elif ai == 27:
        val = int.from_bytes(b[off:off+8], "big"); off += 8
    else:
        return -1

    if major in (0, 1, 7):
        return off
    if major in (2, 3):
        return off + val
    if major == 4:
        for _ in range(val):
            off = _skip(b, off)
            if off < 0:
                return -1
        return off
    if major == 5:
        for _ in range(val * 2):
            off = _skip(b, off)
            if off < 0:
                return -1
        return off
    if major == 6:
        return _skip(b, off)
    return -1


# ---------------------------------------------------------------- 自测
# Self-test:  python3 rkp_csr.py

if __name__ == "__main__":
    # 用合法 CBOR 构造假数据 / build fake data with valid CBOR
    dev = dumps({1: 2, 3: "keymint"})                  # 假 DeviceInfo / fake DeviceInfo
    prot = dumps([b"\xa1\x01\x03", {}, b"payload"])    # 假 COSE_Encrypt / fake COSE_Encrypt
    mac = bytes(range(32))

    print("=" * 60)
    print(" rkp_csr 自测 / self-test")
    print("=" * 60)

    # ---------- 1. 基本形状 / basic shape ----------
    csr = build_csr_v2(b"0123456789abcdef", dev, prot, mac)
    print(f"\n[1] 生成 CSR / generated: {len(csr)} bytes")
    print(f"    顶层 / top-level: 0x{csr[0]:02x} (应为/expected 0x84 = array(4))")
    assert csr[0] == 0x84, "顶层必须是 4 元素数组 / top level must be array(4)"

    items = _decode_array(csr)
    assert items and len(items) == 4, "顶层必须 4 元素 / top level must have 4 items"
    print(f"    元素数 / items: {len(items)}  OK")

    # ---------- 2. DeviceInfo 对 / DeviceInfo pair ----------
    print(f"\n[2] DeviceInfo 对 / pair: {items[0][:8].hex()}...")
    print(f"    首字节 / first byte: 0x{items[0][0]:02x} (应为/expected 0x82 = array(2))")
    assert items[0][0] == 0x82, "DeviceInfo 必须是 array(2) / must be array(2)"
    # 末尾应是空 map / it must end with an empty map
    assert items[0].endswith(b"\xa0"), "DeviceInfo 末尾必须是空 map / must end with {}"
    print(f"    末尾空 map / trailing map: OK")

    # ---------- 3. challenge ----------
    print(f"\n[3] challenge: {items[1][:20].hex()}... ({len(items[1])} bytes 编码/encoded)")

    # ---------- 4. ProtectedData ----------
    print(f"\n[4] ProtectedData: 0x{items[2][0]:02x}... ({len(items[2])} bytes)")

    # ---------- 5. MacedKeysToSign ----------
    print(f"\n[5] MacedKeysToSign: 0x{items[3][0]:02x} (应为/expected 0x84 = COSE_Mac0)")
    assert items[3][0] == 0x84, "MacedKeys 必须 4 元素 / must have 4 items"

    mk = _decode_array(items[3])
    assert mk and len(mk) == 4, "MacedKeys 内部必须 4 项 / must have 4 items"
    print(f"    内部 / inner {len(mk)} 项 / items:")
    print(f"      [0] {mk[0]!r}   <- 空 bstr / empty bstr (0x40)")
    print(f"      [1] {mk[1]!r}   <- 空 map / empty map (0xa0)")
    print(f"      [2] {mk[2]!r}   <- nil (0xf6)")
    print(f"      [3] {len(mk[3])} bytes 编码 / encoded (32 mac + 2 head)")

    assert mk[0] == b"\x40", "protected 必须是空 bstr / must be empty bstr"
    assert mk[1] == b"\xa0", "unprotected 必须是空 map / must be empty map"
    assert mk[2] == b"\xf6", "payload 必须是 nil / must be nil"

    # ---------- 6. v2 判定 / v2 detection ----------
    print(f"\n[6] looks_like_v2_csr(): {looks_like_v2_csr(csr)}")
    assert looks_like_v2_csr(csr), "应判定为 v2 / should be detected as v2"

    # ---------- 7. 非法输入 / bad inputs ----------
    print(f"\n[7] 非法输入校验 / bad-input validation:")
    cases = [
        ("空 challenge / empty challenge", lambda: build_csr_v2(b"", dev, prot, mac)),
        ("过长 challenge / too long",      lambda: build_csr_v2(b"x" * 65, dev, prot, mac)),
        ("错误 MAC 长度 / wrong MAC len",  lambda: build_csr_v2(b"x", dev, prot, mac[:16])),
        ("空 deviceInfo / empty",          lambda: build_csr_v2(b"x", b"", prot, mac)),
        ("空 protectedData / empty",       lambda: build_csr_v2(b"x", dev, b"", mac)),
    ]
    for name, fn in cases:
        try:
            fn()
            print(f"      {name}: 未抛异常 FAIL / did not raise FAIL")
            raise SystemExit(1)
        except ValueError:
            print(f"      {name}: 正确拒绝 / correctly rejected  OK")

    # ---------- 8. 完整性检查 / completeness ----------
    print(f"\n[8] 完整性检查 / completeness:")
    okc, consumed, total = check_complete(csr)
    print(f"    {describe_truncation(csr)}")
    assert okc, "刚生成的 CSR 应当完整 / a fresh CSR must be complete"

    trunc = csr[:-10]
    okc2, _, _ = check_complete(trunc)
    print(f"    截断 10 字节 / truncated by 10: {describe_truncation(trunc)}")
    assert not okc2, "截断的 CSR 应被检出 / truncation must be detected"

    extra = csr + b"\x00\x01"
    okc3, _, _ = check_complete(extra)
    print(f"    尾部多余 / trailing bytes: {describe_truncation(extra)}")
    assert not okc3, "多余字节应被检出 / trailing bytes must be detected"

    print("\n" + "=" * 60)
    print(" 自测通过 / ALL TESTS PASSED")
    print("=" * 60)
