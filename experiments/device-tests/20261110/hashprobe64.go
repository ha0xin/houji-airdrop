package main
import("net";"time";"fmt";"io";"encoding/binary";"crypto/sha256";"bytes")
func must(e error){if e!=nil{panic(e)}}
func main(){
 ni,e:=net.InterfaceByName("xos0");must(e);addrs,e:=ni.Addrs();must(e);var ip net.IP
 for _,a:=range addrs {n,ok:=a.(*net.IPNet);if ok&&n.IP.IsLinkLocalUnicast(){ip=n.IP;break}}
 if ip==nil{panic("no xos0 link-local address")}
 l,e:=net.ListenTCP("tcp6",&net.TCPAddr{IP:ip,Port:18870,Zone:"xos0"});must(e);defer l.Close();must(l.SetDeadline(time.Now().Add(60*time.Second)));fmt.Println("READY",ip)
 for k:=0;k<2;k++{c,e:=l.AcceptTCP();must(e);must(c.SetDeadline(time.Now().Add(50*time.Second)));var header [8]byte;_,e=io.ReadFull(c,header[:]);must(e);mode:=binary.BigEndian.Uint32(header[:4]);n:=int64(binary.BigEndian.Uint32(header[4:]));if mode>1||n<1||n>64<<20{panic("bad header")};start:=time.Now();md:=sha256.New()
 if mode==0{_,e=io.CopyN(md,c,n);must(e);_,e=c.Write(md.Sum(nil));must(e)}else{data:=make([]byte,n);for i:=range data{data[i]=byte(i)};_,e=io.Copy(c,bytes.NewReader(data));must(e);sum:=sha256.Sum256(data);ack:=make([]byte,32);_,e=io.ReadFull(c,ack);must(e);if !bytes.Equal(ack,sum[:]){panic("hash mismatch")}}
 fmt.Printf("HASH_OK mode=%d bytes=%d seconds=%f\n",mode,n,time.Since(start).Seconds());must(c.Close())
 }
}
