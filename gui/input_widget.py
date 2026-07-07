# -*- coding: utf-8 -*-
"""
快捷输入窗口 — 截图完成后弹出，显示缩略图预览 + 追问输入。
支持多图缩略图预览、× 删除单张、Enter 确认发送、Escape 取消。
"""
from typing import List, Optional
from PyQt6.QtCore import Qt, pyqtSignal, QSize
from PyQt6.QtGui import QFont, QColor, QPalette, QPixmap, QImage, QIcon
from PyQt6.QtWidgets import (
    QDialog, QVBoxLayout, QTextEdit, QHBoxLayout,
    QPushButton, QLabel, QScrollArea, QWidget, QSizePolicy,
)

from config import INPUT_WINDOW_WIDTH

INPUT_BASE_HEIGHT = 160
THUMB_AREA_HEIGHT = 120


class InputDialog(QDialog):
    """
    截图后的追问输入弹窗 — 支持多图缩略图预览。

    用法:
        dlg = InputDialog()
        dlg.add_thumbnails([qimage1, qimage2, ...])
        if dlg.exec() == QDialog.DialogCode.Accepted:
            user_text = dlg.get_text()
    """
    confirmed = pyqtSignal(str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self._thumb_pixmaps: List[QPixmap] = []
        self._thumb_widgets: List[QWidget] = []
        self._has_thumbs = False
        self._setup_ui()

    def _setup_ui(self):
        self.setWindowTitle("Ai_Flow 截图助手")
        base_h = INPUT_BASE_HEIGHT
        self.setFixedSize(INPUT_WINDOW_WIDTH, base_h)

        self.setWindowFlags(
            Qt.WindowType.FramelessWindowHint |
            Qt.WindowType.WindowStaysOnTopHint |
            Qt.WindowType.Tool
        )
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, False)
        self.setCursor(Qt.CursorShape.ArrowCursor)

        # 深色背景
        palette = self.palette()
        palette.setColor(QPalette.ColorRole.Window, QColor(40, 40, 45, 245))
        self.setPalette(palette)
        self.setAutoFillBackground(True)

        # 主布局
        self._main_layout = QVBoxLayout(self)
        self._main_layout.setContentsMargins(16, 12, 16, 12)
        self._main_layout.setSpacing(8)

        # 标题
        title = QLabel("✏️ 追问（可选）- 点击发送提交给 AI")
        title.setFont(QFont("Microsoft YaHei", 11))
        title.setStyleSheet("color: #c8c8c8;")
        self._main_layout.addWidget(title)

        # ---- 缩略图区域（初始隐藏） ----
        self._thumb_container = QWidget()
        self._thumb_layout = QHBoxLayout(self._thumb_container)
        self._thumb_layout.setContentsMargins(0, 0, 0, 4)
        self._thumb_layout.setSpacing(6)
        self._thumb_layout.addStretch()

        self._thumb_scroll = QScrollArea()
        self._thumb_scroll.setWidget(self._thumb_container)
        self._thumb_scroll.setWidgetResizable(True)
        self._thumb_scroll.setFixedHeight(100)
        self._thumb_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        self._thumb_scroll.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self._thumb_scroll.setStyleSheet("""
            QScrollArea { background: transparent; border: none; }
            QScrollBar:horizontal { height: 6px; background: #2a2a30; border-radius: 3px; }
            QScrollBar::handle:horizontal { background: #555; border-radius: 3px; }
            QScrollBar::add-line:horizontal, QScrollBar::sub-line:horizontal { width: 0; }
        """)
        self._thumb_scroll.hide()
        self._main_layout.addWidget(self._thumb_scroll)

        # ---- 多行输入框 ----
        self._text_edit = QTextEdit()
        self._text_edit.setPlaceholderText("输入你的问题，例如：请分析这些截图的内容...")
        self._text_edit.setFont(QFont("Microsoft YaHei", 11))
        self._text_edit.setCursor(Qt.CursorShape.ArrowCursor)
        self._text_edit.setTextInteractionFlags(Qt.TextInteractionFlag.NoTextInteraction)
        self._text_edit.setStyleSheet("""
            QTextEdit {
                background-color: #2a2a30; color: #e8e8e8;
                border: 1px solid #555; border-radius: 6px; padding: 8px;
            }
            QTextEdit:focus { border-color: #4a8af4; }
        """)
        self._text_edit.setMaximumHeight(60)
        self._main_layout.addWidget(self._text_edit)

        # ---- 按钮行 ----
        btn_layout = QHBoxLayout()
        btn_layout.setSpacing(10)

        self._skip_btn = QPushButton("取消 (Esc)")
        self._skip_btn.setCursor(Qt.CursorShape.ArrowCursor)
        self._skip_btn.setStyleSheet("""
            QPushButton {
                background-color: #3a3a40; color: #aaa; border: 1px solid #555;
                border-radius: 5px; padding: 6px 18px; font-size: 12px;
            }
            QPushButton:hover { background-color: #4a4a50; color: #ddd; }
        """)
        self._skip_btn.clicked.connect(self._on_skip)

        self._confirm_btn = QPushButton("发送 (Enter)")
        self._confirm_btn.setCursor(Qt.CursorShape.ArrowCursor)
        self._confirm_btn.setStyleSheet("""
            QPushButton {
                background-color: #2b5db8; color: white; border: 1px solid #3a6fd8;
                border-radius: 5px; padding: 6px 18px; font-size: 12px; font-weight: bold;
            }
            QPushButton:hover { background-color: #3a6fd8; }
        """)
        self._confirm_btn.clicked.connect(self._on_confirm)

        btn_layout.addStretch()
        btn_layout.addWidget(self._skip_btn)
        btn_layout.addWidget(self._confirm_btn)
        self._main_layout.addLayout(btn_layout)

        self._text_edit.setFocus()
        self._center_on_screen()

    # ============================================================
    # Thumbnails API
    # ============================================================

    def add_thumbnails(self, images: List[QImage]):
        """添加缩略图到预览区。"""
        if not images:
            return

        for img in images:
            # 压缩为 80×60 缩略图
            thumb = QPixmap.fromImage(img).scaled(
                80, 60, Qt.AspectRatioMode.KeepAspectRatio,
                Qt.TransformationMode.SmoothTransformation)
            self._thumb_pixmaps.append(thumb)
            self._add_thumb_widget(thumb, len(self._thumb_pixmaps) - 1)

        self._has_thumbs = True
        self._update_layout()

    def _add_thumb_widget(self, pixmap: QPixmap, index: int):
        """创建单个缩略图 widget（图片 + × 删除按钮）。"""
        container = QWidget()
        container.setFixedSize(96, 92)
        container.setStyleSheet("background: #2a2a35; border-radius: 6px;")

        vbox = QVBoxLayout(container)
        vbox.setContentsMargins(4, 4, 4, 2)
        vbox.setSpacing(2)

        # 图片
        img_label = QLabel()
        img_label.setPixmap(pixmap)
        img_label.setFixedSize(80, 60)
        img_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        img_label.setStyleSheet("border: 1px solid #4a4a55; border-radius: 3px;")
        vbox.addWidget(img_label, alignment=Qt.AlignmentFlag.AlignCenter)

        # 删除按钮 + 编号
        del_btn = QPushButton(f"✕ #{index + 1}")
        del_btn.setFixedSize(60, 18)
        del_btn.setStyleSheet("""
            QPushButton {
                background: #c0392b; color: white; border: none;
                border-radius: 3px; font-size: 9px; padding: 0;
            }
            QPushButton:hover { background: #e74c3c; }
        """)
        del_btn.clicked.connect(lambda: self._remove_thumbnail(index))
        vbox.addWidget(del_btn, alignment=Qt.AlignmentFlag.AlignCenter)

        # 插入到 stretch 之前
        count = self._thumb_layout.count()
        self._thumb_layout.insertWidget(count - 1, container)
        self._thumb_widgets.append(container)

    def _remove_thumbnail(self, index: int):
        """删除指定缩略图。"""
        if 0 <= index < len(self._thumb_pixmaps):
            self._thumb_pixmaps.pop(index)
            # 移除 widget
            widget = self._thumb_widgets.pop(index)
            self._thumb_layout.removeWidget(widget)
            widget.deleteLater()
            # 重建所有缩略图（更新编号）
            self._rebuild_thumbnails()

    def _rebuild_thumbnails(self):
        """重建缩略图区域（删除后更新编号）。"""
        # 清除旧 widgets
        for w in self._thumb_widgets:
            self._thumb_layout.removeWidget(w)
            w.deleteLater()
        self._thumb_widgets.clear()

        # 重建
        for i, pm in enumerate(self._thumb_pixmaps):
            self._add_thumb_widget(pm, i)

        if not self._thumb_pixmaps:
            self._has_thumbs = False
            self._update_layout()

    def _update_layout(self):
        """根据是否有缩略图调整窗口高度。"""
        if self._has_thumbs:
            self.setFixedSize(INPUT_WINDOW_WIDTH, INPUT_BASE_HEIGHT + THUMB_AREA_HEIGHT)
            self._thumb_scroll.show()
        else:
            self.setFixedSize(INPUT_WINDOW_WIDTH, INPUT_BASE_HEIGHT)
            self._thumb_scroll.hide()

    # ============================================================
    # Actions
    # ============================================================

    def _center_on_screen(self):
        from PyQt6.QtWidgets import QApplication
        screen = QApplication.primaryScreen()
        if screen:
            geo = screen.availableGeometry()
            x = (geo.width() - self.width()) // 2 + geo.x()
            y = (geo.height() - self.height()) // 3 + geo.y()
            self.move(x, y)

    def _on_confirm(self):
        self.confirmed.emit(self.get_text())
        self.accept()

    def _on_skip(self):
        self.confirmed.emit("")
        self.accept()

    def set_text(self, text: str):
        if text:
            self._text_edit.setPlainText(text)
            cursor = self._text_edit.textCursor()
            cursor.movePosition(cursor.MoveOperation.End)
            self._text_edit.setTextCursor(cursor)

    def get_text(self) -> str:
        return self._text_edit.toPlainText().strip()

    def keyPressEvent(self, event):
        key = event.key()
        if key == Qt.Key.Key_Return or key == Qt.Key.Key_Enter:
            if event.modifiers() & Qt.KeyboardModifier.ControlModifier:
                self._on_confirm()
            elif event.modifiers() & Qt.KeyboardModifier.ShiftModifier:
                self._text_edit.insertPlainText("\n")
            else:
                self._on_confirm()
        elif key == Qt.Key.Key_Escape:
            self._on_skip()
        else:
            super().keyPressEvent(event)
