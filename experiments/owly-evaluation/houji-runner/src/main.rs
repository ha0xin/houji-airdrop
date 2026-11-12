#[cfg(target_os = "android")]
mod android;

#[cfg(any(target_os = "android", test))]
#[path = "../../owly/crates/owly-radio/src/radiotap.rs"]
#[allow(dead_code)]
mod radiotap;

#[cfg(target_os = "android")]
fn main() -> std::io::Result<()> {
    android::run()
}

#[cfg(not(target_os = "android"))]
fn main() {
    eprintln!("houji-owly runs on Android");
}
