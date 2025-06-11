from PySide6.QtWidgets import QDialog, QVBoxLayout, QLabel, QProgressBar
from PySide6.QtCore import Qt
from nojoin.utils.theme_utils import apply_theme_to_widget
from nojoin.utils.config_manager import config_manager

class MeetingNotesProgressDialog(QDialog):
    def __init__(self, parent=None, message="Generating meeting notes..."):
        super().__init__(parent)
        self.setWindowTitle("Generating Meeting Notes")
        
        # Use scaled minimum size
        from nojoin.utils.ui_scale_manager import get_ui_scale_manager
        ui_scale_manager = get_ui_scale_manager()
        min_width = ui_scale_manager.scale_value(320)
        min_height = ui_scale_manager.scale_value(120)
        self.setMinimumWidth(min_width)
        self.setMinimumHeight(min_height)
        self.setModal(True)
        self.setWindowFlags(self.windowFlags() & ~Qt.WindowContextHelpButtonHint)
        apply_theme_to_widget(self, config_manager.get("theme", "dark"))

        layout = QVBoxLayout(self)
        layout.setSpacing(16)
        layout.setContentsMargins(20, 20, 20, 20)

        self.message_label = QLabel(message)
        self.message_label.setAlignment(Qt.AlignCenter)
        self.message_label.setWordWrap(True)
        self.message_label.setStyleSheet("font-size: 13px;")
        layout.addWidget(self.message_label)

        self.spinner = QProgressBar()
        self.spinner.setRange(0, 0)
        self.spinner.setTextVisible(False)
        self.spinner.setFixedHeight(22)
        self.spinner.setStyleSheet("""
            QProgressBar {
                border: 1px solid #bbb;
                border-radius: 5px;
                text-align: center;
                background: #f8f8f8;
            }
            QProgressBar::chunk {
                background-color: #4CAF50;
                border-radius: 4px;
            }
        """)
        layout.addWidget(self.spinner)

        subtitle = QLabel("This may take a moment...")
        subtitle.setAlignment(Qt.AlignCenter)
        subtitle.setStyleSheet("color: #666; font-size: 11px;")
        layout.addWidget(subtitle)

        self.setLayout(layout)

    def set_message(self, message):
        self.message_label.setText(message) 