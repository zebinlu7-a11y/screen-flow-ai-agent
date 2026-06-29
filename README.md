<p align="center">
  <img src="assets/logo.png" alt="Ai_Flow" width="96" height="86">
</p>

<h2 align="center">Ai_Flow — 科研办公科研助手</h2>

<p align="center">
  <b>截图+AI 流式对话 · ReAct 桌面自动化 · 手机远程控制 · 长期记忆 · 隐私保护</b>
</p>

---

## 这是什么

Ai_Flow 是一个 Windows 桌面常驻悬浮窗。按下快捷键截取屏幕任意区域，豆包多模态大模型流式解析。也可以直接在输入框打字对话。

内置 **ReAct GUI Agent**，输入自然语言指令即可自动操作桌面和浏览器——打开软件、搜索网页、填写表单、点击按钮，完全模拟人类操作。支持**手机远程控制**，手机浏览器打开即可实时查看 PC 屏幕并发送指令。

- **纯文本对话** — 悬浮窗底部输入框打字，Enter 发送
- **截图提问** — `Ctrl+D` 连续截图，缩略图累积，点发送统一提交
- **OCR 识别** — `Ctrl+R` 截图，PaddleOCR 本地识别返回可复制文字
- **桌面自动化** — `Ctrl+G` 或输入 `自动 任务`，ReAct Agent 自动操作桌面/浏览器
- **手机远程控制** — 手机浏览器打开局域网地址，实时看截图+发指令+控制PC
- **浏览器自动化** — Playwright MCP Server + CDP 双引擎，自动打开网页操作
- **语音输入** — `Ctrl+Y` 语音转文字

## 操作演示

悬浮窗常驻桌面，底部随时打字。`Ctrl+F` 隐藏/显示。

<img src="assets/user2.png" alt="对话" width="700">

`Ctrl+D` 进入截图，拖拽松手自动确认变绿，连续多框。`Ctrl+Z` 撤销。

<img src="assets/user3.png" alt="连续框选" width="700">

<img src="assets/user.png" alt="截图" width="700">

框选完 Enter 放入对话框，缩略图累积，可删除单张。输入文字点发送。

<img src="assets/user1.png" alt="追问" width="700">

## 快捷键

| 快捷键 | 功能 |
|--------|------|
| `Ctrl+D` | 截图发送 |
| `Ctrl+R` | OCR 文字识别 |
| `Ctrl+G` | GUI Agent 桌面自动化 |
| `Ctrl+Y` | 语音识别 |
| `Ctrl+F` | 隐藏/显示窗口 |
| `ESC` | 终止当前 GUI Agent 操作 |
| `Ctrl+Q` | 退出程序 |

## 功能

| 功能 | 说明 |
|------|------|
| 常驻悬浮窗 | 启动即显示，可拖拽移动、四角缩放、半透明置顶 |
| 连续截图 | 松手自动确认，多框同时提交，Ctrl+Z 撤销 |
| 缩略图预览 | 截图累积显示在输入框上方，可单独删除 |
| 多模态 AI | 豆包 VL，支持多图 + 文字混合输入 |
| 流式输出 | 逐字显示，Markdown 渲染（表格/代码块/标题） |
| 多轮对话 | 上下文自动管理，FAISS 语义检索早期对话 |
| **🖥️ GUI Agent** | ReAct 纯视觉循环：截图→思考→行动，模拟人类操作桌面 |
| **📱 手机远程控制** | 手机浏览器控制 PC，实时截图+发指令，同WiFi/热点/Tailscale |
| **🔒 隐私保护** | 悬浮窗防屏幕捕获，截图/录屏/远程会议都看不到 Ai_Flow 窗口 |
| **🌐 浏览器自动化** | Playwright MCP + CDP 双引擎，自动打开网页、搜索、填写 |
| OCR 识别 | PaddleOCR 本地识别 |
| 语音输入 | 语音转文字，滚轮选句子，Ctrl+Enter 发送 |
| 长期记忆 | 对话后自动提取事实，下次对话关键词直返 |
| 模型切换 | mini / lite / pro 三档随时切换 |
| 即时设置 | 悬浮窗底部按钮，随时配置 API Key / 代理 / OCR |
| 系统托盘 | 最小化到托盘，右键菜单操作 |
| 对话管理 | 侧边栏切换/搜索/重命名/删除对话 |

