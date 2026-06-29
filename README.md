<p align="center">
  <img src="assets/logo.png" alt="Ai_Flow" width="96" height="86">
</p>

<h2 align="center">Ai_Flow — 科研办公助手</h2>

<p align="center">
  <b>截图 + 多模态 AI → 流式输出 · 桌面自动化 ReAct Agent · 长期记忆</b>
</p>

---

## 这是什么

Ai_Flow 是一个 Windows 桌面常驻悬浮窗。按下快捷键截取屏幕任意区域，豆包多模态大模型流式解析。也可以直接在输入框打字对话。内置 **ReAct GUI Agent**，输入自然语言指令即可自动操作桌面和浏览器。

- **纯文本对话** — 悬浮窗底部输入框打字，Enter 发送
- **截图提问** — `Ctrl+D` 连续截图，缩略图累积，点发送统一提交
- **OCR 识别** — `Ctrl+R` 截图，PaddleOCR 本地识别返回可复制文字
- **🖥️ 桌面自动化** — `Ctrl+G` 或输入 `自动 任务`，AI 自动操作桌面/浏览器
- **🌐 浏览器自动化** — 自动打开网页、搜索、填写表单、点击链接（Playwright MCP + CDP 双引擎）
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
| **🖥️ GUI Agent** | ReAct 纯视觉桌面自动化：截图→思考→行动→循环 |
| **🌐 浏览器自动化** | Playwright MCP Server + CDP 回退，自动操作网页 |
| OCR 识别 | PaddleOCR 本地识别 |
| 语音输入 | 语音转文字，滚动选择句子，Ctrl+Enter 发送 |
| 长期记忆 | 对话后自动提取事实，下次对话关键词直返 |
| 模型切换 | mini / lite / pro 三档随时切换 |
| 即时设置 | 悬浮窗底部按钮，随时配置 API Key / 代理 / OCR |
| 系统托盘 | 最小化到托盘，右键菜单操作 |
| 对话管理 | 侧边栏切换/搜索/重命名/删除对话 |
| 隐私模式 | 悬浮窗在屏幕共享/录屏中不可见 |

## GUI Agent 桌面自动化

输入框输入 `自动 你的任务` 或用 `Ctrl+G` 打开任务面板，AI 自动操作桌面。

### 使用方式

```
自动 打开百度搜索python，然后点击进入一个python网页
自动 帮我关闭当前vscode页面
自动 打开浏览器进入b站搜索周杰伦
```

### ReAct 工作流程

```
Loop (最多15步):
  1. 截图全桌面 → 发送给豆包 VL
  2. 模型观察 → 思考 → 决定下一步动作
     ↓
  {"done": false, "thought": "看到百度首页...",
   "action": "click", "x": 500, "y": 300}
     ↓
  3. pyautogui 执行 (click / type / press / scroll / wait)
  4. 记录历史 → 下一轮
  5. 模型判定完成 → {"done": true, "reason": "..."}
↓
最终审计
```

### 核心设计

| 特性 | 说明 |
|------|------|
| **纯视觉定位** | 不使用 DOM 树/XPath，模型直接看截图返回归一化坐标 (0-1000)，运行时转换为像素 |
| **ReAct 范式** | 每步观察→思考→行动，模型动态决策，不依赖预定义步骤 |
| **坐标归一化** | `x_pixel = x * img_width / 1000`，模型不需要知道实际分辨率 |
| **双引擎** | 浏览器任务自动启用 Playwright MCP 打开页面，ReAct 接管后续操作 |
| **安全机制** | ESC 立即终止、最大15步、连续3次相同动作自动退出、pyautogui fail-safe |

## 快速上手

### 安装

```bash
git clone https://github.com/zebinlu7-a11y/screen-flow-ai-agent.git
cd screen-flow-ai-agent
pip install -r requirements.txt
```

### 配置

启动后点悬浮窗底部设置按钮（⚙️），填写：

| 配置项 | 获取地址 |
|--------|----------|
| API Key | [console.volcengine.com/ark/region:ark+cn-beijing/apiKey](https://console.volcengine.com/ark/region:ark+cn-beijing/apiKey) |
| 代理 | 如 `http://127.0.0.1:7897`，不需要则留空 |
| OCR 凭证 | [console.cloud.tencent.com/cam/capi](https://console.cloud.tencent.com/cam/capi)（可选） |

浏览器自动化需要 Node.js（Playwright MCP Server 依赖 `npx`）。

### 启动

```bash
python main.py
```

## 下载

[**Ai_Flow.zip**](https://github.com/zebinlu7-a11y/screen-flow-ai-agent/releases/download/v1.0/Ai_Flow.zip)（75MB），解压双击 `Ai_Flow.exe` 运行。

## 架构

```
┌─────────────────────────────────────────────────────┐
│                main.py (Qt 主进程)                   │
│  ┌──────────┐  ┌───────────┐  ┌──────────────────┐ │
│  │ 热键监听  │  │ ResultWin │  │ StreamWorker     │ │
│  │ (pynput) │  │ (悬浮窗)   │  │ (QThread 流式)   │ │
│  └──────────┘  └───────────┘  └──────────────────┘ │
│                                                      │
│  ┌──────────────────────────────────────────────┐   │
│  │ DesktopAgentProcessThread (子进程)             │   │
│  │  └─ subprocess → run_gui_agent.py             │   │
│  │     └─ ReAct Loop                             │   │
│  │        ├─ react_decide() 截图→豆包VL决策       │   │
│  │        ├─ pyautogui 执行动作                   │   │
│  │        └─ BrowserMCP (Playwright MCP/CDP)     │   │
│  └──────────────────────────────────────────────┘   │
│                                                      │
│  ┌──────────────────────────────────────────────┐   │
│  │ LangGraph 状态机 + RAG 记忆                    │   │
│  │  ├─ trim_history → call_vlm → END             │   │
│  │  ├─ FAISS 语义检索早期对话                     │   │
│  │  └─ 长期记忆: 自动提取 + 去重 + 注入提示词     │   │
│  └──────────────────────────────────────────────┘   │
└─────────────────────────────────────────────────────┘
```

## 项目结构

```text
AIRAG/
├── main.py                  # Qt 主入口: 悬浮窗 + 热键 + 子进程管理
├── config.py                # 全局配置 (API Key / 模型 / 代理)
├── build_exe.py             # PyInstaller 打包脚本
├── requirements.txt         # 依赖
│
├── agent/                   # AI Agent 模块
│   ├── state.py             # LangGraph AgentState 定义
│   ├── graph.py             # LangGraph 状态机 + 流式对话 + 记忆检索
│   ├── llm_client.py        # 豆包 VL ChatModel (OpenAI-compatible 封装)
│   ├── gui_agent.py         # 🖥️ ReAct GUI Agent (纯视觉循环 + MCP)
│   └── run_gui_agent.py     # GUI Agent 独立子进程入口
│
├── gui/                     # Qt 界面
│   ├── capture_window.py    # 截图遮罩 (多框拖拽 + Ctrl+Z)
│   ├── result_window.py     # 悬浮窗 (Markdown + 流式 + 侧边栏)
│   ├── api_key_dialog.py    # 设置弹窗
│   └── gui_agent_panel.py   # GUI Agent 任务面板
│
├── utils/                   # 工具
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
| 语音 | PyAudio + 腾讯云 ASR |

## License

MIT © [zebinlu7-a11y](https://github.com/zebinlu7-a11y)
