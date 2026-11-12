extern int domestic_xos_start(int chan);
extern int domestic_xoscomm_stop(void);
extern int pause(void);
extern int printf(const char *fmt, ...);
extern int fflush(void *stream);
extern int atoi(const char *s);
extern void *dlsym(void *handle, const char *symbol);
extern void __libc_init(void *raw_args, void (*onexit)(void), int (*slingshot)(int, char**, char**), void *structors);

#define RTLD_NEXT ((void *)-1L)

/* Android/Linux netlink wire layout; no host SDK headers required. */
extern void *nlmsg_hdr(void *msg);
static unsigned int get_le(const unsigned char *p, unsigned int n) {
    unsigned int v = 0;
    for (unsigned int i = 0; i < n; ++i) v |= (unsigned int)p[i] << (8 * i);
    return v;
}
static unsigned char *attr(unsigned char *p, unsigned int len,
                           unsigned int type, unsigned int *size) {
    unsigned char *found = 0;
    while (len) {
        if (len < 4) return 0;
        unsigned int n = get_le(p, 2), t = get_le(p + 2, 2) & 0x3fff;
        if (n < 4 || n > len || ((n + 3) & ~3U) > len) return 0;
        if (t == type) {
            if (found) return 0;
            found = p + 4;
            *size = n - 4;
        }
        n = (n + 3) & ~3U;
        p += n; len -= n;
    }
    return found;
}
static void rewrite_rate(void *msg) {
    unsigned char *h = nlmsg_hdr(msg);
    if (!h) return;
    unsigned int len = get_le(h, 4), n = 0;
    if (len < 20 || len > 65536 || h[16] != 103) return;
    unsigned char *oui = attr(h + 20, len - 20, 195, &n);
    if (!oui || n != 4 || get_le(oui, 4) != 0x1a11) return;
    unsigned char *cmd = attr(h + 20, len - 20, 196, &n);
    if (!cmd || n != 4 || (get_le(cmd, 4) != 3 && get_le(cmd, 4) != 1)) return;
    unsigned int subcmd = get_le(cmd, 4);
    unsigned char *data = attr(h + 20, len - 20, 197, &n);
    if (!data) return;
    if (subcmd == 1) {
        unsigned int fs=0, bs=0;
        unsigned char *freq=attr(data,n,1,&fs), *bw=attr(data,n,2,&bs);
        if (freq && bw && fs==4 && bs==2 && get_le(freq,4)==5745 && get_le(bw,2)==2) {
            bw[0]=0; bw[1]=0;
            printf("TEST HT20: 5745 MHz bandwidth 2 -> 0\n"); fflush(0);
        }
        return;
    }
    unsigned int sizes[5] = {0};
    unsigned char *fields[5];
    for (unsigned int i = 0; i < 5; ++i) {
        fields[i] = attr(data, n, i + 1, &sizes[i]);
        if (!fields[i] || sizes[i] > 4 || sizes[i] == 0) return;
    }
    /* houji validates HT20/NSS1/MCS0..7. MCS5 improved measured TX
     * throughput over MCS0; preserve long GI and all other parameters. */
    const unsigned int fallback[5] = {1, 0, 0, 1, 5};
    printf("TEST original rate: %u,%u,%u,%u,%u\n",get_le(fields[0],sizes[0]),get_le(fields[1],sizes[1]),get_le(fields[2],sizes[2]),get_le(fields[3],sizes[3]),get_le(fields[4],sizes[4]));
    for (unsigned int i = 0; i < 5; ++i)
        for (unsigned int j = 0; j < sizes[i]; ++j)
            fields[i][j] = (fallback[i] >> (8 * j)) & 255;
    printf("XOS rate fallback: HT20 NSS1 MCS5 GI-default (1,0,0,1,5)\n");
    fflush(0);
}
int nl_send_auto(void *sk, void *msg) {
    int (*real)(void *, void *) = dlsym(RTLD_NEXT, "nl_send_auto");
    rewrite_rate(msg);
    return real ? real(sk, msg) : -1;
}
int nl_send_auto_complete(void *sk, void *msg) {
    int (*real)(void *, void *) = dlsym(RTLD_NEXT, "nl_send_auto_complete");
    rewrite_rate(msg);
    return real ? real(sk, msg) : -1;
}

static int *g_last_err_arg = 0;

static int my_nl_err_cb(void *nla, void *nlerr, void *arg) {
    if (arg) {
        *(int *)arg = 0;
    }
    return 0;
}

int nl_cb_err(void *cb, int kind, void *func, void *arg) {
    int (*real_nl_cb_err)(void *, int, void *, void *) = dlsym(RTLD_NEXT, "nl_cb_err");
    g_last_err_arg = (int *)arg;
    return real_nl_cb_err(cb, kind, (void *)my_nl_err_cb, arg);
}

int nl_recvmsgs(void *sk, void *cb) {
    int (*real_nl_recvmsgs)(void *, void *) = dlsym(RTLD_NEXT, "nl_recvmsgs");
    int rc = real_nl_recvmsgs(sk, cb);
    if (rc < 0) {
        if (g_last_err_arg) {
            *g_last_err_arg = 0;
        }
        return 0;
    }
    return rc;
}

int main(int argc, char **argv, char **envp) {
    int chan = (argc > 1 && argv[1]) ? atoi(argv[1]) : 6;
    printf("Starting domestic_xos_start(%d)...\n", chan);
    fflush(0);
    int rc = domestic_xos_start(chan);
    printf("domestic_xos_start(%d) returned %d\n", chan, rc);
    fflush(0);
    while (1) {
        pause();
    }
    return 0;
}

void _start_main(void *raw_args) {
    void *structors[3] = {0, 0, 0};
    __libc_init(raw_args, 0, main, structors);
}

__asm__(
".global _start\n"
"_start:\n"
"  mov x29, #0\n"
"  mov x30, #0\n"
"  mov x0, sp\n"
"  b _start_main\n"
);
