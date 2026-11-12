// Offline iOS arm64 probe. By default, only enumerate the AWDL snapshot group.
#include <CoreFoundation/CoreFoundation.h>
#include <dlfcn.h>
#include <errno.h>
#include <inttypes.h>
#include <stdbool.h>
#include <signal.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <time.h>

typedef CFMutableDictionaryRef (*CopyChannelsFn)(
    CFStringRef, CFStringRef, uint64_t, uint64_t, uint64_t);
typedef int (*ChannelCountFn)(CFDictionaryRef);
typedef CFTypeRef (*SubscribeFn)(
    void *, CFMutableDictionaryRef, CFMutableDictionaryRef *, uint64_t, CFTypeRef);
typedef CFDictionaryRef (*SampleFn)(CFTypeRef, CFMutableDictionaryRef, CFTypeRef);
typedef void (*IterateFn)(CFDictionaryRef, int (^)(CFDictionaryRef));
typedef CFStringRef (*StringAccessorFn)(CFDictionaryRef);
typedef uint8_t (*FormatFn)(CFDictionaryRef);
typedef int64_t (*IntegerValueFn)(CFDictionaryRef, int);

static volatile sig_atomic_t stop_requested;

static void request_stop(int signal_number)
{
    stop_requested = signal_number;
}

static bool equal_string(CFStringRef value, CFStringRef expected)
{
    return value && CFEqual(value, expected);
}

static int describe_channels(void *lib, CFDictionaryRef channels)
{
    StringAccessorFn group_of = (StringAccessorFn)dlsym(lib, "IOReportChannelGetGroup");
    StringAccessorFn subgroup_of = (StringAccessorFn)dlsym(lib, "IOReportChannelGetSubGroup");
    StringAccessorFn name_of = (StringAccessorFn)dlsym(lib, "IOReportChannelGetChannelName");
    StringAccessorFn unit_of = (StringAccessorFn)dlsym(lib, "IOReportChannelGetUnitLabel");
    StringAccessorFn driver_of = (StringAccessorFn)dlsym(lib, "IOReportChannelGetDriverName");
    FormatFn format_of = (FormatFn)dlsym(lib, "IOReportChannelGetFormat");
    if (!group_of || !subgroup_of || !name_of || !unit_of || !driver_of || !format_of) {
        fputs("IOReport metadata accessors unavailable\n", stderr);
        return 2;
    }

    CFArrayRef entries = CFDictionaryGetValue(channels, CFSTR("IOReportChannels"));
    const CFStringRef expected[] = {CFSTR("AWDLDuration(s)"), CFSTR("AWDLUse(%)"),
                                    CFSTR("SDB"), CFSTR("TS(ms)")};
    const CFIndex count = sizeof(expected) / sizeof(expected[0]);
    if (!entries || CFGetTypeID(entries) != CFArrayGetTypeID() ||
        CFArrayGetCount(entries) != count) {
        fputs("Unexpected AWDL Snapshot channel list\n", stderr);
        return 3;
    }

    unsigned seen = 0;
    for (CFIndex index = 0; index < count; index++) {
        CFDictionaryRef entry = CFArrayGetValueAtIndex(entries, index);
        if (!entry || CFGetTypeID(entry) != CFDictionaryGetTypeID() ||
            !equal_string(group_of(entry), CFSTR("AWDL Metrics")) ||
            !equal_string(subgroup_of(entry), CFSTR("AWDL Snapshot")))
            return 3;
        CFStringRef name = name_of(entry);
        CFIndex match = 0;
        while (match < count && !equal_string(name, expected[match]))
            match++;
        unsigned format = format_of(entry);
        if (match == count || (seen & (1u << match)) || format != 1)
            return 3;
        seen |= 1u << match;
        char name_text[32];
        if (!CFStringGetCString(expected[match], name_text, sizeof(name_text), kCFStringEncodingUTF8))
            return 3;
        CFStringRef unit = unit_of(entry);
        char unit_text[24] = "";
        if (unit && !CFStringGetCString(unit, unit_text, sizeof(unit_text), kCFStringEncodingUTF8))
            unit_text[0] = '\0';
        const char *driver = equal_string(driver_of(entry), CFSTR("AppleBCMWLANCore"))
            ? "AppleBCMWLANCore" : "[provider from IORegistry]";
        printf("channel=%s format=%u unit=%s provider=%s\n",
               name_text, format, unit_text[0] ? unit_text : "[none]", driver);
    }
    return 0;
}

static bool wait_ms(long delay_ms)
{
    struct timespec remaining = {delay_ms / 1000, (delay_ms % 1000) * 1000000};
    while (!stop_requested) {
        if (nanosleep(&remaining, &remaining) == 0)
            return !stop_requested;
        if (errno != EINTR)
            return false;
    }
    return false;
}

