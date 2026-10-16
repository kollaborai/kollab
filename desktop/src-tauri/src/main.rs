//! Kollab desktop: a menu bar app that owns a local Kollab runtime.
//!
//! On launch it attaches to a web UI already answering on KOLLAB_WEBUI_PORT
//! (default 8080), or starts `kollab --web-ui` from the newer of the bundled
//! sidecar and an installed CLI. Closing the window hides it to the menu bar;
//! only Quit stops the runtime the app started, and it asks first.

use std::io::{Read, Write};
use std::net::{SocketAddr, TcpStream};
#[cfg(unix)]
use std::os::unix::process::CommandExt;
use std::path::{Path, PathBuf};
use std::process::{Child, Command, Stdio};
use std::sync::Mutex;
#[cfg(target_os = "macos")]
use std::sync::OnceLock;
use std::time::{Duration, Instant};

use tauri::menu::{CheckMenuItem, Menu, MenuItem, PredefinedMenuItem, Submenu};
use tauri::tray::TrayIconBuilder;
use tauri::webview::{NewWindowResponse, PageLoadEvent};
use tauri::{
    AppHandle, Manager, RunEvent, Url, WebviewUrl, WebviewWindow, WebviewWindowBuilder, WindowEvent, Wry,
};
#[cfg(target_os = "macos")]
use tauri::ActivationPolicy;
use tauri_plugin_autostart::{MacosLauncher, ManagerExt as _};
use tauri_plugin_dialog::{DialogExt, MessageDialogButtons, MessageDialogKind};
#[cfg(target_os = "macos")]
use objc2::{rc::Retained, runtime::{AnyObject, ClassBuilder, Sel}, class, msg_send, sel};
use tauri_plugin_window_state::StateFlags;

mod updater;

/// How long a spawned web UI gets to answer before the error page shows.
const START_TIMEOUT: Duration = Duration::from_secs(30);

/// The bundled status page (loading, install, error) on macOS and Linux.
const STATUS_PAGE: &str = "tauri://localhost/index.html";

/// Reports the page title and whether the React root rendered, once it has or
/// after 10 s, by navigating to `kollab-desktop://report?...`, which
/// `on_navigation` logs and cancels. No IPC, so it works on the web UI's origin.
const REPORT_JS: &str = r#"(() => {
  const root = document.getElementById('root');
  let sent = false;
  const report = () => {
    if (sent) return;
    sent = true;
    const rendered = !!(root && root.childElementCount);
    location.href = 'kollab-desktop://report?' + new URLSearchParams({ title: document.title, rendered });
  };
  if (!root || root.childElementCount) return report();
  new MutationObserver(report).observe(root, { childList: true });
  setTimeout(report, 10000);
})();"#;

/// Tells the web UI it runs in this window, so its update banner can hand a
/// Kollab.app update to `kollab-desktop://update` instead of failing.
const DESKTOP_JS: &str = "window.kollabDesktop = { update: 'kollab-desktop://update' };";

/// Startup and Retry share one operation. Spawn admission and publishing happen
/// under this same lock, so closing it cannot leave a late, untracked child.
struct Runtime<C> {
    owned: Option<C>,
    operation: Option<u64>,
    next_id: u64,
    closing: bool,
    quit_prompt: Option<u64>,
    exit_worker: bool,
    exit_ready: bool,
}

#[derive(Debug, PartialEq)]
enum Quit {
    Ignore,
    Exit,
    Confirm(u64),
}

impl<C> Default for Runtime<C> {
    fn default() -> Self {
        Self { owned: None, operation: None, next_id: 0, closing: false, quit_prompt: None,
            exit_worker: false, exit_ready: false }
    }
}

impl<C> Runtime<C> {
    fn begin(&mut self) -> Option<u64> {
        if self.closing || self.operation.is_some() { return None; }
        self.next_id += 1;
        self.operation = Some(self.next_id);
        self.operation
    }

    fn current(&self, operation: u64) -> bool {
        !self.closing && self.operation == Some(operation)
    }

    fn spawn(&mut self, operation: u64, spawn: impl FnOnce() -> std::io::Result<C>) -> std::io::Result<bool> {
        if !self.current(operation) { return Ok(false); }
        if self.owned.is_some() { return Err(std::io::Error::other("the previous runtime has not stopped")); }
        self.owned = Some(spawn()?);
        Ok(true)
    }

    fn finish(&mut self, operation: u64) {
        if self.operation == Some(operation) { self.operation = None; }
    }

    fn close(&mut self) {
        self.closing = true;
        self.operation = None;
        self.quit_prompt = None;
    }

    fn finish_exit(&mut self, stopped: bool) -> bool {
        self.exit_worker = false;
        self.exit_ready = stopped && self.owned.is_none();
        if !self.exit_ready { self.closing = false; }
        self.exit_ready
    }

    fn request_quit(&mut self) -> Quit {
        if self.closing || self.quit_prompt.is_some() { return Quit::Ignore; }
        if self.owned.is_none() && self.operation.is_none() {
            self.close();
            return Quit::Exit;
        }
        self.next_id += 1;
        self.quit_prompt = Some(self.next_id);
        Quit::Confirm(self.next_id)
    }

    fn confirm_quit(&mut self, prompt: u64, quit: bool) -> bool {
        if self.closing || self.quit_prompt != Some(prompt) { return false; }
        self.quit_prompt = None;
        if quit { self.close(); }
        quit
    }
}

#[derive(Default)]
struct Spawned(Mutex<Runtime<Child>>);

/// The tray's "Launch at Login" item, so its check mark follows the real setting.
struct LoginItem(CheckMenuItem<Wry>);

fn ui_url() -> Url {
    let port = std::env::var("KOLLAB_WEBUI_PORT")
        .ok()
        .and_then(|p| p.parse::<u16>().ok())
        .unwrap_or(8080);
    format!("http://127.0.0.1:{port}/").parse().expect("valid url")
}

