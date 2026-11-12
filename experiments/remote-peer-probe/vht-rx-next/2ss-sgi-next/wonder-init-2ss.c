/* SPDX-License-Identifier: GPL-2.0-only
 * Reuse the existing audited netlink transport. Expose only fixed-channel
 * 149 setup, with no probe-frame transmit or arbitrary frequency command.
 */
#include "../../../../third_party/upstream/native/wonder-probe/probe.c"

static void initialise(u32 width, u32 gi, u8 nss, u8 mcs, const char *country)
{
    const u8 bssid[] = {0x00, 0x25, 0x00, 0xff, 0x94, 0x73};
    verify_interface(0);

    usize at = vendor(4);
    attr(1, country, 3);
    finish_vendor(at);

    at = vendor(1);
    a32(1, 5745);
    a16(2, width);
    finish_vendor(at);

    at = vendor(2);
    a32(1, 0);
    usize nested = used;
    attr(2 | 0x8000, 0, 0);
    a8(1, 1);
    attr(2, bssid, sizeof(bssid));
    ((struct ah *)(tx + nested))->size = used - nested;
    finish_vendor(at);

    at = vendor(3);
    a32(1, width == 2 ? 2 : 1);
    /* Match the strict NLA_U16 policy. attr() zeroes the two alignment
     * bytes also consumed by this driver's historical nla_get_u32. */
    a16(2, width);
    a32(3, gi);
    a8(4, nss);
    a8(5, mcs);
    finish_vendor(at);

    /* The source-built driver initializes both aggregation fields to zero.
     * It has no AMPDUCFG vendor command, so no synthetic success is needed. */
    out("cache=complete country=");
    out(country);
    out(" frequency=5745 bw=");
    number(width);
    out(" gi=");
    number(gi);
    out(" nss=");
    number(nss);
    out(" mcs=");
    number(mcs);
    out(" firmware_aggregation=off\n");
}

static int parse_profile(const char *p, u32 *width, u32 *gi, u8 *nss, u8 *mcs)
{
    if (equal(p, "ht20")) {
        *width = 0; *gi = 0; *nss = 1; *mcs = 7; return 1;
    }
    if (equal(p, "vht80")) {
        *width = 2; *gi = 0; *nss = 1; *mcs = 7; return 1;
    }
    if (equal(p, "vht80-sgi")) {
        *width = 2; *gi = 1; *nss = 1; *mcs = 7; return 1;
    }
    if (equal(p, "vht80-2ss-sgi")) {
        *width = 2; *gi = 1; *nss = 2; *mcs = 7; return 1;
    }
    if (equal(p, "vht80-2ss-mcs8-sgi")) {
        *width = 2; *gi = 1; *nss = 2; *mcs = 8; return 1;
    }
    if (equal(p, "vht80-2ss-mcs9-sgi")) {
        *width = 2; *gi = 1; *nss = 2; *mcs = 9; return 1;
    }
    return 0;
}

__attribute__((used, noreturn)) void houji_entry(long *stack)
{
    int argc = (int)stack[0];
    char **argv = (char **)(stack + 1);
    int create = argc == 3 && equal(argv[1], "create");
    int cache = argc == 7 && equal(argv[1], "cache");
    u32 width = 0, gi = 0;
    u8 nss = 1, mcs = 7;
    if (!create && !cache)
        fail("usage: wonder-init create PHY | cache PHY IFINDEX PROFILE COUNTRY 149", -22);
    want_phy = decimal(argv[2], 0x7fffffff);
    if (cache) {
        want_if = decimal(argv[3], 0x7fffffff);
        if (!want_if || len(argv[5]) != 2 || argv[5][0] < 'A' ||
            argv[5][0] > 'Z' || argv[5][1] < 'A' || argv[5][1] > 'Z' ||
            !equal(argv[6], "149"))
            fail("invalid interface, country or channel", -22);
        if (!parse_profile(argv[4], &width, &gi, &nss, &mcs))
            fail("unknown radio profile", -22);
    }
    connect_netlink();
    verify_phy();
    if (create) {
        begin(family, NL80211_CMD_NEW_INTERFACE, 0);
        a32(NL80211_ATTR_WIPHY, want_phy);
        attr(NL80211_ATTR_IFNAME, "wonder0", 8);
        a32(NL80211_ATTR_IFTYPE, NL80211_IFTYPE_MONITOR);
        request(NONE, 0);
    } else {
        initialise(width, gi, nss, mcs, argv[5]);
    }
    call(SC_CLOSE, sock, 0, 0, 0, 0, 0);
    leave(0);
}

__asm__(".global houji_start\nhouji_start:\nmov x0, sp\nbl houji_entry\n");
