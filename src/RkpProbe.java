/*
 * RkpProbe —— 只读诊断：打印设备 RKP 服务的真实接口结构
 * 不生成密钥、不联外网、不写任何产物。
 */
package rkpb;

import java.lang.reflect.*;

public final class RkpProbe {
    private static final String PREFIX = "android.hardware.security.keymint.";
    private static final String SERVICE = PREFIX + "IRemotelyProvisionedComponent";

    public static void main(String[] args) {
        try {
            Class<?> ibinder = Class.forName("android.os.IBinder");
            Object svc = Class.forName("android.os.ServiceManager")
                    .getMethod("checkService", String.class)
                    .invoke(null, SERVICE + "/default");
            System.out.println("service = " + svc);
            if (svc == null) { System.out.println("NO_SERVICE"); return; }

            Class<?> api = Class.forName(SERVICE);
            System.out.println("api class = " + api.getName());
            Object proxy = Class.forName(SERVICE + "$Stub")
                    .getMethod("asInterface", ibinder).invoke(null, svc);
            System.out.println("proxy class = " + proxy.getClass().getName());

            System.out.println("\n--- IRemotelyProvisionedComponent 方法 ---");
            for (Method m : api.getMethods()) {
                System.out.println("  " + m.getReturnType().getSimpleName() + " " + m.getName()
                        + java.util.Arrays.toString(m.getParameterTypes()));
            }

            Object info = api.getMethod("getHardwareInfo").invoke(proxy);
            Class<?> ic = info.getClass();
            System.out.println("\n--- HardwareInfo 类 = " + ic.getName() + " ---");
            for (Field f : ic.getFields()) {
                Object v;
                try { v = f.get(info); } catch (Throwable t) { v = "<ERR " + t + ">"; }
                System.out.println("  field " + f.getType().getSimpleName() + " " + f.getName() + " = " + v);
            }
            System.out.println("\n--- HardwareInfo.toString() ---");
            System.out.println("  " + info);

            // 关键：supportedEekCurve 的真实类型与取值
            System.out.println("\n--- supportedEekCurve 深入 ---");
            try {
                Field ce = ic.getField("supportedEekCurve");
                Object v = ce.get(info);
                System.out.println("  type=" + ce.getType().getName() + " value=" + v);
                if (v instanceof Enum) {
                    System.out.println("  enum ordinal=" + ((Enum<?>) v).ordinal()
                            + " name=" + ((Enum<?>) v).name());
                }
            } catch (Throwable t) {
                System.out.println("  取不到 supportedEekCurve: " + t);
                for (Field f : ic.getDeclaredFields()) {
                    System.out.println("    declared: " + f.getType().getName() + " " + f.getName());
                }
            }
        } catch (Throwable t) {
            while (t instanceof InvocationTargetException && t.getCause() != null) t = t.getCause();
            System.out.println("PROBE_FAILED: " + t);
            t.printStackTrace(System.out);
        }
    }
}
