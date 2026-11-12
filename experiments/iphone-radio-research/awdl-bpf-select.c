/* Darwin 21 net/bpf.h. Reject all packets before attaching, with no reads. */
#include <errno.h>
#include <fcntl.h>
#include <net/bpf.h>
#include <net/if.h>
#include <stdio.h>
#include <sys/ioctl.h>
#include <unistd.h>

int main(void)
{
    alarm(3);
    int fd = -1;
    for (unsigned i = 0; i < 32; ++i) {
        char path[32];
        snprintf(path, sizeof(path), "/dev/bpf%u", i);
        fd = open(path, O_RDONLY | O_CLOEXEC);
        if (fd >= 0)
            break;
        if (errno != EBUSY) {
            perror("open");
            return 1;
        }
    }
    if (fd < 0) {
        fputs("No idle BPF descriptor\n", stderr);
        return 1;
    }

    struct bpf_insn reject = { .code = BPF_RET | BPF_K, .k = 0 };
    struct bpf_program filter = { .bf_len = 1, .bf_insns = &reject };
    struct ifreq interface = { .ifr_name = "awdl0" };
    int status = 1;
    unsigned initial = 0, requested = 147, actual = 0;
    if (ioctl(fd, BIOCSETF, &filter) < 0) {
        perror("BIOCSETF");
    } else if (ioctl(fd, BIOCSETIF, &interface) < 0) {
        perror("BIOCSETIF");
    } else if (ioctl(fd, BIOCGDLT, &initial) < 0) {
        perror("BIOCGDLT initial");
    } else if (ioctl(fd, BIOCSDLT, &requested) < 0) {
        printf("initial=%u requested=%u\n", initial, requested);
        perror("BIOCSDLT");
    } else if (ioctl(fd, BIOCGDLT, &actual) < 0) {
        perror("BIOCGDLT final");
    } else {
        printf("interface=awdl0 initial=%u requested=%u actual=%u packet_reads=0\n",
               initial, requested, actual);
        status = actual != requested;
    }
    close(fd);
    return status;
}
