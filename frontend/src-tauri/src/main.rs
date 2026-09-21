// ScriptHub 桌面壳（PRD-V5 / Phase V5-B）
// 职责（刻意最小，见 4.D）：窗口 + 单实例 + sidecar 生命周期 + 端口协商/注入 + 托盘
#![cfg_attr(not(debug_assertions), windows_subsystem = "windows")]

#[cfg(windows)]
use std::process::Command;

use std::net::TcpListener;
use std::path::PathBuf;
use std::sync::Mutex;
use tauri::Manager;
use tauri_plugin_shell::process::CommandEvent;
use tauri_plugin_shell::ShellExt;

/// sidecar pid（退出时杀进程树）
struct SidecarPid(Mutex<Option<u32>>);

/// 从 8001 起探测空闲端口（PRD 4.B：最多 +20）
fn pick_port() -> u16 {
    for p in 8001..8021 {
        if TcpListener::bind(("127.0.0.1", p)).is_ok() {
            return p;
        }
    }
    8001 // 全占用则回落（sidecar 起不来会走错误页）
}

/// 杀进程树（POSIX：先杀子进程再杀本体；Windows taskkill /T）
/// plugin spawn 的 sidecar 可能经 wrapper 启动（pid 非脚本进程），
/// 仅 kill 进程组不可靠 → 递归 /proc 找子进程逐个杀（PRD 3.1 优雅退出）
fn kill_tree(pid: u32) {
    #[cfg(unix)]
    {
        // 收集后代 pid（/proc/<pid>/task/<pid>/children）
        fn children_of(pid: u32, acc: &mut Vec<u32>) {
            let p = format!("/proc/{pid}/task/{pid}/children");
            if let Ok(s) = std::fs::read_to_string(&p) {
                for tok in s.split_whitespace() {
                    if let Ok(c) = tok.parse::<u32>() {
                        acc.push(c);
                        children_of(c, acc);
                    }
                }
            }
        }
        let mut all = vec![pid];
        let mut desc = Vec::new();
        children_of(pid, &mut desc);
        all.extend(desc);
        // 先 TERM 后代，再本体
        for p in all.iter().rev() {
            unsafe {
                libc::kill(*p as i32, libc::SIGTERM);
            }
        }
        // 给 200ms 退出窗口，仍在则 KILL
        std::thread::sleep(std::time::Duration::from_millis(200));
        for p in all.iter().rev() {
            unsafe {
                libc::kill(*p as i32, libc::SIGKILL);
            }
        }
    }
    #[cfg(windows)]
    {
        let _ = Command::new("taskkill")
            .args(["/T", "/F", "/PID", &pid.to_string()])
            .status();
    }
}

