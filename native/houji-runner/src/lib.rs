#[cfg(target_os = "android")]
pub mod android;

#[cfg(any(target_os = "android", test))]
#[path = "../../owly/crates/owly-radio/src/radiotap.rs"]
#[allow(dead_code)]
pub(crate) mod radiotap;

#[cfg(any(target_os = "android", test))]
#[cfg_attr(not(target_os = "android"), allow(dead_code))]
mod ndp;
#[cfg(any(target_os = "android", test))]
#[cfg_attr(not(target_os = "android"), allow(dead_code))]
mod net_util;
#[cfg(any(target_os = "android", test))]
#[cfg_attr(not(target_os = "android"), allow(dead_code))]
mod peer_ctl;
#[cfg(any(target_os = "android", test))]
#[cfg_attr(not(target_os = "android"), allow(dead_code))]
mod rx_gap;
#[cfg(any(target_os = "android", test))]
#[cfg_attr(not(target_os = "android"), allow(dead_code))]
mod vnet;
