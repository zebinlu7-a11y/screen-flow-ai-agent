"""
GUI Agent 命令面板 — 输入自动化任务指令。
"""
from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtGui import QFont, QColor
from PyQt6.QtWidgets import (
    QDialog, QVBoxLayout, QTextEdit, QHBoxLayout,
    QPushButton, QLabel, QProgressBar,
)


class GuiAgentDialog(QDialog):
    """GUI Agent 任务输入 + 进度显示。"""

    task_submitted = pyqtSignal(str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self._setup_ui()

    def _setup_ui(self):
        self.setWindowTitle("GUI Agent — 自动化操作")
        self.setFixedSize(500, 300)

        self.setWindowFlags(
            Qt.WindowType.FramelessWindowHint |
            Qt.WindowType.WindowStaysOnTopHint |
            Qt.WindowType.Tool
        )

        self.setStyleSheet("background: #282832; border-radius: 10px;")

        layout = QVBoxLayout(self)
        layout.setContentsMargins(20, 16, 20, 16)
        layout.setSpacing(10)

        title = QLabel("🤖 GUI Agent — 告诉我你要做什么")
        title.setFont(QFont("Microsoft YaHei", 13, QFont.Weight.Bold))
        title.setStyleSheet("color: #aaccff;")
        layout.addWidget(title)

        hint = QLabel("例如: 打开百度搜索Python教程、打开记事本写一段代码、打开文件夹截图...")
        hint.setFont(QFont("Microsoft YaHei", 10))
        hint.setStyleSheet("color: #8899aa;")
        hint.setWordWrap(True)
        layout.addWidget(hint)

        # 输入框
        self._input = QTextEdit()
        self._input.setPlaceholderText("输入你的任务...")
        self._input.setFont(QFont("Microsoft YaHei", 12))
        self._input.setMaximumHeight(60)
        self._input.setStyleSheet("""
            QTextEdit {
                background: #1e1e28; color: #e0e0e0; border: 1px solid #555;
                border-radius: 6px; padding: 8px;
            }
            QTextEdit:focus { border-color: #4a8af4; }
        """)
        layout.addWidget(self._input)

        # 进度条
        self._progress = QProgressBar()
        self._progress.setStyleSheet("""
            QProgressBar {
                background: #2a2a35; border: 1px solid #555; border-radius: 4px;
                height: 20px; text-align: center;
            }
            QProgressBar::chunk { background: #4a8af4; border-radius: 3px; }
        """)
        self._progress.hide()
        layout.addWidget(self._progress)

        # 状态
        self._status = QLabel("")
        self._status.setFont(QFont("Microsoft YaHei", 10))
        self._status.setStyleSheet("color: #88aacc;")
        self._status.setWordWrap(True)
        layout.addWidget(self._status)

        # 按钮
        btn_layout = QHBoxLayout()
        btn_layout.setSpacing(10)

        cancel_btn = QPushButton("取消 (Esc)")
        cancel_btn.setStyleSheet("""
            QPushButton { background: #3a3a40; color: #aaa; border: 1px solid #555;
                border-radius: 5px; padding: 7px 20px; }
            QPushButton:hover { background: #4a4a50; color: #ddd; }
        """)
        cancel_btn.clicked.connect(self.reject)
        btn_layout.addWidget(cancel_btn)

        btn_layout.addStretch()

        self._submit_btn = QPushButton("执行 (Enter)")
        self._submit_btn.setStyleSheet("""
            QPushButton { background: #2b5db8; color: white; border: none;
                border-radius: 5px; padding: 7px 20px; font-weight: bold; }
            QPushButton:hover { background: #3a6fd8; }
        """)
        self._submit_btn.clicked.connect(self._on_submit)
        btn_layout.addWidget(self._submit_btn)

        layout.addLayout(btn_layout)
        self._input.setFocus()
        self._center()

    def _center(self):
        from PyQt6.QtWidgets import QApplication
        screen = QApplication.primaryScreen()
        if screen:
            geo = screen.availableGeometry()
            x = (geo.width() - self.width()) // 2 + geo.x()
            y = (geo.height() - self.height()) // 3 + geo.y()
            self.move(x, y)

    def _on_submit(self):
        text = self._input.toPlainText().strip()
        if not text:
            return
        self._submit_btn.setEnabled(False)
        self._progress.setVisible(True)
        self._progress.setMaximum(0)  # 不确定进度
        self._status.setText("正在分析任务...")
        self.task_submitted.emit(text)

    def set_progress(self, msg: str):
        """更新进度文字。"""
        self._status.setText(msg)

    def set_done(self, success: bool, msg: str):
        """执行完成。"""
        self._progress.setMaximum(100)
        self._progress.setValue(100 if success else 0)
        self._status.setText(msg)
        self._submit_btn.setText("完成")
        self._submit_btn.setEnabled(True)
        self._submit_btn.clicked.disconnect()
        self._submit_btn.clicked.connect(self.accept)

    def keyPressEvent(self, event):
        if event.key() == Qt.Key.Key_Return and not (
            event.modifiers() & Qt.KeyboardModifier.ShiftModifier
        ):
            self._on_submit()
        elif event.key() == Qt.Key.Key_Escape:
            self.reject()
        else:
            super().keyPressEvent(event)
