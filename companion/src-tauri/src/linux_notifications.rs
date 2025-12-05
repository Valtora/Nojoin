#[cfg(target_os = "linux")]
use log::error;
#[cfg(target_os = "linux")]
use notify_rust::{Notification, Timeout};
#[cfg(target_os = "linux")]
use tauri::AppHandle;
#[cfg(target_os = "linux")]
use tauri_plugin_updater::UpdaterExt;

#[cfg(target_os = "linux")]
pub fn show_update_notification(app: AppHandle, version: String) {
    let app_handle = app.clone();

    // notify-rust's wait_for_action blocks, so this is fine in the thread.
    let notification = Notification::new()
        .summary("Update Available")
        .body(&format!("Version {} is available.", version))
        .icon("dialog-information")
        .appname("Nojoin Companion")
        .timeout(Timeout::Never) // Keep it until user interacts
        .action("update", "Update Now")
        .action("cancel", "Not Now")
        .show();

    match notification {
        Ok(handle) => {
            handle.wait_for_action(move |action| {
                if action == "update" {
                    trigger_update(app_handle.clone());
                }
            });
        }
        Err(e) => {
            error!("Failed to show notification: {:?}", e);
        }
    }
}

#[cfg(target_os = "linux")]
fn trigger_update(app: AppHandle) {
    tauri::async_runtime::spawn(async move {
        let updater = match app.updater() {
            Ok(u) => u,
            Err(e) => {
                error!("Failed to get updater: {}", e);
                return;
            }
        };

        match updater.check().await {
            Ok(Some(update)) => {
                if let Err(e) = update.download_and_install(|_, _| {}, || {}).await {
                    error!("Failed to install update: {}", e);
                } else {
                    app.restart();
                }
            }
            Ok(None) => {
                // No update available
            }
            Err(e) => error!("Failed to check update: {}", e),
        }
    });
}