/// True when a Kollab web UI answers at `url`: its /openapi.json is titled
/// "Kollab WebUI", the same check `kollab --web-ui` makes before it starts one.
fn webui_running(url: &Url) -> bool {
    let Ok(addr) = format!("127.0.0.1:{}", url.port().unwrap_or(80)).parse::<SocketAddr>() else {
        return false;
    };
    let Ok(mut stream) = TcpStream::connect_timeout(&addr, Duration::from_secs(1)) else {
        return false;
    };
    let _ = stream.set_read_timeout(Some(Duration::from_secs(2)));
    let mut response = Vec::new();
    if stream.write_all(b"GET /openapi.json HTTP/1.0\r\nHost: 127.0.0.1\r\n\r\n").is_err()
        || stream.read_to_end(&mut response).is_err()
    {
        return false;
    }
    let response = String::from_utf8_lossy(&response);
    let Some((_, body)) = response.split_once("\r\n\r\n") else {
        return false;
    };
    serde_json::from_str::<serde_json::Value>(body).is_ok_and(|v| v["info"]["title"] == "Kollab WebUI")
}

/// The version `kollab --version` prints ("<name> X.Y.Z"), or None if it doesn't run.
fn kollab_version(bin: &Path) -> Option<Vec<u32>> {
    let out = Command::new(bin)
        .arg("--version")
        .stdin(Stdio::null())
        .stderr(Stdio::null())
        .output()
        .ok()?;
    let text = String::from_utf8(out.stdout).ok()?;
    text.split_whitespace()
        .last()?
        .split('.')
        .map(|part| part.chars().take_while(char::is_ascii_digit).collect::<String>().parse().ok())
        .collect()
}

/// macOS bundles a launcher beside an unpacked, signed Python runtime
/// (scripts/desktop_release.py stage), since codesign cannot sign a scie.
/// Linux keeps the scie as opaque package data outside linuxdeploy's reach.
fn bundled_kollab() -> Option<PathBuf> {
    #[cfg(target_os = "linux")]
    let path = linux_bundled_kollab(&std::env::current_exe().ok()?)?;
    #[cfg(target_os = "macos")]
    let path = macos_bundled_kollab(&std::env::current_exe().ok()?)?;
    #[cfg(not(any(target_os = "linux", target_os = "macos")))]
    let path = std::env::current_exe().ok()?.with_file_name("kollab");
    path.is_file().then_some(path)
}

/// Kollab.app/Contents/MacOS/kollab-desktop -> Kollab.app/Contents/Resources/kollab,
/// the sh launcher that runs Resources/runtime/bin/python3.12 -m kollabor_cli_main.
#[cfg(any(target_os = "macos", test))]
fn macos_bundled_kollab(executable: &Path) -> Option<PathBuf> {
    Some(executable.parent()?.parent()?.join("Resources/kollab"))
}

/// Debian and AppImage share the usr/bin + usr/share package layout. Keeping
/// the scie outside linuxdeploy's ELF scan preserves its appended Python data.
#[cfg(any(target_os = "linux", test))]
fn linux_bundled_kollab(executable: &Path) -> Option<PathBuf> {
    Some(executable.parent()?.parent()?.join("share/kollab/runtime/kollab"))
}

/// GUI apps don't get the shell's PATH: ask the login shell, then try the usual install spots.
fn installed_kollab() -> Option<PathBuf> {
    let default_shell = if cfg!(target_os = "macos") { "/bin/zsh" } else { "/bin/sh" };
    let shell = std::env::var("SHELL").unwrap_or_else(|_| default_shell.into());
    let from_shell = Command::new(shell)
        .args(["-lc", "command -v kollab"])
        .stdin(Stdio::null())
        .stderr(Stdio::null())
        .output()
        .ok()
        .and_then(|out| {
            let text = String::from_utf8(out.stdout).ok()?;
            text.lines().rev().map(str::trim).find(|l| l.starts_with('/')).map(PathBuf::from)
        });
    let home = std::env::var("HOME").unwrap_or_default();
    let fallbacks = [
        format!("{home}/.local/bin/kollab"),
        "/opt/homebrew/bin/kollab".into(),
        "/usr/local/bin/kollab".into(),
        #[cfg(target_os = "linux")]
        "/usr/bin/kollab".into(),
    ];
    from_shell
        .into_iter()
        .chain(fallbacks.into_iter().map(PathBuf::from))
        .find(|p| p.is_file())
}

/// The newer of the bundled and an installed kollab; the bundled one on a tie.
fn pick_kollab() -> Option<PathBuf> {
    let versioned = |path: PathBuf| kollab_version(&path).map(|version| (version, path));
    let bundled = bundled_kollab().and_then(versioned);
    let installed = installed_kollab().and_then(versioned);
    eprintln!("kollab-desktop: bundled kollab {bundled:?}, installed kollab {installed:?}");
    match (bundled, installed) {
        (Some(bundled), Some(installed)) if installed.0 > bundled.0 => Some(installed.1),
        (Some(bundled), _) => Some(bundled.1),
        (None, installed) => installed.map(|(_, path)| path),
    }
}

/// Starts `kollab --web-ui` in its own session and process group, its output going to `log`.
fn spawn_webui(kollab: &Path, log: &Path) -> std::io::Result<Child> {
    let out = std::fs::File::create(log)?;
    let mut command = Command::new(kollab);
    command
        .arg("--web-ui")
        .env("BROWSER", "true") // no browser tab: this window is the browser
        .env("PYTHONUNBUFFERED", "1") // so the error page shows the latest output
        .current_dir(std::env::var("HOME").unwrap_or_else(|_| "/".into()))
        .stdin(Stdio::null())
        .stdout(out.try_clone()?)
        .stderr(out);
    // setsid: Quit signals the CLI, its engine and its UI server as one group.
    unsafe {
        command.pre_exec(|| match libc::setsid() {
            -1 => Err(std::io::Error::last_os_error()),
            _ => Ok(()),
        });
    }
    command.spawn()
}

fn show_status(win: &WebviewWindow, state: &str, log: &str) {
    let mut url: Url = STATUS_PAGE.parse().expect("valid url");
    url.query_pairs_mut().append_pair("state", state).append_pair("log", log);
    let _ = win.navigate(url);
}

fn while_current(app: &AppHandle, operation: u64, action: impl FnOnce()) -> bool {
    let current = app.state::<Spawned>().0.lock().unwrap().current(operation);
    if !current { return false; }
    // Tauri may dispatch this action to the UI thread. Never hold its state
    // lock while waiting for that thread, which also handles Quit callbacks.
    action();
    true
}

