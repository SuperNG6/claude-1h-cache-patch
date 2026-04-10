# Claude Code 1h 缓存补丁

为 Claude Code 启用 **1 小时提示缓存**，替换默认的短期缓存策略。

[English](README.md)

---

## 补丁作用

Claude Code 使用提示缓存（Prompt Caching）来降低重复上下文的 token 费用。默认情况下，缓存有效期受限——本补丁强制让缓存判断函数始终返回 `true`，无条件启用 `ttl: "1h"` 路径。

**补丁前：**
```js
function cX5(H) {
  // 仅 Bedrock 用户 + 环境变量才走这里
  if (K9() === "bedrock" && gH(process.env.ENABLE_PROMPT_CACHING_1H_BEDROCK)) return true;
  // 非付费 / 超额用户直接拒绝
  if (!(m8() && !Pk.isUsingOverage)) return false;
  // 付费用户走 GrowthBook 远程白名单
  // ...
}
```

**补丁后：**
```js
function cX5(H) { return true }
```

效果：Claude Code 创建的每个缓存块都会带上 `ttl: "1h"`，系统提示、工具列表、对话历史在整个对话期间保持缓存，不再提前失效，有效减少 token 消耗。

---

## 兼容性

| 平台 | 安装类型 | 自动监听方式 |
|---|---|---|
| macOS | native binary | launchd `WatchPaths`（FSEvents） |
| Linux | native binary | systemd `.path` unit；不可用时降级为 autostart |
| Windows | native binary | 任务计划程序 + 后台守护进程 |
| 全平台 | npm `cli.js` | 补丁支持；无目录监听（npm 更新直接替换文件） |
| 全平台 | VSCode 插件版 Claude Code | 补丁支持；无目录监听（扩展更新由编辑器管理） |

补丁基于**语义锚点**（源码中的字符串字面量）定位目标函数，不依赖混淆后的函数名，只要 Anthropic 不重命名这些内部字符串，补丁在小版本更新后依然有效。

> **注意：** macOS 上修改二进制文件会使原始代码签名失效。工具在补丁后自动用 ad-hoc 身份重签（`codesign -s -`），使 Gatekeeper 接受修改后的二进制。

---

## 环境要求

- Python 3.6+
- 已安装 Claude Code（native binary **或** npm 全局安装均可）
- macOS：需要 `codesign`（随 Xcode Command Line Tools 附带）

---

## 使用方法

```bash
# 一键：打补丁 + 安装自动监听
python3 claude-1h-cache.py

# 单独命令
python3 claude-1h-cache.py patch      # 仅打补丁
python3 claude-1h-cache.py watch      # 仅安装自动监听
python3 claude-1h-cache.py status     # 查看当前状态
python3 claude-1h-cache.py restore    # 从备份还原原始文件
python3 claude-1h-cache.py unwatch    # 卸载自动监听
```

打完补丁后，**重启 Claude Code** 使修改生效。

---

## 自动监听（更新后自动重打）

Claude Code 自动更新时会把新版本下载到 `~/.local/share/claude/versions/`。自动监听机制检测到该目录出现新文件后，自动重新打补丁，**无需手动干预**。

| 平台 | 机制 | 配置文件位置 |
|---|---|---|
| macOS | launchd agent | `~/Library/LaunchAgents/com.claude.1h-cache-autopatch.plist` |
| Linux | systemd path unit | `~/.config/systemd/user/claude-1h-cache-autopatch.path` |
| Windows | 任务计划 + 守护进程 | 任务名：`ClaudeCode1hCacheAutopatch` |

日志文件：`~/.local/share/claude/1h-cache-patch.log`

---

## 补丁原理

1. **定位** — 通过已知安装路径查找 Claude Code binary 或 `cli.js`
2. **识别** — 搜索语义锚点字符串 `tengu_prompt_cache_1h_config`，定位目标函数，不依赖混淆后的函数名
3. **替换** — 对函数体进行**等长字节替换**，用空格填充以保持文件大小不变
4. **重签名** — macOS 上去除原始签名，改用 ad-hoc 签名，使 Gatekeeper 接受修改后的二进制
5. **验证** — 回读文件确认补丁标记存在且原始函数已消除；失败时自动从备份还原

---

## 还原

修改前会在 `<binary>.1h-cache-bak` 创建备份。

```bash
python3 claude-1h-cache.py restore
```

---

## 免责声明

本工具修改第三方应用程序的二进制文件，使用风险由用户自行承担。本项目与 Anthropic 无关，不受其认可。如果 Anthropic 大幅修改 Claude Code 的内部结构，补丁可能失效。
