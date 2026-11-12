use std::time::Instant;

const LIMITS_US: [u64; 8] = [250, 1_000, 4_000, 16_000, 32_000, 64_000, 128_000, 256_000];
const BUCKETS: usize = LIMITS_US.len() + 1;

#[derive(Default)]
pub struct RxGaps {
    previous_radio: Option<u64>,
    previous_host: Option<Instant>,
    matched: u64,
    missing: u64,
    equal: u64,
    backwards: u64,
    radio_count: [u64; BUCKETS],
    radio_us: [u64; BUCKETS],
    host_count: [u64; BUCKETS],
    host_us: [u64; BUCKETS],
    host_delayed: u64,
    equal_host_delayed: u64,
    host_caught_up: u64,
}

fn bucket(gap: u64) -> usize {
    LIMITS_US.partition_point(|&limit| gap >= limit)
}

impl RxGaps {
    pub fn record(&mut self, timestamp_us: Option<u64>, host: Instant) {
        self.matched += 1;
        let host = self.previous_host.replace(host).map(|previous| {
            host.saturating_duration_since(previous).as_micros() as u64
        });
        if let Some(host) = host {
            let host_bucket = bucket(host);
            self.host_count[host_bucket] += 1;
            self.host_us[host_bucket] += host;
        }

        let Some(stamp) = timestamp_us.filter(|&stamp| stamp != 0) else {
            self.missing += 1;
            self.previous_radio = None;
            return;
        };
        let Some(previous) = self.previous_radio.replace(stamp) else {
            return;
        };
        let Some(radio) = stamp.checked_sub(previous) else {
            self.backwards += 1;
            return;
        };
        if radio == 0 {
            self.equal += 1;
            self.equal_host_delayed += u64::from(host.is_some_and(|host| host >= 16_000));
            return;
        }
        let radio_bucket = bucket(radio);
        self.radio_count[radio_bucket] += 1;
        self.radio_us[radio_bucket] += radio;
        if let Some(host) = host {
            self.host_delayed += u64::from(radio < 2_000 && host >= 16_000);
            self.host_caught_up += u64::from(radio >= 16_000 && host < 2_000);
        }
    }

    pub fn report(&self) {
        println!(
            "OWLY_RX_GAPS schema=1 matched={} missing={} equal={} backwards={} host_delayed={} equal_host_delayed={} host_caught_up={} bounds_us={:?} radio_count={:?} radio_us={:?} host_count={:?} host_us={:?}",
            self.matched, self.missing, self.equal, self.backwards, self.host_delayed,
            self.equal_host_delayed, self.host_caught_up, LIMITS_US, self.radio_count, self.radio_us, self.host_count,
            self.host_us,
        );
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::time::Duration;

    #[test]
    fn distinguishes_delivery_delay_and_clock_discontinuities() {
        let start = Instant::now();
        let at = |us| start + Duration::from_micros(us);
        let mut stats = RxGaps::default();
        stats.record(Some(10), at(0));
        stats.record(Some(510), at(20_000));
        stats.record(Some(20_510), at(20_500));
        stats.record(Some(20_510), at(40_500));
        stats.record(Some(10), at(40_700));
        stats.record(None, at(40_800));
        stats.record(Some(100_000), at(41_000));
        assert_eq!(stats.matched, 7);
        assert_eq!((stats.missing, stats.equal, stats.backwards), (1, 1, 1));
        assert_eq!((stats.host_delayed, stats.host_caught_up), (1, 1));
        assert_eq!(stats.radio_count.iter().sum::<u64>(), 2);
        assert_eq!(stats.equal_host_delayed, 1);
        assert_eq!(stats.host_count.iter().sum::<u64>(), 6);
        assert_eq!(stats.radio_us.iter().sum::<u64>(), 20_500);
        assert_eq!(stats.host_us.iter().sum::<u64>(), 41_000);
    }
}
