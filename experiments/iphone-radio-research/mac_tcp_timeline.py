"""Bounded timeline of read-only Darwin TCP_CONNECTION_INFO snapshots."""

import ctypes
import os
import threading
import time


V2_FIELDS = (
    "mss", "rtt_current_ms", "rtt_smoothed_ms", "tx_packets", "tx_bytes",
    "tx_retransmit_packets", "tx_retransmit_bytes", "rx_packets", "rx_bytes",
    "rx_out_of_order_bytes", "snd_cwnd_bytes", "snd_wnd_bytes", "snd_sbbytes",
    "loss_recovery",
)
INTERVAL_NS = 200_000_000


def load_v2(path):
    """Return a snapshot function that reads the caller's existing socket fd."""
    library = ctypes.CDLL(os.fspath(path))
    sample = library.awdl_tcp_info_sample_v2
    sample.argtypes = (ctypes.c_int, ctypes.POINTER(ctypes.c_uint64), ctypes.c_size_t)
    sample.restype = ctypes.c_int
    vector = ctypes.c_uint64 * len(V2_FIELDS)

    def snapshot(fd):
        output = vector()
        error = sample(fd, output, len(output))
        if error:
            raise OSError(error, os.strerror(error))
        return dict(zip(V2_FIELDS, output))

    return snapshot


class TcpTimeline:
    """Nest inside the socket's context and read result() after joining.

    The owner keeps the original socket open until this context exits. The
    sampler holds a reference to it and uses that socket's unchanged fd.
    """

    def __init__(self, sock, sample_fd, origin_ns, *, max_samples=512):
        if not isinstance(max_samples, int) or not 1 <= max_samples <= 1024:
            raise ValueError("max_samples must be in 1..1024")
        self._socket = sock
        self._fd = sock.fileno()
        self._sample_fd = sample_fd
        self._origin_ns = origin_ns
        self._limit = max_samples
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._capture, name="awdl-tcp-timeline")
        self._samples = []
        self._status = "pending"
        self._error = {}
        self._started = False
        self._closed = False

    def __enter__(self):
        if self._status != "pending":
            raise RuntimeError("TcpTimeline cannot be reused")
        self._status = "running"
        try:
            self._thread.start()
            self._started = True
        except RuntimeError:
            self._status = "start_error"
        return self

    def _capture(self):
        deadline = self._origin_ns
        while not self._stop.wait(max(0, deadline - time.monotonic_ns()) / 1e9):
            try:
                fd = self._socket.fileno()
                if fd < 0 or fd != self._fd:
                    self._status = "socket_changed"
                    return
                timestamp = time.monotonic_ns()
                values = self._sample_fd(fd)
                row = {"offset_ns": timestamp - self._origin_ns, **values}
            except OSError as error:
                self._status = "sample_error"
                self._error = {"errno": error.errno}
                return
            except Exception as error:
                self._status = "sample_error"
                self._error = {"error_type": type(error).__name__}
                return
            self._samples.append(row)
            if len(self._samples) == self._limit:
                self._status = "sample_limit"
                return
            elapsed = max(0, time.monotonic_ns() - deadline)
            deadline += (elapsed // INTERVAL_NS + 1) * INTERVAL_NS

    def __exit__(self, exc_type, exc, traceback):
        self._stop.set()
        if self._started:
            self._thread.join()
        if self._status == "running":
            self._status = "complete" if self._samples else "no_samples"
        self._closed = True

    def result(self):
        if not self._closed:
            raise RuntimeError("read the timeline after its context exits")
        return {
            "status": self._status,
            "interval_ms": INTERVAL_NS // 1_000_000,
            "samples": list(self._samples),
            **self._error,
        }
