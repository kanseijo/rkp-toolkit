# 安全说明 / Security Policy

## 你的 keybox 是敏感文件 / Your keybox is sensitive

`keybox.xml` 里包含一份 **EC 私钥**（`BEGIN EC PRIVATE KEY`）。它代表设备的
key attestation 身份：

A `keybox.xml` contains an **EC private key** (`BEGIN EC PRIVATE KEY`). It
represents a device's key-attestation identity:

- 拿到它的人可以伪造该设备的密钥认证 / whoever holds it can forge that
  device's key attestation
- 它通常还被用作 Play Integrity 判定依据 / it is commonly used as an input to
  Play Integrity verdicts
- 一旦公开，**无法撤销**，只能等证书过期 / once public it **cannot be
  revoked**; you can only wait for the certificate to expire

因此 / Therefore:

| 做 / Do | 不做 / Don't |
|---|---|
| 私密保存 / keep it private | 提交到仓库 / commit it |
| 单独传给自己信任的设备 / transfer privately | 发到群里、论坛、issue / post it publicly |
| 用完删除临时文件 / delete temp files | 上传到网盘公开链接 / upload to a public share |

本仓库的 `.gitignore` 已排除 `keybox.xml`、`real_keybox.xml`、`*.pem`、
`rkp_out/`、`rkp_work.zip` 等文件，但这只是**兜底**，不能代替你自己的检查。

The `.gitignore` here excludes `keybox.xml`, `real_keybox.xml`, `*.pem`,
`rkp_out/`, `rkp_work.zip` and similar. That is a **safety net**, not a
substitute for checking yourself.

提交前自查 / Check before you commit:

```bash
git status
git diff --cached | grep -i "PRIVATE KEY"    # 应当没有输出 / should print nothing
```

如果已经误提交 / If you already committed one:

1. 立刻作废该 keybox，重新签发 / revoke it by re-issuing a new keybox
2. 用 `git filter-repo` 清理历史 / purge history with `git filter-repo`
3. 强制推送并通知所有克隆者 / force-push and tell everyone who cloned

只删除文件、再提交一次**是不够的**，历史里仍然留着。
Deleting the file in a new commit is **not enough**; it stays in history.

## 报告问题 / Reporting a vulnerability

如果发现安全问题（例如工具会意外写出明文私钥），请不要开公开 issue，
通过仓库的 Security 页面私下报告，或直接联系维护者。

If you find a security issue (for example the tool writing a plaintext private
key where it should not), please do not open a public issue. Use the
repository's Security tab to report it privately, or contact the maintainers.

## 适用范围 / Scope

本工具只应在**你自己拥有或已获授权**的设备上使用。它通过设备本地的 RKP
HAL 向 Google 申请证书，不会绕过设备的安全机制。

This tool is meant for devices **you own or are authorized to test**. It asks
Google for certificates through the device's own local RKP HAL and does not
bypass any device security mechanism.
