# AIRAG 架构说明

本文档按“程序真实执行路径”解释 AIRAG 当前阶段的架构。重点覆盖主程序、主线程、全局快捷键、`Ctrl+D` 截图问答、AI 流式调用、对话记忆、图片上下文、可选 Redis 短期记忆、远程手机控制和桌面自动化子进程。

## 1. 项目定位

AIRAG 是一个 Windows 桌面 AI 助手。它常驻系统托盘，通过全局快捷键触发截图、OCR、语音输入、AI 问答和桌面自动化。

核心交互不是传统聊天网页，而是“截图/文本/语音/远程手机指令 -> 桌面悬浮窗 -> 多模态模型/自动化执行”的桌面工作流。

主要技术栈：

| 模块 | 技术 |
|---|---|
| 桌面 UI | PyQt6 |
| 全局快捷键 | pynput |
| 多模态模型 | 豆包 VL，OpenAI-compatible Chat Completions |
| Agent 状态机 | LangGraph |
| 流式输出 | QThread + asyncio + model stream |
| 截图处理 | QImage -> PIL -> JPEG -> Base64 |
| 对话记忆 | 进程内 `self._messages` + JSON + 可选 Redis |
| 中期检索 | BM25 |
| 长期记忆 | `profile.json` + facts 向量索引 |
| 桌面自动化 | 子进程 + ReAct 核心决策 + Loop Engineer 外层控制 + PyAutoGUI/Playwright MCP |
| 手机远程控制 | aiohttp server + QR code |

## 2. 主程序启动

入口在 `main.py` 的 `main()`。启动后创建 `QApplication`，再创建核心控制器 `ScreenAIAgent`，最后进入 Qt 主事件循环。

```text
main.py
  main()
    -> QApplication(sys.argv)
    -> app.setQuitOnLastWindowClosed(False)
    -> ScreenAIAgent(app)
    -> app.exec()
```

`app.exec()` 之后，主线程进入 Qt EventLoop。所有 UI 操作，例如显示悬浮窗、截图遮罩、更新文字、弹菜单，都必须回到这个 Qt 主线程执行。

## 3. 主控制器 ScreenAIAgent

`ScreenAIAgent` 是整个应用的主控制器，位于 `main.py`。它不是单纯 UI 类，而是把用户系统、会话、记忆、快捷键、截图、AI 流式调用、远程控制和自动化子进程串起来。

初始化阶段大致做这些事：

1. 生成当前用户 ID。
2. 加载当前活跃会话或最近会话。
3. 可选从 Redis 恢复最近短期记忆窗口。
4. 加载长期记忆 profile。
5. 构建 LangGraph。
6. 创建 ResultWindow 悬浮对话框。
7. 创建系统托盘菜单。
8. 注册全局快捷键。
9. 启动手机远程控制服务。
10. 启动后台截图推送线程，供手机端实时预览桌面。

核心状态：

| 字段 | 含义 |
|---|---|
| `self._messages` | 当前会话的 LangChain 消息列表，是主短期记忆 |
| `self._user_id` | 当前用户 ID，由 API key 派生 |
| `self._active_conv_id` | 当前会话 ID |
| `self._profile` | 长期记忆 profile |
| `self._graph` | LangGraph 状态机 |
| `self._result_window` | 悬浮结果/输入窗口 |
| `self._capture_win` | 全屏截图遮罩窗口 |
| `self._stream_worker` | AI 流式调用线程 |
| `self._agent_thread` | 桌面自动化子进程管理线程 |
| `self._remote_server` | 手机远程控制服务 |

## 4. 线程和进程全景

AIRAG 启动后不是只有一个线程。主线程负责 UI，耗时工作放到 QThread、后台线程或子进程里。

