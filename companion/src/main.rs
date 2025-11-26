use std::sync::{Arc, Mutex};
use std::sync::atomic::AtomicU32;
use std::thread;
use std::time::{Duration, Instant};
use tray_icon::{TrayIconBuilder, menu::{Menu, MenuItem, MenuEvent, PredefinedMenuItem}, Icon};
use tao::event_loop::{EventLoop, ControlFlow};

mod server;
mod audio;
mod state;
mod uploader;
mod config;
mod notifications;

use state::{AppState, AppStatus};
use config::Config;

fn load_icon() -> Icon {

    let (icon_rgba, icon_width, icon_height) = {
        let image = image::load_from_memory(include_bytes!("icon.png"))
            .expect("Failed to open icon path")
            .into_rgba8();
        let (width, height) = image.dimensions();
        let rgba = image.into_raw();
        (rgba, width, height)
    };
    Icon::from_rgba(icon_rgba, icon_width, icon_height).expect("Failed to open icon")
}

fn main() {
    let event_loop = EventLoop::new();
    
    // Tray Setup
    let tray_menu = Menu::new();
    
    let status_i = MenuItem::new("Status: Ready to Record", false, None);
    let open_web_i = MenuItem::new("Open Web App", true, None);
    let check_updates_i = MenuItem::new("Check for Updates", true, None);
    let help_i = MenuItem::new("Help", true, None);
    let about_i = MenuItem::new("About", true, None);
    let restart_i = MenuItem::new("Restart", true, None);
    let quit_i = MenuItem::new("Quit", true, None);

    tray_menu.append(&status_i).unwrap();
    tray_menu.append(&PredefinedMenuItem::separator()).unwrap();
    tray_menu.append(&open_web_i).unwrap();
    tray_menu.append(&check_updates_i).unwrap();
    tray_menu.append(&help_i).unwrap();
    tray_menu.append(&about_i).unwrap();
    tray_menu.append(&PredefinedMenuItem::separator()).unwrap();
    tray_menu.append(&restart_i).unwrap();
    tray_menu.append(&quit_i).unwrap();
    
    let _tray_icon = TrayIconBuilder::new()
        .with_menu(Box::new(tray_menu))
        .with_tooltip("Nojoin Companion")
        .with_icon(load_icon())
        .build()
        .unwrap();

    // App State
    let (audio_tx, audio_rx) = crossbeam_channel::unbounded();
    let config = Config::load();
    let state = Arc::new(AppState {
        status: Mutex::new(AppStatus::Idle),
        current_recording_id: Mutex::new(None),
        current_sequence: Mutex::new(1),
        audio_command_tx: audio_tx,
        config: Mutex::new(config),
        recording_start_time: Mutex::new(None),
        accumulated_duration: Mutex::new(Duration::new(0, 0)),
        input_level: AtomicU32::new(0),
        output_level: AtomicU32::new(0),
    });

    // Audio Thread
    let state_audio = state.clone();
    thread::spawn(move || {
        audio::run_audio_loop(state_audio, audio_rx);
    });

    // Server Thread (Tokio)
    let state_server = state.clone();
    thread::spawn(move || {
        let rt = tokio::runtime::Runtime::new().unwrap();
        rt.block_on(server::start_server(state_server));
    });

    // Event Loop
    let menu_channel = MenuEvent::receiver();
    
    event_loop.run(move |_event, _, control_flow| {
        *control_flow = ControlFlow::WaitUntil(Instant::now() + Duration::from_millis(100));

        // Update Status
        if let Ok(status) = state.status.try_lock() {
             let status_text = match *status {
                 AppStatus::Idle => "Status: Ready to Record",
                 AppStatus::Recording => "Status: Recording",
                 AppStatus::Paused => "Status: Recording Paused",
                 AppStatus::Uploading => "Status: Uploading Recording",
                 AppStatus::Error(_) => "Status: Error",
             };
             let _ = status_i.set_text(status_text);
        }

        if let Ok(event) = menu_channel.try_recv() {
            if event.id == quit_i.id() {
                *control_flow = ControlFlow::Exit;
            } else if event.id == open_web_i.id() {
                 let url = {
                     let config = state.config.lock().unwrap();
                     config.web_app_url.clone()
                 };
                 let _ = open::that(url);
            } else if event.id == check_updates_i.id() {
                 notifications::show_notification("Updates", "You are on the latest version.");
            } else if event.id == help_i.id() {
                 let _ = open::that("https://github.com/Valtora/Nojoin"); 
            } else if event.id == about_i.id() {
                 notifications::show_notification("About", "Nojoin Companion v0.1.0");
            } else if event.id == restart_i.id() {
                 notifications::show_notification("Restart", "Please restart the application manually.");
            }
        }
    });
}
