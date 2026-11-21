/*
 * RkpBridge —— 独立最小 RKP AIDL 桥接（无任何 Magisk/模块依赖）
 *              Standalone minimal RKP AIDL bridge (no Magisk/module dependency)
 *
 * 职责：在本机生成 P-256 密钥对 -> 让设备 TEE 为该公钥签 CSR -> 输出
 * 私钥始终留在本机（不经过网络、不写入 keybox）。
 * Role: generate a P-256 key pair locally -> have the device TEE sign a CSR for
 * that public key -> emit the result. The private key never leaves the device
 * (never sent over the network, never written into the keybox).
 *
 * 用法 (设备 root) / Usage (device root):
 *   app_process -Djava.class.path=/data/local/tmp/rkp.jar /system/bin rkpb.RkpBridge <workdir>
 *
 * workdir/in.bin  : [u32]challenge | [u32]eekP256 | [u32]eekX25519
 * workdir/out.bin : status | ...（见下 / see below）
 * workdir/key.der : PKCS#8 私钥 / private key（成功后写出，权限 / chmod 600）
 */
package rkpb;

import java.io.*;
import java.security.*;
import java.security.interfaces.ECPublicKey;
import java.security.spec.ECGenParameterSpec;
import java.lang.reflect.*;
import java.nio.file.attribute.PosixFilePermission;
import java.util.*;
import javax.crypto.Mac;
import javax.crypto.spec.SecretKeySpec;

public final class RkpBridge {
    private static final String PREFIX = "android.hardware.security.keymint.";
    private static final String SERVICE = PREFIX + "IRemotelyProvisionedComponent";
    private static final int LIMIT = 0xa000;
    private static final int ALG_ES256 = -7;

    public static void main(String[] args) throws Exception {
        // 兼容两种调用方式 / accept two calling conventions:
        //   1) <workdir>                 -> 读 workdir/in.bin，写 workdir/out.bin
        //   2) <in.bin> <out.bin>        -> 显式指定输入输出文件
        if (args.length != 1 && args.length != 2) {
            System.err.println("usage: rkpb.RkpBridge <workdir> | <in.bin> <out.bin>");
            System.exit(2);
        }
        File dir;
        File inFile;
        File outFile;
        if (args.length == 1) {
            dir = new File(args[0]);
            inFile = new File(dir, "in.bin");
            outFile = new File(dir, "out.bin");
        } else {
            inFile = new File(args[0]);
            outFile = new File(args[1]);
            dir = outFile.getAbsoluteFile().getParentFile();
        }
        byte[] reply;
        try { reply = run(inFile); }
        catch (Throwable t) {
            while (t instanceof InvocationTargetException && t.getCause() != null) t = t.getCause();
            String m = t.toString(); if (m.length() > 1000) m = m.substring(0, 1000);
            System.err.println("RkpBridge failed: " + m);
            // 打完整堆栈，方便定位是哪一层抛的
            // Print the full stack so we can see which layer threw.
            try { t.printStackTrace(System.err); } catch (Throwable ignored) {}
            reply = err(2, m);
        }
        try (FileOutputStream f = new FileOutputStream(outFile)) { f.write(reply); }
        handOver(outFile);
    }

