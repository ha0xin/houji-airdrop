package org.houji.owly;

import android.app.*;
import android.os.*;
import android.content.*;
import android.net.Uri;
import android.database.Cursor;
import android.provider.OpenableColumns;
import android.view.View;
import android.view.WindowManager;
import android.widget.*;
import org.json.*;
import java.io.*;
import java.nio.file.*;
import java.util.*;
import java.util.concurrent.*;

public class MainActivity extends Activity {
    static final String CONTROL="/data/local/tmp/owly-app/control.sh";
    final ExecutorService worker=Executors.newSingleThreadExecutor();
    final Handler handler=new Handler(Looper.getMainLooper());
    final ArrayList<String> macs=new ArrayList<>();
    TextView stateLabel, transferLabel, detail, fileLabel;
    Spinner devices;
    Button scan, connect, stop, send;
    Switch coexistSwitch;
    volatile boolean working=false;
    boolean sending=false, closed=false;
    String peersKey="", selectedPath=null, currentSession="";
    long prompted=-1;
    AlertDialog prompt;
    File exportFile;

    File file(String path){ return new File(getFilesDir(),path); }
    void message(String text){runOnUiThread(()->detail.setText(text));}
    void controls(boolean busy){working=busy;scan.setEnabled(!busy);connect.setEnabled(!busy);stop.setEnabled(!busy);send.setEnabled(!busy&&!sending);if(coexistSwitch!=null)coexistSwitch.setEnabled(!busy);}
    boolean isCoexistAuto(){try{File f=file("coexist.mode");if(!f.exists())return true;return !"off".equals(new String(Files.readAllBytes(f.toPath()),"UTF-8").trim());}catch(Exception e){return true;}}
    void saveCoexistMode(boolean auto){try{Files.write(file("coexist.mode").toPath(),(auto?"auto\n":"off\n").getBytes("UTF-8"));}catch(Exception e){message("保存共存设置失败："+e.getMessage());}}
    Button button(LinearLayout box,String label,Runnable action){Button b=new Button(this);b.setText(label);box.addView(b);b.setOnClickListener(v->action.run());return b;}
    TextView label(LinearLayout box,String text,int size){TextView t=new TextView(this);t.setText(text);t.setTextSize(size);t.setPadding(0,12,0,12);box.addView(t);return t;}
    String root(String action)throws Exception {
        ProcessBuilder builder=new ProcessBuilder("su","-c","sh "+CONTROL+" "+action).redirectErrorStream(true);
        java.lang.Process process=builder.start();
        StringBuilder text=new StringBuilder();
        try(BufferedReader reader=new BufferedReader(new InputStreamReader(process.getInputStream()))){String s;while((s=reader.readLine())!=null){if(text.length()<24000)text.append(s).append('\n');}}
        int code=process.waitFor();
        if(code!=0)throw new IOException("操作失败（"+code+"）\n"+text);
        return text.toString();
    }
    void radio(String action,String title){
        controls(true);stateLabel.setText(title);detail.setText("正在切换无线链路，可能需要约 20–40 秒…");
        worker.execute(()->{try{
            String result=root(action);
            runOnUiThread(()->{controls(false);message(action.equals("stop")?"已停止，普通 Wi-Fi 已恢复":result);
                if(action.equals("stop")){stateLabel.setText("已停止");transferLabel.setText("");transferLabel.setVisibility(View.GONE);file("status.json").delete();}
            });
        }catch(Exception e){runOnUiThread(()->{controls(false);stateLabel.setText("操作未完成");detail.setText(e.getMessage()+"\n如提示拒绝 root，请在 KernelSU 授权 Owly AirDrop。");});}});
    }
    void command(JSONObject value)throws Exception {
        if(!value.has("session"))value.put("session",currentSession);
        String name=UUID.randomUUID().toString();
        File temp=file("commands/"+name+".tmp"), ready=file("commands/"+name+".json");
        try(FileOutputStream out=new FileOutputStream(temp)){out.write(value.toString().getBytes("UTF-8"));out.getFD().sync();}
        Files.move(temp.toPath(),ready.toPath(),StandardCopyOption.ATOMIC_MOVE);
    }
    void answer(long id,boolean accept,String session){try{command(new JSONObject().put("op",accept?"accept":"decline").put("id",id).put("session",session));}catch(Exception e){message(e.toString());}}
    String formatSize(long bytes){
        if(bytes>=1_000_000L)return String.format(Locale.ROOT,"%.2f MB",bytes/1_000_000.0);
        if(bytes>=1_000L)return String.format(Locale.ROOT,"%.1f KB",bytes/1_000.0);
        return bytes+" B";
    }
    String formatDuration(double secs){
        if(secs>=60.0){long mins=(long)(secs/60.0);double rem=secs-(mins*60.0);return String.format(Locale.ROOT,"%d 分 %.1f 秒（%.2f s）",mins,rem,secs);}
        return String.format(Locale.ROOT,"%.2f 秒",secs);
    }
    String formatSpeed(double mbps){
        if(mbps>=0.1)return String.format(Locale.ROOT,"%.2f MB/s",mbps);
        if(mbps>0.0)return String.format(Locale.ROOT,"%.1f KB/s（%.3f MB/s）",mbps*1000.0,mbps);
        return "计算中…";
    }
    String renderTransfer(JSONObject tr){
        if(tr==null)return null;
        boolean active=tr.optBoolean("active",false);
        boolean hasOk=!tr.isNull("ok")&&tr.optBoolean("ok",false);
        String dir="send".equals(tr.optString("direction"))?"发送":"接收";
        long bytes=tr.optLong("bytes",0L);
        long total=tr.optLong("total",0L);
        double secs=tr.has("duration_secs")?tr.optDouble("duration_secs",0.0):tr.optLong("elapsed_ms",0L)/1000.0;
        double mbps=tr.optDouble("speed_mbps",0.0);
        if(active){
            if(total>0){
                int pct=(int)Math.min(100L,(bytes*100L)/total);
                return String.format(Locale.ROOT,"实时%s：%s / %s（%d%%）\n当前平均速度：%s · 已用时间：%s",
                    dir,formatSize(bytes),formatSize(total),pct,formatSpeed(mbps),formatDuration(secs));
            }
            return String.format(Locale.ROOT,"实时%s：%s\n当前平均速度：%s · 已用时间：%s",
                dir,formatSize(bytes),formatSpeed(mbps),formatDuration(secs));
        }
        if(hasOk){
            int files=tr.optInt("files",1);
            return String.format(Locale.ROOT,"最近一次%s报告（%d 项 · %s）\n总传输时间：%s · 平均速度：%s",
                dir,files,formatSize(bytes),formatDuration(secs),formatSpeed(mbps));
        }
        return null;
    }
    void refresh(){
        if(working)return;
        try{
            File status=file("status.json");if(!status.exists())return;
            JSONObject data=new JSONObject(new String(Files.readAllBytes(status.toPath()),"UTF-8"));
            String session=data.optString("session");
            if(!session.equals(currentSession)){currentSession=session;prompted=-1;if(prompt!=null){prompt.dismiss();prompt=null;}}
            String mode=data.optString("mode");
            JSONObject tr=data.optJSONObject("transfer");
            boolean recvActive=tr!=null&&tr.optBoolean("active",false)&&"recv".equals(tr.optString("direction"));
            sending=data.optBoolean("send_busy");send.setEnabled(!sending);
            stateLabel.setText(mode.equals("scanning")?"搜索附近设备":mode.equals("ready")?(recvActive?"已连接 · 正在接收":sending?"已连接 · 正在发送":"已连接 · 可以接收"):mode.equals("stopped")?"已停止":"需要检查");
            String text=data.optString("message");
            long total=data.optLong("total"),sent=data.optLong("sent");
            if(tr==null&&sending&&total>0)text+=String.format(Locale.ROOT,"\n%.1f / %.1f MiB",sent/1048576.0,total/1048576.0);
            String stats=renderTransfer(tr);
            if(stats!=null){transferLabel.setText(stats);transferLabel.setVisibility(View.VISIBLE);}
            else{transferLabel.setVisibility(View.GONE);}
            JSONObject receiver=data.optJSONObject("receiver");
            if(receiver!=null)text+="\n已发现对方的 AirDrop 接收服务";
            detail.setText(text);
            JSONArray list=data.optJSONArray("peers");
            if(list!=null&&!list.toString().equals(peersKey)){
                peersKey=list.toString();String old=devices.getSelectedItemPosition()>=0&&devices.getSelectedItemPosition()<macs.size()?macs.get(devices.getSelectedItemPosition()):null;
                macs.clear();ArrayList<String> labels=new ArrayList<>();
                for(int i=0;i<list.length();i++){JSONObject p=list.getJSONObject(i);String mac=p.getString("mac");macs.add(mac);String name=p.optString("name");int type=p.optInt("device_class");String kind=type==2?"iPhone / iPad":type==1?"Mac":type==8?"Apple TV":"Apple 设备";if(name.isEmpty()||name.matches("[0-9a-fA-F-]{36}"))name=kind;int strength=p.optInt("rssi",-100);labels.add(name+" · "+(strength>-55?"信号强":strength>-75?"信号中等":"信号较弱")+"\n设备尾号 "+mac.substring(12));}
                devices.setAdapter(new ArrayAdapter<String>(this,android.R.layout.simple_spinner_dropdown_item,labels));
                if(old!=null&&macs.contains(old))devices.setSelection(macs.indexOf(old));
            }
            JSONObject request=data.optJSONObject("request");
            if(request!=null&&request.getLong("id")!=prompted){
                prompted=request.getLong("id");final long id=prompted;
                final String promptSession=currentSession;
                if(prompt!=null)prompt.dismiss();
                JSONArray names=request.getJSONArray("files");StringBuilder description=new StringBuilder(request.optString("sender","附近设备")).append(" 想发送：\n");
                for(int i=0;i<names.length();i++)description.append(names.getString(i)).append('\n');
                prompt=new AlertDialog.Builder(this).setTitle("接收 AirDrop？").setMessage(description.toString())
                    .setPositiveButton("接收",(d,w)->answer(id,true,promptSession)).setNegativeButton("拒绝",(d,w)->answer(id,false,promptSession))
                    .setOnCancelListener(d->answer(id,false,promptSession)).show();
            }else if(request==null&&prompt!=null){prompt.dismiss();prompt=null;prompted=-1;}
        }catch(Exception e){detail.setText("读取状态失败："+e.getMessage());}
    }
    final Runnable poll=new Runnable(){public void run(){if(closed)return;refresh();handler.postDelayed(this,350);}};
    @Override public void onCreate(Bundle saved){super.onCreate(saved);
        getWindow().addFlags(WindowManager.LayoutParams.FLAG_KEEP_SCREEN_ON);
        file("commands").mkdirs();file("outbox").mkdirs();file("received").mkdirs();
        ScrollView scroll=new ScrollView(this);scroll.setOnApplyWindowInsetsListener((v,insets)->{v.setPadding(insets.getSystemWindowInsetLeft(),insets.getSystemWindowInsetTop(),insets.getSystemWindowInsetRight(),insets.getSystemWindowInsetBottom());return insets;});LinearLayout box=new LinearLayout(this);box.setOrientation(LinearLayout.VERTICAL);int p=(int)(22*getResources().getDisplayMetrics().density);box.setPadding(p,p,p,p);scroll.addView(box);setContentView(scroll);
        label(box,"Owly AirDrop",28);label(box,"小米 14 · 独立测试版",15);
        label(box,"① 将 iPhone AirDrop 设为“所有人（10 分钟）”，打开分享面板。\n② 搜索并选择 iPhone，然后连接。\n③ iPhone 选择“Xiaomi 14 Owly”发送；或在这里选文件发给 iPhone。",15);
        stateLabel=label(box,"尚未开启",21);
        transferLabel=label(box,"",15);
        transferLabel.setTextIsSelectable(true);
        transferLabel.setBackgroundColor(0xFFEEF4FB);
        transferLabel.setTextColor(0xFF143354);
        transferLabel.setPadding(p/2,p/3,p/2,p/3);
        transferLabel.setVisibility(View.GONE);
        coexistSwitch=new Switch(this);
        coexistSwitch.setText("保持普通 Wi-Fi 联网（2.4GHz / 非 DFS 5GHz）");
        coexistSwitch.setChecked(isCoexistAuto());
        coexistSwitch.setPadding(0,8,0,12);
        coexistSwitch.setOnCheckedChangeListener((btn,checked)->{
            saveCoexistMode(checked);
            message(checked?"已设为自动保持普通 Wi-Fi 联网（2.4 GHz 或非 DFS 5 GHz 时启用共存）":"已设为独占模式（AirDrop 期间暂停普通 Wi-Fi）");
        });
        box.addView(coexistSwitch);
        scan=button(box,"搜索附近设备",()->radio("scan","正在搜索"));
        devices=new Spinner(this);box.addView(devices);
        connect=button(box,"连接所选设备",()->{
            int i=devices.getSelectedItemPosition();if(i<0||i>=macs.size()){message("请先搜索，并打开 iPhone 的 AirDrop 分享面板。");return;}
            String mac=macs.get(i);if(!mac.matches("([0-9a-f]{2}:){5}[0-9a-f]{2}"))return;
            radio("connect "+mac,"正在连接所选设备");
        });
        button(box,"选择发送文件",()->{
            if(sending||working)return;
            Intent intent=new Intent(Intent.ACTION_OPEN_DOCUMENT).setType("*/*").addCategory(Intent.CATEGORY_OPENABLE);startActivityForResult(intent,1);
        });
        fileLabel=label(box,"尚未选择文件",15);
        send=button(box,"发送文件",()->{try{
            if(selectedPath==null){message("请先选择文件。");return;}
            command(new JSONObject().put("op","send").put("path",selectedPath));
            sending=true;send.setEnabled(false);message("已提交发送请求");
        }catch(Exception e){message(e.toString());}});
        button(box,"收到的文件 · 保存到…",this::receivedFiles);
        stop=button(box,"停止并恢复 Wi-Fi",()->radio("stop","正在恢复 Wi-Fi"));
        label(box,"开启“保持普通 Wi-Fi 联网”时，在 2.4 GHz 或非 DFS 5 GHz（如 36–48 / 149–165 信道）下会自动维持原 Wi-Fi 连接；未连接 Wi-Fi 或处于 DFS 信道时会自动回退独占模式。提示：双 5 GHz 共存下从 iPhone 接收大体积视频时若速度偏慢，可临时关闭上方开关使用独占高速模式，或将手机 Wi-Fi 连至 2.4 GHz。单次无线会话最多 30 分钟；结束请点“停止并恢复 Wi-Fi”。",13);
        detail=label(box,"首次使用需要在 KernelSU 授权此 App 的 root 权限。",14);detail.setTextIsSelectable(true);
        handler.post(poll);
    }
    void collect(File dir,ArrayList<File> out){File[] children=dir.listFiles();if(children==null)return;Arrays.sort(children);for(File child:children){if(child.getName().startsWith("."))continue;if(child.isDirectory())collect(child,out);else out.add(child);}}
    void receivedFiles(){ArrayList<File> paths=new ArrayList<>();collect(file("received"),paths);if(paths.isEmpty()){message("还没有收到文件。");return;}
        String prefix=file("received").getAbsolutePath()+"/";String[] names=new String[paths.size()];for(int i=0;i<paths.size();i++)names[i]=paths.get(i).getAbsolutePath().substring(prefix.length());
        new AlertDialog.Builder(this).setTitle("选择要保存的文件").setItems(names,(d,n)->{exportFile=paths.get(n);Intent intent=new Intent(Intent.ACTION_CREATE_DOCUMENT).setType("application/octet-stream").addCategory(Intent.CATEGORY_OPENABLE).putExtra(Intent.EXTRA_TITLE,exportFile.getName());startActivityForResult(intent,2);}).show();
    }
    @Override protected void onActivityResult(int request,int result,Intent data){super.onActivityResult(request,result,data);if(result!=RESULT_OK||data==null)return;Uri uri=data.getData();if(uri==null)return;
        if(request==2&&exportFile!=null){final File source=exportFile;worker.execute(()->{try(InputStream in=new FileInputStream(source);OutputStream out=getContentResolver().openOutputStream(uri)){copy(in,out);message("已保存："+source.getName());}catch(Exception e){message("保存失败："+e.getMessage());}});return;}
        if(request!=1)return;
        String name="file.bin";try(Cursor c=getContentResolver().query(uri,new String[]{OpenableColumns.DISPLAY_NAME},null,null,null)){if(c!=null&&c.moveToFirst())name=c.getString(0);}
        name=name.replaceAll("[/\\\\\\p{Cntrl}]","_");if(name.isEmpty()||name.equals(".")||name.equals(".."))name="file.bin";
        final String label=name;fileLabel.setText("正在读取文件…");
        worker.execute(()->{File directory=file("outbox/"+UUID.randomUUID());directory.mkdirs();File destination=new File(directory,label);
            try(InputStream in=getContentResolver().openInputStream(uri);OutputStream out=new FileOutputStream(destination)){copy(in,out);String old=selectedPath;selectedPath=destination.getAbsolutePath();if(old!=null)new File(old).delete();runOnUiThread(()->fileLabel.setText(label+String.format(Locale.ROOT," · %.2f MiB",destination.length()/1048576.0)));}
            catch(Exception e){destination.delete();message("读取失败："+e.getMessage());}
        });
    }
    void copy(InputStream in,OutputStream out)throws IOException{byte[] bytes=new byte[65536];long count=0;int n;while((n=in.read(bytes))!=-1){count+=n;if(count>4L*1024*1024*1024)throw new IOException("本测试版单文件上限 4 GiB");out.write(bytes,0,n);}}
    @Override protected void onDestroy(){closed=true;handler.removeCallbacks(poll);super.onDestroy();}
}
