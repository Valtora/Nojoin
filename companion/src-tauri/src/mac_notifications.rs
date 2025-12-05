#[cfg(target_os = "macos")]
use log::error;
#[cfg(target_os = "macos")]
use mac_notification_sys::{send_notification, MainButton, NotificationResponse};
#[cfg(target_os = "macos")]
use tauri::AppHandle;

#[cfg(target_os = "macos")]
pub fn show_update_notification(app: AppHandle, version: String, url: String) {
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
                .main_button(MainButton::SingleAction("Download")),
        ),
    );

    match response {
        Ok(resp) => {
            match resp {
                NotificationResponse::ActionButton(action) => {
                    if action == "Download" {
                        let _ = open::that(url);
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
