//! The peer table's entries: what each AWDL peer has said about itself.

use alloc::string::String;

use crate::channel::Sequence;
use crate::election::Election;
use crate::tlv::{Advert, HtCapabilities};
use crate::{MacAddr, Time};

/// An AWDL peer, as heard.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct Peer {
    pub mac: MacAddr,
    /// From its ARPA TLV.
    pub name: String,
    pub version: u8,
    pub devclass: u8,
    /// Of the last action frame heard from it, in dBm.
    pub rssi: Option<i8>,
    /// When it was last heard, by action or data frame.
    pub last_seen: Time,
    pub election: Election,
    pub sequence: Option<Sequence>,
    /// How far its AW schedule is ahead of ours, in µs.
    pub sync_offset_us: Option<i64>,
    pub ht: Option<HtCapabilities>,
    /// Its station address on its infrastructure network.
    pub infra: Option<MacAddr>,
    /// Whether it has sent a MIF.
    pub mif: bool,
    /// Whether it sends Election Parameters version 2, which then wins
    /// over version 1.
    pub v2: bool,
    /// It has sent a MIF and announced its version and device class: only
    /// then is it a peer to talk to (AWDL-17).
    pub valid: bool,
    rx_seq_win: [Option<(u16, u128)>; 17],
    pub(crate) tx_qos_seq: u16,
    pub(crate) tx_ba_active: bool,
    pub(crate) tx_ba_token: u8,
    pub(crate) tx_ba_last_req: Option<Time>,
    pub(crate) tx_ba_attempts: u8,
}

/// What a host is told about a peer that has turned valid.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct PeerInfo {
    pub mac: MacAddr,
    pub name: String,
    pub devclass: u8,
    pub rssi: Option<i8>,
}

impl Peer {
    pub fn new(mac: MacAddr, now: Time) -> Peer {
        Peer {
            mac,
            name: String::new(),
            version: 0,
            devclass: 0,
            rssi: None,
            last_seen: now,
            election: Election::own(mac),
            sequence: None,
            sync_offset_us: None,
            ht: None,
            infra: None,
            mif: false,
            v2: false,
            valid: false,
            rx_seq_win: [None; 17],
            tx_qos_seq: 0,
            tx_ba_active: false,
            tx_ba_token: 0,
            tx_ba_last_req: None,
            tx_ba_attempts: 0,
        }
    }

    /// Next contiguous 12-bit QoS TID 0 sequence number for outbound BlockAck sessions.
    pub(crate) fn next_tx_qos_seq(&mut self) -> u16 {
        let s = self.tx_qos_seq;
        self.tx_qos_seq = (s + 1) & 0x0fff;
        s
    }

    /// Sliding 128-sequence retry deduplicator per QoS TID (`0..15`) or non-QoS (`16`).
    pub(crate) fn check_rx_duplicate(&mut self, slot: usize, seq: u16, is_retry: bool) -> bool {
        let seq = seq & 0x0fff;
        let entry = &mut self.rx_seq_win[slot.min(16)];
        let Some((head, bits)) = entry else {
            *entry = Some((seq, 1));
            return false;
        };
        let ahead = seq.wrapping_sub(*head) & 0x0fff;
        if ahead == 0 {
            return is_retry;
        }
        if ahead < 2048 {
            *bits = if ahead >= 128 { 1 } else { (*bits << ahead) | 1 };
            *head = seq;
            return false;
        }
        let behind = head.wrapping_sub(seq) & 0x0fff;
        if behind < 128 {
            let mask = 1u128 << behind;
            if is_retry && (*bits & mask) != 0 {
                return true;
            }
            *bits |= mask;
        }
        false
    }

    pub fn info(&self) -> PeerInfo {
        PeerInfo {
            mac: self.mac,
            name: self.name.clone(),
            devclass: self.devclass,
            rssi: self.rssi,
        }
    }

    /// Takes in what one of its action frames said, except its sync
    /// parameters, which the node reads against its own clock. Returns
    /// whether it has just turned valid.
    pub(crate) fn absorb(&mut self, a: &Advert, mif: bool) -> bool {
        if let Some(name) = &a.name {
            self.name.clone_from(name);
        }
        if let Some((version, devclass)) = a.version {
            self.version = version;
            self.devclass = devclass;
        }
        if let Some(s) = a.sequence {
            self.sequence = Some(s);
        }
        if let Some(ht) = a.ht {
            self.ht = Some(ht);
        }
        if let Some(infra) = a.data_path.and_then(|d| d.infra) {
            self.infra = Some(infra);
        }
        if let Some(e) = a.election
            && (a.election_v2 || !self.v2)
        {
            let el = &mut self.election;
            el.master = e.master;
            el.height = e.height;
            el.master_metric = e.master_metric;
            el.self_metric = e.self_metric;
            if a.election_v2 {
                el.sync = e.sync.unwrap_or(self.mac);
                el.master_counter = e.master_counter;
                el.self_counter = e.self_counter;
                self.v2 = true;
            }
        }
        self.mif |= mif;
        let was = self.valid;
        self.valid = self.mif && self.version != 0 && self.devclass != 0;
        self.valid && !was
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn deduplicates_single_and_ampdu_burst_retries() {
        let mut p = Peer::new(MacAddr([2, 0, 0, 0, 0, 1]), Time(1));
        for seq in 4090..4098 {
            assert!(!p.check_rx_duplicate(0, seq, false));
        }
        // Full 8-MPDU burst retry across the 12-bit wraparound boundary:
        for seq in 4090..4098 {
            assert!(p.check_rx_duplicate(0, seq, true));
        }
        // Fresh non-retry frames and unseen retry sequence numbers still pass:
        assert!(!p.check_rx_duplicate(0, 4098, true));
        assert!(p.check_rx_duplicate(0, 4098, true));
        // Independent TID slot is unaffected:
        assert!(!p.check_rx_duplicate(1, 4090, true));
    }
}
