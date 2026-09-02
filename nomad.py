import sys
import os
import shutil
import threading
import time
import math
import traceback

def exception_hook(exctype, value, tb):
    with open("error_log.txt", "a") as f:
        f.write("".join(traceback.format_exception(exctype, value, tb)))
    sys.__excepthook__(exctype, value, tb)
    sys.exit(1)

sys.excepthook = exception_hook

from PyQt5.QtWidgets import (QApplication, QWidget, QLabel, QVBoxLayout, 
                             QPushButton, QHBoxLayout, QSystemTrayIcon, 
                             QMenu, QAction, QSlider, QCheckBox, QFrame, 
                             QProgressBar, QGraphicsDropShadowEffect, QSizePolicy)
from PyQt5.QtCore import Qt, QPoint, QThread, pyqtSignal, QTimer, QSize
from PyQt5.QtGui import QFont, QPalette, QColor, QIcon, QPixmap, QPainter, QBrush

DESTINATION_FOLDER = r"E:\nomad"

class CopyThread(QThread):
    progress = pyqtSignal(int)
    speed = pyqtSignal(float) # bytes per second
    finished = pyqtSignal()
    
    def __init__(self, urls, destination):
        super().__init__()
        self.urls = urls
        self.destination = destination
        self.is_running = True
        
    def run(self):
        # Calculate total size
        total_size = 0
        files_to_copy = []
        for url in self.urls:
            if url.isLocalFile():
                path = url.toLocalFile()
                if os.path.isdir(path):
                    for root, dirs, files in os.walk(path):
                        for f in files:
                            fp = os.path.join(root, f)
                            total_size += os.path.getsize(fp)
                            rel_dir = os.path.relpath(root, path)
                            files_to_copy.append((fp, os.path.join(self.destination, os.path.basename(path), rel_dir, f)))
                else:
                    total_size += os.path.getsize(path)
                    files_to_copy.append((path, os.path.join(self.destination, os.path.basename(path))))

        if total_size == 0:
            self.progress.emit(100)
            self.finished.emit()
            return

        copied_size = 0
        chunk_size = 1024 * 1024 * 5 # 5 MB chunks
        start_time = time.time()
        last_time = start_time
        last_copied = 0

        for src, dst in files_to_copy:
            if not self.is_running:
                break
                
            os.makedirs(os.path.dirname(dst), exist_ok=True)
            if os.path.exists(dst):
                dst = self.get_unique_path(dst)
                
            with open(src, 'rb') as fsrc, open(dst, 'wb') as fdst:
                while True:
                    if not self.is_running:
                        break
                    buf = fsrc.read(chunk_size)
                    if not buf:
                        break
                    fdst.write(buf)
                    copied_size += len(buf)
                    
                    # Update speed and progress
                    current_time = time.time()
                    time_diff = current_time - last_time
                    if time_diff > 0.1: # update every 100ms
                        speed_bps = (copied_size - last_copied) / time_diff
                        self.speed.emit(speed_bps)
                        last_time = current_time
                        last_copied = copied_size
                        
                    self.progress.emit(int((copied_size / total_size) * 100))

        self.progress.emit(100)
        self.speed.emit(0.0)
        self.finished.emit()

    def get_unique_path(self, path):
        base, ext = os.path.splitext(path)
        counter = 1
        while os.path.exists(path):
            path = f"{base}_{counter}{ext}"
            counter += 1
        return path

