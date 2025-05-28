"""
Theme utility for Nojoin: provides QSS and palette logic for robust theme switching.
"""
from PySide6.QtGui import QPalette, QColor
from PySide6.QtWidgets import QWidget

# --- THEME PALETTE AND FONT HIERARCHY ---
THEME_PALETTE = {
    "dark": {
        "primary_bg": "#000000",
        "secondary_bg": "#000000",
        "panel_bg": "#000000",
        "panel_border": "#ff9800",
        "accent": "#ff9800",
        "accent2": "#ff9800",
        "primary_text": "#ff9800",
        "secondary_text": "#ff9800",
        "muted_text": "#888888",
        "html_bg": "#000000",
        "html_text": "#ff9800",
        "chip_bg": "#000000",
        "chip_border": "#ff9800",
        "chip_text": "#ff9800",
        "speaker_chip_border": "#ff9800",
        "speaker_chip_text": "#ff9800",
        "status_bar_bg": "#000000",
        "status_bar_text": "#ff9800",
        "disabled_bg": "#222222",
        "disabled_text": "#888888",
    },
    "light": {
        "primary_bg": "#ffffff",
        "secondary_bg": "#ffffff",
        "panel_bg": "#ffffff",
        "panel_border": "#007aff",
        "accent": "#007aff",
        "accent2": "#007aff",
        "primary_text": "#007aff",
        "secondary_text": "#007aff",
        "muted_text": "#888888",
        "html_bg": "#ffffff",
        "html_text": "#007aff",
        "chip_bg": "#ffffff",
        "chip_border": "#007aff",
        "chip_text": "#007aff",
        "speaker_chip_border": "#007aff",
        "speaker_chip_text": "#007aff",
        "status_bar_bg": "#ffffff",
        "status_bar_text": "#007aff",
        "disabled_bg": "#dddddd",
        "disabled_text": "#888888",
    }
}
FONT_HIERARCHY = {
    "h1": {"size": 38, "weight": 800},
    "h2": {"size": 22, "weight": 700},
    "body": {"size": 15, "weight": 400},
    "caption": {"size": 13, "weight": 400},
    "meta": {"size": 12, "weight": 400},
}

