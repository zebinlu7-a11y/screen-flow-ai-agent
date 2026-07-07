# -*- coding: utf-8 -*-
"""
设置弹窗 — API Key + 代理地址配置。
"""
from PyQt6.QtCore import Qt
from PyQt6.QtGui import QFont, QColor, QPalette
from PyQt6.QtWidgets import (
    QDialog, QVBoxLayout, QLineEdit, QHBoxLayout,
    QPushButton, QLabel,
)

from utils.api_key_manager import (
    get_api_key, set_api_key, get_proxy, set_proxy,
    get_tencent_secret_id, set_tencent_secret_id,
    get_tencent_secret_key, set_tencent_secret_key,
)


class ApiKeyDialog(QDialog):
    """
    API Key + 代理设置弹窗。

    用法:
        dlg = ApiKeyDialog()
        if dlg.exec() == QDialog.DialogCode.Accepted:
            print("设置已保存")
    """

    def __init__(self, parent=None):
        super().__init__(parent)
        self._drag_pos = None  # 拖动起始位置
        self._setup_ui()

    def _setup_ui(self):
        self.setWindowTitle("Ai_Flow - 设置")
        self.setFixedSize(480, 460)

        self.setWindowFlags(
            Qt.WindowType.FramelessWindowHint |
            Qt.WindowType.WindowStaysOnTopHint |
            Qt.WindowType.Tool
        )
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, False)

        # 深色背景
        palette = self.palette()
        palette.setColor(QPalette.ColorRole.Window, QColor(35, 35, 42, 245))
        self.setPalette(palette)
        self.setAutoFillBackground(True)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(20, 16, 20, 14)
        layout.setSpacing(8)

        # 标题
        title = QLabel("⚙️ 设置")
        title.setFont(QFont("Microsoft YaHei", 13))
        title.setStyleSheet("color: #aaccff; font-weight: bold;")
        layout.addWidget(title)

        # ---- API Key ----
        key_label = QLabel("🔑 火山引擎 API Key")
        key_label.setFont(QFont("Microsoft YaHei", 10))
        key_label.setStyleSheet("color: #aabbcc; margin-top: 4px;")
        layout.addWidget(key_label)

        current_key = get_api_key()
        self._key_input = QLineEdit()
        self._key_input.setPlaceholderText("粘贴 API Key（例如：c774d2b6-xxxx-xxxx-xxxx-xxxxxxxxxxxx）")
        self._key_input.setEchoMode(QLineEdit.EchoMode.Password)
        self._key_input.setFont(QFont("Consolas", 10))
        self._key_input.setStyleSheet("""
            QLineEdit {
                background-color: #1e1e28; color: #e0e0e0;
                border: 1px solid #555; border-radius: 5px; padding: 7px 10px;
            }
            QLineEdit:focus { border-color: #4a8af4; }
        """)
        if current_key:
            self._key_input.setText(current_key)
        layout.addWidget(self._key_input)

        # ---- 代理地址 ----
        proxy_label = QLabel("🌐 代理地址（访问 API 需要）")
        proxy_label.setFont(QFont("Microsoft YaHei", 10))
        proxy_label.setStyleSheet("color: #aabbcc; margin-top: 6px;")
        layout.addWidget(proxy_label)

        current_proxy = get_proxy()
        self._proxy_input = QLineEdit()
        self._proxy_input.setPlaceholderText("例如：http://127.0.0.1:7890，不需要则留空")
        self._proxy_input.setFont(QFont("Consolas", 10))
        self._proxy_input.setStyleSheet("""
            QLineEdit {
                background-color: #1e1e28; color: #e0e0e0;
                border: 1px solid #555; border-radius: 5px; padding: 7px 10px;
            }
            QLineEdit:focus { border-color: #4a8af4; }
        """)
        if current_proxy:
            self._proxy_input.setText(current_proxy)
        layout.addWidget(self._proxy_input)

        # ---- 腾讯云 OCR 凭证 ----
        ocr_label = QLabel("📷 腾讯云 OCR 凭证（Ctrl+R 文字识别，免费 1000 次/月）")
        ocr_label.setFont(QFont("Microsoft YaHei", 10))
        ocr_label.setStyleSheet("color: #aabbcc; margin-top: 6px;")
        layout.addWidget(ocr_label)

        sid_label = QLabel("SecretId")
        sid_label.setFont(QFont("Microsoft YaHei", 9))
        sid_label.setStyleSheet("color: #8899aa;")
        layout.addWidget(sid_label)

        current_sid = get_tencent_secret_id()
        self._sid_input = QLineEdit()
        self._sid_input.setPlaceholderText("腾讯云 SecretId")
        self._sid_input.setEchoMode(QLineEdit.EchoMode.Password)
        self._sid_input.setFont(QFont("Consolas", 10))
        self._sid_input.setStyleSheet("""
            QLineEdit {
                background-color: #1e1e28; color: #e0e0e0;
                border: 1px solid #555; border-radius: 5px; padding: 6px 10px;
            }
            QLineEdit:focus { border-color: #4a8af4; }
        """)
        if current_sid:
            self._sid_input.setText(current_sid)
        layout.addWidget(self._sid_input)

        skey_label = QLabel("SecretKey")
        skey_label.setFont(QFont("Microsoft YaHei", 9))
        skey_label.setStyleSheet("color: #8899aa;")
        layout.addWidget(skey_label)

        current_skey = get_tencent_secret_key()
        self._skey_input = QLineEdit()
        self._skey_input.setPlaceholderText("腾讯云 SecretKey")
        self._skey_input.setEchoMode(QLineEdit.EchoMode.Password)
        self._skey_input.setFont(QFont("Consolas", 10))
        self._skey_input.setStyleSheet("""
            QLineEdit {
                background-color: #1e1e28; color: #e0e0e0;
                border: 1px solid #555; border-radius: 5px; padding: 6px 10px;
            }
            QLineEdit:focus { border-color: #4a8af4; }
        """)
        if current_skey:
            self._skey_input.setText(current_skey)
        layout.addWidget(self._skey_input)

        desc = QLabel("设置保存在程序目录下，下次启动自动加载。")
        desc.setFont(QFont("Microsoft YaHei", 9))
        desc.setStyleSheet("color: #667788;")
        layout.addWidget(desc)

        layout.addStretch()

        # 按钮行
        btn_layout = QHBoxLayout()
        btn_layout.setSpacing(10)

        skip_btn = QPushButton("取消")
        skip_btn.setStyleSheet("""
            QPushButton {
                background: #3a3a40; color: #aaa; border: 1px solid #555;
                border-radius: 5px; padding: 7px 20px; font-size: 12px;
            }
            QPushButton:hover { background: #4a4a50; color: #ddd; }
        """)
        skip_btn.clicked.connect(self.reject)
        btn_layout.addWidget(skip_btn)

        btn_layout.addStretch()

        save_btn = QPushButton("保存")
        save_btn.setStyleSheet("""
            QPushButton {
                background: #2b5db8; color: white; border: none;
                border-radius: 5px; padding: 7px 22px; font-size: 12px; font-weight: bold;
            }
            QPushButton:hover { background: #3a6fd8; }
        """)
        save_btn.clicked.connect(self._on_save)
        btn_layout.addWidget(save_btn)

        layout.addLayout(btn_layout)

        # 居中
        self._center_on_screen()

    def _center_on_screen(self):
        from PyQt6.QtWidgets import QApplication
        screen = QApplication.primaryScreen()
        if screen:
            geo = screen.availableGeometry()
            x = (geo.width() - self.width()) // 2 + geo.x()
            y = (geo.height() - self.height()) // 3 + geo.y()
            self.move(x, y)

    def _on_save(self):
        key = self._key_input.text().strip()
        proxy = self._proxy_input.text().strip()
        sid = self._sid_input.text().strip()
        skey = self._skey_input.text().strip()

        if key:
            set_api_key(key)
        if proxy:
            set_proxy(proxy)
        elif proxy == "" and get_proxy():
            set_proxy("")
        if sid:
            set_tencent_secret_id(sid)
        if skey:
            set_tencent_secret_key(skey)
        self.accept()

    # ---- 拖动窗口 ----

    def mousePressEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            self._drag_pos = event.globalPosition().toPoint()
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event):
        if self._drag_pos is not None:
            delta = event.globalPosition().toPoint() - self._drag_pos
            self.move(self.pos() + delta)
            self._drag_pos = event.globalPosition().toPoint()
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event):
        self._drag_pos = None
        super().mouseReleaseEvent(event)

    def keyPressEvent(self, event):
        if event.key() == Qt.Key.Key_Return or event.key() == Qt.Key.Key_Enter:
            self._on_save()
        elif event.key() == Qt.Key.Key_Escape:
            self.reject()
        else:
            super().keyPressEvent(event)