fn start_boot(app: &AppHandle, restart: bool) {
    let Some(operation) = app.state::<Spawned>().0.lock().unwrap().begin() else { return; };
    let app = app.clone();
    std::thread::spawn(move || {
        if restart && !stop_spawned(&app) {
            while_current(&app, operation, || {
                if let Some(win) = app.get_webview_window("main") {
                    show_status(&win, "error", "The previous runtime has not stopped. Retry after it exits.");
                }
            });
        } else {
            if restart {
                while_current(&app, operation, || {
                    if let Some(win) = app.get_webview_window("main") {
                        show_status(&win, "loading", "");
                    }
                });
            }
            boot(&app, operation);
        }
        app.state::<Spawned>().0.lock().unwrap().finish(operation);
    });
}

/// Attach to a running web UI, or start one and wait for it to answer.
fn boot(app: &AppHandle, operation: u64) {
    let Some(win) = app.get_webview_window("main") else {
        return;
    };
    let url = ui_url();
    if webui_running(&url) {
        eprintln!("kollab-desktop: attaching to the web UI at {url}");
        while_current(app, operation, || { let _ = win.navigate(url); });
        return;
    }
    let Some(kollab) = pick_kollab() else {
        eprintln!("kollab-desktop: no kollab found, showing the install page");
        while_current(app, operation, || show_status(&win, "install", ""));
        return;
    };
    let log = app
        .path()
        .app_log_dir()
        .unwrap_or_else(|_| std::env::temp_dir())
        .join("webui.log");
    let _ = std::fs::create_dir_all(log.parent().expect("log file has a folder"));
    let spawned = app.state::<Spawned>().0.lock().unwrap().spawn(operation, || spawn_webui(&kollab, &log));
    match spawned {
        Ok(true) => eprintln!("kollab-desktop: started {} --web-ui", kollab.display()),
        Ok(false) => return,
        Err(err) => {
            while_current(app, operation, || show_status(&win, "error", &format!("could not start {}: {err}", kollab.display())));
            return;
        }
    }
    let deadline = Instant::now() + START_TIMEOUT;
    while Instant::now() < deadline {
        if !while_current(app, operation, || {}) { return; }
        if webui_running(&url) {
            eprintln!("kollab-desktop: web UI is up at {url}");
            while_current(app, operation, || { let _ = win.navigate(url); });
            return;
        }
        // WNOWAIT leaves even an exited leader reserved until cleanup. try_wait
        // here would reap it and make a cached process-group ID unsafe to signal.
        let exited = app.state::<Spawned>().0.lock().unwrap().owned.as_ref()
            .is_some_and(|child| child_exited(child).unwrap_or(true));
        if exited {
            break;
        }
        std::thread::sleep(Duration::from_millis(250));
    }
    eprintln!("kollab-desktop: the web UI did not answer, showing the error page");
    let text = std::fs::read_to_string(&log).unwrap_or_default();
    let lines: Vec<&str> = text.lines().collect();
    while_current(app, operation, || show_status(&win, "error", &lines[lines.len().saturating_sub(40)..].join("\n")));
}

/// Stops the web UI this app started the way Ctrl+C would: SIGINT to its
/// process group, then SIGTERM if anything is left after 5 s.
fn stop_spawned(app: &AppHandle) -> bool {
    let state = app.state::<Spawned>();
    let mut runtime = state.0.lock().unwrap();
    let Some(child) = runtime.owned.as_mut() else { return true; };
    match stop_child(child) {
        Ok(true) => { runtime.owned = None; true }
        Ok(false) => {
            eprintln!("kollab-desktop: owned runtime remains after the cleanup deadline; no force used");
            false
        }
        Err(err) => {
            eprintln!("kollab-desktop: cannot verify/stop the owned runtime: {err}; no unverified signal sent");
            false
        }
    }
}

/// An unreaped direct child reserves its PID, including after it exits. Never
/// turn this observation into try_wait: freeing that PID before the last group
/// signal could target an unrelated process group that later reuses its number.
fn child_exited(child: &Child) -> std::io::Result<bool> {
    let mut info: libc::siginfo_t = unsafe { std::mem::zeroed() };
    let result = unsafe {
        libc::waitid(libc::P_PID, child.id() as libc::id_t, &mut info,
            libc::WEXITED | libc::WNOHANG | libc::WNOWAIT)
    };
    if result != 0 { return Err(std::io::Error::last_os_error()); }
    #[cfg(target_os = "macos")]
    let pid = info.si_pid;
    #[cfg(target_os = "linux")]
    let pid = unsafe { info.si_pid() };
    Ok(pid != 0)
}

#[cfg(target_os = "macos")]
fn group_has_peers(group: libc::pid_t) -> std::io::Result<bool> {
    const PROC_PGRP_ONLY: u32 = 2; // sys/proc_info.h; libc exposes the function, not this constant.
    // libproc returns bytes. A full buffer is inconclusive, so hold rather than
    // reap the leader while an unseen group member could still be running.
    let mut pids = [0 as libc::pid_t; 1024];
    let capacity = std::mem::size_of_val(&pids);
    unsafe { *libc::__error() = 0; }
    let bytes = unsafe {
        libc::proc_listpids(PROC_PGRP_ONLY, group as u32,
            pids.as_mut_ptr().cast(), capacity as i32)
    };
    if bytes < 0 || (bytes == 0 && std::io::Error::last_os_error().raw_os_error() != Some(0)) {
        return Err(std::io::Error::last_os_error());
    }
    if bytes as usize >= capacity {
        return Err(std::io::Error::new(std::io::ErrorKind::WouldBlock, "process group listing is incomplete"));
    }
    Ok(pids[..bytes as usize / std::mem::size_of::<libc::pid_t>()]
        .iter().any(|pid| *pid > 0 && *pid != group))
}

/// Linux exposes process groups through /proc, not macOS libproc. The command
/// name may contain spaces and parentheses, so read fields only after its end.
#[cfg(any(target_os = "linux", test))]
fn stat_process_group(stat: &str) -> std::io::Result<libc::pid_t> {
    stat.rsplit_once(") ").and_then(|(_, fields)| fields.split_whitespace().nth(2))
        .and_then(|group| group.parse().ok())
        .ok_or_else(|| std::io::Error::new(std::io::ErrorKind::InvalidData, "invalid /proc process stat"))
}