fn main() {
    tauri::Builder::default()
        .plugin(tauri_plugin_shell::init())
        .plugin(tauri_plugin_dialog::init())
        .plugin(tauri_plugin_fs::init())
        .plugin(tauri_plugin_single_instance::init(|app, _args, _cwd| {
            // 二次启动 → 聚焦已有窗口（PRD 3.1 单实例锁）
            if let Some(w) = app.get_webview_window("main") {
                let _ = w.show();
                let _ = w.set_focus();
            }
        }))
        .manage(SidecarPid(Mutex::new(None)))
        .setup(|app| {
            // --- sidecar 启动 ---
            let port = pick_port();
            // SCRIPTHUB_DATA_DIR 外部可覆盖（开发/测试用）；未设置才用 app_data_dir
            let data_dir = std::env::var("SCRIPTHUB_DATA_DIR")
                .map(PathBuf::from)
                .unwrap_or_else(|_| {
                    app.path()
                        .app_data_dir()
                        .unwrap_or_else(|_| std::env::temp_dir().join("scripthub"))
                });
            let old_data_dir = std::env::var("SCRIPTHUB_OLD_DATA_DIR")
                .map(PathBuf::from)
                .ok()
                .or_else(|| {
                    std::env::current_exe()
                        .ok()
                        .and_then(|p| p.parent().map(|d| d.join("data")))
                        .filter(|d| d.exists())
                });

            let mut cmd = app
                .shell()
                .sidecar("scripthub-server")
                .map_err(|e| format!("sidecar 解析失败: {e}"))?
                .env("SCRIPTHUB_PORT", port.to_string())
                .env("SCRIPTHUB_DATA_DIR", data_dir.display().to_string());
            if let Some(old) = old_data_dir {
                cmd = cmd.env("SCRIPTHUB_OLD_DATA_DIR", old.display().to_string());
            }

            let (mut rx, child) = cmd
                .spawn()
                .map_err(|e| format!("sidecar 启动失败: {e}"))?;
            let pid = child.pid();
            *app.state::<SidecarPid>().0.lock().unwrap() = Some(pid);
            println!("[shell] sidecar pid={pid} port={port} data={}", data_dir.display());

            tauri::async_runtime::spawn(async move {
                while let Some(event) = rx.recv().await {
                    match event {
                        CommandEvent::Stdout(line) => {
                            println!("[sidecar] {}", String::from_utf8_lossy(&line));
                        }
                        CommandEvent::Stderr(line) => {
                            eprintln!("[sidecar:err] {}", String::from_utf8_lossy(&line));
                        }
                        _ => {}
                    }
                }
            });

            // --- 初始化脚本注入（PRD 4.B 方案 a）：页面加载前定义 API 地址 ---
            let js = format!("window.__SCRIPTHUB_API__ = 'http://127.0.0.1:{port}';");
            if let Some(win) = app.get_webview_window("main") {
                win.eval(&js)?;
            }
            // V5-F 修复：eval 在 React 首渲染后才执行（竞态），isTauri() 首判 false 且
            // 前端 useMemo 缓存不会重算。补发 api-ready 事件 → 前端已有 reload 兜底
            // （config.ts 监听 scripthub://api-ready 后带地址整页重载）。延迟 1s 等 listen 注册。
            let app2 = app.handle().clone();
            let js2 = js.clone();
            tauri::async_runtime::spawn(async move {
                std::thread::sleep(std::time::Duration::from_secs(1));
                use tauri::Emitter;
                let _ = app2.emit("scripthub://api-ready", js2.trim_start_matches("window.__SCRIPTHUB_API__ = '").trim_end_matches("';"));
            });

            // --- 托盘（PRD 3.1 P0）：图标 + 菜单（显示主窗口/退出） ---
            use tauri::menu::{Menu, MenuItem};
            use tauri::tray::TrayIconBuilder;
            let show = MenuItem::with_id(app, "show", "显示主窗口", true, None::<&str>)?;
            let quit = MenuItem::with_id(app, "quit", "退出", true, None::<&str>)?;
            let menu = Menu::with_items(app, &[&show, &quit])?;
            let _tray = TrayIconBuilder::new()
                .menu(&menu)
                .tooltip("ScriptHub")
                .on_menu_event(|app, event| match event.id.as_ref() {
                    "show" => {
                        if let Some(w) = app.get_webview_window("main") {
                            let _ = w.show();
                            let _ = w.set_focus();
                        }
                    }
                    "quit" => {
                        // 真退出：杀 sidecar 进程树后退出
                        if let Some(pid) = app.state::<SidecarPid>().0.lock().unwrap().take() {
                            kill_tree(pid);
                        }
                        app.exit(0);
                    }
                    _ => {}
                })
                .icon(app.default_window_icon().unwrap().clone())
                .build(app)?;
            Ok(())
        })
        .on_window_event(|window, event| {
            match event {
                // 关窗 → 隐藏到托盘（PRD 3.1：进程存活，调度继续）；托盘「退出」才真退
                tauri::WindowEvent::CloseRequested { api, .. } => {
                    let _ = window.hide();
                    api.prevent_close();
                }
                // 窗口销毁（真退出路径）→ 兜底杀 sidecar（PRD 3.1 优雅退出）
                tauri::WindowEvent::Destroyed => {
                    let pid = window
                        .app_handle()
                        .state::<SidecarPid>()
                        .0
                        .lock()
                        .unwrap()
                        .take();
                    if let Some(pid) = pid {
                        kill_tree(pid);
                    }
                }
                _ => {}
            }
        })
        .run(tauri::generate_context!())
        .expect("error while running tauri application");
}
