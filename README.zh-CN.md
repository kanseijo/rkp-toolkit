# RKP 签发工具链

**简体中文** · [English](README.md)

从设备申请 Google 远程密钥配置（RKP）证书，打包成 TEESimulator 可用的
`keybox.xml`。

---

## 目录

| 章节 | 说明 |
|---|---|
| [支持情况](#支持情况) | **先看这个**：哪些设备实测可用 |
| [前置要求](#前置要求) | Termux 版本、root、网络 |
| [快速开始](#快速开始) | 下载 Release 即可用 |
| [从源码构建](#从源码构建) | 改代码或自行审计 |
| [环境自动配置](#环境自动配置) | 换源测速与依赖安装 |
| [命令](#命令) | 各子命令的作用 |
| [自测](#自测) | 不连设备的本地验证 |
| [产物位置](#产物位置) | keybox.xml 落在哪 |
| [排查](#排查) | 常见错误对照表 |
| [重要声明](#重要声明) | **烧录过 TEE 很可能失败** |
| [已知限制](#已知限制) | 能力边界 |
| [许可证与来源](#许可证与来源) | MIT + AOSP 归属 |

技术细节：

- [流程](#流程) · [Android 版本差异](#android-版本差异) · [keybox.xml 的两个硬性要求](#keyboxxml-的两个硬性要求)
- [证书有效期](#证书有效期) · [版本号探测](#版本号探测) · [项目结构](#项目结构)

---

## 支持情况

按实测结果记录，不是承诺。

| 设备 | 系统 | 结果 |
|---|---|---|
| OnePlus Ace 6 / 一加 Ace 6 (PLQ110) | Android 16 | ✅ 成功 |
| ASUS ROG Phone 9 Pro / 玩家国度 9 Pro | Android 15 | ✅ 成功 |
| Redmi Note 11 / 红米 Note 11 (spes, 全球版) | Android 13 | ❌ 无 RKP HAL |
| OnePlus Ace 3 / 一加 Ace 3 (PJE110) | A15 系统 + A13 vendor | ❌ HAL 未实现 v3 方法 |

后两台失败**不是本工具的缺陷**：只要有一台未改动的设备成功，就说明工具链
本身正常。

---

## 前置要求

### Termux v0.118.3 或更高

**必须使用 v0.118.3 及以上版本。**

旧版本存在软件源签名与包管理问题，尤其是 Google Play 上那份停留在
**0.118.0** 的构建——它已被官方放弃，`setup.sh` 换源后会无法更新索引，
表现为 `索引更新失败 / index update failed`。

下载地址：

| 来源 | 链接 |
|---|---|
| **GitHub Releases**（推荐） | <https://github.com/termux/termux-app/releases/> |
| **F-Droid**（中文） | <https://f-droid.org/zh/packages/com.termux/> |
| **F-Droid**（English） | <https://f-droid.org/en/packages/com.termux/> |

> ⚠️ **不要从 Google Play 安装 Termux。**
> Play 版本停留在 0.118.0 且已停止维护，无法正常使用现代软件源。

确认版本：

```bash
echo $TERMUX_VERSION
# 期望：0.118.3 或更高
```

`issue.sh` 启动时会检查版本，低于 0.118.3 会给出提示。

### 其他要求

| 要求 | 说明 |
|---|---|
| Android 12 或更高 | 需要 RKP HAL |
| 已 root | 调用 RKP HAL 需要 root |
| 能访问 Google | `remoteprovisioning.googleapis.com` |

### 网络受限时

如果 `remoteprovisioning.googleapis.com` 连不上，可以让工具走反向代理：

```bash
./issue.sh --proxy micloud
# 或者
RKP_SERVER=https://deviceapi.micloud.xiaomi.net/proxyGoogle ./issue.sh
```

`micloud` 指向 `https://deviceapi.micloud.xiaomi.net/proxyGoogle`，这是小米设备
用来访问 `remoteprovisioning.googleapis.com` 的透明通道。实测
`fetchEekChain` 与 `signCertificates` 的返回与直连**完全一致**。

> ⚠️ 这是**非官方**通道：可能随时失效，且第三方能看到流量元数据
> （设备指纹、证书链）。私钥不会离开设备，所以无法仅凭流量重建 keybox。
> 能直连时请优先直连。

---

## 快速开始

在设备上操作（Termux + root）。

### 一行安装（推荐）

```bash
bash <(curl -L https://raw.githubusercontent.com/kanseijo/rkp-toolkit/main/install.sh)
```

国内网络访问 GitHub 慢时，用带加速的脚本：

```bash
bash <(curl -L https://gh-proxy.org/https://raw.githubusercontent.com/kanseijo/rkp-toolkit/main/install.sh)
```

安装脚本会先测速，太慢就自动切到 `gh-proxy.org`，下载后校验 SHA256，
解压时覆盖同名冲突文件。

可选环境变量：

| 变量 | 含义 |
|---|---|
| `RKP_DIR=/path` | 装到指定目录，默认 `$HOME` |
| `RKP_PROXY=1` | 强制走加速 |
| `RKP_PROXY=0` | 禁用加速 |
| `RKP_KEEP=1` | 保留下载的 zip |

跑完会打印一行启动命令，例如：

```bash
cd ~ && cd rkp-toolkit && sh issue.sh
```

### 手动下载

### 1. 下载

从 [Releases](https://github.com/kanseijo/rkp-toolkit/releases/latest) 下载
最新的 `rkp-toolkit.zip`，也可以在 Termux 里一条命令搞定：

```bash
pkg install -y unzip curl
curl -LO https://github.com/kanseijo/rkp-toolkit/releases/latest/download/rkp-toolkit.zip
```

> 这个链接始终指向最新版，文件名不变。

想固定版本：

```bash
curl -LO https://github.com/kanseijo/rkp-toolkit/releases/download/v1.0.0/rkp-toolkit.zip
```

### 2. 解压

```bash
unzip rkp-toolkit.zip
cd rkp-toolkit
```

### 校验（可选）

```bash
curl -LO https://github.com/kanseijo/rkp-toolkit/releases/latest/download/SHA256SUMS
sha256sum -c SHA256SUMS
```

### 3. 运行

```bash
./issue.sh
```

首次运行会自动检测依赖，缺失时调用 `setup.sh` 完成换源与安装。

### 4. 取产物

成功后产物在归档目录里：

```
~/rkp_out/2026-11-21_0001/keybox.xml
```

每次运行（成功或失败）都会归档，编号按当天递增。

> ⚠️ **`keybox.xml` 含设备私钥，切勿提交或公开分享。**
> 详见 [SECURITY.md](SECURITY.md)

### 从源码运行

需要 git 和 Java 构建环境时见 [从源码构建](#从源码构建)。

---

## 从源码构建

只有改代码或想自行审计二进制时才需要这一节。普通用户直接用 Release 里的包，
里面已含编译好的 `rkp_bridge.jar`。

### 克隆

```bash
git clone https://github.com/kanseijo/rkp-toolkit.git
cd rkp-toolkit
```

### Python 部分

无需构建，直接运行：

```bash
python3 rkp_csr.py          # 核心自测
./issue.sh
```

### Java 部分

`src/RkpBridge.java` 通过反射调用 RKP HAL，需要编译成 dex 再打包进 jar。

**需要：**

| 依赖 | 用途 |
|---|---|
| JDK 8+ | `javac` 编译 `.java` |
| `android.jar`（或等价 stub） | 编译期 Android API |
| D8（`r8.jar` 中的 `com.android.tools.r8.D8`） | class → dex |
| Python 3 | 把 `classes.dex` 写入 jar |

**构建：**

```bash
sh build.sh <android.jar> [r8.jar]
```

生成 `rkp_bridge.jar`（约 9.6 KB），覆盖仓库里的旧文件。

**获取依赖：**

- `android.jar`：Android SDK `platforms/android-35/android.jar`，或 AOSP 构建产物
- `r8.jar`：Android SDK `build-tools/*/lib/r8.jar`，或
  [r8 发布页](https://r8.googlesource.com/r8)

### 验证二进制与源码一致

```bash
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

---

## 环境自动配置

`setup.sh` 全自动完成：测速选源 → 写入 `sources.list` → 装依赖 → 自检。

通常不需要手动运行，`issue.sh` 发现缺东西时会自动调用。

### 测速原理

对每个候选镜像测量 `$base/termux-main/dists/stable/Release` 的往返时间，
选出最快的。延迟在设备侧测，不同网络结果不同。

### 写入的内容

```bash
# /data/data/com.termux/files/usr/etc/apt/sources.list
deb https://mirrors.bfsu.edu.cn/termux/apt/termux-main/ stable main
```

原配置备份到 `sources.list.rkp.bak`，可用 `--restore` 还原。

### 镜像失败会自动换

写完配置后会试跑 `pkg update`。如果失败，**自动换到下一个更快的镜像重试**，
直到某个成功。全部失败时：

- 依赖已齐全 → 恢复原配置，继续执行
- 依赖缺失 → 恢复原配置，退出并给出建议

备份只在当前配置**不是本工具生成**时才创建，避免把坏配置当成“原始配置”存起来。

### 全部镜像都失败时

`setup.sh` 会打印 apt 的**真实报错**（最后 12 行），并自动判断是不是本地问题：

| 识别到 | 含义 | 处理 |
|---|---|---|
| `lock` | 锁被占用 | 停止换源，提示 `--repair` |
| `dns` | 域名解析失败 | 提示检查网络 |
| `keyring` | GPG 签名失败 | 提示 `termux-change-repo` |
| `disk` | 空间不足 | 提示清理 |

本地问题换镜像也没用，所以会**立即停止**，不会白试 8 个源。

修复：

```bash
sh setup.sh --repair     # 清理 apt 锁和旧索引
```

### 用法

```bash
sh setup.sh              # 自动：测速并选最快
sh setup.sh --cn         # 只用国内源
sh setup.sh --global     # 只用国外源
sh setup.sh --mirror URL # 指定某个源
sh setup.sh --restore    # 恢复备份的配置
sh setup.sh --repair     # 清理 apt 锁和旧索引
sh setup.sh --dry-run    # 只测速不修改
```

### 为什么不用 pip 装 cryptography

在 Termux 上 `pip install cryptography` 会尝试从源码编译，无法链接 `libssl`。
用打包好的版本：

```bash
pkg install python-cryptography
```

`setup.sh` 已经这么做了。

---

## 依赖

| 包 | 用途 |
|---|---|
| `python` | 运行工具 |
| `python-cryptography` | 证书解析与打包 |
| `curl` | 访问 Google RKP 服务器 |

`rkp_bridge.jar` 已随包提供，设备上不需要额外安装 Java 运行时，用系统的
`app_process` 即可。

---

## 命令

```bash
python3 rkp_toolkit.py all      # 自检 + 签发 + 打包（默认）
python3 rkp_toolkit.py check    # 只做自检
python3 rkp_toolkit.py probe    # 打印 HAL 版本与支持曲线
python3 rkp_toolkit.py issue    # 取 EEK、生成 CSR、提交 Google
python3 rkp_toolkit.py keybox   # 用现有证书打包 keybox.xml
python3 rkp_toolkit.py verify   # 校验已有 keybox.xml
```

常用参数：

| 参数 | 含义 |
|---|---|
| `--workdir DIR` | 临时目录（默认 `rkp_out`） |
| `-o FILE` | 输出 keybox 路径 |
| `--no-archive` | 不归档，产物留在原目录 |

---

## 自测

`rkp_csr.py` 无需设备即可验证 Android 13 的 CSR 组装：

```bash
python3 rkp_csr.py
```

跑 8 组断言，覆盖字节布局、截断检测与 CBOR 编码，最后打印
`自测通过 / ALL TESTS PASSED`。

---

## 流程

```
A. fetchEekChain      POST /v1:fetchEekChain
                      → 拿到 challenge 和端到端加密公钥链
B. TEE 生成 CSR       HAL generateCertificateRequest(V2)(...)
                      → CSR 由设备 TEE 签名
C. signCertificates   POST /v1:signCertificates?challenge=...
                      → Google 返回证书链
D. 打包 keybox.xml    叶子在前、SEC1 私钥
```

端点是 gRPC 冒号语法：`/v1:fetchEekChain` 返回 200，而 `/v1/fetchEekChain`
返回 404。

---

## Android 版本差异

两条路径差异很大，工具会自动选择。

### RKP 后端从哪里来

两个版本的后端**位置不同**，自检会分别检查：

| | Android 13 | Android 14+ |
|---|---|---|
| 后端 | `keystore2` 内建 | `rkpdapp` APK |
| 服务名 | `android.security.remoteprovisioning` | `/apex/com.android.rkpd*/priv-app/*` |
| 独立 APK | **没有** | 有 |

Android 13 上 `keystore2` 自己向 servicemanager 注册
`android.security.remoteprovisioning`（AOSP
`system/security/keystore2/src/keystore2_main.rs:37` 的
`REMOTE_PROVISIONING_SERVICE_NAME`），RKP binder 就在这个服务里。
**不存在**包含 `IRemotelyProvisionedComponent` 的 APK，所以按 APK 查找在 A13 上
永远找不到。

两版本的 HAL 层服务名是一样的
（`android.hardware.security.keymint.IRemotelyProvisionedComponent/default`），
所以签发流程本身不需要改动。

### 自检怎么判后端

按顺序试，任一命中即通过：

1. `/apex/com.android.rkpd*/priv-app/*/*.apk`（A14+）
2. `android.security.remoteprovisioning` 服务（A13）
3. HAL 服务本身存在（`service check .../default`）

`service` 用绝对路径 `/system/bin/service` 调用，因为 `su -c` 的 `PATH`
通常很精简，直接写 `service` 可能找不到。同时也会检查 `vndservice`，
因为 vendor AIDL HAL 可能注册在 `/dev/vndbinder`。

自检失败时会打印**实际探测结果**，不是只说“没找到”：

```
—— 实际探测结果 ——
  A14+ APEX: 无
  服务: 无匹配
  HAL check: Service not found: android.hardware.security.keymint....
  keystore2: system 999 1 keystore2
```

A13 上 `keystore2` 只有在**成功连上 TEE HAL** 时才会注册
`android.security.remoteprovisioning`（AOSP `keystore2_main.rs:142` 是
`if let Ok(...)`，连不上就静默跳过，不报错）。所以这个服务缺失通常意味着
设备根本没有 `IRemotelyProvisionedComponent` HAL。

### HAL 版本与方法对应

| | Android 12/13（HAL v1/v2） | Android 14+（HAL v3） |
|---|---|---|
| HAL 方法 | `generateCertificateRequest` | `generateCertificateRequestV2` |
| EEK | **必需** | 不需要 |
| CSR 来源 | HAL 只给三件套，**客户端组装** | HAL 直接返回完整 CSR |
| CSR 结构 | `[[DeviceInfo, {}], challenge, ProtectedData, MacedKeys]` | `AuthenticatedRequest<CsrPayload>` |
| 版本号字段 | 无 | 有（`1`） |

工具**不完全相信版本号**：只要方法存在就先试
`generateCertificateRequestV2`，再退回 `generateCertificateRequest`，
并且两条 EEK 曲线都会试。

### Android 13 的 CSR 组装

HAL 返回的是**分开的三部分**：

```
mac           : HMAC-256(EK_mac, KeysToMacStructure)   32 字节
deviceInfo    : CBOR 编码的 VerifiedDeviceInfo
protectedData : BCC 的 COSE_Encrypt
```

`rkp_csr.py` 把它们组装成服务器要的 4 元素数组，对齐 AOSP
`system/security/provisioner/rkp_factory_extraction_tool.cpp:96-115`
的 `composeCertificateRequest()`：

```
[ [VerifiedDeviceInfo, {}], challenge, ProtectedData, MacedKeys ]
```

容易写错的几点：

- DeviceInfo 数组是 `[已验证, {}]`，**不是** `[{}, 已验证]`
- `MacedKeys` 的 payload 是 nil，protected 是空 bstr，tag 用 HAL 返回的 mac
- 顶层**没有** version 字段（那是 v3 才有）

AOSP 的 `.add(std::vector<uint8_t>(0))` 生成的是空 bstr（`0x40`），
**不是**空数组（`0x80`）。

### 版本号探测

```bash
python3 rkp_toolkit.py probe
```

它会打印 `versionNumber` 和 `supportedEekCurve`。

---

## keybox.xml 的两个硬性要求

这两条是踩过坑才确认的，缺一不可。

### 1. 私钥必须是 SEC1

```xml
<PrivateKey format="pem">
  -----BEGIN EC PRIVATE KEY-----   <!-- 必须是 SEC1 -->
```

TEESimulator 用 BoringSSL 的 `EC_KEY_parse_private_key` 解析私钥，
它只接受 **SEC1（RFC 5915）**。如果给 **PKCS#8**（`BEGIN PRIVATE KEY`），
会报：

```
DECODE_ERROR, ec_asn1.cc:48
failing DeviceGenerateKey request with error
```

App 侧显示 `-30000`。

### 2. 证书顺序必须是叶子在前

```xml
<CertificateChain>
  <NumberOfCertificates>4</NumberOfCertificates>
  <Certificate type="leaf">…</Certificate>   <!-- 叶子持私钥 -->
  <Certificate type="intermediate">…</Certificate>
  <Certificate type="intermediate">…</Certificate>
  <Certificate type="root">…</Certificate>   <!-- 根证书在最后 -->
</CertificateChain>
```

根证书放第一会得到 `BAD_SIGNATURE`。

---

## 产物位置

工具**只负责生成文件，不会自动部署**。

### 自动归档

每次运行结束（**成功和失败都算**），工作目录里的所有产物会被移进一个
带日期和批次的文件夹：

```
~/rkp_out/                      <- 临时目录
├── 2026-11-21_0001/            <- 第 1 批
│   ├── keybox.xml
│   ├── certs.der
│   ├── key.der
│   └── ...
├── 2026-11-21_0002/            <- 第 2 批
└── 2026-11-22_0001/            <- 第二天从 0001 重新开始
```

跑完会打印归档位置：

```
==========================================
 归档
==========================================
 目录：
   /data/data/com.termux/files/home/rkp_out/2026-11-21_0001
 文件：8
 keybox.xml:
   /data/data/com.termux/files/home/rkp_out/2026-11-21_0001/keybox.xml
 大小: 4358 bytes
 md5: 5314b2aa17a7d7409046933a03184205
==========================================
```

批次号规则：

- 只扫描**当天**的目录，不会去读历史日期的文件夹
- 当天已有 `_0001`、`_0002`，下次就是 `_0003`
- 换天后从 `_0001` 重新计数

不想要归档就加 `--no-archive`，产物留在原目录：

```bash
python3 rkp_toolkit.py all --no-archive
```

### 自行部署参考

如果你确实要用命令行部署到 TEESimulator：

```bash
cp ~/rkp_out/2026-11-21_0001/keybox.xml /data/adb/teesim/real_keybox.xml
chmod 600 /data/adb/teesim/real_keybox.xml
kill $(pidof keystore2)
```

keybox 在 keystore2 启动时加载，**必须重启 keystore2** 才生效。

---

## 证书有效期

签出的证书有效期很短，通常只有两周左右。打包时会打印叶子证书的有效期：

```
叶子有效期: 2026-11-19 06:03:06 -> 2026-12-02 13:54:37 UTC
该证书剩余 11 天有效期
```

到期前需要重新签发，没有续期机制。

---

## 排查

| 现象 | 原因 | 处理 |
|---|---|---|
| `找不到 RKP 后端` | 设备没有 RKP HAL（多见于 A13 及更早） | 该设备不支持 |
| `generateCertificateRequestV2 is unimplemented` | 固件停留在旧版 HAL | 见 [重要声明](#重要声明) |
| `Failure in CSR Generation (code 17)` | 厂商私有 TEE 错误 | 通常是刷过机的设备 |
| `PermissionError: ... key.der` | 私钥属 root | 见下 |
| `sh: 0: Can't open issue.sh` | 目录不对 | `ls ~/rkp-toolkit/issue.sh` |
| `索引更新失败` | 镜像不可达 | `sh setup.sh --global` 或 `--repair` |

### 文件权限

`rkp_bridge.jar` 以 root 运行（`su -c`），它创建的 `key.der` 和 `out.bin`
默认属 `root:root`、权限 `0600`。Termux 用户读不了，打包会失败：

```
PermissionError: [Errno 13] Permission denied: '.../key.der'
```

现在由两方面共同解决：

1. **bridge 主动交还**：调用时通过 `RKP_CALLER_UID` / `RKP_CALLER_GID`
   传入调用者 uid/gid，bridge 写完文件后 chown 回去。
2. **Python 兜底**：`_ensure_readable()` 在读取前检查，不可读时依次尝试
   `chown` → `chmod` → 复制副本。

手动修复：

```bash
su -c "chown $(id -u):$(id -g) ~/rkp_out/*/key.der ~/rkp_out/*/out.bin"
```

### 收集诊断信息

失败时工具会在批次目录里写出 `debug.txt`，包含 bridge 输出、Java 堆栈、
设备启动状态与固件版本。反馈问题时请附这个文件，不要截图。

---

## 重要声明

### 烧录过 TEE 的设备很可能失败

**如果设备烧录过 TEE（或 vendor 分区被单独刷过、刷过跨版本固件、
使用过移植 ROM），RKP 签发很可能失败，且无法通过软件手段修复。**

原因：RKP 的 CSR 必须在 TEE 内部生成，其中包含一条 **DICE 证书链**，用来
证明设备固件各启动阶段的完整性。vendor 或 TEE 被改动后，链对不上，厂商
HAL 就会直接拒绝，返回类似 `Failure in CSR Generation (code 17)` 的私有错误码。

这类失败的特征：

- `generateCertificateRequestV2 is unimplemented`（固件停留在旧版）
- 两种 EEK 曲线返回**完全相同的**厂商私有错误码
- 即使 `verifiedbootstate=green`、`flash.locked=1` 仍然失败
- `ro.build.version.release` 与 `ro.vendor.build.version.release` **不一致**

**这不是本工具的缺陷。** 只要有一台未改动的设备能成功，就说明工具链本身是
正常的——问题在设备固件。

检查自己的设备：

```bash
su -c "getprop ro.build.version.release"          # 系统
su -c "getprop ro.vendor.build.version.release"   # 厂商
su -c "getprop ro.boot.verifiedbootstate"
su -c "getprop ro.boot.flash.locked"
```

两行版本号不一致 → 大概率失败，别浪费时间。

### 无 RKP HAL 的设备

Android 13 及更早的机型可能完全没有 `IRemotelyProvisionedComponent` HAL
（Android 13 CDD 未强制要求）。自检会报 `找不到 RKP 后端`。

### 能力边界

- **不伪造证书**：CSR 由设备自己的 TEE 签名，工具只负责转发与打包。
- **不绕过校验**：Google 服务器对设备真实性的检查照常进行。
- **不能修复固件问题**：见上文。

---

## 项目结构

```
.
├── issue.sh              # 入口
├── setup.sh              # 换源测速与装依赖
├── rkp_toolkit.py        # 主逻辑
├── rkp_csr.py            # Android 13 CSR 组装
├── build.sh              # 重新编译 jar
├── rkp_bridge.jar        # 调用 HAL 的桥
├── src/
│   ├── RkpBridge.java    # 反射调用 RKP HAL
│   └── RkpProbe.java     # HAL 探测
├── docs/
│   └── android13-rkp-research.md
├── README.md             # English
├── README.zh-CN.md       # 简体中文（本文件）
├── SECURITY.md
├── CONTRIBUTING.md
└── LICENSE
```

`rkp_bridge.jar` 一并提交，是为了让 Termux 用户零构建直接用。源码在 `src/`，
可用 `build.sh` 复现。

---

## 已知限制

- **厂商实现差异**：AOSP 的 Rust 参考实现中，v1/v2 的 `generate_cert_req()`
  只是个 `Unimplemented` 占位，真实实现全部由 SoC 厂商自写。不同设备的实际
  行为可能有出入。
- **EEK 曲线**：优先采用设备上报的 `supportedEekCurve`，但两条曲线都会尝试。
  如果厂商上报有误，结果仍可能有差异。
- **需要真实设备**：CSR 必须由设备 TEE 签发，不能在服务器上模拟。
- **证书有效期短**：签出的证书通常只有 2 周左右，到期需重新签发。
- **需要 root**：调用 RKP HAL 需要 root。
- **硬编码的 MAC 密钥**：`MacedPublicKey` 用的是公开的测试密钥，这是能在
  不持有设备密钥的前提下构造 CSR 的前提。

---

## 许可证与来源

本项目采用 **MIT**（见 [LICENSE](LICENSE)）。

其中 `rkp_csr.py` 衍生自 AOSP 的 Apache-2.0 代码，相关归属声明在
[NOTICE](NOTICE)，Apache-2.0 全文在
[LICENSES/Apache-2.0.txt](LICENSES/Apache-2.0.txt)。

### 各文件来源

| 文件 | 来源 | 许可 |
|---|---|---|
| `rkp_csr.py` | AOSP `rkp_factory_extraction_tool.cpp:96-115` 的 Python 翻译 | Apache-2.0 |
| `rkp_toolkit.py` | 独立实现，协议参考 AOSP AIDL | MIT |
| `src/RkpBridge.java` | 独立实现 | MIT |
| `src/RkpProbe.java` | 独立实现 | MIT |
| `issue.sh` `setup.sh` `build.sh` | 独立实现 | MIT |

### 参考但未复制代码

- [Duck-ToolBox](https://github.com/eltavine/Duck-ToolBox) `crates/duck-rkp`（MIT）
- Google RKP 服务端行为，黑盒观察

**未使用 OhMyKeymint 的代码。** OhMyKeymint 采用 GLA v4（仅限非商业，含
copyleft），与本项目的 MIT 不兼容。

### 为什么不用 GLA v4

GLA v4 含 copyleft 且禁止商业使用。如果你的项目也要开源，建议采用 MIT
或 Apache-2.0 这类宽松许可。

### 没有伪造能力

本工具依赖设备自身的 KeyMint TEE 签发 CSR，不能凭空伪造，也不绕过 Google
服务器对设备真实性的校验。
