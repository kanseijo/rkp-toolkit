# Android 13 (API 33) RKP 机制调研报告

> 所有结论均来自直接抓取的 AOSP 源码（`android.googlesource.com`，`?format=TEXT` 原始文件 base64 解码）。
> 分支：`android12-release` / `android13-release` / `android13-qpr{1,2,3}-release` / `android14-release` / `android15-release` / `main`。
> 无法确认的地方已明确标注「无法确认」。

---

## 0. 结论速览（对工具链最重要的 5 点）

1. **Android 13 = `IRemotelyProvisionedComponent` v2，CSR schema version 2。** Android 13 上 `generateCertificateRequestV2` **不存在**（AIDL 里根本没有这个方法，所有 QPR 分支均确认）。你的 HAL v3 工具链的 CSR 格式在 A13 上**完全不适用**。
2. **A13 的 IRPC AIDL 与 A12 逐字节完全相同**（`diff` 结果 IDENTICAL）。A12→A13 的唯一 HAL 差异是 `RpcHardwareInfo` 新增 `uniqueId` 字段。所以"v1 vs v2"是 **AIDL package 版本号**的差别，不是代码差别。
3. **EEK 在 A13 是强制的**：`generateCertificateRequest` 必须传入有效的 EEK 证书链，`supportedEekCurve` 不能是 `CURVE_NONE`（VTS 强制只接受 P256 / 25519）。
4. **A13 的 CSR 是 v1 风格的 4 元组结构**，由 *客户端*（keystore2 / rkp_factory_extraction_tool）组装，不是 HAL 直接吐出的 `AuthenticatedRequest`。这与 Android 14+ 有本质区别。
5. **`remote_provisioning.tee.rkp_only` 在 A13 存在**，默认 `false`（混合模式，可回退到出厂密钥）。

---

## 1. Android 13 的 RKP HAL 版本

### 1.1 HAL package 位置（重要，容易踩坑）

| 版本 | AIDL package 路径 | 说明 |
|---|---|---|
| Android 12 | `hardware/interfaces/security/keymint/aidl/android/hardware/security/keymint/` | 与 KeyMint 同 package |
| **Android 13** | **同上**（`security/keymint/...`，**没有** `security/rkp/` 目录） | 仍与 KeyMint 同 package |
| Android 14+ | `hardware/interfaces/security/rkp/aidl/android/hardware/security/keymint/` | 独立模块 `android.hardware.security.rkp`，但 **Java/C++ 命名空间仍保留 `keymint`** 以维持 ABI |

`android13-release` 的 `hardware/interfaces/security/` 目录仅有：`OWNERS, dice, keymint, secureclock, sharedsecret` —— **没有 `rkp` 子目录**（A14 才有）。

**v2 → v3 的模块拆分**（引自 `security/rkp/CHANGELOG.md`）：
> * The RKP HAL now builds separately from KeyMint.
> * The HAL remains under the `android.hardware.security.keymint` package for compatibility with previous releases. ABI compatibility requires this.
> * Dependencies on the RKP HAL must add a dependency on `"android.hardware.security.rkp"` generated code (instead of `"android.hardward.security.keymint"`).

### 1.2 版本号对照（权威来源）

`hardware/interfaces/security/rkp/CHANGELOG.md`（A14+ 分支）原文：

```
## Releases
* **Android S (12):** IRemotelyProvisionedComponent v1
* **Android T (13):** IRemotelyProvisionedComponent v2
* **Android U (14):** IRemotelyProvisionedComponent v3
```

`android14-release` 的 `IRemotelyProvisionedComponent.aidl:101-107` 亦直接写明：
```
 * Versioning
 * ==========
 * Versions 1 and 2 of the schema, as previously defined in DeviceInfo.aidl, diverge in
 * functionality from Version 3. ...
 * Vendors implementing Version 1 (Android S/12) or Version 2 (Android T/13) do not need to
 * implement generateCertificateRequestV2.
 * Vendors implementing Version 3 (Android U/14) need to implement generateCertificateRequestV2.
```

冻结 API 目录亦印证：`aidl_api/android.hardware.security.keymint/` 在 `android12-release` 只有 `1, current`，在 `android13-release` 是 `1, 2, current`。

> ⚠️ 注意区分两个"version"：
> - **AIDL interface version**（v1/v2/v3）= `getInterfaceVersion()` 返回的 binder 版本。
> - **`RpcHardwareInfo.versionNumber`** = HAL 自己上报的"CSR schema 版本"，语义是"实现版本"。
> 在 AOSP 参考实现里两者正好对齐，但 AIDL 注释明确说 `versionNumber` 是 implementation defined。

### 1.3 `RpcHardwareInfo.versionNumber` 在 A13 设备上典型值

**典型值 = `2`**。

`RpcHardwareInfo.aidl` 的字段注释（A13）：
> Implementation version of the remotely provisioned component hardware. The version number is implementation defined, and not necessarily globally meaningful.

AOSP KeyMint 参考实现（`system/keymint/ta/src/rkp.rs`，A14 分支）里可见 `IRPC_V2` / `IRPC_V3` 常量，V2 配置上报 `version_number: IRPC_V2`。**注意**：`platform/system/keymint` 仓库**在 Android 13 不存在**（HTTP 404），A14 才独立出来；A13 的 KeyMint TA 参考实现位于哪些厂商目录**无法确认**（AOSP 侧未在 `platform/system/keymint` 提供）。因此"A13 参考实现返回 2"这一点是基于 schema 版本定义推断的强结论，而非 A13 源码直证。

