# claude-web-console — 项目上下文

> 这是给 Claude Code 的项目记忆，新会话在本目录工作时会自动读取。
> 面向人的使用说明见 [README.md](README.md)。

## 这是什么

一个**本地**网页操控台：在手机/浏览器上跟 `claude` 对话、查看指定目录文件。
对话进程、文件读写全在本机，手机只是远程界面。源于需求：「在手机上用 Max 订阅额度
操控 claude 在指定目录的对话，且只让自己设备访问」。

## 架构与文件

- `server.py` — FastAPI。把 `claude -p --output-format stream-json --verbose --include-partial-messages`
  包成 HTTP：
  - `POST /chat`（SSE 流式，body `{prompt, session_id?, model?, images?}`，prompt 经 stdin 传入避开 Windows 转义；
    model 仅接受 `/api/info` 返回的白名单，命中则加 `--model`；客户端断开会 kill 子进程＝「停止生成」；
    **图片输入双轨**：带 `images:[{media_type,data(base64)}]` 时切 `--input-format stream-json`、把图片+文本
    作为 content 块写 stdin，靠 `result` 事件主动 kill 收尾（该模式进程不自退）；纯文本仍走原 -p 文本 stdin 链路）
  - `GET /api/info`（返回 work_dir / claude / 可选模型列表 models / 默认模型 default_model）
  - `GET /sessions`、`GET /session/{sid}`（**只读**历史对话，见下；/session 回放含工具 input/result）
  - `GET /files?path=`、`GET /file?path=`（限定在 WORK_DIR 内，防 `../` 越权）
  - 全部接口用 `web_console_token` 鉴权（Authorization: Bearer 或 ?token=）
- `index.html` — 手机端单页（Claude Code 风格 UI）：对话(逐字流式) 为主视图；顶栏含
  ＋新对话 / 历史 / 文件 / 设置 四个图标按钮；历史(浏览/回放/续聊)、文件树、文件查看、
  设置(模型选择/工作目录/完成通知开关/退出登录)均为从右滑入的覆盖面板。
  输入框左侧图片按钮（拍照/相册）→ 选图转 base64、预览缩略图条、随消息发出。
  刷新恢复：当前 sessionId 存 localStorage（`cwc_session`），重新打开时自动回放上次对话、接着聊。
- `config.json`（**gitignore**，机密）— 配置项；env 同名大写变量可覆盖，优先级 env > config > 默认
- `config.example.json` / `requirements.txt` / `README.md`

配置项：`web_console_token` / `auth_mode` / `claude_code_oauth_token` / `base_url` / `auth_token` / `api_key`
/ `work_dir` / `work_dirs`（可选，多项目白名单，前端可切换）/ `host` / `port` / `proxy` / `claude_bin`
/ `model`（默认模型，空＝CLI 默认）/ `models`（前端下拉可选列表，可整体覆盖）
/ `permission_mode`（默认权限模式，空＝CLI 默认）/ `permission_modes`（前端下拉可选列表，可整体覆盖）
/ `effort`（默认推理强度，空＝CLI 默认）/ `efforts`（前端下拉可选列表，可整体覆盖）
/ `serverchan_sendkey`（可选，Server酱 SendKey，填了则任务完成/出错时微信推送）

**鉴权模式（`auth_mode`）**：
- `subscription`（默认）：走 Max/订阅额度，用 `claude_code_oauth_token` 或本机已登录凭据。
- `relay`：走第三方「中转站」的 Anthropic 兼容接口——按 `base_url` 注入 `ANTHROPIC_BASE_URL`，
  密钥用 `auth_token`（→ `ANTHROPIC_AUTH_TOKEN`，多数 claude-code 中转站走 Bearer）或 `api_key`（→ `ANTHROPIC_API_KEY`），二选一。
- 留空 `auth_mode` 时：填了 `base_url` 即视为 relay，否则 subscription。
- 两模式**互斥注入**：relay 模式会清掉 `CLAUDE_CODE_OAUTH_TOKEN`，并在用 `auth_token` 时清掉 `ANTHROPIC_API_KEY`
  （`AUTH_TOKEN` 与 `API_KEY` 同时存在会被官方 API 以 401 拒绝；API key 也会盖过订阅 OAuth）。
- 注：中转站＝按量付费、用别人代理的额度，**不走 Max 订阅**——是绕开「不要设 ANTHROPIC_API_KEY」那条订阅红线的另一条独立路径，自行评估中转站可信度与数据风险。

