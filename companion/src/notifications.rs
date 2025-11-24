use notify_rust::Notification;

pub fn show_notification(summary: &str, body: &str) {
    let _ = Notification::new()
        .summary(summary)
        .body(body)
        .appname("Nojoin Companion")
        .show();
}
