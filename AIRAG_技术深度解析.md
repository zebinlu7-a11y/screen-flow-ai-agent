# AIRAG 项目深度技术解析 — 面试准备文档

## 项目概述

**AIRAG (AI RAG + Agent)** 是一个 Windows 桌面 AI 助手，融合了三个 AI 范式：

1.  **多模态对话** — LangGraph 状态机 + 豆包 VL,支持截图+文字混合输入,流式输出
2.  **GUI Agent 桌面自动化** — ReAct 纯视觉循环,pyautogui 操作桌面,浏览器自动化
3.  **RAG 记忆系统** — FAISS 语义检索 + TF-IDF 中文向量化 + 长期记忆提取

技术栈：PyQt6 + LangChain + LangGraph + FAISS + Playwright MCP + 豆包(火山引擎方舟)

---

## 一、整体架构

```
┌─────────────────────────────────────────────────────┐
│                   main.py (Qt 主进程)                │
│  ┌──────────┐  ┌───────────┐  ┌──────────────────┐ │
│  │ 热键监听  │  │ ResultWin │  │ StreamWorker     │ │
│  │ (pynput) │  │ (悬浮窗)   │  │ (QThread 流式)   │ │
│  └──────────┘  └───────────┘  └──────────────────┘ │
│                                                      │
│  ┌──────────────────────────────────────────────┐   │
│  │ DesktopAgentProcessThread (子进程)             │   │
│  │  └─ subprocess → run_gui_agent.py             │   │
│  │     └─ gui_agent.run_gui_task()               │   │
│  │        ├─ BrowserMCP (Playwright MCP/CDP)     │   │
│  │        └─ ReAct Loop (截图→思考→行动→循环)    │   │
│  └──────────────────────────────────────────────┘   │
└─────────────────────────────────────────────────────┘
```

**为什么用子进程？** Qt 是 asyncio 事件循环,Playwright 同步 API 会阻塞。把 GUI Agent 放进独立子进程,通过管道实时通信,通过 JSON 结果文件返回最终结果。

---

## 二、LangGraph 状态机 — 多模态对话引擎

### 图结构

```
trim_history → call_vlm → END
```

两节点线性图,用 `MemorySaver` 检查点器按 `thread_id` 持久化状态。每次对话结束后状态保存到内存,下次对话自动恢复之前的上下文。

### 混合上下文策略 (graph.py `stream_graph()`)

这是解决"长对话上下文超限"的核心方案:

```
┌──────────────────────────────────────┐
│  对话历史 (全量 messages)             │
│                                      │
│  ┌─────────────┐ ┌─────────────────┐│
│  │ 最近 N 轮    │ │ 早期对话         ││
│  │ (完整保留)   │ │ (FAISS 语义检索) ││
│  │ RECENT_ROUNDS│ │ 索引→搜索 top3  ││
│  │ × 2 条消息   │ │ 相关消息注入提示  ││
│  └─────────────┘ └─────────────────┘│
│          ↓               ↓          │
│     SystemPrompt + FAISS上下文       │
│     + recent_msgs + input_msg       │
└──────────────────────────────────────┘
```

**关键点**：
- 最近的 3 轮(6 条消息)完整保留 — 保证对话连续性
- 早期对话用 FAISS 语义检索 — 找到与当前问题最相关的历史消息
- 没有硬 token 上限 — 靠检索条数控制上下文大小
- 这是**结构记忆+内容记忆**的混合方案

### 关键词直返 (Direct Keyword Reply)

```python
graph.py:_direct_keyword_reply()
```

**完全不调大模型**的快速路径。当用户问题中的关键词命中长期记忆中的事实时,直接返回匹配结果。例：用户之前说过"我叫张三",下次问"我叫什么名字"就直接返回,不消耗 token。

### 流式输出

`stream_graph()` 是一个 async generator。它构建完整上下文后,调用 `ChatDoubaoVL.stream()` 获取 SSE 流,逐个 token yield。在 Qt 端,`StreamWorker` (QThread) 通过 `token_received` 信号将每个 token 发送到主线程的 `ResultWindow`,实现逐字渲染。

---

## 三、ReAct Agent — GUI 桌面自动化

### 什么是 ReAct？

**Re**asoning + **Act**ing — 不是预先规划所有步骤，而是每一步都:
1. **观察**当前屏幕 → 截图
2. **推理**下一步该做什么 → 豆包 VL 看图思考
3. **行动** → pyautogui 执行 (click/type/press/scroll/wait)
4. 重复直到任务完成或达到最大步数

