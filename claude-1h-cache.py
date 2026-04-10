#!/usr/bin/env python3
"""
Claude Code 1h Cache Patch Tool
跨平台：macOS / Linux / Windows
安装类型：native binary / npm cli.js 自动识别

用法：
  python3 claude-1h-cache.py              # 打补丁 + 安装自动监听
  python3 claude-1h-cache.py patch        # 仅打补丁
  python3 claude-1h-cache.py watch        # 仅安装自动监听
  python3 claude-1h-cache.py status       # 查看状态
  python3 claude-1h-cache.py restore      # 还原备份
  python3 claude-1h-cache.py unwatch      # 卸载自动监听
  python3 claude-1h-cache.py daemon       # 内部用：守护进程模式（Windows/Linux 后台监听）
"""

import sys
import os
import shutil
import subprocess
import platform
import time
import json

# ─── 版本 ─────────────────────────────────────────────────────────────────────
VERSION = "2.0.0"

# ─── 补丁锚点（版本无关，依赖语义字符串）────────────────────────────────────
ORIG_FUNC = (
    b'function cX5(H){'
    b'if(K9()==="bedrock"&&gH(process.env.ENABLE_PROMPT_CACHING_1H_BEDROCK))return!0;'
    b'if(!(m8()&&!Pk.isUsingOverage))return!1;'
    b'let q=B66();if(q===null)q=S_("tengu_prompt_cache_1h_config",{}).allowlist??[],g66(q);'
    b'return H!==void 0&&q.some((K)=>K.endsWith("*")?H.startsWith(K.slice(0,-1)):H===K)}'
)
PATCH_MARKER = b'/*__1h_patched__*/'
PATCH_CORE   = b'function cX5(H){return!0}'

def _make_replacement():
    base = PATCH_MARKER + PATCH_CORE
    assert len(base) <= len(ORIG_FUNC), f"补丁长度超出原函数：{len(base)} > {len(ORIG_FUNC)}"
    return base + b' ' * (len(ORIG_FUNC) - len(base))

REPLACEMENT = _make_replacement()
MAX_SCAN_FILE_SIZE = 80 * 1024 * 1024
MAX_SCAN_DEPTH = 6
SCAN_SKIP_DIRS = {
    ".git", ".hg", ".svn", "__pycache__", "tmp", "cache", "logs", "log", "extensions-cache"
}

# ─── 平台 ─────────────────────────────────────────────────────────────────────
SYSTEM   = platform.system()   # Darwin / Linux / Windows
IS_MAC   = SYSTEM == "Darwin"
IS_WIN   = SYSTEM == "Windows"
IS_LINUX = SYSTEM == "Linux"
HOME     = os.path.expanduser("~")
SCRIPT   = os.path.abspath(sys.argv[0])

# ─── 颜色（Windows CMD 不一定支持，做了兼容）──────────────────────────────────
_use_color = sys.stdout.isatty() and not IS_WIN or (IS_WIN and os.environ.get("WT_SESSION"))
def _c(code, s): return f"\033[{code}m{s}\033[0m" if _use_color else s
def green(s):  return _c("32", s)
def yellow(s): return _c("33", s)
def red(s):    return _c("31", s)
def cyan(s):   return _c("36", s)
def bold(s):   return _c("1",  s)

def ok(msg):      print(f"  {green('v')}  {msg}")
def warn(msg):    print(f"  {yellow('!')}  {msg}")
def err(msg):     print(f"  {red('x')}  {msg}", file=sys.stderr)
def info(msg):    print(f"  {cyan('i')}  {msg}")
def section(msg): print(f"\n{bold(cyan(msg))}")

