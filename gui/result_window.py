"""
透明悬浮结果窗 — 流式显示 AI 回复，支持 Markdown、拖动、缩放。

特性：
  - 无边框、置顶、半透明背景
  - 鼠标按住标题栏拖动窗口
  - 鼠标拖拽边缘/角落缩放窗口
  - 呼吸灯加载动画（等待 AI 回复时）
  - 右键菜单：清空/复制/关闭
"""
import re
from PyQt6.QtCore import Qt, QPoint, QSize, QRect, QTimer, pyqtSignal
from gui.sidebar_widget import SidebarWidget
from PyQt6.QtGui import (
    QFont, QColor, QPalette, QAction, QImage,
    QTextCursor, QMouseEvent, QCursor, QPixmap,
)
from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QTextEdit, QHBoxLayout,
    QPushButton, QLabel, QMenu, QLineEdit, QComboBox,
)

from config import (
    RESULT_WINDOW_WIDTH, RESULT_WINDOW_HEIGHT,
    RESULT_WINDOW_OPACITY, RESULT_FONT_SIZE,
)

# 边缘检测距离（像素）
EDGE_MARGIN = 8
# 最小窗口尺寸
MIN_WIDTH = 300
MIN_HEIGHT = 200


class ResultWindow(QWidget):
    """
    透明、可拖动、可缩放、置顶的悬浮结果窗。
    底部带有追问输入框 + 待发送图片缩略图，支持连续纯文本追问。
    """
    # 信号：用户在追问框输入文字并发送 / 切换模型 / 打开设置
    follow_up_requested = pyqtSignal(str)
    model_changed = pyqtSignal(str)
    settings_requested = pyqtSignal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self._buffer = ""
        self._pending_images: list = []    # 待发送图片 (base64 列表)
        self._pending_qimgs: list = []    # 待发送图片 (QImage 列表，供复制用)
        self._thumb_widgets: list = []    # 缩略图 widget 列表

        # ---- 拖动/缩放状态 ----
        self._drag_pos = QPoint()          # 拖动起始偏移
        self._dragging = False             # 是否在拖动窗口
        self._resizing = False             # 是否在缩放窗口
        self._resize_edge = ""             # 缩放边缘方向: n, s, e, w, ne, nw, se, sw
        self._resize_start_geo: QRect | None = None  # 缩放前窗口几何
        self._resize_start_pos: QPoint | None = None  # 缩放起始鼠标全局坐标

        # ---- 加载动画 ----
        self._loading = False
        self._loading_dots = 0
        self._loading_timer = QTimer(self)
        self._loading_timer.timeout.connect(self._tick_loading)
        self._loading_base_text = ""

        self._setup_ui()

    # ============================================================
    # Sidebar wrapper
    # ============================================================

    def _on_sidebar_conv_selected(self, conv_id: str):
        """侧边栏切换对话 → 转发给外部监听者。"""
        if hasattr(self, '_conv_listener') and self._conv_listener:
            self._conv_listener(conv_id)

    def _on_sidebar_new_conv(self):
        if hasattr(self, '_conv_listener_new') and self._conv_listener_new:
            self._conv_listener_new()

    def _on_sidebar_refresh(self):
        """仅刷新侧边栏列表，不切换对话。"""
        if hasattr(self, '_conv_refresh_listener') and self._conv_refresh_listener:
            self._conv_refresh_listener()

    def set_sidebar_listener(self, on_select, on_new, on_refresh=None, user_id=""):
        self._conv_listener = on_select
        self._conv_listener_new = on_new
        self._conv_refresh_listener = on_refresh
        self._sidebar_widget.set_user_id(user_id)

    def _toggle_sidebar(self):
        self._sidebar_visible = not self._sidebar_visible
        if self._sidebar_visible:
            self._sidebar_widget.show()
            self._sidebar_sep.show()
            self._sidebar_toggle_btn.setText("✕")
        else:
            self._sidebar_widget.hide()
            self._sidebar_sep.hide()
            self._sidebar_toggle_btn.setText("☰")

    def refresh_sidebar(self, convs: list, active_id: str):
        self._sidebar_widget.set_conversations(convs, active_id)

    # ============================================================
    # UI Setup
    # ============================================================

    def _setup_ui(self):
        # Tool 标志：不显示在任务栏，只显示在托盘
        self.setWindowFlags(
            Qt.WindowType.FramelessWindowHint |
            Qt.WindowType.WindowStaysOnTopHint |
            Qt.WindowType.Tool |
            Qt.WindowType.NoDropShadowWindowHint
        )
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, False)
        self.setWindowOpacity(RESULT_WINDOW_OPACITY)
        self._win_hidden_done = False
        self.setMinimumSize(MIN_WIDTH, MIN_HEIGHT)
        self.resize(RESULT_WINDOW_WIDTH + 220, RESULT_WINDOW_HEIGHT)  # 给侧边栏留空间
        self._position_bottom_right()

        # 深色半透明背景
        palette = self.palette()
        palette.setColor(QPalette.ColorRole.Window, QColor(30, 30, 35, 235))
        self.setPalette(palette)
        self.setAutoFillBackground(True)

        # 启用鼠标追踪（边缘检测需要）
        self.setMouseTracking(True)

        # 主布局（水平：侧边栏 + 右侧内容）
        self._main_h_layout = QHBoxLayout(self)
        self._main_h_layout.setContentsMargins(0, 0, 0, 0)
        self._main_h_layout.setSpacing(0)

        # 侧边栏（内嵌，默认隐藏）
        self._sidebar_widget = SidebarWidget()
        self._sidebar_widget.conversation_selected.connect(self._on_sidebar_conv_selected)
        self._sidebar_widget.new_conversation_clicked.connect(self._on_sidebar_new_conv)
        self._sidebar_widget.refresh_requested.connect(self._on_sidebar_refresh)
        self._sidebar_widget.setFixedWidth(180)
        self._sidebar_visible = False
        self._sidebar_widget.hide()
        self._main_h_layout.addWidget(self._sidebar_widget)

        # 分隔线（跟随侧边栏显隐）
        self._sidebar_sep = QWidget()
        self._sidebar_sep.setFixedWidth(1)
        self._sidebar_sep.setStyleSheet("background: #3a3a45;")
        self._sidebar_sep.hide()
        self._main_h_layout.addWidget(self._sidebar_sep)

        # 右侧内容区
        right_widget = QWidget()
        layout = QVBoxLayout(right_widget)
        layout.setContentsMargins(8, 6, 8, 6)
        layout.setSpacing(4)

        # ---- 标题栏 ----
        title_bar = QHBoxLayout()
        title_bar.setSpacing(6)

        # 侧边栏切换按钮
        self._sidebar_toggle_btn = QPushButton("☰")
        self._sidebar_toggle_btn.setFixedSize(26, 26)
        self._sidebar_toggle_btn.setToolTip("显示/隐藏对话历史")
        self._sidebar_toggle_btn.setStyleSheet("""
            QPushButton { background: #3a3a40; color: #ccc; border: none;
                border-radius: 4px; font-size: 14px; font-weight: bold; }
            QPushButton:hover { background: #555; color: white; }
        """)
        self._sidebar_toggle_btn.clicked.connect(self._toggle_sidebar)
        title_bar.addWidget(self._sidebar_toggle_btn)

        self._title_label = QLabel("Ai_Flow")
        self._title_label.setFont(QFont("Microsoft YaHei", 11))
        self._title_label.setStyleSheet("color: #88aaff; font-weight: bold;")
        title_bar.addWidget(self._title_label)
        title_bar.addStretch()

        self._clear_btn = QPushButton("清空")
        self._clear_btn.setStyleSheet("""
            QPushButton {
                background: #3a3a40; color: #ccc; border: none;
                border-radius: 4px; padding: 3px 10px; font-size: 11px;
            }
            QPushButton:hover { background: #555; }
        """)
        self._clear_btn.clicked.connect(self.clear_content)
        title_bar.addWidget(self._clear_btn)

        # 推后台按钮
        min_btn = QPushButton("—")
        min_btn.setFixedSize(24, 24)
        min_btn.setToolTip("隐藏到后台（Ctrl+D 可重新截图）")
        min_btn.setStyleSheet("""
            QPushButton {
                background: #3a3a40; color: #ccc; border: none;
                border-radius: 12px; font-size: 14px; font-weight: bold;
            }
            QPushButton:hover { background: #555; color: white; }
        """)
        min_btn.clicked.connect(self.hide)
        title_bar.addWidget(min_btn)

        close_btn = QPushButton("✕")
        close_btn.setFixedSize(24, 24)
        close_btn.setStyleSheet("""
            QPushButton {
                background: #c0392b; color: white; border: none;
                border-radius: 12px; font-size: 13px; font-weight: bold;
            }
            QPushButton:hover { background: #e74c3c; }
        """)
        close_btn.clicked.connect(self.close)
        title_bar.addWidget(close_btn)

        layout.addLayout(title_bar)

        # ---- 内容显示区 ----
        self._text_view = QTextEdit()
        self._text_view.setReadOnly(True)
        self._text_view.setTextInteractionFlags(
            Qt.TextInteractionFlag.TextSelectableByMouse |
            Qt.TextInteractionFlag.TextSelectableByKeyboard
        )
        self._text_view.setFont(QFont("Microsoft YaHei", RESULT_FONT_SIZE))
        self._text_view.setStyleSheet("""
            QTextEdit {
                background-color: transparent;
                color: #e0e0e0;
                border: 1px solid #3a3a45;
                border-radius: 6px;
                padding: 8px;
                selection-background-color: #3a5a8c;
            }
        """)
        self._text_view.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        layout.addWidget(self._text_view, stretch=1)

        # ---- 语音识别区 ----
        self._speech_status = QLabel("")
        self._speech_status.setFont(QFont("Microsoft YaHei", 10))
        self._speech_status.setStyleSheet("color: #88cc88; padding: 0 4px;")
        self._speech_status.hide()
        layout.addWidget(self._speech_status)

        self._speech_text = QTextEdit()
        self._speech_text.setReadOnly(True)
        self._speech_text.setFixedHeight(80)
        self._speech_text.setFont(QFont("Microsoft YaHei", 11))
        self._speech_text.setStyleSheet("""
            QTextEdit {
                background: #1a2a1a; color: #ccddcc; border: 1px solid #2a4a2a;
                border-radius: 4px; padding: 4px 8px;
            }
        """)
        self._speech_text.hide()
        self._speech_sentences = []
        self._speech_selected = -1
        layout.addWidget(self._speech_text)

        # ---- 待发送图片缩略图区 ----
        from PyQt6.QtWidgets import QScrollArea, QSizePolicy

        self._thumb_scroll = QScrollArea()
        self._thumb_scroll.setFixedHeight(86)
        self._thumb_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        self._thumb_scroll.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self._thumb_scroll.setStyleSheet("""
            QScrollArea { background: transparent; border: 1px solid #3a3a45; border-radius: 4px; }
            QScrollBar:horizontal { height: 5px; background: #2a2a30; border-radius: 2px; }
            QScrollBar::handle:horizontal { background: #555; border-radius: 2px; }
            QScrollBar::add-line:horizontal, QScrollBar::sub-line:horizontal { width: 0; }
        """)
        self._thumb_scroll.hide()

        self._thumb_container = QWidget()
        self._thumb_container.setStyleSheet("background: transparent;")
        self._thumb_container_layout = QHBoxLayout(self._thumb_container)
        self._thumb_container_layout.setContentsMargins(4, 4, 4, 4)
        self._thumb_container_layout.setSpacing(4)
        self._thumb_container_layout.addStretch()
        self._thumb_scroll.setWidget(self._thumb_container)
        self._thumb_scroll.setWidgetResizable(True)
        layout.addWidget(self._thumb_scroll)

        # ---- 复制按钮区（缩略图下方，有图时显示） ----
        self._copy_bar = QHBoxLayout()
        self._copy_bar.setContentsMargins(0, 2, 0, 0)

        self._copy_btn = QPushButton("📋复制")
        self._copy_btn.setFixedHeight(26)
        self._copy_btn.setStyleSheet("""
            QPushButton {
                background: #2a5a3a; color: #aaddaa; border: 1px solid #3a6a4a;
                border-radius: 4px; padding: 2px 14px; font-size: 10px;
            }
            QPushButton:hover { background: #3a7a4a; color: #ccffcc; }
        """)
        self._copy_btn.clicked.connect(self._copy_images_to_clipboard)
        self._copy_btn.hide()
        self._copy_bar.addWidget(self._copy_btn)
        self._copy_bar.addStretch()
        layout.addLayout(self._copy_bar)

        # ---- 底部追问输入区 ----
        ask_layout = QHBoxLayout()
        ask_layout.setSpacing(6)

        self._ask_input = QLineEdit()
        self._ask_input.setPlaceholderText("💬 输入追问内容，Enter 发送...")
        self._ask_input.setFont(QFont("Microsoft YaHei", 11))
        self._ask_input.setStyleSheet("""
            QLineEdit {
                background-color: #2a2a35;
                color: #e0e0e0;
                border: 1px solid #4a4a55;
                border-radius: 5px;
                padding: 6px 10px;
            }
            QLineEdit:focus { border-color: #4a8af4; }
        """)
        self._ask_input.returnPressed.connect(self._send_follow_up)
        ask_layout.addWidget(self._ask_input, stretch=1)

        # ---- 模型选择器 ----
        from config import MODEL_OPTIONS, MODEL_OPTIONS_DEFAULT, DOUBAO_MODEL_NAME
        self._model_combo = QComboBox()
        self._model_combo.setFixedWidth(110)
        self._model_combo.setStyleSheet("""
            QComboBox {
                background: #2a2a35; color: #aaccff; border: 1px solid #4a4a55;
                border-radius: 5px; padding: 4px 8px; font-size: 10px;
            }
            QComboBox:hover { border-color: #6a8af4; }
            QComboBox::drop-down { border: none; }
            QComboBox QAbstractItemView {
                background: #2a2a35; color: #ccc; selection-background-color: #3a5a8c;
                border: 1px solid #555;
            }
        """)
        for label, model_id in MODEL_OPTIONS.items():
            self._model_combo.addItem(label, model_id)
            # 选中当前模型
            if model_id == DOUBAO_MODEL_NAME:
                self._model_combo.setCurrentIndex(self._model_combo.count() - 1)
        self._model_combo.currentIndexChanged.connect(self._on_model_changed)
        ask_layout.addWidget(self._model_combo)

        # 设置按钮
        settings_btn = QPushButton("⚙")
        settings_btn.setFixedWidth(30)
        settings_btn.setToolTip("设置 API Key / OCR 凭证")
        settings_btn.setStyleSheet("""
            QPushButton {
                background: #3a3a40; color: #aaa; border: 1px solid #555;
                border-radius: 5px; padding: 4px 0; font-size: 14px;
            }
            QPushButton:hover { background: #555; color: #fff; }
        """)
        settings_btn.clicked.connect(self._open_settings)
        ask_layout.addWidget(settings_btn)

        send_btn = QPushButton("发送")
        send_btn.setStyleSheet("""
            QPushButton {
                background: #2b5db8; color: white; border: none;
                border-radius: 5px; padding: 6px 14px; font-size: 11px; font-weight: bold;
            }
            QPushButton:hover { background: #3a6fd8; }
        """)
        send_btn.clicked.connect(self._send_follow_up)
        ask_layout.addWidget(send_btn)

        layout.addLayout(ask_layout)

        # 右侧区域加入水平布局
        self._main_h_layout.addWidget(right_widget, stretch=1)

    # ============================================================
    # Positioning
    # ============================================================

    def _position_bottom_right(self):
        from PyQt6.QtWidgets import QApplication
        screen = QApplication.primaryScreen()
        if screen:
            available = screen.availableGeometry()
            x = available.right() - self.width() - 20
            y = available.bottom() - self.height() - 40
            self.move(x, y)

    def position_near_rect(self, rect: QRect):
        from PyQt6.QtWidgets import QApplication
        screen = QApplication.primaryScreen()
        if not screen:
            return
        available = screen.availableGeometry()
        gap = 16
        win_w = self.width()
        win_h = self.height()

        x = rect.right() + gap
        y = rect.top()

        if x + win_w > available.right():
            x = rect.left() - win_w - gap
        if x < available.left():
            x = rect.left()
            y = rect.bottom() + gap

        if y + win_h > available.bottom():
            y = available.bottom() - win_h - gap
        if y < available.top():
            y = available.top() + gap

        self.move(x, y)

    # ============================================================
    # Loading Animation（呼吸灯）
    # ============================================================

    def start_loading(self, base_text: str = "⏳ AI 正在思考"):
        """启动加载动画。"""
        self._loading = True
        self._loading_dots = 0
        self._loading_base_text = base_text
        self._update_loading_display()
        self._loading_timer.start(400)  # 每 400ms 切换

    def stop_loading(self):
        """停止加载动画。"""
        self._loading = False
        self._loading_timer.stop()
        self._title_label.setText("🤖 AI 解析结果")

    def _tick_loading(self):
        """加载动画的每个 tick。"""
        self._loading_dots = (self._loading_dots + 1) % 4
        self._update_loading_display()

    def _update_loading_display(self):
        """更新加载动画显示。"""
        dots = " ·" * self._loading_dots
        if self._loading_dots == 0:
            dots = ""
        self._title_label.setText(f"{self._loading_base_text}{dots}")

        # 内容区：在已有内容下方追加加载动画
        pulse = ["○", "◔", "◑", "◕"][self._loading_dots]
        loading_html = (
            f"<div style='text-align:center; padding:20px 0;'>"
            f"<span style='font-size:28px; color:#4a8af4;'>{pulse}</span><br>"
            f"<span style='color:#8899aa; font-size:13px;'>{self._loading_base_text}{dots}</span>"
            f"</div>"
        )

        if self._buffer.strip():
            # 已有内容 + 后面加加载动画
            self._text_view.setHtml(
                self._render_markdown(self._buffer) + loading_html
            )
        else:
            self._text_view.setHtml(loading_html)

    # ============================================================
    # Content API
    # ============================================================

    def append_text(self, text: str):
        """追加流式文本。首次调用时自动停止加载动画并保留已有内容。"""
        if self._loading:
            self.stop_loading()
            # 保留已有内容（如用户问题），不清空 buffer

        self._buffer += text
        self._text_view.setHtml(self._render_markdown(self._buffer))
        cursor = self._text_view.textCursor()
        cursor.movePosition(QTextCursor.MoveOperation.End)
        self._text_view.setTextCursor(cursor)

    def set_content(self, text: str):
        self._buffer = text
        self._text_view.setHtml(self._render_markdown(text))

    def clear_content(self):
        self._buffer = ""
        self._text_view.clear()

    def get_content(self) -> str:
        return self._buffer

    # ---- 语音识别 ----

    def set_speech_status(self, text: str):
        """显示/隐藏语音状态。"""
        self._speech_status.setText(text)
        self._speech_status.setVisible(bool(text))

    def set_speech_sentences(self, sentences: list):
        """更新语音识别句子列表。"""
        self._speech_sentences = list(sentences)
        self._speech_text.show()
        self._speech_status.setText(f"🎤 已识别 {len(sentences)} 句 (滚轮选择, Ctrl+Enter 提问)")
        self._refresh_speech_display()

    def get_speech_selected(self) -> str:
        """获取当前选中的句子。"""
        if 0 <= self._speech_selected < len(self._speech_sentences):
            return self._speech_sentences[self._speech_selected]
        return ""

    def wheelEvent(self, event):
        """滚轮选择语音识别句子。"""
        if self._speech_sentences and self._speech_text.isVisible():
            delta = event.angleDelta().y()
            if delta > 0:
                self._speech_selected = max(0, self._speech_selected - 1)
            elif delta < 0:
                self._speech_selected = min(len(self._speech_sentences) - 1, self._speech_selected + 1)
            self._refresh_speech_display()
        super().wheelEvent(event)

    def _refresh_speech_display(self):
        """刷新语音句子显示（高亮选中句）。"""
        html = ""
        for i, s in enumerate(self._speech_sentences):
            if i == self._speech_selected:
                html += f"<span style='background:#3a6a3a;color:#fff;padding:1px 4px;'>▶ {s}</span> "
            else:
                html += f"<span style='color:#99bb99;'>{s}</span> "
        self._speech_text.setHtml(html)

    # ---- 追问 ----

    def get_input_text(self) -> str:
        """获取底部追问输入框当前文本。"""
        return self._ask_input.text().strip()

    def clear_input(self):
        """清空底部追问输入框。"""
        self._ask_input.clear()

    def focus_input(self):
        """聚焦到底部输入框（截完图后直接可打字）。"""
        self._ask_input.setFocus()
        self._ask_input.raise_()

    # ---- 待发送图片缩略图 ----

    def add_image_thumbnail(self, base64_data: str, qimage: QImage):
        """添加一张截图缩略图到待发送列表。"""
        self._pending_images.append(base64_data)
        self._pending_qimgs.append(qimage)

        # 显示复制按钮
        self._copy_btn.show()

        # 创建 70×52 缩略图
        from PyQt6.QtWidgets import QLabel
        pm = QPixmap.fromImage(qimage).scaled(
            70, 52, Qt.AspectRatioMode.KeepAspectRatio,
            Qt.TransformationMode.SmoothTransformation)

        container = QWidget()
        container.setFixedSize(82, 76)
        container.setStyleSheet("background: #2a2a35; border-radius: 4px;")

        vbox = QVBoxLayout(container)
        vbox.setContentsMargins(3, 3, 3, 1)
        vbox.setSpacing(1)

        img_label = QLabel()
        img_label.setPixmap(pm)
        img_label.setFixedSize(70, 52)
        img_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        img_label.setStyleSheet("border: 1px solid #4a4a55; border-radius: 2px;")
        vbox.addWidget(img_label, alignment=Qt.AlignmentFlag.AlignCenter)

        idx = len(self._thumb_widgets)
        del_btn = QPushButton(f"✕ {idx + 1}")
        del_btn.setFixedSize(50, 16)
        del_btn.setStyleSheet("""
            QPushButton { background: #c0392b; color: white; border: none;
                border-radius: 2px; font-size: 9px; padding: 0; }
            QPushButton:hover { background: #e74c3c; }
        """)
        del_btn.clicked.connect(lambda _, i=idx: self._remove_thumbnail(i))
        vbox.addWidget(del_btn, alignment=Qt.AlignmentFlag.AlignCenter)

        # 插入到 stretch 之前
        count = self._thumb_container_layout.count()
        self._thumb_container_layout.insertWidget(count - 1, container)
        self._thumb_widgets.append(container)

        self._thumb_scroll.show()
        # 调整窗口高度
        self.resize(self.width(), self.height() + 1)
        self.resize(self.width(), self.height() - 1)

    def _remove_thumbnail(self, index: int):
        """删除指定缩略图 — 不重建，直接移除并更新编号。"""
        if not (0 <= index < len(self._pending_images)):
            return
        self._pending_images.pop(index)
        self._pending_qimgs.pop(index)
        w = self._thumb_widgets.pop(index)
        self._thumb_container_layout.removeWidget(w)
        w.hide()
        w.deleteLater()
        # 更新剩余缩略图编号（不重建 widget）
        for i, container in enumerate(self._thumb_widgets):
            vbox = container.layout()
            if vbox:
                for j in range(vbox.count()):
                    item = vbox.itemAt(j)
                    btn = item.widget() if item else None
                    if isinstance(btn, QPushButton) and "✕" in (btn.text() or ""):
                        btn.setText(f"✕ {i + 1}")
                        try:
                            btn.clicked.disconnect()
                        except Exception:
                            pass
                        btn.clicked.connect(lambda _, idx=i: self._remove_thumbnail(idx))
        if not self._pending_images:
            self._thumb_scroll.hide()
            self._copy_btn.hide()

    def _copy_images_to_clipboard(self):
        """将所有待发送图片复制到剪贴板。"""
        if not self._pending_qimgs:
            return
        from PyQt6.QtWidgets import QApplication
        clipboard = QApplication.clipboard()
        if len(self._pending_qimgs) == 1:
            clipboard.setImage(self._pending_qimgs[0])
        else:
            # 多张图：水平拼接
            total_w = sum(img.width() for img in self._pending_qimgs) + (len(self._pending_qimgs) - 1) * 4
            max_h = max(img.height() for img in self._pending_qimgs)
            combined = QImage(total_w, max_h, QImage.Format.Format_ARGB32)
            combined.fill(QColor(255, 255, 255))
            p = QPainter(combined)
            x = 0
            for img in self._pending_qimgs:
                p.drawImage(x, (max_h - img.height()) // 2, img)
                x += img.width() + 4
            p.end()
            clipboard.setImage(combined)

    def get_pending_images(self) -> list:
        """获取所有待发送图片的 base64 列表。"""
        return list(self._pending_images)

    def clear_pending_images(self):
        """清空待发送图片。"""
        self._pending_images = []
        self._pending_qimgs = []
        for w in self._thumb_widgets:
            self._thumb_container_layout.removeWidget(w)
            w.deleteLater()
        self._thumb_widgets = []
        self._thumb_scroll.hide()
        self._copy_btn.hide()

    def _send_follow_up(self):
        """用户按 Enter 或点击发送按钮时触发。Ctrl+Enter → 发送语音句子。"""
        from PyQt6.QtWidgets import QApplication
        ctrl_held = QApplication.keyboardModifiers() & Qt.KeyboardModifier.ControlModifier

        if ctrl_held and self._speech_sentences:
            # Ctrl+Enter → 发送选中的语音句子
            selected = self.get_speech_selected()
            if selected:
                self._ask_input.setText(selected)
                self._ask_input.clear()
                self.follow_up_requested.emit(selected)
                return

        text = self._ask_input.text().strip()
        if text or self._pending_images:
            self._ask_input.clear()
            self.follow_up_requested.emit(text)

    def _on_model_changed(self, index):
        """模型下拉框切换时触发。"""
        model_id = self._model_combo.currentData()
        if model_id:
            self.model_changed.emit(model_id)

    def _open_settings(self):
        """点击设置按钮 → 发射信号由 main.py 处理。"""
        self.settings_requested.emit()

    def set_current_model(self, model_name: str):
        """外部设置当前选中的模型（启动时同步）。"""
        for i in range(self._model_combo.count()):
            if self._model_combo.itemData(i) == model_name:
                self._model_combo.setCurrentIndex(i)
                break

    # ============================================================
    # Markdown → HTML
    # ============================================================

    def _render_markdown(self, text: str) -> str:
        """将 Markdown 转换为 HTML，支持表格。"""
        html = text

        # 0. 先处理表格（在转义 HTML 之前，因为我们要插入真正的 HTML 标签）
        html = self._render_tables(html)

        # 1. 转义 HTML 特殊字符（保护已处理的表格 HTML）
        html = self._escape_except_tags(html)

        # 2. 代码块 ```...```
        html = re.sub(
            r'```(\w*)\n(.*?)```',
            r'<pre style="background:#1e1e2a;padding:10px;border-radius:4px;overflow-x:auto;"><code>\2</code></pre>',
            html, flags=re.DOTALL)
        # 行内代码 `...`
        html = re.sub(
            r'`([^`]+)`',
            r'<code style="background:#2a2a35;padding:1px 4px;border-radius:3px;color:#f0c070;">\1</code>',
            html)
        # 标题
        html = re.sub(r'^### (.+)$', r'<h3 style="color:#88aaff;margin:8px 0;">\1</h3>', html, flags=re.MULTILINE)
        html = re.sub(r'^## (.+)$', r'<h2 style="color:#88ccff;margin:10px 0;">\1</h2>', html, flags=re.MULTILINE)
        html = re.sub(r'^# (.+)$', r'<h1 style="color:#aaddff;margin:12px 0;">\1</h1>', html, flags=re.MULTILINE)
        # 粗体 / 斜体
        html = re.sub(r'\*\*(.+?)\*\*', r'<b>\1</b>', html)
        html = re.sub(r'\*(.+?)\*', r'<i>\1</i>', html)
        # 列表
        html = re.sub(r'^- (.+)$', r'<li>\1</li>', html, flags=re.MULTILINE)
        html = re.sub(r'^\d+\. (.+)$', r'<li>\1</li>', html, flags=re.MULTILINE)
        # 水平线
        html = re.sub(r'^---$', r'<hr style="border:0;border-top:1px solid #444;">', html, flags=re.MULTILINE)
        html = html.replace("\n\n", "<br><br>")

        return f"""
        <div style="font-family: 'Microsoft YaHei', 'Segoe UI', sans-serif;
                    font-size: {RESULT_FONT_SIZE}px; line-height: 1.6; color: #e0e0e0;">
            {html}
        </div>
        """

    def _render_tables(self, text: str) -> str:
        """将 Markdown 表格转换为 HTML 表格。"""
        lines = text.split("\n")
        result = []
        table_buffer = []
        in_table = False

        def flush_table():
            """将缓存的表格行转换为 HTML。"""
            nonlocal table_buffer
            if not table_buffer:
                return

            # 过滤：至少要有 separator 行（|---...|）才算是表格
            has_separator = any(
                re.match(r'^\|[\s\-:]+\|', row) for row in table_buffer
            )
            if not has_separator or len(table_buffer) < 2:
                result.extend(table_buffer)
                table_buffer = []
                return

            html_parts = ['<table style="border-collapse:collapse;width:100%;margin:10px 0;">']

            header_done = False
            for row in table_buffer:
                # Separator 行 → 标记 header 结束
                if re.match(r'^\|[\s\-:]+\|', row):
                    header_done = True
                    continue

                cells = [c.strip() for c in row.split("|")[1:-1]]
                if not cells:
                    continue

                if not header_done:
                    # 表头
                    html_parts.append("<tr>")
                    for c in cells:
                        html_parts.append(
                            f'<th style="border:1px solid #3a4a5a;padding:6px 12px;'
                            f'background:#2a3a50;color:#aaccff;text-align:left;">{c}</th>'
                        )
                    html_parts.append("</tr>")
                else:
                    # 表体
                    html_parts.append("<tr>")
                    for c in cells:
                        html_parts.append(
                            f'<td style="border:1px solid #3a3a45;padding:5px 10px;'
                            f'vertical-align:top;">{c}</td>'
                        )
                    html_parts.append("</tr>")

            html_parts.append("</table>")
            result.append("".join(html_parts))
            table_buffer = []

        for line in lines:
            is_table_row = bool(re.match(r'^\|.+\|$', line.strip()))

            if is_table_row:
                if not in_table:
                    in_table = True
                table_buffer.append(line)
            else:
                if in_table:
                    flush_table()
                    in_table = False
                result.append(line)

        # 处理末尾的表格
        if in_table:
            flush_table()

        return "\n".join(result)

    def _escape_except_tags(self, text: str) -> str:
        """转义 HTML 特殊字符，但保留已有的 HTML 标签。"""
        # 保护已存在的 HTML 标签
        protected = []
        def _save(m):
            protected.append(m.group(0))
            return f"\x00PROTECT{len(protected) - 1}\x00"

        # 保护所有 HTML 标签
        text = re.sub(r'<[^>]+>', _save, text)

        # 转义剩余文本中的特殊字符
        text = text.replace("&", "&amp;")
        text = text.replace("<", "&lt;")
        text = text.replace(">", "&gt;")

        # 还原保护的 HTML 标签
        for i, tag in enumerate(protected):
            text = text.replace(f"\x00PROTECT{i}\x00", tag)

        return text

    # ============================================================
    # Mouse Events — 拖动 + 缩放 + 边缘检测
    # ============================================================

    def _get_resize_edge(self, pos: QPoint) -> str:
        """检测鼠标在窗口的哪个边缘/角落，返回方向字符串。"""
        w, h = self.width(), self.height()
        x, y = pos.x(), pos.y()

        on_left = x <= EDGE_MARGIN
        on_right = x >= w - EDGE_MARGIN
        on_top = y <= EDGE_MARGIN
        on_bottom = y >= h - EDGE_MARGIN

        if on_top and on_left:
            return "nw"
        if on_top and on_right:
            return "ne"
        if on_bottom and on_left:
            return "sw"
        if on_bottom and on_right:
            return "se"
        if on_top:
            return "n"
        if on_bottom:
            return "s"
        if on_left:
            return "w"
        if on_right:
            return "e"
        return ""

    def _cursor_for_edge(self, edge: str) -> QCursor:
        """根据边缘方向返回对应的光标。"""
        cursors = {
            "n": Qt.CursorShape.SizeVerCursor,
            "s": Qt.CursorShape.SizeVerCursor,
            "e": Qt.CursorShape.SizeHorCursor,
            "w": Qt.CursorShape.SizeHorCursor,
            "ne": Qt.CursorShape.SizeBDiagCursor,
            "sw": Qt.CursorShape.SizeBDiagCursor,
            "nw": Qt.CursorShape.SizeFDiagCursor,
            "se": Qt.CursorShape.SizeFDiagCursor,
        }
        return QCursor(cursors.get(edge, Qt.CursorShape.ArrowCursor))

    def mousePressEvent(self, event: QMouseEvent):
        if event.button() != Qt.MouseButton.LeftButton:
            return

        pos = event.position().toPoint()
        edge = self._get_resize_edge(pos)

        if edge:
            # 开始缩放
            self._resizing = True
            self._resize_edge = edge
            self._resize_start_geo = self.geometry()
            self._resize_start_pos = event.globalPosition().toPoint()
            event.accept()
        else:
            # 开始拖动
            self._dragging = True
            self._drag_pos = event.globalPosition().toPoint() - self.frameGeometry().topLeft()
            event.accept()

    def mouseMoveEvent(self, event: QMouseEvent):
        pos = event.position().toPoint()
        gpos = event.globalPosition().toPoint()

        if self._resizing:
            # 缩放窗口
            self._do_resize(gpos)
            event.accept()
        elif self._dragging:
            # 拖动窗口
            new_pos = gpos - self._drag_pos
            self.move(new_pos)
            event.accept()
        else:
            # 仅移动鼠标 — 更新光标提示
            edge = self._get_resize_edge(pos)
            if edge:
                self.setCursor(self._cursor_for_edge(edge))
            else:
                self.setCursor(Qt.CursorShape.ArrowCursor)

    def mouseReleaseEvent(self, event: QMouseEvent):
        if event.button() == Qt.MouseButton.LeftButton:
            self._dragging = False
            self._resizing = False
            self._resize_edge = ""
            self._resize_start_geo = None
            self._resize_start_pos = None
            self.setCursor(Qt.CursorShape.ArrowCursor)

    def _do_resize(self, gpos: QPoint):
        """根据当前缩放方向和鼠标位置调整窗口几何。"""
        if not self._resize_start_geo or not self._resize_start_pos:
            return

        dx = gpos.x() - self._resize_start_pos.x()
        dy = gpos.y() - self._resize_start_pos.y()
        geo = QRect(self._resize_start_geo)
        edge = self._resize_edge

        if "e" in edge:
            geo.setRight(max(geo.left() + MIN_WIDTH, geo.right() + dx))
        if "w" in edge:
            geo.setLeft(min(geo.right() - MIN_WIDTH, geo.left() + dx))
        if "s" in edge:
            geo.setBottom(max(geo.top() + MIN_HEIGHT, geo.bottom() + dy))
        if "n" in edge:
            geo.setTop(min(geo.bottom() - MIN_HEIGHT, geo.top() + dy))

        self.setGeometry(geo)

    def showEvent(self, event):
        """窗口显示时：隐藏任务栏图标 + 防屏幕捕获。"""
        super().showEvent(event)
        if not self._win_hidden_done and self.winId():
            self._win_hidden_done = True
            try:
                import ctypes
                from PyQt6.QtCore import QTimer
                hwnd = int(self.winId())
                # 从任务栏隐藏
                ctypes.windll.user32.SetWindowLongPtrW(hwnd, -20,
                    ctypes.windll.user32.GetWindowLongPtrW(hwnd, -20) | 0x80)
                # 防止被腾讯会议等屏幕共享捕获（Win10 2004+）
                try:
                    ctypes.windll.user32.SetWindowDisplayAffinity(hwnd, 0x11)
                except Exception:
                    pass
            except Exception:
                pass

    def closeEvent(self, event):
        """拦截关闭事件：只隐藏窗口不退出程序。"""
        self.hide()
        event.ignore()

    # ============================================================
    # Context Menu
    # ============================================================

    def contextMenuEvent(self, event):
        menu = QMenu(self)
        menu.setStyleSheet("""
            QMenu {
                background-color: #2a2a32; color: #e0e0e0;
                border: 1px solid #444; padding: 4px;
            }
            QMenu::item { padding: 6px 20px; }
            QMenu::item:selected { background-color: #3a5a8c; }
        """)

        clear_action = QAction("清空内容", self)
        clear_action.triggered.connect(self.clear_content)
        menu.addAction(clear_action)

        copy_action = QAction("复制全部", self)
        copy_action.triggered.connect(lambda: _get_clipboard().setText(self._buffer))
        menu.addAction(copy_action)

        menu.addSeparator()

        hide_action = QAction("关闭窗口", self)
        hide_action.triggered.connect(self.hide)
        menu.addAction(hide_action)

        menu.exec(event.globalPos())


def _get_clipboard():
    from PyQt6.QtWidgets import QApplication
    return QApplication.clipboard()