**工具链建议**：不要只信 `versionNumber`，应以 `versionNumber <= 2` **且** `generateCertificateRequestV2` 调用不可用（`TransactionException`）来判定走 v1/v2 路径。

### 1.4 `supportedEekCurve` 在 Android 13 的状态

**必须支持，且不能为 `CURVE_NONE`。**

- 定义（A13 `RpcHardwareInfo.aidl`）：
  ```java
  const int CURVE_NONE = 0;
  const int CURVE_P256 = 1;
  const int CURVE_25519 = 2;
  int supportedEekCurve = CURVE_NONE;
  ```
- 字段注释：`CURVE_NONE` 是默认值，仅为"防止实现者忘记填"；**VTS 会检查并确保通过的实现不返回 CURVE_NONE**。
- VTS 实证 —— `security/keymint/aidl/vts/functional/VtsRemotelyProvisionedComponentTests.cpp:235-242`（A13）：
  ```cpp
  const std::set<int> validCurves = {RpcHardwareInfo::CURVE_P256, RpcHardwareInfo::CURVE_25519};
  ASSERT_EQ(validCurves.count(hwInfo.supportedEekCurve), 1) << "Invalid curve: " << ...;
  ```
  → A13 的 IRPC **必须**返回 P256 或 25519 之一。
- **`CURVE_25519` 优先**（AIDL 注释："with preference for 25519 if it's available"），但 P256 亦完全合法。
- ⚠️ **动态行为**：VTS 的 `getSessionKey()`（行 386-393）把 `CURVE_NONE` 与 `CURVE_25519` 归入 **X25519** 分支处理。这意味着即使某设备返回 `CURVE_NONE`，其 EEK 仍按 X25519 处理。

---

## 2. EEK（Ephemeral Encryption Key）机制

### 2.1 Android 13 是否必须使用 EEK —— **是**

A13 的 `generateCertificateRequest` **签名中 EEK 链是必需参数**，且 HAL 内部用它加密整个 BCC + MAC key。没有 EEK 就无法产出合法 CSR。

### 2.2 `generateCertificateRequest` 完整签名（A13，与 A12 完全相同）

```aidl
byte[] generateCertificateRequest(in boolean testMode,
                                  in MacedPublicKey[] keysToSign,
                                  in byte[] endpointEncryptionCertChain,
                                  in byte[] challenge,
                                  out DeviceInfo deviceInfo,
                                  out ProtectedData protectedData);
```

返回值 = `keysToSignMac` = `HMAC-256(EK_mac, .cbor KeysToMacStructure)`。

**⚠️ 关于 `fetchEekChain`：这个方法在 AOSP 中不存在。**
在 `android12-release` / `android13-release` / `android14-release` / `android15-release` / `main` 的 `IRemotelyProvisionedComponent.aidl` 中，`fetchEekChain` 出现次数均为 **0**。A13 IRPC 只有 3 个方法：
```aidl
RpcHardwareInfo getHardwareInfo();
byte[] generateEcdsaP256KeyPair(in boolean testMode, out MacedPublicKey macedPublicKey);
byte[] generateCertificateRequest(... 6 params ...);
```
EEK 链由 **调用方（客户端）** 生成并传入，不是从 HAL 拉取。见 §2.3。

### 2.3 EEK 链格式（`EekChain`，CDDL 引自 A13 `IRemotelyProvisionedComponent.aidl`）

```
EekChain = [ + SignedSignatureKey, SignedEek ]
```
即：**CBOR array，从 root 到 leaf 排序，至少 2 个元素**。除最后一个外，每个元素是 Ed25519（或 P256）签名密钥；最后一个是 EEK 加密密钥本身。root 自签名。

```
SignedSignatureKey = [                      // COSE_Sign1
    protected: bstr .cbor { 1 : AlgorithmEdDSA / AlgorithmES256 },
    unprotected: {},
    payload: bstr .cbor SignatureKeyEd25519 / bstr .cbor SignatureKeyP256,
    signature: bstr PureEd25519(.cbor SignatureKeySignatureInput) / bstr ECDSA(...)
]

SignatureKeyEd25519 = { 1:1 (OKP), 3:AlgorithmEdDSA, -1:6 (Ed25519), -2:bstr }
SignatureKeyP256    = { 1:2 (EC2), 3:AlgorithmES256, -1:1 (P256), -2:bstr X, -3:bstr Y }

SignedEek = [                               // COSE_Sign1
    protected: bstr .cbor { 1 : AlgorithmEdDSA / AlgorithmES256 },
    unprotected: {},
    payload: bstr .cbor EekX25519 / bstr .cbor EekP256,
    signature: bstr ...
]

EekX25519 = { 1:1, 2:bstr KID, 3:-25 (ECDH-ES+HKDF-256), -1:4 (X25519), -2:bstr }
EekP256   = { 1:2, 2:bstr KID, 3:-25, -1:1 (P256), -2:bstr X, -3:bstr Y }

AlgorithmES256 = -7
AlgorithmEdDSA = -8
```

**签名输入格式统一定为**：
```
[cotext, body_protected, external_aad, payload]
  "Signature1", bstr .cbor {1: alg}, bstr .size 0, bstr .cbor <cose_key>
```

**HAL 侧校验规则**（AIDL 注释）：
- 结构不符 → 返回 `STATUS_INVALID_EEK (5)`。
- `testMode == true` → **忽略签名长度和内容**，**不得**尝试验证签名。
- `testMode == false` → 必须验证链签名，且 root 公钥必须命中**预置在 secure area 中的授权 EEK root 集合**；否则 `STATUS_INVALID_EEK`。

