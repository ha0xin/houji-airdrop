import android.os.Binder;
import android.os.IBinder;
import android.os.Parcel;
import android.os.RemoteException;
import java.io.BufferedReader;
import java.io.IOException;
import java.io.InputStreamReader;
import java.util.concurrent.CountDownLatch;
import java.util.concurrent.Executors;
import java.util.concurrent.ScheduledExecutorService;
import java.util.concurrent.ScheduledFuture;
import java.util.concurrent.TimeUnit;

/** Preserve the Xiaomi APK signature; adapt its AIDL lifecycle to the owly xos0 backend. */
public class XosBridge extends Binder {
    static final String DESCRIPTOR = "com.domestic.xoscomm.IDomesticXOsComm";
    static final String SERVICE = DESCRIPTOR + "/default";
    static final String DEFAULT_RADIO = "/data/local/tmp/xos/owly/radio.sh";
    static final int EFFECTIVE_CHANNEL = 149;

    final int allowedUid;
    final String radioScript;
    final Object lock = new Object();
    final ScheduledExecutorService cleanup = Executors.newSingleThreadScheduledExecutor();
    IBinder owner;
    IBinder.DeathRecipient death;
    ScheduledFuture<?> pending;
    int generation;
    boolean active;

    static final class Timeouts {
        static final ScheduledExecutorService POOL = Executors.newSingleThreadScheduledExecutor();
    }

    XosBridge(int uid, String radioScript) {
        this.allowedUid = uid;
        this.radioScript = radioScript;
        attachInterface(null, DESCRIPTOR);
    }

    static String resolveRadioScript() {
        String env = System.getenv("XOS_RADIO_SCRIPT");
        return (env != null && !env.trim().isEmpty()) ? env.trim() : DEFAULT_RADIO;
    }

    void checkCaller() {
        int uid = Binder.getCallingUid();
        if (uid != 0 && uid != allowedUid) {
            throw new SecurityException("XOS bridge only permits the installed MiShare UID");
        }
    }

    void radio(String action) throws Exception {
        System.out.println("RADIO_COMMAND=" + action);
        System.out.flush();
        Process p = new ProcessBuilder("/system/bin/sh", radioScript, action)
                .redirectErrorStream(true)
                .start();
        ScheduledFuture<?> timeout = Timeouts.POOL.schedule(p::destroyForcibly, 120, TimeUnit.SECONDS);
        try (BufferedReader in = new BufferedReader(new InputStreamReader(p.getInputStream()))) {
            String line;
            while ((line = in.readLine()) != null) {
                System.out.println(line);
                System.out.flush();
            }
        }
        int rc = p.waitFor();
        timeout.cancel(false);
        if (rc != 0) {
            throw new IOException("radio " + action + " failed: " + rc);
        }
    }

    void begin(IBinder token, int requested) throws Exception {
        if (token == null) {
            throw new IllegalArgumentException("client death token required");
        }
        synchronized (lock) {
            generation++;
            if (pending != null) {
                pending.cancel(false);
                pending = null;
            }
            if (owner != null && death != null) {
                owner.unlinkToDeath(death, 0);
            }
            owner = token;
            death = () -> release(token, "binder-death");
            token.linkToDeath(death, 0);
            try {
                radio("enter");
                active = true;
                System.out.println(
                        "START_OK requested_channel="
                                + requested
                                + " effective_channel="
                                + EFFECTIVE_CHANNEL
                                + " uid="
                                + allowedUid);
                System.out.flush();
            } catch (Exception e) {
                active = false;
                release(token, "start-failed");
                throw e;
            }
        }
    }

    void release(IBinder token, String reason) {
        synchronized (lock) {
            if (token != null && owner != token) {
                return;
            }
            final int version = ++generation;
            if (pending != null) {
                pending.cancel(false);
            }
            if (owner != null && death != null) {
                owner.unlinkToDeath(death, 0);
            }
            owner = null;
            death = null;
            pending = cleanup.schedule(
                    () -> {
                        synchronized (lock) {
                            if (version != generation) {
                                return;
                            }
                            try {
                                radio("restore");
                                active = false;
                                System.out.println("STOP_OK reason=" + reason);
                            } catch (Exception e) {
                                System.out.println("RESTORE_ERROR " + e);
                            } finally {
                                System.out.flush();
                            }
                        }
                    },
                    2,
                    TimeUnit.SECONDS);
        }
    }

    @Override
    protected boolean onTransact(int code, Parcel data, Parcel reply, int flags)
            throws RemoteException {
        if (code == INTERFACE_TRANSACTION) {
            reply.writeString(DESCRIPTOR);
            return true;
        }
        if (code >= 1 && code <= 16777215) {
            data.enforceInterface(DESCRIPTOR);
            checkCaller();
        }
        try {
            if (code == 1) {
                begin(data.readStrongBinder(), data.readInt());
                reply.writeNoException();
                return true;
            }
            if (code == 2) {
                release(null, "client-stop");
                reply.writeNoException();
                return true;
            }
            if (code == 3) {
                reply.writeNoException();
                reply.writeString(
                        "{\"bridge\":\"houji-owly-xos0\",\"active\":"
                                + active
                                + ",\"effectiveChannel\":"
                                + EFFECTIVE_CHANNEL
                                + "}");
                return true;
            }
        } catch (Exception e) {
            throw new IllegalStateException(e);
        }
        return super.onTransact(code, data, reply, flags);
    }

    public static void main(String[] args) throws Exception {
        String radioScript = resolveRadioScript();
        XosBridge bridge = new XosBridge(Integer.parseInt(args[0]), radioScript);
        Class.forName("android.os.ServiceManager")
                .getMethod("addService", String.class, IBinder.class)
                .invoke(null, SERVICE, bridge);
        System.out.println(
                "XOS_BRIDGE_READY allowed_uid="
                        + bridge.allowedUid
                        + " radio="
                        + bridge.radioScript);
        System.out.flush();
        new CountDownLatch(1).await();
    }
}
