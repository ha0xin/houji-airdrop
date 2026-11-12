package main

import (
	"bytes"
	"crypto/sha256"
	"context"
	"encoding/binary"
	"errors"
	"fmt"
	"io"
	"net"
	"os"
	"strconv"
	"syscall"
	"time"
)

const (
	iface = "owly0"
	port = 18870
	maxSize = 128 << 20
)

func linkLocal() (net.IP, error) {
	device, err := net.InterfaceByName(iface)
	if err != nil {
		return nil, err
	}
	addrs, err := device.Addrs()
	if err != nil {
		return nil, err
	}
	for _, addr := range addrs {
		ip, ok := addr.(*net.IPNet)
		if ok && ip.IP.IsLinkLocalUnicast() {
			return ip.IP, nil
		}
	}
	return nil, errors.New("no owly0 IPv6 link-local address")
}

func transfer(conn *net.TCPConn, data []byte) error {
	if err := conn.SetDeadline(time.Now().Add(100 * time.Second)); err != nil {
		return err
	}
	var header [12]byte
	if _, err := io.ReadFull(conn, header[:]); err != nil {
		return err
	}
	mode := binary.BigEndian.Uint32(header[:4])
	size := int64(binary.BigEndian.Uint32(header[4:8]))
	sample := binary.BigEndian.Uint32(header[8:])
	if sample == 0 || mode > 1 || size < 1 || size > int64(len(data)) {
		return errors.New("bad probe header")
	}
	start := time.Now()
	var digest []byte
	if mode == 0 {
		hasher := sha256.New()
		if _, err := io.CopyN(hasher, conn, size); err != nil {
			return err
		}
		digest = hasher.Sum(nil)
		if _, err := conn.Write(digest); err != nil {
			return err
		}
	} else {
		data := data[:size]
		sha := sha256.Sum256(data)
		digest = sha[:]
		if _, err := io.Copy(conn, bytes.NewReader(data)); err != nil {
			return err
		}
		var remote [32]byte
		if _, err := io.ReadFull(conn, remote[:]); err != nil {
			return err
		}
		if !bytes.Equal(remote[:], digest) {
			return errors.New("Mac reported wrong digest")
		}
	}
	fmt.Printf("HASH_OK sample=%d mode=%d bytes=%d seconds=%.6f sha256=%x\n", sample, mode, size, time.Since(start).Seconds(), digest)
	return nil
}

func listen(ip net.IP, mss int) (*net.TCPListener, error) {
    config := net.ListenConfig{}
    if mss != 0 {
        config.Control = func(_, _ string, raw syscall.RawConn) error {
            var socketErr error
            err := raw.Control(func(fd uintptr) {
                socketErr = syscall.SetsockoptInt(int(fd), syscall.IPPROTO_TCP, syscall.TCP_MAXSEG, mss)
            })
            if err != nil {
                return err
            }
            return socketErr
        }
    }
    listener, err := config.Listen(context.Background(), "tcp6",
        (&net.TCPAddr{IP: ip, Port: port, Zone: iface}).String())
    if err != nil {
        return nil, err
    }
    return listener.(*net.TCPListener), nil
}

func tcpMSS(conn *net.TCPConn) (int, error) {
    raw, err := conn.SyscallConn()
    if err != nil {
        return 0, err
    }
    var mss int
    var socketErr error
    err = raw.Control(func(fd uintptr) {
        mss, socketErr = syscall.GetsockoptInt(int(fd), syscall.IPPROTO_TCP, syscall.TCP_MAXSEG)
    })
    if err != nil {
        return 0, err
    }
    return mss, socketErr
}

func run() error {
	if len(os.Args) != 3 && len(os.Args) != 4 {
		return errors.New("usage: owly-hashprobe transfer-count max-transfer-bytes [1160]")
	}
	tries, err := strconv.Atoi(os.Args[1])
	if err != nil || tries < 1 || tries > 20 {
		return errors.New("transfer-count out of range")
	}
	size, err := strconv.Atoi(os.Args[2])
	if err != nil || size < 1 || size > maxSize {
		return errors.New("max-transfer-bytes out of range")
	}
	var mss int
	if len(os.Args) == 4 {
		mss, err = strconv.Atoi(os.Args[3])
		if err != nil || mss != 1160 {
			return errors.New("unsupported test MSS")
		}
	}
	data := make([]byte, size)
	for i := range data {
		data[i] = byte(i)
	}
	ip, err := linkLocal()
	if err != nil {
		return err
	}
	listener, err := listen(ip, mss)
	if err != nil {
		return err
	}
	defer listener.Close()
	fmt.Println("HASH_READY", ip)
	var failures int
	for i := 0; i < tries; i++ {
		if err := listener.SetDeadline(time.Now().Add(120 * time.Second)); err != nil {
			return err
		}
		conn, err := listener.AcceptTCP()
		if err != nil {
			return err
		}
		actualMSS, mssErr := tcpMSS(conn)
		if mssErr != nil || (mss != 0 && actualMSS > mss) {
			conn.Close()
			return fmt.Errorf("requested MSS %d, effective %d, error %v", mss, actualMSS, mssErr)
		}
		fmt.Printf("TCP_MSS attempt=%d effective=%d\n", i+1, actualMSS)
		err = transfer(conn, data)
		conn.Close()
		if err != nil {
			failures++
			fmt.Printf("HASH_FAILED attempt=%d error=%v\n", i+1, err)
		}
	}
	if failures != 0 {
		return fmt.Errorf("%d probe transfer(s) failed", failures)
	}
	return nil
}

func main() {
	if err := run(); err != nil {
		fmt.Fprintln(os.Stderr, err)
		os.Exit(1)
	}
}