**生产环境 EEK 链从哪来**（A13 `security/keymint/support/remote_prov_utils.cpp:269-283`）：
```cpp
bytevec getProdEekChain(int32_t supportedEekCurve) {
    cppbor::Array chain;
    if (supportedEekCurve == RpcHardwareInfo::CURVE_P256) {
        chain.add(kCoseEncodedEcdsa256RootCert);   // root
        chain.add(kCoseEncodedEcdsa256GeekCert);   // GEEK (Google EEK) leaf
    } else {
        chain.add(kCoseEncodedRootCert);
        chain.add(kCoseEncodedGeekCert);
    }
    return chain.encode();
}
```
即 **Google 的 GEEK 链是硬编码常量**，按曲线二选一，长度恰好 2（root + leaf）。同一个文件里的 `generateEekChain(curve, length, eekId)`（行 217-267）是**测试用**的本地生成器（`--test_mode` 才用）。

> 关键含义：生产 CSR 必须使用 Google 预置的 GEEK 链。`getProdEekChain` 的 `kCoseEncodedRootCert` / `kCoseEncodedGeekCert` / `kCoseEncodedEcdsa256*Cert` 常量定义在 `security/keymint/support/include/...` 的某个头文件中（应为 base64 字符串常量）。**其具体字节值无法在本轮抓取中逐一确认**，但可直接复用该文件/常量的公开数值。

### 2.4 EEK 曲线支持

| 值 | 名称 | A13 是否可用 | 备注 |
|---|---|---|---|
| 0 | `CURVE_NONE` | ❌ VTS 拒绝 | 仅作默认占位；若出现，VTS 按 **X25519** 处理 |
| 1 | `CURVE_P256` | ✅ | 链签名用 ECDSA-P256，EEK 为 P-256 ECDH；坐标 **big-endian** |
| 2 | `CURVE_25519` | ✅ **首选** | 链签名用 Ed25519，EEK 为 X25519；坐标 **little-endian** |

P256 提供时的曲线映射（`remote_prov_utils.cpp:149-198`）：
- `isEek == true` 且 25519 → `generateX25519KeyPair()`；否则 → `generateED25519KeyPair()`。
- P256 分支：签名密钥与 EEK **都用** `generateEc256KeyPair()`（同一个 EC 密钥类型），仅 COSE 头部 algorithm 不同（`ES256` vs `ECDH_ES_HKDF_256`）。

### 2.5 设备如何用 EEK 加密 MacedPublicKey

**这是最容易误解的一点：设备并不"用 EEK 加密 MacedPublicKey"。**

实际流程是两层独立机制：

1. **MacedPublicKey 本身是明文 COSE_Mac0**（`MacedPublicKey.aidl`）：
   ```
   MacedPublicKey = [                        // COSE_Mac0
       protected: bstr .cbor { 1 : 5 },      // HMAC-256
       unprotected: { },
       payload : bstr .cbor PublicKey,       // 明文！不加密
       tag : bstr HMAC-256(K_mac, MAC_structure)
   ]
   MAC_structure = ["MAC0", bstr .cbor {1:5}, bstr .size 0, bstr .cbor PublicKey]
   ```
   它只被 **MAC**，不被加密。A13 的 `generateEcdsaP256KeyPair` 返回的 `macedKey` 与这里一致。

2. **EEK 加密的是"MAC 密钥 + BCC"**，产物就是 `ProtectedData`（`ProtectedData.aidl`）：
   ```
   ProtectedData = [                         // COSE_Encrypt
       protected: bstr .cbor { 1 : 3 },      // AES-GCM 256
       unprotected: { 5 : bstr .size 12 },   // IV
       ciphertext: bstr,                     // AES-GCM-256(K, .cbor ProtectedDataPayload)
       recipients : [[                       // COSE_Recipient
           protected : bstr .cbor { 1 : -25 },   // ECDH-ES + HKDF-256
           unprotected : { -1 : PubKeyX25519 / PubKeyEcdhP256,   // Ephemeral_pub
                           4 : bstr },                            // KID : EEK ID
           ciphertext : nil
       ]]
   ]
   ```
   密钥派生：
   ```
   ikm  = ECDH(EEK_pub, Ephemeral_priv)
   salt = null
   info = .cbor Context
   K    = HKDF-SHA-256(ikm, salt, info)

   Context = [
       AlgorithmID : 3,                       // AES-GCM 256
       PartyUInfo  : [bstr "client", bstr .size 0, Ephemeral_pub],
       PartyVInfo  : [bstr "server", bstr .size 0, EEK pubkey],
       SuppPubInfo : [256, bstr .size 0]
   ]
   ```
   被加密的明文：
   ```
   ProtectedDataPayload [ SignedMac, Bcc, ? AdditionalDKSignatures ]
   ```
   - `SignedMac` = COSE_Sign1，**payload 是 32 字节的 MAC key**，用 `KM_priv` 签名：
     ```
     SignedMac_structure = ["Signature1", bstr .cbor {1: alg}, bstr .cbor SignedMacAad, bstr .size 32]
     SignedMacAad = [ challenge (32..64 bytes), VerifiedDeviceInfo, tag ]
       // tag 是 COSE_Mac0 of KeysToCertify 的 tag，把密钥集合绑定到签名上
     ```
   - `Bcc` = `[ PubKeyEd25519 / PubKeyECDSA256 (DK_pub), + BccEntry ]`，第一个元素是裸 COSE_key，后续每个 `BccEntry` 是 `COSE_Sign1(BccPayload)`，由前一个 key 签名。