```text
AIRAG 主进程
  |
  |-- Qt 主线程
  |     |-- QApplication event loop
  |     |-- ResultWindow 悬浮窗
  |     |-- CaptureWindow 截图遮罩
  |     |-- 托盘菜单
  |     |-- QTimer 回调
  |
  |-- pynput 快捷键监听线程
  |     |-- 检测 Ctrl+D / Ctrl+R / Ctrl+F / Ctrl+Q / ESC
  |     |-- 通过 QTimer.singleShot 切回 Qt 主线程
  |
  |-- StreamWorker QThread
  |     |-- 创建 asyncio event loop
  |     |-- 调用 stream_graph()
  |     |-- 接收模型 token
  |     |-- 通过 Qt signal 发回主线程更新 UI
  |
  |-- RemoteServer 后台线程
  |     |-- aiohttp HTTP 服务
  |     |-- 手机端发送指令、获取状态、取消任务
  |
  |-- Screen pusher daemon thread
  |     |-- 定时 pyautogui.screenshot()
  |     |-- 推送给手机远程页面预览
  |
  |-- DesktopAgentProcessThread QThread
  |     |-- subprocess.Popen(run_gui_agent.py)
  |     |-- 桌面自动化 ReAct + Loop Engineer 子进程
  |
  |-- MemWorker QThread
        |-- 会话结束后抽取长期记忆 facts
        |-- merge_facts -> save_profile
```

这样设计的原因：

- PyQt UI 只能在主线程安全更新。
- 大模型流式请求会阻塞，必须放到 `StreamWorker`。
- pynput 的全局快捷键回调不在 Qt 主线程，所以需要 `QTimer.singleShot(0, ...)` 切回主线程。
- 桌面自动化风险更高、耗时更长，所以放到独立子进程，方便取消和隔离。

## 5. Ctrl+D 截图问答完整链路

`Ctrl+D` 是最核心路径：用户截图，输入问题，模型结合图片和对话记忆回答。

### 5.1 快捷键触发

用户按下 `Ctrl+D` 后，流程是：

```text
pynput GlobalHotKeys 后台线程
  -> 捕获 Ctrl+D
  -> 调用快捷键回调
  -> QTimer.singleShot(0, self._start_capture_flow)
  -> 回到 Qt 主线程执行截图 UI
```

这里不能直接在 pynput 线程里创建 PyQt 窗口，否则容易出现线程安全问题。项目使用 `QTimer.singleShot(0, callback)` 把任务投递回 Qt 主线程。

### 5.2 打开截图遮罩

`_start_capture_flow()` 在主线程执行：

```text
_start_capture_flow()
  -> 如果已有截图窗口，直接返回，避免重复打开
  -> 隐藏 ResultWindow，避免截到自己的悬浮窗
  -> QApplication.processEvents()
  -> 创建 CaptureWindow
  -> captured 信号连接 _on_image_captured
  -> destroyed 信号连接恢复 ResultWindow
  -> showFullScreen()
```

`CaptureWindow` 是全屏透明遮罩。它先截取当前屏幕作为背景，然后绘制半透明遮罩、用户拖拽矩形、边框、锚点和底部提示条。

用户可以：

- 鼠标拖拽选择截图区域。
- 多选多个区域。
- 调整区域大小。
- `Ctrl+Z` 撤销最后一个区域。
- `Enter` 确认截图。
- `Esc` 取消。

### 5.3 截图转图片输入

用户按 `Enter` 后，`CaptureWindow` 发出 `captured(images)` 信号。`images` 是 `(QImage, QRect)` 列表。

`_on_image_captured()` 做这些事：

```text
_on_image_captured(images)
  -> 保存第一张截图区域，用于定位悬浮窗
  -> 每张 QImage 转 PIL
  -> compress_image() 压缩到配置宽高以内
  -> pil_to_base64() 转 JPEG base64
  -> add_image_thumbnail() 放入 ResultWindow 待发送图片区
  -> 显示悬浮窗
  -> 聚焦输入框
```

图片在进入模型前是这样的：

```text
QImage
  -> PIL Image
  -> JPEG 压缩
  -> base64 字符串
  -> build_multimodal_message()
  -> HumanMessage(content=[text block, image_url block, ...])
```

## 6. ResultWindow 对话框

`ResultWindow` 是用户看到的悬浮对话框。它同时承担：

- 显示模型回答。
- 展示用户截图缩略图。
- 输入追问文本。
- 切换问答/操作模式。
- 显示侧边栏历史会话。
- 触发模型切换、设置、远程连接等 UI 操作。

当用户输入文本并发送时，`ResultWindow` 发出 `follow_up_requested(text)` 信号，主控制器的 `_on_follow_up()` 接收。

