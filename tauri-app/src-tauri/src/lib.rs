// ClipDown Tauri app — wraps the Python Flask backend.
// On macOS (dev): runs the local venv at ~/clipdown/venv.
// On Windows (release): runs the bundled embeddable Python from Tauri resources.

use std::path::PathBuf;
use std::process::{Child, Command, Stdio};
use std::sync::Mutex;
use std::thread;
use std::time::{Duration, Instant};

use tauri::Manager;

pub struct PythonProcess(pub Mutex<Option<Child>>);

const SERVER_PORT: u16 = 8899;
const SERVER_URL: &str = "http://localhost:8899";

/// Locate the app root that holds app.py + Python runtime.
/// Windows release: <resource_dir>/portable
/// macOS dev: $HOME/clipdown
fn find_app_root(app: &tauri::AppHandle) -> Option<PathBuf> {
    if cfg!(target_os = "windows") {
        if let Ok(rd) = app.path().resource_dir() {
            let p = rd.join("portable");
            if p.join("app.py").exists() {
                return Some(p);
            }
        }
    }
    if let Ok(home) = std::env::var("HOME") {
        let p = PathBuf::from(&home).join("clipdown");
        if p.join("app.py").exists() {
            return Some(p);
        }
    }
    None
}

/// Resolve the Python executable inside the app root.
fn python_bin(root: &PathBuf) -> PathBuf {
    if cfg!(target_os = "windows") {
        root.join("runtime").join("python").join("pythonw.exe")
    } else {
        root.join("venv").join("bin").join("python3")
    }
}

/// PATH prepended so bundled ffmpeg / yt-dlp / deno are found by yt-dlp subprocess.
fn build_child_path(root: &PathBuf) -> String {
    let bin = root.join("bin");
    let py = root.join("runtime").join("python");
    if cfg!(target_os = "windows") {
        // Prepend our bundled bins; keep existing PATH so system utilities remain reachable.
        let existing = std::env::var("PATH").unwrap_or_default();
        format!("{};{};{}", bin.display(), py.display(), existing)
    } else {
        format!(
            "{}:/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin",
            bin.display()
        )
    }
}

/// Check whether the Flask server is already responsive.
fn ping_server() -> bool {
    std::net::TcpStream::connect_timeout(
        &format!("127.0.0.1:{}", SERVER_PORT).parse().unwrap(),
        Duration::from_millis(300),
    )
    .is_ok()
}

/// Start the Python backend. Returns the child handle if we spawned one,
/// or None if the server was already running (e.g. via launchd).
fn spawn_python_server(app: &tauri::AppHandle) -> Option<Child> {
    if ping_server() {
        log::info!("Server already running on port {}", SERVER_PORT);
        return None;
    }

    let root = match find_app_root(app) {
        Some(p) => p,
        None => {
            log::error!("Could not find app root (portable resources or ~/clipdown)");
            return None;
        }
    };

    let python = python_bin(&root);
    let app_py = root.join("app.py");

    if !python.exists() {
        log::error!("Python binary not found at {:?}", python);
        return None;
    }

    log::info!("Starting Python server: {:?}", python);

    let mut cmd = Command::new(&python);
    cmd.arg(&app_py)
        .current_dir(&root)
        .env("HOST", "127.0.0.1")
        .env("PORT", SERVER_PORT.to_string())
        .env("PATH", build_child_path(&root))
        .stdout(Stdio::null())
        .stderr(Stdio::null());

    // Windows: hide the console window that would otherwise flash on spawn.
    #[cfg(target_os = "windows")]
    {
        use std::os::windows::process::CommandExt;
        const CREATE_NO_WINDOW: u32 = 0x08000000;
        cmd.creation_flags(CREATE_NO_WINDOW);
    }

    cmd.spawn()
        .map_err(|e| log::error!("Failed to spawn python: {}", e))
        .ok()
}

/// Block until the server responds on port 8899, up to `timeout_secs`.
fn wait_for_server(timeout_secs: u64) -> bool {
    let start = Instant::now();
    while start.elapsed() < Duration::from_secs(timeout_secs) {
        if ping_server() {
            return true;
        }
        thread::sleep(Duration::from_millis(200));
    }
    false
}

#[tauri::command]
fn server_status() -> serde_json::Value {
    serde_json::json!({
        "running": ping_server(),
        "url": SERVER_URL,
    })
}

#[cfg_attr(mobile, tauri::mobile_entry_point)]
pub fn run() {
    tauri::Builder::default()
        .plugin(tauri_plugin_log::Builder::default()
            .level(log::LevelFilter::Info)
            .build())
        .plugin(tauri_plugin_shell::init())
        .plugin(tauri_plugin_opener::init())
        .plugin(tauri_plugin_dialog::init())
        .plugin(tauri_plugin_notification::init())
        .plugin(tauri_plugin_process::init())
        .plugin(tauri_plugin_updater::Builder::new().build())
        .setup(|app| {
            let handle = app.handle().clone();
            // Spawn Python server on the main app handle so resource_dir() resolves.
            let child = spawn_python_server(&handle);
            let _ = wait_for_server(15);
            app.manage(PythonProcess(Mutex::new(child)));
            Ok(())
        })
        .invoke_handler(tauri::generate_handler![server_status])
        .on_window_event(|window, event| {
            if let tauri::WindowEvent::CloseRequested { .. } = event {
                // Kill our Python child (if we own it) when the last window closes.
                let handle = window.app_handle();
                if let Some(state) = handle.try_state::<PythonProcess>() {
                    if let Ok(mut guard) = state.0.lock() {
                        if let Some(mut child) = guard.take() {
                            log::info!("Terminating Python server (PID {})", child.id());
                            let _ = child.kill();
                        }
                    }
                }
            }
        })
        .run(tauri::generate_context!())
        .expect("error while running tauri application");
}