## 关键决策（别推翻）

- **用 Max 订阅额度，不用 API key**：靠 `CLAUDE_CODE_OAUTH_TOKEN`（`claude setup-token` 生成）
  或本机 `claude /login` 已登录凭据。**不要设 `ANTHROPIC_API_KEY`**（否则走按量付费）。
  已验证 `apiKeySource=none`，即走的订阅。
- **走 CLI 无头模式而非 Agent SDK 库**：因为官方 Agent SDK 文档默认引导配 API key；
  直接 spawn `claude` CLI + 订阅登录才用得上订阅额度。
- **访问控制 = 访问令牌**（用户选的最低成本方案）。手机访问优先 Tailscale，其次内网穿透到
  端口 **8765**，务必带 token。
- **代理**：本机 HTTP 代理端口 **7899**（已写进 config）。全局/TUN VPN 则无需 proxy。
- Windows：`claude` 是 `.cmd`，子进程经 `cmd /c` 运行；asyncio 用 Proactor 事件循环。
- **历史对话直接读 claude CLI 的落盘**：`~/.claude/projects/<编码目录>/<sid>.jsonl`，
  编码＝把 WORK_DIR 绝对路径里所有非字母数字字符替成 `-`（`d:\work\AI_NovelGenerator-main`
  → `d--work-AI-NovelGenerator-main`）。每行一个事件，取 type=user/assistant 且非 isSidechain
  的 message.content（text / tool_use 块）。**只读不改**——续聊仍走 `/chat` 的 `--resume`。

## 当前状态（已端到端验证）

流式对话 ✅ / 走订阅额度 ✅ / 代理 7899 ✅ / 多轮 session_id ✅ / 文件浏览+鉴权 ✅ / config.json ✅
/ 工具可观测性 ✅ / 历史回放续聊 ✅ / markdown+表格 ✅ / thinking ✅ / 结构化提问 ✅ / 微信推送 ✅ / 图片输入 ✅ / 权限模式 ✅ / 多项目切换 ✅
默认模型为 CLI 默认的 `claude-sonnet-4-6`；前端 header 有模型下拉（默认/Opus/Sonnet/Haiku），
选择存 localStorage 并随 /chat 传给后端。发送中按钮变「停止」，点它中断 fetch、后端随之 kill 子进程。
> 当前 config 实际跑在 **relay 模式**（base_url 指向中转站、用 api_key）——图片输入等验证都走的它，
> 未耗 Max 额度。切回订阅只需把 auth_mode 改回 subscription。
历史会话：「历史」标签列出本目录所有历史 jsonl（摘要+时间+条数，按 mtime 倒序）。点某条 → 把对话
**载入「对话」视图**（替换当前 #msgs 内容、AI 渲染 markdown）、设好 sessionId、切到对话页
并滚到最新一条，随后直接发消息即走 --resume 续聊、新回复追加在同一对话流里。
（不再用独立只读浮层——历史与对话合一。）
> 长会话分页（已实测）：`/session/{sid}` 支持 `?end=&limit=`，默认返回**最近** limit 条
> （`_SESSION_MAX_EVENTS`=1000）并带 `start/end/total`。前端默认载最近一段、滚到底＝最新；
> 上滑到顶触发 `loadOlderHistory` 用 `?end=当前最早索引` 拉更早的一段、正序渲染进 fragment
> 后 insertBefore 到最前、并按 scrollHeight 增量维持滚动位置（不跳动）。修掉了「长会话只显示
> 最早 1000 条、最近的反而被截掉」的问题。

工具调用可观测性：盲操作手机端的核心——必须能看清电脑上的 claude 到底做了什么。前端把流里完整
`assistant` 消息的 `tool_use` 块渲染成**可折叠工具卡片**（图标+工具名+副标题：Bash/PowerShell 显示命令、
Read/Write/Edit 显示文件路径、Grep 显示 pattern 等；展开看完整 input）；`user` 消息里的 `tool_result`
按 `tool_use_id` **回填**到对应卡片（✓/出错状态 + 输出，出错默认展开）。历史回放同理（/session 多发
tool input/result，前端复用 renderToolCard/fillToolResult）。
> 数据来源（已实测 SSE）：工具完整 input 在 `assistant` 消息的 tool_use 块；结果在**随后的 `user`
> 消息**的 tool_result 块（{tool_use_id, content, is_error}）。流式的 input_json_delta 太碎，弃用，
> 一律用完整 assistant 消息渲染。后端 /chat 原样透传这些行无需改动；只 /session 增了 input/result。

