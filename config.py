"""
AIRAG 全局配置文件
"""
import os

from utils.api_key_manager import get_api_key, get_model, get_proxy

# ============================================================
# 快捷键
# ============================================================
DEFAULT_HOTKEY = "ctrl+d"
TOGGLE_HOTKEY = "ctrl+f"
OCR_HOTKEY = "ctrl+r"
QUIT_HOTKEY = "ctrl+q"

# ============================================================
# 网络代理（访问火山引擎 API 需要）
# ============================================================
HTTP_PROXY = get_proxy()   # 从 airag_config.json 读取，或通过设置界面配置

# ============================================================
# 豆包 VL API 配置 (火山引擎方舟)
# ============================================================
# 优先读取本地配置文件，其次环境变量，最后用占位符
ARK_API_KEY = get_api_key() or os.environ.get("ARK_API_KEY", "")
ARK_BASE_URL = "https://ark.cn-beijing.volces.com/api/v3"
DOUBAO_MODEL_NAME = get_model() or os.environ.get("DOUBAO_MODEL_NAME", "doubao-seed-2-0-mini-260428")

# GUI 自动化子进程解释器。主界面需要 PyQt/pynput，自动化进程优先使用 browser-use 环境。
_BROWSER_USE_PYTHON = r"E:\Anaconda3\envs\browser-use\python.exe"
GUI_AGENT_PYTHON = (
    os.environ.get("AIRAG_GUI_AGENT_PYTHON")
    or (_BROWSER_USE_PYTHON if os.path.exists(_BROWSER_USE_PYTHON) else "")
)

# ============================================================
# Playwright MCP Server 配置
# ============================================================
# 官方版优先，旧社区版作为兜底
# 通过 npx 启动，JSON-RPC 2.0 over stdio 通信
MCP_SERVER_PACKAGE = os.environ.get("AIRAG_MCP_SERVER_PACKAGE", "@playwright/mcp")
MCP_SERVER_FALLBACK_PACKAGE = "@executeautomation/playwright-mcp-server"
MCP_SERVER_PACKAGES = [
    MCP_SERVER_PACKAGE,
]
if MCP_SERVER_FALLBACK_PACKAGE not in MCP_SERVER_PACKAGES:
    MCP_SERVER_PACKAGES.append(MCP_SERVER_FALLBACK_PACKAGE)
MCP_BROWSER_HEADLESS = False     # 必须为 False（用户需要看到浏览器）
MCP_INITIALIZE_TIMEOUT = 60      # 首次启动可能需下载 npm 包，预留 60s
MCP_DEFAULT_TIMEOUT = 30         # 常规工具调用超时
MCP_SCREENSHOT_WIDTH = 1920      # 截图宽度
MCP_SCREENSHOT_HEIGHT = 1080     # 截图高度

# 可选模型列表
MODEL_OPTIONS = {
    "mini (轻量)":  "doubao-seed-2-0-mini-260428",
    "lite (中级)":  "doubao-seed-2-0-lite-260428",
    "pro (高级)":   "doubao-seed-2-0-pro-260215",
}
MODEL_OPTIONS_DEFAULT = "mini (轻量)"  # 默认选中项

# ============================================================
# 上下文限制
# ============================================================
MAX_TURNS = 5                 # 最多保留最近 5 轮对话
MAX_MESSAGES = MAX_TURNS * 2  # 最多保留 10 条消息
RECENT_ROUNDS = 3             # 最近 N 轮完整保留

# ============================================================
# 图片处理配置
# ============================================================
MAX_IMAGE_WIDTH = 1920        # 压缩最大宽度
MAX_IMAGE_HEIGHT = 1080       # 压缩最大高度
JPEG_QUALITY = 85             # JPEG 压缩质量 (1-100)
MAX_IMAGE_BASE64_KEEP_TURNS = 0  # JSON 中不保留图片 base64（只存文本）

# ============================================================
# 上下文持久化
# ============================================================
CONTEXT_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "context_history.json")

# ============================================================
# UI 配置
# ============================================================
# 截图遮罩
MASK_OPACITY = 0.3            # 遮罩层透明度
HANDLE_SIZE = 8               # 锚点大小(像素)
HANDLE_HIT_RADIUS = 10        # 锚点点击检测半径

# 结果窗口
RESULT_WINDOW_WIDTH = 500     # 默认宽度
RESULT_WINDOW_HEIGHT = 400    # 默认高度
RESULT_WINDOW_OPACITY = 0.92  # 窗口不透明度
RESULT_FONT_SIZE = 13         # 字体大小
RESULT_BG_COLOR = "rgba(30, 30, 30, 0.92)"  # 深色半透明背景
RESULT_TEXT_COLOR = "#e0e0e0"  # 文字颜色

# 输入窗口
INPUT_WINDOW_WIDTH = 420
INPUT_WINDOW_HEIGHT = 160