这是 **Agent 范式**,不是写死的 RPA 脚本。模型自己决定每一步做什么。

### 核心循环 (`run_gui_task()`)

```
for iteration in 1..15:
    ① 检查 ESC 取消信号
    ② 截图全桌面 (pyautogui.screenshot)
    ③ react_decide(task, img, history):
       - 图片 → JPEG base64
       - 拼装 REACT_PROMPT + 历史记录(最近10步)
       - 调用 ChatDoubaoVL.invoke()
       - 解析 JSON: {"done": false, "thought": "...", "action": "click", "x": 500, "y": 300}
    ④ 如果 done → 退出循环
    ⑤ 执行动作 (pyautogui)
    ⑥ 记录历史 → 睡眠1.5秒
    ⑦ 防卡死检测 (连续3次相同动作→退出)
⑧ 最终审计
```

### 为什么是纯视觉？vs DOM 树方案

**DOM 树方案** (已废弃): 注入 JS 扫描 DOM,自己写文本匹配算法找元素。问题:
- 中文匹配不可靠 (百度搜索框没有中文属性)
- 现代前端框架的 Shadow DOM/iframe 穿透困难
- 桌面应用没有 DOM

**纯视觉方案** (当前): 截图发给多模态大模型,模型直接返回坐标。优势:
- 浏览器和桌面应用通用
- 模型自己理解界面布局,不需要匹配算法
- 天然处理视觉变化

### 坐标归一化 (0-1000 空间)

模型返回的坐标在 0-1000 的抽象空间中(不是像素),运行时转换:
```python
x_pixel = x * img_width / 1000
y_pixel = y * img_height / 1000
```

这和 AI_RPA_pyqt.py 的 `(x/1000)*1920` 是同一策略。归一化让模型不需要知道实际分辨率。

### 安全机制

| 机制 | 实现 |
|------|------|
| ESC 热键 | 文件信号→子进程每步前检查→立即退出 |
| 最大15步 | MAX_REACT_ITERATIONS |
| 卡死检测 | 连续3次相同动作自动退出 |
| pyautogui fail-safe | 鼠标移到四角立即停止 |
| 进程级隔离 | 子进程,可被 terminate() 强制杀掉 |

---

## 四、MCP (Model Context Protocol) — 浏览器自动化

### 什么是 MCP？

Anthropic 提出的 **Model Context Protocol** — LLM 与外部工具/服务交互的标准协议。本项目实现了一个 MCP Client,通过 JSON-RPC 2.0 over stdio 与 Playwright MCP Server 通信。

### 架构

```
AIRAG (Python)
  └─ PlaywrightMCPClient
       │ JSON-RPC 2.0 over stdin/stdout
       ↓
  npx @playwright/mcp (Node.js)
       │ CDP (Chrome DevTools Protocol)
       ↓
  Edge/Chrome 浏览器
```

### JSON-RPC 2.0 协议

```
请求:  {"jsonrpc":"2.0","id":1,"method":"tools/call","params":{...}}
响应:  {"jsonrpc":"2.0","id":1,"result":{...}}
通知:  {"jsonrpc":"2.0","method":"notifications/initialized"}  (无id,不等响应)
```

### 握手流程

```
1. initialize  → 服务器返回能力信息
2. initialized → 客户端通知已就绪
3. tools/list  → 获取可用工具列表
```

### 请求-响应模型 (同步化)

虽然是异步管道通信,但通过 `threading.Event` 实现同步等待:
```python
def _send_request(method, params, timeout):
    event = threading.Event()
    self._pending[req_id] = event     # 注册等待者
    self._process.stdin.write(request) # 发送
    event.wait(timeout)                # 阻塞等待
    return self._responses.pop(req_id) # 取结果
```

后台 `_read_loop` 守护线程持续读 stdout,匹配 id 后 `event.set()` 唤醒等待者。

### 双引擎 Fallback

```
BrowserMCP.connect()
  ├─ 优先: PlaywrightMCPClient (MCP Server)
  │   └─ 需要 npx + Node.js
  └─ 回退: Playwright CDP 直连
      └─ 启动 Edge/Chrome --remote-debugging-port=9222
      └─ chromium.connect_over_cdp("http://127.0.0.1:9222")
```

### MCP 工具调用

通过 `call_tool("browser_navigate", {"url": "..."})` 等方式调用。工具名在不同 Server 实现间有差异(`browser_*` vs `playwright_*`),客户端自动做前缀映射。

---

## 五、LLM Client — ChatDoubaoVL

### 设计模式