完成通知 + 刷新恢复（移动刚需，纯前端）：
- 刷新恢复：每次拿到 session_id 存 localStorage `cwc_session`，重新打开 App 时 `openSession(last, true)`
  回放上次对话、继续聊；新对话按钮 / 空会话会清除该键。
- 完成通知：设置里开关（`cwc_notify`，需浏览器授权 Notification）。**仅当页面切到后台**
  （visibilityState!=visible）时，一轮对话完成/出错才触发：标题闪烁 + 系统通知；停止生成不通知。
  回到前台自动停止闪烁。本地项目不上 Web Push（要 SW + 推送服务器），用浏览器原生 Notification 足够。

微信推送（后端，Server酱，关掉浏览器/锁屏也能收到）：
- config 填 `serverchan_sendkey` 才启用；`/chat` 子进程**自然跑完/出错**时后端 POST 推送
  （title+desp，摘要取末条 result 文本）；**客户端中断「停止生成」不推**。
- 自动识别两种版本（`_serverchan_url`）：sendkey 形如 `sctp{uid}t...` ＝ Server酱³，推
  `https://<uid>.push.ft07.com/send/<key>.send`（uid 用正则 `^sctp(\d+)t` 从 key 里抽）；
  否则当 Turbo 版，推 `https://sctapi.ftqq.com/<key>.send`。
- 推送在线程池跑（不阻塞事件循环）、走与 claude 同一代理、失败只记 warn 绝不影响对话。
- 坑（已修）：Server酱³ 的 `push.ft07.com` 套 Cloudflare，默认 `Python-urllib` UA 会被 403 +
  `error code: 1010` 拦掉——请求必须带常规浏览器 User-Agent 才放行。
- 注：Server酱³ 默认推到它自己的 **App**（非微信服务号，那是老 Turbo 版的行为）；返回
  `{"code":0,"message":"SUCCESS","pushid":...}` 即成功下发，投递去向由后台「消息通道」决定。
- `/api/info` 返回 `wechat_push` 布尔，前端设置面板显示启用状态（开关在服务端，非前端可控）。
- 与浏览器通知的分工：浏览器通知＝前台/刚切后台、关页面即失效；微信推送＝彻底离开设备的兜底。

结构化提问（AskUserQuestion）：前端识别流里完整 `assistant` 消息中的 AskUserQuestion `tool_use`
（含 `input.questions`：question/header/options[{label,description}]/multiSelect），渲染成**可点选项卡片**。
单问题单选＝点一下即发；多选/多问题＝选中后点「发送回答」。点选的答案作为**下一条普通消息**走 --resume 发出。
> 关键发现（已实测）：`claude -p` 无头 stream-json 模式下 AskUserQuestion **无法内联回答**——harness 会
> 自动合成 `{"type":"tool_result","is_error":true,"content":"Answer questions?"}` 驳回它，然后模型改用纯文本
> 追问。所以没法做「真·内联工具应答」（那需要 Agent SDK 的控制协议，与本项目 CLI-only 决策冲突）。
> 当前方案＝把所选 label 当普通回复发回去，模型正好在文本追问、能正确理解。后端无需改动。

thinking 状态：发送后立即显示「● 思考中…」脉冲提示，首个内容到达即移除；流式 `thinking_delta`
渲染成独立的 💭 思考气泡（虚线、灰斜体），历史回放也会还原 thinking 块（后端 /session 多抽 thinking 块，
kind="thinking"）。注：扩展思考内容仅在模型实际开启 thinking 时才有；没有时也有「思考中…」等待提示。

图片输入（手机拍照/截图发给 claude，多模态）：前端图片按钮选图 → FileReader 转 base64 + media_type、
预览可删缩略图；发送时随 body `images` 发出、用户气泡回显缩略图。后端**双轨**（见上 /chat）：有图才切
`--input-format stream-json` 把 `{type:image,source:{type:base64,media_type,data}}` 块 + text 块作为一条
user 消息写入 stdin。
> 关键发现（已实测，走 relay 验证未耗 Max 额度）：stream-json 输入模式下进程**发完消息不自退**、挂着
> 等下一条 stdin——所以必须靠 `result` 事件主动 kill 收尾，否则 /chat 会挂死。这就是「有图才切、纯文本
> 不动」双轨的原因。图片块 CLI 能正确解析（多模态输入），relay 中转站也支持 vision。

