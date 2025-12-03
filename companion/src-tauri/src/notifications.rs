use tauri::api::notification::Notification;

pub fn show_notification(summary: &str, body: &str) {
    let _ = Notification::new("com.valtora.nojoin.companion")
        .title(summary)
        .body(body)
        .show();
}