# ─── 定位安装 ─────────────────────────────────────────────────────────────────
def find_target():
    """
    返回 (path, mode, versions_dir_or_None)
    mode: "binary" | "npm" | "vscode"
    versions_dir: native binary 模式下的版本目录（用于监听）
    """

    # ── native binary：~/.local/share/claude/versions/（macOS / Linux）
    versions_dir = os.path.join(HOME, ".local", "share", "claude", "versions")
    if os.path.isdir(versions_dir):
        entries = [
            v for v in os.listdir(versions_dir)
            if not v.endswith(".bak") and not v.endswith(".1h-cache-bak")
               and os.path.isfile(os.path.join(versions_dir, v))
        ]
        if entries:
            def ver_key(v):
                parts = v.split(".")
                return [int(x) if x.isdigit() else 0 for x in parts]
            latest = sorted(entries, key=ver_key, reverse=True)[0]
            return os.path.join(versions_dir, latest), "binary", versions_dir

    # ── native binary：Windows %LOCALAPPDATA%\Programs\claude\
    if IS_WIN:
        local_app = os.environ.get("LOCALAPPDATA", "")
        for base in [
            os.path.join(local_app, "Programs", "claude"),
            os.path.join(local_app, "claude"),
        ]:
            if os.path.isdir(base):
                # versions 子目录
                vdir = os.path.join(base, "versions")
                if os.path.isdir(vdir):
                    entries = [
                        v for v in os.listdir(vdir)
                        if os.path.isfile(os.path.join(vdir, v))
                           and not v.endswith(".bak")
                    ]
                    if entries:
                        def ver_key2(v):
                            stem = os.path.splitext(v)[0]
                            return [int(x) if x.isdigit() else 0 for x in stem.split(".")]
                        latest = sorted(entries, key=ver_key2, reverse=True)[0]
                        return os.path.join(vdir, latest), "binary", vdir
                # 直接的 .exe
                exe = os.path.join(base, "claude.exe")
                if os.path.isfile(exe):
                    return exe, "binary", base

    # ── npm 全局安装
    npm_candidates = _find_npm_cli()
    if npm_candidates:
        return npm_candidates, "npm", None

    # ── VSCode 插件版（扩展目录 / globalStorage）
    vscode_target = _find_vscode_target()
    if vscode_target:
        return vscode_target, "vscode", None

    return None, None, None


def _file_has_patch_anchor(path: str) -> bool:
    try:
        if os.path.getsize(path) > MAX_SCAN_FILE_SIZE:
            return False
        with open(path, "rb") as f:
            data = f.read()
        return ORIG_FUNC in data or PATCH_MARKER in data
    except Exception:
        return False


def _scan_dir_for_patch_target(root: str):
    if not os.path.isdir(root):
        return None

    # 常见位置优先
    preferred = [
        os.path.join(root, "cli.js"),
        os.path.join(root, "dist", "cli.js"),
        os.path.join(root, "out", "cli.js"),
        os.path.join(root, "node_modules", "@anthropic-ai", "claude-code", "cli.js"),
        os.path.join(root, "node_modules", "@anthropic-ai", "claude-code", "dist", "cli.js"),
        os.path.join(root, "claude"),
        os.path.join(root, "claude.exe"),
    ]
    for p in preferred:
        if os.path.isfile(p) and _file_has_patch_anchor(p):
            return p

    root_depth = root.rstrip(os.sep).count(os.sep)
    name_candidates = {"cli.js", "claude", "claude.exe"}
    for cur, dirs, files in os.walk(root):
        depth = cur.rstrip(os.sep).count(os.sep) - root_depth
        if depth >= MAX_SCAN_DEPTH:
            dirs[:] = []
            continue
        dirs[:] = [d for d in dirs if d not in SCAN_SKIP_DIRS]
        cur_lower = cur.lower()
        for fn in files:
            fn_lower = fn.lower()
            if fn_lower in name_candidates or (fn_lower.endswith(".js") and ("claude" in cur_lower or "claude" in fn_lower)):
                p = os.path.join(cur, fn)
                if _file_has_patch_anchor(p):
                    return p
    return None


def _collect_vscode_roots():
    roots = []
    if IS_WIN:
        user_profile = os.environ.get("USERPROFILE", HOME)
        appdata = os.environ.get("APPDATA", "")
        roots += [
            os.path.join(user_profile, ".vscode", "extensions"),
            os.path.join(user_profile, ".vscode-insiders", "extensions"),
            os.path.join(user_profile, ".cursor", "extensions"),
            os.path.join(appdata, "Code", "User", "globalStorage"),
            os.path.join(appdata, "Code - Insiders", "User", "globalStorage"),
            os.path.join(appdata, "Cursor", "User", "globalStorage"),
            os.path.join(appdata, "VSCodium", "User", "globalStorage"),
        ]
    elif IS_MAC:
        roots += [
            os.path.join(HOME, ".vscode", "extensions"),
            os.path.join(HOME, ".vscode-insiders", "extensions"),
            os.path.join(HOME, ".cursor", "extensions"),
            os.path.join(HOME, "Library", "Application Support", "Code", "User", "globalStorage"),
            os.path.join(HOME, "Library", "Application Support", "Code - Insiders", "User", "globalStorage"),
            os.path.join(HOME, "Library", "Application Support", "Cursor", "User", "globalStorage"),
            os.path.join(HOME, "Library", "Application Support", "VSCodium", "User", "globalStorage"),
        ]
    else:
        roots += [
            os.path.join(HOME, ".vscode", "extensions"),
            os.path.join(HOME, ".vscode-insiders", "extensions"),
            os.path.join(HOME, ".cursor", "extensions"),
            os.path.join(HOME, ".config", "Code", "User", "globalStorage"),
            os.path.join(HOME, ".config", "Code - Insiders", "User", "globalStorage"),
            os.path.join(HOME, ".config", "Cursor", "User", "globalStorage"),
            os.path.join(HOME, ".config", "VSCodium", "User", "globalStorage"),
        ]
    # 去重并保留顺序
    return list(dict.fromkeys(roots))


