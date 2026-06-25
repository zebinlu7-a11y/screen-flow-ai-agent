"""
截图遮罩窗口 — 先全屏截图，在静态图上画框（仿 QQ 截图）。

原理：
  1. 创建窗口前先截取全屏为一张静态图片
  2. 窗口铺满屏幕，把截图绘制为背景
  3. 用户在背景图上拖拽画框
  4. 确认后从原始截图中裁剪对应区域

这样不论屏幕分辨率/缩放怎么变，都能正确显示和截图。
"""
from typing import Optional, List
from PyQt6.QtCore import Qt, QRect, QPoint, pyqtSignal
from PyQt6.QtGui import (
    QPainter, QPen, QColor, QBrush, QFont, QPixmap,
)
from PyQt6.QtWidgets import QWidget, QApplication

from config import MASK_OPACITY, HANDLE_SIZE, HANDLE_HIT_RADIUS


class CaptureWindow(QWidget):
    """
    全屏截图遮罩 — 先截全屏为静态背景，在背景上画框。

    Enter → 发射所有已确认框的截图列表
    Esc → 取消关闭
    """
    captured = pyqtSignal(list)

    def __init__(self):
        super().__init__()
        self.setWindowFlags(
            Qt.WindowType.FramelessWindowHint |
            Qt.WindowType.WindowStaysOnTopHint |
            Qt.WindowType.Tool
        )
        self.setCursor(Qt.CursorShape.CrossCursor)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)

        self._state = 0          # 0=IDLE, 1=SELECTING, 2=ADJUSTING
        self._start = QPoint()
        self._end = QPoint()
        self._active_handle = -1
        self._drag_anchor = QPoint()
        self._drag_start_rect = QRect()

        # 已确认的选区
        self._confirmed: List[QRect] = []

        # 全屏截图（原始分辨率）
        self._screenshot: Optional[QPixmap] = None

    # ============================================================
    # Lifecycle
    # ============================================================

    def showFullScreen(self):
        """先截全屏，再显示窗口。"""
        screen = QApplication.primaryScreen()
        if screen:
            # 直接截取整个屏幕（原始分辨率）
            self._screenshot = screen.grabWindow(0)
            # 窗口覆盖整个屏幕
            self.setGeometry(screen.geometry())

        self._confirmed = []
        self._state = 0
        self._start = QPoint()
        self._end = QPoint()
        self._active_handle = -1
        self.show()
        self.activateWindow()
        self.raise_()

    # ============================================================
    # Paint
    # ============================================================

    def paintEvent(self, _):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        w, h = self.width(), self.height()
        full = QRect(0, 0, w, h)

        # Layer 1: 全屏截图背景（缩放适配窗口）
        if self._screenshot:
            p.drawPixmap(full, self._screenshot,
                         QRect(0, 0, self._screenshot.width(), self._screenshot.height()))

        # Layer 2: 暗色遮罩
        p.fillRect(full, QColor(0, 0, 0, int(255 * MASK_OPACITY)))

        # ---- 已确认框（绿色） ----
        for i, rect in enumerate(self._confirmed):
            if not rect.isEmpty() and self._screenshot:
                # 挖空：显示截图原色
                src_rect = self._to_source_rect(rect)
                p.drawPixmap(rect, self._screenshot, src_rect)
                # 绿色边框
                p.setPen(QPen(QColor(0, 200, 80), 2))
                p.setBrush(Qt.BrushStyle.NoBrush)
                
                p.drawRect(rect)
                # 编号
                label = str(i + 1)
                sz = 22
                lx, ly = rect.left() + 3, rect.top() + 3
                p.fillRect(QRect(lx - 1, ly - 1, sz, sz), QColor(0, 180, 60, 230))
                p.setPen(QColor(255, 255, 255))
                p.setFont(QFont("Microsoft YaHei", 11, QFont.Weight.Bold))
                p.drawText(QRect(lx, ly, sz - 2, sz - 2),
                            Qt.AlignmentFlag.AlignCenter, label)

        # ---- 当前编辑框（蓝色） ----
        r = self._normalized()
        if not r.isEmpty() and self._screenshot:
            src_rect = self._to_source_rect(r)
            p.drawPixmap(r, self._screenshot, src_rect)
            # 蓝框
            p.setPen(QPen(QColor(30, 144, 255), 2))
            p.setBrush(Qt.BrushStyle.NoBrush)
            p.drawRect(r)
            p.setPen(QPen(QColor(255, 255, 255, 80), 4))
            p.drawRect(r)
            # 尺寸
            txt = f"{r.width()} × {r.height()}"
            p.setPen(QColor(255, 255, 255))
            p.setFont(QFont("Microsoft YaHei", 10, QFont.Weight.Bold))
            ly = r.top() - 24
            if ly < 4:
                ly = r.bottom() + 4
            tr = QRect(r.left(), ly, r.width(), 20)
            p.fillRect(tr.adjusted(-6, -2, 6, 2), QColor(30, 144, 255, 200))
            p.drawText(tr, Qt.AlignmentFlag.AlignCenter, txt)
            # 锚点
            if self._state == 2:
                self._draw_handles(p, r)

        # ---- 提示 ----
        count = len(self._confirmed)
        bar_y = h - 40
        p.fillRect(QRect(0, bar_y, w, 40), QColor(0, 0, 0, 180))
        if count == 0 and self._state == 0:
            hint = "拖拽鼠标框选  |  Enter 放入对话框  |  Esc 取消"
        else:
            parts = []
            if count > 0:
                parts.append(f"已选 {count} 个区域")
            if self._state == 0:
                parts.append("继续拖拽画框")
            parts.append("Ctrl+Z 撤销")
            parts.append("Enter 全部放入对话框")
            parts.append("Esc 取消")
            hint = "  |  ".join(parts)
        p.setFont(QFont("Microsoft YaHei", 11))
        p.setPen(QColor(200, 220, 255))
        p.drawText(QRect(0, bar_y, w, 40), Qt.AlignmentFlag.AlignCenter, hint)
        p.end()

    def _draw_handles(self, p, r):
        s = HANDLE_SIZE
        pts = {
            0: r.topLeft(), 1: QPoint(r.center().x(), r.top()),
            2: r.topRight(), 3: QPoint(r.right(), r.center().y()),
            4: r.bottomRight(), 5: QPoint(r.center().x(), r.bottom()),
            6: r.bottomLeft(), 7: QPoint(r.left(), r.center().y()),
        }
        p.setPen(QPen(QColor(255, 255, 255), 2))
        p.setBrush(QBrush(QColor(30, 144, 255)))
        for pt in pts.values():
            p.drawRect(QRect(pt.x() - s // 2, pt.y() - s // 2, s, s))

    # ---- 坐标映射 ----

    def _to_source_rect(self, widget_rect: QRect) -> QRect:
        """将 widget 坐标映射到原始截图坐标。"""
        if not self._screenshot:
            return widget_rect
        sx = self._screenshot.width() / max(1, self.width())
        sy = self._screenshot.height() / max(1, self.height())
        return QRect(
            int(widget_rect.x() * sx),
            int(widget_rect.y() * sy),
            int(widget_rect.width() * sx),
            int(widget_rect.height() * sy),
        )

    def _normalized(self):
        return QRect(self._start, self._end).normalized()

    def _handle_at(self, pos):
        r = self._normalized()
        if r.isEmpty():
            return -1
        pts = {
            0: r.topLeft(), 1: QPoint(r.center().x(), r.top()),
            2: r.topRight(), 3: QPoint(r.right(), r.center().y()),
            4: r.bottomRight(), 5: QPoint(r.center().x(), r.bottom()),
            6: r.bottomLeft(), 7: QPoint(r.left(), r.center().y()),
        }
        for h_idx, pt in pts.items():
            if abs(pos.x() - pt.x()) <= HANDLE_HIT_RADIUS and abs(pos.y() - pt.y()) <= HANDLE_HIT_RADIUS:
                return h_idx
        return -1

    _cursors = {
        0: Qt.CursorShape.SizeFDiagCursor, 4: Qt.CursorShape.SizeFDiagCursor,
        2: Qt.CursorShape.SizeBDiagCursor, 6: Qt.CursorShape.SizeBDiagCursor,
        1: Qt.CursorShape.SizeVerCursor, 5: Qt.CursorShape.SizeVerCursor,
        3: Qt.CursorShape.SizeHorCursor, 7: Qt.CursorShape.SizeHorCursor,
    }

    # ============================================================
    # Mouse
    # ============================================================

    def mousePressEvent(self, e):
        if e.button() != Qt.MouseButton.LeftButton:
            return
        pos = e.position().toPoint()

        if self._state == 0:
            self._state = 1
            self._start = pos
            self._end = pos
            self.update()
        elif self._state == 2:
            h = self._handle_at(pos)
            if h >= 0:
                self._active_handle = h
                self._drag_anchor = pos
                self._drag_start_rect = QRect(self._normalized())
                self.setCursor(self._cursors.get(h, Qt.CursorShape.CrossCursor))

    def mouseMoveEvent(self, e):
        pos = e.position().toPoint()
        if self._state == 1:
            self._end = pos
            self.update()
        elif self._state == 2 and self._active_handle >= 0:
            dx = pos.x() - self._drag_anchor.x()
            dy = pos.y() - self._drag_anchor.y()
            nr = self._resize(self._drag_start_rect, dx, dy)
            self._start = nr.topLeft()
            self._end = nr.bottomRight()
            self.update()

    def mouseReleaseEvent(self, e):
        if e.button() != Qt.MouseButton.LeftButton:
            return
        if self._state == 1:
            r = self._normalized()
            if r.width() > 10 and r.height() > 10:
                self._confirmed.append(r)
            self._state = 0
            self._start = QPoint()
            self._end = QPoint()
            self.setCursor(Qt.CursorShape.CrossCursor)
            self.update()
        elif self._state == 2:
            self._active_handle = -1
            self._state = 0
            self.setCursor(Qt.CursorShape.CrossCursor)
            self.update()

    def _resize(self, rect, dx, dy):
        r = QRect(rect)
        r.setRight(r.right() + dx)
        r.setBottom(r.bottom() + dy)
        if r.width() < 20:
            r.setRight(r.left() + 20)
        if r.height() < 20:
            r.setBottom(r.top() + 20)
        return r

    # ============================================================
    # Keyboard
    # ============================================================

    def keyPressEvent(self, e):
        if e.key() == Qt.Key.Key_Z and (e.modifiers() & Qt.KeyboardModifier.ControlModifier):
            if self._confirmed:
                self._confirmed.pop()
                self.update()
            return

        if e.key() in (Qt.Key.Key_Return, Qt.Key.Key_Enter):
            self._emit_all()
            return

        if e.key() == Qt.Key.Key_Escape:
            self.close()

    def _emit_all(self):
        """从原始截图中裁切所有已确认区域，发射列表。"""
        if not self._confirmed or not self._screenshot:
            self.close()
            return

        results = []
        for rect in self._confirmed:
            src_rect = self._to_source_rect(rect)
            # 从原始分辨率截图中裁剪
            cropped = self._screenshot.copy(
                src_rect.x(), src_rect.y(),
                src_rect.width(), src_rect.height()
            )
            results.append((cropped.toImage(), rect))

        self.captured.emit(results)
        self.close()
