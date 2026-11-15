//! Private app-file IPC and Owly's AirDrop protocol driver. No XOS dependency.
use std::{collections::HashMap, fs, io, net::SocketAddrV6, path::{Path, PathBuf}, time::{Duration, Instant}};
use std::os::unix::fs::PermissionsExt;
use owly_awdl::{MacAddr, PeerInfo};
use owly_airdrop::{driver::{mdns::Mdns, receiver::{Receiver, ReceiverEvent, Options, Request},
    sender::{Sender, Payload, Event as SendEvent}, tls::Identity},
    proto::{mdns::{Service, Burst, BrowseEvent, RECEIVER_FLAGS}, receiver, sender, messages::Offer}};
use serde_json::{json, Value};
use tokio::sync::{watch, mpsc};

fn calc_speed_mbps(bytes: u64, secs: f64) -> f64 {
    if secs > 0.0 && bytes > 0 { (bytes as f64 / 1_000_000.0) / secs } else { 0.0 }
}

fn format_bytes(bytes: u64) -> String {
    if bytes >= 1_000_000 { format!("{:.2} MB", bytes as f64 / 1_000_000.0) }
    else if bytes >= 1_000 { format!("{:.1} KB", bytes as f64 / 1_000.0) }
    else { format!("{bytes} B") }
}

fn format_duration(secs: f64) -> String {
    if secs >= 60.0 {
        let mins = (secs as u64) / 60;
        let rem = secs - (mins as f64 * 60.0);
        format!("{mins} 分 {rem:.1} 秒（{secs:.2} s）")
    } else {
        format!("{secs:.2} 秒")
    }
}

fn format_speed(mb_per_s: f64) -> String {
    if mb_per_s >= 0.1 { format!("{mb_per_s:.2} MB/s") }
    else if mb_per_s > 0.0 { format!("{:.1} KB/s（{mb_per_s:.3} MB/s）", mb_per_s * 1000.0) }
    else { "0.00 MB/s".to_string() }
}

fn app_label(path: &Path, app_dir: &Path) -> io::Result<()> {
    use std::os::unix::ffi::OsStrExt;
    let source = std::ffi::CString::new(app_dir.as_os_str().as_bytes())?;
    let target = std::ffi::CString::new(path.as_os_str().as_bytes())?;
    let mut label = [0u8; 512];
    let n = unsafe { libc::getxattr(source.as_ptr(), c"security.selinux".as_ptr(), label.as_mut_ptr().cast(), label.len()) };
    if n < 0 { return Err(io::Error::last_os_error()); }
    if unsafe { libc::setxattr(target.as_ptr(), c"security.selinux".as_ptr(), label.as_ptr().cast(), n as usize, 0) } != 0 {
        return Err(io::Error::last_os_error());
    }
    Ok(())
}

fn save(dir: &Path, state: &Value) -> io::Result<()> {
    let tmp = dir.join("status.tmp");
    fs::write(&tmp, serde_json::to_vec(state)?)?;
    fs::set_permissions(&tmp, fs::Permissions::from_mode(0o644))?;
    app_label(&tmp, dir)?;
    fs::rename(tmp, dir.join("status.json"))
}

fn give_to_app(path: &Path, uid: u32, app_dir: &Path) -> io::Result<()> {
    use std::os::unix::ffi::OsStrExt;
    let meta = fs::symlink_metadata(path)?;
    if meta.file_type().is_symlink() { return Err(io::Error::other("unexpected received symlink")); }
    let c = std::ffi::CString::new(path.as_os_str().as_bytes())?;
    if unsafe { libc::chown(c.as_ptr(), uid, uid) } != 0 { return Err(io::Error::last_os_error()); }
    fs::set_permissions(path, fs::Permissions::from_mode(if meta.is_dir() { 0o700 } else { 0o600 }))?;
    app_label(path, app_dir)?;
    if meta.is_dir() { for item in fs::read_dir(path)? { give_to_app(&item?.path(), uid, app_dir)?; } }
    Ok(())
}

pub async fn run(dir: PathBuf, uid: u32, own: MacAddr, selected: MacAddr,
                 mut peers: watch::Receiver<Vec<PeerInfo>>) -> io::Result<()> {
    let result = serve(&dir, uid, own, selected, &mut peers).await;
    if let Err(error) = &result {
        let _ = save(&dir, &json!({"mode":"error", "message":error.to_string()}));
    }
    result
}

