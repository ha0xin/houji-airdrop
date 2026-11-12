// Native Apple peer-to-peer TCP baseline, using the Owly hashprobe wire format.
#include <CommonCrypto/CommonDigest.h>
#include <Network/Network.h>
#include <TargetConditionals.h>
#include <arpa/inet.h>
#include <dispatch/dispatch.h>
#include <errno.h>
#include <inttypes.h>
#include <net/if.h>
#include <stdbool.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <time.h>
#include <unistd.h>

#if defined(AWDL_TEST_LOOPBACK)
static const char probe_interface[] = "lo0";
#else
static const char probe_interface[] = "awdl0";
static const char service_type[] = "_awdlhash._tcp";
#endif

enum { max_payload = 64 << 20, chunk_size = 64 << 10 };

static bool parse_u32(const char *text, uint32_t maximum, uint32_t *result)
{
    char *end = NULL;
    errno = 0;
    unsigned long value = strtoul(text, &end, 10);
    if (errno || end == text || *end || value > maximum)
        return false;
    *result = (uint32_t)value;
    return true;
}

static bool parse_address(const char *text, unsigned index, uint16_t port,
                          struct sockaddr_in6 *address)
{
    *address = (struct sockaddr_in6){
        .sin6_len = sizeof(*address), .sin6_family = AF_INET6,
        .sin6_port = htons(port), .sin6_scope_id = index,
    };
    if (inet_pton(AF_INET6, text, &address->sin6_addr) != 1)
        return false;
#if defined(AWDL_TEST_LOOPBACK)
    return IN6_IS_ADDR_LOOPBACK(&address->sin6_addr);
#else
    return IN6_IS_ADDR_LINKLOCAL(&address->sin6_addr);
#endif
}

static bool endpoint_matches(nw_endpoint_t endpoint, const struct sockaddr_in6 *expected)
{
    const struct sockaddr *raw = endpoint ? nw_endpoint_get_address(endpoint) : NULL;
    if (!raw || raw->sa_family != AF_INET6 || raw->sa_len < sizeof(*expected))
        return false;
    const struct sockaddr_in6 *address = (const struct sockaddr_in6 *)raw;
    return (expected->sin6_port == 0 || address->sin6_port == expected->sin6_port) &&
           (address->sin6_scope_id == 0 || address->sin6_scope_id == expected->sin6_scope_id) &&
           memcmp(&address->sin6_addr, &expected->sin6_addr, sizeof(address->sin6_addr)) == 0;
}

static bool path_matches(nw_path_t path, const struct sockaddr_in6 *local,
                         const struct sockaddr_in6 *peer)
{
    if (!path || nw_path_get_status(path) != nw_path_status_satisfied)
        return false;
    __block unsigned interface_count = 0;
    __block bool interface_matches = false;
    nw_path_enumerate_interfaces(path, ^bool(nw_interface_t interface) {
        interface_count++;
        const char *name = nw_interface_get_name(interface);
        if (name && strcmp(name, probe_interface) == 0 &&
            nw_interface_get_index(interface) == local->sin6_scope_id)
            interface_matches = true;
        return true;
    });
    return interface_count == 1 && interface_matches &&
           endpoint_matches(nw_path_copy_effective_local_endpoint(path), local) &&
           endpoint_matches(nw_path_copy_effective_remote_endpoint(path), peer);
}

static void print_error(const char *operation, nw_error_t error)
{
    if (error)
        fprintf(stderr, "%s failed: domain=%d code=%d\n",
                operation, nw_error_get_error_domain(error), nw_error_get_error_code(error));
    else
        fprintf(stderr, "%s failed\n", operation);
}

static bool wait_seconds(dispatch_semaphore_t semaphore, int seconds)
{
    return dispatch_semaphore_wait(semaphore, dispatch_time(DISPATCH_TIME_NOW,
                                                            seconds * NSEC_PER_SEC)) == 0;
}