`_on_follow_up()` 的路由逻辑：

```text
_on_follow_up(text)
  -> 收集 ResultWindow 中待发送图片
  -> 如果是语音选中文本，包装成语音转写总结问题
  -> 如果无文本且无图片，直接返回
  -> 如果当前是 operate 模式且没有图片，走桌面自动化
  -> 否则走 AI 多模态流式问答
```

这里有一个重要分支：

- 有图片：默认走多模态问答。
- 无图片且处于操作模式：可以路由到桌面自动化。
- 普通文字追问：走聊天问答。

## 7. AI 流式回答

AI 回答由 `_run_ai_stream()` 启动。

```text
_run_ai_stream(user_text, image_base64_list)
  -> 根据截图位置移动 ResultWindow
  -> 在窗口里追加用户问题标题
  -> 显示 loading 状态
  -> 创建 StreamWorker
  -> 连接 token_received / stream_finished / stream_error
  -> start()
```

`StreamWorker` 是 `QThread`。它在线程内部创建 asyncio 事件循环，然后调用 `agent.graph.stream_graph()`。

```text
StreamWorker.run()
  -> asyncio.new_event_loop()
  -> stream_graph(
       graph=self._graph,
       messages=self._messages,
       user_text=self._user_text,
       image_base64_list=self._image_base64_list,
       user_id=self._user_id
     )
  -> 每收到一个 token
  -> emit token_received(token)
```

主线程收到 `token_received` 后调用 `_on_token_received()`，把 token 追加到 `ResultWindow`。这样模型可以逐字流式显示，同时 UI 不会卡死。

流式结束后 `_on_stream_finished()`：

```text
_on_stream_finished()
  -> stop_loading()
  -> 读取 ResultWindow 当前完整内容
  -> 把本轮用户消息构造成 HumanMessage
  -> 如果有图片，使用 build_multimodal_message()
  -> 把 AI 完整回答保存为 AIMessage
  -> append 到 self._messages
  -> _save_current_conv()
  -> 清空本轮图片缓存
```

## 8. LangGraph 和三层记忆检索

当前 `agent/graph.py` 的核心不是复杂多节点 Agent，而是“流式模型调用 + 三层记忆检索”。`build_graph()` 仍保留 LangGraph 状态机接口，用于兼容非流式路径和后续扩展。

### 8.1 三层记忆模型

当前设计分为：

| 层级 | 数据来源 | 作用 | 检索方式 |
|---|---|---|---|
| 短期记忆 | `self._messages` 最近 `RECENT_ROUNDS` 轮 | 保留当前上下文、最近图片/追问 | 直接截取最近消息 |
| 中期记忆 | 更早的历史对话文本 | 召回本会话早期相关内容 | BM25 召回 + embedding 向量召回 + 关键词召回 -> RRF -> lightweight rerank |
| 长期记忆 | `profile.json` 用户 facts | 跨会话用户偏好、项目事实 | 向量检索 + 关键词 fallback |

核心函数是：

```text
retrieve_memory_context(messages, query, user_id)
  -> recent_count = RECENT_ROUNDS * 2
  -> short_term = messages[-recent_count:]
  -> older = messages[:-recent_count]
  -> older 进入中期向量索引
  -> search_conversation_history(query)
  -> build_memory_context(user_id, query)
  -> 返回 RetrievedMemory
```

### 8.2 RRF 和 rerank

检索链路当前分两级：

`	ext
候选召回
  -> BM25 召回
  -> 关键词召回
  -> RRF 融合多路候选
  -> lightweight rerank 精排
  -> top_k 注入 prompt
`

长期记忆 facts 使用两路召回：memory_facts BM25 检索、embedding 向量检索和关键词匹配。两路结果先用 RRF 合并，避免单一路径漏召回；之后用轻量 rerank 结合原始召回分和 query 关键词覆盖率做最终排序。

中期对话历史现在和长期记忆使用同一套形状：conversations BM25 召回 + embedding 向量召回 + 当前 older messages 关键词召回，之后 RRF 融合，再用 lightweight rerank 精排。后续如果接 embedding，可以直接把 embedding 结果作为第三路候选加入 RRF。

