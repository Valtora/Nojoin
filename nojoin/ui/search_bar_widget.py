from PySide6.QtWidgets import QWidget, QHBoxLayout, QLineEdit, QToolButton, QStyle
from PySide6.QtCore import Signal, Qt
from PySide6.QtGui import QIcon

class SearchBarWidget(QWidget):
    text_changed = Signal(str)
    cleared = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self._init_ui()

    def _init_ui(self):
        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(2)
        self.search_bar_edit = QLineEdit()
        self.search_bar_edit.setPlaceholderText("Search meetings...")
        self.search_bar_edit.setObjectName("SearchBarInput")
        self.search_bar_edit.textChanged.connect(self._on_text_changed)
        self.clear_search_button = QToolButton()
        self.clear_search_button.setIcon(self.style().standardIcon(QStyle.SP_LineEditClearButton))
        self.clear_search_button.setAutoRaise(True)
        self.clear_search_button.setCursor(Qt.PointingHandCursor)
        self.clear_search_button.setVisible(False)
        self.clear_search_button.clicked.connect(self._on_clear_clicked)
        layout.addWidget(self.search_bar_edit)
        layout.addWidget(self.clear_search_button)
        self.setLayout(layout)

    def _on_text_changed(self, text):
        self.clear_search_button.setVisible(bool(text))
        self.text_changed.emit(text)

    def _on_clear_clicked(self):
        self.search_bar_edit.clear()
        self.clear_search_button.setVisible(False)
        self.cleared.emit()

    def set_theme(self, border_color, border_radius="8px"):
        # No-op: border is now controlled by QSS for robust theming
        pass

    def set_text(self, text):
        self.search_bar_edit.setText(text)

    def text(self):
        return self.search_bar_edit.text()

    def set_placeholder(self, text):
        self.search_bar_edit.setPlaceholderText(text)

    def focus_search(self):
        self.search_bar_edit.setFocus()

    def get_text(self):
        return self.search_bar_edit.text() 