    private static byte[] run(File inFile) throws Exception {
        byte[] challenge, eekP256, eekX25519;
        try (DataInputStream in = new DataInputStream(new FileInputStream(inFile))) {
            challenge = rd(in); eekP256 = rd(in); eekX25519 = rd(in);
            if (in.read() != -1) throw new IOException("trailing bytes in in.bin");
        }
        if (challenge.length == 0 || challenge.length > 64) throw new IOException("bad challenge");

        // 私钥写在输入文件所在目录 / write the private key next to the input file
        File keyDir = inFile.getAbsoluteFile().getParentFile();

        // 1) 本机生成 P-256 密钥对 / generate a P-256 key pair locally
        KeyPairGenerator kpg = KeyPairGenerator.getInstance("EC");
        kpg.initialize(new ECGenParameterSpec("secp256r1"), new SecureRandom());
        KeyPair kp = kpg.generateKeyPair();
        ECPublicKey ec = (ECPublicKey) kp.getPublic();
        byte[] x = fixed(ec.getW().getAffineX(), 32);
        byte[] y = fixed(ec.getW().getAffineY(), 32);
        writePrivate(new File(keyDir, "key.der"), kp.getPrivate().getEncoded());

        // 2) 构造 MacedPublicKey（与服务器端校验算法一致：PureSoft HMAC）
        //    Build MacedPublicKey matching the server's verification algorithm
        byte[] publicCoseKey = coseEc2Key(x, y);
        byte[] maced = macedPublicKey(publicCoseKey);

        // 3) 逐个 RKP 实例尝试 / try each RKP instance in turn
        //
        // 不同设备可能把实现放在 default 或 strongbox 上，所以两个都试。
        // A device may expose its implementation on either default or strongbox.
        Class<?> ibinder = Class.forName("android.os.IBinder");
        Class<?> api = Class.forName(SERVICE);
        Method checkSvc = Class.forName("android.os.ServiceManager")
                .getMethod("checkService", String.class);
        Method asInterface = Class.forName(SERVICE + "$Stub")
                .getMethod("asInterface", ibinder);

        Class<?> dc = Class.forName(PREFIX + "DeviceInfo");
        Class<?> pc = Class.forName(PREFIX + "ProtectedData");

        // MacedPublicKey[] keysToSign：所有尝试共用同一个待签公钥
        // MacedPublicKey[] keysToSign: shared by every attempt below.
        Class<?> mk = Class.forName(PREFIX + "MacedPublicKey");
        Object key = mk.getConstructor().newInstance();
        mk.getField("macedKey").set(key, maced);
        Object keys = Array.newInstance(mk, 1);
        Array.set(keys, 0, key);

        byte[] csr = null;
        String used = "";
        List<String> errors = new ArrayList<String>();

        String[] insts = {"default", "strongbox"};
        for (String inst : insts) {
            if (csr != null) break;

            Object svc;
            try { svc = checkSvc.invoke(null, SERVICE + "/" + inst); }
            catch (Throwable t) { errors.add(inst + ": 取服务失败 " + rootCause(t)); continue; }
            if (svc == null) { errors.add(inst + ": 该实例不存在 / no such instance"); continue; }

            Object proxy;
            try { proxy = asInterface.invoke(null, svc); }
            catch (Throwable t) { errors.add(inst + ": asInterface 失败 " + rootCause(t)); continue; }

            Object info;
            int ver;
            try {
                info = api.getMethod("getHardwareInfo").invoke(proxy);
                ver = readInt(info, "versionNumber");
            } catch (Throwable t) {
                errors.add(inst + ": getHardwareInfo 失败 " + rootCause(t));
                continue;
            }

            boolean hasV2 = hasMethod(api, "generateCertificateRequestV2");
            boolean hasV1 = hasMethod(api, "generateCertificateRequest");
            int reported = readInt(info, "supportedEekCurve");
            System.out.println("RkpBridge: [" + inst + "] version=" + ver
                    + " hasV2=" + hasV2 + " hasV1=" + hasV1
                    + " eekCurve=" + reported);

            // ---- V2：不需要 EEK / V2 needs no EEK ----
            if (hasV2) {
                try {
                    byte[] r = (byte[]) api.getMethod("generateCertificateRequestV2",
                            keys.getClass(), byte[].class)
                            .invoke(proxy, keys, challenge);
                    if (r != null && r.length > 0 && r.length <= LIMIT * 8) {
                        csr = r; used = "v2";
                        System.out.println("RkpBridge: [" + inst + "] V2 成功");
                        break;
                    }
                    errors.add(inst + " V2: 长度异常 " + (r == null ? "null" : r.length));
                } catch (Throwable t) {
                    errors.add(inst + " V2: " + rootCause(t));
                    System.out.println("RkpBridge: [" + inst + "] V2 失败: " + rootCause(t));
                }
            }

            // ---- V1：需要 EEK，两种曲线都试 / V1 needs an EEK; try both curves ----
            if (hasV1) {
                int[] curves = (reported == 1) ? new int[]{1, 2} : new int[]{2, 1};
                for (int curve : curves) {
                    byte[] eek = (curve == 1) ? eekP256 : eekX25519;
                    if (eek == null || eek.length == 0) {
                        errors.add(inst + " V1 curve=" + curve + ": 无 EEK");
                        continue;
                    }
                    try {
                        Object device = dc.getConstructor().newInstance();
                        Object prot = pc.getConstructor().newInstance();
                        System.out.println("RkpBridge: [" + inst + "] -> generateCertificateRequest"
                                + " (curve=" + curve + ", eek=" + eek.length + "B)");
                        byte[] mac = (byte[]) api.getMethod("generateCertificateRequest",
                                boolean.class, keys.getClass(), byte[].class, byte[].class, dc, pc)
                                .invoke(proxy, false, keys, eek, challenge, device, prot);

                        byte[] devInfo = (byte[]) dc.getField("deviceInfo").get(device);
                        byte[] protData = (byte[]) pc.getField("protectedData").get(prot);
                        if (mac == null || mac.length != 32)
                            throw new IOException("invalid keysToSign MAC");
                        if (devInfo == null || devInfo.length == 0 || devInfo.length > LIMIT)
                            throw new IOException("bad DeviceInfo");
                        if (protData == null || protData.length == 0 || protData.length > LIMIT)
                            throw new IOException("bad ProtectedData");

                        // [u32 macLen]mac [u32 devLen]dev [u32 protLen]prot
                        ByteArrayOutputStream vb = new ByteArrayOutputStream();
                        try (DataOutputStream o = new DataOutputStream(vb)) {
                            wr(o, mac); wr(o, devInfo); wr(o, protData);
                        }
                        csr = vb.toByteArray();
                        used = "v1";
                        System.out.println("RkpBridge: [" + inst + "] V1 curve=" + curve + " 成功");
                        break;
                    } catch (Throwable t) {
                        errors.add(inst + " V1 curve=" + curve + ": " + rootCause(t));
                        System.out.println("RkpBridge: [" + inst + "] V1 curve=" + curve
                                + " 失败: " + rootCause(t));
                    }
                }
            }
        }

        // ---- 诊断：testMode=true 是否可用 ----
        // Diagnostic only: does test mode work? Its certs cannot be used in
        // production, but it tells us whether the TEE can build a CSR at all.
        if (csr == null) {
            for (String inst : insts) {
                try {
                    Object svc = checkSvc.invoke(null, SERVICE + "/" + inst);
                    if (svc == null) continue;
                    Object proxy = asInterface.invoke(null, svc);
                    Object info = api.getMethod("getHardwareInfo").invoke(proxy);
                    int rep = readInt(info, "supportedEekCurve");
                    byte[] eek = (rep == 1) ? eekP256 : eekX25519;
                    if (eek == null || eek.length == 0) eek = eekP256;
                    if (eek == null || eek.length == 0) continue;

                    Object device = dc.getConstructor().newInstance();
                    Object prot = pc.getConstructor().newInstance();
                    api.getMethod("generateCertificateRequest",
                            boolean.class, keys.getClass(), byte[].class, byte[].class, dc, pc)
                            .invoke(proxy, true, keys, eek, challenge, device, prot);
                    errors.add(inst + " testMode=true: 成功（仅诊断 / diagnostic only）");
                    System.out.println("RkpBridge: [" + inst + "] testMode=true 成功");
                } catch (Throwable t) {
                    errors.add(inst + " testMode=true: " + rootCause(t));
                }
            }
        }

        if (csr == null) {
            StringBuilder sb = new StringBuilder("所有路径都失败 / every path failed");
            for (String e : errors) sb.append("\n  - ").append(e);
            throw new IOException(sb.toString());
        }

        if (csr == null || csr.length == 0 || csr.length > LIMIT * 8) {
            throw new IOException("TEE 返回的 CSR 长度异常: " + (csr == null ? "null" : csr.length));
        }

        ByteArrayOutputStream b = new ByteArrayOutputStream();
        try (DataOutputStream o = new DataOutputStream(b)) {
            o.writeInt(0);
            wr(o, used.getBytes("UTF-8"));
            wr(o, csr);
        }
        System.out.println("RkpBridge: TEE 已签出 CSR (method=" + used + ", " + csr.length + " bytes)");
        return b.toByteArray();
    }