继承 LangChain 的 `BaseChatModel`,用 OpenAI-compatible SDK 调用火山引擎方舟 API。这不是 OpenAI 的 API,是火山引擎提供的 **OpenAI-compatible 格式**的端点。

### 关键实现

```python
class ChatDoubaoVL(BaseChatModel):
    model_name: str  # 默认 doubao-seed-2-0-mini-260428
    api_key: str     # 从 config.ARK_API_KEY 读取
    base_url: str    # https://ark.cn-beijing.volces.com/api/v3
    temperature: float = 0.7
    _client: OpenAI  # openai.OpenAI 实例

    def _generate(messages):  # 非流式
        response = self._client.chat.completions.create(
            model=..., messages=..., temperature=...
        )
        return ChatResult(...)

    def _stream(messages):    # 流式
        stream = self._client.chat.completions.create(stream=True, ...)
        for event in stream:
            yield ChatGenerationChunk(...)
```

### 多模态消息转换

LangChain 格式 → OpenAI 格式:
- `HumanMessage(content=[{type:"text"},{type:"image_url"}])` → `{role:"user", content:[{type:"text",text:...},{type:"image_url",image_url:{url:...}}]}`
- `AIMessage` → `role:"assistant"`
- `SystemMessage` → `role:"system"`

### 模型选项

三档可在悬浮窗随时切换:
- `doubao-seed-2-0-mini-260428` — 轻量,快速
- `doubao-seed-2-0-lite-260428` — 中等
- `doubao-seed-2-0-pro-260215` — 最强推理

---

## 六、长短期记忆系统

### 短期记忆 (对话上下文)

**结构记忆**: 最近 N 轮完整保留 (`RECENT_ROUNDS=3`,6条消息)

**内容记忆**: 早期对话通过 FAISS 语义检索召回。在 `graph.py` 的 `index_conversation_messages()` 中将消息索引,在 `search_conversation_history()` 中搜索相关历史注入 System Prompt。

### 长期记忆 (用户 Profile)

**文件结构**: `airag_data/users/{user_id_hash}/profile.json`

**提取流程** (每次对话结束后台执行):
```
对话消息 → ChatDoubaoVL(mini)
         → 提取事实
         → {type: identity|preference|project|problem|knowledge, content: "..."}
         → 去重 (Jaccard 字符重叠 >50% 视为重复)
         → 最多保留50条
         → 同步到 FAISS
```

**注入流程** (每轮对话):
```
用户问题 → search_facts(query)
        → FAISS 语义搜索 (优先)
        → 关键词匹配 (回退)
        → 相关事实注入 System Prompt:
          "## 关于当前用户（长期记忆）\n- fact1\n- fact2"
```

**关键词直返** (不调 LLM):
```
用户问题 → 提取关键词 → 匹配事实内容 → 直接返回 → 省 token
```

### FAISS 向量存储 — TF-IDF 向量化

**为什么不用 Embedding 模型？** 避免下载大模型和 GPU 依赖,实现完全本地化。

**方案**: TF-IDF + jieba 中文分词
```python
TfidfVectorizer(
    tokenizer=jieba.lcut,   # 中文分词
    max_features=512,       # 512维向量
    norm='l2'               # L2归一化 → Cosine相似度
)
```

存储: FAISS `IndexFlatIP` (内积=Cosine相似度,L2归一化后等价)

持久化: `.index` 文件 (FAISS binary) + `.json` 文件 (元数据)

---

## 七、Qt UI 架构

### 窗口层级

```
QApplication
├─ ResultWindow (无边框,置顶,常驻悬浮)
│   ├─ 侧边栏 (对话列表)
│   ├─ 内容区 (Markdown 渲染)
│   ├─ 缩略图区 (截图累积)
│   ├─ 输入框 + 模型切换 + 模式切换
│   └─ 发送按钮
├─ CaptureWindow (全屏截图遮罩,按需显示)
├─ GuiAgentDialog (GUI Agent 任务面板)
├─ ApiKeyDialog (设置弹窗)
└─ 系统托盘 (最小化入口)
```

### 线程模型

| 线程 | 用途 |
|------|------|
| Qt 主线程 | UI 渲染,信号/槽 |
| StreamWorker (QThread) | asyncio 事件循环,流式调用 LLM |
| DesktopAgentProcessThread (QThread) | subprocess 管理,实时进度转发 |
| pynput 后台线程 | 全局热键监听 |
| MemWorker (QThread) | 后台记忆提取 |
| SpeechWorker (daemon Thread) | 麦克风录音+腾讯云 ASR |

### 信号/槽 通信

