#!/usr/bin/env python3
"""Read the public Owly radiotap fixtures; emit only role-labeled AWDL capability fields."""

import argparse
from collections import Counter
import hashlib
import json
from pathlib import Path
import struct
import tomllib


def frames(data, counts):
    if len(data) < 24 or data[:4] != b"\xd4\xc3\xb2\xa1" or struct.unpack_from("<I", data, 20)[0] != 127:
        raise ValueError("Expected the corpus's little-endian radiotap pcap")
    pos = 24
    while pos < len(data):
        if len(data) - pos < 16:
            raise ValueError("Truncated pcap record header")
        captured, original = struct.unpack_from("<II", data, pos + 8)
        pos += 16
        if captured > original or captured > len(data) - pos:
            raise ValueError("Invalid pcap record length")
        packet = data[pos:pos + captured]
        pos += captured
        counts["records"] += 1
        complete = captured == original
        if not complete:
            counts["truncated_records"] += 1
        if len(packet) < 8 or packet[0] != 0:
            counts["unparseable_radiotap"] += 1
            continue
        radiotap_len, present = struct.unpack_from("<HI", packet, 2)
        fields = 8
        extension = present
        while extension & (1 << 31):
            if fields + 4 > radiotap_len or fields + 4 > len(packet):
                raise ValueError("Truncated radiotap present words")
            extension = struct.unpack_from("<I", packet, fields)[0]
            fields += 4
        if not fields <= radiotap_len <= len(packet):
            raise ValueError("Invalid radiotap length")
        flags = 0
        if present & 2:
            if present & 1:
                fields = ((fields + 7) & ~7) + 8
            if fields >= radiotap_len:
                raise ValueError("Truncated radiotap flags")
            flags = packet[fields]
        if flags & 0x40:
            counts["bad_fcs_records"] += 1
            continue
        tail = 4 if complete and flags & 0x10 else 0
        yield packet[radiotap_len:len(packet) - tail], complete


def elements(payload, wide):
    width = 3 if wide else 2
    while len(payload) >= width:
        size = int.from_bytes(payload[1:width], "little")
        if len(payload) < width + size:
            raise ValueError("Truncated element")
        yield payload[0], payload[width:width + size]
        payload = payload[width + size:]
    if payload and (not wide or any(payload)):
        raise ValueError("Unexpected trailing bytes")


def mcs_map(value):
    return [("0-7", "0-8", "0-9", None)[(value >> (2 * i)) & 3] for i in range(8)]


def vht(value):
    if len(value) != 12:
        raise ValueError("VHT IE body must be 12 bytes")
    cap, rx, rx_rate, tx, tx_rate = struct.unpack("<I4H", value)
    return dict(cap_hex=f"{cap:08x}", max_mpdu=(3895, 7991, 11454, None)[cap & 3],
                width_set=(cap >> 2) & 3, sgi80=bool(cap & (1 << 5)),
                max_ampdu_exponent=(cap >> 23) & 7,
                rx_mcs=mcs_map(rx), tx_mcs=mcs_map(tx),
                rx_highest_rate_raw=rx_rate, tx_highest_rate_raw=tx_rate)


def ht(value):
    if len(value) < 6:
        raise ValueError("Truncated HT capabilities")
    cap = int.from_bytes(value[2:4], "little")
    mask = value[5:-2] if len(value) >= 8 else value[5:]
    return dict(cap_hex=f"{cap:04x}", width_20_40=bool(cap & 2),
                max_amsdu=7935 if cap & 0x800 else 3839,
                ampdu_params=f"{value[4]:02x}", rx_mcs_mask=mask.hex(), raw=value.hex())