async fn serve(dir: &Path, uid: u32, own: MacAddr, selected: MacAddr,
               peers: &mut watch::Receiver<Vec<PeerInfo>>) -> io::Result<()> {
    fs::create_dir_all(dir.join("commands"))?;
    fs::create_dir_all(dir.join("received"))?;
    let scanning = selected.0 == [0; 6];
    let session = format!("{}-{}", std::process::id(), std::time::SystemTime::now()
        .duration_since(std::time::UNIX_EPOCH).map_err(io::Error::other)?.as_nanos());
    let mut state = json!({"mode":if scanning {"scanning"} else {"ready"},
        "session":session,
        "message":if scanning {"请在列表中选择 iPhone"} else {"已连接，等待 AirDrop"},
        "peers":[], "request":null, "receiver":null, "send_busy":false, "transfer":null});
    let mut timer = tokio::time::interval(Duration::from_millis(300));
    if scanning {
        loop {
            timer.tick().await;
            state["peers"] = json!(peers.borrow().iter().map(|p| json!({
                "mac":p.mac.to_string(), "name":p.name, "rssi":p.rssi, "device_class":p.devclass})).collect::<Vec<_>>());
            save(dir, &state)?;
        }
    }
    rustls::crypto::ring::default_provider().install_default().ok();
    let scope = unsafe { libc::if_nametoindex(c"owly0".as_ptr()) };
    if scope == 0 { return Err(io::Error::last_os_error()); }
    let identity = Identity::generate()?;
    let mut config = receiver::Config::new("Xiaomi 14 Owly");
    config.model_name = "Xiaomi 14".into();
    config.limits.max_upload_body = 4 << 30;
    let (server, mut events) = Receiver::bind(SocketAddrV6::new(own.link_local(), 8771, 0, scope),
        &identity, Options::new(config, dir.join("received")).with_progress(true)).await?;
    tokio::spawn(server.run());
    let id = own.to_string().replace(':', "");
    let mdns = Mdns::new(scope).with_burst(Some(Burst { count: 3,
        interval: Duration::from_millis(250), pause: Duration::from_secs(2) }));
    let (_registration, mut browse) = mdns.register_and_browse(Service { instance:id.clone(), host:format!("owly-{id}"),
        port:8771, addr:own.link_local(), flags:RECEIVER_FLAGS }).await?;
    let mut pending: HashMap<u64, Request> = HashMap::new();
    let mut receiver_addr = None;
    let mut receiver_flags = 0;
    let (updates, mut updated) = mpsc::unbounded_channel::<Value>();
    loop {
        tokio::select! {
            Some(event) = events.recv() => match event {
                ReceiverEvent::Request(request) => {
                    let files = match &request.ask.offer {
                        Offer::Files(files) => files.iter().map(|f| f.name.clone()).collect::<Vec<_>>(),
                        Offer::Urls(urls) => urls.clone(),
                    };
                    // One foreground prompt at a time; dropping older requests declines them.
                    pending.clear();
                    state["request"] = json!({"id":request.id,"sender":request.ask.sender_computer_name,"files":files});
                    pending.insert(request.id, request);
                },
                ReceiverEvent::Withdrawn{id} => {
                    pending.remove(&id);
                    if state["request"]["id"].as_u64() == Some(id) { state["request"] = Value::Null; }
                },
                ReceiverEvent::Progress{id:_, written, wire_bytes, total, elapsed} => {
                    let secs = elapsed.as_secs_f64();
                    let mbps = calc_speed_mbps(written, secs);
                    state["message"] = json!("正在接收…");
                    state["transfer"] = json!({"active":true, "ok":Value::Null, "direction":"recv",
                        "bytes":written, "wire_bytes":wire_bytes, "total":total,
                        "elapsed_ms":elapsed.as_millis() as u64, "duration_secs":secs, "speed_mbps":mbps});
                },
                ReceiverEvent::Received{id:_, paths, bytes, wire_bytes, duration} => {
                    for path in &paths { give_to_app(path, uid, dir)?; }
                    let secs = duration.as_secs_f64();
                    let mbps = calc_speed_mbps(bytes, secs);
                    state["message"] = json!(format!("接收完成：{} 个文件/目录（{}），总耗时 {}，平均速度 {}",
                        paths.len(), format_bytes(bytes), format_duration(secs), format_speed(mbps)));
                    state["received"] = json!(paths.iter().map(|p| p.to_string_lossy()).collect::<Vec<_>>());
                    state["transfer"] = json!({"active":false, "ok":true, "direction":"recv",
                        "files":paths.len(), "bytes":bytes, "wire_bytes":wire_bytes, "total":bytes,
                        "elapsed_ms":duration.as_millis() as u64, "duration_secs":secs, "speed_mbps":mbps});
                },
                ReceiverEvent::Urls{id:_,urls} => { state["message"] = json!(format!("收到链接：{}", urls.join("\n"))); },
                ReceiverEvent::Failed{id:_,reason} => {
                    state["message"] = json!(format!("接收失败：{reason}"));
                    if state["transfer"]["active"] == true && state["transfer"]["direction"] == "recv" {
                        state["transfer"]["active"] = json!(false);
                        state["transfer"]["ok"] = json!(false);
                    }
                },
                ReceiverEvent::Discovered{..} => { state["message"] = json!("对方已发现此设备，等待发送"); },
            },
            Some(event) = browse.events.recv() => match event {
                BrowseEvent::Found(found) if found.addr == selected.link_local() => {
                    let addr = SocketAddrV6::new(found.addr, found.port, 0, scope);
                    receiver_addr = Some(addr); receiver_flags = found.flags;
                    state["receiver"] = json!({"name":found.instance,"address":addr.to_string()});
                },
                BrowseEvent::Lost(instance) => {
                    if state["receiver"]["name"].as_str() == Some(&instance) {
                        receiver_addr = None; state["receiver"] = Value::Null;
                    }
                },
                _ => {},
            },
            Some(update) = updated.recv() => {
                for (key,value) in update.as_object().unwrap() { state[key] = value.clone(); }
            },
            _ = timer.tick() => {
                for entry in fs::read_dir(dir.join("commands"))? {
                    let path = entry?.path();
                    if path.extension().and_then(|s|s.to_str()) != Some("json") { continue; }
                    let command: Value = match fs::read(&path).ok().filter(|b|b.len() <= 16384)
                        .and_then(|b|serde_json::from_slice(&b).ok()) { Some(v) => v, None => { let _=fs::remove_file(path); continue; } };
                    fs::remove_file(&path)?;
                    if command["session"].as_str() != Some(&session) { continue; }
                    match command["op"].as_str().unwrap_or("") {
                        "accept" | "decline" => {
                            if let Some(request) = command["id"].as_u64().and_then(|id|pending.remove(&id)) {
                                if command["op"] == "accept" { request.accept(); state["message"]=json!("正在接收…"); }
                                else { request.decline(); state["message"]=json!("已拒绝"); }
                                state["request"]=Value::Null;
                            }
                        },
                        "send" if state["send_busy"] != true => {
                            let Some(addr) = receiver_addr else { state["message"]=json!("尚未发现对方的 AirDrop 接收服务，请打开所有人（10 分钟）"); continue; };
                            let candidate = PathBuf::from(command["path"].as_str().unwrap_or(""));
                            let allowed = dir.join("outbox").canonicalize()?;
                            let path = match candidate.canonicalize() { Ok(p) if p.starts_with(&allowed) && p.is_file() => p,
                                _ => { state["message"]=json!("发送文件无效"); continue; } };
                            let file_bytes = fs::metadata(&path).map(|m| m.len()).unwrap_or(0);
                            let tx=updates.clone(); let id=id.clone(); let flags=receiver_flags;
                            state["send_busy"]=json!(true); state["message"]=json!("正在准备文件并等待对方接受…");
                            tokio::spawn(async move {
                                let (progress, mut rx) = mpsc::unbounded_channel();
                                let notify=tx.clone();
                                let monitor=tokio::spawn(async move {
                                    let mut upload_started: Option<Instant> = None;
                                    let mut last_emit: Option<Instant> = None;
                                    let mut wire_sent = 0u64;
                                    let mut wire_total = 0u64;
                                    while let Some(event)=rx.recv().await {
                                        match event {
                                            SendEvent::Accepted => {
                                                let now = Instant::now();
                                                upload_started = Some(now);
                                                last_emit = Some(now);
                                                let _ = notify.send(json!({
                                                    "message": "对方已接受，正在发送…",
                                                    "transfer": {
                                                        "active": true, "ok": Value::Null, "direction": "send",
                                                        "bytes": 0, "total": file_bytes, "elapsed_ms": 0,
                                                        "duration_secs": 0.0, "speed_mbps": 0.0
                                                    }
                                                }));
                                            }
                                            SendEvent::Progress(p) => {
                                                let now = Instant::now();
                                                let started = *upload_started.get_or_insert(now);
                                                wire_sent = p.sent;
                                                wire_total = p.total;
                                                if p.sent == p.total || last_emit.map_or(true, |t| now.duration_since(t) >= Duration::from_millis(250)) {
                                                    last_emit = Some(now);
                                                    let elapsed = now.duration_since(started);
                                                    let secs = elapsed.as_secs_f64();
                                                    let est_bytes = if p.total > 0 && file_bytes > 0 {
                                                        ((p.sent as u128 * file_bytes as u128) / p.total as u128) as u64
                                                    } else { p.sent };
                                                    let mbps = calc_speed_mbps(est_bytes, secs);
                                                    let _ = notify.send(json!({
                                                        "message": "正在发送…",
                                                        "sent": p.sent, "total": p.total,
                                                        "transfer": {
                                                            "active": true, "ok": Value::Null, "direction": "send",
                                                            "bytes": est_bytes, "wire_bytes": p.sent,
                                                            "total": if file_bytes > 0 { file_bytes } else { p.total },
                                                            "elapsed_ms": elapsed.as_millis() as u64,
                                                            "duration_secs": secs, "speed_mbps": mbps
                                                        }
                                                    }));
                                                }
                                            }
                                        }
                                    }
                                    (upload_started, wire_sent, wire_total)
                                });
                                let call_started = Instant::now();
                                let outcome=match Sender::new(sender::Config::new("Xiaomi 14 Owly", id)) {
                                    Ok(sender)=>sender.send(addr,&Payload::Files(vec![path]),flags,Some(progress)).await,
                                    Err(e)=>Err(e),
                                };
                                let (upload_started, _wire_sent, wire_total) = monitor.await.unwrap_or((None, 0, 0));
                                let elapsed = upload_started.unwrap_or(call_started).elapsed();
                                let secs = elapsed.as_secs_f64();
                                match outcome {
                                    Ok(sender::Outcome::Delivered) => {
                                        let effective_bytes = if file_bytes > 0 { file_bytes } else { wire_total };
                                        let mbps = calc_speed_mbps(effective_bytes, secs);
                                        let message = format!("发送完成：1 个文件（{}），总耗时 {}，平均速度 {}",
                                            format_bytes(effective_bytes), format_duration(secs), format_speed(mbps));
                                        let _ = tx.send(json!({
                                            "send_busy": false, "message": message,
                                            "transfer": {
                                                "active": false, "ok": true, "direction": "send", "files": 1,
                                                "bytes": effective_bytes, "wire_bytes": wire_total, "total": effective_bytes,
                                                "elapsed_ms": elapsed.as_millis() as u64, "duration_secs": secs, "speed_mbps": mbps
                                            }
                                        }));
                                    }
                                    Ok(sender::Outcome::Declined) => {
                                        let _ = tx.send(json!({
                                            "send_busy": false, "message": "对方拒绝或未及时接受",
                                            "transfer": {"active": false, "ok": false, "direction": "send"}
                                        }));
                                    }
                                    Ok(sender::Outcome::Failed(e)) => {
                                        let _ = tx.send(json!({
                                            "send_busy": false, "message": format!("发送失败：{e}"),
                                            "transfer": {"active": false, "ok": false, "direction": "send"}
                                        }));
                                    }
                                    Err(e) => {
                                        let _ = tx.send(json!({
                                            "send_busy": false, "message": format!("发送失败：{e}"),
                                            "transfer": {"active": false, "ok": false, "direction": "send"}
                                        }));
                                    }
                                }
                            });
                        },
                        _ => {},
                    }
                }
            }
        }
        save(dir, &state)?;
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn formats_transfer_size_duration_and_speed() {
        assert_eq!(format_bytes(512), "512 B");
        assert_eq!(format_bytes(24_500), "24.5 KB");
        assert_eq!(format_bytes(26_707_537), "26.71 MB");

        assert_eq!(format_duration(3.424), "3.42 秒");
        assert_eq!(format_duration(107.1), "1 分 47.1 秒（107.10 s）");

        let slow = calc_speed_mbps(26_707_537, 107.10);
        assert!((slow - 0.24937).abs() < 0.001);
        assert_eq!(format_speed(slow), "0.25 MB/s");
        assert_eq!(format_speed(0.05), "50.0 KB/s（0.050 MB/s）");
        assert_eq!(format_speed(0.0), "0.00 MB/s");
    }
}