#[cfg(target_os = "linux")]
fn group_has_peers(group: libc::pid_t) -> std::io::Result<bool> {
    for entry in std::fs::read_dir("/proc")? {
        let entry = entry?;
        let Some(pid) = entry.file_name().to_str().and_then(|name| name.parse::<libc::pid_t>().ok()) else {
            continue;
        };
        if pid == group { continue; }
        match std::fs::read_to_string(entry.path().join("stat")) {
            Ok(stat) if stat_process_group(&stat)? == group => return Ok(true),
            Ok(_) => {}
            Err(err) if err.kind() == std::io::ErrorKind::NotFound => {} // exited during the scan
            Err(err) => return Err(err), // incomplete evidence must not release ownership
        }
    }
    Ok(false)
}

fn signal_owned_group(child: &Child, signal: libc::c_int) -> std::io::Result<()> {
    child_exited(child)?; // ECHILD refuses a cached/reaped PID; WNOWAIT retains ownership.
    if unsafe { libc::killpg(child.id() as libc::pid_t, signal) } != 0 {
        return Err(std::io::Error::last_os_error());
    }
    Ok(())
}

fn stop_child(child: &mut Child) -> std::io::Result<bool> {
    if group_exited(child, Duration::ZERO)? { return Ok(true); }
    eprintln!("kollab-desktop: stopping owned process group {}", child.id());
    signal_owned_group(child, libc::SIGINT)?;
    if group_exited(child, Duration::from_secs(5))? { return Ok(true); }
    eprintln!("kollab-desktop: still running after 5 s, sending SIGTERM");
    signal_owned_group(child, libc::SIGTERM)?;
    group_exited(child, Duration::from_secs(2))
}

/// Reap only once the leader exited and has no remaining group members. Until
/// then the same exact Child stays in the slot, including on a failed Retry.
fn group_exited(child: &mut Child, timeout: Duration) -> std::io::Result<bool> {
    let deadline = Instant::now() + timeout;
    loop {
        if child_exited(child)? && !group_has_peers(child.id() as libc::pid_t)? {
            child.wait()?;
            return Ok(true);
        }
        if Instant::now() >= deadline {
            return Ok(false);
        }
        std::thread::sleep(Duration::from_millis(100));
    }
}

/// While the window is hidden the app lives in the menu bar only: an accessory
/// app with no Dock icon, as Docker Desktop does.
fn menu_bar_only(app: &AppHandle, on: bool) {
    #[cfg(target_os = "macos")]
    {
    let policy = if on { ActivationPolicy::Accessory } else { ActivationPolicy::Regular };
    if let Err(err) = app.set_activation_policy(policy) {
        eprintln!("kollab-desktop: could not change the activation policy: {err}");
    }
    }
    #[cfg(not(target_os = "macos"))]
    let _ = (app, on); // Linux/Windows keep their ordinary taskbar/tray policy.
}

fn show_window(app: &AppHandle) {
    menu_bar_only(app, false);
    if let Some(win) = app.get_webview_window("main") {
        let _ = win.unminimize();
        let _ = win.show();
        let _ = win.set_focus();
    }
}

/// Quit from the tray, Cmd+Q or the Dock. Stopping a runtime the app
/// started stops its agents, so that asks first; an attached one is left alone.
fn request_quit(app: &AppHandle) {
    let action = app.state::<Spawned>().0.lock().unwrap().request_quit();
    let prompt = match action {
        Quit::Ignore => return,
        Quit::Exit => return exit_now(app),
        Quit::Confirm(prompt) => prompt,
    };
    let handle = app.clone();
    app.dialog()
        .message("Quit Kollab and stop the agents it started?")
        .title("Quit Kollab")
        .kind(MessageDialogKind::Warning)
        .buttons(MessageDialogButtons::OkCancelCustom("Quit".into(), "Cancel".into()))
        .show(move |quit| {
            let accepted = handle.state::<Spawned>().0.lock().unwrap().confirm_quit(prompt, quit);
            if accepted {
                exit_now(&handle);
            }
        });
}

fn exit_now(app: &AppHandle) {
    {
        let state = app.state::<Spawned>();
        let mut runtime = state.0.lock().unwrap();
        runtime.close();
        if runtime.exit_worker { return; }
        runtime.exit_worker = true;
    }
    let app = app.clone();
    std::thread::spawn(move || {
        let stopped = stop_spawned(&app);
        let ready = app.state::<Spawned>().0.lock().unwrap().finish_exit(stopped);
        if ready {
            app.exit(0);
        } else {
            show_window(&app);
            app.dialog()
                .message("Some owned processes could not be stopped safely. Kollab will stay open. Choose Quit again to retry.")
                .title("Could not quit Kollab")
                .kind(MessageDialogKind::Error)
                .show(|_| {});
        }
    });
}

/// The app for the quit Apple Event handler, an Objective-C callback with no context.
#[cfg(target_os = "macos")]
static QUIT_APP: OnceLock<AppHandle> = OnceLock::new();

#[cfg(target_os = "macos")]
const fn four_char(code: &[u8; 4]) -> u32 {
    u32::from_be_bytes(*code)
}

/// Takes the quit Apple Event (Dock > Quit, AppleScript, logout) from AppKit,
/// which quits at once and so stops the runtime unasked. Window utilities send
/// one when an app's last window closes (Vorssaint does, ~0.1 s after). Logout, restart and shutdown still
/// quit; a quit while the window shows asks like Quit Kollab; a quit while the
/// app sits in the menu bar is refused.
#[cfg(target_os = "macos")]
fn take_quit_event(app: &AppHandle) {
    unsafe extern "C-unwind" fn on_quit(_: &AnyObject, _: Sel, event: *mut AnyObject, reply: *mut AnyObject) {
        let Some(app) = QUIT_APP.get() else { return };
        unsafe {
            let sender: *mut AnyObject = msg_send![event, attributeDescriptorForKeyword: four_char(b"spid")];
            let pid: i32 = if sender.is_null() { -1 } else { msg_send![sender, int32Value] };
            let reason: *mut AnyObject = msg_send![event, attributeDescriptorForKeyword: four_char(b"why?")];
            let ns_app: *mut AnyObject = msg_send![class!(NSApplication), sharedApplication];
            let policy: isize = msg_send![ns_app, activationPolicy];
            let name = Command::new("/bin/ps").args(["-o", "comm=", "-p", &pid.to_string()]).output();
            let name = name.map(|out| String::from_utf8_lossy(&out.stdout).trim().to_string()).unwrap_or_default();
            eprintln!("kollab-desktop: quit event from pid {pid} ({name})");
            if !reason.is_null() {
                exit_now(app); // logout, restart or shutdown
            } else if policy == 0 {
                request_quit(app); // the window shows (a regular app)
            } else {
                eprintln!("kollab-desktop: refused, the app is in the menu bar (Quit Kollab quits)");
                if !reply.is_null() {
                    let canceled: *mut AnyObject = msg_send![class!(NSAppleEventDescriptor), descriptorWithInt32: -128i32];
                    let () = msg_send![reply, setParamDescriptor: canceled, forKeyword: four_char(b"errn")];
                }
            }
        }
    }
    let _ = QUIT_APP.set(app.clone());
    let mut builder = ClassBuilder::new(c"KollabQuitHandler", class!(NSObject)).expect("a new class name");
    unsafe {
        builder.add_method(sel!(handleQuit:withReplyEvent:), on_quit as unsafe extern "C-unwind" fn(_, _, _, _));
    }
    let handler: Retained<AnyObject> = unsafe { msg_send![builder.register(), new] };
    unsafe {
        let manager: *mut AnyObject = msg_send![class!(NSAppleEventManager), sharedAppleEventManager];
        let () = msg_send![manager, setEventHandler: Retained::into_raw(handler), andSelector: sel!(handleQuit:withReplyEvent:), forEventClass: four_char(b"aevt"), andEventID: four_char(b"quit")];
    }
}

