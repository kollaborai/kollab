//! Kollab.app updates itself from the latest GitHub release.
//!
//! Like the CLI binary's /upgrade (kollabor/updates/auto_update.py:_upgrade_binary):
//! the release's own SHA-256 is the integrity check, since the app is signed ad hoc.
//! Install downloads and verifies everything first, stops the owned runtime the way
//! Quit does, swaps the bundle in one rename, and relaunches the new app.

use std::path::{Path, PathBuf};
use std::process::{Command, Stdio};
use std::sync::atomic::{AtomicBool, Ordering};
use std::sync::Mutex;
use std::time::Duration;

use tauri::{AppHandle, Manager};
use tauri_plugin_dialog::{DialogExt, MessageDialogButtons, MessageDialogKind};

const LATEST_RELEASE: &str = "https://api.github.com/repos/kollaborai/kollab/releases/latest";
const DOWNLOAD_PAGE: &str = "https://github.com/kollaborai/kollab/releases/latest";
const CHECK_EVERY: Duration = Duration::from_secs(6 * 60 * 60);

/// One prompt or install at a time; a version the user put off is not asked again
/// by the background check until the app restarts (Check for Updates still asks).
#[derive(Default)]
pub struct Updater {
    busy: AtomicBool,
    later: Mutex<Option<String>>,
}

#[derive(Debug, PartialEq)]
pub struct Release {
    pub version: String,
    pub page: String,
    pub zip: Option<(String, String)>, // (zip url, .sha256 url) for this Mac
}

/// KOLLAB_DESKTOP_RELEASE_URL points the updater at a test release (file:// works).
fn release_url() -> String {
    std::env::var("KOLLAB_DESKTOP_RELEASE_URL").unwrap_or_else(|_| LATEST_RELEASE.into())
}

pub fn parse_version(text: &str) -> Option<Vec<u32>> {
    text.trim().trim_start_matches('v').split('.').map(|part| part.parse().ok()).collect()
}

fn platform() -> &'static str {
    if cfg!(target_arch = "aarch64") { "macos-aarch64" } else { "macos-x86_64" }
}

/// The release's version and, when it has one for `platform`, its app zip + checksum.
pub fn parse_release(json: &serde_json::Value, platform: &str) -> Option<Release> {
    let version = json["tag_name"].as_str()?.trim_start_matches('v').to_string();
    parse_version(&version)?;
    let asset = |name: &str| {
        json["assets"].as_array()?.iter()
            .find(|a| a["name"] == name)
            .and_then(|a| a["browser_download_url"].as_str().map(String::from))
    };
    let base = format!("kollab-desktop-{platform}-{version}.app.zip");
    let zip = asset(&base).zip(asset(&format!("{base}.sha256")));
    let page = json["html_url"].as_str().unwrap_or(DOWNLOAD_PAGE).to_string();
    Some(Release { version, page, zip })
}

fn curl(url: &str) -> Command {
    let mut command = Command::new("/usr/bin/curl");
    command.args(["-fsSL", "--proto", "=https,file", "-A", "kollab-desktop", url]).stdin(Stdio::null());
    command
}

fn fetch_release() -> Result<Release, String> {
    let out = curl(&release_url()).args(["--max-time", "20"]).output().map_err(|e| e.to_string())?;
    if !out.status.success() {
        return Err(format!("could not reach GitHub: {}", String::from_utf8_lossy(&out.stderr).trim()));
    }
    let json: serde_json::Value = serde_json::from_slice(&out.stdout).map_err(|e| e.to_string())?;
    parse_release(&json, platform()).ok_or_else(|| "the latest release has no version".into())
}

/// Kollab.app/Contents/MacOS/kollab-desktop -> Kollab.app, or None outside a bundle.
pub fn bundle_of(executable: &Path) -> Option<PathBuf> {
    let bundle = executable.parent()?.parent()?.parent()?;
    (bundle.extension()? == "app").then(|| bundle.to_path_buf())
}

fn running_bundle() -> Option<PathBuf> {
    bundle_of(&std::env::current_exe().ok()?)
}

/// Every few hours, while the app runs from a bundle.
pub fn watch(app: &AppHandle) {
    let Some(bundle) = running_bundle() else { return; };
    remove_stale_updates(&bundle);
    let app = app.clone();
    std::thread::spawn(move || loop {
        std::thread::sleep(Duration::from_secs(30));
        check(&app, false);
        std::thread::sleep(CHECK_EVERY);
    });
}

