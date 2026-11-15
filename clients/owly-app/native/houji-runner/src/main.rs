#[cfg(target_os = "android")]
mod android;
#[cfg(target_os = "android")]
mod app;

#[cfg(any(target_os = "android", test))]
#[path = "../../owly/crates/owly-radio/src/radiotap.rs"]
#[allow(dead_code)]
mod radiotap;

#[cfg(any(target_os = "android", test))]
mod ndp;
#[cfg(any(target_os = "android", test))]
mod peer_ctl;
#[cfg(any(target_os = "android", test))]
mod rx_gap;

#[cfg(target_os = "android")]
fn main() -> std::io::Result<()> {
    android::run()
}

#[cfg(not(target_os = "android"))]
fn main() {
    eprintln!("houji-owly runs on Android");
}