/// "Launch at Login" starts the app hidden in the menu bar. Off by default.
fn toggle_launch_at_login(app: &AppHandle) {
    let autolaunch = app.autolaunch();
    let result = match autolaunch.is_enabled() {
        Ok(true) => autolaunch.disable(),
        _ => autolaunch.enable(),
    };
    if let Err(err) = result {
        eprintln!("kollab-desktop: could not change Launch at Login: {err}");
    }
    let _ = app.state::<LoginItem>().0.set_checked(autolaunch.is_enabled().unwrap_or(false));
}

/// An update install stops the owned runtime the way Quit does. On Err the app
/// carries on as it was (the runtime that could not stop is still owned).
fn stop_for_update(app: &AppHandle) -> Result<(), String> {
    {
        let state = app.state::<Spawned>();
        let mut runtime = state.0.lock().unwrap();
        if runtime.closing { return Err("Kollab is quitting.".into()); }
        runtime.close();
        runtime.exit_worker = true;
    }
    let stopped = stop_spawned(app);
    if app.state::<Spawned>().0.lock().unwrap().finish_exit(stopped) { return Ok(()); }
    Err("Some owned processes could not be stopped safely, so the update was not installed.".into())
}

/// The swap failed after the runtime stopped: the old app is back, start it again.
fn resume_after_update(app: &AppHandle) {
    app.state::<Spawned>().0.lock().unwrap().finish_exit(false);
    start_boot(app, true);
}

/// Ctrl+C, `kill` or a closed terminal quit through the same exit path as
/// Quit (without the question), so a spawned web UI is never orphaned.
fn quit_on_signals(app: &AppHandle) {
    use tokio::signal::unix::{signal, SignalKind};
    for kind in [SignalKind::interrupt(), SignalKind::terminate(), SignalKind::hangup()] {
        let app = app.clone();
        tauri::async_runtime::spawn(async move {
            if let Ok(mut stream) = signal(kind) {
                stream.recv().await;
                eprintln!("kollab-desktop: quit signal received");
                exit_now(&app);
            }
        });
    }
}

/// Http(s) and mailto links open in the default browser; other schemes are dropped.
fn open_external(url: &Url) {
    if matches!(url.scheme(), "http" | "https" | "mailto") {
        eprintln!("kollab-desktop: opening {url} in the default browser");
        let url = url.to_string();
        #[cfg(target_os = "macos")]
        let opener = "open";
        #[cfg(target_os = "linux")]
        let opener = "xdg-open";
        #[cfg(target_os = "windows")]
        let opener = "explorer.exe";
        std::thread::spawn(move || Command::new(opener).arg(url).status());
    }
}

/// The window shows only the bundled status page and the web UI's own origin.
fn allowed_in_window(url: &Url) -> bool {
    matches!(url.scheme(), "tauri" | "about" | "data" | "blob") || url.origin() == ui_url().origin()
}

/// The macOS menu bar: the usual items, except that Quit goes through `request_quit`.
#[cfg(target_os = "macos")]
fn app_menu(app: &AppHandle) -> tauri::Result<Menu<Wry>> {
    Menu::with_items(
        app,
        &[
            &Submenu::with_items(
                app,
                "Kollab",
                true,
                &[
                    &PredefinedMenuItem::about(app, None, None)?,
                    &MenuItem::with_id(app, "update", "Check for Updates…", true, None::<&str>)?,
                    &PredefinedMenuItem::separator(app)?,
                    &PredefinedMenuItem::hide(app, None)?,
                    &PredefinedMenuItem::hide_others(app, None)?,
                    &PredefinedMenuItem::show_all(app, None)?,
                    &PredefinedMenuItem::separator(app)?,
                    &MenuItem::with_id(app, "quit", "Quit Kollab", true, Some("CmdOrCtrl+Q"))?,
                ],
            )?,
            &Submenu::with_items(
                app,
                "Edit",
                true,
                &[
                    &PredefinedMenuItem::undo(app, None)?,
                    &PredefinedMenuItem::redo(app, None)?,
                    &PredefinedMenuItem::separator(app)?,
                    &PredefinedMenuItem::cut(app, None)?,
                    &PredefinedMenuItem::copy(app, None)?,
                    &PredefinedMenuItem::paste(app, None)?,
                    &PredefinedMenuItem::select_all(app, None)?,
                ],
            )?,
            &Submenu::with_items(
                app,
                "Window",
                true,
                &[&PredefinedMenuItem::minimize(app, None)?, &PredefinedMenuItem::close_window(app, None)?],
            )?,
        ],
    )
}

#[cfg(not(target_os = "macos"))]
fn app_menu(app: &AppHandle) -> tauri::Result<Menu<Wry>> {
    Menu::with_items(app, &[
        &Submenu::with_items(app, "Kollab", true, &[
            &MenuItem::with_id(app, "open", "Open Kollab", true, None::<&str>)?,
            &MenuItem::with_id(app, "quit", "Quit Kollab", true, Some("CmdOrCtrl+Q"))?,
        ])?,
        &Submenu::with_items(app, "Edit", true, &[
            &PredefinedMenuItem::undo(app, None)?,
            &PredefinedMenuItem::redo(app, None)?,
            &PredefinedMenuItem::separator(app)?,
            &PredefinedMenuItem::cut(app, None)?,
            &PredefinedMenuItem::copy(app, None)?,
            &PredefinedMenuItem::paste(app, None)?,
            &PredefinedMenuItem::select_all(app, None)?,
        ])?,
    ])
}