/// A staging folder another process left (crashed, or killed mid-cleanup) holds
/// a whole app; single-instance means no other Kollab is using one.
fn remove_stale_updates(bundle: &Path) {
    let Some(entries) = bundle.parent().and_then(|dir| std::fs::read_dir(dir).ok()) else { return; };
    let ours = format!(".Kollab-update-{}", std::process::id());
    for entry in entries.flatten() {
        let name = entry.file_name().to_string_lossy().into_owned();
        if name.starts_with(".Kollab-update-") && name != ours {
            eprintln!("kollab-desktop: removing a stale update folder {name}");
            let _ = std::fs::remove_dir_all(entry.path());
        }
    }
}

/// Asks about a newer release. `manual` (the menu, the web UI's banner) also
/// reports "up to date" and errors, and asks about a version put off earlier.
pub fn check(app: &AppHandle, manual: bool) {
    let updater = app.state::<Updater>();
    if updater.busy.swap(true, Ordering::SeqCst) { return; }
    let app = app.clone();
    std::thread::spawn(move || {
        let done = || app.state::<Updater>().busy.store(false, Ordering::SeqCst);
        let current = app.package_info().version.to_string();
        let release = match fetch_release() {
            Ok(release) => release,
            Err(err) => {
                eprintln!("kollab-desktop: update check failed: {err}");
                if manual { tell(&app, "Could not check for updates", &err, MessageDialogKind::Error); }
                return done();
            }
        };
        eprintln!("kollab-desktop: running {current}, latest release {}", release.version);
        if parse_version(&release.version) <= parse_version(&current) {
            if manual { tell(&app, "Kollab is up to date", &format!("Kollab {current} is the latest version."), MessageDialogKind::Info); }
            return done();
        }
        let later = app.state::<Updater>().later.lock().unwrap().clone();
        if !manual && later.as_deref() == Some(release.version.as_str()) { return done(); }
        let installable = cfg!(target_os = "macos") && release.zip.is_some() && running_bundle().is_some();
        let (text, yes) = if installable {
            (format!("You have {current}. Install it now? Kollab restarts, and the agents it started stop."), "Install")
        } else {
            (format!("You have {current}. Download it from the release page."), "Download")
        };
        let handle = app.clone();
        app.dialog()
            .message(text)
            .title(format!("Kollab {} is available", release.version))
            .kind(MessageDialogKind::Info)
            .buttons(MessageDialogButtons::OkCancelCustom(yes.into(), "Later".into()))
            .show(move |accepted| {
                let state = handle.state::<Updater>();
                if !accepted {
                    *state.later.lock().unwrap() = Some(release.version);
                    state.busy.store(false, Ordering::SeqCst);
                } else if installable {
                    let app = handle.clone();
                    std::thread::spawn(move || install(&app, &release));
                } else {
                    crate::open_external(&release.page.parse().expect("valid url"));
                    state.busy.store(false, Ordering::SeqCst);
                }
            });
    });
}

fn tell(app: &AppHandle, title: &str, text: &str, kind: MessageDialogKind) {
    app.dialog().message(text).title(title).kind(kind).show(|_| {});
}

/// Never returns on success: the new app is relaunched and this one exits.
fn install(app: &AppHandle, release: &Release) {
    let result = (|| {
        let bundle = running_bundle().ok_or("Kollab is not running from Kollab.app")?;
        let staged = Staged::prepare(&bundle, release)?;
        crate::stop_for_update(app)?;
        if let Err(err) = staged.swap(&bundle) {
            crate::resume_after_update(app);
            return Err(err);
        }
        drop(staged); // the old app; app.exit would cut this cleanup short
        relaunch_after_exit(&bundle);
        app.exit(0);
        Ok::<(), String>(())
    })();
    if let Err(err) = result {
        eprintln!("kollab-desktop: update failed: {err}");
        let text = format!("{err}\n\nKollab {} is on the release page: {}", release.version, release.page);
        tell(app, "Could not update Kollab", &text, MessageDialogKind::Error);
    }
    app.state::<Updater>().busy.store(false, Ordering::SeqCst);
}

/// The new app, downloaded and checked, in a hidden folder beside the running one
/// (the same volume, so the swap is a rename). Removed when dropped.
struct Staged {
    dir: PathBuf,
    app: PathBuf,
}

