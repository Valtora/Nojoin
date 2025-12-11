#[cfg(target_os = "macos")]
use log::error;
#[cfg(target_os = "macos")]
use mac_notification_sys::{send_notification, MainButton, NotificationResponse};
#[cfg(target_os = "macos")]
use tauri::AppHandle;

#[cfg(target_os = "macos")]
pub fn request_permission() {
    use objc::runtime::{Object};
    use objc::{msg_send, sel, class};
    use block::ConcreteBlock;
    use log::info;

    info!("Requesting notification permissions...");

    unsafe {
        let center: *mut Object = msg_send![class!(UNUserNotificationCenter), currentNotificationCenter];
        
        // Options: Alert | Sound | Badge
        // UNAuthorizationOptionBadge = 1 << 0
        // UNAuthorizationOptionSound = 1 << 1
        // UNAuthorizationOptionAlert = 1 << 2
        let options: usize = 7; 

        let completion_handler = ConcreteBlock::new(|granted: i8, _error: *mut Object| {
            if granted != 0 {
                info!("Notification permission granted");
            } else {
                info!("Notification permission denied");
            }
        });
        let completion_handler = completion_handler.copy();

        let _: () = msg_send![center, requestAuthorizationWithOptions:options completionHandler:completion_handler];
    }
}

#[cfg(target_os = "macos")]
use tauri::AppHandle;
#[cfg(target_os = "macos")]
use objc::runtime::{Object, BOOL, YES};
#[cfg(target_os = "macos")]
use objc::{class, msg_send, sel, sel_impl};
#[cfg(target_os = "macos")]
use block::ConcreteBlock;

#[cfg(target_os = "macos")]
pub fn request_permission() {
    unsafe {
        let center: *mut Object = msg_send![class!(UNUserNotificationCenter), currentNotificationCenter];
        let options: usize = 7; // Badge | Sound | Alert
        
        let block = ConcreteBlock::new(|granted: BOOL, _error: *mut Object| {
            if granted == YES {
                log::info!("Notification permission granted");
            } else {
                log::warn!("Notification permission denied");
            }
        });
        let block = block.copy();

        let _: () = msg_send![center, requestAuthorizationWithOptions:options completionHandler:block];
    }
}

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