def _find_vscode_target():
    roots = _collect_vscode_roots()
    for root in roots:
        if not os.path.isdir(root):
            continue
        # 先优先扫描含 claude 的子目录
        try:
            entries = sorted(os.listdir(root), reverse=True)
        except Exception:
            entries = []
        for entry in entries:
            if "claude" not in entry.lower():
                continue
            p = _scan_dir_for_patch_target(os.path.join(root, entry))
            if p:
                return p
        # 再回退扫描根目录（兼容无 claude 命名的目录结构）
        p = _scan_dir_for_patch_target(root)
        if p:
            return p
    return None


def _find_npm_cli():
    # npm root -g
    try:
        npm_root = subprocess.check_output(
            ["npm", "root", "-g"], text=True, stderr=subprocess.DEVNULL, shell=IS_WIN
        ).strip().splitlines()[0].strip()
        p = os.path.join(npm_root, "@anthropic-ai", "claude-code", "cli.js")
        if os.path.isfile(p): return p
    except Exception:
        pass

    # 固定路径
    candidates = []
    if IS_WIN:
        appdata = os.environ.get("APPDATA", "")
        candidates += [
            os.path.join(appdata, "npm", "node_modules", "@anthropic-ai", "claude-code", "cli.js"),
        ]
        nvm_home = os.environ.get("NVM_HOME", "")
        if nvm_home and os.path.isdir(nvm_home):
            for v in os.listdir(nvm_home):
                candidates.append(os.path.join(nvm_home, v, "node_modules", "@anthropic-ai", "claude-code", "cli.js"))
    else:
        candidates += [
            "/usr/local/lib/node_modules/@anthropic-ai/claude-code/cli.js",
            "/usr/lib/node_modules/@anthropic-ai/claude-code/cli.js",
            os.path.join(HOME, ".npm-global", "lib", "node_modules", "@anthropic-ai", "claude-code", "cli.js"),
        ]
        # nvm
        nvm_dir = os.environ.get("NVM_DIR", os.path.join(HOME, ".nvm"))
        if os.path.isdir(nvm_dir):
            vdir = os.path.join(nvm_dir, "versions", "node")
            if os.path.isdir(vdir):
                for v in os.listdir(vdir):
                    candidates.append(os.path.join(vdir, v, "lib", "node_modules", "@anthropic-ai", "claude-code", "cli.js"))
        # Homebrew
        for root in ["/opt/homebrew/lib/node_modules", "/usr/local/lib/node_modules"]:
            p = os.path.join(root, "@anthropic-ai", "claude-code", "cli.js")
            candidates.append(p)

    for p in candidates:
        if os.path.isfile(p): return p
    return None

# ─── 补丁状态 ─────────────────────────────────────────────────────────────────
def detect_state(data: bytes) -> str:
    if PATCH_MARKER in data: return "patched"
    if ORIG_FUNC in data:    return "original"
    return "unknown"

# ─── macOS 重签名 ─────────────────────────────────────────────────────────────
def resign_macos(path: str) -> bool:
    try:
        subprocess.run(["codesign", "--remove-signature", path],
                       check=True, capture_output=True)
        subprocess.run(["codesign", "-s", "-", path],
                       check=True, capture_output=True)
        return True
    except Exception:
        return False