impl Drop for Staged {
    fn drop(&mut self) {
        let _ = std::fs::remove_dir_all(&self.dir);
    }
}

fn run(command: &mut Command, what: &str) -> Result<String, String> {
    let out = command.stdin(Stdio::null()).output().map_err(|e| format!("{what}: {e}"))?;
    if !out.status.success() {
        let err = String::from_utf8_lossy(&out.stderr);
        return Err(format!("{what} failed: {}", err.trim()));
    }
    Ok(String::from_utf8_lossy(&out.stdout).trim().to_string())
}

fn plist_value(app: &Path, key: &str) -> Result<String, String> {
    run(Command::new("/usr/libexec/PlistBuddy").args(["-c", &format!("Print :{key}")])
        .arg(app.join("Contents/Info.plist")), &format!("reading {key}"))
}

impl Staged {
    fn prepare(bundle: &Path, release: &Release) -> Result<Self, String> {
        let (zip_url, sum_url) = release.zip.as_ref().ok_or("the release has no Kollab.app for this Mac")?;
        let parent = bundle.parent().ok_or("Kollab.app has no folder")?;
        let dir = parent.join(format!(".Kollab-update-{}", std::process::id()));
        let _ = std::fs::remove_dir_all(&dir);
        std::fs::create_dir(&dir).map_err(|e| format!("cannot write to {}: {e}", parent.display()))?;
        let mut staged = Staged { app: PathBuf::new(), dir };
        let zip = staged.dir.join("Kollab.app.zip");
        run(curl(zip_url).arg("-o").arg(&zip), "downloading the update")?;
        let expected = run(&mut curl(sum_url), "downloading its checksum")?;
        let expected = expected.split_whitespace().next().unwrap_or_default().to_lowercase();
        let actual = run(Command::new("/usr/bin/shasum").args(["-a", "256"]).arg(&zip), "checksumming")?;
        let actual = actual.split_whitespace().next().unwrap_or_default().to_string();
        if expected.len() != 64 || actual != expected {
            return Err(format!("the download's SHA-256 {actual} does not match the release's {expected}"));
        }
        let unpacked = staged.dir.join("new");
        run(Command::new("/usr/bin/ditto").args(["-x", "-k"]).arg(&zip).arg(&unpacked), "unpacking")?;
        let app = unpacked.join(bundle.file_name().ok_or("Kollab.app has no name")?);
        staged.app = if app.is_dir() { app } else { unpacked.join("Kollab.app") };
        verify(&staged.app, &plist_value(bundle, "CFBundleIdentifier")?, &release.version)?;
        Ok(staged)
    }

    /// One atomic exchange (APFS), else two renames; any failure puts the old app back.
    fn swap(&self, bundle: &Path) -> Result<(), String> {
        let old = if exchange(&self.app, bundle).is_ok() {
            self.app.clone() // the exchange left the old app at the staged path
        } else {
            let old = self.dir.join("old.app");
            std::fs::rename(bundle, &old).map_err(|e| format!("cannot move the old app aside: {e}"))?;
            if let Err(err) = std::fs::rename(&self.app, bundle) {
                let _ = std::fs::rename(&old, bundle);
                return Err(format!("cannot move the new app in: {err}"));
            }
            old
        };
        let installed = run(Command::new("/usr/bin/codesign").args(["--verify", "--deep", "--strict"]).arg(bundle),
            "verifying the installed app");
        if installed.is_err() && exchange(&old, bundle).is_err() {
            let _ = std::fs::rename(bundle, self.dir.join("failed.app"));
            let _ = std::fs::rename(&old, bundle);
        }
        installed.map(|_| ())
    }
}

/// The new app is sealed, the same app, and the version the release names.
fn verify(app: &Path, identifier: &str, version: &str) -> Result<(), String> {
    if !app.join("Contents/Info.plist").is_file() { return Err("the download holds no Kollab.app".into()); }
    run(Command::new("/usr/bin/codesign").args(["--verify", "--deep", "--strict"]).arg(app), "verifying the signature")?;
    let found = plist_value(app, "CFBundleIdentifier")?;
    if found != identifier { return Err(format!("the download is {found}, not {identifier}")); }
    let found = plist_value(app, "CFBundleShortVersionString")?;
    if found != version { return Err(format!("the download is version {found}, not {version}")); }
    Ok(())
}