权限模式（工具权限限制）：设置里下拉选 permission-mode（默认/plan/acceptEdits/bypassPermissions），
存 localStorage（`cwc_perm`）、随 /chat 传给后端，后端白名单校验后加 `--permission-mode`。
输入框上方状态条仿 Claude Code 终端（`⏸ plan mode on` 绿 / `⏵⏵ accept edits on` 蓝 /
`⏵⏵ bypass permissions on` 红；default 不显示）。CLI 合法值：default/plan/acceptEdits/auto/dontAsk/bypassPermissions。
> 注：`-p` 无头模式下权限确认无法内联（同 AskUserQuestion 的 harness 限制），所以有用的主要是
> **plan**（只读规划不执行，最安全）与 acceptEdits/bypassPermissions（放行）；default 遇需确认操作可能被拒。
> 已实测 `--permission-mode plan` 参数正确透传子进程且生效（走 relay 验证）。

推理强度（`--effort`）：设置里下拉选 low/medium/high/xhigh/max（空＝CLI 默认），存 localStorage
（`cwc_effort`）、随 /chat 传给后端，后端白名单校验（`_ALLOWED_EFFORTS`）后加 `--effort`。
已实测非法值 400、`/api/info` 返回 efforts 列表。与模型/权限模式同一套「config 默认+白名单+前端下拉」结构。

多项目切换：config 配 `work_dirs` 白名单（每项字符串或 {path,label}）则设置里出现工作目录下拉；
`/files /file /sessions /session` 带 `?work_dir=`、`/chat` body 带 `work_dir`，后端 `resolve_work_dir`
**白名单校验**（不在白名单一律 403）后作为该请求的工作目录/子进程 cwd。切目录会开新对话（各目录历史独立，
因 jsonl 落盘目录按工作目录编码）。选择存 localStorage `cwc_workdir`。未配 work_dirs 时退回单个 work_dir（向后兼容）。
> 安全红线：**绝不接受前端传任意路径当工作目录**（那＝任意目录跑 claude）。前端只能选白名单里的，
> safe_path 也改成按传入 base 隔离越权。已实测：白名单外路径/父目录/`../`越权全部 403。
AI 气泡渲染 markdown：index.html 内置无依赖的 `renderMarkdown()`（先转义再排版，防 XSS；
支持标题/加粗/斜体/行内与围栏代码/有序无序列表/引用/分隔线/链接/GFM 表格(含 :--: 对齐)）。实时对话流式期间显示纯文本，
结束时整体渲染 markdown（避免半截语法）；历史回放的 AI 消息直接渲染，用户消息保持纯文本。
> 注：`/sessions`、`/session/{id}` 已用真实落盘文件**端到端验证**（列表/摘要/回放均正常）。
> 模型切换 / 停止生成 / 续聊 这三条**走 claude 子进程的链路尚未实机验证**（需本机登录态），下次跑起来确认。

## 待办 / 下一步候选

- [ ] **换掉泄露的凭据**：OAuth token 与 relay 的 api_key、Server酱 sendkey 都在对话里贴出过，建议重置。
- [x] 模型做成 config 可选项（命令加 `--model`，支持选 Opus）。
- [x] `/chat` 加「停止生成」按钮（前端中断 fetch + 后端 kill 子进程）。
- [x] 工具调用可观测性（可折叠卡片 + 结果回填）。
- [x] 历史对话浏览/回放/续聊；刷新恢复。
- [x] markdown + GFM 表格渲染；thinking 气泡；结构化提问选项卡片。
- [x] 微信/App 推送（Server酱，关页面也能收到）。
- [x] 图片输入（手机拍照/截图，双轨 stream-json）。
- [x] 工具权限限制：权限模式前端可选（default/plan/acceptEdits/bypassPermissions）。
- [x] 多项目切换：config `work_dirs` 白名单，前端设置里切换工作目录（安全：只能选白名单）。
- [ ] 文件查看加「编辑保存」。
- [ ] 可选：工具白/黑名单（config `allowed_tools`/`disallowed_tools` → `--allowed-tools`）进一步收紧。
- [ ] 语音输入（Web Speech API）/ 多项目切换（work_dir 可在设置里选）。

## 安全红线

网页背后能在本机跑 `claude`（默认含写文件/执行命令）。务必：足够长的随机 token；
不要裸暴露 8765 到公网（优先 Tailscale，否则穿透+HTTPS+token）；`config.json` 永不进 git。
