use std::sync::{Arc, Mutex};
use std::thread;
use std::time::{Duration, Instant};
use tray_icon::{TrayIconBuilder, menu::{Menu, MenuItem, MenuEvent}, Icon};
use tao::event_loop::{EventLoop, ControlFlow};

mod server;
mod audio;
mod state;
mod uploader;
mod config;

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
    let quit_i = MenuItem::new("Quit", true, None);
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
        *control_flow = ControlFlow::WaitUntil(Instant::now() + Duration::from_millis(50));

        if let Ok(event) = menu_channel.try_recv() {
            if event.id == quit_i.id() {
                *control_flow = ControlFlow::Exit;
            }
        }
    });
}