**所以 EEK 保护的是**「BCC（可追踪设备的设备唯一标识）+ MAC 密钥」这一段；待认证公钥 `KeysToSign` 是明文，但被 `tag`（HMAC）和 `SignedMacAad` 里嵌的同一个 `tag` 绑定在一起，防篡改。

`ProtectedData` 注释里的一条硬要求：
> None of the CBOR in ProtectedData uses CBOR tags. If an implementation includes tags, parsers may reject the data.

### 2.6 `cose_sign1_unverified` / ProtectedData 结构

- **`cose_sign1_unverified` 这个名字在 AOSP RKP AIDL 中不存在**，无法从源码确认。A13 相关结构是：`MacedPublicKey`（COSE_Mac0，非 Sign1）、`ProtectedData`（COSE_Encrypt）、`SignedMac`（COSE_Sign1，嵌在 ProtectedData 明文中）、`BccEntry`（COSE_Sign1）。若你的工具链内部用了 `cose_sign1_unverified` 作为结构体名，它对应的是 **`SignedMac`**。
- `ProtectedData` 完整结构见 §2.5；`BccPayload` 字段（A13 `ProtectedData.aidl`）：
  ```
  BccPayload = {                                // CWT [RFC8392]
      1 : tstr,                                 // Issuer
      2 : tstr,                                 // Subject
      -4670552 : bstr .cbor PubKeyEd25519 / bstr .cbor PubKeyECDSA256,  // Subject Public Key
      -4670553 : bstr,                          // Key Usage
      -4670545 : bstr,                          // Code Hash
      ? -4670546 : bstr,                        // Code Descriptor
      ? -4670547 : bstr,                        // Configuration Hash
      -4670548 : bstr .cbor { ? -70002 : tstr, ? -70003 : int, ? -70004 : null },
      -4670549 : bstr,                          // Authority Hash
      ? -4670550 : bstr,                        // Authority Descriptor
      -4670551 : bstr,                          // Mode
  }
  ```

---

## 3. API 差异

### 3.1 Android 13 是否**没有** `generateCertificateRequestV2` —— **确认没有**

证据链（三重确认）：
1. `android13-release` 的 `IRemotelyProvisionedComponent.aidl` 中 `grep -c generateCertificateRequestV2` = **0**。
2. 冻结 API `aidl_api/android.hardware.security.keymint/2/.../IRemotelyProvisionedComponent.aidl` 仅有 3 个方法（`getHardwareInfo` / `generateEcdsaP256KeyPair` / `generateCertificateRequest`）+ 5 个 `STATUS_*` 常量，**无 V2**。
3. `android13-qpr1-release`、`android13-qpr2-release`、`android13-qpr3-release` 全部同样为 `api = [1, 2, current]`、`V2_in_IRPC = 0`。

### 3.2 `generateCertificateRequestV2` 是哪个版本引入的

**Android 14（U）。** 它是 IRPC **v3** 的新增方法。

- 首次出现于 `hardware/interfaces/security/rkp/aidl/`（A14 拆分后的独立模块）。
- A14 冻结 API `aidl_api/android.hardware.security.rkp/3/.../IRemotelyProvisionedComponent.aidl` 首次包含：
  ```aidl
  byte[] generateCertificateRequestV2(in MacedPublicKey[] keysToSign, in byte[] challenge);
  const int STATUS_REMOVED = 6;   // 也在 v3 新增
  ```
  `aidl_api/.../1` 与 `.../2` 均**不含** V2。
- 签名只有 2 个参数（无 `testMode`、无 EEK、无 `out` 参数），返回完整 CSR。

### 3.3 Android 13 上调用 V2 会怎样

**Binder 层直接失败，不会进入 HAL 实现。**

AIDL 的版本化接口方法在 v1/v2 服务上不存在对应 transaction code。客户端通过生成的 proxy 调用时，会得到：
- C++ NDK：`::ndk::ScopedAStatus` 为 `EX_TRANSACTION_FAILED` / `EX_UNSUPPORTED_OPERATION`
- Rust：`binder::Status::TransactionError` 或 `UnknownTransaction`

AOSP 参考实现里的显式拒绝（Android 14 KeyMint `ta/src/rkp.rs:197-202`）：
```rust
if self.rpc_info.get_version() < IRPC_V3 {
    return Err(km_err!(Unimplemented,
        "generate_cert_req_v2 is not implemented for IRPC HAL V2 and below."));
}
```
> 注意：这是 A14 的实现。**A13 上根本走不到这一步**，因为方法不存在。

**工具链实践建议**：先 `getHardwareInfo()` 读 `versionNumber`，并 `try/catch` V2 的 transaction 失败，**双条件**判定后再决定 CSR 路径。

---

## 4. CSR 格式差异

### 4.1 Android 13 的 CSR 是 v1 还是 v3 格式

**都不是。A13 是 v1 风格的 CSR（schema version 2），与 Android 12 的 wire format 相同。**

A13 的 HAL 只返回 `(keysToSignMac, DeviceInfo, ProtectedData)` 三个碎片，**最终 CSR 由客户端组装**。A13 `system/security/provisioner/rkp_factory_extraction_tool.cpp:96-115` 给出了权威的组装格式：

