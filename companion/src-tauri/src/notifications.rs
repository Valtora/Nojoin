use tauri::AppHandle;
use tauri_plugin_notification::NotificationExt;

pub fn show_notification(app: &AppHandle, summary: &str, body: &str) {
    let _ = app.notification()
        .builder()
        .title(summary)
        .body(body)
        .show();
}
