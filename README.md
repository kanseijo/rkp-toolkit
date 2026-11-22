# RKP Provisioning Toolkit

[简体中文](README.zh-CN.md) · **English**

Requests Google Remote Key Provisioning (RKP) certificates from a device and
packages them into a `keybox.xml` usable by TEESimulator.

---

## Contents

| Section | What it covers |
|---|---|
| [Support Status](#support-status) | **start here**: tested devices |
| [Requirements](#requirements) | Termux version, root, network |
| [Quick Start](#quick-start) | just download a Release |
| [Build from Source](#build-from-source) | modify or audit |
| [Automatic Setup](#automatic-setup) | mirrors and dependencies |
| [Commands](#commands) | what each subcommand does |
| [Self-Test](#self-test) | offline verification |
| [Output Location](#output-location) | where the keybox lands |
| [Troubleshooting](#troubleshooting) | common errors |
| [Important Notice](#important-notice) | **TEE reflashing breaks RKP** |
| [Known Limitations](#known-limitations) | scope and limits |
| [License and Provenance](#license-and-provenance) | MIT + AOSP attribution |

Technical details:

- [Protocol Flow](#protocol-flow) · [Android Version Differences](#android-version-differences) · [Two Hard Requirements](#two-hard-requirements)
- [Certificate Lifetime](#certificate-lifetime) · [Version Probing](#version-probing) · [Repository Layout](#repository-layout)

---

## Support Status

Recorded from actual testing, not a promise.

| Device | OS | Result |
|---|---|---|
| OnePlus Ace 6 (PLQ110) | Android 16 | ✅ works |
| ASUS ROG Phone 9 Pro | Android 15 | ✅ works |
| Redmi Note 11 (spes, global) | Android 13 | ❌ no RKP HAL |
| OnePlus Ace 3 (PJE110) | A15 system + A13 vendor | ❌ HAL lacks v3 method |

Both failures are **not defects in this tool**: as long as one unmodified device
succeeds, the toolchain itself is fine.

---

## Requirements

### Termux v0.118.3 or newer

**Version v0.118.3 or newer is required.**

Older builds have repository-signature and package-management problems. This is
especially true of the **0.118.0** build on Google Play, which the project has
abandoned: after `setup.sh` switches mirrors, the index update fails with
`index update failed`.

Download:

| Source | Link |
|---|---|
| **GitHub Releases** (recommended) | <https://github.com/termux/termux-app/releases/> |
| **F-Droid** (Chinese) | <https://f-droid.org/zh/packages/com.termux/> |
| **F-Droid** (English) | <https://f-droid.org/en/packages/com.termux/> |

> ⚠️ **Do not install Termux from Google Play.**
> The Play build is stuck at 0.118.0 and unmaintained; it cannot use current
> repositories.

Check the version:

```bash
echo $TERMUX_VERSION
# expected: 0.118.3 or newer
```

`issue.sh` checks this on startup and warns when the version is below 0.118.3.

### Other requirements

| Requirement | Notes |
|---|---|
| Android 12 or newer | needs the RKP HAL |
| rooted device | root is required to reach the RKP HAL |
| Google reachable | `remoteprovisioning.googleapis.com` |

### Restricted networks

**By default the tool probes both routes first and uses the faster one.** The
probes run concurrently, so a blocked direct route does not stall you: it is
capped at 5 seconds, and the proxy is picked as soon as it answers. Nothing else
needs to be configured.

To choose explicitly:

```bash
./issue.sh --proxy auto        # 先测速再选（默认行为）/ probe first (default)
./issue.sh --proxy direct      # 只用直连 / direct only
./issue.sh --proxy micloud     # 强制走小米代理 / force the Xiaomi proxy
RKP_SERVER=https://deviceapi.micloud.xiaomi.net/proxyGoogle ./issue.sh
```

With `--proxy auto` the tool times a real `fetchEekChain` call against both
routes and uses whichever is faster:

```
[i] 网络探测 / network probe
[i]     直连 / direct        148 ms  699 bytes
[i]     代理 / micloud       279 ms  699 bytes
[OK] → 使用直连（更快）/ using direct (148 ms)
```

`micloud` maps to `https://deviceapi.micloud.xiaomi.net/proxyGoogle`, a
transparent route to `remoteprovisioning.googleapis.com` used by Xiaomi
devices. Both `fetchEekChain` and `signCertificates` were verified to return
byte-identical responses to a direct connection.

> ⚠️ It is an **unofficial** route: it may break without notice, and a third
> party can observe traffic metadata (device fingerprint, certificate chain).
> Your private key never leaves the device, so the keybox cannot be
> reconstructed from the traffic alone. Prefer a direct connection when
> available.

---

## Quick Start

Do this on the device (Termux + root).

### One-line install (recommended)

```bash
bash <(curl -L https://raw.githubusercontent.com/kanseijo/rkp-toolkit/main/install.sh)
```

If GitHub is slow in your network, use the proxied copy of the installer:

```bash
bash <(curl -L https://gh-proxy.org/https://raw.githubusercontent.com/kanseijo/rkp-toolkit/main/install.sh)
```

The installer measures GitHub throughput first, switches to `gh-proxy.org`
automatically when it is too slow, verifies the archive checksum, and unpacks
over any conflicting files.

Optional environment variables:

| Variable | Meaning |
|---|---|
| `RKP_DIR=/path` | install somewhere other than `$HOME` |
| `RKP_PROXY=1` | force the proxy |
| `RKP_PROXY=0` | never use the proxy |
| `RKP_KEEP=1` | keep the downloaded zip |

When it finishes it prints the single command to start, for example:

```bash
cd ~ && cd rkp-toolkit && sh issue.sh
```

### Manual download

### 1. Download

Download the latest `rkp-toolkit.zip` from
[Releases](https://github.com/kanseijo/rkp-toolkit/releases/latest), or use a
single command in Termux:

```bash
pkg install -y unzip curl
curl -LO https://github.com/kanseijo/rkp-toolkit/releases/latest/download/rkp-toolkit.zip
```

> This link always points at the newest release; the filename never changes.

To pin a specific version:

```bash
curl -LO https://github.com/kanseijo/rkp-toolkit/releases/download/v1.0.0/rkp-toolkit.zip
```

### 2. Unpack

```bash
unzip rkp-toolkit.zip
cd rkp-toolkit
```

### Verify (optional)

```bash
curl -LO https://github.com/kanseijo/rkp-toolkit/releases/latest/download/SHA256SUMS
sha256sum -c SHA256SUMS
```

### 3. Run

```bash
./issue.sh
```

On first run it checks dependencies and calls `setup.sh` to switch mirrors and
install anything missing.

### 4. Collect the output

The result lands in a dated batch directory:

```
~/rkp_out/2026-11-21_0001/keybox.xml
```

Every run — successful or not — is archived, numbered per day.

> ⚠️ **The generated `keybox.xml` holds a device private key. Never commit or
> share it.** See [SECURITY.md](SECURITY.md)

### Running from source

See [Build from Source](#build-from-source) if you want to work from the
repository or rebuild the jar.

---

## Build from Source

You only need this section to modify the code or audit the binary yourself.
Regular users should use the Release archive, which already contains a
prebuilt `rkp_bridge.jar`.

### Clone

```bash
git clone https://github.com/kanseijo/rkp-toolkit.git
cd rkp-toolkit
```

### The Python part

Nothing to build, run it directly:

```bash
python3 rkp_csr.py          # core self-test
./issue.sh
```

### The Java part

`src/RkpBridge.java` calls the RKP HAL reflectively and must be compiled to dex
and packed into a jar.

**Requires:**

| Dependency | Purpose |
|---|---|
| JDK 8+ | `javac` compiles the `.java` sources |
| `android.jar` (or an equivalent stub) | compile-time Android API |
| D8 (`com.android.tools.r8.D8` in `r8.jar`) | class → dex |
| Python 3 | packs `classes.dex` into the jar |

**Build:**

```bash
sh build.sh <android.jar> [r8.jar]
```

Produces `rkp_bridge.jar` (~9.6 KB), overwriting the committed copy.

**Where to get the dependencies:**

- `android.jar`: Android SDK `platforms/android-35/android.jar`, or an AOSP build
- `r8.jar`: Android SDK `build-tools/*/lib/r8.jar`, or the
  [r8 releases](https://r8.googlesource.com/r8)

### Verify the binary matches the source

```bash
sh build.sh <android.jar> r8.jar
python3 -c "
import zipfile
d = zipfile.ZipFile('rkp_bridge.jar').read('classes.dex')
for k in [b'generateCertificateRequestV2', b'RKP_CALLER_UID', b'strongbox']:
    print(k.decode(), 'OK' if k in d else 'MISSING')
"
```

> Built with the same JDK and D8 versions, `classes.dex` is **byte-for-byte
> reproducible** (verified). The jar's zip container adds timestamps that change
> the file hash, but the dex content is identical.

---

## Automatic Setup

`setup.sh` handles everything: benchmark mirrors → write `sources.list` →
install dependencies → self-check.

You rarely need to run it by hand; `issue.sh` calls it when something is
missing.

### How benchmarking works

It measures the round-trip time of `$base/termux-main/dists/stable/Release` for
every candidate mirror and picks the fastest. Latencies are measured from the
device, so results differ per network.

### What gets written

```bash
# /data/data/com.termux/files/usr/etc/apt/sources.list
deb https://mirrors.bfsu.edu.cn/termux/apt/termux-main/ stable main
```

A backup is made at `sources.list.rkp.bak`; `--restore` puts it back.

### Automatic mirror fallback

After writing the config it runs `pkg update`. If that fails it **moves on to
the next-fastest mirror and retries**, until one works. If every mirror fails:

- dependencies already present → restore the original config and continue
- dependencies missing → restore the original config, exit with suggestions

A backup is only taken when the current config was **not** generated by this
tool, so a broken config never becomes the "pristine" backup.

### When every mirror fails

`setup.sh` prints apt's **real error** (last 12 lines) and works out whether the
failure is local:

| Detected | Meaning | Action |
|---|---|---|
| `lock` | a lock is held | stop switching, suggest `--repair` |
| `dns` | DNS resolution failed | suggest checking the network |
| `keyring` | GPG signature failed | suggest `termux-change-repo` |
| `disk` | out of space | suggest cleaning up |

A local problem is not fixed by another mirror, so it **stops immediately**
instead of trying all 8.

Repair:

```bash
sh setup.sh --repair     # clear apt locks and stale indexes
```

### Usage

```bash
sh setup.sh              # auto: benchmark and pick the fastest
sh setup.sh --cn         # Chinese mirrors only
sh setup.sh --global     # global mirrors only
sh setup.sh --mirror URL # force one mirror
sh setup.sh --restore    # restore the saved config
sh setup.sh --repair     # clear apt locks and stale indexes
sh setup.sh --dry-run    # benchmark only, change nothing
```

### Why not install cryptography with pip

On Termux, `pip install cryptography` tries to build from source and fails to
link against `libssl`. Use the packaged build instead:

```bash
pkg install python-cryptography
```

`setup.sh` already does this.

---

## Dependencies

| Package | Purpose |
|---|---|
| `python` | runs the toolkit |
| `python-cryptography` | certificate parsing and packaging |
| `curl` | talks to the Google RKP server |

`rkp_bridge.jar` is bundled; no Java runtime install is needed on the device
beyond the system's `app_process`.

---

## Commands

```bash
python3 rkp_toolkit.py all      # self-check + issue + package (default)
python3 rkp_toolkit.py check    # self-check only
python3 rkp_toolkit.py probe    # print HAL version and supported curves
python3 rkp_toolkit.py issue    # fetch EEK, generate CSR, submit to Google
python3 rkp_toolkit.py keybox   # package keybox.xml from existing certs
python3 rkp_toolkit.py verify   # validate an existing keybox.xml
```

Common flags:

| Flag | Meaning |
|---|---|
| `--workdir DIR` | scratch directory (default `rkp_out`) |
| `-o FILE` | output keybox path |
| `--no-archive` | keep artifacts in place, skip archiving |

---

## Self-Test

`rkp_csr.py` verifies the Android 13 CSR assembly without touching a device:

```bash
python3 rkp_csr.py
```

It runs 8 assertion groups covering byte layout, truncation detection and CBOR
encoding, then prints `ALL TESTS PASSED`.

---

## Protocol Flow

```
A. fetchEekChain      POST /v1:fetchEekChain
                      → challenge + end-to-end encryption key chain
B. TEE generate CSR   HAL generateCertificateRequest(V2)(...)
                      → CSR signed by the device TEE
C. signCertificates   POST /v1:signCertificates?challenge=...
                      → Google returns the certificate chain
D. pack keybox.xml    leaf-first ordering, SEC1 private key
```

The endpoint uses gRPC colon syntax: `/v1:fetchEekChain` returns 200 while
`/v1/fetchEekChain` returns 404.

---

## Android Version Differences

The two paths differ substantially; the tool selects one automatically.

### Where the RKP backend comes from

The backend lives in a **different place** on each version, and the self-check
accepts either:

| | Android 13 | Android 14+ |
|---|---|---|
| Backend | built into `keystore2` | `rkpdapp` APK |
| Service | `android.security.remoteprovisioning` | `/apex/com.android.rkpd*/priv-app/*` |
| Standalone APK | **none** | yes |

On Android 13, `keystore2` registers `android.security.remoteprovisioning` with
the servicemanager itself (AOSP
`system/security/keystore2/src/keystore2_main.rs:37`,
`REMOTE_PROVISIONING_SERVICE_NAME`), and the RKP binder lives in that service.
**No APK** contains `IRemotelyProvisionedComponent`, so searching by APK never
succeeds on A13.

The HAL-level service name is identical on both versions
(`android.hardware.security.keymint.IRemotelyProvisionedComponent/default`), so
the provisioning flow itself needs no change.

### How the self-check decides

Tried in order, any hit passes:

1. `/apex/com.android.rkpd*/priv-app/*/*.apk` (A14+)
2. `android.security.remoteprovisioning` service (A13)
3. the HAL service itself (`service check .../default`)

`service` is invoked by absolute path because a `su -c` shell usually has a
minimal `PATH`, where a bare `service` may not resolve. `vndservice` is checked
too, since vendor AIDL HALs may register on `/dev/vndbinder`.

When the self-check fails it prints the **actual probe results**, not just
"not found":

```
actual probe results
  A14+ APEX: none
  services: no match
  HAL check: Service not found: android.hardware.security.keymint....
  keystore2: system 999 1 keystore2
```

On A13, `keystore2` only registers `android.security.remoteprovisioning` when it
**successfully connects to the TEE HAL** (AOSP `keystore2_main.rs:142` is an
`if let Ok(...)`, so a failed connection is skipped silently). The missing
service therefore usually means the device has no
`IRemotelyProvisionedComponent` HAL at all.

### HAL version and method mapping

| | Android 12/13 (HAL v1/v2) | Android 14+ (HAL v3) |
|---|---|---|
| HAL method | `generateCertificateRequest` | `generateCertificateRequestV2` |
| EEK | **required** | not needed |
| CSR source | HAL returns three pieces, **client-assembled** | HAL returns the full CSR |
| CSR structure | `[[DeviceInfo, {}], challenge, ProtectedData, MacedKeys]` | `AuthenticatedRequest<CsrPayload>` |
| Version field | none | present (`1`) |

The tool does not trust the version number alone: it tries
`generateCertificateRequestV2` first when the method exists, then falls back to
`generateCertificateRequest` with both EEK curves.

### CSR Assembly on Android 13

The HAL returns **three separate pieces**:

```
mac           : HMAC-256(EK_mac, KeysToMacStructure)   32 bytes
deviceInfo    : CBOR-encoded VerifiedDeviceInfo
protectedData : COSE_Encrypt of the BCC
```

`rkp_csr.py` assembles them into the 4-element array Google expects, mirroring
AOSP `system/security/provisioner/rkp_factory_extraction_tool.cpp:96-115`
`composeCertificateRequest()`:

```
[ [VerifiedDeviceInfo, {}], challenge, ProtectedData, MacedKeys ]
```

Key points that are easy to get wrong:

- The DeviceInfo pair is `[verified, {}]`, **not** `[{}, verified]`
- `MacedKeys` has a nil payload, an empty bstr protected header, and the MAC
  from the HAL as the tag
- There is **no** top-level version field; that exists only in v3

AOSP's `.add(std::vector<uint8_t>(0))` produces an empty bstr (`0x40`), **not**
an empty array (`0x80`).

### Version Probing

```bash
python3 rkp_toolkit.py probe
```

It prints `versionNumber` and `supportedEekCurve`.

---

## Two Hard Requirements

Both were confirmed the hard way; neither can be skipped.

### 1. The private key must be SEC1

```xml
<PrivateKey format="pem">
  -----BEGIN EC PRIVATE KEY-----   <!-- must be SEC1 -->
```

TEESimulator parses the key with BoringSSL's `EC_KEY_parse_private_key`, which
accepts **SEC1 (RFC 5915)** only. Supplying **PKCS#8** (`BEGIN PRIVATE KEY`)
produces:

```
DECODE_ERROR, ec_asn1.cc:48
failing DeviceGenerateKey request with error
```

and the app reports `-30000`.

### 2. The chain must be leaf-first

```xml
<CertificateChain>
  <NumberOfCertificates>4</NumberOfCertificates>
  <Certificate type="leaf">…</Certificate>   <!-- leaf holds the private key -->
  <Certificate type="intermediate">…</Certificate>
  <Certificate type="intermediate">…</Certificate>
  <Certificate type="root">…</Certificate>   <!-- root last -->
</CertificateChain>
```

Putting the root first yields `BAD_SIGNATURE`.

---

## Output Location

The tool **only produces the file and never deploys it automatically**.

### Automatic archiving

After every run — **successful or not** — all artifacts in the work directory
are moved into a dated batch folder:

```
~/rkp_out/                      <- scratch space
├── 2026-11-21_0001/            <- batch 1
│   ├── keybox.xml
│   ├── certs.der
│   ├── key.der
│   └── ...
├── 2026-11-21_0002/            <- batch 2
└── 2026-11-22_0001/            <- next day restarts at 0001
```

The archive location is printed when the run ends:

```
==========================================
 archived
==========================================
 directory:
   /data/data/com.termux/files/home/rkp_out/2026-11-21_0001
 files: 8
 keybox.xml:
   /data/data/com.termux/files/home/rkp_out/2026-11-21_0001/keybox.xml
 size: 4358 bytes
 md5: 5314b2aa17a7d7409046933a03184205
==========================================
```

Batch numbering:

- only **today's** directories are scanned; older days are never read
- if `_0001` and `_0002` exist, the next one is `_0003`
- a new day restarts at `_0001`

Pass `--no-archive` to keep artifacts in place:

```bash
python3 rkp_toolkit.py all --no-archive
```

### Optional deployment reference

If you do want to deploy to TEESimulator from the command line:

```bash
cp ~/rkp_out/2026-11-21_0001/keybox.xml /data/adb/teesim/real_keybox.xml
chmod 600 /data/adb/teesim/real_keybox.xml
kill $(pidof keystore2)
```

The keybox is loaded when keystore2 starts, so **keystore2 must be restarted**.

---

## Certificate Lifetime

Issued certificates are short-lived — typically about two weeks. The leaf
validity is printed when packaging:

```
leaf validity: 2026-11-19 06:03:06 -> 2026-12-02 13:54:37 UTC
11 days left on this certificate
```

Re-issue before it expires; there is no renewal mechanism.

---

## Troubleshooting

| Symptom | Cause | Fix |
|---|---|---|
| `no RKP backend found` | device has no RKP HAL (common on A13 and older) | device not supported |
| `generateCertificateRequestV2 is unimplemented` | firmware stuck on an older HAL | see [Important Notice](#important-notice) |
| `Failure in CSR Generation (code 17)` | vendor-private TEE error | usually a reflashed device |
| `PermissionError: ... key.der` | private key owned by root | see below |
| `sh: 0: Can't open issue.sh` | wrong directory | `ls ~/rkp-toolkit/issue.sh` |
| `index update failed` | mirror unreachable | `sh setup.sh --global` or `--repair` |

### File ownership

`rkp_bridge.jar` runs as root (`su -c`), so `key.der` and `out.bin` it creates
default to `root:root` with mode `0600`. The Termux user cannot read them and
packaging fails:

```
PermissionError: [Errno 13] Permission denied: '.../key.der'
```

Addressed from both sides:

1. **The bridge hands files back**: the caller's uid/gid are passed via
   `RKP_CALLER_UID` / `RKP_CALLER_GID` and the bridge chowns them after writing.
2. **Python fallback**: `_ensure_readable()` checks before reading and falls back
   through `chown` → `chmod` → copy.

Fix manually:

```bash
su -c "chown $(id -u):$(id -g) ~/rkp_out/*/key.der ~/rkp_out/*/out.bin"
```

### Collecting diagnostics

On failure the tool writes `debug.txt` in the batch directory, containing the
bridge stdout, the Java stack trace, device boot state and firmware versions.
Attach that file when reporting a problem instead of screenshots.

---

## Important Notice

### TEE reflashing often breaks RKP

**If the device's TEE has been reflashed — or the vendor partition was flashed
separately, cross-version firmware was installed, or a ported ROM is in use —
RKP provisioning will very likely fail, and no software workaround exists.**

Why: the RKP CSR must be produced inside the TEE and embeds a **DICE
certificate chain** attesting the integrity of each boot stage. After the vendor
partition or TEE is modified, the chain no longer matches and the vendor HAL
refuses, returning a private error such as
`Failure in CSR Generation (code 17)`.

Symptoms of this failure mode:

- `generateCertificateRequestV2 is unimplemented` (firmware stuck on an older HAL)
- both EEK curves return the **identical** vendor-private error code
- fails even with `verifiedbootstate=green` and `flash.locked=1`
- `ro.build.version.release` and `ro.vendor.build.version.release` **disagree**

**This is not a defect in this tool.** If any unmodified device succeeds, the
toolchain itself is fine; the problem lies in the device firmware.

Check your own device:

```bash
su -c "getprop ro.build.version.release"          # system
su -c "getprop ro.vendor.build.version.release"   # vendor
su -c "getprop ro.boot.verifiedbootstate"
su -c "getprop ro.boot.flash.locked"
```

If the two version lines disagree, it will most likely fail — don't waste time.

### Devices without an RKP HAL

Devices on Android 13 and older may have no
`IRemotelyProvisionedComponent` HAL at all (the Android 13 CDD does not require
one). The self-check reports `no RKP backend found`.

### What this tool does not do

- **Does not fabricate certificates**: the CSR is signed by the device's own
  TEE; the tool only forwards and packages.
- **Does not bypass validation**: Google's server-side device checks still
  apply.
- **Cannot fix firmware problems**: see above.

---

## Repository Layout

```
.
├── issue.sh              # entry point
├── setup.sh              # mirror benchmark + deps
├── rkp_toolkit.py        # main logic
├── rkp_csr.py            # Android 13 CSR assembly
├── build.sh              # rebuild the jar
├── rkp_bridge.jar        # HAL bridge
├── src/
│   ├── RkpBridge.java    # reflective RKP HAL calls
│   └── RkpProbe.java     # HAL probe
├── docs/
│   └── android13-rkp-research.md
├── README.md             # English (this file)
├── README.zh-CN.md       # 简体中文
├── SECURITY.md
├── CONTRIBUTING.md
└── LICENSE
```

`rkp_bridge.jar` is committed so Termux users need no build step. The source is
in `src/` and `build.sh` reproduces it.

---

## Known Limitations

- **Vendor variation**: in AOSP's Rust reference implementation, v1/v2's
  `generate_cert_req()` is only an `Unimplemented` placeholder; the real
  implementations are all written by SoC vendors, so behaviour can differ.
- **EEK curve**: the reported `supportedEekCurve` is preferred, but both curves
  are tried. If a vendor reports it incorrectly, results may still vary.
- **A real device is required**: the CSR must be signed by the device TEE and
  cannot be simulated on a server.
- **Short-lived certificates**: issued certificates last about two weeks and
  must be re-issued after that.
- **Root required**: reaching the RKP HAL requires root.
- **Hard-coded MAC key**: `MacedPublicKey` uses the public test MAC key, which
  is what allows building a CSR without holding the device key.

---

## License and Provenance

This project is under **MIT** (see [LICENSE](LICENSE)).

`rkp_csr.py` is derived from Apache-2.0 licensed AOSP code. The attribution is
in [NOTICE](NOTICE) and the full license text in
[LICENSES/Apache-2.0.txt](LICENSES/Apache-2.0.txt).

### Per-file provenance

| File | Origin | License |
|---|---|---|
| `rkp_csr.py` | Python translation of AOSP `rkp_factory_extraction_tool.cpp:96-115` | Apache-2.0 |
| `rkp_toolkit.py` | independent, protocol per AOSP AIDL | MIT |
| `src/RkpBridge.java` | independent | MIT |
| `src/RkpProbe.java` | independent | MIT |
| `issue.sh` `setup.sh` `build.sh` | independent | MIT |

### Referenced but not copied

- [Duck-ToolBox](https://github.com/eltavine/Duck-ToolBox) `crates/duck-rkp` (MIT)
- Google RKP backend behaviour, black-box observation

**No OhMyKeymint code is used.** OhMyKeymint is under GLA v4 (non-commercial
only, with copyleft), which is incompatible with this project's MIT.

### Why not GLA v4

GLA v4 carries copyleft and forbids commercial use. If you open-source your own
work, a permissive license such as MIT or Apache-2.0 is recommended.

### No Fabrication

This tool depends on the device's own KeyMint TEE to sign the CSR. It cannot
fabricate one, and it does not bypass Google's device authenticity checks.
