// Public Darwin TCP_CONNECTION_INFO wrapper for a caller-owned connected socket.
#include <errno.h>
#include <netinet/in.h>
#include <netinet/tcp.h>
#include <stddef.h>
#include <stdint.h>
#include <sys/socket.h>

// Stable output order for ctypes. No kernel structure crosses the ABI.
enum { snapshot_fields = 10, snapshot_v2_fields = 14 };

static int read_info(int fd, struct tcp_connection_info *info)
{
    socklen_t length = sizeof(*info);
    if (getsockopt(fd, IPPROTO_TCP, TCP_CONNECTION_INFO, info, &length) == -1)
        return errno;
    if (length < offsetof(struct tcp_connection_info, tcpi_txretransmitpackets) +
                 sizeof(info->tcpi_txretransmitpackets))
        return EMSGSIZE;
    return 0;
}

static void write_v1(uint64_t *out, const struct tcp_connection_info *info)
{
    out[0] = info->tcpi_maxseg;
    out[1] = info->tcpi_rttcur;
    out[2] = info->tcpi_srtt;
    out[3] = info->tcpi_txpackets;
    out[4] = info->tcpi_txbytes;
    out[5] = info->tcpi_txretransmitpackets;
    out[6] = info->tcpi_txretransmitbytes;
    out[7] = info->tcpi_rxpackets;
    out[8] = info->tcpi_rxbytes;
    out[9] = info->tcpi_rxoutoforderbytes;
}

int awdl_tcp_info_sample(int fd, uint64_t *out, size_t out_count)
{
    if (!out || out_count != snapshot_fields)
        return EINVAL;
    struct tcp_connection_info info = {0};
    int error = read_info(fd, &info);
    if (error)
        return error;
    write_v1(out, &info);
    return 0;
}

int awdl_tcp_info_sample_v2(int fd, uint64_t *out, size_t out_count)
{
    if (!out || out_count != snapshot_v2_fields)
        return EINVAL;
    struct tcp_connection_info info = {0};
    int error = read_info(fd, &info);
    if (error)
        return error;
    write_v1(out, &info);
    out[10] = info.tcpi_snd_cwnd;
    out[11] = info.tcpi_snd_wnd;
    out[12] = info.tcpi_snd_sbbytes;
    out[13] = (info.tcpi_flags & TCPCI_FLAG_LOSSRECOVERY) != 0;
    return 0;
}