当前 embedding 是可选分支：配置 AIRAG_EMBEDDING_MODEL_NAME 后启用 dense retrieval；未配置或调用失败时自动降级到 BM25 + 关键词召回。这里没有引入 cross-encoder reranker，是因为 AIRAG 当前是桌面端应用，优先考虑离线可用、低延迟和低部署复杂度。需要更强精排能力时，可以加入 bge-reranker、Cohere Rerank 或模型打分。

### 8.3 Prompt 组装

`stream_graph()` 中的模型输入大致是：

```text
SystemMessage(
  BASE_SYSTEM_PROMPT
  + 长期记忆：当前用户相关事实
  + 中期记忆：历史相关对话
)
+ 最近几轮 short_term_messages
+ 本轮 input_msg，可能包含图片
```

这样做的好处：

- 最近上下文作为真实 chat message 保留，模型能理解连续对话。
- 早期历史不会全部塞进上下文，只召回相关片段。
- 长期记忆不再直接回答，而是作为辅助上下文交给模型判断。
- 如果记忆和本轮输入冲突，系统提示要求以本轮输入为准。

### 8.4 图片和记忆的关系

图片上下文主要属于短期记忆。

原因：

- 图片 base64 很大，不适合全部写入长期 JSON 或向量库。
- 向量检索模块主要面向文本，不直接检索原始图片。
- 当前模型需要看图时，应该把本轮图片直接作为多模态输入传给模型。

当前策略：

```text
本轮图片
  -> image_base64_list
  -> build_multimodal_message()
  -> 直接发给模型

历史图片
  -> JSON 默认不保留 base64
  -> Redis 可选保留最近 N 轮图片
  -> 更长期建议保存 OCR/视觉摘要或图片文件引用，而不是保存原始 base64
```

## 9. 对话持久化

AIRAG 当前有三类会话存储：

### 9.1 进程内短期记忆

`self._messages` 是运行时最重要的短期记忆。每轮结束后会追加：

```text
HumanMessage(content=用户文本或多模态内容)
AIMessage(content=完整回答)
```

它用于下一轮模型调用，也用于保存到历史会话。

### 9.2 JSON 会话历史

`_save_current_conv()` 会调用：

```text
save_conversation(user_id, conv)
save_context(self._messages, CONTEXT_FILE)
```

JSON 存储适合：

- 会话列表展示。
- 程序重启恢复。
- 长期留存文本对话。

但 JSON 不适合保存大量图片 base64，所以 `MAX_IMAGE_BASE64_KEEP_TURNS = 0` 时默认不保留图片 base64。

### 9.3 可选 Redis 短期记忆

项目现在提供了 `utils/session_memory.py` 作为可选 Redis 短期记忆层。

Redis 配置在 `config.py`：

```python
REDIS_URL = os.environ.get("AIRAG_REDIS_URL", "redis://localhost:6379/0")
REDIS_SHORT_TERM_ENABLED = os.environ.get("AIRAG_REDIS_ENABLED", "1") != "0"
REDIS_SHORT_TERM_TTL_SECONDS = 7 * 24 * 3600
REDIS_SHORT_TERM_MAX_MESSAGES = 30
REDIS_KEEP_IMAGE_TURNS = 2
```

Redis 的定位是：

- 存最近短期窗口。
- 支持 TTL 自动过期。
- 支持程序重启后恢复最近上下文。
- 可选保存最近几轮图片 base64。

Redis 不是必须依赖。`redis_available()` 会检测 Redis 是否可用；如果 Redis 包没装、服务没启动或连接失败，程序自动回退到原来的 `self._messages` + JSON，不影响主流程。

面试解释可以这样说：

> 当前项目是单机桌面应用，主短期记忆用进程内 `self._messages` 就能满足实时对话；Redis 是可选增强层，用于跨进程、重启恢复和 TTL 管理。由于图片 base64 体积大，不适合大规模长期写入 Redis，所以 Redis 只保存最近短期窗口。长期来看，如果做多用户 Web 服务或跨端同步，再把 Redis 作为 session state 层会更合适。

## 10. 长期记忆系统

长期记忆在 `utils/memory_store.py`。