# ─── 核心：打补丁 ─────────────────────────────────────────────────────────────
def do_patch(target: str, mode: str) -> bool:
    try:
        data = open(target, "rb").read()
    except PermissionError:
        err(f"读取失败，权限不足。")
        _hint_sudo()
        return False

    state = detect_state(data)
    count = data.count(ORIG_FUNC) if state == "original" else data.count(PATCH_MARKER)
    info(f"当前状态：{green('已打补丁') if state=='patched' else yellow('未打补丁') if state=='original' else red('未知')}")
    if state in ("original", "patched"):
        info(f"匹配位置：{count} 处")

    if state == "patched":
        ok("已是补丁状态，无需操作。")
        return True

    if state == "unknown":
        err("未找到目标函数锚点，版本结构可能已变更，请更新本脚本。")
        return False

    # 备份
    bak = target + ".1h-cache-bak"
    if not os.path.isfile(bak):
        try:
            shutil.copy2(target, bak)
            ok(f"备份已创建：{bak}")
        except Exception as e:
            err(f"备份失败：{e}")
            return False
    else:
        info("备份已存在，跳过。")

    # 替换
    patched = data.replace(ORIG_FUNC, REPLACEMENT)
    try:
        with open(target, "wb") as f:
            f.write(patched)
    except PermissionError:
        err("写入失败，权限不足。")
        _hint_sudo()
        return False

    # macOS：重签名
    if mode == "binary" and IS_MAC:
        if resign_macos(target):
            ok("代码签名已更新（ad-hoc）。")
        else:
            warn("codesign 不可用，跳过签名。若程序崩溃请手动运行：")
            warn(f"  codesign -s - {target}")

    # 验证
    verify = open(target, "rb").read()
    if PATCH_MARKER not in verify or ORIG_FUNC in verify:
        err("验证失败，自动还原...")
        shutil.copy2(bak, target)
        if mode == "binary" and IS_MAC:
            resign_macos(target)
        return False

    ok("补丁写入成功，已验证。")
    return True


def do_restore(target: str, mode: str) -> bool:
    bak = target + ".1h-cache-bak"
    if not os.path.isfile(bak):
        err(f"未找到备份：{bak}")
        return False
    try:
        shutil.copy2(bak, target)
        if mode == "binary" and IS_MAC:
            resign_macos(target)
        ok("已还原至原始版本。")
        return True
    except PermissionError:
        err("写入失败，权限不足。")
        _hint_sudo()
        return False

# ─── 自动监听：macOS launchd ──────────────────────────────────────────────────
_LAUNCHD_LABEL = "com.claude.1h-cache-autopatch"
_LAUNCHD_PLIST = os.path.join(HOME, "Library", "LaunchAgents", f"{_LAUNCHD_LABEL}.plist")

def watch_install_macos(versions_dir: str):
    plist = f"""<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN"
  "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>Label</key>
  <string>{_LAUNCHD_LABEL}</string>
  <key>WatchPaths</key>
  <array>
    <string>{versions_dir}</string>
  </array>
  <key>ProgramArguments</key>
  <array>
    <string>{sys.executable}</string>
    <string>{SCRIPT}</string>
    <string>patch</string>
  </array>
  <key>StandardOutPath</key>
  <string>{os.path.join(HOME, ".local", "share", "claude", "1h-cache-patch.log")}</string>
  <key>StandardErrorPath</key>
  <string>{os.path.join(HOME, ".local", "share", "claude", "1h-cache-patch.log")}</string>
  <key>ThrottleInterval</key>
  <integer>5</integer>
</dict>
</plist>
"""
    os.makedirs(os.path.dirname(_LAUNCHD_PLIST), exist_ok=True)
    with open(_LAUNCHD_PLIST, "w") as f:
        f.write(plist)

    # 卸载旧的再装
    subprocess.run(["launchctl", "unload", _LAUNCHD_PLIST],
                   capture_output=True)
    result = subprocess.run(["launchctl", "load", _LAUNCHD_PLIST],
                            capture_output=True)
    if result.returncode == 0:
        ok(f"launchd 监听已安装（WatchPaths: {versions_dir}）")
        return True
    else:
        warn(f"launchctl load 失败：{result.stderr.decode().strip()}")
        return False


def watch_uninstall_macos():
    if os.path.isfile(_LAUNCHD_PLIST):
        subprocess.run(["launchctl", "unload", _LAUNCHD_PLIST], capture_output=True)
        os.remove(_LAUNCHD_PLIST)
        ok("launchd 监听已卸载。")
    else:
        info("launchd 监听未安装。")