/// The menu bar (tray) icon: Open Kollab, Launch at Login, Check for Updates, Quit Kollab.
fn build_tray(app: &tauri::App) -> tauri::Result<()> {
    let login_enabled = app.autolaunch().is_enabled().unwrap_or(false);
    let login = CheckMenuItem::with_id(app, "login", "Launch at Login", true, login_enabled, None::<&str>)?;
    let menu = Menu::with_items(
        app,
        &[
            &MenuItem::with_id(app, "open", "Open Kollab", true, None::<&str>)?,
            &login,
            &MenuItem::with_id(app, "update", "Check for Updates…", true, None::<&str>)?,
            &PredefinedMenuItem::separator(app)?,
            &MenuItem::with_id(app, "quit", "Quit Kollab", true, None::<&str>)?,
        ],
    )?;
    app.manage(LoginItem(login));
    TrayIconBuilder::with_id("main")
        .icon(tauri::image::Image::from_bytes(include_bytes!("../icons/tray.png"))?)
        .icon_as_template(cfg!(target_os = "macos"))
        .tooltip("Kollab")
        .menu(&menu)
        .build(app)?;
    Ok(())
}

/// Test hook: presses the window's red close button. `performClose:` takes the
/// same AppKit path as a click (windowShouldClose:), which `Window::close` skips.
#[cfg(target_os = "macos")]
fn press_close_button(app: &AppHandle) {
    let Some(ns_window) = app.get_webview_window("main").and_then(|win| win.ns_window().ok()) else {
        return;
    };
    let sender = std::ptr::null_mut::<AnyObject>();
    unsafe {
        let () = msg_send![ns_window.cast::<AnyObject>(), performClose: sender];
    }
}

#[cfg(not(target_os = "macos"))]
fn press_close_button(app: &AppHandle) {
    if let Some(win) = app.get_webview_window("main") { let _ = win.close(); }
}

#[tauri::command]
fn retry(app: AppHandle) {
    start_boot(&app, true);
}