一轮或多轮对话结束后，后台 `MemWorker` 会调用小模型抽取 facts：

```text
extract_facts_from_conversation()
  -> 读取最近对话文本
  -> 调用 mini 模型抽取用户事实
  -> 输出 identity / preference / project / problem / knowledge 等类型
  -> merge_facts()
  -> save_profile()
  -> sync_facts_to_vector()
```

长期记忆文件：

```text
airag_data/users/{user_id}/profile.json
```

结构大致是：

```json
{
  "user_id": "...",
  "facts": [
    {
      "id": "f...",
      "type": "project",
      "content": "用户正在开发 AIRAG 桌面 AI 助手",
      "created": "..."
    }
  ],
  "summary": "",
  "stats": {}
}
```

检索时：

```text
build_memory_context(user_id, query)
  -> load_profile()
  -> search_facts()
     -> memory_facts BM25 召回
     -> embedding 向量召回
     -> 关键词召回
     -> RRF 融合
     -> lightweight rerank 精排
  -> 格式化为“长期记忆：当前用户相关事实”
```

## 11. 向量检索系统

`utils/vector_store.py` 封装了轻量向量库。

当前不是云端 embedding，而是使用本地 BM25 文本检索，并提供可选 dense embedding 检索分支。BM25 负责关键词和稀疏检索，适合快捷键、配置项、报错码、函数名等精确词召回；embedding 负责语义相似召回。没有配置 embedding 模型时，系统自动降级为 BM25 + 关键词召回。

两个主要 collection：

| Collection | 来源 | 用途 |
|---|---|---|
| `conversations` | 历史对话文本 | 中期记忆检索 |
| `memory_facts` | 长期 facts | 长期记忆检索 |

中期记忆索引：

```text
older messages
  -> 提取文本
  -> 加上角色前缀“用户/AI”
  -> 写入 conversations 向量库
  -> 查询时 search(query, top_k=4)
```

长期记忆索引：

```text
profile facts
  -> sync_facts_to_vector()
  -> 写入 memory_facts 向量库
  -> 查询时 search_facts()
```

`VectorStore.add()` 已经按 id 跳过重复写入，避免每轮重复索引同一批旧消息。

## 12. OCR 流程

`Ctrl+R` 触发 OCR。

```text
Ctrl+R
  -> pynput 后台线程
  -> QTimer.singleShot(0, _start_ocr_flow)
  -> CaptureWindow 截图
  -> _on_ocr_captured(images)
  -> QImage 转 PIL
  -> ocr_recognize_batch()
  -> ResultWindow 显示可复制文本
```

OCR 路径和 AI 截图问答共用 `CaptureWindow`，区别是截图完成后不调用模型，而是调用 OCR 工具。

## 13. 桌面自动化流程

当用户处于操作模式，或者输入带有自动化前缀的文本时，主程序不走普通 AI 问答，而是进入桌面自动化。

主流程：

```text
_on_follow_up(text)
  -> _extract_automation_task(text)
  -> _run_desktop_automation(task)
  -> DesktopAgentProcessThread
  -> subprocess.Popen(run_gui_agent.py)
  -> 子进程执行 ReAct Core + Loop Engineer
```

为什么用子进程：

- 桌面自动化可能卡住或失败。
- 子进程更容易取消。
- 自动化依赖 Playwright/PyAutoGUI/browser-use 等环境，和主 UI 隔离更稳。

### 13.1 ReAct Core + Loop Engineer

桌面自动化不是纯 ReAct，也不是纯状态机，而是：

```text
ReAct Core 作为核心决策
+ Loop Engineer 作为外层控制框架
```

两层职责分开：

| 层 | 负责什么 | 代码位置 |
|---|---|---|
| ReAct Core | 看截图、读任务/历史/操作记忆，决定下一步 action | `react_decide()` |
| Loop Engineer | 截图观察、动作执行、重复检测、失败恢复、停止条件、审计、操作记忆更新 | `LoopController` + `run_gui_task()` |
| Executor | 真正执行鼠标、键盘、滚轮、PPT 翻页、浏览器 MCP 等工具 | `_pyautogui_*`、`BrowserMCP` |
| Auditor | 最终验收当前屏幕是否满足任务 | `audit_result()` |
| Operation Memory | 每轮结束后压缩操作流程，下轮继续读入 | `utils/gui_operation_memory.py` |