# ─── 自动监听：Linux systemd ──────────────────────────────────────────────────
_SYSTEMD_DIR     = os.path.join(HOME, ".config", "systemd", "user")
_SYSTEMD_SERVICE = "claude-1h-cache-autopatch.service"
_SYSTEMD_PATH    = "claude-1h-cache-autopatch.path"

def watch_install_linux(versions_dir: str):
    os.makedirs(_SYSTEMD_DIR, exist_ok=True)

    service = f"""[Unit]
Description=Claude Code 1h Cache Auto Patch

[Service]
Type=oneshot
ExecStart={sys.executable} {SCRIPT} patch
StandardOutput=append:{HOME}/.local/share/claude/1h-cache-patch.log
StandardError=append:{HOME}/.local/share/claude/1h-cache-patch.log
"""
    path_unit = f"""[Unit]
Description=Watch Claude Code versions directory

[Path]
PathChanged={versions_dir}
Unit={_SYSTEMD_SERVICE}

[Install]
WantedBy=default.target
"""
    with open(os.path.join(_SYSTEMD_DIR, _SYSTEMD_SERVICE), "w") as f:
        f.write(service)
    with open(os.path.join(_SYSTEMD_DIR, _SYSTEMD_PATH), "w") as f:
        f.write(path_unit)

    try:
        subprocess.run(["systemctl", "--user", "daemon-reload"], check=True, capture_output=True)
        subprocess.run(["systemctl", "--user", "enable", "--now", _SYSTEMD_PATH],
                       check=True, capture_output=True)
        ok(f"systemd path 监听已安装并启动。")
        return True
    except subprocess.CalledProcessError as e:
        warn(f"systemctl 失败：{e.stderr.decode().strip() if e.stderr else e}")
        info("如 systemd 不可用，改用守护进程模式：")
        return _watch_install_daemon_linux(versions_dir)


def _watch_install_daemon_linux(versions_dir: str):
    """systemd 不可用时，改写 autostart 桌面条目或 .bashrc 启动守护"""
    autostart_dir = os.path.join(HOME, ".config", "autostart")
    os.makedirs(autostart_dir, exist_ok=True)
    desktop = f"""[Desktop Entry]
Type=Application
Name=Claude 1h Cache Autopatch
Exec={sys.executable} {SCRIPT} daemon {versions_dir}
Hidden=false
NoDisplay=false
X-GNOME-Autostart-enabled=true
"""
    desktop_file = os.path.join(autostart_dir, "claude-1h-cache-autopatch.desktop")
    with open(desktop_file, "w") as f:
        f.write(desktop)
    ok(f"已写入 autostart 条目：{desktop_file}")
    info("登录后自动启动守护进程。立即启动：")
    info(f"  nohup {sys.executable} {SCRIPT} daemon {versions_dir} &")
    return True


def watch_uninstall_linux():
    try:
        subprocess.run(["systemctl", "--user", "disable", "--now", _SYSTEMD_PATH],
                       capture_output=True)
    except Exception:
        pass
    for f in [_SYSTEMD_SERVICE, _SYSTEMD_PATH]:
        p = os.path.join(_SYSTEMD_DIR, f)
        if os.path.isfile(p): os.remove(p)
    try:
        subprocess.run(["systemctl", "--user", "daemon-reload"], capture_output=True)
    except Exception:
        pass
    ok("systemd 监听已卸载。")


# ─── 自动监听：Windows 任务计划 ───────────────────────────────────────────────
_TASK_NAME = "ClaudeCode1hCacheAutopatch"