Qt 跨线程安全机制: 所有从工作线程到 UI 的更新都通过 `pyqtSignal` → `QTimer.singleShot(0, ...)` 确保在主线程执行。

### 隐私模式

通过 Windows API `SetWindowDisplayAffinity(hwnd, 0x11)` 让窗口在屏幕捕获/录屏软件中不可见。

---

## 八、数据流全景图

```
用户输入 "自动 打开百度搜索python"
  │
  ├─ main.py: _on_follow_up → _extract_automation_task()
  │   提取到: "打开百度搜索python"
  │
  ├─ _run_desktop_automation()
  │   └─ DesktopAgentProcessThread.start()
  │      └─ subprocess: python run_gui_agent.py "打开百度搜索python"
  │         │
  │         ├─ is_browser_task() → True
  │         ├─ BrowserMCP.connect()
  │         │   ├─ MCP Server 启动 (npx @playwright/mcp)
  │         │   └─ navigtate("https://www.baidu.com")
  │         │
  │         └─ ReAct Loop:
  │            [1] react_decide() → "看到百度首页,点击搜索框" → click(500,250)
  │            [2] react_decide() → "搜索框已聚焦" → type("python")
  │            [3] react_decide() → "按回车搜索" → press("enter")
  │            [4] react_decide() → "搜索结果显示" → click(450,400) 点第一个结果
  │            [5] react_decide() → "已进入python网页,done" → {done:true}
  │
  ├─ audit_result() → 截图→豆包pro→{"success":true}
  │
  └─ 结果回传主进程 → ResultWindow 显示完成
```

---

## 九、面试可能被深挖的点

### Q: ReAct 和传统 RPA 有什么区别？

传统 RPA: 写死步骤 → 按顺序执行。元素定位靠选择器(XPath/CSS)或图像模板匹配。
ReAct Agent: 每步观察→思考→行动,动态决策。用多模态 LLM 理解界面,不需要预定义选择器。

### Q: 为什么坐标用 0-1000 归一化而不是像素？

LLM 不知道截图的分辨率。归一化空间让模型用一个稳定的坐标系。运行时根据实际截图尺寸转换。AI_RPA_pyqt.py 也用了同样的策略。

### Q: MCP 协议为什么要用 JSON-RPC over stdio？

- **JSON-RPC 2.0**: 简单的请求/响应/通知模型,语言无关
- **stdio**: 不需要网络端口,天然进程隔离,随子进程自动清理
- **Event 同步化**: 虽然是异步管道,但用 threading.Event 把每个请求变成同步等待,简化上层调用

### Q: 长期记忆为什么用 TF-IDF 而不是 Embedding？

- 零外部依赖 (不需要下载 Embedding 模型)
- CPU 友好,启动快
- 对中文关键词匹配足够好 (用 jieba 分词)
- 缺点: 语义理解不如神经网络 Embedding

### Q: 如果 MCP Server 挂了怎么办？

`BrowserMCP.connect()` 自动回退到 CDP 直连(启动带 `--remote-debugging-port` 的浏览器,用 Playwright 的 `connect_over_cdp()` 连接)。

### Q: 怎么防止 GUI Agent 无限循环？

三层防护:
1. MAX_REACT_ITERATIONS = 15
2. 连续3次相同动作自动退出
3. ESC 热键随时终止子进程

### Q: 上下文不会越来越长吗？

不会。`stream_graph()` 使用混合策略:
- 最近3轮完整保留
- 早期对话只保留通过 FAISS 语义检索到的 top-3 条
- `trim_history_node` 硬截断为 max_turns×2 条

---

## 十、关键代码位置速查

| 功能 | 文件 | 函数/类 |
|------|------|---------|
| ReAct 循环 | agent/gui_agent.py | `run_gui_task()` |
| ReAct 决策 | agent/gui_agent.py | `react_decide()` |
| 浏览器 MCP | agent/gui_agent.py | `BrowserMCP`, `PlaywrightMCPClient` |
| LangGraph 图 | agent/graph.py | `build_graph()` |
| 流式对话 | agent/graph.py | `stream_graph()` |
| 关键词直返 | agent/graph.py | `_direct_keyword_reply()` |
| LLM 客户端 | agent/llm_client.py | `ChatDoubaoVL` |
| 长期记忆提取 | utils/memory_store.py | `extract_facts_from_conversation()` |
| FAISS 存储 | utils/vector_store.py | `VectorStore` |
| Qt 主控制器 | main.py | `ScreenAIAgent` |
| 截图遮罩 | gui/capture_window.py | `CaptureWindow` |
| 悬浮窗 | gui/result_window.py | `ResultWindow` |