    /** 判断接口上是否有某个方法（用于兼容 Android 13/14 的 HAL 差异）
     *  Check whether the interface declares a method (handles the Android 13/14 HAL split) */
    /** 取出异常根因的简短描述 / short description of the root cause */
    private static String rootCause(Throwable t) {
        while (t instanceof InvocationTargetException && t.getCause() != null) t = t.getCause();
        String m = String.valueOf(t);
        return m.length() > 300 ? m.substring(0, 300) : m;
    }

    private static boolean hasMethod(Class<?> api, String name) {
        for (Method m : api.getMethods()) {
            if (m.getName().equals(name)) return true;
        }
        return false;
    }

    /** 读取 RpcHardwareInfo 的 int 字段，失败返回 -1
     *  Read an int field of RpcHardwareInfo; returns -1 on failure */
    private static int readInt(Object info, String name) {
        try {
            Object v = info.getClass().getField(name).get(info);
            if (v instanceof Number) return ((Number) v).intValue();
            if (v instanceof Enum) return ((Enum<?>) v).ordinal();
            return -1;
        } catch (Throwable t) {
            return -1;
        }
    }

    private static byte[] fixed(java.math.BigInteger v, int n) {
        byte[] r = new byte[n];
        byte[] a = v.toByteArray();
        if (a.length > n) System.arraycopy(a, a.length - n, r, 0, n);
        else System.arraycopy(a, 0, r, n - a.length, a.length);
        return r;
    }