theme_qss = {
    "dark": """
        QMainWindow, QDialog {
            background: qlineargradient(x1:0, y1:0, x2:0, y2:1, stop:0 #181818, stop:1 #232323);
        }
        QWidget {
            color: #f5f5f5;
            background: #232323;
            font-family: 'Segoe UI', Arial, sans-serif;
            font-size: 13px;
        }
        QStatusBar {
            background: #181818;
            color: #ff9800;
            border-top: 1px solid #333;
        }
        QPushButton {
            background: {palette['accent']};
            color: {palette['secondary_bg']};
            border: none;
            border-radius: 6px;
            padding: 6px 16px;
            font-weight: bold;
            margin: 2px;
            min-width: 32px;
            min-height: 28px;
        }
        QPushButton:disabled {
            background: {palette['disabled_bg']};
            color: {palette['disabled_text']};
        }
        QPushButton:hover {
            background: {hover_color};
            color: {palette['secondary_bg']};
        }
        QPushButton:pressed {
            background: {palette['accent2']};
            color: {palette['secondary_bg']};
        }
        QPushButton#AddLabelButton {
            background: {palette['accent']};
            color: {palette['secondary_bg']};
            border-radius: 8px;
            padding: 2px 12px;
            font-weight: bold;
            font-size: {font['caption']['size']}px;
        }
        QTableView {
            background: #181818;
            alternate-background-color: #232323;
            gridline-color: #333;
            color: #f5f5f5;
            selection-background-color: #ff9800;
            selection-color: #181818;
            border-radius: 6px;
        }
        QTableView QTableCornerButton::section {
            background: #181818;
        }
        QTableView::item:selected, QTableView::item:active:selected {
            color: #181818 !important;
            background: #ff9800;
        }
        QHeaderView::section {
            background: #181818;
            color: #ff9800;
            border: none;
            font-weight: bold;
        }
        QLabel {
            color: #ff9800;
        }
        QLineEdit, QTextEdit {
            background: #232323;
            color: #f5f5f5;
            border: 1px solid #444;
            border-radius: 4px;
        }
        QTextEdit[readOnly="true"] {
            border: none;
            background: #232323;
        }
        QFrame[frameShape="4"] { /* QFrame.HLine */
            background: #333;
            max-height: 2px;
            min-height: 2px;
        }
        QSlider::groove:horizontal {
            border: 1px solid {slider_groove};
            height: 4px;
            background: {slider_groove};
            border-radius: 2px;
        }
        QSlider::handle:horizontal {
            background: {slider_handle};
            border: 1px solid {slider_handle};
            width: 14px;
            border-radius: 7px;
        }
        QComboBox {
            border: 1px solid #444;
            border-radius: 4px;
            background: #232323;
            color: #f5f5f5;
        }
        QComboBox::drop-down {
            subcontrol-origin: padding;
            subcontrol-position: top right;
            width: 15px;
            border-left-width: 1px;
            border-left-color: #444;
            border-left-style: solid;
            border-top-right-radius: 3px;
            border-bottom-right-radius: 3px;
        }
        QComboBox::down-arrow {
            image: url(assets/down_arrow_light.png); /* TODO: Create this asset */
        }
        QComboBox QAbstractItemView {
            border: 1px solid #444;
            background: #232323;
            selection-background-color: #ff9800;
            color: #f5f5f5;
        }
        QProgressBar {
            border: 1px solid #444;
            border-radius: 4px;
            text-align: center;
            color: #f5f5f5;
            background-color: #232323;
        }
        QProgressBar::chunk {
            background-color: #ff9800;
            border-radius: 3px;
            margin: 1px;
        }
        QMessageBox {
            background-color: #232323;
        }
        QMessageBox QLabel {
            color: #f5f5f5;
        }
        QMessageBox QPushButton {
            padding: 4px 12px;
            min-height: 24px;
        }
        QMenu {
            background: #232323;
            color: #f5f5f5;
            border-radius: 8px;
            border: 1px solid #444;
            min-width: 180px;
        }
        QMenu::item {
            border-radius: 4px;
            background: transparent;
            color: #f5f5f5;
        }
        QMenu::item:selected {
            background: #ff9800;
            color: #181818;
        }
        QMenu::item:hover {
            background: #ff9800;
            color: #181818;
        }
        QMenu::separator {
            background: #444;
        }
        /* Tag chips */
        QLabel[tagchip="true"] {
            background: #232323;
            color: #ff9800;
            border-radius: 8px;
            border: 1px solid #ff9800;
        }
        /* Speaker chips */
        QLabel[speakerchip="true"] {
            background: #232323;
            color: #2196f3;
            border-radius: 8px;
            border: 1px solid #2196f3;
        }
        /* Meta text */
        .meta {
            color: #888;
        }
        /* --- Custom Meeting List Item Widget Styling --- */
        QListWidget::item {
            background: #232323;
            border: none;
        }
        MeetingListItemWidget, QWidget#MeetingListItemWidget {
            background: #232323;
            border-radius: 4px;
        }
        QListWidget::item:selected MeetingListItemWidget, QListWidget::item:selected QWidget#MeetingListItemWidget {
            background-color: #ff980022;
        }
        QListWidget::item:hover MeetingListItemWidget, QListWidget::item:hover QWidget#MeetingListItemWidget {
            background-color: #f5f5f511;
        }
        QLabel#MeetingListItemTitleLabel {
            color: #f5f5f5;
            font-weight: bold;
            background: #232323;
        }
        QListWidget::item:selected QLabel#MeetingListItemTitleLabel {
            color: #ff9800;
        }
        QLabel#MeetingListItemDateTimeLabel {
            color: #888;
            background: #232323;
        }
        QListWidget::item:selected QLabel#MeetingListItemDateTimeLabel {
            color: #ff9800;
        }
        QLineEdit#SearchBarInput, QLineEdit#MeetingChatLineEdit {
            border: none;
            border-bottom: 2px solid #ff9800;
            border-radius: 0;
            background: transparent;
            padding: 6px 10px;
        }
        QTextEdit#MeetingNotesEdit {
            background: {palette['panel_bg']};
            border: none;
            color: {palette['primary_text']};
        }
        /* Audio Warning Banner */
        QFrame#AudioWarningBanner {
            background: #ff6f00;
            border-radius: 4px;
            margin: 2px 0;
        }
        QLabel#AudioWarningLabel {
            color: #181818;
            font-weight: bold;
            background: transparent;
        }
        QPushButton#CloseWarningButton {
            background: transparent;
            color: #181818;
            border: none;
            font-weight: bold;
            font-size: 12px;
            padding: 0px;
        }
        QPushButton#CloseWarningButton:hover {
            background: rgba(0, 0, 0, 0.1);
            border-radius: 8px;
        }
        QFrame#MeetingListItemCard[selected="false"] {
            background: #000000;
            border: 2px solid #ff9800;
            border-radius: 10px;
        }
        QFrame#MeetingListItemCard[selected="true"] {
            background: #ff9800;
            border: 2px solid #ff9800;
            border-radius: 10px;
        }
        QFrame#MeetingListItemCard[selected="false"] QLabel {
            color: #ff9800;
            background: transparent;
        }
        QFrame#MeetingListItemCard[selected="true"] QLabel {
            color: #000000;
            background: transparent;
        }
    """,
    "light": """
        QMainWindow, QDialog {
            background: qlineargradient(x1:0, y1:0, x2:0, y2:1, stop:0 #f0f0f0, stop:1 #e8e8e8);
        }
        QWidget {
            color: #181818;
            background: #ffffff;
            font-family: 'Segoe UI', Arial, sans-serif;
            font-size: 13px;
        }
        QStatusBar {
            background: #e0e0e0;
            color: #007acc;
            border-top: 1px solid #ccc;
        }
        QPushButton {
            background: {palette['accent']};
            color: {palette['secondary_bg']};
            border: none;
            border-radius: 6px;
            padding: 6px 16px;
            font-weight: bold;
            margin: 2px;
            min-width: 32px;
            min-height: 28px;
        }
        QPushButton:disabled {
            background: {palette['disabled_bg']};
            color: {palette['disabled_text']};
        }
        QPushButton:hover {
            background: {hover_color};
            color: {palette['secondary_bg']};
        }
        QPushButton:pressed {
            background: {palette['accent2']};
            color: {palette['secondary_bg']};
        }
        QPushButton#AddLabelButton {
            background: {palette['accent']};
            color: {palette['secondary_bg']};
            border-radius: 8px;
            padding: 2px 12px;
            font-weight: bold;
            font-size: {font['caption']['size']}px;
        }
        QTableView {
            background: #ffffff;
            alternate-background-color: #f0f0f0;
            gridline-color: #dcdcdc;
            color: #181818;
            selection-background-color: #007acc;
            selection-color: #ffffff;
            border-radius: 6px;
        }
        QTableView QTableCornerButton::section {
            background: #f0f0f0;
        }
        QTableView::item:selected, QTableView::item:active:selected {
            color: #ffffff !important;
            background: #007acc;
        }
        QHeaderView::section {
            background: #f0f0f0;
            color: #007acc;
            border: none;
            font-weight: bold;
        }
        QLabel {
            color: #007acc;
        }
        QLineEdit, QTextEdit {
            background: #ffffff;
            color: #181818;
            border: 1px solid #cccccc;
            border-radius: 4px;
        }
        QTextEdit[readOnly="true"] {
            border: none;
            background: #ffffff;
        }
        QFrame[frameShape="4"] { /* QFrame.HLine */
            background: #d0d0d0;
            max-height: 1px;
            min-height: 1px;
        }
        QSlider::groove:horizontal {
            border: 1px solid {slider_groove};
            height: 4px;
            background: {slider_groove};
            border-radius: 2px;
        }
        QSlider::handle:horizontal {
            background: {slider_handle};
            border: 1px solid {slider_handle};
            width: 14px;
            border-radius: 7px;
        }
        QComboBox {
            border: 1px solid #cccccc;
            border-radius: 4px;
            padding: 4px 8px;
            background: #ffffff;
            color: #181818;
        }
        QComboBox::drop-down {
            subcontrol-origin: padding;
            subcontrol-position: top right;
            width: 15px;
            border-left-width: 1px;
            border-left-color: #cccccc;
            border-left-style: solid;
            border-top-right-radius: 3px;
            border-bottom-right-radius: 3px;
        }
        QComboBox::down-arrow {
            image: url(assets/down_arrow_dark.png); /* TODO: Create this asset */
        }
        QComboBox QAbstractItemView {
            border: 1px solid #cccccc;
            background: #ffffff;
            selection-background-color: #007acc;
            color: #181818;
            selection-color: #ffffff;
        }
        QProgressBar {
            border: 1px solid #cccccc;
            border-radius: 4px;
            text-align: center;
            color: #181818;
            background-color: #f0f0f0;
        }
        QProgressBar::chunk {
            background-color: #007acc;
            border-radius: 3px;
            margin: 1px;
        }
        QMessageBox {
            background-color: #f0f0f0;
        }
        QMessageBox QLabel {
            color: #181818;
        }
        QMessageBox QPushButton {
            padding: 4px 12px;
            min-height: 24px;
        }
        QMenu {
            background: #ffffff;
            color: #181818;
            border-radius: 8px;
            padding: 8px 0;
            border: 1px solid #cccccc;
            min-width: 180px;
        }
        QMenu::item {
            padding: 8px 24px;
            border-radius: 4px;
            background: transparent;
            color: #181818;
        }
        QMenu::item:selected {
            background: #007acc;
            color: #ffffff;
        }
        QMenu::item:hover {
            background: #007acc;
            color: #ffffff;
        }
        QMenu::separator {
            height: 1px;
            background: #cccccc;
            margin: 4px 12px;
        }
        QFrame#SearchFrame {
            background: #ffffff;
            border: 2px solid #cccccc;
            border-radius: 10px;
            padding: 4px;
        }
        QFrame#SearchFrame QLineEdit {
            background: #f0f0f0;
            color: #181818;
            border: none;
            font-size: 15px;
            padding: 6px 8px;
            border-radius: 6px;
        }
        QFrame#SearchFrame QToolButton {
            background: transparent;
            border: none;
            border-radius: 12px;
            min-width: 24px;
            min-height: 24px;
        }
        QFrame#SearchFrame QToolButton:hover {
            background: #e0e0e0;
        }
        /* --- Chat Modern UI --- */
        QListWidget#ChatHistoryList {
            background: #ffffff;
            border: none;
            padding: 8px 0;
        }
        QListWidget#ChatHistoryList::item {
            background: transparent;
            border: 1px;
            margin: 0;
            padding: 0;
        }
        QWidget#ChatMessageWidget {
            border-radius: 14px;
            padding: 0;
            margin: 0;
        }
        QTextEdit {
            background: transparent;
            border: none;
            font-size: 15px;
            color: #181818;
        }
        QLabel#ChatSenderLabel {
            font-size: 12px;
            color: #007acc;
            font-weight: bold;
            margin-bottom: 2px;
        }
        QLabel#ChatTimestampLabel {
            font-size: 11px;
            color: #888;
            margin-left: 8px;
        }
        /* User bubble */
        ChatMessageWidget[is_user="true"] QTextEdit {
            background: #007acc;
            color: #ffffff;
            border-radius: 14px;
            padding: 8px 12px;
            margin-left: 40px;
            margin-right: 0;
        }
        /* Assistant bubble */
        ChatMessageWidget[is_user="false"] QTextEdit {
            background: #ffffff;
            color: #181818;
            border: 1.5px solid #007acc;
            border-radius: 14px;
            padding: 8px 12px;
            margin-right: 40px;
            margin-left: 0;
        }
        /* Typing indicator */
        QLabel#TypingSpinnerLabel {
            color: #007acc;
            font-size: 18px;
            font-weight: bold;
            letter-spacing: 2px;
        }
        QLabel#TypingTextLabel {
            color: #555;
            font-size: 13px;
            margin-left: 6px;
        }
        QLineEdit#SearchBarInput, QLineEdit#MeetingChatLineEdit {
            border: none;
            border-bottom: 2px solid #007acc;
            border-radius: 0;
            background: transparent;
            padding: 6px 10px;
        }
        QTextEdit#MeetingNotesEdit {
            background: {palette['panel_bg']};
            border: none;
            color: {palette['primary_text']};
        }
        /* Audio Warning Banner */
        QFrame#AudioWarningBanner {
            background: #ffa726;
            border-radius: 4px;
            margin: 2px 0;
        }
        QLabel#AudioWarningLabel {
            color: #181818;
            font-weight: bold;
            background: transparent;
        }
        QPushButton#CloseWarningButton {
            background: transparent;
            color: #181818;
            border: none;
            font-weight: bold;
            font-size: 12px;
            padding: 0px;
        }
        QPushButton#CloseWarningButton:hover {
            background: rgba(0, 0, 0, 0.1);
            border-radius: 8px;
        }
        QFrame#MeetingListItemCard[selected="false"] {
            background: #ffffff;
            border: 2px solid #007aff;
            border-radius: 10px;
        }
        QFrame#MeetingListItemCard[selected="true"] {
            background: #007aff;
            border: 2px solid #007aff;
            border-radius: 10px;
        }
        QFrame#MeetingListItemCard[selected="false"] QLabel {
            color: #007aff;
            background: transparent;
        }
        QFrame#MeetingListItemCard[selected="true"] QLabel {
            color: #ffffff;
            background: transparent;
        }
    """
}