static void print_tcp_report(nw_data_transfer_report_t report, dispatch_queue_t queue,
                             unsigned interface_index, bool sent_payload, uint32_t payload_size)
{
    if (!report) {
        puts("TCP_REPORT status=unavailable");
        return;
    }
    __block struct {
        uint64_t duration_ms, sent_app, received_app, sent_transport, received_transport;
        uint64_t retransmitted, duplicate, out_of_order, smoothed_rtt_ms, min_rtt_ms;
    } counts = {0};
    __block const char *status = "unavailable";
    dispatch_semaphore_t ready = dispatch_semaphore_create(0);
    nw_data_transfer_report_collect(report, queue, ^(nw_data_transfer_report_t collected) {
        if (collected && nw_data_transfer_report_get_state(collected) == nw_data_transfer_report_state_collected) {
            uint32_t path_count = nw_data_transfer_report_get_path_count(collected);
            nw_interface_t interface = path_count == 1
                ? nw_data_transfer_report_copy_path_interface(collected, 0) : NULL;
            const char *name = interface ? nw_interface_get_name(interface) : NULL;
            if (!name || strcmp(name, probe_interface) != 0 || nw_interface_get_index(interface) != interface_index) {
                status = "unexpected_path";
            } else {
                counts.duration_ms = nw_data_transfer_report_get_duration_milliseconds(collected);
                counts.sent_app = nw_data_transfer_report_get_sent_application_byte_count(collected, 0);
                counts.received_app = nw_data_transfer_report_get_received_application_byte_count(collected, 0);
                counts.sent_transport = nw_data_transfer_report_get_sent_transport_byte_count(collected, 0);
                counts.received_transport = nw_data_transfer_report_get_received_transport_byte_count(collected, 0);
                counts.retransmitted = nw_data_transfer_report_get_sent_transport_retransmitted_byte_count(collected, 0);
                counts.duplicate = nw_data_transfer_report_get_received_transport_duplicate_byte_count(collected, 0);
                counts.out_of_order = nw_data_transfer_report_get_received_transport_out_of_order_byte_count(collected, 0);
                counts.smoothed_rtt_ms = nw_data_transfer_report_get_transport_smoothed_rtt_milliseconds(collected, 0);
                counts.min_rtt_ms = nw_data_transfer_report_get_transport_minimum_rtt_milliseconds(collected, 0);
                status = (sent_payload ? counts.sent_app : counts.received_app) >= payload_size
                    ? "collected" : "incomplete_counters";
            }
        }
        dispatch_semaphore_signal(ready);
    });
    if (!wait_seconds(ready, 1)) {
        puts("TCP_REPORT status=timed_out");
    } else if (strcmp(status, "collected") != 0) {
        printf("TCP_REPORT status=%s\n", status);
    } else {
        printf("TCP_REPORT status=collected path=%s duration_ms=%" PRIu64
               " sent_app_bytes=%" PRIu64 " received_app_bytes=%" PRIu64
               " sent_tcp_bytes=%" PRIu64 " received_tcp_bytes=%" PRIu64
               " retransmitted_tcp_bytes=%" PRIu64 " duplicate_tcp_bytes=%" PRIu64
               " out_of_order_tcp_bytes=%" PRIu64 " smoothed_rtt_ms=%" PRIu64
               " min_rtt_ms=%" PRIu64 "\n",
               probe_interface, counts.duration_ms, counts.sent_app, counts.received_app,
               counts.sent_transport, counts.received_transport, counts.retransmitted,
               counts.duplicate, counts.out_of_order, counts.smoothed_rtt_ms, counts.min_rtt_ms);
    }
}

