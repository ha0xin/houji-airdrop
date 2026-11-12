package main
import("net";"time";"fmt";"io";"encoding/binary")
func mustUDP(e error){if e!=nil{panic(e)}}
func main(){
 ni,e:=net.InterfaceByName("xos0");mustUDP(e);as,e:=ni.Addrs();mustUDP(e);var ip net.IP
 for _,a:=range as{n,ok:=a.(*net.IPNet);if ok&&n.IP.IsLinkLocalUnicast(){ip=n.IP;break}}
 if ip==nil{panic("no link local")}
 l,e:=net.ListenTCP("tcp6",&net.TCPAddr{IP:ip,Port:18872,Zone:"xos0"});mustUDP(e);defer l.Close()
 mustUDP(l.SetDeadline(time.Now().Add(30*time.Second)));fmt.Println("READY",ip)
 c,e:=l.AcceptTCP();mustUDP(e);defer c.Close();mustUDP(c.SetDeadline(time.Now().Add(20*time.Second)))
 var h [12]byte;_,e=io.ReadFull(c,h[:]);mustUDP(e)
 size:=binary.BigEndian.Uint32(h[:4]);count:=binary.BigEndian.Uint32(h[4:8]);ms:=binary.BigEndian.Uint32(h[8:])
 if size<1000||size>1400||count<1||count>100000||ms<1000||ms>10000{panic("bad request")}
 u,e:=net.ListenUDP("udp6",&net.UDPAddr{IP:ip,Port:18873,Zone:"xos0"});mustUDP(e);defer u.Close()
 dst:=&net.UDPAddr{IP:c.RemoteAddr().(*net.TCPAddr).IP,Port:18874,Zone:"xos0"}
 data:=make([]byte,size);for i:=range data{data[i]=byte(i)}
 start:=time.Now();duration:=time.Duration(ms)*time.Millisecond;errors:=0
 for i:=uint32(0);i<count;i++ {
  due:=start.Add(time.Duration(int64(duration)*int64(i)/int64(count)))
  if delay:=time.Until(due);delay>0{time.Sleep(delay)}
  binary.BigEndian.PutUint32(data[:4],i)
  mustUDP(u.SetWriteDeadline(time.Now().Add(2*time.Second)))
  n,e:=u.WriteToUDP(data,dst);if e!=nil||n!=len(data){errors++}
 }
 fmt.Fprintf(c,"DONE count=%d seconds=%.6f errors=%d\n",count,time.Since(start).Seconds(),errors)
 fmt.Printf("UDP_DONE count=%d seconds=%.6f errors=%d\n",count,time.Since(start).Seconds(),errors)
}
