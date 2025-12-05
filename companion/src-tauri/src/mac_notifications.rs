#[cfg(target_os = "macos")]
use log::error;
#[cfg(target_os = "macos")]
use mac_notification_sys::{send_notification, MainButton, NotificationResponse};
#[cfg(target_os = "macos")]
use tauri::AppHandle;
#[cfg(target_os = "macos")]
use tauri_plugin_updater::UpdaterExt;

#[cfg(target_os = "macos")]
pub fn show_update_notification(app: AppHandle, version: String) {
    let _bundle_id = app.config().identifier.clone();
    let title = "Update Available";
    let message = format!("Version {} is available.", version);

    // We run this in a blocking way, but since this function is called from a spawned thread in main.rs, it's fine.
    let response = send_notification(
        &title,
        None,
        &message,
        Some(
            mac_notification_sys::Notification::new()
                .main_button(MainButton::SingleAction("Update Now")),
        ),
    );

    match response {
        Ok(resp) => {
            match resp {
                NotificationResponse::ActionButton(action) => {
                    if action == "Update Now" {
                        trigger_update(app);
                    }
                }
                NotificationResponse::Click => {
                    // Maybe just open the app or do nothing?
                    // For now, we only care about the button.
                }
                _ => {}
            }
        }
        Err(e) => {
            error!("Failed to send notification: {:?}", e);
        }
    }
}

#[cfg(target_os = "macos")]
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