#if !defined(AWDL_TEST_LOOPBACK)
static nw_interface_t find_awdl_service(const char *service, unsigned index, dispatch_queue_t queue)
{
    nw_browse_descriptor_t descriptor = nw_browse_descriptor_create_bonjour_service(service_type, "local.");
    nw_parameters_t parameters = nw_parameters_create();
    nw_parameters_set_include_peer_to_peer(parameters, true);
    nw_browser_t browser = nw_browser_create(descriptor, parameters);
    if (!browser)
        return NULL;
    dispatch_semaphore_t done = dispatch_semaphore_create(0);
    dispatch_semaphore_t stopped = dispatch_semaphore_create(0);
    __block nw_interface_t found = NULL;
    __block bool finished = false;
    nw_browser_set_queue(browser, queue);
    nw_browser_set_state_changed_handler(browser, ^(nw_browser_state_t state, nw_error_t error) {
        if (state == nw_browser_state_cancelled)
            dispatch_semaphore_signal(stopped);
        if (!finished && (state == nw_browser_state_waiting || state == nw_browser_state_failed ||
                          state == nw_browser_state_cancelled)) {
            print_error("browse", error);
            finished = true;
            dispatch_semaphore_signal(done);
        }
    });
    nw_browser_set_browse_results_changed_handler(browser,
        ^(nw_browse_result_t old_result, nw_browse_result_t new_result, bool complete) {
            (void)old_result;
            (void)complete;
            if (finished || !new_result)
                return;
            nw_endpoint_t endpoint = nw_browse_result_copy_endpoint(new_result);
            if (!endpoint || nw_endpoint_get_type(endpoint) != nw_endpoint_type_bonjour_service)
                return;
            const char *name = nw_endpoint_get_bonjour_service_name(endpoint);
            if (!name || strcmp(name, service) != 0)
                return;
            nw_browse_result_enumerate_interfaces(new_result, ^bool(nw_interface_t interface) {
                const char *ifname = nw_interface_get_name(interface);
                if (!ifname || strcmp(ifname, probe_interface) != 0 ||
                    nw_interface_get_index(interface) != index)
                    return true;
                found = interface;
                return false;
            });
            if (found) {
                finished = true;
                dispatch_semaphore_signal(done);
            }
        });
    nw_browser_start(browser);
    bool received = wait_seconds(done, 8);
    nw_browser_cancel(browser);
    bool cancelled = wait_seconds(stopped, 1);
    if (!received)
        fputs("AWDL Bonjour result timed out\n", stderr);
    return received && cancelled ? found : NULL;
}
#endif

static bool wait_ready(nw_connection_t connection, dispatch_queue_t queue)
{
    dispatch_semaphore_t done = dispatch_semaphore_create(0);
    __block bool finished = false;
    __block bool ready = false;
    nw_connection_set_queue(connection, queue);
    nw_connection_set_state_changed_handler(connection, ^(nw_connection_state_t state, nw_error_t error) {
        if (finished)
            return;
        if (state == nw_connection_state_ready) {
            ready = true;
        } else if (state == nw_connection_state_waiting || state == nw_connection_state_failed ||
                   state == nw_connection_state_cancelled) {
            print_error("connection", error);
        } else {
            return;
        }
        finished = true;
        dispatch_semaphore_signal(done);
    });
    nw_connection_start(connection);
    return wait_seconds(done, 8) && ready;
}

static nw_connection_t accept_once(nw_listener_t listener, dispatch_queue_t queue,
                                    const struct sockaddr_in6 *peer)
{
    struct sockaddr_in6 expected_peer = *peer;
    dispatch_semaphore_t done = dispatch_semaphore_create(0);
    dispatch_semaphore_t stopped = dispatch_semaphore_create(0);
    __block bool finished = false;
    __block nw_connection_t accepted = NULL;
    nw_listener_set_queue(listener, queue);
    nw_listener_set_new_connection_handler(listener, ^(nw_connection_t candidate) {
        if (finished || !endpoint_matches(nw_connection_copy_endpoint(candidate), &expected_peer)) {
            nw_connection_cancel(candidate);
            return;
        }
        accepted = candidate;
        finished = true;
        dispatch_semaphore_signal(done);
    });
    nw_listener_set_state_changed_handler(listener, ^(nw_listener_state_t state, nw_error_t error) {
        if (state == nw_listener_state_cancelled) {
            puts("LISTENER_CANCELLED");
            dispatch_semaphore_signal(stopped);
        }
        if (state == nw_listener_state_ready) {
            puts("LISTEN_READY");
            fflush(stdout);
        } else if (!finished && (state == nw_listener_state_waiting ||
                   state == nw_listener_state_failed || state == nw_listener_state_cancelled)) {
            print_error("listener", error);
            finished = true;
            dispatch_semaphore_signal(done);
        }
    });
    nw_listener_start(listener);
    bool received = wait_seconds(done, 16);
    nw_listener_cancel(listener);
    bool cancelled = wait_seconds(stopped, 1);
    if (!received)
        fputs("AWDL listener accept timed out\n", stderr);
    if (received && !cancelled && accepted)
        nw_connection_cancel(accepted);
    return received && cancelled ? accepted : NULL;
}