def get_theme_qss(theme_name: str) -> str:
    """
    Returns the QSS string for the given theme, using THEME_PALETTE and FONT_HIERARCHY.
    """
    palette = THEME_PALETTE[theme_name]
    font = FONT_HIERARCHY
    # Set hover color for each theme
    if theme_name == "dark":
        hover_color = "#ffb74d"  # lighter orange
        slider_groove = "#444444"
        slider_handle = "#ffb74d"
    else:
        hover_color = "#0051a8"  # darker blue
        slider_groove = "#cccccc"
        slider_handle = "#007aff"
    return f"""
    QMainWindow, QDialog {{
        background: qlineargradient(x1:0, y1:0, x2:0, y2:1, stop:0 {palette['secondary_bg']}, stop:1 {palette['primary_bg']});
    }}
    QWidget {{
        color: {palette['primary_text']};
        background: {palette['primary_bg']};
        font-family: 'Segoe UI', Arial, sans-serif;
        font-size: {font['body']['size']}px;
    }}
    QStatusBar {{
        background: {palette['status_bar_bg']};
        color: {palette['status_bar_text']};
        border-top: 1px solid {palette['panel_border']};
    }}
    QPushButton {{
        background: {palette['accent']};
        color: {palette['secondary_bg']};
        border: none;
        border-radius: 6px;
        padding: 6px 16px;
        font-weight: bold;
        margin: 2px;
        min-width: 32px;
        min-height: 28px;
    }}
    QPushButton#AddLabelButton {{
        background: {palette['accent']};
        color: {palette['secondary_bg']};
        border-radius: 8px;
        padding: 2px 12px;
        font-weight: bold;
        font-size: {font['caption']['size']}px;
    }}
    QLabel#SpeakerLabelTitle {{
        color: {palette['accent']};
        font-size: {font['h2']['size']}px;
        font-weight: {font['h2']['weight']};
        margin-bottom: 8px;
        margin-top: 4px;
    }}
    QFrame#MainVerticalDivider {{
        background: {palette['panel_border']};
        min-width: 3px;
        max-width: 4px;
    }}
    .meeting-list-title {{
        color: {palette['accent']};
        font-weight: bold;
        font-size: {font['body']['size']}px;
    }}
    QPushButton:disabled {{
        background: {palette['disabled_bg']};
        color: {palette['disabled_text']};
    }}
    QPushButton:hover {{
        background: {hover_color};
        color: {palette['secondary_bg']};
    }}
    QPushButton:pressed {{
        background: {palette['accent2']};
        color: {palette['secondary_bg']};
    }}
    QFrame[objectName="MainPanelLeft"], QFrame[objectName="MainPanelRight"], QFrame#ChatPanel {{
        background: {palette['panel_bg']};
        border: 2.5px solid {palette['panel_border']};
        border-radius: 18px;
        padding: 12px;
    }}
    QFrame[objectName="MainPanelCenter"] {{
        background: {palette['panel_bg']};
        border: none;
        border-radius: 0px;
        padding: 12px;
    }}
    QTextEdit#MeetingNotesEdit {{
        background: {palette['panel_bg']};
        border: none;
        color: {palette['primary_text']};
    }}
    QLabel#MeetingContextInfo {{
        font-size: {font['h1']['size']}px;
        font-weight: {font['h1']['weight']};
        color: {palette['accent']};
        margin-bottom: 6px;
        border-radius: 12px;
        border: 2px solid {palette['panel_border']};
        padding: 8px 12px;
        background: {palette['panel_bg']};
    }}
    QLabel {{
        color: {palette['accent']};
        font-size: {font['body']['size']}px;
    }}
    QLineEdit, QTextEdit {{
        background: {palette['panel_bg']};
        color: {palette['primary_text']};
        border: 1.5px solid {palette['panel_border']};
        border-radius: 8px;
        padding: 4px;
        font-size: {font['body']['size']}px;
    }}
    QTextEdit[readOnly="true"] {{
        border: none;
        background: {palette['panel_bg']};
        color: {palette['primary_text']};
    }}
    QFrame[frameShape="4"] {{ /* QFrame.HLine */
        background: {palette['panel_border']};
        max-height: 2px;
        min-height: 2px;
    }}
    /* Tag chips */
    QLabel[tagchip="true"] {{
        background: {palette['chip_bg']};
        color: {palette['chip_text']};
        border-radius: 8px;
        padding: 2px 8px;
        margin: 2px;
        border: 1px solid {palette['chip_border']};
        font-size: {font['caption']['size']}px;
    }}
    /* Speaker chips */
    QLabel[speakerchip="true"] {{
        background: {palette['chip_bg']};
        color: {palette['speaker_chip_text']};
        border-radius: 8px;
        padding: 2px 8px;
        margin: 2px;
        border: 1px solid {palette['speaker_chip_border']};
        font-size: {font['caption']['size']}px;
    }}
    /* Meta text */
    .meta {{
        color: {palette['muted_text']};
        font-size: {font['meta']['size']}px;
    }}
    /* --- Custom Meeting List Item Widget Styling --- */
    QListWidget::item {{
        background: {palette['primary_bg']};
        border: none;
        margin: 1px 0px;
        padding: 0px;
    }}
    MeetingListItemWidget, QWidget#MeetingListItemWidget {{
        background: {palette['primary_bg']};
        border-radius: 4px;
        padding: 0px;
        margin: 0px;
    }}
    QListWidget::item:selected MeetingListItemWidget, QListWidget::item:selected QWidget#MeetingListItemWidget {{
        background-color: {palette['accent']}22;
    }}
    QListWidget::item:hover MeetingListItemWidget, QListWidget::item:hover QWidget#MeetingListItemWidget {{
        background-color: {palette['primary_text']}11;
    }}
    QLabel#MeetingListItemTitleLabel {{
        color: {palette['primary_text']};
        font-size: {font['body']['size'] + 1}px;
        font-weight: bold;
        background: {palette['primary_bg']};
        padding: 0px;
        margin: 0px;
    }}
    QListWidget::item:selected QLabel#MeetingListItemTitleLabel {{
        color: {palette['accent']};
    }}
    QLabel#MeetingListItemDateTimeLabel {{
        color: {palette['muted_text']};
        font-size: {font['caption']['size']}px;
        background: {palette['primary_bg']};
        padding: 0px;
        margin: 0px;
    }}
    QListWidget::item:selected QLabel#MeetingListItemDateTimeLabel {{
        color: {palette['accent']};
    }}
    QLineEdit#SearchBarInput, QLineEdit#MeetingChatLineEdit {{
        border: none;
        border-bottom: 2px solid {palette['accent']};
        border-radius: 0;
        background: transparent;
        padding: 6px 10px;
    }}
    """

