<p align="center">
  <img src="assets/logo.png" alt="Ai_Flow" width="96" height="86">
</p>

<h2 align="center">Ai_Flow — 截图解析悬浮窗</h2>

<p align="center">
  <b>截图 + 多模态大模型 / OCR → 流式输出到悬浮窗</b>
</p>

---

## 这是什么

启动后桌面常驻悬浮窗，底部输入框可直接打字对话。按下快捷键截取屏幕任意区域，大模型解析或 OCR 识别后结果流式显示。

- 纯文本对话：底部输入框打字，Enter 发送
- 截图提问：`Ctrl+D` 连续截图，缩略图累积，点发送统一提交
- OCR 识别：`Ctrl+R` 截图，腾讯云 OCR 返回可复制文字

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
| `Ctrl+F` | 隐藏/显示窗口 |
| `Ctrl+G` | GUI Agent 桌面自动化 |
| `Ctrl+Y` | 语音识别 |
| `ESC` | 终止当前 GUI Agent 操作 |
| `Ctrl+Q` | 退出程序 |

## 功能

| 功能 | 说明 |
|------|------|
| 常驻悬浮窗 | 启动即显示，可拖拽移动、四角缩放、半透明置顶 |
| 连续截图 | 松手自动确认，多框同时提交，Ctrl+Z 撤销 |
| 缩略图预览 | 截图累积显示在输入框上方，可单独删除 |
| 多模态 AI | 豆包 VL，支持多图 + 文字混合输入 |
| 流式输出 | 逐字显示，Markdown 渲染 |
| 多轮对话 | 上下文自动管理，语义检索历史 |
| **🖥️ GUI Agent** | ReAct 纯视觉桌面自动化：截图→思考→行动→循环，自动操作浏览器/桌面应用 |
| **🌐 浏览器自动化** | Playwright MCP Server + CDP 双引擎，自动打开网页、填写表单、点击链接 |
| OCR 识别 | PaddleOCR 本地识别 |
| 语音输入 | 语音转文字，自动填入输入框 |
| 模型切换 | mini / lite / pro 三档随时切换 |
| 长期记忆 | 自动从对话提取事实，下次对话关键词直返 |
| 即时设置 | 悬浮窗底部按钮，随时配置 API Key / OCR 凭证 |
| 系统托盘 | 最小化到托盘，右键菜单操作 |
| 跨平台 | Windows / Mac / Linux |

## GUI Agent 桌面自动化

输入框输入 `自动 你的任务` 或用 `Ctrl+G` 打开任务面板，AI 自动操作桌面。

### 使用方式

```
自动 打开百度搜索python，然后点击进入一个python网页
自动 帮我关闭当前vscode页面
自动 打开浏览器进入b站搜索周杰伦
```

### 工作流程

```
ReAct Loop:
  1. 截图全桌面 → 发送给豆包 VL
  2. 模型观察 → 思考 → 决定下一步动作
  3. 执行动作 (click / type / press / scroll / wait)
  4. 截图看结果 → 回到步骤 1
  5. 模型判定任务完成 → 最终审计
```

### 双引擎

| 引擎 | 说明 |
|------|------|
| 纯视觉 | 全桌面截图，豆包 VL 直接返回归一化坐标，pyautogui 执行 |
| 浏览器 MCP | 浏览器任务自动启用：Playwright MCP Server 打开页面 → ReAct 接管页面操作 |

### 安全机制

| 机制 | 说明 |
|------|------|
| `ESC` 终止 | 随时按 ESC 立即终止当前操作 |
| 防卡死 | 连续 3 步相同动作自动退出 |
| 最大步数 | 最多 15 步，防止无限循环 |
| fail-safe | pyautogui 安全模式，鼠标移到四角立即停止 |

## 快速上手

### 安装

```bash
git clone https://github.com/zebinlu7-a11y/screen-flow-ai-agent.git
cd screen-flow-ai-agent
pip install -r requirements.txt
```

### 配置

启动后点悬浮窗底部设置按钮，填写：

| 配置项 | 获取地址 |
|--------|----------|
| API Key | [console.volcengine.com/ark/region:ark+cn-beijing/apiKey](https://console.volcengine.com/ark/region:ark+cn-beijing/apiKey) |
| 代理 | 如 `http://127.0.0.1:7897`，不需要则留空 |
| OCR 凭证 | [console.cloud.tencent.com/cam/capi](https://console.cloud.tencent.com/cam/capi)（可选） |

模型调用使用 OpenAI-compatible 格式，默认 base_url 为 `https://ark.cn-beijing.volces.com/api/v3`。

### 启动

```bash
python main.py
```

## 下载

[**Ai_Flow.zip**](https://github.com/zebinlu7-a11y/screen-flow-ai-agent/releases/download/v1.0/Ai_Flow.zip)（75MB），解压双击 `Ai_Flow.exe` 运行。

## 项目结构

```text
AIRAG/
├── main.py                  # 主入口：Qt 悬浮窗 + 系统托盘 + 热键
├── config.py                # 全局配置 (API Key / 模型 / 代理)
├── build_exe.py             # PyInstaller 打包脚本
├── requirements.txt         # 依赖
│
├── agent/                   # AI Agent 模块
│   ├── state.py             # LangGraph 状态定义
│   ├── graph.py             # LangGraph 状态机 + 流式对话 + 长期记忆
│   ├── llm_client.py        # 豆包 VL ChatModel (OpenAI-compatible)
│   ├── gui_agent.py         # 🖥️ ReAct GUI Agent: 纯视觉循环自动化
│   └── run_gui_agent.py     # GUI Agent 独立子进程入口
│
├── gui/                     # Qt 界面
│   ├── capture_window.py    # 截图遮罩 (多框拖拽 + Ctrl+Z 撤销)
│   ├── result_window.py     # 悬浮窗 (Markdown 渲染 + 流式输出 + 模型切换)
│   ├── api_key_dialog.py    # API Key / OCR / 代理 设置弹窗
│   └── gui_agent_panel.py   # GUI Agent 任务输入面板
│
├── utils/                   # 工具
│   ├── image_tool.py        # 图片压缩 / Base64 转换
│   ├── ocr_tool.py          # PaddleOCR 本地识别
│   ├── context_store.py     # 对话上下文持久化
│   ├── api_key_manager.py   # API Key / 模型 本地存储
│   ├── user_manager.py      # 多用户 + 对话管理
│   ├── memory_store.py      # 长期记忆提取与存储
│   ├── vector_store.py      # FAISS 语义检索
│   └── speech_worker.py     # 语音识别工作线程
│
└── assets/                  # 截图和图标
```

## License

MIT © [zebinlu7-a11y](https://github.com/zebinlu7-a11y)
