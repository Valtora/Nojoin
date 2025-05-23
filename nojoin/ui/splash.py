from PySide6.QtWidgets import QSplashScreen
from PySide6.QtGui import QPixmap
from PySide6.QtCore import Qt
import os

class SplashScreen(QSplashScreen):
    def __init__(self, image_path: str):
        pixmap = QPixmap(image_path)
        if not pixmap.isNull():
            scaled_pixmap = pixmap.scaled(pixmap.width() * 0.2, pixmap.height() * 0.2, Qt.KeepAspectRatio, Qt.SmoothTransformation)
        else:
            scaled_pixmap = pixmap
        super().__init__(scaled_pixmap, Qt.WindowStaysOnTopHint)
        self.setWindowFlag(Qt.FramelessWindowHint)
        self.setStyleSheet("background-color: #18120b;")  # Match app dark theme
        self.setMask(scaled_pixmap.mask())

    def show_splash(self):
        self.show()
        # Process events to ensure splash is shown immediately
        from PySide6.QtWidgets import QApplication
        QApplication.processEvents()

    def finish_splash(self, widget):
        self.finish(widget) 