def apply_theme_to_widget(widget: QWidget, theme_name: str):
    """
    Apply the theme QSS and palette to the given widget (QMainWindow, QDialog, etc).
    """
    qss = get_theme_qss(theme_name)
    widget.setStyleSheet(qss)
    palette = QPalette()
    if theme_name == "dark":
        palette.setColor(QPalette.Window, QColor("#232323"))
        palette.setColor(QPalette.Base, QColor("#232323"))
        palette.setColor(QPalette.WindowText, QColor("#f5f5f5"))
        palette.setColor(QPalette.Text, QColor("#f5f5f5"))
        palette.setColor(QPalette.Button, QColor("#181818"))
        palette.setColor(QPalette.ButtonText, QColor("#ff9800"))
        palette.setColor(QPalette.Highlight, QColor("#ff9800"))
        palette.setColor(QPalette.HighlightedText, QColor("#181818"))
    else:
        palette.setColor(QPalette.Window, QColor("#ffffff"))
        palette.setColor(QPalette.Base, QColor("#ffffff"))
        palette.setColor(QPalette.WindowText, QColor("#181818"))
        palette.setColor(QPalette.Text, QColor("#181818"))
        palette.setColor(QPalette.Button, QColor("#e0e0e0"))
        palette.setColor(QPalette.ButtonText, QColor("#007acc"))
        palette.setColor(QPalette.Highlight, QColor("#007acc"))
        palette.setColor(QPalette.HighlightedText, QColor("#ffffff"))
    widget.setPalette(palette)