```cpp
Array composeCertificateRequest(const ProtectedData& protectedData,
                                const DeviceInfo& verifiedDeviceInfo,
                                const std::vector<uint8_t>& challenge,
                                const std::vector<uint8_t>& keysToSignMac) {
    Array macedKeysToSign = Array()
        .add(std::vector<uint8_t>(0))   // empty protected headers as bstr
        .add(Map())                     // empty unprotected headers
        .add(Null())                    // nil for the payload
        .add(keysToSignMac);            // MAC as returned from the HAL

    Array deviceInfo =
        Array().add(EncodedItem(verifiedDeviceInfo.deviceInfo)).add(Map());  // empty device info

    Array certificateRequest = Array()
        .add(std::move(deviceInfo))     // [ DeviceInfo, {} ]
        .add(challenge)
        .add(EncodedItem(protectedData.protectedData))
        .add(std::move(macedKeysToSign));
    return certificateRequest;
}
```

即 **A13 CSR 顶层 = 4 元素 CBOR array**：
```
CertificateRequest = [
    [ DeviceInfo, {} ],           // [ VerifiedDeviceInfo(bstr/.cbor), UnverifiedDeviceInfo(map) ]
    challenge : bstr,
    ProtectedData : COSE_Encrypt,
    MacedKeys : COSE_Mac0 [ bstr(empty protected), {}, nil, tag ]
]
```

### 4.2 `AuthenticatedRequest` 的结构差异

**`AuthenticatedRequest` 在 Android 13 中不存在** —— 它是 **v3（A14+）** 才引入的概念。A13 侧完全没有任何 `AuthenticatedRequest` 结构。

A14+ 的 `AuthenticatedRequest<CsrPayload>`（`android14-release/.../IRemotelyProvisionedComponent.aidl:327-354`）：
```
Csr = AuthenticatedRequest<CsrPayload>

CsrPayload = [
    version: 3,                     ; CsrPayload CDDL Schema version
    CertificateType,                ; tstr: "widevine" / "keymint"
    DeviceInfo,
    KeysToSign,
]

AuthenticatedRequest<T> = [
    version: 1,                     ; AuthenticatedRequest CDDL Schema version
    UdsCerts,
    DiceCertChain,
    SignedData<[
        challenge: bstr .size (0..64),
        bstr .cbor T,
    ]>,
]
```

**逐项对比 v2 (A13) vs v3 (A14+)：**

| 维度 | A13 / v2 | A14+ / v3 |
|---|---|---|
| 顶层结构 | 4 元组（见 §4.1） | `AuthenticatedRequest<CsrPayload>` 4 元组 |
| 顶层 version 字段 | ❌ 无 | ✅ `1` |
| CSR payload version | 藏在 `DeviceInfo.version` = `2` | `CsrPayload[0]` = `3` |
| `CertificateType` | ❌ 不存在 | ✅ `"keymint"` / `"widevine"` |
| EEK / ProtectedData | ✅ 必需 | ❌ 移除 |
| `testMode` | ✅ 存在于方法签名 | ❌ 移除（`STATUS_REMOVED`） |
| 加密层 | AES-GCM-256 + ECDH，多层签名/MAC | 单层 `COSE_Sign1` |
| 签名主体 | `KM_priv` 签 MAC key（`SignedMac`） | `CDI_Leaf_Priv` 签整个 payload |
| DICE/UDS | `Bcc` 在 ProtectedData 内（加密） | `DiceCertChain` 明文 + `UdsCerts` |
| 密钥上限 | 无 `supportedNumKeysInCsr` | ✅ `supportedNumKeysInCsr`（默认 4，`MIN`=20） |
| 签名算法 | EdDSA / ES256 | EdDSA / ES256 / **ES384** |
| 哈希 | SHA-256 | SHA-256 / SHA-384 / SHA-512 |

### 4.3 DeviceInfo 字段差异

A13 的 `DeviceInfo.aidl`（CDDL）：
```
DeviceInfo = {
    "brand" : tstr,
    "manufacturer" : tstr,
    "product" : tstr,
    "model" : tstr,
    "device" : tstr,
    "vb_state" : "green" / "yellow" / "orange",     // 注意没有 "red"
    "bootloader_state" : "locked" / "unlocked",
    "vbmeta_digest": bstr,
    ? "os_version" : tstr,                          // 对 TEE 而言非可选
    "system_patch_level" : uint,                    // YYYYMMDD
    "boot_patch_level" : uint,
    "vendor_patch_level" : uint,
    "version" : 2,                                  // CDDL schema version
    "security_level" : "tee" / "strongbox",
    "fused": 1 / 0,
}
```

`security/rkp/CHANGELOG.md` 的 v1→v2 变更（**这正是 A12→A13 的差异**）：
```
## IRemotelyProvisionedComponent 1 -> 2
* DeviceInfo
  * Most entries are no longer optional.
  * `att_id_state` is now `fused`. `fused` is used to indicate if SecureBoot is enabled.
  * `version` is now `2`.
  * `board` has been removed.
  * `device` has been added.
* RpcHardwareInfo
  * `uniqueId` String added as a field in order to differentiate IRPC instances on device.
```

**A12 → A13 的 DeviceInfo 字段差异：**
| 变更 | v1 (A12) | v2 (A13) |
|---|---|---|
| `att_id_state` | 存在 | **删除**，改为 `fused` |
| `board` | 存在 | **删除** |
| `device` | 不存在 | **新增** |
| `version` | `1` | `2` |
| 可空性 | 多数可选 | **多数变必填** |

A14 (v3) 中 `DeviceInfo` 基本沿用 v2 字段集，但**版本号移出**：
```
## IRemotelyProvisionedComponent 2 -> 3
* DeviceInfo
  * `version` has moved to a top-level field within the CSR generated by the HAL.
```