def watch_install_windows(versions_dir: str):
    """
    创建一个在用户登录时启动的任务，以守护进程方式持续监听。
    使用 Windows Task Scheduler + pythonw 静默运行。
    """
    pythonw = sys.executable.replace("python.exe", "pythonw.exe")
    if not os.path.isfile(pythonw):
        pythonw = sys.executable  # 降级使用 python.exe

    xml = f"""<?xml version="1.0" encoding="UTF-16"?>
<Task version="1.2" xmlns="http://schemas.microsoft.com/windows/2004/02/mit/task">
  <Triggers>
    <LogonTrigger>
      <Enabled>true</Enabled>
    </LogonTrigger>
  </Triggers>
  <Actions Context="Author">
    <Exec>
      <Command>{pythonw}</Command>
      <Arguments>"{SCRIPT}" daemon "{versions_dir}"</Arguments>
    </Exec>
  </Actions>
  <Settings>
    <MultipleInstancesPolicy>IgnoreNew</MultipleInstancesPolicy>
    <ExecutionTimeLimit>PT0S</ExecutionTimeLimit>
    <DisallowStartIfOnBatteries>false</DisallowStartIfOnBatteries>
    <StopIfGoingOnBatteries>false</StopIfGoingOnBatteries>
  </Settings>
</Task>
"""
    xml_path = os.path.join(os.environ.get("TEMP", ""), "claude_patch_task.xml")
    with open(xml_path, "w", encoding="utf-16") as f:
        f.write(xml)

    result = subprocess.run(
        ["schtasks", "/Create", "/F", "/TN", _TASK_NAME, "/XML", xml_path],
        capture_output=True, text=True
    )
    os.remove(xml_path)

    if result.returncode == 0:
        ok(f"Windows 任务计划已创建：{_TASK_NAME}")
        # 立即启动守护
        subprocess.Popen(
            [pythonw, SCRIPT, "daemon", versions_dir],
            creationflags=subprocess.DETACHED_PROCESS | subprocess.CREATE_NO_WINDOW
        )
        ok("守护进程已在后台启动。")
        return True
    else:
        warn(f"任务计划创建失败：{result.stderr.strip()}")
        info("请以管理员身份运行本脚本。")
        return False


def watch_uninstall_windows():
    result = subprocess.run(
        ["schtasks", "/Delete", "/F", "/TN", _TASK_NAME],
        capture_output=True, text=True
    )
    if result.returncode == 0:
        ok("Windows 任务计划已删除。")
    else:
        info("任务计划不存在或删除失败。")


# ─── 守护进程：跨平台文件监听（用于 Windows / Linux 无 systemd 场景）──────────
def daemon_watch(versions_dir: str):
    """
    轮询 versions_dir，发现新文件时自动打补丁。
    日志写入同目录的 1h-cache-patch.log。
    """
    log_path = os.path.join(HOME, ".local", "share", "claude", "1h-cache-patch.log")
    os.makedirs(os.path.dirname(log_path), exist_ok=True)

    def log(msg):
        ts = time.strftime("%Y-%m-%d %H:%M:%S")
        line = f"[{ts}] {msg}\n"
        with open(log_path, "a", encoding="utf-8") as f:
            f.write(line)

    log(f"守护进程启动，监听：{versions_dir}")

    def get_files():
        if not os.path.isdir(versions_dir): return set()
        return {
            f for f in os.listdir(versions_dir)
            if not f.endswith(".bak") and not f.endswith(".1h-cache-bak")
               and os.path.isfile(os.path.join(versions_dir, f))
        }

    known = get_files()

    while True:
        time.sleep(10)
        try:
            current = get_files()
            new_files = current - known
            if new_files:
                log(f"发现新文件：{new_files}，触发补丁...")
                target, mode, _ = find_target()
                if target:
                    data = open(target, "rb").read()
                    state = detect_state(data)
                    if state == "original":
                        bak = target + ".1h-cache-bak"
                        if not os.path.isfile(bak):
                            shutil.copy2(target, bak)
                        patched = data.replace(ORIG_FUNC, REPLACEMENT)
                        with open(target, "wb") as f:
                            f.write(patched)
                        if mode == "binary" and IS_MAC:
                            resign_macos(target)
                        log(f"补丁成功：{target}")
                    elif state == "patched":
                        log("已是补丁状态，跳过。")
                    else:
                        log("锚点未找到，版本可能已变更。")
                known = current
        except Exception as e:
            log(f"错误：{e}")


# ─── 监听安装分发 ─────────────────────────────────────────────────────────────
def do_watch_install(versions_dir: str) -> bool:
    if versions_dir is None:
        warn("当前安装模式不支持目录监听（更新通常不会在 versions 目录产生新文件）。")
        info("建议更新后手动运行：python3 claude-1h-cache.py patch")
        return False
    if IS_MAC:
        return watch_install_macos(versions_dir)
    elif IS_LINUX:
        return watch_install_linux(versions_dir)
    elif IS_WIN:
        return watch_install_windows(versions_dir)
    else:
        warn(f"未知平台 {SYSTEM}，跳过监听安装。")
        return False


