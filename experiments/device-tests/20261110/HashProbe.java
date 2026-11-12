import java.net.*;import java.io.*;import java.util.*;import java.security.*;
public class HashProbe {
 public static void main(String[] args)throws Exception {
  NetworkInterface ni=NetworkInterface.getByName("xos0");InetAddress addr=null;
  for(InetAddress a:Collections.list(ni.getInetAddresses()))if(a instanceof Inet6Address&&a.isLinkLocalAddress())addr=a;
  try(ServerSocket ss=new ServerSocket()){ss.setReuseAddress(true);ss.bind(new InetSocketAddress(addr,18870));ss.setSoTimeout(60000);System.out.println("READY "+addr);
   for(int k=0;k<2;k++)try(Socket s=ss.accept()) {
    s.setSoTimeout(45000);DataInputStream in=new DataInputStream(s.getInputStream());DataOutputStream out=new DataOutputStream(s.getOutputStream());int mode=in.readInt(),size=in.readInt();
    if((mode!=0&&mode!=1)||size<1||size>16*1024*1024)throw new IOException("header");
    byte[] b=new byte[65536];for(int i=0;i<b.length;i++)b[i]=(byte)i;
    MessageDigest md=MessageDigest.getInstance("SHA-256");long t=System.nanoTime();int n=0;
    if(mode==0){while(n<size){int r=in.read(b,0,Math.min(b.length,size-n));if(r<0)throw new EOFException();md.update(b,0,r);n+=r;}out.write(md.digest());out.flush();}
    else {while(n<size){int r=Math.min(b.length,size-n);out.write(b,0,r);md.update(b,0,r);n+=r;}out.flush();byte[] expected=md.digest(),ack=new byte[32];in.readFully(ack);if(!Arrays.equals(expected,ack))throw new IOException("hash mismatch");}
    System.out.println("HASH_OK mode="+mode+" bytes="+n+" seconds="+(System.nanoTime()-t)/1e9);
   }
  }
 }
}