def ba_parameters(body):
    action = body[1]
    if action == 0 and len(body) >= 9:
        (parameter,) = struct.unpack_from("<H", body, 3)
        status = None
    elif action == 1 and len(body) >= 9:
        status, parameter = struct.unpack_from("<HH", body, 3)
    elif action == 2 and len(body) >= 6:
        parameter, status = struct.unpack_from("<HH", body, 2)
        return (action, None, (parameter >> 12) & 15, status, None, None, None)
    else:
        return None
    return (action, body[2], (parameter >> 2) & 15, status, (parameter >> 6) & 1023,
            bool(parameter & 1), bool(parameter & 2))


def summary(path):
    data = path.read_bytes()
    manifest_bytes = path.with_suffix(".toml").read_bytes()
    manifest = tomllib.loads(manifest_bytes.decode())
    if len(manifest["peers"]) != 1:
        raise ValueError("Expected one peer")
    role = {bytes.fromhex(address.replace(":", "")): name for name, address in
            (("linux_local", manifest["local"]), ("ios_peer", manifest["peers"][0]))}
    if len(role) != 2 or not all(len(address) == 6 and address[:5] == b"\x02\0\0\0\0"
                                 for address in role):
        raise ValueError("Expected distinct corpus stand-ins")
    hist = {r: Counter() for r in role.values()}
    actions = Counter()
    incomplete = Counter()
    counts = Counter(records=0, truncated_records=0, bad_fcs_records=0,
                     unparseable_radiotap=0, incomplete_ba_actions=0)
    for frame, complete in frames(data, counts):
        if len(frame) < 26 or frame[0] != 0xd0 or frame[10:16] not in role:
            continue
        sender = role[frame[10:16]]
        body = frame[24:]
        if body[0] == 3 and body[1] <= 2 and complete:
            receiver = role.get(frame[4:10], "other")
            parameters = ba_parameters(body)
            if parameters is None:
                counts["incomplete_ba_actions"] += 1
            else:
                actions[(sender, receiver, *parameters)] += 1
        if len(body) < 16 or body[:6] != bytes.fromhex("7f0017f20810") or body[6] not in (0, 3):
            continue
        if not complete:
            incomplete[(sender, body[6])] += 1
            continue
        try:
            tlvs = [(kind, value) for kind, value in elements(body[16:], True) if kind != 0 or value]
            containers = []
            for kind, value in tlvs:
                if kind == 17:
                    ies = list(elements(value, False))
                    containers.append(dict(ie_ids=[i for i, _ in ies],
                                           vht=[vht(v) for i, v in ies if i == 191]))
            item = dict(subtype=body[6], order=[i for i, _ in tlvs],
                        ht=[ht(v) for i, v in tlvs if i == 7],
                        channel_sequence=[v.hex() for i, v in tlvs if i == 18],
                        data_path_flags=[f"{int.from_bytes(v[:2], 'little'):04x}" for i, v in tlvs if i == 12],
                        data_path_lengths=[len(v) for i, v in tlvs if i == 12],
                        vht_containers=containers)
            hist[sender][json.dumps(item, sort_keys=True)] += 1
        except ValueError:
            incomplete[(sender, body[6])] += 1
    ba_fields = ("sender", "receiver", "action", "dialog_token", "tid", "status_or_reason", "window", "amsdu", "immediate")
    return dict(file=path.name, sha256=hashlib.sha256(data).hexdigest(),
                manifest_sha256=hashlib.sha256(manifest_bytes).hexdigest(),
                packet_counts=dict(counts),
                roles={r: [dict(count=n, **json.loads(key)) for key, n in h.most_common()]
                       for r, h in hist.items()},
                incomplete=[dict(role=r, subtype=t, count=n) for (r, t), n in sorted(incomplete.items())],
                ba_actions=[dict(zip(ba_fields, key), count=n) for key, n in actions.most_common()])


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("captures", nargs="+", type=Path)
    parser.add_argument("--source-commit", required=True)
    args = parser.parse_args()
    print(json.dumps(dict(source_commit=args.source_commit,
                          captures=[summary(path) for path in args.captures]), indent=2))


if __name__ == "__main__":
    main()
