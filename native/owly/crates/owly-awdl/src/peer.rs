//! The peer table's entries: what each AWDL peer has said about itself.

use alloc::string::String;

use crate::channel::Sequence;
use crate::election::Election;
use crate::tlv::{Advert, HtCapabilities, VhtCapabilities};
use crate::{MacAddr, Time};

/// Normalized RF/MCS capability summary distilled from a peer's HT/VHT TLVs.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub struct PeerCaps {
    pub ht: bool,
    pub vht: bool,
    pub nss: u8,
    pub bw: u8,
    pub max_mcs: u8,
    /// Active operating-mode Short GI for the selected PHY/BW (never cross-inferred).
    pub sgi: bool,
    /// Active operating-mode LDPC for the selected PHY (never cross-inferred).
    pub ldpc: bool,
    pub ht_bw40: bool,
    pub ht_sgi20: bool,
    pub ht_sgi40: bool,
    pub ht_ldpc: bool,
    pub vht_sgi80: bool,
    pub vht_ldpc: bool,
    pub max_mpdu: u16,
    pub ampdu_exp: u8,
    pub mpdu_density: u8,
    pub ht_mcs_mask: u16,
}

impl Default for PeerCaps {
    fn default() -> Self {
        Self {
            ht: false,
            vht: false,
            nss: 1,
            bw: 20,
            max_mcs: 7,
            sgi: false,
            ldpc: false,
            ht_bw40: false,
            ht_sgi20: false,
            ht_sgi40: false,
            ht_ldpc: false,
            vht_sgi80: false,
            vht_ldpc: false,
            max_mpdu: 3895,
            ampdu_exp: 0,
            mpdu_density: 0,
            ht_mcs_mask: 0,
        }
    }
}

impl PeerCaps {
    pub fn from_ies(ht: Option<HtCapabilities>, vht: Option<VhtCapabilities>) -> Self {
        let ht_bw40 = ht.is_some_and(|h| h.bw40());
        let ht_sgi20 = ht.is_some_and(|h| h.sgi20());
        let ht_sgi40 = ht.is_some_and(|h| h.sgi40());
        let ht_ldpc = ht.is_some_and(|h| h.ldpc());
        let mpdu_density = ht.map_or(0, |h| h.mpdu_density());
        let ht_mcs_mask = ht.map_or(0, |h| (h.rx_mcs_mask() & 0xffff) as u16);

        if let Some(v) = vht {
            let (nss, max_mcs) = v.rx_streams_and_mcs();
            let vht_sgi80 = v.sgi80();
            let vht_ldpc = v.rx_ldpc();
            return Self {
                ht: true,
                vht: true,
                nss,
                bw: 80,
                max_mcs,
                sgi: vht_sgi80,
                ldpc: vht_ldpc,
                ht_bw40,
                ht_sgi20,
                ht_sgi40,
                ht_ldpc,
                vht_sgi80,
                vht_ldpc,
                max_mpdu: v.max_mpdu() as u16,
                ampdu_exp: v.max_ampdu_exp(),
                mpdu_density,
                ht_mcs_mask,
            };
        }
        if let Some(h) = ht {
            let bw = if ht_bw40 { 40 } else { 20 };
            let sgi = if ht_bw40 { ht_sgi40 } else { ht_sgi20 };
            return Self {
                ht: true,
                vht: false,
                nss: h.rx_nss(),
                bw,
                max_mcs: 7,
                sgi,
                ldpc: ht_ldpc,
                ht_bw40,
                ht_sgi20,
                ht_sgi40,
                ht_ldpc,
                vht_sgi80: false,
                vht_ldpc: false,
                max_mpdu: 3895,
                ampdu_exp: h.ampdu_exp(),
                mpdu_density,
                ht_mcs_mask,
            };
        }
        Self::default()
    }
}

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
    pub vht: Option<VhtCapabilities>,
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
    pub(crate) tx_ba_amsdu: Option<bool>,
    pub(crate) tx_ba_window: Option<u16>,
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
    pub caps: PeerCaps,
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
            vht: None,
            infra: None,
            mif: false,
            v2: false,
            valid: false,
            rx_seq_win: [None; 17],
            tx_qos_seq: 0,
            tx_ba_active: false,
            tx_ba_amsdu: None,
            tx_ba_window: None,
            tx_ba_token: 0,
            tx_ba_last_req: None,
            tx_ba_attempts: 0,
        }
    }

    /// Clears negotiated outbound Block Ack session parameters for TID 0.
    pub(crate) fn reset_tx_ba(&mut self) {
        self.tx_ba_active = false;
        self.tx_ba_amsdu = None;
        self.tx_ba_window = None;
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

    pub fn caps(&self) -> PeerCaps {
        PeerCaps::from_ies(self.ht, self.vht)
    }

    pub fn info(&self) -> PeerInfo {
        PeerInfo {
            mac: self.mac,
            name: self.name.clone(),
            devclass: self.devclass,
            rssi: self.rssi,
            caps: self.caps(),
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
        if let Some(vht) = a.vht {
            self.vht = Some(vht);
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

    #[test]
    fn review_vht_does_not_invent_sgi80_or_vht_ldpc_from_ht() {
        let ht = HtCapabilities {
            capabilities: 0x006f, // ht_ldpc + bw40 + sgi20 + sgi40
            ampdu_params: 0x1b,   // exp=3, density=6
            rx_mcs: 0xffff,
        };
        let vht = VhtCapabilities {
            cap_info: 0x0180_0001, // max_mpdu=7991(1), exp=3, sgi80=0, rx_ldpc=0
            rx_mcs_map: 0xffea,    // 2SS MCS 0-9
            tx_mcs_map: 0xffea,
        };
        let caps = PeerCaps::from_ies(Some(ht), Some(vht));
        assert!(caps.vht);
        assert_eq!(caps.bw, 80);
        assert_eq!(caps.nss, 2);
        assert_eq!(caps.max_mcs, 9);
        assert!(!caps.vht_sgi80);
        assert!(!caps.vht_ldpc);
        assert!(!caps.sgi);
        assert!(!caps.ldpc);
        assert!(caps.ht_bw40);
        assert!(caps.ht_sgi20);
        assert!(caps.ht_sgi40);
        assert!(caps.ht_ldpc);
        assert_eq!(caps.max_mpdu, 7991);
        assert_eq!(caps.ampdu_exp, 3);
        assert_eq!(caps.mpdu_density, 6);
        assert_eq!(caps.ht_mcs_mask, 0xffff);
    }

    #[test]
    fn review_ht40_does_not_invent_sgi40_from_sgi20() {
        let ht = HtCapabilities {
            capabilities: 0x0022, // bw40 + sgi20 only, NO sgi40, NO ldpc
            ampdu_params: 0x0e,   // exp=2, density=3
            rx_mcs: 0x00ff,
        };
        let caps = PeerCaps::from_ies(Some(ht), None);
        assert!(caps.ht);
        assert!(!caps.vht);
        assert_eq!(caps.bw, 40);
        assert_eq!(caps.nss, 1);
        assert!(caps.ht_bw40);
        assert!(caps.ht_sgi20);
        assert!(!caps.ht_sgi40);
        assert!(!caps.sgi);
        assert!(!caps.ldpc);
        assert_eq!(caps.ampdu_exp, 2);
        assert_eq!(caps.mpdu_density, 3);
        assert_eq!(caps.ht_mcs_mask, 0x00ff);
    }
}
