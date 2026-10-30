//! Kollab app: the desktop menu bar app, or the phone app shell (#369).

#[cfg(desktop)]
mod desktop;

// The updater reaches these through `crate::`, as it did when it lived in main.rs.
#[cfg(desktop)]
pub(crate) use desktop::{open_external, resume_after_update, stop_for_update};

// The link screen, where a loader error sends the phone to scan a new QR code (#369).
#[cfg(target_os = "android")]
const RELINK: &str = "http://tauri.localhost/?relink";
#[cfg(target_os = "ios")]
const RELINK: &str = "tauri://localhost/?relink";

#[cfg_attr(mobile, tauri::mobile_entry_point)]
pub fn run() {
    #[cfg(desktop)]
    desktop::run();
    #[cfg(mobile)]
    tauri::Builder::default()
        .plugin(tauri_plugin_dialog::init())
        .plugin(tauri_plugin_barcode_scanner::init())
        .setup(|app| {
            tauri::WebviewWindowBuilder::new(app, "main", tauri::WebviewUrl::default())
                .initialization_script(format!(
                    "window.__KOLLAB_APP__ = {{ relink() {{ location.replace({RELINK:?}); }} }};"
                ))
                .build()?;
            Ok(())
        })
        .run(tauri::generate_context!())
        .expect("error while running the Kollab phone app");
}