**参考实现的实际编码顺序**（A14 KeyMint `ta/src/rkp.rs:95-110`），注意用的是 **RFC 7049** 而非 8949 的规范化：
```rust
// The DeviceInfo.aidl file specifies that map keys should be ordered according
// to RFC 7049 canonicalization rules, which are:
// - shorter-encoded key < longer-encoded key
// - lexicographic comparison for same-length keys
// Note that this is *different* than the ordering required in RFC 8949 s4.2.1.
```
实际写入顺序：`brand, fused, model, device, product, vb_state, os_version, manufacturer, vbmeta_digest, security_level, boot_patch_level, bootloader_state, system_patch_level, vendor_patch_level`。

> ⚠️ **实现提醒**：`vb_state` 的映射里没有 `"red"` 的合法值，但参考实现把 `VerifiedBootState::Failed` 映射成 `"red"`（`ta/src/rkp.rs:78`）。属上游代码的不一致，服务端可能会拒。

---

## 5. keystore2 的 RKP 集成

### 5.1 Android 13 keystore2 怎么处理 RKP

A13 的 keystore2 **直接持有 HAL binder**，不走 RKPD daemon。`system/security/keystore2/src/remote_provisioning.rs`：

```rust
pub struct RemoteProvisioningService {
    device_by_sec_level: HashMap<SecurityLevel, Strong<dyn IRemotelyProvisionedComponent>>,
    curve_by_sec_level:  HashMap<SecurityLevel, i32>,   // 缓存 supportedEekCurve
}
```
- `new_native_binder()`（行 211-232）：启动时对 TEE（可选 StrongBox）取 `IRemotelyProvisionedComponent`，并**缓存 `supportedEekCurve`**。
- `generate_csr()`（行 264-303）：从 DB 取未签名的 attestation keys → `dev.generateCertificateRequest(...)` → 把返回的 MAC 和每个 `MacedPublicKey` 的 COSE_Mac0 payload 拼成 CBOR array 返回。

**A13 特有的 AIDL**（`keystore2/aidl/android/security/remoteprovisioning/IRemoteProvisioning.aidl`）：
```aidl
byte[] generateCsr(in boolean testMode, in int numCsr, in byte[] eek, in byte[] challenge,
                   in SecurityLevel secLevel, out ProtectedData protectedData,
                   out DeviceInfo deviceInfo);
void provisionCertChain(in byte[] publicKey, in byte[] batchCert, in byte[] certs,
                        in long expirationDate, in SecurityLevel secLevel);
void generateKeyPair(in boolean is_test_mode, in SecurityLevel secLevel);
ImplInfo[] getImplementationInfo();

parcelable ImplInfo { SecurityLevel secLevel; int supportedCurve; }
```
→ **`eek` 是调用方传入的 `byte[]`**，且 `ImplInfo` 只暴露 `supportedCurve`，让上层（GmsCore）知道该用哪条 GEEK 链。

**A14+ 的架构变化**：`remoteprovisioning` AIDL 从 keystore2 移出（A14 `keystore2/aidl/android/security/` 下已无该目录），keystore2 改为通过 **`rkpd_client.rs`**（A14 新增）向 **RKPD** 请求 attestation key。A13 **没有** `rkpd_client.rs`（404），也没有 `platform/packages/modules/RemoteKeyProvisioning` mainline 模块（A14 才有）。

### 5.2 `remote_provisioning.tee.rkp_only` 在 Android 13 存在吗 —— **存在**

A13 `remote_provisioning.rs:81-92`：
```rust
fn is_rkp_only(&self) -> bool {
    let default_value = false;
    let property_name = match self.security_level {
        SecurityLevel::STRONGBOX => "remote_provisioning.strongbox.rkp_only",
        SecurityLevel::TRUSTED_ENVIRONMENT => "remote_provisioning.tee.rkp_only",
        _ => return default_value,
    };
    rustutils::system_properties::read_bool(property_name, default_value).unwrap_or(default_value)
}
```
- 属性名：`remote_provisioning.tee.rkp_only` / `remote_provisioning.strongbox.rkp_only`
- **默认值 `false`**（即默认混合模式）
- A14 的同名函数完全一致（`a14 remote_provisioning.rs:62-63`），行为未变。
- 语义：`rkp_only == true` → **绝不回退**到出厂密钥，RKP 失败直接报错。

### 5.3 没有 RKP 的设备怎么回退到非 RKP 证明

**A13 是"混合模式（hybrid）"设计**，回退逻辑在 `check_rem_prov_enabled()`（行 98-116）与 `get_remotely_provisioned_attestation_key_and_certs()`（行 139-186）：

```rust
fn check_rem_prov_enabled(&self, db: &mut KeystoreDB) -> Result<bool> {
    if self.is_rkp_only() { return Ok(true); }            // ① rkp_only 强制开启
    if !self.is_hal_present.load(Relaxed)
        || get_remotely_provisioned_component(&self.security_level).is_err() {
        self.is_hal_present.store(false, Relaxed);
        return Ok(false);                                  // ② 无 HAL → 关闭 RKP
    }
    // ③ 混合系统：看池里是否已有 key（服务器是否已下发过）
    let pool_status = db.get_attestation_pool_status(0, &self.km_uuid)?;
    Ok(pool_status.total != 0)
}
```

回退决策（行 146-165）：
```rust
if !self.is_asymmetric_key(params) || !self.check_rem_prov_enabled(db)? {
    // 返回 None → 底层 KM 实例使用出厂密钥（factory provisioned key）
    Ok(None)
} else {
    match get_rem_prov_attest_key(...) {
        Err(e) => {
            if self.is_rkp_only() { return Err(e); }       // rkp_only：直接失败
            log_rkp_error_stats(MetricsRkpError::FALL_BACK_DURING_HYBRID, &sec_level);
            Ok(None)                                       // 否则回退
        }
        Ok(v) => v,
    }
}
```

