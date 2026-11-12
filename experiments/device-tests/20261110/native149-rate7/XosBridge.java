import android.os.*;
import java.io.*;
import java.net.*;
import java.util.concurrent.*;

/** Preserve the Xiaomi APK signature; adapt its existing AIDL lifecycle externally. */
public class XosBridge extends Binder {
    static final String DESCRIPTOR="com.domestic.xoscomm.IDomesticXOsComm";
    static final String SERVICE=DESCRIPTOR+"/default";
    static final String RADIO="/data/local/tmp/houji-buildtest-20261110/radio-native149-rate7.sh";
    final int allowedUid;
    final Object lock=new Object();
    final ScheduledExecutorService cleanup=Executors.newSingleThreadScheduledExecutor();
    IBinder owner;IBinder.DeathRecipient death;ScheduledFuture<?> pending;
    int generation;boolean active;
    XosBridge(int uid){allowedUid=uid;attachInterface(null,DESCRIPTOR);}
    void checkCaller(){int uid=Binder.getCallingUid();if(uid!=0&&uid!=allowedUid)throw new SecurityException("XOS bridge only permits the installed MiShare UID");}
    void radio(String action)throws Exception {
        System.out.println("RADIO_COMMAND="+action);System.out.flush();
        java.lang.Process p=new ProcessBuilder("/system/bin/sh",RADIO,action).redirectErrorStream(true).start();
        ScheduledFuture<?> timeout=ExecutorsHolder.timeouts.schedule(()->p.destroyForcibly(),90,TimeUnit.SECONDS);
        try(BufferedReader in=new BufferedReader(new InputStreamReader(p.getInputStream()))){String s;while((s=in.readLine())!=null){System.out.println(s);System.out.flush();}}
        int rc=p.waitFor();timeout.cancel(false);if(rc!=0)throw new IOException("radio "+action+" failed: "+rc);
    }
    static class ExecutorsHolder {static final ScheduledExecutorService timeouts=Executors.newSingleThreadScheduledExecutor();}
    void begin(IBinder token,int requested)throws Exception {
        if(token==null)throw new IllegalArgumentException("client death token required");
        synchronized(lock){
            generation++;if(pending!=null){pending.cancel(false);pending=null;}
            if(owner!=null&&death!=null)owner.unlinkToDeath(death,0);
            owner=token;death=()->release(token,"binder-death");token.linkToDeath(death,0);
            try{
                radio("enter");active=true;
                System.out.println("START_OK requested_channel="+requested+" effective_channel=149 uid="+allowedUid);System.out.flush();
            }catch(Exception e){active=false;release(token,"start-failed");throw e;}
        }
    }
    void release(IBinder token,String reason){
        synchronized(lock){
            if(token!=null&&owner!=token)return;
            final int version=++generation;
            if(pending!=null)pending.cancel(false);
            if(owner!=null&&death!=null)owner.unlinkToDeath(death,0);owner=null;death=null;
            pending=cleanup.schedule(()->{
                synchronized(lock){if(version!=generation)return;
                    try{radio("restore");active=false;System.out.println("STOP_OK reason="+reason);}
                    catch(Exception e){System.out.println("RESTORE_ERROR "+e);}
                    finally{System.out.flush();}
                }
            },2,TimeUnit.SECONDS);
        }
    }
    @Override protected boolean onTransact(int code,Parcel data,Parcel reply,int flags)throws RemoteException {
        if(code==INTERFACE_TRANSACTION){reply.writeString(DESCRIPTOR);return true;}
        if(code>=1&&code<=16777215){data.enforceInterface(DESCRIPTOR);checkCaller();}
        try{
            if(code==1){begin(data.readStrongBinder(),data.readInt());reply.writeNoException();return true;}
            if(code==2){release(null,"client-stop");reply.writeNoException();return true;}
            if(code==3){reply.writeNoException();reply.writeString("{\"bridge\":\"houji-exclusive\",\"active\":"+active+",\"effectiveChannel\":149}");return true;}
        }catch(Exception e){throw new IllegalStateException(e);}
        return super.onTransact(code,data,reply,flags);
    }
    public static void main(String[] args)throws Exception {
        XosBridge bridge=new XosBridge(Integer.parseInt(args[0]));
        Class.forName("android.os.ServiceManager").getMethod("addService",String.class,IBinder.class).invoke(null,SERVICE,bridge);
        System.out.println("XOS_BRIDGE_READY allowed_uid="+bridge.allowedUid);System.out.flush();
        new CountDownLatch(1).await();
    }
}