static int read_snapshots(void *lib, CFMutableDictionaryRef channels, long delay_ms)
{
    SubscribeFn subscribe = (SubscribeFn)dlsym(lib, "IOReportCreateSubscription");
    SampleFn sample = (SampleFn)dlsym(lib, "IOReportCreateSamples");
    IterateFn iterate = (IterateFn)dlsym(lib, "IOReportIterate");
    StringAccessorFn group_of = (StringAccessorFn)dlsym(lib, "IOReportChannelGetGroup");
    StringAccessorFn subgroup_of = (StringAccessorFn)dlsym(lib, "IOReportChannelGetSubGroup");
    StringAccessorFn name_of = (StringAccessorFn)dlsym(lib, "IOReportChannelGetChannelName");
    FormatFn format_of = (FormatFn)dlsym(lib, "IOReportChannelGetFormat");
    IntegerValueFn value_of = (IntegerValueFn)dlsym(lib, "IOReportSimpleGetIntegerValue");

    if (!subscribe || !sample || !iterate || !group_of || !subgroup_of ||
        !name_of || !format_of || !value_of) {
        fputs("IOReport sampling symbols unavailable\n", stderr);
        return 2;
    }

    struct sigaction action = {0};
    action.sa_handler = request_stop;
    sigemptyset(&action.sa_mask);
    if (sigaction(SIGINT, &action, NULL) || sigaction(SIGTERM, &action, NULL)) {
        perror("sigaction");
        return 3;
    }

    int status = 3;
    CFMutableDictionaryRef subscribed = NULL;
    CFTypeRef subscription = subscribe(NULL, channels, &subscribed, 0, NULL);
    printf("subscription=%s subscribed_channels=%s\n",
           subscription ? "present" : "NULL", subscribed ? "present" : "NULL");
    if (!subscription || !subscribed) {
        fputs("IOReport did not return a complete subscription\n", stderr);
        goto done;
    }
    if (delay_ms == 0) {
        status = 0;
        goto done;
    }

    for (int index = 0; index < 2; index++) {
        if (stop_requested)
            goto done;
        CFDictionaryRef snapshot = sample(subscription, subscribed, NULL);
        if (!snapshot) {
            fputs("IOReport snapshot unavailable\n", stderr);
            goto done;
        }
        __block unsigned duration_count = 0;
        __block unsigned use_count = 0;
        __block int64_t duration = 0;
        __block int64_t use = 0;
        iterate(snapshot, ^int(CFDictionaryRef entry) {
            const int simple_integer_format = 1;
            if (equal_string(group_of(entry), CFSTR("AWDL Metrics")) &&
                equal_string(subgroup_of(entry), CFSTR("AWDL Snapshot")) &&
                format_of(entry) == simple_integer_format) {
                CFStringRef name = name_of(entry);
                if (equal_string(name, CFSTR("AWDLDuration(s)"))) {
                    duration = value_of(entry, 0);
                    duration_count++;
                } else if (equal_string(name, CFSTR("AWDLUse(%)"))) {
                    use = value_of(entry, 0);
                    use_count++;
                }
            }
            return 0;  // kIOReportIterOk
        });
        CFRelease(snapshot);

        if (duration_count != 1 || use_count != 1) {
            fputs("Expected one AWDL duration and one use sample\n", stderr);
            goto done;
        }
        printf("snapshot=%d AWDLDuration(s)=%" PRId64 " AWDLUse(%%)=%" PRId64 "\n",
               index + 1, duration, use);
        if (index == 0 && !wait_ms(delay_ms)) {
            if (!stop_requested)
                perror("nanosleep");
            goto done;
        }
    }
    status = 0;

done:
    if (subscription)
        CFRelease(subscription);
    if (subscribed)
        CFRelease(subscribed);
    return stop_requested ? 128 + stop_requested : status;
}

int main(int argc, char **argv)
{
    long delay_ms = 0;
    bool subscribe = argc == 2 && strcmp(argv[1], "--check-subscription") == 0;
    if (argc == 3 && strcmp(argv[1], "--sample-ms") == 0) {
        char *end = NULL;
        errno = 0;
        delay_ms = strtol(argv[2], &end, 10);
        if (errno || *end || delay_ms < 100 || delay_ms > 15000) {
            fputs("Sampling interval must be 100 through 15000 milliseconds\n", stderr);
            return 1;
        }
        subscribe = true;
    } else if (argc != 1 && !subscribe) {
        fprintf(stderr, "Usage: %s [--check-subscription | --sample-ms 100..15000]\n", argv[0]);
        return 1;
    }

    void *lib = dlopen("/usr/lib/libIOReport.dylib", RTLD_LOCAL | RTLD_NOW);
    if (!lib) {
        fprintf(stderr, "IOReport unavailable: %s\n", dlerror());
        return 2;
    }

    int status = 2;
    CopyChannelsFn copy_channels = (CopyChannelsFn)dlsym(lib, "IOReportCopyChannelsInGroup");
    ChannelCountFn count_channels = (ChannelCountFn)dlsym(lib, "IOReportGetChannelCount");
    if (!copy_channels || !count_channels) {
        fputs("IOReport channel discovery symbols unavailable\n", stderr);
        goto done;
    }

    CFMutableDictionaryRef channels =
        copy_channels(CFSTR("AWDL Metrics"), CFSTR("AWDL Snapshot"), 0, 0, 0);
    if (!channels) {
        fputs("AWDL Snapshot channel dictionary unavailable\n", stderr);
        goto done;
    }

    int count = count_channels(channels);
    printf("AWDL Metrics / AWDL Snapshot: %d matching channels\n", count);
    status = describe_channels(lib, channels);
    if (status == 0 && subscribe)
        status = read_snapshots(lib, channels, delay_ms);
    CFRelease(channels);

done:
    dlclose(lib);
    return status;
}
