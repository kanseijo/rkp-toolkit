# RKP 签发工具链 / RKP Provisioning Toolkit

从设备申请 Google 远程密钥配置（RKP）证书，打包成 TEESimulator 可用的 `keybox.xml`。

Requests Google Remote Key Provisioning (RKP) certificates from a device and
packages them into a `keybox.xml` usable by TEESimulator.

---

## 目录 / Contents

| 章节 / Section | 说明 / What it covers |
|---|---|
| [支持情况](#支持情况--support-status) | **先看这个**：哪些设备实测可用 / **start here**: tested devices |
| [前置要求](#前置要求--requirements) | Termux 版本、root、网络 / Termux version, root, network |
| [快速开始](#快速开始--quick-start) | 下载 Release 即可用 / just download a Release |
| [从源码构建](#从源码构建--build-from-source) | 改代码或自行审计 / modify or audit |
| [环境自动配置](#环境自动配置--automatic-setup) | 换源测速与依赖安装 / mirrors and dependencies |
| [命令](#命令--commands) | 各子命令的作用 / what each subcommand does |
| [自测](#自测--self-test) | 不连设备的本地验证 / offline verification |
| [产物位置](#产物位置--output-location) | keybox.xml 落在哪 / where the keybox lands |
| [排查](#排查--troubleshooting) | 常见错误对照表 / common errors |
| [重要声明](#重要声明--important-notice) | **烧录过 TEE 很可能失败** / **TEE reflashing breaks RKP** |
| [已知限制](#已知限制--known-limitations) | 能力边界 / scope and limits |
| [许可证与来源](#许可证与来源--license-and-provenance) | MIT + AOSP 归属 / MIT + AOSP attribution |

技术细节 / Technical details:

- [流程](#流程--protocol-flow) · [Android 版本差异](#android-版本差异--android-version-differences) · [keybox.xml 两个硬性要求](#keyboxxml-的两个硬性要求--two-hard-requirements)
- [证书有效期](#证书有效期--certificate-lifetime) · [版本号探测](#版本号探测--version-probing) · [项目结构](#项目结构--repository-layout)

---

## 支持情况 / Support Status

按实测结果记录，不是承诺。/ Recorded from actual testing, not a promise.

| 设备 / Device | 系统 / OS | 结果 / Result |
|---|---|---|
| OnePlus Ace 6 / 一加 Ace 6 (PLQ110) | Android 16 | ✅ 成功 / works |
| ASUS ROG Phone 9 Pro / 玩家国度 9 Pro | Android 15 | ✅ 成功 / works |
| Redmi Note 11 / 红米 Note 11 (spes, global) | Android 13 | ❌ 无 RKP HAL / no RKP HAL |
| OnePlus Ace 3 / 一加 Ace 3 (PJE110) | A15 系统 + A13 vendor | ❌ HAL 未实现 v2 / no v3 method |

---
---

## 前置要求 / Requirements

### Termux v0.118.3 或更高 / Termux v0.118.3 or newer

**必须使用 v0.118.3 及以上版本。**

旧版本存在软件源签名与包管理问题，尤其是 Google Play 上那份停留在
**0.118.0** 的构建——它已被官方放弃，`setup.sh` 换源后会无法更新索引，
表现为 `索引更新失败 / index update failed`。

**Version v0.118.3 or newer is required.**

Older builds have repository-signature and package-management problems. This is
especially true of the **0.118.0** build on Google Play, which the project has
abandoned: after `setup.sh` switches mirrors, the index update fails with
`索引更新失败 / index update failed`.

下载地址 / Download:

| 来源 / Source | 链接 / Link |
|---|---|
| **GitHub Releases**（推荐 / recommended） | <https://github.com/termux/termux-app/releases/> |
| **F-Droid**（中文 / Chinese） | <https://f-droid.org/zh/packages/com.termux/> |
| **F-Droid**（English） | <https://f-droid.org/en/packages/com.termux/> |

> ⚠️ **不要从 Google Play 安装 Termux。**
> Play 版本停留在 0.118.0 且已停止维护，无法正常使用现代软件源。
>
> **Do not install Termux from Google Play.**
> The Play build is stuck at 0.118.0 and unmaintained; it cannot use current
> repositories.

确认版本 / Check the version:

```bash
echo $TERMUX_VERSION
# 期望 / expected: 0.118.3 或更高 / or newer
```

`issue.sh` 启动时会检查版本，低于 0.118.3 会给出提示。
`issue.sh` checks this on startup and warns when the version is below 0.118.3.

### 其他要求 / Other requirements

| 要求 / Requirement | 说明 / Notes |
|---|---|
| Android 12 或更高 / Android 12 or newer | 需要 RKP HAL / needs the RKP HAL |
| 已 root / rooted device | 调用 RKP HAL 需要 root / root is required |
| 能访问 Google 服务器 / Google reachable | `remoteprovisioning.googleapis.com` |

---

## 快速开始 / Quick Start

在设备上操作（Termux + root）/ Do this on the device (Termux + root).

### 1. 下载 / Download

从 [Releases](https://github.com/kanseijo/rkp-toolkit/releases/latest) 下载
最新的 `rkp-toolkit.zip`；也可以在 Termux 里一条命令搞定。

Download the latest `rkp-toolkit.zip` from
[Releases](https://github.com/kanseijo/rkp-toolkit/releases/latest), or use a
single command in Termux:

```bash
pkg install -y unzip curl
curl -LO https://github.com/kanseijo/rkp-toolkit/releases/latest/download/rkp-toolkit.zip
```

> 这个链接始终指向最新版，文件名不变。
> This link always points at the newest release; the filename never changes.

想固定版本 / To pin a specific version:

```bash
curl -LO https://github.com/kanseijo/rkp-toolkit/releases/download/v1.0.0/rkp-toolkit-v1.0.0.zip
```

### 2. 解压 / Unpack

```bash
unzip rkp-toolkit.zip
cd rkp-toolkit
```

### 校验（可选）/ Verify (optional)

```bash
curl -LO https://github.com/kanseijo/rkp-toolkit/releases/latest/download/SHA256SUMS
sha256sum -c SHA256SUMS
```

### 3. 运行 / Run

```bash
./issue.sh
```

首次运行会自动检测依赖，缺失时调用 `setup.sh` 完成换源与安装。
On first run it checks dependencies and calls `setup.sh` to switch mirrors and
install anything missing.

### 4. 取产物 / Collect the output

成功后产物在归档目录里 / the result lands in a dated batch directory:

```
~/rkp_out/2026-11-21_0001/keybox.xml
```

每次运行（成功或失败）都会归档，编号按当天递增。
Every run — successful or not — is archived, numbered per day.

> ⚠️ **`keybox.xml` 含设备私钥，切勿提交或公开分享。**
> 详见 [SECURITY.md](SECURITY.md)
>
> **The generated `keybox.xml` holds a device private key. Never commit or
> share it.** See [SECURITY.md](SECURITY.md)

### 从源码运行 / Running from source

需要 git 和 Java 构建环境时见 [从源码构建](#从源码构建--build-from-source)。
See [Build from source](#从源码构建--build-from-source) if you want to work from
the repository or rebuild the jar.

---

## 从源码构建 / Build from Source

只有改代码或想自行审计二进制时才需要这一节。普通用户直接用 Release 里的包，
里面已含编译好的 `rkp_bridge.jar`。

You only need this section to modify the code or audit the binary yourself.
Regular users should use the Release archive, which already contains a
prebuilt `rkp_bridge.jar`.

### 克隆 / Clone

```bash
git clone https://github.com/kanseijo/rkp-toolkit.git
cd rkp-toolkit
```

### Python 部分 / The Python part

无需构建，直接运行 / nothing to build, run it directly:

```bash
python3 rkp_csr.py          # 核心自测 / core self-test
./issue.sh
```

### Java 部分 / The Java part

`src/RkpBridge.java` 通过反射调用 RKP HAL，需要编译成 dex 再打包进 jar。
`src/RkpBridge.java` calls the RKP HAL reflectively and must be compiled to dex
and packed into a jar.

**需要 / Requires:**

| 依赖 / Dependency | 用途 / Purpose |
|---|---|
| JDK 8+ | `javac` 编译 `.java` |
| `android.jar`（或等价 stub） | 编译期 Android API |
| D8（`r8.jar` 中的 `com.android.tools.r8.D8`） | class → dex |
| Python 3 | 把 `classes.dex` 写入 jar |

**构建 / Build:**

```bash
sh build.sh <android.jar> [r8.jar]
```

生成 / produces `rkp_bridge.jar`（约 9.6 KB），覆盖仓库里的旧文件。
overwriting the committed copy.

**获取依赖 / Where to get them:**

- `android.jar`：Android SDK `platforms/android-35/android.jar`，或 AOSP 构建产物
- `r8.jar`：Android SDK `build-tools/*/lib/r8.jar`，或
  [r8 发布页](https://r8.googlesource.com/r8)

- `android.jar`: Android SDK `platforms/android-35/android.jar`, or an AOSP build
- `r8.jar`: Android SDK `build-tools/*/lib/r8.jar`, or the
  [r8 releases](https://r8.googlesource.com/r8)

### 验证二进制与源码一致 / Verify the binary matches the source

```bash
# 重新构建后应当与仓库中的文件功能等价
sh build.sh <android.jar> r8.jar
python3 -c "
import zipfile
d = zipfile.ZipFile('rkp_bridge.jar').read('classes.dex')
for k in [b'generateCertificateRequestV2', b'RKP_CALLER_UID', b'strongbox']:
    print(k.decode(), 'OK' if k in d else 'MISSING')
"
```

> 用相同版本的 JDK 与 D8 构建时，`classes.dex` 是**字节级可复现**的（已实测）。
> jar 外层只是 zip 容器，压缩时间戳会让文件哈希不同，但 dex 内容一致。
>
> Built with the same JDK and D8 versions, `classes.dex` is **byte-for-byte
> reproducible** (verified). The jar's zip container adds timestamps that change
> the file hash, but the dex content is identical.

---

## 环境自动配置 / Automatic Setup

`setup.sh` 全自动完成：测速选源 → 写入 sources.list → 装依赖 → 自检。

`setup.sh` does everything automatically: benchmark mirrors -> write
sources.list -> install dependencies -> self-check.

```bash
sh setup.sh                # 自动测速并选最快的源 / pick the fastest mirror
sh setup.sh --cn           # 只用国内源 / mainland China mirrors only
sh setup.sh --global       # 只用国外源 / global mirrors only
sh setup.sh --mirror URL   # 指定源 / use a specific mirror
sh setup.sh --dry-run      # 只测速，不改任何文件 / benchmark only
sh setup.sh --restore      # 恢复原始配置 / restore the original config
```

### 测速原理 / How benchmarking works

对每个镜像请求其 `termux-main/dists/stable/Release`（几十 KB），
按真实往返时间排序，取最快的。

For each mirror it fetches `termux-main/dists/stable/Release` (tens of KB) and
sorts by real round-trip time, then picks the fastest.

内置镜像列表 / built-in mirror list:

| 地区 / region | 镜像 / mirror |
|---|---|
| 国内 / CN | 清华 TUNA、北大 PKU、北外 BFSU、南大 NJU、山大 SDU、华中科大 HUST、中科大 USTC |
| 国外 / global | 官方 Cloudflare（`packages-cf.termux.dev`）、官方源站（`packages.termux.dev`） |

### 写入的内容 / What gets written

```bash
# /data/data/com.termux/files/usr/etc/apt/sources.list
deb https://<最快镜像>/termux/apt/termux-main/ stable main
```

### 镜像失败会自动换 / automatic mirror fallback

写完配置后会试跑 `pkg update`。如果失败，**自动换到下一个更快的镜像重试**，
直到某个成功。全部失败时：

- 依赖已齐全 → 恢复原配置，继续执行（不影响本次任务）
- 依赖缺失 → 恢复原配置，退出并给出排查建议

After writing the config it runs `pkg update`. If that fails it **moves on to the
next-fastest mirror and retries**, until one works. If every mirror fails:

- dependencies already present -> restore the original config and continue
- dependencies missing -> restore the original config, exit with suggestions

### 全部镜像都失败时 / when every mirror fails

`setup.sh` 会打印 apt 的**真实报错**（最后 12 行），并自动判断是不是本地问题：

`setup.sh` prints apt's **real error** (last 12 lines) and works out whether the
failure is local:

| 识别到 / detected | 含义 / meaning | 处理 / action |
|---|---|---|
| `lock` | 锁被占用 / a lock is held | 停止换源，提示 `--repair` |
| `dns` | 域名解析失败 / DNS failure | 提示检查网络 |
| `keyring` | GPG 签名失败 / signature failure | 提示 `termux-change-repo` |
| `disk` | 空间不足 / out of space | 提示清理 |

本地问题换镜像也没用，所以会**立即停止**，不会白试 8 个源。
A local problem is not fixed by another mirror, so it **stops immediately**
instead of trying all 8.

修复命令 / repair:

```bash
sh setup.sh --repair     # 清理 apt 锁和旧索引 / clear locks and stale indexes
```

注意：备份只在当前配置**不是本工具生成**时才创建，避免把坏配置当成"原始配置"备份。
Note: a backup is only taken when the current config was **not** generated by
this tool, so a broken config never becomes the "pristine" backup.

原配置备份到 `sources.list.rkp.bak`，可随时 `--restore` 还原。
本次选择记录在 `sources.list.rkp.current`。

The original file is backed up to `sources.list.rkp.bak` and can be restored at
any time with `--restore`. The current choice is recorded in
`sources.list.rkp.current`.

如果装过 `root-repo`，它写入的 `sources.list.d/root.list` 也会同步指向同一镜像。
If `root-repo` is installed, its `sources.list.d/root.list` is repointed at the
same mirror.

### 为什么不用 pip 装 cryptography

Termux 上的 pip 轮子链接不到系统的 `libssl`，`import cryptography` 会报
`undefined symbol`。用 `pkg install python-cryptography` 才是对的。脚本优先
用 pkg，失败才回退 pip。

On Termux, pip wheels cannot link against the system `libssl` and
`import cryptography` fails with `undefined symbol`. Use
`pkg install python-cryptography` instead. The script prefers pkg and only
falls back to pip if that fails.

---

## 依赖 / Dependencies

- **Python 3.8+**，需要 `cryptography` / requires `cryptography`
  ```bash
  pip install cryptography
  ```
- **root**（必须是 Magisk/KernelSU/APatch 提供的 `su`，Termux 自带的是假的）
  / **root** via Magisk/KernelSU/APatch; the `su` bundled with Termux is fake
- **rkp_bridge.jar**（同目录 / same directory, 7554 字节 / bytes）

---

## 命令 / Commands

```bash
python3 rkp_toolkit.py check     # 环境自检 / self-check environment
python3 rkp_toolkit.py all       # 完整流程 / full pipeline -> keybox.xml
python3 rkp_toolkit.py issue     # 只申请证书 / fetch certs only (certs.der + key.der)
python3 rkp_toolkit.py keybox    # 只打包 / package only (from existing artifacts)
python3 rkp_toolkit.py verify    # 验证 keybox.xml / verify an existing keybox.xml
```

常用参数 / Common options:

```bash
--workdir rkp_out      # 中间产物目录 / intermediate directory (default: rkp_out)
-o keybox.xml          # 输出路径 / output path
--fingerprint "..."    # 提交给 Google 的 build fingerprint / fingerprint sent to Google
```

---

## 自测 / Self-Test

`rkp_csr.py` 带完整自测，不需要设备也不需要网络：
`rkp_csr.py` ships with a full self-test that needs no device and no network:

```bash
python3 rkp_csr.py
```

它会逐项校验 CSR 结构（顶层 4 元素、DeviceInfo 对、MacedKeys 内部的
空 bstr / 空 map / nil），并验证 5 种非法输入都被正确拒绝。
It checks every part of the CSR structure (4-element top level, the DeviceInfo
pair, and the empty bstr / empty map / nil inside MacedKeys), and confirms that
5 kinds of invalid input are rejected.

---

## 流程 / Protocol Flow

```
A. POST /v1:fetchEekChain        -> challenge + EEK 链 / EEK chain
B. app_process 调 / calls RKP HAL -> CSR + 私钥 / private key (TEE-generated)
C. POST /v1:signCertificates      -> Google 签发的证书链 / chain signed by Google
D. 打包 / package                 -> keybox.xml
```

注意 A 和 C 的路径是 **gRPC 风格**（`/v1:fetchEekChain`，冒号不是斜杠），
写成 `/v1/fetchEekChain` 会 404。

Note that A and C use **gRPC-style paths** (`/v1:fetchEekChain`, colon not
slash). Writing `/v1/fetchEekChain` returns 404.

---

## Android 版本差异 / Android Version Differences

两条路径差异很大，工具会自动选择。
The two paths differ substantially; the tool selects one automatically.

### RKP 后端从哪里来 / Where the RKP backend comes from

两个版本的后端**位置不同**，自检会分别检查：
The backend lives in a **different place** on each version, and the self-check
accepts either:

| | Android 13 | Android 14+ |
|---|---|---|
| 后端 / backend | `keystore2` 内建 / built into `keystore2` | `rkpdapp` APK |
| 服务名 / service | `android.security.remoteprovisioning` | `/apex/com.android.rkpd*/priv-app/*` |
| 有没有独立 APK / standalone APK | **没有 / none** | 有 / yes |

Android 13 上 `keystore2` 自己向 servicemanager 注册
`android.security.remoteprovisioning`（AOSP
`system/security/keystore2/src/keystore2_main.rs:37` 的
`REMOTE_PROVISIONING_SERVICE_NAME`），RKP binder 就在这个服务里。
**不存在**包含 `IRemotelyProvisionedComponent` 的 APK，所以按 APK 查找在 A13 上
永远找不到。

On Android 13, `keystore2` registers `android.security.remoteprovisioning` with
the servicemanager itself (AOSP
`system/security/keystore2/src/keystore2_main.rs:37`,
`REMOTE_PROVISIONING_SERVICE_NAME`), and the RKP binder lives in that service.
**No APK** contains `IRemotelyProvisionedComponent`, so searching by APK never
succeeds on A13.

两版本的 HAL 层服务名是一样的
（`android.hardware.security.keymint.IRemotelyProvisionedComponent/default`），
所以签发流程本身不需要改动。

### 自检怎么判后端 / How the self-check decides

按顺序试，任一命中即通过 / tried in order, any hit passes:

1. `/apex/com.android.rkpd*/priv-app/*/*.apk`（A14+）
2. `android.security.remoteprovisioning` 服务（A13）
3. HAL 服务本身存在（`service check .../default`）

`service` 用绝对路径 `/system/bin/service` 调用，因为 `su -c` 的 `PATH`
通常很精简，直接写 `service` 可能找不到。
`service` is invoked by absolute path because a `su -c` shell usually has a
minimal `PATH`, where a bare `service` may not resolve.

自检失败时会打印**实际探测结果**，不是只说"没找到"：

```
—— 实际探测结果 / actual probe results ——
  A14+ APEX: 无 / none
  服务 / services: 无匹配 / no match
  HAL check: Service not found: android.hardware.security.keymint....
  keystore2: system 999 1 keystore2
```

A13 上 `keystore2` 只有在**成功连上 TEE HAL** 时才会注册
`android.security.remoteprovisioning`（AOSP `keystore2_main.rs:142` 是
`if let Ok(...)`，连不上就静默跳过，不报错）。所以这个服务缺失通常意味着
设备根本没有 `IRemotelyProvisionedComponent` HAL。

On A13, `keystore2` only registers `android.security.remoteprovisioning` when it
**successfully connects to the TEE HAL** (AOSP `keystore2_main.rs:142` is an
`if let Ok(...)`, so a failed connection is skipped silently). The missing
service therefore usually means the device has no
`IRemotelyProvisionedComponent` HAL at all.
The HAL-level service name is identical on both versions
(`android.hardware.security.keymint.IRemotelyProvisionedComponent/default`), so
the provisioning flow itself needs no change.

| | Android 12/13（HAL v1/v2） | Android 14+（HAL v3） |
|---|---|---|
| HAL 方法 / method | `generateCertificateRequest` | `generateCertificateRequestV2` |
| EEK | **必需 / required** | 不需要 / not needed |
| CSR 来源 / source | HAL 只给三件套，**客户端组装**<br>/ three pieces, **client-assembled** | HAL 直接返回完整 CSR<br>/ HAL returns the full CSR |
| CSR 结构 / structure | `[[DeviceInfo, {}], challenge, ProtectedData, MacedKeys]` | `AuthenticatedRequest<CsrPayload>` |
| 版本号字段 / version field | 无 / none | 有 `1` / present (`1`) |

### Android 13 的 CSR 组装 / CSR Assembly on Android 13

HAL 返回的是**分开的三部分** / The HAL returns **three separate pieces**:

```
mac           : HMAC-256(EK_mac, KeysToMacStructure)   32 字节 / bytes
deviceInfo    : VerifiedDeviceInfo（CBOR）
protectedData : COSE_Encrypt（加密的 BCC + 临时 MAC key）
                / encrypted BCC + ephemeral MAC key
```

服务器要的是组装好的 4 元组，对齐 AOSP
`system/security/provisioner/rkp_factory_extraction_tool.cpp:96-115`
的 `composeCertificateRequest()`：

The server expects an assembled 4-tuple, matching AOSP
`system/security/provisioner/rkp_factory_extraction_tool.cpp:96-115`
`composeCertificateRequest()`:

```
[
    [ VerifiedDeviceInfo, {} ],        <- 注意：已验证在前 / note: verified first
    challenge,
    ProtectedData,
    MacedKeys = [ bstr(""), {}, nil, mac ]   <- payload 是 nil / payload is nil
]
```

容易写错的三点 / Three easy-to-miss details:

1. `DeviceInfo` 数组是 `[已验证, {}]`，不是 `[空, 已验证]`
   / the pair is `[verified, {}]`, not `[{}, verified]`
2. `MacedKeys` 的 payload 是 **nil**，protected 是**空 bstr**
   / `MacedKeys` has a **nil** payload and an **empty bstr** protected header
3. 顶层**没有** version 字段（那是 v3 才有的）
   / there is **no** top-level version field (that is v3-only)

AOSP 的 `hwtrust` 就是用第 1 项的类型区分 v2/v3 / `hwtrust` distinguishes them
by the type of the first element:

```rust
match version_or_device_info {
    Value::Array(device_info) => Self::v2_from_cbor_values(...),
    Value::Integer(i)         => Self::v3_from_authenticated_request(...),
}
```

### 版本判断 / Version Detection

两道保险，防止厂商 HAL 行为不一致 /
Two safeguards, in case a vendor HAL reports an inconsistent version:

```java
boolean hasV2 = hasMethod(api, "generateCertificateRequestV2");
if (ver >= 3 && hasV2) {
    // Android 14+
} else {
    // Android 12/13
}
```

`generateCertificateRequestV2` 是 Android 14 才引入的，Android 13 上调用会
在 binder 层抛 TransactionError，进不到 HAL。

`generateCertificateRequestV2` was introduced in Android 14. Calling it on
Android 13 throws a TransactionError at the binder layer and never reaches the HAL.

---

## keybox.xml 的两个硬性要求 / Two Hard Requirements

这两条是踩过坑才确认的，缺一不可。
Both were confirmed the hard way; neither can be skipped.

### 1. 私钥必须是 SEC1 / The private key must be SEC1

```xml
<PrivateKey format="pem">
  -----BEGIN EC PRIVATE KEY-----   <!-- 必须是 SEC1 / must be SEC1 -->
```

TEESimulator 用 BoringSSL 的 `EC_KEY_parse_private_key` 解析私钥，
它只接受 **SEC1（RFC 5915）**。如果给 **PKCS#8**（`BEGIN PRIVATE KEY`），
会报 / TEESimulator parses the key with BoringSSL's
`EC_KEY_parse_private_key`, which accepts **SEC1 (RFC 5915)** only. Supplying
**PKCS#8** (`BEGIN PRIVATE KEY`) produces:

```
DECODE_ERROR, ec_asn1.cc:48
failing DeviceGenerateKey request with error
rc -30000
```

Key Attestation 显示 `KeyStoreException: -30000`
/ Key Attestation then shows `KeyStoreException: -30000`.

### 2. 证书顺序必须是叶子在前 / The chain must be leaf-first

```
[0] 叶子（持私钥）/ leaf (holds the private key)  <- 必须 / required
[1] Droid CA3
[2] Droid CA2
[3] Key Attestation CA1（自签名根 / self-signed root）
```

顺序反了会导致 `BAD_SIGNATURE` / `ERROR_CERTIFICATE_CHAIN_NOT_TRUSTED`。
Reversing the order causes `BAD_SIGNATURE` / `ERROR_CERTIFICATE_CHAIN_NOT_TRUSTED`.

工具链会用签发关系自动重排，不用手工调整。
The toolkit reorders automatically from the issuer relationship; no manual fixups.

---

## 产物位置 / Output Location

工具**只负责生成文件，不会自动部署**。

The tool **only produces the file and never deploys it automatically**.

### 自动归档 / Automatic archiving

每次运行结束（**成功和失败都算**），工作目录里的所有产物会被移进一个
带日期和批次的文件夹：

After every run — **successful or not** — all artifacts in the work directory
are moved into a dated batch folder:

```
~/rkp_out/                      <- 临时目录 / scratch space
├── 2026-11-21_0001/            <- 第 1 批 / batch 1
│   ├── keybox.xml
│   ├── certs.der
│   ├── key.der
│   ├── csr.bin
│   └── ...
├── 2026-11-21_0002/            <- 第 2 批 / batch 2
└── 2026-11-22_0001/            <- 第二天从 0001 重新开始 / next day restarts at 0001
```

跑完会打印归档位置：

```
==========================================
 归档 / archived
==========================================
 目录 / directory:
   /data/data/com.termux/files/home/rkp_out/2026-11-21_0001
 文件 / files: 8
 keybox.xml:
   /data/data/com.termux/files/home/rkp_out/2026-11-21_0001/keybox.xml
 大小 / size: 4358 bytes
 md5: 5314b2aa17a7d7409046933a03184205
==========================================
```

批次号规则 / batch numbering:

- 只扫描**当天**的目录，不会去读历史日期的文件夹
  / only **today's** directories are scanned; older days are never read
- 当天已有 `_0001`、`_0002`，下次就是 `_0003`
  / if `_0001` and `_0002` exist, the next one is `_0003`
- 换天后从 `_0001` 重新计数
  / a new day restarts at `_0001`

不想要归档就加 `--no-archive`，产物留在原目录：
To skip archiving, pass `--no-archive` and artifacts stay in place:

```bash
python3 rkp_toolkit.py all --no-archive
```

### 自行部署参考 / Optional deployment reference

如果你确实要用命令行部署到 TEESimulator：

```bash
su -c "cp ~/rkp_out/keybox.xml /data/adb/teesim/real_keybox.xml"
su -c "chmod 600 /data/adb/teesim/real_keybox.xml"
su -c "kill \$(pidof keystore2)"
```

keybox 在 keystore2 启动时加载，**必须重启 keystore2** 才生效。
The keybox is loaded when keystore2 starts, so **keystore2 must be restarted**.

JingMatrix 原版 TEESimulator 的配置在 `/data/adb/teesim/config.json`
/ The original JingMatrix TEESimulator reads `/data/adb/teesim/config.json`:

```jsonc
{
  "profiles": {
    "1": {
      "keybox": "real_keybox.xml",   // 相对 /data/adb/teesim / relative to it
      "mode": "generation",
      "apps": ["io.github.vvb2060.keyattestation"]
    }
  }
}
```

---

## 证书有效期 / Certificate Lifetime

RKP 叶证书有效期很短（实测约 12 天）。`keybox` 阶段会打印剩余天数，
到期后重跑 `sh issue.sh` 即可。

RKP leaf certificates have a short lifetime (about 12 days in practice). The
`keybox` stage prints the remaining days; re-run `sh issue.sh` after expiry.

---

## 排查 / Troubleshooting

看 TEESimulator 日志（注意标签大小写）/
Check the TEESimulator log (mind the tag's capitalisation):

```bash
su -c "logcat -d -t 300 | grep -a TEESimulator"
```

关键成功标志 / Success indicator:

```
generateKey: level=TEE, profile=1, EC/P_256 ...
```

失败会看到 / Failure looks like:

```
failing DeviceGenerateKey request with error Error { ... }
```

RKP 相关日志 / RKP-related logs:

```bash
su -c "logcat -d -t 500 | grep -a -E 'RkpBridge|IRemotelyProvisioned'"
```

如果 `issue.sh` 卡在 “B. 设备 TEE 生成 CSR”，工具会打印 bridge 的真实 stderr。
常见原因 / If `issue.sh` fails at "B. 设备 TEE 生成 CSR", the tool prints the
bridge's real stderr. Common causes:

| stderr | 原因 / cause | 解决 / fix |
|---|---|---|
| `need workdir` | bridge 与工具版本不匹配 / version mismatch | 用同一份 zip 里的两个文件 / use both files from the same zip |
| `RKP default service 不可用` | HAL 未注册 / HAL not registered | 确认设备支持 RKP |
| `既无 V2 也无可用 EEK` | 版本与曲线都不匹配 / version and curve mismatch | 见下方“已知限制” / see Known Limitations |
| 找不到包含 IRemotelyProvisionedComponent 的 APK | A13 无此 APK / none on A13 | 需 `service list` 里有 `android.security.remoteprovisioning` |
| `invoke failed: ...SecurityException` | SELinux 拦截 / SELinux denial | `setenforce 0` 后重试 |
| `PermissionError: ... key.der` | 私钥属 root / key owned by root | 见下方 / see below |

### 文件权限 / File ownership

`rkp_bridge.jar` 以 root 运行（`su -c`），它创建的 `key.der` 和 `out.bin`
默认属 `root:root`、权限 `0600`。Termux 用户读不了，打包会失败：

`rkp_bridge.jar` runs as root (`su -c`), so `key.der` and `out.bin` it creates
default to `root:root` with mode `0600`. The Termux user cannot read them and
packaging fails:

```
PermissionError: [Errno 13] Permission denied: '.../key.der'
```

现在由两方面共同解决 / addressed from both sides:

1. **bridge 主动交还**：调用时通过 `RKP_CALLER_UID` / `RKP_CALLER_GID`
   传入调用者 uid/gid，bridge 写完文件后 `chown` 回去。
   / the bridge chowns the files back, using the uid/gid passed via
   `RKP_CALLER_UID` / `RKP_CALLER_GID`.
2. **Python 兜底**：`_ensure_readable()` 在读取前检查，不可读时依次尝试
   `chown` → `chmod` → 复制副本。
   / `_ensure_readable()` checks before reading and falls back through
   `chown` -> `chmod` -> copy.

手动修复 / fix manually:

```bash
su -c "chown $(id -u):$(id -g) ~/rkp_out/key.der ~/rkp_out/out.bin"
```

---

## 版本号探测 / Version Probing

想知道设备走哪条路径 / To see which path your device takes:

```bash
python3 rkp_toolkit.py probe
```

它会打印 `versionNumber` 和 `supportedEekCurve`。
It prints `versionNumber` and `supportedEekCurve`.

---

## 许可证与来源 / License and Provenance

本项目采用 **MIT**（见 [LICENSE](LICENSE)）。

This project is under **MIT** (see [LICENSE](LICENSE)).

其中 `rkp_csr.py` 衍生自 AOSP 的 Apache-2.0 代码，相关归属声明在
[NOTICE](NOTICE)，Apache-2.0 全文在
[LICENSES/Apache-2.0.txt](LICENSES/Apache-2.0.txt)。

`rkp_csr.py` is derived from Apache-2.0 licensed AOSP code. The attribution is
in [NOTICE](NOTICE) and the full license text in
[LICENSES/Apache-2.0.txt](LICENSES/Apache-2.0.txt).

### 各文件来源 / Per-file provenance

| 文件 / File | 来源 / Origin | 许可 / License |
|---|---|---|
| `rkp_csr.py` | AOSP `rkp_factory_extraction_tool.cpp:96-115` 的 Python 翻译 / translation | Apache-2.0 |
| `rkp_toolkit.py` | 独立实现，协议参考 AOSP AIDL / independent, protocol per AOSP AIDL | MIT |
| `src/RkpBridge.java` | 独立实现 / independent | MIT |
| `src/RkpProbe.java` | 独立实现 / independent | MIT |
| `issue.sh` `setup.sh` `build.sh` | 独立实现 / independent | MIT |

### 参考但未复制代码 / Referenced but not copied

- [Duck-ToolBox](https://github.com/eltavine/Duck-ToolBox) `crates/duck-rkp`（MIT）
- Google RKP 服务端行为，黑盒观察 / backend behaviour, black-box observation

**未使用 OhMyKeymint 的代码。** OhMyKeymint 采用 GLA v4（仅限非商业，含
copyleft），与本项目的 MIT 不兼容。

**No OhMyKeymint code is used.** OhMyKeymint is under GLA v4 (non-commercial
only, with copyleft), which is incompatible with this project's MIT.

### 为什么不用 GLA v4 / Why not GLA v4

GLA v4 含 copyleft 且禁止商业使用。如果你的项目也要开源，建议采用 MIT
或 Apache-2.0 这类宽松许可。

GLA v4 carries copyleft and forbids commercial use. If you open-source your own
work, a permissive license such as MIT or Apache-2.0 is recommended.

### 没有伪造能力 / No Fabrication

本工具依赖设备自身的 KeyMint TEE 签发 CSR，不能凭空伪造，也不绕过 Google
服务器对设备真实性的校验。

This tool depends on the device's own KeyMint TEE to sign the CSR. It cannot
fabricate one, and it does not bypass Google's device authenticity checks.

---

## 重要声明 / Important Notice

### 烧录过 TEE 的设备很可能失败 / TEE reflashing often breaks RKP

**如果设备烧录过 TEE（或 vendor 分区被单独刷过、刷过跨版本固件、
使用过移植 ROM），RKP 签发很可能失败，且无法通过软件手段修复。**

**If the device's TEE has been reflashed — or the vendor partition was flashed
separately, cross-version firmware was installed, or a ported ROM is in use —
RKP provisioning will very likely fail, and no software workaround exists.**

原因 / Why:

RKP 的 CSR 必须在 TEE 内部生成，其中包含一条 **DICE 证书链**，用来证明设备
固件各启动阶段的完整性。vendor 或 TEE 被改动后，链对不上，厂商 HAL 就会
直接拒绝，返回类似 `Failure in CSR Generation (code 17)` 的私有错误码。

The RKP CSR must be produced inside the TEE and embeds a **DICE certificate
chain** attesting the integrity of each boot stage. After the vendor partition
or TEE is modified, the chain no longer matches and the vendor HAL refuses,
returning a private error such as `Failure in CSR Generation (code 17)`.

这类失败的特征 / Symptoms of this failure mode:

- `generateCertificateRequestV2 is unimplemented`（固件停留在旧版）
- 两种 EEK 曲线返回**完全相同的**厂商私有错误码
- 即使 `verifiedbootstate=green`、`flash.locked=1` 仍然失败
- `ro.build.version.release` 与 `ro.vendor.build.version.release` **不一致**

- `generateCertificateRequestV2 is unimplemented` (firmware stuck on an older HAL)
- both EEK curves return the **identical** vendor-private error code
- fails even with `verifiedbootstate=green` and `flash.locked=1`
- `ro.build.version.release` and `ro.vendor.build.version.release` **disagree**

**这不是本工具的缺陷。** 只要有一台未改动的设备能成功，就说明工具链本身是
正常的——问题在设备固件。

**This is not a defect in this tool.** If any unmodified device succeeds, the
toolchain itself is fine; the problem lies in the device firmware.

检查自己的设备 / Check your own device:

```bash
su -c "getprop ro.build.version.release"          # 系统 / system
su -c "getprop ro.vendor.build.version.release"   # 厂商 / vendor
su -c "getprop ro.boot.verifiedbootstate"
su -c "getprop ro.boot.flash.locked"
```

两行版本号不一致 → 大概率失败，别浪费时间。
If the two version lines disagree, it will most likely fail — don't waste time.

### 无 RKP HAL 的设备 / Devices without an RKP HAL

Android 13 及更早的机型可能完全没有 `IRemotelyProvisionedComponent` HAL
（Android 13 CDD 未强制要求）。自检会报 `找不到 RKP 后端`。

Devices on Android 13 and older may have no `IRemotelyProvisionedComponent` HAL
at all (the Android 13 CDD does not require one). The self-check reports
`找不到 RKP 后端 / no RKP backend found`.

### 能力边界 / What this tool does not do

- **不伪造证书**：CSR 由设备自己的 TEE 签名，工具只负责转发与打包。
  **Does not fabricate certificates**: the CSR is signed by the device's own
  TEE; the tool only forwards and packages.
- **不绕过校验**：Google 服务器对设备真实性的检查照常进行。
  **Does not bypass validation**: Google's server-side device checks still apply.
- **不能修复固件问题**：见上文。
  **Cannot fix firmware problems**: see above.

---

## 项目结构 / Repository Layout

```
.
├── issue.sh              # 入口：自检 + 签发 / entry point
├── setup.sh              # Termux 镜像测速与装依赖 / mirror + deps
├── rkp_toolkit.py        # 主逻辑 / main logic
├── rkp_csr.py            # Android 13 CSR 组装 / CSR assembly
├── build.sh              # 重新编译 jar / rebuild the jar
├── rkp_bridge.jar        # 调用 HAL 的桥 / HAL bridge
├── src/
│   ├── RkpBridge.java    # 反射调用 RKP HAL
│   └── RkpProbe.java     # HAL 探测 / HAL probe
├── docs/
│   └── android13-rkp-research.md
├── SECURITY.md
├── CONTRIBUTING.md
└── LICENSE
```

`rkp_bridge.jar` 一并提交，是为了让 Termux 用户零构建直接用。
源码在 `src/`，可用 `build.sh` 复现。

`rkp_bridge.jar` is committed so Termux users need no build step. The source
is in `src/` and `build.sh` reproduces it.

---

## 已知限制 / Known Limitations

- **厂商实现差异**：AOSP 的 Rust 参考实现中，v1/v2 的 `generate_cert_req()` 只是
  个 `Unimplemented` 占位，真实实现全部由 SoC 厂商自写。不同设备的实际行为
  可能有出入。
  / **Vendor variation**: in AOSP's Rust reference implementation, v1/v2's
  `generate_cert_req()` is only an `Unimplemented` placeholder; the real
  implementations are all written by SoC vendors, so behaviour can differ.
- **EEK 曲线**：优先采用设备上报的 `supportedEekCurve`。如果厂商上报有误，
  需要在 `rkp_toolkit.py` 中强制指定曲线。
  / **EEK curve**: the reported `supportedEekCurve` is preferred. If a vendor
  reports it incorrectly, force the curve in `rkp_toolkit.py`.
- **需要真实设备**：CSR 必须由设备 TEE 签发，不能在服务器上模拟。
  / **A real device is required**: the CSR must be signed by the device TEE and
  cannot be simulated on a server.
- **证书有效期短**：签出的证书通常只有 2 周左右，到期需重新签发。
  / **Short-lived certificates**: issued certificates last about two weeks and
  must be re-issued after that.
- **需要 root**：调用 RKP HAL 需要 root。
  / **Root required**: reaching the RKP HAL requires root.
- **硬编码的 MAC 密钥**：`MacedPublicKey` 用的是公开的测试密钥，这是能在
  不持有设备密钥的前提下构造 CSR 的前提。
  / **Hard-coded MAC key**: `MacedPublicKey` uses the public test MAC key, which
  is what allows building a CSR without holding the device key.