**回退的三条判定路径：**
1. `rkp_only == true` 且 HAL 不存在 → 仍"启用"，但后续必然失败报错（**无回退**）。
2. `rkp_only == false` 且 **无 IRPC HAL**（Android 13 早期未实现 RKP 的设备）→ `check_rem_prov_enabled` 返回 `false` → **始终使用出厂密钥**（永远不产生 RKP CSR）。
3. `rkp_only == false` 且 HAL 存在但池为空 / 取 key 出错 → 回退出厂密钥，并上报 `FALL_BACK_DURING_HYBRID` 指标。
4. "Hybrid" 的翻转机制：只要服务器下发过一次 key（`pool_status.total != 0`），RKP 即被激活，之后正常走 RKP 路径。

---

## 6. 实际兼容性

### 6.1 AOSP 参考实现如何在 Android 13 生成 CSR

**关键事实：`platform/system/keymint` 仓库在 `android13-release` 不存在（HTTP 404），A14 才引入。**

A13 上 KeyMint TA 参考实现的实际归属 **无法确认**（AOSP 未在 `platform/system/keymint` 提供 A13 版本）。但 A13 可用的**权威参考代码**是：

| 组件 | 路径（android13-release） |
|---|---|
| HAL AIDL | `hardware/interfaces/security/keymint/aidl/android/hardware/security/keymint/IRemotelyProvisionedComponent.aidl` |
| HAL 辅助库（EEK、CBOR、BCC 校验） | `hardware/interfaces/security/keymint/support/remote_prov_utils.cpp` |
| 客户端 CSR 组装 | `system/security/provisioner/rkp_factory_extraction_tool.cpp` |
| keystore2 服务 | `system/security/keystore2/src/remote_provisioning.rs` |
| VTS | `hardware/interfaces/security/keymint/aidl/vts/functional/VtsRemotelyProvisionedComponentTests.cpp` |

A13 提取工具的调用序列（`rkp_factory_extraction_tool.cpp:174-190`）—— **这就是你的工具链在 A13 上要复刻的流程**：
```cpp
getHardwareInfo(&hwInfo);                                   // 1. 读 supportedEekCurve
generateCertificateRequest(
    FLAGS_test_mode,
    emptyKeys,                                             // 2. 空 keysToSign（仅取 BCC）
    getEekChain(hwInfo.supportedEekCurve),                  // 3. 传入 GEEK 链
    challenge,                                              // 4. 16 字节随机 challenge
    &verifiedDeviceInfo, &protectedData, &keysToSignMac);   // 5. 三个 out 参数
composeCertificateRequest(protectedData, verifiedDeviceInfo, challenge, keysToSignMac);
```
其中 `kChallengeSize = 16`（行 52），`getEekChain` 在非 test_mode 下返回 `getProdEekChain(curve)`（行 132）。

> 注意 A13 与 A14 的分工差异：A14 的 KeyMint `ta/src/rkp.rs` 里 `generate_cert_req()`（v1/v2 路径，行 178-190）在参考实现中**只是 `Unimplemented` 的 TODO 占位**：
> ```rust
> if self.rpc_info.get_version() > IRPC_V2 {
>     return Err(rpc_err!(Removed, "generate_cert_req is not supported in IRPC V3+ HAL."));
> }
> let _device_info = self.rpc_info()?;
> Err(km_err!(Unimplemented, "TODO: GenerateCertificateRequest"))
> ```
> **含义**：AOSP 的 Rust KeyMint 参考实现从未真正实现 v1/v2 的 `generateCertificateRequest`。A13 的真实实现全部由 **SoC 厂商（Qualcomm/MTK/Google）自己用 C++/Rust 写**。这也解释了为什么 A13 的 RKP 行为在不同设备上差异很大。

### 6.2 Qualcomm / MTK 的 Android 13 实现已知问题

**AOSP 源码中无法确认厂商私有实现的具体缺陷**。AOSP 只提供上述参考库与 VTS。以下为**基于 AOSP 代码可推断的**高风险点（非厂商 bug 断言）：

1. **`supportedEekCurve` 上报错误**：不少早期实现返回 `CURVE_NONE` 或与实际 GEEK 曲线不符。VTS `supportsValidCurve` 会失败。若上报 P256 但 HAL 内实际按 X25519 处理，`generateCertificateRequest` 会返回 `STATUS_INVALID_EEK (5)`。
2. **P256 坐标字节序**：AIDL 明确 **P256 为 big-endian，Ed25519 为 little-endian**。混用会导致 EEK 校验失败或服务器解密失败。`remote_prov_utils.cpp` 的 `getAffineCoordinates()` 负责转换。
3. **`os_version` 必填**：AIDL 注释 *"Not optional for TEE."*。早期实现容易漏填。
4. **`vb_state` 的 `"red"`**：AOSP 参考实现会产出非法值（见 §4.3 提醒）。
5. **`uniqueId` 缺失或超长**：A13 VTS `uniqueId` 测试要求 v2 设备返回 `1..=32` 字符；缺失会导致 VTS 失败，且 keystore2 用它区分同一设备上的多个 IRPC 实例（`remote_provisioning.rs:646-651`）。
6. **DeviceInfo 的 RFC 7049 规范化排序**：服务端按 7049 校验，实现容易按 8949 排序而失败。
7. **`ProtectedData` 中禁止 CBOR tag**：AIDL 明确要求"不使用 CBOR tags"，带 tag 会被解析器拒绝。
8. **BCC 为"degenerate"（单证书）**：AIDL 允许退化 BCC（`DK_pub == KM_pub`），很多早期 A13 设备采用此形式。服务端若要求完整 DICE 链可能拒签。
9. **testMode 与签名校验**：`testMode == true` 时 HAL **必须忽略链签名**；某些实现仍会校验，导致测试链路被误拒。