def get_menu_qss(theme_name: str) -> str:
    qss = theme_qss.get(theme_name, theme_qss["dark"])
    # Extract QMenu section only
    import re
    match = re.search(r'(QMenu[\s\S]*?)(?=Q|$)', qss)
    return match.group(1) if match else ""

def get_border_color(theme_name: str) -> str:
    """
    Returns the border color hex code for the given theme.
    """
    if theme_name == "dark":
        return "#444"  # Matches dark theme border in QSS
    else:
        return "#cccccc"  # Matches light theme border in QSS

def get_html_body_style(theme_name: str) -> str:
    """
    Generates a CSS style string for the HTML body based on the theme.
    """
    palette = THEME_PALETTE.get(theme_name, THEME_PALETTE["dark"])
    font_details = FONT_HIERARCHY["body"]
    # Ensure the text color is applied with !important
    return (
        f"background-color: {palette['html_bg']}; "
        f"color: {palette['html_text']} !important; "
        f"font-family: 'Segoe UI', Arial, sans-serif; "
        f"font-size: {font_details['size']}pt; "
        f"font-weight: {font_details['weight']};"
    )

def wrap_html_body(content: str, theme_name: str) -> str:
    """
    Wraps HTML content with a <body> tag styled according to the theme.
    Ensures basic document structure.
    """
    body_style = get_html_body_style(theme_name)
    palette = THEME_PALETTE.get(theme_name, THEME_PALETTE["dark"])
    
    # Check if content already has <html> or <body> tags
    has_html_tag = "<html" in content.lower()
    has_body_tag = "<body" in content.lower()

    # If content is a full HTML document, try to inject style into existing body or head
    if has_html_tag and has_body_tag:
        import re
        try:
            # Try to add style to existing body tag
            content = re.sub(r"<body([^>]*)>", f'''<body\1 style="{body_style}">''', content, count=1, flags=re.IGNORECASE)
            # Additionally, inject a style block into the head for more general overrides
            style_block = f"""
            <style>
                body {{ {body_style} }}
                p, div, span, li, th, td, caption, label, legend, summary, article, aside, footer, header, main, nav, section, dl, dt, dd, figcaption, figure, mark, data, time, abbr, address, b, bdi, bdo, cite, code, del, dfn, em, i, ins, kbd, pre, q, rp, rt, ruby, s, samp, small, strong, sub, sup, u, var, wbr, font {{
                    color: {palette['html_text']} !important;
                    background-color: transparent !important; /* Prevent unwanted backgrounds */
                }}
                h1, h2, h3, h4, h5, h6 {{
                    color: {palette['html_text']} !important;
                }}
                a {{ color: {palette['accent']} !important; }}
                a:visited {{ color: {palette['accent2']} !important; }}
            </style>
            """
            if "<head>" in content.lower():
                content = re.sub(r"(<head[^>]*>)", r"\1" + style_block, content, count=1, flags=re.IGNORECASE)
            elif "<html>" in content.lower(): # if no head, but html tag exists, add a head with style
                content = re.sub(r"(<html[^>]*>)", r"\1<head>" + style_block + "</head>", content, count=1, flags=re.IGNORECASE)

        except re.error: 
            pass 
        return content 

    return f"""
    <!DOCTYPE html>
    <html>
    <head>
        <meta charset="UTF-8">
        <style>
            body {{
                {body_style}
            }}
            /* General override for common text-containing elements */
            p, div, span, li, th, td, caption, label, legend, summary, article, aside, footer, header, main, nav, section, dl, dt, dd, figcaption, figure, mark, data, time, abbr, address, b, bdi, bdo, cite, code, del, dfn, em, i, ins, kbd, pre, q, rp, rt, ruby, s, samp, small, strong, sub, sup, u, var, wbr, font {{
                color: {palette['html_text']} !important;
                background-color: transparent !important; /* Prevent unwanted backgrounds */
            }}
            h1, h2, h3, h4, h5, h6 {{
                color: {palette['html_text']} !important;
            }}
            a {{ color: {palette['accent']} !important; }}
            a:visited {{ color: {palette['accent2']} !important; }}
        </style>
    </head>
    <body style="{body_style}">
        {content}
    </body>
    </html>
    """

QSS_MEETING_CONTEXT_INFO = """
#MeetingContextInfo {
    font-size: 45px;
    margin-bottom: 6px;
}
"""

def get_modern_menu_qss(theme_name: str) -> str:
    """
    Returns a modern QSS string for QMenu with 10px border-radius and theme-aware accent highlight.
    """
    palette = THEME_PALETTE[theme_name]
    accent = palette['accent']
    accent_text = palette['secondary_bg'] if theme_name == 'dark' else '#ffffff'
    bg = palette['primary_bg']
    fg = palette['primary_text']
    border = palette['panel_border']
    separator = palette['panel_border'] if theme_name == 'dark' else '#cccccc'
    return f'''
    QMenu {{
        background: {bg};
        color: {fg};
        border-radius: 10px;
        border: 1.5px solid {border};
        padding: 8px 0;
        min-width: 180px;
    }}
    QMenu::item {{
        padding: 10px 28px;
        border-radius: 6px;
        background: transparent;
        color: {fg};
    }}
    QMenu::item:selected, QMenu::item:hover {{
        background: {accent};
        color: {accent_text};
    }}
    QMenu::separator {{
        height: 1px;
        background: {separator};
        margin: 6px 16px;
    }}
    ''' 