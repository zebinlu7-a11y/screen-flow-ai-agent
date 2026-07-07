# -*- coding: utf-8 -*-
"""
侧边栏 — 对话历史列表 + 新建/切换/删除。
"""
from datetime import datetime
from PyQt6.QtCore import Qt, pyqtSignal, QEvent
from PyQt6.QtGui import QFont, QColor, QPalette
from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QPushButton, QLabel,
    QScrollArea, QLineEdit, QFrame,
)


class SidebarWidget(QWidget):
    """对话历史侧边栏。"""

    conversation_selected = pyqtSignal(str)
    new_conversation_clicked = pyqtSignal()
    refresh_requested = pyqtSignal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setFixedWidth(220)
        self._conversations = []
        self._active_id = ""
        self._user_id = ""  # 由外部设置
        self._setup_ui()

    def set_user_id(self, user_id: str):
        self._user_id = user_id

    def _setup_ui(self):
        self.setStyleSheet("""
            SidebarWidget { background: #1e1e28; border-right: 1px solid #3a3a45; }
        """)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(8, 10, 8, 10)
        layout.setSpacing(6)

        # 标题
        title = QLabel("📁 对话历史")
        title.setFont(QFont("Microsoft YaHei", 12, QFont.Weight.Bold))
        title.setStyleSheet("color: #aaccff;")
        layout.addWidget(title)

        # 搜索/过滤
        self._search = QLineEdit()
        self._search.setPlaceholderText("搜索对话...")
        self._search.setStyleSheet("""
            QLineEdit {
                background: #2a2a35; color: #ccc; border: 1px solid #4a4a55;
                border-radius: 4px; padding: 4px 8px; font-size: 11px;
            }
            QLineEdit:focus { border-color: #4a8af4; }
        """)
        self._search.textChanged.connect(self._on_search)
        layout.addWidget(self._search)

        # 新建按钮
        new_btn = QPushButton("➕ 新建对话")
        new_btn.setStyleSheet("""
            QPushButton {
                background: #2b5db8; color: white; border: none;
                border-radius: 4px; padding: 6px; font-size: 12px; font-weight: bold;
            }
            QPushButton:hover { background: #3a6fd8; }
        """)
        new_btn.clicked.connect(self.new_conversation_clicked.emit)
        layout.addWidget(new_btn)

        # 分隔线
        sep = QFrame()
        sep.setFrameShape(QFrame.Shape.HLine)
        sep.setStyleSheet("color: #3a3a45;")
        layout.addWidget(sep)

        # 对话列表
        self._list_scroll = QScrollArea()
        self._list_scroll.setWidgetResizable(True)
        self._list_scroll.setStyleSheet("QScrollArea { background: transparent; border: none; }")
        self._list_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)

        self._list_widget = QWidget()
        self._list_layout = QVBoxLayout(self._list_widget)
        self._list_layout.setContentsMargins(0, 0, 0, 0)
        self._list_layout.setSpacing(2)
        self._list_layout.addStretch()
        self._list_scroll.setWidget(self._list_widget)
        layout.addWidget(self._list_scroll, stretch=1)

    # ============================================================
    # Public API
    # ============================================================

    def set_conversations(self, convs: list, active_id: str = ""):
        """刷新对话列表。convs = [{"id":..., "title":..., "created":..., "msg_count":...}]"""
        self._conversations = convs
        self._active_id = active_id
        self._rebuild_list(convs)

    def _rebuild_list(self, convs: list):
        """重建列表 UI。"""
        # 清除旧项（保留 stretch）
        while self._list_layout.count() > 1:
            item = self._list_layout.takeAt(0)
            if item.widget():
                item.widget().deleteLater()

        # 分组
        today = datetime.now().strftime("%Y-%m-%d")
        groups = {"今天": [], "昨天": [], "更早": []}

        for c in convs:
            created = c.get("created", "")[:10]
            if created == today:
                groups["今天"].append(c)
            else:
                groups["更早"].append(c)

        for group_name in ["今天", "更早"]:
            items = groups[group_name]
            if not items:
                continue

            label = QLabel(f"  {group_name}")
            label.setStyleSheet("color: #667788; font-size: 10px; padding: 4px 0;")
            self._list_layout.insertWidget(self._list_layout.count() - 1, label)

            for c in items:
                btn = self._make_conv_item(c)
                self._list_layout.insertWidget(self._list_layout.count() - 1, btn)

    def _make_conv_item(self, conv: dict) -> QWidget:
        """创建单个对话条目（标题 + 删除按钮）。"""
        cid = conv.get("id", "")
        title = conv.get("title", "未命名")[:16]
        created = conv.get("created", "")[11:16]
        count = conv.get("msg_count", 0)
        is_active = cid == self._active_id

        container = QWidget()
        container.setStyleSheet("background: transparent;")
        hbox = QHBoxLayout(container)
        hbox.setContentsMargins(0, 0, 0, 0)
        hbox.setSpacing(2)

        # 主按钮（点击切换）
        btn = QPushButton()
        bg = "#2a4a6a" if is_active else "#2a2a35"
        label = f"{created}  {title}"
        if count > 0:
            label += f"  ({count})"
        btn.setText(label)
        btn.setStyleSheet(f"""
            QPushButton {{
                background: {bg}; color: #ccc; border: none;
                border-radius: 4px; padding: 6px 6px; font-size: 11px;
                text-align: left;
            }}
            QPushButton:hover {{ background: #3a5a7a; }}
        """)
        btn.setCursor(Qt.CursorShape.PointingHandCursor)
        btn.clicked.connect(lambda: self.conversation_selected.emit(cid))
        btn.setToolTip("双击重命名")
        btn.installEventFilter(self)
        btn.setProperty("conv_id", cid)
        hbox.addWidget(btn, stretch=1)

        # 删除按钮
        del_btn = QPushButton("×")
        del_btn.setFixedSize(20, 20)
        del_btn.setStyleSheet("""
            QPushButton {
                background: transparent; color: #888; border: none;
                border-radius: 10px; font-size: 13px; font-weight: bold;
            }
            QPushButton:hover { background: #c0392b; color: white; }
        """)
        del_btn.setToolTip("删除此对话")
        del_btn.clicked.connect(lambda checked, c=cid, t=title: self._confirm_delete(c, t))
        hbox.addWidget(del_btn)

        return container

    def eventFilter(self, obj, event):
        """双击重命名。"""
        if event.type() == QEvent.Type.MouseButtonDblClick:
            conv_id = obj.property("conv_id")
            if conv_id:
                self._rename_dialog(conv_id)
                return True
        return super().eventFilter(obj, event)

    def _confirm_delete(self, conv_id: str, title: str):
        """弹出确认删除对话框。"""
        from PyQt6.QtWidgets import QMessageBox
        reply = QMessageBox.question(
            self, "删除对话",
            f"确定要删除「{title}」吗？\n此操作不可恢复。",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.Cancel,
            QMessageBox.StandardButton.Cancel,
        )
        if reply == QMessageBox.StandardButton.Yes:
            from utils.user_manager import delete_conversation
            delete_conversation(self._user_id, conv_id)
            self.refresh_requested.emit()

    def _rename_dialog(self, conv_id: str):
        """弹出重命名输入框。"""
        from PyQt6.QtWidgets import QInputDialog
        from utils.user_manager import load_conversation, save_conversation
        conv = load_conversation(self._user_id, conv_id)
        if not conv:
            return
        old_title = conv.get("title", "")
        new_title, ok = QInputDialog.getText(
            self, "重命名", "对话名称:",
            text=old_title,
        )
        if ok and new_title.strip() and new_title.strip() != old_title:
            conv["title"] = new_title.strip()
            save_conversation(self._user_id, conv)
            self.refresh_requested.emit()

    def _on_context_menu(self, pos, conv_id: str):
        from PyQt6.QtWidgets import QMenu
        menu = QMenu(self)
        menu.setStyleSheet("QMenu { background: #2a2a32; color: #e0e0e0; border: 1px solid #444; }")
        delete_action = menu.addAction("🗑 删除")
        action = menu.exec(self.mapToGlobal(pos))
        if action == delete_action:
            from utils.user_manager import delete_conversation
            delete_conversation("", conv_id)
            # 通知刷新
            self.new_conversation_clicked.emit()

    def _on_search(self, text: str):
        """搜索过滤对话列表。"""
        if not text.strip():
            self._rebuild_list(self._conversations)
        else:
            filtered = [c for c in self._conversations if text.lower() in c.get("title", "").lower()]
            self._rebuild_list(filtered)