def do_watch_uninstall():
    if IS_MAC:
        watch_uninstall_macos()
    elif IS_LINUX:
        watch_uninstall_linux()
    elif IS_WIN:
        watch_uninstall_windows()
    else:
        warn(f"未知平台 {SYSTEM}。")


# ─── 权限提示 ─────────────────────────────────────────────────────────────────
def _hint_sudo():
    if IS_WIN:
        info("请以管理员身份运行 PowerShell / CMD，再重试。")
    else:
        info(f"请用：sudo python3 {SCRIPT}")


# ─── 主入口 ───────────────────────────────────────────────────────────────────
def main():
    cmd = sys.argv[1] if len(sys.argv) > 1 else "all"

    # 守护进程模式（内部调用，不打印 banner）
    if cmd == "daemon":
        watch_dir = sys.argv[2] if len(sys.argv) > 2 else None
        if not watch_dir:
            _, _, watch_dir = find_target()
        if not watch_dir:
            sys.exit(1)
        daemon_watch(watch_dir)
        return

    print()
    print(bold(cyan("  ╔═══════════════════════════════════════════════════╗")))
    print(bold(cyan(f"  ║  Claude Code 1h Cache Patch Tool v{VERSION}          ║")))
    print(bold(cyan(f"  ║  平台：{SYSTEM:<10} Python {sys.version.split()[0]:<10}          ║")))
    print(bold(cyan("  ╚═══════════════════════════════════════════════════╝")))
    print()

    # ── 定位
    section("[ 1 ] 定位 Claude Code 安装...")
    target, mode, versions_dir = find_target()
    if not target:
        err("未找到 Claude Code。请确认已安装（native、npm 或 VSCode 插件版）。")
        sys.exit(1)
    ok(f"安装类型：{bold(mode)}")
    ok(f"目标文件：{cyan(target)}")
    if versions_dir:
        info(f"版本目录：{cyan(versions_dir)}")

    # ── status
    if cmd == "status":
        section("[ 状态 ]")
        data = open(target, "rb").read()
        state = detect_state(data)
        if state == "patched":
            ok("已打补丁，1h 缓存已启用。")
        elif state == "original":
            warn("未打补丁。")
        else:
            warn("状态未知，锚点字符串未找到。")
        bak = target + ".1h-cache-bak"
        if os.path.isfile(bak):
            info(f"备份存在：{cyan(bak)}")
        # 监听状态
        if IS_MAC and os.path.isfile(_LAUNCHD_PLIST):
            ok("launchd 自动监听已安装。")
        elif IS_LINUX:
            sp = os.path.join(_SYSTEMD_DIR, _SYSTEMD_PATH)
            if os.path.isfile(sp):
                ok("systemd 自动监听已安装。")
        sys.exit(0)

    # ── restore
    if cmd == "restore":
        section("[ 还原 ]")
        do_restore(target, mode)
        sys.exit(0)

    # ── unwatch
    if cmd == "unwatch":
        section("[ 卸载监听 ]")
        do_watch_uninstall()
        sys.exit(0)

    # ── patch only
    if cmd == "patch":
        section("[ 打补丁 ]")
        ok_patch = do_patch(target, mode)
        sys.exit(0 if ok_patch else 1)

    # ── watch only
    if cmd == "watch":
        section("[ 安装自动监听 ]")
        do_watch_install(versions_dir)
        sys.exit(0)

    # ── all（默认）：patch + watch
    section("[ 2 ] 打补丁...")
    ok_patch = do_patch(target, mode)

    section("[ 3 ] 安装自动监听...")
    ok_watch = do_watch_install(versions_dir)

    print()
    print(bold(green("  ┌────────────────────────────────────────────────┐")))
    if ok_patch:
        print(bold(green("  │  v  补丁已生效                                 │")))
    if ok_watch:
        print(bold(green("  │  v  自动监听已安装（更新后自动重打）           │")))
    print(bold(green("  └────────────────────────────────────────────────┘")))
    print()
    info("重启 Claude Code 后生效。")
    info(f"查看日志：{cyan(os.path.join(HOME, '.local', 'share', 'claude', '1h-cache-patch.log'))}")
    info(f"还原命令：python3 {os.path.basename(SCRIPT)} restore")
    info(f"卸载监听：python3 {os.path.basename(SCRIPT)} unwatch")
    print()


if __name__ == "__main__":
    main()
