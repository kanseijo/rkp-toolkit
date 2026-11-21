# 贡献指南 / Contributing

## 环境 / Environment

- Python 3.8+（Termux 上为 3.14）/ Python 3.8+ (3.14 on Termux)
- `cryptography` 库 / the `cryptography` library
- 重新编译 `rkp_bridge.jar` 需要 JDK 与 D8 / rebuilding `rkp_bridge.jar`
  needs a JDK and D8

## 代码结构 / Layout

| 路径 / Path | 作用 / Purpose |
|---|---|
| `issue.sh` | 入口脚本，串起自检与签发 / entry point |
| `setup.sh` | Termux 镜像测速与依赖安装 / mirror benchmark and deps |
| `rkp_toolkit.py` | 主逻辑：RKP 协议、打包、归档 / main logic |
| `rkp_csr.py` | Android 13 的 CSR 组装 / CSR assembly for A13 |
| `src/RkpBridge.java` | 反射调用 RKP HAL 的桥 / reflective HAL bridge |
| `src/RkpProbe.java` | HAL 探测工具 / HAL probe |
| `docs/` | 调研笔记 / research notes |

## 改代码的约定 / Conventions

**注释双语。** 项目里所有注释和用户可见的输出都是中文在前、英文在后，
请保持这个风格。

**Comments are bilingual.** Every comment and user-facing string is Chinese
first, English second. Please keep that style.

**不要静默失败。** 出错时必须把真实原因打出来，而不是只报一个笼统的
错误码。历史上最花时间的几次排查，都是因为真实异常被吞掉了。

**Never fail silently.** On error, surface the real cause instead of a generic
status code. The most time-consuming bug hunts in this project's history were
all caused by swallowed exceptions.

**尊重用户文件。** 任何会移动或改写用户数据的操作（归档、chown）都要有
兜底路径，并且不能破坏原文件。

**Respect user files.** Anything that moves or rewrites user data (archiving,
chown) needs a fallback path and must not destroy the original.

## 提交前 / Before submitting

```bash
# 语法 / syntax
for f in *.py; do python3 -c "import ast;ast.parse(open('$f').read())"; done
for f in *.sh; do sh -n "$f"; done

# 核心逻辑自测 / core logic self-test
python3 rkp_csr.py

# 确认没有私钥混进来 / make sure no private key slipped in
git diff --cached | grep -i "PRIVATE KEY"
```

第二条 `rkp_csr.py` 会跑 8 组断言，覆盖 CSR 结构、截断检测、CBOR 编解码。
必须通过。

The second command runs 8 assertion groups covering CSR structure, truncation
detection and CBOR encoding. It must pass.

## 重新编译 jar / Rebuilding the jar

```bash
sh build.sh <stubs.jar> [r8.jar]
```

需要 Android SDK 的 `android.jar`（或等价 stub）和 D8。改完 `src/*.java`
一定要重新编译并把新的 `rkp_bridge.jar` 一起提交，否则源码和二进制不一致。

You need the Android SDK `android.jar` (or an equivalent stub) and D8. After
editing `src/*.java`, rebuild and commit the new `rkp_bridge.jar`, otherwise
source and binary drift apart.

## 提交信息 / Commit messages

用中文或英文都可以，建议写清「为什么」而不只是「改了什么」。

Either language is fine. Explain **why**, not just what changed.