实际闭环是：

```text
Loop Engineer
  -> 加载当前操作窗口记忆
  -> 截图观察当前桌面/浏览器
  -> ReAct Core 决策 action
  -> Executor 执行动作
  -> Loop Engineer 判断画面是否变化、动作是否重复
  -> 若失败/重复，切换 fallback 方法，最多 5 种
  -> 若完成，进入 Auditor 审计
  -> 审计后压缩本轮流程，更新操作记忆和向量索引
```

这样设计的原因：

- ReAct 灵活，适合桌面、网页、PPT、文件夹这类状态不可预设的环境。
- 纯 ReAct 容易重复点击、重复滚动、卡在遮挡窗口或广告页面。
- Loop Engineer 把工程约束放到外层：连续失败换方法、最多尝试次数、取消/失败自动停、审计后写记忆。
- 操作记忆让手机端或下一轮追问可以继续当前窗口状态，例如“继续翻到第十页”。

### 13.2 工具动作集

当前桌面自动化动作包括：

- 鼠标：`click`、`double`、`right`、`move`、`drag`
- 输入：`type`、`fill`、`press`、`hotkey`
- 页面/窗口：`scroll`、`multi_scroll`、`page_jump`、`page_down`、`page_up`、`alt_tab`、`focus_window`、`maximize`
- 浏览器：`observe_browser`、`activate_browser`、`open_url`、`read_page`
- 视野调整：`zoom_in`、`zoom_out`、`screenshot`、`wait`

PPT 翻页不再要求一页一页走。ReAct 可以根据截图和历史估算目标页差距，例如当前第 1 页、目标第 10 页，直接输出：

```json
{"action": "page_jump", "text": "9"}
```

Loop Engineer 会执行连续 `PageDown`，然后重新截图，让 ReAct 判断是否到达目标页。

完成后，主进程把自动化结果和日志写回对话历史，也会进入 `_save_current_conv()`。

## 14. 手机远程控制

`remote/server.py` 提供手机远程控制服务。主程序启动后会创建 `RemoteServer`，并生成局域网访问地址/二维码。

手机端可以：

- 查看桌面截图预览。
- 输入指令。
- 接收任务结果。
- 取消任务。

主进程里还有一个 daemon 截图推送线程，定期获取桌面截图并交给远程服务：

```text
screen_pusher_loop
  -> pyautogui.screenshot()
  -> remote_server.set_screenshot(img)
  -> sleep(1.5s)
```

远程指令最终还是回到主控制器，由主线程通过 QTimer 定期检查并派发。

## 15. 当前阶段架构总结

AIRAG 当前是一个以 PyQt 主线程为中心的桌面 Agent：

```text
快捷键 / 手机 / 悬浮窗输入
  -> ScreenAIAgent 主控制器
  -> 截图或文本收集
  -> 路由：
       1. 多模态问答 -> StreamWorker -> stream_graph -> 豆包 VL
       2. OCR -> ocr_recognize_batch
       3. 桌面自动化 -> 子进程 ReAct Core + Loop Engineer
  -> ResultWindow 展示结果
  -> self._messages 保存短期上下文
  -> JSON/Redis 保存会话
  -> 后台抽取长期记忆
  -> BM25 支撑中长期检索
```

记忆系统当前的核心设计是：

```text
短期记忆：最近几轮真实消息，包含本轮多模态图片输入
中期记忆：更早历史文本，走 BM25 + embedding + 关键词召回 + RRF + rerank
长期记忆：用户 facts/profile，走 BM25 + embedding + 关键词召回 + RRF + rerank
Redis：可选短期窗口增强，不作为强依赖
```

这个设计的取舍是：

- 保持桌面应用部署简单。
- 图片直接走多模态输入，避免向量库/长期存储承载大 base64。
- 文本历史通过召回、RRF 融合和 rerank 压缩上下文，避免 prompt 过长。
- 长期用户事实独立存储，便于跨会话个性化。
- Redis 作为可选层，为后续多进程、多端同步预留空间。
