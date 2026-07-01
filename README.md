# claude-web-console

[English](README.en.md) | 中文

一个**本地、自托管**的网页操控台：在手机浏览器里驱动本机的 `claude` CLI——在指定目录对话、看清 Claude 做了什么、浏览文件。对话进程、文件读写、命令执行全在本机，手机只是远程界面。访问用一个令牌(token)鉴权。

> ⚠️ **仅限本地/自托管使用。** 网页背后能在你电脑上跑 `claude`（默认含写文件、执行命令的工具）。务必用访问令牌保护，切勿把端口直接暴露到公网（优先用 Tailscale）。

## 功能

- **流式对话** —— SSE 逐字返回、多轮 session、停止生成按钮。
- **工具可观测性** —— 工具调用渲染成可折叠卡片（命令 / 文件路径 / 参数），结果回填（✓ 或出错 + 输出）。手机盲操作的核心：看清电脑上的 Claude 到底做了什么。
- **历史对话** —— 列出当前工作目录的历史会话，点开回放（气泡、工具调用、thinking）并 `--resume` 续聊。刷新自动恢复上次对话。
- **markdown + 表格** —— 内置无依赖渲染器（防 XSS）：标题、加粗/斜体、行内与围栏代码、列表、引用、链接、GFM 表格。
- **thinking** —— 等待时显示「思考中…」脉冲，扩展思考内容渲染成 💭 气泡。
- **结构化提问** —— `AskUserQuestion` 渲染成可点选项卡片。
- **图片输入** —— 手机拍照/截图直接发给 Claude（base64，多模态）。
- **权限模式** —— 每次对话可选：Ask before edits / Edit automatically / Plan mode / Bypass permissions，与 Claude Code UI 一致。
- **多项目切换** —— 在设置里于白名单工作目录间切换。
- **通知** —— 页面切后台时浏览器通知，另有可选的微信/App 推送（Server酱），关掉浏览器也能收到。
- **双鉴权模式** —— Max/订阅额度，或第三方中转站（Anthropic 兼容端点）。
- **Claude Code 风格 UI** —— 手机单页应用，历史/文件/设置为滑入面板。

## 1. 安装依赖

```powershell
cd d:\work\claude-web-console
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
```

## 2. 鉴权

### 方式 A —— Max / 订阅额度（推荐）

一次性生成长期订阅凭据：

```powershell
claude setup-token   # 用你的 Max 账号登录
```

它会输出 `sk-ant-oat01-...` 形式的 OAuth token。**这是机密，别贴给任何人/任何对话。** 通过 config 的 `claude_code_oauth_token` 字段（或环境变量 `CLAUDE_CODE_OAUTH_TOKEN`）使用。

> 订阅模式下**不要**设置 `ANTHROPIC_API_KEY`，否则会走按量付费而非订阅额度。

### 方式 B —— 中转站（第三方代理）

用 `base_url` + 密钥指向一个 Anthropic 兼容的中转站端点。这是按量付费、用别人代理的额度，**不走** Max 订阅。请自行评估中转站的可信度与数据隐私风险。

## 3. 写配置文件

复制 `config.example.json` 为 `config.json`（已在 `.gitignore`，不会进版本库），填入：

```json
{
  "web_console_token": "一个足够长的随机访问令牌",

  "auth_mode": "",
  "claude_code_oauth_token": "sk-ant-oat01-...（setup-token 生成的，机密！）",

  "base_url": "",
  "auth_token": "",
  "api_key": "",

  "work_dir": "d:\\work\\AI_NovelGenerator-main",
  "work_dirs": [],

  "host": "0.0.0.0",
  "port": 8765,
  "claude_bin": "",
  "proxy": "",
  "model": "",
  "permission_mode": "",
  "serverchan_sendkey": ""
}
```

关键字段：

- `web_console_token`：访问网页时校验的令牌，要足够长且随机。
- `auth_mode`：`subscription`（Max/订阅）或 `relay`（第三方中转）。留空自动判断：填了 `base_url` 即视为 `relay`，否则 `subscription`。
- `claude_code_oauth_token`：订阅凭据；不填则复用本机 `claude /login` 的登录态。
- `base_url` / `auth_token` / `api_key`：中转模式。`base_url` 是 Anthropic 兼容端点；密钥用 `auth_token`（→ `ANTHROPIC_AUTH_TOKEN`，Bearer，多数 claude-code 中转站用这个）**或** `api_key`（→ `ANTHROPIC_API_KEY`，`x-api-key`），二选一别同时填。
- `work_dir`：Claude 操作的工作目录。
- `work_dirs`：可选，多项目切换白名单。每项为字符串路径或 `{path, label}`。前端只能从此列表里选——任意路径会被拒绝。
- `proxy`：需翻墙访问 Anthropic 时填（如 `http://127.0.0.1:7890`）；用全局/TUN 模式 VPN 则留空。
- `model` / `permission_mode`：模型与权限模式的默认值（两者 UI 里都可选）。
- `serverchan_sendkey`：可选，[Server酱](https://sc3.ft07.com/) 的 SendKey；填了则任务完成/出错时微信推送。自动识别 Server酱³（`sctp…` 形式的 key）与老 Turbo 版。

任一项都可被同名大写**环境变量覆盖**（如 `$env:PORT`），优先级：**环境变量 > config.json > 默认**。

## 4. 启动

```powershell
python server.py
```

启动后日志里会打印访问地址和 token，例如：

```
http://<本机IP>:8765/?token=xxxxxxxx
```

## 5. 手机访问

- **同一 WiFi**：手机浏览器打开 `http://<电脑局域网IP>:8765/`，输入 token。
- **在外网**：用 [Tailscale](https://tailscale.com/) 把手机和电脑组进同一私有网络，访问 Tailscale 分配的 IP；或用内网穿透把 **8765** 端口暴露出去（务必保留 token，建议再加 HTTPS）。

## 接口

| 方法 | 路径 | 说明 |
|------|------|------|
| POST | `/chat` | body `{prompt, session_id?, model?, permission_mode?, work_dir?, images?}`，SSE 流式返回；`session_id` 用于多轮续接 |
| GET  | `/api/info` | 工作目录、模型列表、权限模式、推送状态 |
| GET  | `/sessions` | 列出工作目录的历史会话（只读） |
| GET  | `/session/{sid}` | 回放某个会话（只读） |
| GET  | `/files?path=` | 列目录（限定在工作目录内） |
| GET  | `/file?path=` | 读文件（≤2MB，二进制不显示） |

全部接口用 `Authorization: Bearer <token>` 或 `?token=` 鉴权。

## 安全提醒

这个网页背后能在你电脑上跑 `claude`（默认含写文件、执行命令的工具）。务必：

- 用足够长的随机 token；
- 不要把 **8765** 裸暴露公网；优先 Tailscale，其次穿透 + HTTPS + token；
- `config.json` 与 `.claude/settings*.json` 不进 git（已 gitignore——含机密）；
- 想更安全可在设置里用 **Plan 模式**（只读规划），或在 `server.py` 里给 `claude` 限制工具（如 `--allowed-tools Read Glob Grep`）。
