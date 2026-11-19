//! Ledgerly for Windows: one window showing the app's own screens (bundled, served at
//! https://tauri.localhost). The screens talk to your Ledgerly server over https with this
//! PC's device token, exactly like the Android app.
use tauri::webview::{DownloadEvent, WebviewWindowBuilder};
use tauri::{Url, WebviewUrl};

/// Only Ledgerly's own pages may load in the window; any other address (a WhatsApp or web
/// link) is refused here and opened in the default browser by the screens instead.
fn is_own_page(url: &Url) -> bool {
    match url.scheme() {
        "tauri" | "about" | "blob" | "data" => true,
        "http" | "https" => matches!(url.host_str(), Some("tauri.localhost") | Some("ipc.localhost")),
        _ => false,
    }
}

#[cfg_attr(mobile, tauri::mobile_entry_point)]
pub fn run() {
    tauri::Builder::default()
        .plugin(tauri_plugin_opener::init())
        .setup(|app| {
            WebviewWindowBuilder::new(app, "main", WebviewUrl::App("index.html".into()))
                .title("Ledgerly")
                .inner_size(1280.0, 820.0)
                .min_inner_size(380.0, 560.0)
                .center()
                .use_https_scheme(true)
                .on_navigation(|url| is_own_page(url))
                .on_download(|_webview, event| match event {
                    // documents and CSV exports: the normal save to the Downloads folder
                    DownloadEvent::Requested { .. } => true,
                    _ => true,
                })
                .build()?;
            Ok(())
        })
        .run(tauri::generate_context!())
        .expect("error while running Ledgerly");
}