static bool send_bytes(nw_connection_t conn, dispatch_queue_t queue, const void *bytes, size_t length)
{
    dispatch_semaphore_t done = dispatch_semaphore_create(0);
    dispatch_data_t data = dispatch_data_create(bytes, length, queue, DISPATCH_DATA_DESTRUCTOR_DEFAULT);
    if (!data)
        return false;
    __block bool success = false;
    nw_connection_send(conn, data, NW_CONNECTION_DEFAULT_MESSAGE_CONTEXT, true, ^(nw_error_t error) {
        success = error == NULL;
        if (error)
            print_error("send", error);
        dispatch_semaphore_signal(done);
    });
    dispatch_semaphore_wait(done, DISPATCH_TIME_FOREVER);
    return success;
}

static bool receive_bytes(nw_connection_t conn, uint8_t *output, CC_SHA256_CTX *hasher, uint32_t length)
{
    while (length != 0) {
        uint32_t requested = length < chunk_size ? length : chunk_size;
        dispatch_semaphore_t done = dispatch_semaphore_create(0);
        __block size_t received = 0;
        nw_connection_receive(conn, requested, requested,
            ^(dispatch_data_t content, nw_content_context_t context, bool complete, nw_error_t error) {
                (void)context;
                (void)complete;
                if (error) {
                    print_error("receive", error);
                } else if (content) {
                    dispatch_data_apply(content, ^bool(dispatch_data_t region, size_t offset,
                                                      const void *data, size_t size) {
                        (void)region;
                        if (size > requested || offset > requested - size)
                            return false;
                        if (output)
                            memcpy(output + offset, data, size);
                        if (hasher)
                            CC_SHA256_Update(hasher, data, (CC_LONG)size);
                        received += size;
                        return true;
                    });
                }
                dispatch_semaphore_signal(done);
            });
        dispatch_semaphore_wait(done, DISPATCH_TIME_FOREVER);
        if (received == 0 || received > requested)
            return false;
        length -= (uint32_t)received;
        if (output)
            output += received;
    }
    return true;
}

static uint64_t clock_ns(clockid_t clock)
{
    struct timespec current;
    if (clock_gettime(clock, &current) != 0)
        abort();
    return (uint64_t)current.tv_sec * 1000000000 + (uint64_t)current.tv_nsec;
}

struct transfer_clock {
    uint64_t start_mono, end_mono, start_wall, end_wall;
};

static bool transfer(nw_connection_t conn, dispatch_queue_t queue, bool client, uint32_t mode,
                     uint32_t size, uint32_t sample, const uint8_t *payload,
                     const uint8_t expected[CC_SHA256_DIGEST_LENGTH], struct transfer_clock *clock)
{
    const uint32_t header[] = {htonl(mode), htonl(size), htonl(sample)};
    uint32_t request[3];
    if (client) {
        clock->start_wall = clock_ns(CLOCK_REALTIME);
        clock->start_mono = clock_ns(CLOCK_MONOTONIC);
        if (!send_bytes(conn, queue, header, sizeof(header)))
            return false;
    } else {
        if (!receive_bytes(conn, (uint8_t *)request, NULL, sizeof(request)) ||
            memcmp(request, header, sizeof(header)) != 0)
            return false;
        clock->start_wall = clock_ns(CLOCK_REALTIME);
        clock->start_mono = clock_ns(CLOCK_MONOTONIC);
    }

    uint8_t actual[CC_SHA256_DIGEST_LENGTH];
    if (client == (mode == 0)) {
        if (!send_bytes(conn, queue, payload, size) ||
            !receive_bytes(conn, actual, NULL, sizeof(actual)) ||
            memcmp(actual, expected, sizeof(actual)) != 0)
            return false;
    } else {
        CC_SHA256_CTX hasher;
        CC_SHA256_Init(&hasher);
        if (!receive_bytes(conn, NULL, &hasher, size))
            return false;
        CC_SHA256_Final(actual, &hasher);
        if (memcmp(actual, expected, sizeof(actual)) != 0 ||
            !send_bytes(conn, queue, actual, sizeof(actual)))
            return false;
    }
    clock->end_mono = clock_ns(CLOCK_MONOTONIC);
    clock->end_wall = clock_ns(CLOCK_REALTIME);
    return true;
}