fn main() {
    tauri::Builder::default()
        .plugin(tauri_plugin_single_instance::init(|app, _args, _cwd| {
            eprintln!("kollab-desktop: second launch, focusing the existing window");
            show_window(app);
        }))
        // Never restore "hidden": a window closed to the menu bar must open on the next launch.
        .plugin(
            tauri_plugin_window_state::Builder::default()
                .with_state_flags(StateFlags::all() & !StateFlags::VISIBLE)
                .build(),
        )
        .plugin(tauri_plugin_dialog::init())
        .plugin(tauri_plugin_autostart::init(MacosLauncher::LaunchAgent, Some(vec!["--hidden"])))
        .manage(Spawned::default())
        .manage(updater::Updater::default())
        .menu(app_menu)
        .on_menu_event(|app, event| match event.id().as_ref() {
            "open" => show_window(app),
            "quit" => request_quit(app),
            "login" => toggle_launch_at_login(app),
            "update" => updater::check(app, true),
            _ => {}
        })
        .on_window_event(|window, event| {
            if let WindowEvent::CloseRequested { api, .. } = event {
                api.prevent_close();
                let _ = window.hide();
                menu_bar_only(window.app_handle(), true);
                eprintln!("kollab-desktop: window closed; hidden, the app keeps running in the menu bar");
            }
        })
        .invoke_handler(tauri::generate_handler![retry])
        .setup(|app| {
            let hidden = std::env::args().any(|arg| arg == "--hidden"); // Launch at Login
            let handle = app.handle().clone();
            WebviewWindowBuilder::new(app, "main", WebviewUrl::App("index.html".into()))
                .title("Kollab")
                .inner_size(1280.0, 800.0)
                .min_inner_size(390.0, 600.0)
                .visible(!hidden)
                .on_navigation(move |url| {
                    // The web UI's update banner hands a Kollab.app update to the app.
                    if url.scheme() == "kollab-desktop" && url.host_str() == Some("update") {
                        updater::check(&handle, true);
                        return false;
                    }
                    if url.scheme() == "kollab-desktop" {
                        let report: Vec<_> = url.query_pairs().collect();
                        eprintln!("kollab-desktop: page report: {report:?}");
                        // Test hook: press the red close button a second later, as the user does.
                        if std::env::var_os("KOLLAB_DESKTOP_SELFTEST_CLOSE").is_some() {
                            let app = handle.clone();
                            std::thread::spawn(move || {
                                std::thread::sleep(Duration::from_secs(1));
                                let main = app.clone();
                                let _ = app.run_on_main_thread(move || press_close_button(&main));
                            });
                        }
                        return false;
                    }
                    let allowed = allowed_in_window(url);
                    if !allowed {
                        open_external(url);
                    }
                    allowed
                })
                .on_new_window(|url, _features| {
                    open_external(&url);
                    NewWindowResponse::Deny
                })
                .on_page_load(|win, payload| {
                    eprintln!("kollab-desktop: page {:?}: {}", payload.event(), payload.url());
                    if matches!(payload.event(), PageLoadEvent::Finished)
                        && payload.url().origin() == ui_url().origin()
                    {
                        if let Err(err) = win.eval(format!("{DESKTOP_JS}\n{REPORT_JS}")) {
                            eprintln!("kollab-desktop: could not run the page report: {err}");
                        }
                    }
                })
                .build()?;
            build_tray(app)?;
            if hidden {
                menu_bar_only(app.handle(), true);
            }
            #[cfg(target_os = "macos")]
            take_quit_event(app.handle());
            let handle = app.handle().clone();
            quit_on_signals(&handle);
            start_boot(&handle, false);
            updater::watch(&handle);
            Ok(())
        })
        .build(tauri::generate_context!())
        .expect("error while building Kollab")
        .run(|app, event| match event {
            #[cfg(target_os = "macos")]
            RunEvent::Reopen { .. } => show_window(app),
            // A menu bar app outlives its window: only an explicit exit (Quit, a signal) has a code.
            RunEvent::ExitRequested { code, api, .. } => {
                eprintln!("kollab-desktop: exit requested (code {code:?})");
                let ready = app.state::<Spawned>().0.lock().unwrap().exit_ready;
                if code.is_none() {
                    api.prevent_exit();
                } else if !ready {
                    api.prevent_exit();
                    exit_now(app);
                }
            }
            RunEvent::WindowEvent { label, event: WindowEvent::Destroyed, .. } => {
                eprintln!("kollab-desktop: window {label} destroyed");
            }
            RunEvent::Exit => {
                app.state::<Spawned>().0.lock().unwrap().close();
                // Normal exits already drained before app.exit. Forced OS
                // termination can bypass ExitRequested; only best effort is
                // possible once this final, non-cancellable event is reached.
                if !stop_spawned(app) {
                    eprintln!("kollab-desktop: OS exit interrupted owned cleanup; no force used");
                }
            }
            _ => {}
        });
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::net::TcpListener;

    #[test]
    fn repeated_retry_and_confirmed_quit_cannot_launch_a_late_child() {
        let mut runtime = Runtime::<u32>::default();
        let operation = runtime.begin().unwrap();
        assert_eq!(runtime.begin(), None);
        let Quit::Confirm(prompt) = runtime.request_quit() else { panic!("startup must ask before quit"); };
        assert!(runtime.confirm_quit(prompt, true));
        let mut launches = 0;
        assert!(!runtime.spawn(operation, || { launches += 1; Ok(7) }).unwrap());
        runtime.finish(operation);
        assert_eq!(launches, 0);
        assert!(runtime.owned.is_none());
        assert_eq!(runtime.begin(), None);
    }

    #[test]
    fn quit_after_publish_keeps_the_exact_owned_child_for_cleanup() {
        let mut runtime = Runtime::default();
        let operation = runtime.begin().unwrap();
        assert!(runtime.spawn(operation, || Ok("original child")).unwrap());
        let Quit::Confirm(prompt) = runtime.request_quit() else { panic!("owned child requires confirmation"); };
        assert!(runtime.confirm_quit(prompt, true));
        runtime.finish(operation);
        assert_eq!(runtime.owned, Some("original child"));
        assert_eq!(runtime.begin(), None);
        assert!(!runtime.spawn(operation, || panic!("replacement must never spawn")).unwrap());
    }

    #[test]
    fn failed_spawn_can_retry_without_an_old_worker_publishing_or_finishing_it() {
        let mut runtime = Runtime::default();
        let first = runtime.begin().unwrap();
        assert!(runtime.spawn(first, || Err(std::io::Error::other("fixture spawn failure"))).is_err());
        runtime.finish(first);
        let second = runtime.begin().unwrap();
        assert!(!runtime.spawn(first, || panic!("stale worker must not run")).unwrap());
        runtime.finish(first);
        assert!(runtime.current(second));
        assert!(runtime.spawn(second, || Ok("retry child")).unwrap());
        runtime.finish(second);
        assert_eq!(runtime.owned, Some("retry child"));
    }

    #[test]
    fn failed_cleanup_cannot_replace_or_lose_the_previous_child() {
        let mut runtime = Runtime::default();
        runtime.owned = Some("still owned");
        let retry = runtime.begin().unwrap();
        assert!(runtime.spawn(retry, || panic!("uncleaned runtime must not be replaced")).is_err());
        runtime.finish(retry);
        assert_eq!(runtime.owned, Some("still owned"));
        let next = runtime.begin().unwrap();
        // Only successful cleanup removes the old slot, before the next spawn.
        assert_eq!(runtime.owned.take(), Some("still owned"));
        assert!(runtime.spawn(next, || Ok("new child")).unwrap());
    }

    #[test]
    fn cancel_quit_keeps_the_runtime_but_a_late_dialog_cannot_undo_signal_exit() {
        let mut runtime = Runtime::default();
        runtime.owned = Some("owned");
        let Quit::Confirm(first) = runtime.request_quit() else { panic!("expected confirmation"); };
        assert_eq!(runtime.request_quit(), Quit::Ignore);
        assert!(!runtime.confirm_quit(first, false));
        assert!(!runtime.closing);
        assert_eq!(runtime.owned, Some("owned"));
        let Quit::Confirm(second) = runtime.request_quit() else { panic!("expected second confirmation"); };
        runtime.close();
        assert!(!runtime.confirm_quit(second, false));
        assert!(runtime.closing);
        assert_eq!(runtime.begin(), None);
        assert_eq!(runtime.owned, Some("owned"));
    }

    #[test]
    fn attaching_then_quitting_never_acquires_an_external_runtime() {
        let mut runtime = Runtime::<u32>::default();
        let attach = runtime.begin().unwrap();
        runtime.finish(attach);
        assert_eq!(runtime.request_quit(), Quit::Exit);
        assert!(runtime.owned.is_none());
        assert_eq!(runtime.begin(), None);
    }

    #[test]
    fn spawn_and_publish_are_atomic_with_the_quit_fence() {
        use std::sync::{Arc, mpsc};
        let runtime = Arc::new(Mutex::new(Runtime::<u32>::default()));
        let operation = runtime.lock().unwrap().begin().unwrap();
        let (entered, entry) = mpsc::channel();
        let (resume, resumed) = mpsc::channel();
        let worker_state = runtime.clone();
        let worker = std::thread::spawn(move || {
            worker_state.lock().unwrap().spawn(operation, || {
                entered.send(()).unwrap();
                resumed.recv_timeout(Duration::from_secs(2)).unwrap();
                Ok(41)
            }).unwrap()
        });
        entry.recv_timeout(Duration::from_secs(2)).unwrap();
        assert!(runtime.try_lock().is_err());
        resume.send(()).unwrap();
        assert!(worker.join().unwrap());
        runtime.lock().unwrap().close();
        let mut state = runtime.lock().unwrap();
        assert_eq!(state.owned, Some(41));
        assert!(!state.spawn(operation, || panic!("post-quit child")).unwrap());
    }

    #[test]
    fn failed_quit_retains_ownership_and_invalidates_the_cancelled_startup() {
        let mut runtime = Runtime::default();
        let old = runtime.begin().unwrap();
        assert!(runtime.spawn(old, || Ok("owned child")).unwrap());
        runtime.close();
        assert!(!runtime.finish_exit(false));
        assert!(!runtime.exit_ready);
        assert_eq!(runtime.owned, Some("owned child"));
        let retry = runtime.begin().unwrap();
        runtime.finish(old);
        assert!(runtime.current(retry));
        assert!(!runtime.spawn(old, || panic!("cancelled startup")).unwrap());
        runtime.close();
        assert!(!runtime.finish_exit(true)); // an acknowledgement cannot discard a surviving owner
        runtime.close();
        assert_eq!(runtime.owned.take(), Some("owned child"));
        assert!(runtime.finish_exit(true));
        assert!(runtime.exit_ready);
        assert_eq!(runtime.begin(), None);
    }

    #[test]
    fn linux_stat_parser_handles_command_parentheses_and_rejects_partial_records() {
        assert_eq!(stat_process_group("17 (a command (with) spaces) S 12 41 17 0").unwrap(), 41);
        assert_eq!(stat_process_group("17 (closing) ) name) Z 12 41 17 0").unwrap(), 41);
        assert!(stat_process_group("17 (truncated").is_err());
        assert!(stat_process_group("17 (name) S 12 invalid").is_err());
    }

    #[test]
    fn linux_sidecar_lookup_matches_debian_and_extracted_appimage_layouts() {
        assert_eq!(linux_bundled_kollab(Path::new("/usr/bin/kollab-desktop")),
            Some(PathBuf::from("/usr/share/kollab/runtime/kollab")));
        assert_eq!(linux_bundled_kollab(Path::new("/tmp/Kollab.AppDir/usr/bin/kollab-desktop")),
            Some(PathBuf::from("/tmp/Kollab.AppDir/usr/share/kollab/runtime/kollab")));
        assert_eq!(linux_bundled_kollab(Path::new("kollab-desktop")), None);
    }

    #[test]
    fn macos_sidecar_lookup_uses_bundle_resources() {
        assert_eq!(macos_bundled_kollab(Path::new("/Applications/Kollab.app/Contents/MacOS/kollab-desktop")),
            Some(PathBuf::from("/Applications/Kollab.app/Contents/Resources/kollab")));
        assert_eq!(macos_bundled_kollab(Path::new("kollab-desktop")), None);
    }

    /// These kernel tests create only private groups with short natural
    /// lifetimes, even if an assertion fails. No Kollab, GUI or user runtime.
    fn kernel_child(script: &str) -> Child {
        let mut command = Command::new("/bin/sh");
        command.args(["-c", script]).stdin(Stdio::null())
            .stdout(Stdio::piped()).stderr(Stdio::null());
        unsafe {
            command.pre_exec(|| {
                libc::signal(libc::SIGINT, libc::SIG_DFL);
                if libc::setsid() == -1 { return Err(std::io::Error::last_os_error()); }
                Ok(())
            });
        }
        command.spawn().unwrap()
    }

    #[test]
    fn kernel_wnowait_reserves_the_exited_child_and_reaped_handles_refuse_signals() {
        let mut child = kernel_child("exit 0");
        let deadline = Instant::now() + Duration::from_secs(2);
        while !child_exited(&child).unwrap() {
            assert!(Instant::now() < deadline);
            std::thread::sleep(Duration::from_millis(10));
        }
        assert!(child_exited(&child).unwrap()); // still waitable: observation did not reap it
        assert!(child.wait().unwrap().success());
        assert_eq!(child_exited(&child).unwrap_err().raw_os_error(), Some(libc::ECHILD));
        assert_eq!(signal_owned_group(&child, libc::SIGINT).unwrap_err().raw_os_error(), Some(libc::ECHILD));
    }

    #[test]
    fn kernel_owned_group_stops_with_sigint_and_is_reaped() {
        use std::os::unix::process::ExitStatusExt;
        let mut child = kernel_child("exec /bin/sleep 2");
        assert!(!child_exited(&child).unwrap());
        assert!(stop_child(&mut child).unwrap());
        assert_eq!(child.wait().unwrap().signal(), Some(libc::SIGINT));
        assert_eq!(child_exited(&child).unwrap_err().raw_os_error(), Some(libc::ECHILD));
    }

    #[test]
    fn kernel_peer_listing_retains_the_exited_leader_until_its_group_drains() {
        use std::io::{BufRead, BufReader};
        let mut child = kernel_child("/bin/sleep 0.4 & printf 'ready\\n'; wait");
        let mut ready = String::new();
        BufReader::new(child.stdout.take().unwrap()).read_line(&mut ready).unwrap();
        assert_eq!(ready, "ready\n");
        assert!(group_has_peers(child.id() as libc::pid_t).unwrap());
        assert!(stop_child(&mut child).unwrap());
        assert_eq!(child_exited(&child).unwrap_err().raw_os_error(), Some(libc::ECHILD));
    }

    /// Serves one canned HTTP/1.0 response on a free port and returns its URL.
    fn serve_once(body: &'static str) -> Url {
        let listener = TcpListener::bind("127.0.0.1:0").unwrap();
        let port = listener.local_addr().unwrap().port();
        std::thread::spawn(move || {
            let (mut conn, _) = listener.accept().unwrap();
            let _ = conn.read(&mut [0; 1024]);
            let _ = write!(conn, "HTTP/1.0 200 OK\r\nContent-Type: application/json\r\n\r\n{body}");
        });
        format!("http://127.0.0.1:{port}/").parse().unwrap()
    }

    #[test]
    fn webui_running_needs_the_kollab_openapi_title() {
        assert!(webui_running(&serve_once(r#"{"info":{"title":"Kollab WebUI"}}"#)));
        assert!(!webui_running(&serve_once(r#"{"info":{"title":"Something Else"}}"#)));
        let closed = TcpListener::bind("127.0.0.1:0").unwrap().local_addr().unwrap().port();
        assert!(!webui_running(&format!("http://127.0.0.1:{closed}/").parse().unwrap()));
    }

    #[test]
    fn kollab_version_reads_the_last_word() {
        let script = std::env::temp_dir().join("kollab-desktop-test-version.sh");
        std::fs::write(&script, "#!/bin/sh\necho kollab-aarch64-apple-darwin 0.15.7\n").unwrap();
        Command::new("chmod").arg("+x").arg(&script).status().unwrap();
        assert_eq!(kollab_version(&script), Some(vec![0, 15, 7]));
        assert!(Some(vec![0, 16, 0]) > kollab_version(&script));
        assert_eq!(kollab_version(Path::new("/nonexistent/kollab")), None);
    }
}
