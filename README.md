# claude-web-console

[English](README.en.md) | 中文

一个**本地**的网页操控台：在指定目录里跟 `claude` 对话、查看目录文件，手机浏览器即可用。
对话进程、文件读写全在本机，手机只是远程界面。访问用一个令牌(token)鉴权。

## 1. 安装依赖

```powershell
cd d:\work\claude-web-console
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
```

## 2. 让 claude 用 Max 订阅额度（一次性）

```powershell
claude setup-token   # 用你的 Max 账号登录，生成长期订阅凭据
```
它会输出一个 `sk-ant-oat01-...` 形式的 OAuth token。**这是机密，别贴给任何人/任何对话。**
后续靠环境变量 `CLAUDE_CODE_OAUTH_TOKEN` 使用它（见下一步）。

> 不要设置 `ANTHROPIC_API_KEY`，否则会走按量付费而不是订阅额度。

## 3. 写配置文件

复制 `config.example.json` 为 `config.json`（已在 .gitignore，不会进版本库），填入：

```json
{
  "web_console_token": "一个足够长的随机访问令牌",
  "claude_code_oauth_token": "sk-ant-oat01-...（setup-token 生成的，机密！）",
  "work_dir": "d:\\work\\AI_NovelGenerator-main",
  "host": "0.0.0.0",
  "port": 8765,
  "proxy": ""
}
```

- `web_console_token`：访问网页时校验的令牌。
- `claude_code_oauth_token`：订阅凭据；填了就用它，否则复用本机 `claude /login` 的登录。
- `proxy`：需翻墙访问 Anthropic 时填代理地址（如 `http://127.0.0.1:7890`），让 claude 子进程走代理；
  用全局/TUN 模式 VPN 则留空（整机流量已走代理）。
- 任一项都可被同名大写**环境变量覆盖**（如 `$env:PORT`），优先级：环境变量 > config.json > 默认。

## 4. 启动

```powershell
python server.py
```

启动后日志里会打印访问地址和 token，例如：
```
http://<本机IP>:8765/?token=xxxxxxxx
```

## 4. 手机访问

- **同一 WiFi**：手机浏览器打开 `http://<电脑局域网IP>:8765/`，输入 token。
- **在外网**：用 Tailscale 把手机和电脑组进同一私有网络，访问 Tailscale 分配的 IP；
  或用内网穿透把 **8765** 端口暴露出去（务必保留 token，建议再加 HTTPS）。

## 接口

| 方法 | 路径 | 说明 |
|------|------|------|
| POST | `/chat` | body `{prompt, session_id?}`，SSE 流式返回；`session_id` 用于多轮续接 |
| GET  | `/files?path=` | 列目录（限定在 WORK_DIR 内） |
| GET  | `/file?path=`  | 读文件（≤2MB，二进制不显示） |

## 安全提醒

这个网页背后能在你电脑上跑 `claude`（默认含写文件、执行命令的工具）。务必：
- 用足够长的随机 token；
- 不要把 **8765** 裸暴露公网；优先 Tailscale，其次穿透 + HTTPS + token；
- 想更安全可在 `server.py` 里给 `claude` 限制工具/权限（如只读 `--allowed-tools Read Glob Grep`）。
