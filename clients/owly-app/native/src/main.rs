#[cfg(target_os = "android")]
mod app;

#[cfg(target_os = "android")]
fn main() -> std::io::Result<()> {
    houji_runner::android::run_with_app(Box::new(|dir, uid, addr, peer, observed| {
        tokio::spawn(async move { app::run(dir, uid, addr, peer, observed).await })
    }))
}

#[cfg(not(target_os = "android"))]
fn main() {
    eprintln!("houji-owly-app runs on Android");
}