int main(int argc, char **argv)
{
    @autoreleasepool {
        if (argc < 9 || argc > 11 || (strcmp(argv[1], "server") != 0 && strcmp(argv[1], "client") != 0)) {
            fputs("Usage: awdl-hashprobe client|server local-ip peer-ip mode bytes sample "
                  "service-token port [--report] [--existing-awdl|--discover-only]\n", stderr);
            return 2;
        }
        bool client = strcmp(argv[1], "client") == 0;
        bool want_report = false;
        bool existing_awdl = false;
        bool discover_only = false;
        for (int option = 9; option < argc; option++) {
            if (!want_report && strcmp(argv[option], "--report") == 0) {
                want_report = true;
#if TARGET_OS_OSX
            } else if (client && !existing_awdl && strcmp(argv[option], "--existing-awdl") == 0) {
                existing_awdl = true;
            } else if (client && !discover_only && strcmp(argv[option], "--discover-only") == 0) {
                discover_only = true;
#endif
            } else {
                fputs("Invalid probe option\n", stderr);
                return 2;
            }
        }
        if (discover_only && (existing_awdl || want_report))
            return 2;
        bool service_valid = existing_awdl ? strcmp(argv[7], "-") == 0 :
            strlen(argv[7]) == 24 && strspn(argv[7], "0123456789abcdef") == 24;
        unsigned index = if_nametoindex(probe_interface);
        struct sockaddr_in6 local;
        struct sockaddr_in6 peer;
        uint32_t mode, size, sample, port;
        if (!index || !parse_u32(argv[8], UINT16_MAX, &port) || port < 1024 ||
            !parse_address(argv[2], index, client ? 0 : (uint16_t)port, &local) ||
            !parse_address(argv[3], index, client ? (uint16_t)port : 0, &peer) ||
            !parse_u32(argv[4], 1, &mode) || !parse_u32(argv[5], max_payload, &size) || size == 0 ||
            !parse_u32(argv[6], UINT32_MAX, &sample) || sample == 0 || !service_valid) {
            fputs("Invalid scoped address or probe parameters\n", stderr);
            return 2;
        }

#if TARGET_OS_OSX && !defined(AWDL_TEST_LOOPBACK)
        if (discover_only) {
            alarm(10);
            dispatch_queue_t discovery = dispatch_queue_create("awdl-browse", DISPATCH_QUEUE_SERIAL);
            if (!find_awdl_service(argv[7], index, discovery))
                return 1;
            printf("AWDL_DISCOVERED ifindex=%u\n", index);
            return 0;
        }
#endif
        uint8_t *payload = malloc(size);
        if (!payload)
            return 2;
        for (uint32_t offset = 0; offset < size; offset++)
            payload[offset] = (uint8_t)offset;
        uint8_t digest[CC_SHA256_DIGEST_LENGTH];
        CC_SHA256(payload, size, digest);

        // Backup bound if a Network.framework callback does not return.
        unsigned timeout_seconds = size > (16u << 20) ? 65 : 25;
        alarm(timeout_seconds);
        dispatch_time_t cancel_deadline = dispatch_time(DISPATCH_TIME_NOW,
                                                         (timeout_seconds - 3) * NSEC_PER_SEC);
        dispatch_queue_t queue = dispatch_queue_create("awdl-hashprobe", DISPATCH_QUEUE_SERIAL);
        nw_parameters_t parameters = nw_parameters_create_secure_tcp(
            NW_PARAMETERS_DISABLE_PROTOCOL, NW_PARAMETERS_DEFAULT_CONFIGURATION);
        nw_parameters_set_local_endpoint(parameters, nw_endpoint_create_address((const struct sockaddr *)&local));
        nw_connection_t connection = NULL;
        if (client) {
#if !defined(AWDL_TEST_LOOPBACK)
            if (!existing_awdl) {
                nw_interface_t interface = find_awdl_service(argv[7], index, queue);
                if (!interface) {
                    free(payload);
                    return 1;
                }
                nw_parameters_require_interface(parameters, interface);
            }
#endif
            connection = nw_connection_create(nw_endpoint_create_address((const struct sockaddr *)&peer), parameters);
        } else {
#if !defined(AWDL_TEST_LOOPBACK)
            nw_parameters_set_include_peer_to_peer(parameters, true);
#endif
            nw_listener_t listener = nw_listener_create(parameters);
            if (listener) {
#if !defined(AWDL_TEST_LOOPBACK)
                nw_advertise_descriptor_t advertisement =
                    nw_advertise_descriptor_create_bonjour_service(argv[7], service_type, "local.");
                nw_advertise_descriptor_set_no_auto_rename(advertisement, true);
                nw_listener_set_advertise_descriptor(listener, advertisement);
#endif
                connection = accept_once(listener, queue, &peer);
            }
        }

        int status = 1;
        if (!connection || !wait_ready(connection, queue))
            goto done;
        {
            __block bool path_rejected = false;
            __weak nw_connection_t watched = connection;
            __block bool final_path_valid = false;
            dispatch_after(cancel_deadline, queue, ^{
                nw_connection_t current = watched;
                if (current)
                    nw_connection_cancel(current);
            });
            nw_connection_set_path_changed_handler(connection, ^(nw_path_t path) {
                if (!path_matches(path, &local, &peer)) {
                    path_rejected = true;
                    nw_connection_t current = watched;
                    if (current)
                        nw_connection_cancel(current);
                }
            });
            if (!path_matches(nw_connection_copy_current_path(connection), &local, &peer)) {
                fputs("PATH_REJECT before payload\n", stderr);
                goto done;
            }
            nw_data_transfer_report_t report = want_report
                ? nw_connection_create_new_data_transfer_report(connection) : NULL;
            struct transfer_clock clock;
            if (!transfer(connection, queue, client, mode, size, sample, payload, digest, &clock)) {
                fputs("TRANSFER_REJECT\n", stderr);
                if (want_report)
                    puts("TCP_REPORT status=transfer_failed");
                goto done;
            }
            dispatch_sync(queue, ^{
                final_path_valid = !path_rejected &&
                    path_matches(nw_connection_copy_current_path(connection), &local, &peer);
            });
            if (!final_path_valid) {
                fputs("PATH_REJECT after payload\n", stderr);
                goto done;
            }
            double elapsed = (clock.end_mono - clock.start_mono) / 1e9;
            printf("HASH_OK sample=%" PRIu32 " mode=%" PRIu32 " bytes=%" PRIu32
                   " seconds=%.6f MBps=%.6f path=%s sha256=",
                   sample, mode, size, elapsed, size / elapsed / 1e6, probe_interface);
            for (size_t offset = 0; offset < sizeof(digest); offset++)
                printf("%02x", digest[offset]);
            putchar('\n');
            printf("HASH_CLOCK sample=%" PRIu32 " mode=%" PRIu32
                   " start_mono_ns=%" PRIu64 " end_mono_ns=%" PRIu64
                   " start_wall_ns=%" PRIu64 " end_wall_ns=%" PRIu64 "\n",
                   sample, mode, clock.start_mono, clock.end_mono, clock.start_wall, clock.end_wall);
            if (want_report)
                print_tcp_report(report, queue, index, client == (mode == 0), size);
            status = 0;
        }

    done:
        if (connection) {
            nw_connection_set_path_changed_handler(connection, NULL);
            nw_connection_cancel(connection);
            dispatch_sync(queue, ^{});
        }
        free(payload);
        return status;
    }
}