---

## 7. 迁移建议（Android 16 HAL v3 工具链 → 兼容 Android 13）

| 步骤 | 动作 |
|---|---|
| 1 | `getHardwareInfo()` 读 `versionNumber`，并以 `try { generateCertificateRequestV2(keys, challenge) } catch (TransactionError)` 做双条件判定 |
| 2 | `versionNumber >= 3` → 走现有 v3 路径（`AuthenticatedRequest<CsrPayload>`） |
| 3 | `versionNumber <= 2` → 走 A13 路径：<br>• 读 `supportedEekCurve`，选 `CURVE_25519`(优先) / `CURVE_P256` 的 **Google GEEK 链**<br>• `generateEcdsaP256KeyPair(testMode=false, &maced)` 逐个生成<br>• `generateCertificateRequest(false, keys, geekChain, challenge, &deviceInfo, &protectedData)`<br>• 用 §4.1 的 4 元组格式自行组装 CSR |
| 4 | 请求体：`v1:signCertificates` 的 `csr` 字段放**原始 CBOR 二进制**（A13 提取工具默认 `--output_format=csr` 即二进制）；不要套 `AuthenticatedRequest` |
| 5 | challenge 长度：A13 要求 **32–64 字节**（`SignedMacAad.challenge = bstr .size (32..64)`）；`MAX_CHALLENGE_SIZE_V2 = 64` |
| 6 | 若设备 `supportedEekCurve == 0`（不合规），按 **X25519** 处理（VTS 行为），但应视为设备缺陷 |
| 7 | 记牢 `testMode` 在 A13 必须显式传 `false`，且此时 HAL **会真正校验 GEEK 链签名**，必须用 Google 生产 GEEK 链 |

---

## 8. 无法确认项（明确声明）

| 项 | 状态 |
|---|---|
| `fetchEekChain` 方法 | **AOSP 中不存在**（所有分支 grep 计数为 0）。EEK 链由客户端生成/内置，不是 HAL API |
| `cose_sign1_unverified` 名称 | **AOSP 中不存在**。A13 对应结构为 `SignedMac`（COSE_Sign1） |
| GEEK 链常量的具体字节值 | 位于 `security/keymint/support/include/` 的头文件中，本轮未逐字节抓取内容；建议直接取用该仓库公开常量 |
| Android 13 KeyMint TA 参考实现仓库位置 | `platform/system/keymint` **A13 不存在**；A13 的实际 TA 参考实现在 AOSP 中的位置无法确认 |
| Qualcomm / MTK A13 私有实现的具体已知 bug | AOSP 源码无法确认；§6.2 为基于 AOSP 代码可推断的风险点 |
| A13 设备 `versionNumber` 的实际取值分布 | 定义上应为 `2`；厂商实际返回值无法从 AOSP 确认 |

---

## 9. 源码引用汇总

| 主题 | 路径 / 行号 |
|---|---|
| A13 IRPC AIDL | `hardware/interfaces/security/keymint/aidl/android/hardware/security/keymint/IRemotelyProvisionedComponent.aidl` @ `android13-release` |
| A13 `RpcHardwareInfo` | 同目录 `RpcHardwareInfo.aidl`（`CURVE_*` 常量、`uniqueId`） |
| A13 冻结 API v2 | 同目录 `aidl_api/android.hardware.security.keymint/2/...` |
| A13 `ProtectedData` | `.../keymint/ProtectedData.aidl`（COSE_Encrypt、Context、SignedMacAad、BccPayload） |
| A13 `MacedPublicKey` | `.../keymint/MacedPublicKey.aidl`（COSE_Mac0） |
| A13 `DeviceInfo` | `.../keymint/DeviceInfo.aidl`（CDDL，version 2） |
| A14 IRPC（v3，含 V2） | `hardware/interfaces/security/rkp/aidl/android/hardware/security/keymint/IRemotelyProvisionedComponent.aidl` @ `android14-release`，行 101-107、306-308、327-354、487 |
| 版本变更日志 | `hardware/interfaces/security/rkp/CHANGELOG.md` @ `android14-release` |
| EEK 工具库 | `hardware/interfaces/security/keymint/support/remote_prov_utils.cpp` @ `android13-release`，行 149-198、217-267、269-283 |
| A13 客户端 CSR 组装 | `system/security/provisioner/rkp_factory_extraction_tool.cpp` @ `android13-release`，行 96-115、174-190 |
| A13 keystore2 RKP | `system/security/keystore2/src/remote_provisioning.rs` @ `android13-release`，行 81-92、98-116、139-186、264-303、416-422 |
| A13 `IRemoteProvisioning` | `system/security/keystore2/aidl/android/security/remoteprovisioning/IRemoteProvisioning.aidl` @ `android13-release` |
| A13 VTS | `hardware/interfaces/security/keymint/aidl/vts/functional/VtsRemotelyProvisionedComponentTests.cpp` @ `android13-release`，行 45、218-260、363-394 |
| A14 KeyMint v3 生成 | `system/keymint/ta/src/rkp.rs` @ `android14-release`，行 47-131、178-202、247-284 |
| A14 keystore2 RKPD 客户端 | `system/security/keystore2/src/rkpd_client.rs` @ `android14-release`（A13 无此文件） |