class NomadWidget(QWidget):
    def __init__(self):
        super().__init__()
        self.is_locked = False
        self.is_always_on_top = True
        self.copy_thread = None
        self.current_opacity = 1.0
        
        # Aura animation properties
        self.aura_state = 'idle' # 'idle' or 'copying'
        self.aura_timer = QTimer(self)
        self.aura_timer.timeout.connect(self.update_aura)
        self.aura_time = 0.0
        self.base_beat_speed = 0.05
        self.current_beat_speed = 0.05
        self.copy_speed_factor = 0.0
        
        self.initUI()
        self._setup_tray()
        
        # Start breathing
        self.aura_timer.start(30) # ~33 fps
        
    def initUI(self):
        self.setWindowFlags(Qt.WindowStaysOnTopHint | Qt.FramelessWindowHint | Qt.Tool)
        self.setAttribute(Qt.WA_TranslucentBackground)
        self.setAcceptDrops(True)
        self.setMouseTracking(True)
        self.setMinimumSize(200, 150)
        
        self.resize(250, 150)
        
        # Main layout
        layout = QVBoxLayout(self)
        layout.setContentsMargins(15, 15, 15, 15)
        
        # Background frame - Glassmorphism with lower opacity
        self.bg_frame = QWidget()
        
        # Add DropShadow for Aura
        self.aura_effect = QGraphicsDropShadowEffect(self)
        self.aura_effect.setBlurRadius(20)
        self.aura_effect.setOffset(0, 0)
        self.aura_effect.setColor(QColor(0, 255, 255, 150))
        self.bg_frame.setGraphicsEffect(self.aura_effect)
        
        layout.addWidget(self.bg_frame)
        
        bg_layout = QVBoxLayout(self.bg_frame)
        bg_layout.setContentsMargins(10, 10, 10, 10)
        
        # Drop area replacing text with progress bar
        self.drop_area = QProgressBar()
        self.drop_area.setTextVisible(False)
        self.drop_area.setRange(0, 100)
        self.drop_area.setValue(0)
        self.drop_area.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        bg_layout.addWidget(self.drop_area)
        
        # Bottom layout for icons and size grip
        bottom_layout = QHBoxLayout()
        bottom_layout.setContentsMargins(0, 5, 0, 0)
        
        self.settings_btn = QPushButton("⚙")
        self.settings_btn.setFixedSize(20, 20)
        self.settings_btn.clicked.connect(self.toggle_settings)
        
        self.close_btn = QPushButton("X")
        self.close_btn.setFixedSize(20, 20)
        self.close_btn.clicked.connect(self.close)
        
        bottom_layout.addWidget(self.settings_btn)
        bottom_layout.addWidget(self.close_btn)
        bottom_layout.addStretch()
        
        # Adding a visual indicator for resizing in the bottom right corner
        self.resize_indicator = QLabel("↘")
        self.resize_indicator.setStyleSheet("color: rgba(255, 255, 255, 100); background: transparent; font-size: 14px;")
        self.resize_indicator.setFixedSize(20, 20)
        self.resize_indicator.setAlignment(Qt.AlignRight | Qt.AlignBottom)
        bottom_layout.addWidget(self.resize_indicator, 0, Qt.AlignBottom | Qt.AlignRight)
        
        bg_layout.addLayout(bottom_layout)
        
        # Apply initial styles
        self.update_styles()
        
        # Settings frame (floating above or expanding)
        self.settings_frame = QFrame(self)
        self.settings_frame.setVisible(False)
        self.settings_frame.setStyleSheet("""
            QFrame {
                background-color: transparent;
                color: white;
            }
        """)
        settings_layout = QVBoxLayout(self.settings_frame)
        
        # Add a button to close settings
        close_settings_layout = QHBoxLayout()
        self.close_settings_btn = QPushButton("Done")
        self.close_settings_btn.setStyleSheet("""
            QPushButton {
                background-color: rgba(255, 255, 255, 30);
                color: white;
                border-radius: 5px;
                padding: 2px 10px;
            }
            QPushButton:hover {
                background-color: rgba(255, 255, 255, 60);
            }
        """)
        self.close_settings_btn.clicked.connect(self.toggle_settings)
        close_settings_layout.addWidget(self.close_settings_btn)
        close_settings_layout.addStretch()
        settings_layout.addLayout(close_settings_layout)
        
        # Opacity slider
        op_layout = QHBoxLayout()
        op_label = QLabel("Opacity:")
        op_label.setStyleSheet("background: transparent;")
        op_layout.addWidget(op_label)
        self.opacity_slider = QSlider(Qt.Horizontal)
        self.opacity_slider.setRange(0, 100)
        self.opacity_slider.setValue(100)
        self.opacity_slider.valueChanged.connect(self.change_opacity)
        self.opacity_slider.setStyleSheet("background: transparent;")
        op_layout.addWidget(self.opacity_slider)
        settings_layout.addLayout(op_layout)
        
        # Checkboxes
        cb_layout = QHBoxLayout()
        self.lock_cb = QCheckBox("Lock")
        self.lock_cb.setChecked(self.is_locked)
        self.lock_cb.toggled.connect(self.set_lock)
        self.lock_cb.setStyleSheet("background: transparent;")
        
        self.top_cb = QCheckBox("Top")
        self.top_cb.setChecked(self.is_always_on_top)
        self.top_cb.toggled.connect(self.set_always_on_top)
        self.top_cb.setStyleSheet("background: transparent;")
        
        cb_layout.addWidget(self.lock_cb)
        cb_layout.addWidget(self.top_cb)
        settings_layout.addLayout(cb_layout)
        
        # Variables for dragging the window
        self.oldPos = self.pos()

    def update_styles(self):
        op = self.current_opacity
        bg_alpha = int(120 * op)
        border_alpha = int(30 * op)
        self.bg_frame.setStyleSheet(f"""
            QWidget {{
                background-color: rgba(30, 30, 30, {bg_alpha});
                border-radius: 15px;
                border: 1px solid rgba(255, 255, 255, {border_alpha});
            }}
        """)
        
        self.drop_area.setStyleSheet(self._get_drop_area_style(self.aura_state == 'copying'))
        
        btn_bg_alpha = int(30 * op)
        btn_hover_alpha = int(60 * op)
        text_alpha = int(255 * op)
        self.settings_btn.setStyleSheet(f"""
            QPushButton {{
                background-color: rgba(255, 255, 255, {btn_bg_alpha});
                color: rgba(255, 255, 255, {text_alpha});
                border-radius: 10px;
                border: none;
            }}
            QPushButton:hover {{
                background-color: rgba(255, 255, 255, {btn_hover_alpha});
            }}
        """)
        
        close_bg_alpha = int(150 * op)
        close_hover_alpha = int(200 * op)
        self.close_btn.setStyleSheet(f"""
            QPushButton {{
                background-color: rgba(231, 76, 60, {close_bg_alpha});
                color: rgba(255, 255, 255, {text_alpha});
                border-radius: 10px;
                border: none;
            }}
            QPushButton:hover {{
                background-color: rgba(231, 76, 60, {close_hover_alpha});
            }}
        """)

    def _get_drop_area_style(self, active=False):
        op = self.current_opacity
        border_alpha = int((200 if active else 40) * op)
        bg_alpha = int((30 if active else 0) * op)
        chunk_alpha = int(150 * op)
        
        border_color = f"rgba(46, 204, 113, {border_alpha})" if active else f"rgba(255, 255, 255, {border_alpha})"
        bg_color = f"rgba(46, 204, 113, {bg_alpha})" if active else "transparent"
        
        return f"""
            QProgressBar {{
                border: 2px dashed {border_color};
                border-radius: 10px;
                background-color: {bg_color};
            }}
            QProgressBar::chunk {{
                background-color: rgba(46, 204, 113, {chunk_alpha});
                border-radius: 8px;
            }}
        """

    def resizeEvent(self, event):
        # Keep settings frame centered or positioned well
        if hasattr(self, 'settings_frame'):
            w = max(0, self.width() - 30)
            h = max(0, self.height() - 30)
            self.settings_frame.setGeometry(15, 15, w, h)
        super().resizeEvent(event)

    def update_aura(self):
        self.aura_time += self.current_beat_speed
        op = self.current_opacity
        
        # Sine wave from 0.0 to 1.0
        intensity = (math.sin(self.aura_time) + 1) / 2.0
        
        if self.aura_state == 'idle':
            # Slow neon blue breathing
            self.current_beat_speed = self.base_beat_speed
            blur = int(10 + intensity * 20) # 10 to 30
            alpha = int(100 + intensity * 100) # 100 to 200
            alpha = int(alpha * op)
            self.aura_effect.setColor(QColor(0, 150, 255, alpha))
            self.aura_effect.setBlurRadius(blur)
        elif self.aura_state == 'copying':
            # Orange beating proportional to speed
            speed_mult = min(max(self.copy_speed_factor / (1024*1024*10), 0.0), 2.0) # normalize up to ~10MB/s -> 2.0
            self.current_beat_speed = self.base_beat_speed * 2 + speed_mult * 0.2
            
            blur = int(15 + intensity * 25)
            base_alpha = 150 + int(speed_mult * 50)
            alpha = int(base_alpha * (0.5 + intensity * 0.5))
            alpha = min(max(alpha, 0), 255)
            alpha = int(alpha * op)
            
            # Bright neon orange: R: 255, G: 120, B: 0
            self.aura_effect.setColor(QColor(255, 120, 0, alpha))
            self.aura_effect.setBlurRadius(blur)

    def update_copy_speed(self, bps):
        self.copy_speed_factor = bps

    def mousePressEvent(self, event):
        if event.button() == Qt.LeftButton and not self.is_locked:
            self.oldPos = event.globalPos()
            
            # Check if clicking in the bottom right corner (20x20 area)
            if event.pos().x() >= self.width() - 25 and event.pos().y() >= self.height() - 25:
                self._is_resizing = True
            else:
                self._is_resizing = False

    def mouseMoveEvent(self, event):
        if not self.is_locked:
            # Update cursor if hovering near the corner
            if event.pos().x() >= self.width() - 25 and event.pos().y() >= self.height() - 25:
                self.setCursor(Qt.SizeFDiagCursor)
            else:
                self.setCursor(Qt.ArrowCursor)
                
            if event.buttons() & Qt.LeftButton:
                if getattr(self, '_is_resizing', False):
                    new_width = max(self.minimumWidth(), event.pos().x())
                    new_height = max(self.minimumHeight(), event.pos().y())
                    self.resize(new_width, new_height)
                elif hasattr(self, 'oldPos'):
                    delta = QPoint(event.globalPos() - self.oldPos)
                    self.move(self.x() + delta.x(), self.y() + delta.y())
                    self.oldPos = event.globalPos()
            
    def contextMenuEvent(self, event):
        menu = QMenu(self)
        
        tv = QAction("Hide / Show", self)
        tv.triggered.connect(self._toggle_vis)
        menu.addAction(tv)
        
        la = QAction("Toggle Lock", self)
        la.triggered.connect(self.toggle_lock)
        menu.addAction(la)
        
        menu.addSeparator()
        
        qa = QAction("Quit", self)
        qa.triggered.connect(QApplication.quit)
        menu.addAction(qa)
        
        menu.exec_(event.globalPos())

    def dragEnterEvent(self, event):
        if event.mimeData().hasUrls():
            event.accept()
            self.drop_area.setStyleSheet(self._get_drop_area_style(active=True))
        else:
            event.ignore()

    def dragLeaveEvent(self, event):
        self.drop_area.setStyleSheet(self._get_drop_area_style(active=False))

    def dropEvent(self, event):
        self.drop_area.setStyleSheet(self._get_drop_area_style(active=False))
        
        if event.mimeData().hasUrls():
            event.setDropAction(Qt.CopyAction)
            event.accept()
            urls = event.mimeData().urls()
            
            self.drop_area.setValue(0)
            self.aura_state = 'copying'
            self.copy_speed_factor = 0.0
            
            self.copy_thread = CopyThread(urls, DESTINATION_FOLDER)
            self.copy_thread.progress.connect(self.drop_area.setValue)
            self.copy_thread.speed.connect(self.update_copy_speed)
            self.copy_thread.finished.connect(self.on_copy_finished)
            self.copy_thread.start()
        else:
            event.ignore()

    def on_copy_finished(self):
        self.aura_state = 'idle'
        self.copy_speed_factor = 0.0
        # Reset progress after 2 seconds
        threading.Timer(2.0, lambda: self.drop_area.setValue(0)).start()

    def _setup_tray(self):
        pix = QPixmap(16, 16)
        pix.fill(Qt.transparent)
        pp = QPainter(pix)
        pp.setRenderHint(QPainter.Antialiasing)
        pp.setBrush(QBrush(QColor(0, 150, 255)))
        pp.setPen(Qt.NoPen)
        pp.drawEllipse(2, 2, 12, 12)
        pp.end()

        self._tray = QSystemTrayIcon(QIcon(pix), self)
        self._tray.setToolTip("") 

        menu = QMenu()

        tv = QAction("Hide / Show", self)
        tv.triggered.connect(self._toggle_vis)
        menu.addAction(tv)

        la = QAction("Toggle Lock", self)
        la.triggered.connect(self.toggle_lock)
        menu.addAction(la)
        menu.addSeparator()

        qa = QAction("Quit", self)
        qa.triggered.connect(QApplication.quit)
        menu.addAction(qa)

        self._tray.setContextMenu(menu)
        self._tray.activated.connect(
            lambda r: self._toggle_vis() if r == QSystemTrayIcon.Trigger else None)
        self._tray.show()

    def _toggle_vis(self):
        if self.isVisible():
            self.hide()
        else:
            self.show()
            self.raise_()

    def toggle_lock(self):
        self.set_lock(not self.is_locked)

    def toggle_settings(self):
        self.settings_frame.setVisible(not self.settings_frame.isVisible())

    def change_opacity(self, value):
        self.current_opacity = value / 100.0
        self.update_styles()

    def set_lock(self, locked):
        self.is_locked = locked
        if self.lock_cb.isChecked() != locked:
            self.lock_cb.setChecked(locked)
        s = "locked" if self.is_locked else "unlocked"
        self._tray.showMessage("", f"Position {s}.",
                               QSystemTrayIcon.Information, 1500) 

    def set_always_on_top(self, on_top):
        self.is_always_on_top = on_top
        flags = self.windowFlags()
        if on_top:
            flags |= Qt.WindowStaysOnTopHint
        else:
            flags &= ~Qt.WindowStaysOnTopHint
        self.setWindowFlags(flags)
        self.show()

if __name__ == '__main__':
    app = QApplication(sys.argv)
    ex = NomadWidget()
    ex.show()
    sys.exit(app.exec_())