    private static byte[] coseEc2Key(byte[] x, byte[] y) throws IOException {
        ByteArrayOutputStream b = new ByteArrayOutputStream();
        try (DataOutputStream o = new DataOutputStream(b)) {
            o.writeByte(0xa5);
            o.write(intKey(1));  o.writeByte(0x02);
            o.write(intKey(3));  o.writeByte(0x26);
            o.write(intKey(-1)); o.writeByte(0x01);
            o.write(intKey(-2)); o.writeByte(0x58); o.writeByte(32); o.write(x);
            o.write(intKey(-3)); o.writeByte(0x58); o.writeByte(32); o.write(y);
        }
        return b.toByteArray();
    }

    /** CBOR 整数编码：正数 major 0，负数 major 1（值存 -1-n）
     *  CBOR integer encoding: major 0 for non-negative, major 1 otherwise (-1-n) */
    private static void writeInt(DataOutputStream o, long v) throws IOException {
        long major = (v >= 0) ? 0 : 1;
        long n = (v >= 0) ? v : (-1 - v);
        o.writeByte((int) ((major << 5) | (n < 24 ? n : 24)));
        if (n >= 24) {
            if (n < 0x100) o.writeByte((int) n);
            else { o.writeByte((int)(n >> 8)); o.writeByte((int)(n & 0xff)); }
        }
    }

    private static byte[] intKey(long v) throws IOException {
        ByteArrayOutputStream b = new ByteArrayOutputStream();
        try (DataOutputStream o = new DataOutputStream(b)) { writeInt(o, v); }
        return b.toByteArray();
    }

    /** MacedPublicKey = [protected, unprotected, payload, tag]
     *  COSE_Mac0 structure as defined by AOSP MacedPublicKey.cddl */
    private static byte[] macedPublicKey(byte[] publicCoseKey) throws Exception {
        // HKDF-SHA256 取 MAC key（与 Rust 端 ring::hkdf 结果一致）：
        // Derive the MAC key with HKDF-SHA256 (matches ring::hkdf on the Rust side):
        //   HKDF-Extract(salt=全零32字节, ikm=全零32字节) -> PRK
        //   HKDF-Expand(PRK, info="Key to MAC public keys", L=32) -> MAC key
        //
        // 注意 HKDF 的 salt 在 Extract 阶段作为 HMAC key；
        // 这里 salt 必须是 32 个 0x00，而不是空串。
        // Note that HKDF's salt is the HMAC key during Extract; here it must be
        // 32 zero bytes, not an empty string.
        Mac prkMac = Mac.getInstance("HmacSHA256");
        prkMac.init(new SecretKeySpec(new byte[32], "HmacSHA256"));
        byte[] prk = prkMac.doFinal(new byte[32]);

        Mac okmMac = Mac.getInstance("HmacSHA256");
        okmMac.init(new SecretKeySpec(prk, "HmacSHA256"));
        byte[] info = concat("Key to MAC public keys".getBytes("UTF-8"), new byte[]{1});
        byte[] macKey = Arrays.copyOf(okmMac.doFinal(info), 32);

        byte[] protectedHdr = new byte[]{(byte)0xa1, 0x01, 0x05};   // {1:5  = HS256}
        byte[] sigStruct = coseSigStructure(protectedHdr, publicCoseKey);

        Mac m = Mac.getInstance("HmacSHA256");
        m.init(new SecretKeySpec(macKey, "HmacSHA256"));
        byte[] tag = m.doFinal(sigStruct);

        ByteArrayOutputStream b = new ByteArrayOutputStream();
        try (DataOutputStream o = new DataOutputStream(b)) {
            o.writeByte(0x84);                                // array(4)
            o.writeByte(0x43); o.write(protectedHdr);
            o.writeByte(0xa0);                                // {}
            o.writeByte(0x58); o.writeByte(publicCoseKey.length); o.write(publicCoseKey);
            o.writeByte(0x58); o.writeByte(tag.length); o.write(tag);
        }
        return b.toByteArray();
    }

