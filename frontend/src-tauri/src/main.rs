// ScriptHub 桌面壳（PRD-V5 / Phase V5-B）
// 职责（刻意最小，见 4.D）：窗口 + 单实例 + sidecar 生命周期 + 端口协商/注入 + 托盘
#![cfg_attr(not(debug_assertions), windows_subsystem = "windows")]

#[cfg(windows)]
use std::process::Command;

use std::net::TcpListener;
use std::path::PathBuf;
use std::sync::atomic::{AtomicBool, Ordering};
use std::sync::Mutex;
use tauri::Manager;
use tauri_plugin_shell::process::CommandEvent;
use tauri_plugin_shell::ShellExt;

/// sidecar pid（退出时杀进程树）
struct SidecarPid(Mutex<Option<u32>>);

/// 主动退出路径置位（托盘 quit / Destroyed 钩子），Terminated 据此不再触发壳退出
static SHUTTING_DOWN: AtomicBool = AtomicBool::new(false);

/// 从 8001 起探测空闲端口（PRD 4.B：最多 +20）
fn pick_port() -> u16 {
    for p in 8001..8021 {
        if TcpListener::bind(("127.0.0.1", p)).is_ok() {
            return p;
        }
    }
    8001 // 全占用则回落（sidecar 起不来会走错误页）
}

/// [弃用保留] webview 缓存目录清理：实测两个坑——
/// ① 删目录与 WebKitGTK 初始化竞态，页面挂起；② 目录不存在时 WebKit 无 dbus 会话无法自建，同样挂起。
/// 修复方式（安装包/启动脚本层面）：升级前 `rm -rf ~/.cache/com.scripthub.app && mkdir -p ~/.cache/com.scripthub.app`
#[allow(dead_code)]
fn clear_webview_cache() {
    if let Some(dir) = dirs::cache_dir() {
        let _ = std::fs::remove_dir_all(dir.join("com.scripthub.app"));
    }
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

/// 真重启（设置面板「立即重启」）：退出并重新拉起壳进程，sidecar 随 Destroyed 钩子回收后由新进程重建
#[tauri::command]
fn restart_app(app: tauri::AppHandle) {
    app.restart()
}

fn main() {
    // V5-G: 缓存清理改在 webview 就绪后延迟执行（见 setup 内 spawn），避免与 GTK 初始化竞态。

    tauri::Builder::default()
        .invoke_handler(tauri::generate_handler![restart_app])
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
            // 数据目录：外部 SCRIPTHUB_DATA_DIR 最高优先（开发/测试覆盖）；否则注入默认目录
            // v2.14：壳注入改用 SCRIPTHUB_DEFAULT_DATA_DIR——若注入 SCRIPTHUB_DATA_DIR，
            // readonly.data_dir 永久为真（面板永远置灰）且 settings.json 的 data_dir 永不生效
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
                .env("SCRIPTHUB_DEFAULT_DATA_DIR", data_dir.display().to_string());
            if let Some(old) = old_data_dir {
                cmd = cmd.env("SCRIPTHUB_OLD_DATA_DIR", old.display().to_string());
            }

            let (mut rx, child) = cmd
                .spawn()
                .map_err(|e| format!("sidecar 启动失败: {e}"))?;
            let pid = child.pid();
            *app.state::<SidecarPid>().0.lock().unwrap() = Some(pid);
            println!("[shell] sidecar pid={pid} port={port} data={}", data_dir.display());

            let app3 = app.handle().clone();
            tauri::async_runtime::spawn(async move {
                while let Some(event) = rx.recv().await {
                    match event {
                        CommandEvent::Stdout(line) => {
                            println!("[sidecar] {}", String::from_utf8_lossy(&line));
                        }
                        CommandEvent::Stderr(line) => {
                            eprintln!("[sidecar:err] {}", String::from_utf8_lossy(&line));
                        }
                        CommandEvent::Terminated(_) => {
                            if SHUTTING_DOWN.load(Ordering::SeqCst) {
                                println!("[sidecar] terminated (主动退出路径，忽略)");
                                continue;
                            }
                            // sidecar 意外死亡（崩溃/被杀）→ 壳同步退出，不留孤儿窗口（V5-D 收尾）
                            eprintln!("[sidecar] terminated unexpectedly, exiting shell");
                            if let Some(pid) = app3
                                .state::<SidecarPid>()
                                .0
                                .lock()
                                .unwrap()
                                .take()
                            {
                                kill_tree(pid);
                            }
                            app3.exit(1);
                        }
                        _ => {}
                    }
                }
            });

            // --- 窗口去装饰（H-1）：必须在窗口首次 map 之前生效 ---
            // tao 的 GTK 建窗顺序是 set_visible(platform_impl/linux/window.rs:181) →
            // set_decorated(:182)：窗口若先被 map，mutter 按「未声明无装饰」给 37px SSD
            // 标题栏（白栏），之后补 _MOTIF_WM_HINTS 只能等 mutter 回收 frame，实测约 1/3
            // 概率回收不掉 → 白栏复发。所以 tauri.conf.json 用 visible:false 建窗（不 map），
            // 这里显式去装饰后再 show：窗口 realize 时 decorations 已为 false，mutter 不建 frame。
            // （幂等：与 tauri.conf.json 的 decorations:false 冗余，防配置回退后白栏复发）
            if let Some(win) = app.get_webview_window("main") {
                win.set_decorations(false)?;
                win.show()?;
            }

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
                        SHUTTING_DOWN.store(true, Ordering::SeqCst);
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
                    SHUTTING_DOWN.store(true, Ordering::SeqCst);
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
