#[cfg(target_os = "linux")]
use log::error;
#[cfg(target_os = "linux")]
use notify_rust::{Notification, Timeout};
#[cfg(target_os = "linux")]
use tauri::AppHandle;

#[cfg(target_os = "linux")]
pub fn show_update_notification(_app: AppHandle, version: String, url: String) {
    let update_url = url.clone();

    // notify-rust's wait_for_action blocks, so this is fine in the thread.
    let notification = Notification::new()
        .summary("Update Available")
        .body(&format!("Version {} is available.", version))
        .icon("dialog-information")
        .appname("Nojoin Companion")
        .timeout(Timeout::Never) // Keep it until user interacts
        .action("update", "Download")
        .action("cancel", "Not Now")
        .show();

    match notification {
        Ok(handle) => {
            handle.wait_for_action(move |action| {
                if action == "update" {
                    let _ = open::that(&update_url);
                }
            });
        }
        Err(e) => {
            error!("Failed to show notification: {:?}", e);
        }
    }
}