    /** ["MAC0", protected, "", payload]
     *  COSE_Mac0 sig_structure (RFC 9052 section 6.3) */
    private static byte[] coseSigStructure(byte[] prot, byte[] payload) throws IOException {
        ByteArrayOutputStream b = new ByteArrayOutputStream();
        try (DataOutputStream o = new DataOutputStream(b)) {
            o.writeByte(0x84);
            o.writeByte(0x64); o.write("MAC0".getBytes("UTF-8"));
            o.writeByte(0x43); o.write(prot);
            o.writeByte(0x40);
            o.writeByte(0x58); o.writeByte(payload.length); o.write(payload);
        }
        return b.toByteArray();
    }

    private static byte[] concat(byte[] a, byte[] b) {
        byte[] r = Arrays.copyOf(a, a.length + b.length);
        System.arraycopy(b, 0, r, a.length, b.length);
        return r;
    }

    private static void writePrivate(File f, byte[] d) throws IOException {
        try (FileOutputStream o = new FileOutputStream(f)) { o.write(d); }
        try {
            Set<PosixFilePermission> p = new HashSet<>();
            p.add(PosixFilePermission.OWNER_READ); p.add(PosixFilePermission.OWNER_WRITE);
            java.nio.file.Files.setPosixFilePermissions(f.toPath(), p);
        } catch (Exception ignored) { }
        handOver(f);
    }

    /**
     * 把文件所有者交还给调用者（Termux 用户）。
     * Hand file ownership back to the caller (the Termux user).
     *
     * 本程序以 root 运行（su -c），创建的文件属 root。如果不改回去，
     * 普通用户读不了 key.der，后续打包会 PermissionError。
     *
     * This program runs as root via `su -c`, so files it creates belong to root.
     * Without handing them back, the unprivileged user cannot read key.der and
     * packaging fails with PermissionError.
     *
     * 调用者的 uid/gid 由父进程通过环境变量传入（su 会把它们保留）。
     * The caller's uid/gid come from the environment, which su preserves.
     */
    private static void handOver(File f) {
        String u = System.getenv("RKP_CALLER_UID");
        String g = System.getenv("RKP_CALLER_GID");
        if (u == null || g == null) return;
        try {
            Class<?> os = Class.forName("android.system.Os");
            Object st = os.getMethod("stat", String.class).invoke(null, f.getAbsolutePath());
            // StructStat 字段是 public / StructStat fields are public
            Object cur = st.getClass().getField("st_uid").get(st);
            if (cur instanceof Number && ((Number) cur).intValue() == 0) {
                java.lang.reflect.Method chown = os.getMethod(
                        "chown", String.class, int.class, int.class);
                chown.invoke(null, f.getAbsolutePath(),
                        Integer.parseInt(u), Integer.parseInt(g));
            }
        } catch (Throwable ignored) { }
    }

    private static byte[] err(int st, String msg) {
        try {
            ByteArrayOutputStream b = new ByteArrayOutputStream();
            try (DataOutputStream o = new DataOutputStream(b)) {
                o.writeInt(st); o.writeInt(msg.getBytes("UTF-8").length);
                o.write(msg.getBytes("UTF-8"));
            }
            return b.toByteArray();
        } catch (IOException e) { return new byte[]{0,0,0,2}; }
    }

    private static byte[] rd(DataInputStream in) throws IOException {
        int n = in.readInt();
        if (n < 0 || n > LIMIT) throw new IOException("bad length " + n);
        byte[] b = new byte[n]; in.readFully(b); return b;
    }

    private static void wr(DataOutputStream o, byte[] b) throws IOException {
        if (b == null || b.length > LIMIT) throw new IOException("bad out length");
        o.writeInt(b.length); o.write(b);
    }
}
