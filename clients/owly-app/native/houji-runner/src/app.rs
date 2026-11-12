//! Private app-file IPC and Owly's AirDrop protocol driver. No XOS dependency.
use std::{collections::HashMap, fs, io, net::SocketAddrV6, path::{Path, PathBuf}, time::Duration};
use std::os::unix::fs::PermissionsExt;
use owly_awdl::{MacAddr, PeerInfo};
use owly_airdrop::{driver::{mdns::Mdns, receiver::{Receiver, ReceiverEvent, Options, Request},
    sender::{Sender, Payload, Event as SendEvent}, tls::Identity},
    proto::{mdns::{Service, Burst, BrowseEvent, RECEIVER_FLAGS}, receiver, sender, messages::Offer}};
use serde_json::{json, Value};
use tokio::sync::{watch, mpsc};

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
        "peers":[], "request":null, "receiver":null, "send_busy":false});
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
        &identity, Options::new(config, dir.join("received"))).await?;
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
                ReceiverEvent::Received{id:_, paths} => {
                    for path in &paths { give_to_app(path, uid, dir)?; }
                    state["message"] = json!(format!("接收完成：{} 个文件/目录", paths.len()));
                    state["received"] = json!(paths.iter().map(|p| p.to_string_lossy()).collect::<Vec<_>>());
                },
                ReceiverEvent::Urls{id:_,urls} => { state["message"] = json!(format!("收到链接：{}", urls.join("\n"))); },
                ReceiverEvent::Failed{id:_,reason} => { state["message"] = json!(format!("接收失败：{reason}")); },
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
                            let tx=updates.clone(); let id=id.clone(); let flags=receiver_flags;
                            state["send_busy"]=json!(true); state["message"]=json!("正在准备文件并等待对方接受…");
                            tokio::spawn(async move {
                                let (progress, mut rx) = mpsc::unbounded_channel();
                                let notify=tx.clone();
                                let monitor=tokio::spawn(async move { while let Some(event)=rx.recv().await {
                                    let update=match event { SendEvent::Accepted=>json!({"message":"对方已接受，正在发送…"}),
                                        SendEvent::Progress(p)=>json!({"message":"正在发送…","sent":p.sent,"total":p.total}) };
                                    let _=notify.send(update);
                                }});
                                let outcome=match Sender::new(sender::Config::new("Xiaomi 14 Owly", id)) {
                                    Ok(sender)=>sender.send(addr,&Payload::Files(vec![path]),flags,Some(progress)).await,
                                    Err(e)=>Err(e),
                                };
                                let _=monitor.await;
                                let message=match outcome { Ok(sender::Outcome::Delivered)=>"发送完成".to_string(),
                                    Ok(sender::Outcome::Declined)=>"对方拒绝或未及时接受".to_string(),
                                    Ok(sender::Outcome::Failed(e))=>format!("发送失败：{e}"),Err(e)=>format!("发送失败：{e}") };
                                let _=tx.send(json!({"send_busy":false,"message":message}));
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
