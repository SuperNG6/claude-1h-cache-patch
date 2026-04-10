# Claude Code 1h Cache Patch

Patch tool that enables **1-hour prompt caching** in Claude Code, replacing the default short-lived cache policy.

[中文文档](README.zh.md)

---

## What It Does

Claude Code uses prompt caching to reduce token costs on repeated context. By default, the cache TTL is restricted — this patch forces the cache helper function to always return `true`, enabling the `ttl: "1h"` path unconditionally.

**Before patch:**
```js
function cX5(H) {
  if (K9() === "bedrock" && gH(process.env.ENABLE_PROMPT_CACHING_1H_BEDROCK)) return true;
  if (!(m8() && !Pk.isUsingOverage)) return false;
  // ... allowlist check via GrowthBook feature flag
}
```

**After patch:**
```js
function cX5(H) { return true }
```

This means every cache block Claude Code creates will carry `ttl: "1h"`, keeping your system prompt, tools, and conversation history cached for a full hour instead of expiring early.

---

## Compatibility

| Platform | Install type | Auto-watch mechanism |
|---|---|---|
| macOS | native binary | launchd `WatchPaths` (FSEvents) |
| Linux | native binary | systemd `.path` unit; falls back to autostart |
| Windows | native binary | Task Scheduler + background daemon |
| All | npm `cli.js` | Patch supported; no directory watch (npm updates replace files in-place) |
| All | VSCode extension Claude Code | Patch supported; no directory watch (extension updates are managed by the editor) |

The patch uses **semantic anchors** (string literals in the source) rather than obfuscated function names, so it survives minor version updates as long as Anthropic doesn't rename these internal strings.

> **Note:** On macOS, modifying the binary invalidates the original code signature. The tool automatically re-signs with an ad-hoc identity (`codesign -s -`) after patching.

---

## Requirements

- Python 3.6+
- Claude Code installed (native binary **or** npm)
- macOS: `codesign` (ships with Xcode Command Line Tools)

---

## Usage

```bash
# One command: patch + install auto-watch
python3 claude-1h-cache.py

# Individual commands
python3 claude-1h-cache.py patch      # Patch only
python3 claude-1h-cache.py watch      # Install auto-watch only
python3 claude-1h-cache.py status     # Show current state
python3 claude-1h-cache.py restore    # Restore original binary from backup
python3 claude-1h-cache.py unwatch    # Uninstall auto-watch
```

After patching, **restart Claude Code** for the change to take effect.

---

## Auto-Watch (Survive Updates)

Claude Code auto-updates by downloading a new binary to `~/.local/share/claude/versions/`. The auto-watch mechanism detects new files in this directory and re-applies the patch automatically — no manual intervention needed after updates.

| Platform | Mechanism | Config location |
|---|---|---|
| macOS | launchd agent | `~/Library/LaunchAgents/com.claude.1h-cache-autopatch.plist` |
| Linux | systemd path unit | `~/.config/systemd/user/claude-1h-cache-autopatch.path` |
| Windows | Scheduled Task + daemon | Task name: `ClaudeCode1hCacheAutopatch` |

Log file: `~/.local/share/claude/1h-cache-patch.log`

---

## How the Patch Works

1. **Locate** — finds the Claude Code binary or `cli.js` by checking known install paths
2. **Identify** — searches for the semantic anchor string `tengu_prompt_cache_1h_config` to locate the target function without relying on minified names
3. **Replace** — performs an **equal-length byte replacement** of the function body, padding with spaces to preserve file size
4. **Re-sign** — on macOS, strips the original signature and applies an ad-hoc signature so Gatekeeper accepts the modified binary
5. **Verify** — reads the file back to confirm the patch marker is present and the original function is gone; auto-restores from backup on failure

---

## Restore

A backup is created at `<binary>.1h-cache-bak` before any modification.

```bash
python3 claude-1h-cache.py restore
```

---

## Disclaimer

This tool modifies a third-party application binary. Use at your own risk. It is not affiliated with or endorsed by Anthropic. The patch may break if Anthropic significantly changes the internal structure of Claude Code.
