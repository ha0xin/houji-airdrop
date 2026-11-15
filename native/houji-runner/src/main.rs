#[cfg(target_os = "android")]
fn main() -> std::io::Result<()> {
    houji_runner::android::run_xos()
}

#[cfg(not(target_os = "android"))]
fn main() {
    eprintln!("owly-xosd runs on Android");
}