## GUI Agent 桌面自动化

输入框输入 `自动 你的任务` 或用 `Ctrl+G` 打开任务面板，AI 自动操作桌面。

### 使用方式

```
自动 打开百度搜索python，然后点击进入一个python网页
自动 帮我关闭当前vscode页面
自动 打开浏览器进入b站搜索周杰伦
自动 打开计算器
自动 帮我在桌面上新建一个文件夹命名为test
```

### ReAct 工作流程

```
ReAct Loop (最多15步):
  1. 截图全桌面 → 发送给豆包 VL
  2. AI 看图思考 → 决定下一步动作
     ↓
  返回 {"done": false, "thought": "看到百度首页...",
        "action": "click", "x": 500, "y": 300}
     ↓
  3. pyautogui 执行 (click / type / press / scroll / wait)
  4. 截图看效果 → 下一步
  5. AI 判定完成 → {"done": true, "reason": "已打开python网页"}
↓
最终审计
```

### 核心设计

| 特性 | 说明 |
|------|------|
| **纯视觉定位** | 模型直接看截图返回归一化坐标 (0-1000)，运行时转换为像素 |
| **ReAct 范式** | 每步观察→思考→行动，AI 动态决策，不依赖预定义步骤 |
| **坐标归一化** | `x_pixel = x * img_width / 1000`，模型不需要知道实际分辨率 |
| **5 种动作** | click(点击)、type(打字)、press(按键)、scroll(滚动)、wait(等待) |
| **安全机制** | ESC 立即终止、最大15步、连续3次相同动作自动退出 |

## 手机远程控制

手机浏览器打开 PC 地址，实时查看截图、发送指令、控制 PC 执行任务。

<img src="assets/phone_remote.png" alt="手机远程控制" width="300">

### 连接方式

| 场景 | 做法 | 手机打开 |
|------|------|----------|
| 同 WiFi | 手机和PC同一WiFi | `http://192.168.1.x:8765` |
| 手机热点 | 手机开热点 → PC连 | 同上(局域网IP不变) |
| Cloudflare Tunnel | 自动获取公网URL | `https://xxx.trycloudflare.com` |

### 功能

- 📸 **实时截图** — 每 1.5 秒自动推送，降采样 720px 省流量
- 📝 **发送指令** — 输入自然语言任务，和PC端"自动 xxx"完全一样
- 🛑 **远程取消** — 点取消按钮 = PC 按 ESC
- 📊 **进度日志** — 实时显示 ReAct 每一步的执行状态

### 启动方式

启动 AIRAG 后悬浮窗自动显示连接地址。PC 端无需额外配置。

## 快速上手

### 安装

```bash
git clone https://github.com/zebinlu7-a11y/screen-flow-ai-agent.git
cd screen-flow-ai-agent
pip install -r requirements.txt
```

浏览器自动化需要 Node.js（Playwright MCP Server 依赖 `npx`）。

### 配置

启动后点悬浮窗底部设置按钮（⚙️），填写：

