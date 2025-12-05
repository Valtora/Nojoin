#[cfg(windows)]
use log::error;
#[cfg(windows)]
use tauri::AppHandle;
#[cfg(windows)]
use win32_notif::{
    notification::{
        actions::{action::ActivationType, ActionButton},
        visual::Text,
    },
    NotificationActivatedEventHandler, NotificationBuilder, ToastsNotifier,
};

#[cfg(windows)]
pub fn show_update_notification(_app: AppHandle, version: String, url: String) {
    // The App ID must match what is registered by the installer or the executable
    // Tauri usually uses the bundle identifier.
    let app_id = "com.valtora.nojoin.companion";

    let notifier_result = ToastsNotifier::new(app_id);
    if let Err(e) = notifier_result {
        error!("Failed to create ToastsNotifier: {:?}", e);
        return;
    }
    let notifier = notifier_result.unwrap();

    let update_url = url.clone();

    let notif_result = NotificationBuilder::new()
        .visual(Text::create(0, "Update Available"))
        .visual(Text::create(
            1,
            &format!("Version {} is available.", version),
        ))
        .action(
            ActionButton::create("Download")
                .with_id("update")
                .with_activation_type(ActivationType::Foreground),
        )
        .action(
            ActionButton::create("Not Now")
                .with_id("cancel")
                .with_activation_type(ActivationType::Foreground),
        )
        .on_activated(NotificationActivatedEventHandler::new(
            move |_notif, args| {
                if let Some(args) = args {
                    if let Some(id) = &args.button_id {
                        if id == "update" {
                            let _ = open::that(&update_url);
                        }
                    }
                }
                Ok(())
            },
        ))
        .build(0, &notifier, "update", "updates");

    match notif_result {
        Ok(notif) => {
            if let Err(e) = notif.show() {
                error!("Failed to show notification: {:?}", e);
            }
        }
        Err(e) => error!("Failed to build notification: {:?}", e),
    }
}
