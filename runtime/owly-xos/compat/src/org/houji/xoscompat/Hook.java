package org.houji.xoscompat;

import android.content.ComponentName;
import android.net.wifi.WifiManager;
import android.os.IBinder;
import android.util.Log;
import android.widget.TextView;
import de.robv.android.xposed.IXposedHookLoadPackage;
import de.robv.android.xposed.XC_MethodHook;
import de.robv.android.xposed.XposedBridge;
import de.robv.android.xposed.callbacks.XC_LoadPackage;
import java.lang.reflect.Method;
import java.util.List;

public final class Hook implements IXposedHookLoadPackage {
    private static final String TAG = "OwlyXosCompat";
    private static final String MISHARE_PKG = "com.miui.mishare.connectivity";
    private static final String RESOLVER_PKG = "com.android.intentresolver";

    private static boolean ownsRadio() {
        try {
            Object val = Class.forName("android.os.SystemProperties")
                    .getMethod("get", String.class)
                    .invoke(null, "sys.xos.exclusive");
            return "1".equals(val);
        } catch (Exception e) {
            return false;
        }
    }

    @Override
    public void handleLoadPackage(XC_LoadPackage.LoadPackageParam p) throws Throwable {
        if (RESOLVER_PKG.equals(p.packageName)) {
            hookIntentResolver(p.classLoader);
            return;
        }
        if (MISHARE_PKG.equals(p.packageName)) {
            hookMiShare(p.classLoader);
        }
    }

    private void hookIntentResolver(ClassLoader cl) throws Throwable {
        final Class<?> stub = Class.forName("com.android.intentresolver.ChooserActivityStubImpl", false, cl);
        final Class<?> target = Class.forName("com.android.intentresolver.chooser.TargetInfo", false, cl);
        XposedBridge.hookMethod(
                stub.getDeclaredMethod("checkStartGalleryActivity", int.class, String.class, List.class),
                new XC_MethodHook() {
                    @Override
                    protected void beforeHookedMethod(MethodHookParam param) throws Throwable {
                        if (!Integer.valueOf(0).equals(param.args[0]) || !MISHARE_PKG.equals(param.args[1])) {
                            return;
                        }
                        boolean fromGrid = false;
                        for (StackTraceElement frame : Thread.currentThread().getStackTrace()) {
                            if ("com.android.intentresolver.adapter.MiuiChooserGridAdapter".equals(frame.getClassName())
                                    && "startSelected".equals(frame.getMethodName())) {
                                fromGrid = true;
                                break;
                            }
                        }
                        if (!fromGrid) {
                            return;
                        }
                        Object selected = stub.getField("mTargetInfo").get(param.thisObject);
                        if (selected == null) {
                            return;
                        }
                        ComponentName component = (ComponentName) target
                                .getMethod("getResolvedComponentName")
                                .invoke(selected);
                        if (component != null
                                && MISHARE_PKG.equals(component.getPackageName())
                                && "com.xiaomi.sharop.AirDropShareTrampolineActivity".equals(component.getClassName())) {
                            param.args[0] = 2;
                            Log.i(TAG, "Gallery AirDrop target: dispatch via standard activity with URI grant preserved");
                        }
                    }
                });
        Log.i(TAG, "Loaded: scoped gallery AirDrop dispatch compatibility");
    }

    private void hookMiShare(ClassLoader cl) throws Throwable {
        XposedBridge.hookMethod(
                WifiManager.class.getDeclaredMethod("getWifiState"),
                new XC_MethodHook() {
                    @Override
                    protected void beforeHookedMethod(MethodHookParam param) {
                        if (!ownsRadio()) {
                            return;
                        }
                        for (StackTraceElement frame : Thread.currentThread().getStackTrace()) {
                            if ("com.xiaomi.sharop.awdl.AwdlManager".equals(frame.getClassName())
                                    && "a".equals(frame.getMethodName())) {
                                param.setResult(WifiManager.WIFI_STATE_ENABLED);
                                Log.i(TAG, "Suppress delayed bridge-owned Wi-Fi-off check");
                                return;
                            }
                        }
                    }
                });

        XposedBridge.hookMethod(
                Class.forName("com.miui.mishare.connectivity.c", false, cl).getDeclaredMethod("e"),
                new XC_MethodHook() {
                    @Override
                    protected void beforeHookedMethod(MethodHookParam param) {
                        try {
                            IBinder bridge = (IBinder) Class.forName("android.os.ServiceManager")
                                    .getMethod("checkService", String.class)
                                    .invoke(null, "com.domestic.xoscomm.IDomesticXOsComm/default");
                            if (bridge != null && bridge.pingBinder()) {
                                param.setResult(true);
                            }
                        } catch (Exception e) {
                            Log.w(TAG, "Bridge availability check failed", e);
                        }
                    }
                });

        String[][] targets = {
                {"com.xiaomi.sharop.awdl.AwdlManager", "a"},
                {"com.xiaomi.sharop.service.AirDropService", "v"},
        };
        for (String[] item : targets) {
            final Method method = Class.forName(item[0], false, cl).getDeclaredMethod(item[1]);
            XposedBridge.hookMethod(method, new XC_MethodHook() {
                @Override
                protected void beforeHookedMethod(MethodHookParam param) {
                    if (ownsRadio()) {
                        Log.i(TAG, "Ignore bridge-owned Wi-Fi transition: " + method.getDeclaringClass().getSimpleName());
                        param.setResult(null);
                    }
                }
            });
        }

        XposedBridge.hookMethod(
                TextView.class.getDeclaredMethod("setText", CharSequence.class, TextView.BufferType.class),
                new XC_MethodHook() {
                    @Override
                    protected void beforeHookedMethod(MethodHookParam param) {
                        if (param.args[0] != null) {
                            String s = param.args[0].toString();
                            if (s.contains("在等待接收或向Apple设备发送文件时") && s.contains("5 GHz")) {
                                param.args[0] = "owly-xos 高速模式在互传会话期间暂时独占无线电，退出会话后自动重连原 WLAN。";
                            }
                        }
                    }
                });
        Log.i(TAG, "Loaded: scoped MiShare 5.4.2 bridge-owned Wi-Fi transition compatibility");
    }
}