#[cfg(target_os = "macos")]
fn exchange(a: &Path, b: &Path) -> std::io::Result<()> {
    use std::os::unix::ffi::OsStrExt;
    let a = std::ffi::CString::new(a.as_os_str().as_bytes())?;
    let b = std::ffi::CString::new(b.as_os_str().as_bytes())?;
    match unsafe { libc::renamex_np(a.as_ptr(), b.as_ptr(), libc::RENAME_SWAP) } {
        0 => Ok(()),
        _ => Err(std::io::Error::last_os_error()),
    }
}

#[cfg(not(target_os = "macos"))]
fn exchange(_: &Path, _: &Path) -> std::io::Result<()> {
    Err(std::io::ErrorKind::Unsupported.into())
}

/// Opens the new app once this process is gone; launched sooner, the
/// single-instance plugin would hand it to this one, which is exiting.
fn relaunch_after_exit(bundle: &Path) {
    let script = r#"i=0; while kill -0 "$1" 2>/dev/null && [ $i -lt 300 ]; do sleep 0.1; i=$((i+1)); done; exec /usr/bin/open -n "$2""#;
    let _ = Command::new("/bin/sh")
        .args(["-c", script, "relaunch", &std::process::id().to_string()])
        .arg(bundle)
        .stdin(Stdio::null()).stdout(Stdio::null()).stderr(Stdio::null())
        .spawn();
}

#[cfg(test)]
mod tests {
    use super::*;

    fn release_json() -> serde_json::Value {
        let url = |name: &str| serde_json::json!({"name": name, "browser_download_url": format!("https://x/{name}")});
        serde_json::json!({
            "tag_name": "v0.17.0",
            "html_url": "https://github.com/kollaborai/kollab/releases/tag/v0.17.0",
            "assets": [
                url("kollab-desktop-macos-aarch64-0.17.0.app.zip"),
                url("kollab-desktop-macos-aarch64-0.17.0.app.zip.sha256"),
                url("kollab-desktop-macos-x86_64-0.17.0.app.zip"),
            ],
        })
    }

    #[test]
    fn release_picks_this_macs_zip_and_needs_its_checksum() {
        let release = parse_release(&release_json(), "macos-aarch64").unwrap();
        assert_eq!(release.version, "0.17.0");
        assert_eq!(release.zip, Some(("https://x/kollab-desktop-macos-aarch64-0.17.0.app.zip".into(),
            "https://x/kollab-desktop-macos-aarch64-0.17.0.app.zip.sha256".into())));
        assert_eq!(parse_release(&release_json(), "macos-x86_64").unwrap().zip, None); // no .sha256
        assert!(parse_release(&serde_json::json!({"tag_name": "nightly"}), "macos-aarch64").is_none());
    }

    #[test]
    fn versions_compare_numerically() {
        assert!(parse_version("0.16.10") > parse_version("v0.16.9"));
        assert!(parse_version("0.17.0") > parse_version("0.16.1"));
        assert_eq!(parse_version("0.16.1"), parse_version("v0.16.1"));
        assert_eq!(parse_version("0.17.0-rc1"), None);
    }

    #[test]
    fn bundle_is_found_only_inside_an_app() {
        assert_eq!(bundle_of(Path::new("/Applications/Kollab.app/Contents/MacOS/kollab-desktop")),
            Some(PathBuf::from("/Applications/Kollab.app")));
        assert_eq!(bundle_of(Path::new("/repo/desktop/src-tauri/target/debug/kollab-desktop")), None);
    }

    #[cfg(target_os = "macos")]
    #[test]
    fn exchange_swaps_two_folders_in_one_call() {
        let root = std::env::temp_dir().join(format!("kollab-swap-test-{}", std::process::id()));
        let _ = std::fs::remove_dir_all(&root);
        for (name, marker) in [("a", "old"), ("b", "new")] {
            std::fs::create_dir_all(root.join(name)).unwrap();
            std::fs::write(root.join(name).join("marker"), marker).unwrap();
        }
        exchange(&root.join("b"), &root.join("a")).unwrap();
        assert_eq!(std::fs::read_to_string(root.join("a/marker")).unwrap(), "new");
        assert_eq!(std::fs::read_to_string(root.join("b/marker")).unwrap(), "old");
        std::fs::remove_dir_all(&root).unwrap();
    }
}