| 配置项 | 获取地址 |
|--------|----------|
| API Key | [console.volcengine.com/ark/region:ark+cn-beijing/apiKey](https://console.volcengine.com/ark/region:ark+cn-beijing/apiKey) |
| 代理 | 如 `http://127.0.0.1:7897`，不需要则留空 |
| OCR 凭证 | [console.cloud.tencent.com/cam/capi](https://console.cloud.tencent.com/cam/capi)（可选） |

### 启动

```bash
python main.py
```

## 下载

[**Ai_Flow.zip**](https://github.com/zebinlu7-a11y/screen-flow-ai-agent/releases/download/v1.0/Ai_Flow.zip)（75MB），解压双击 `Ai_Flow.exe` 运行。

## 架构

```
┌─────────────────────────────────────────────────────────────┐
│  main.py                      Qt 主进程 + 悬浮窗              │
│  ├─ 对话: StreamWorker → LangGraph → 豆包 VL 流式输出        │
│  ├─ 自动化: DesktopAgentProcessThread → 子进程 → ReAct Agent │
│  └─ 远程: RemoteServer → HTTP API → 手机浏览器控制           │
├─────────────────────────────────────────────────────────────┤
│  agent/                        AI 核心                       │
│  ├─ gui_agent.py    ReAct Agent + MCP 浏览器                 │
│  ├─ graph.py        LangGraph 状态机 + 流式对话               │
│  ├─ llm_client.py   豆包 VL ChatModel                        │
│  └─ run_gui_agent.py 子进程入口                              │
├─────────────────────────────────────────────────────────────┤
│  gui/             Qt 界面  |  utils/        工具库            │
│  remote/          手机远程  |  config.py     全局配置          │
└─────────────────────────────────────────────────────────────┘
```

## 项目结构

```text
AIRAG/
├── main.py                  # Qt 主入口: 悬浮窗 + 热键 + 子进程管理 + 远程服务
├── config.py                # 全局配置 (API Key / 模型 / 代理 / 端口)
├── build_exe.py             # PyInstaller 打包脚本
├── requirements.txt         # 依赖
│
├── agent/                   # AI Agent 模块
│   ├── state.py             # LangGraph AgentState 定义
│   ├── graph.py             # LangGraph 状态机 + 流式对话 + 记忆检索
│   ├── llm_client.py        # 豆包 VL ChatModel (OpenAI-compatible 封装)
│   ├── gui_agent.py         # 🖥️ ReAct GUI Agent (纯视觉循环 + MCP 浏览器)
│   └── run_gui_agent.py     # GUI Agent 独立子进程入口
│
├── gui/                     # Qt 界面
│   ├── capture_window.py    # 截图遮罩 (多框拖拽 + Ctrl+Z)
│   ├── result_window.py     # 悬浮窗 (Markdown + 流式 + 侧边栏 + 隐私模式)
│   ├── api_key_dialog.py    # 设置弹窗
│   └── gui_agent_panel.py   # GUI Agent 任务面板
│
├── remote/                  # 手机远程控制
│   ├── server.py            # HTTP API 服务 (aiohttp) + Cloudflare Tunnel
│   └── phone.html           # 手机端界面 (深色主题, 响应式)
│
├── utils/                   # 工具库
│   ├── image_tool.py        # 图片压缩 / Base64
│   ├── ocr_tool.py          # PaddleOCR 本地识别
│   ├── context_store.py     # 对话持久化
│   ├── api_key_manager.py   # 本地配置存储
│   ├── user_manager.py      # 多用户 + 对话管理
│   ├── memory_store.py      # 长期记忆提取/去重/检索
│   ├── vector_store.py      # FAISS + TF-IDF 语义搜索
│   └── speech_worker.py     # 语音识别工作线程
│
└── assets/                  # 截图和图标
```

## 技术栈

| 层次 | 技术 |
|------|------|
| UI | PyQt6, 无边框悬浮窗, 系统托盘 |
| 热键 | pynput GlobalHotKeys |
| Agent 范式 | **ReAct** (Reasoning + Acting) 纯视觉循环 |
| 视觉定位 | 归一化坐标 0-1000 → 像素转换 |
| LLM | 豆包 VL (火山引擎方舟), OpenAI-compatible API |
| 流式 | LangChain BaseChatModel, SSE streaming |
| 状态机 | **LangGraph** + MemorySaver 检查点 |
| 浏览器自动化 | **MCP** (Model Context Protocol) JSON-RPC 2.0 + Playwright CDP 回退 |
| 桌面操作 | pyautogui, pyperclip |
| 语义搜索 | **FAISS** IndexFlatIP + TF-IDF + jieba 分词 |
| 长期记忆 | LLM 提取事实 → Jaccard 去重 → FAISS 索引 → 关键词直返 |
| 远程控制 | aiohttp HTTP API + 轮询 + Cloudflare Tunnel |
| 语音 | PyAudio + 腾讯云 ASR |
| 打包 | PyInstaller |

## 并发架构

```
1 个主进程 ─┬─ 主线程 (Qt QEventLoop)    UI渲染 + 热键 + 定时器
            ├─ QThread: StreamWorker      asyncio 协程 → 流式对话
            ├─ QThread: DesktopAgentThread stdout 管道 → 管理子进程
            ├─ QThread: MemWorker         同步阻塞 → 长期记忆提取
            └─ 守护线程: RemoteServer      aiohttp 协程 → 手机 HTTP API

1 个子进程 ─── python run_gui_agent.py     同步阻塞 → ReAct Agent 执行
```

### 并发单元职责与通信

| 并发单元 | 类型 | 并发模型 | 职责 |
|----------|------|----------|------|
| 主线程 | 线程 #1 | Qt C++ QEventLoop 事件驱动 | UI 渲染、信号槽、热键响应、每秒巡检手机指令和截图推送 |
| StreamWorker | QThread #2 | asyncio 单线程协程 | 流式对话: FAISS 检索 → 豆包 VL 逐 token 推送 |
| DesktopAgentThread | QThread #3 | stdout 管道同步阻塞读 | GUI Agent 子进程管理: 启动、读进度、读结果 |
| MemWorker | QThread #4 | 同步阻塞 | 长期记忆: LLM 提取事实 → Jaccard 去重 → FAISS 索引 |
| RemoteServer | 守护线程 #5 | aiohttp 单线程协程 | 手机 HTTP API: /api/updates(轮询) /api/command(发指令) |
| **子进程** | **独立进程** | **同步阻塞** | **ReAct Agent: 截图→豆包决策→pyautogui执行→循环** |

### 通信详情

| 通信双方 | 方向 | 通信方式 | 同步/异步 | 线程安全机制 |
|----------|------|----------|-----------|-------------|
| StreamWorker → 主线程 | 子线程→主 | `pyqtSignal.emit(token)` | 异步 | Qt 深拷贝到主线程事件队列 |
| DesktopAgentThread → 主线程 | 子线程→主 | `pyqtSignal.emit(msg)` | 异步 | Qt 信号槽，数据拷贝传递 |
| 子进程 → DesktopAgentThread | 子进程→子线程 | `print("[进度] xxx")` → stdout 管道 | 异步 | OS 管道缓冲区，`for line in proc.stdout` 读 |
| 子进程 → 主线程 (结果) | 子进程→磁盘→主线程 | JSON 结果文件 | 同步(等文件) | 子进程写完父进程才读 |
| 主线程 → 子进程 (取消) | 主线程→文件→子进程 | cancel 文件 + `proc.terminate()` | 异步/同步双保险 | 每步 `os.path.exists()` 检查 |
| RemoteServer → 主线程 | 守护线程→主 | `_remote_pending_task = task` | 异步(变量写入) | GIL 保证字符串赋值原子性 |
| 主线程 → RemoteServer | 主→守护线程 | `send_progress/set_screenshot` | 异步 | `threading.Lock` 保护共享数据 |
| 手机 ↔ RemoteServer | HTTP | POST `/api/command` + GET `/api/updates` | 异步(轮询1.5s) | aiohttp 协程，多请求间无共享状态 |
| MemWorker → 主线程 | 子线程→主 | `pyqtSignal.emit(facts)` | 异步 | Qt 信号槽，数据拷贝 |
| GUI Agent → 豆包 API | 子进程→火山引擎 | HTTPS POST | 同步(阻塞等2-5s) | 每次请求独立 |

### 为什么这样设计

| 决策 | 原因 |
|------|------|
| I/O 密集用协程 | 等 LLM 响应/HTTP 请求，单线程协程切换纳秒级，比多线程省内存省切换开销 |
| 简单阻塞用子线程 | pyautogui/文件 I/O 不支持 async，放子线程 GIL 释放时不挡主线程 |
| 事件循环冲突用子进程 | Playwright 同步 API 内部有 asyncio 事件循环，和 Qt C++ QEventLoop 不能同进程共存——两个 `run_forever()` 抢线程。子进程物理隔离 |
| 跨线程通信用 Qt 信号槽 | Qt 自动深拷贝数据到目标线程队列，不加锁，线程安全 |

## License

MIT © [zebinlu7-a11y](https://github.com/zebinlu7-a11y)
