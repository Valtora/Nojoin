#[cfg(windows)]
use tauri::{AppHandle, Manager};
#[cfg(windows)]
use win32_notif::{
    NotificationBuilder, ToastsNotifier,
    notification::{
        actions::{ActionButton, ActivationType},
        visual::Text,
        NotificationActivatedEventHandler
    }
};
#[cfg(windows)]
use log::error;

#[cfg(windows)]
pub fn show_update_notification(app: AppHandle, version: String) {
    // The App ID must match what is registered by the installer or the executable
    // Tauri usually uses the bundle identifier.
    let app_id = "com.valtora.nojoin.companion";
    
    let notifier_result = ToastsNotifier::new(app_id);
    if let Err(e) = notifier_result {
        error!("Failed to create ToastsNotifier: {:?}", e);
        return;
    }
    let notifier = notifier_result.unwrap();

    let app_handle = app.clone();
    
    let notif_result = NotificationBuilder::new()
        .visual(Text::create(0, "Update Available"))
        .visual(Text::create(1, &format!("Version {} is available.", version)))
        .action(
            ActionButton::create("Update Now")
                .with_id("update")
                .with_activation_type(ActivationType::Foreground)
        )
        .action(
            ActionButton::create("Not Now")
                .with_id("cancel")
                .with_activation_type(ActivationType::Foreground)
        )
        .on_activated(NotificationActivatedEventHandler::new(move |_notif, args| {
            if let Some(args) = args {
                if let Some(id) = &args.button_id {
                    if id == "update" {
                        let app = app_handle.clone();
                        tauri::async_runtime::spawn(async move {
                            // Logic to install update
                            match app.updater().check().await {
                                Ok(update) => {
                                    if update.is_update_available() {
                                        // Show a notification that we are updating?
                                        // Or just do it.
                                        if let Err(e) = update.download_and_install().await {
                                            error!("Failed to install update: {}", e);
                                        } else {
                                            tauri::api::process::restart(&app.env());
                                        }
                                    }
                                }
                                Err(e) => error!("Failed to check update: {}", e),
                            }
                        });
                    }
                }
            }
            Ok(())
        }))
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
