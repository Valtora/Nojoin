import os
from PySide6.QtWidgets import (
    QDialog, QFormLayout, QComboBox, QLineEdit, QPushButton, QDialogButtonBox, QLabel, QFileDialog, QCheckBox, QMessageBox, QWidget, QHBoxLayout, QProgressDialog, QScrollArea, QVBoxLayout
)
from PySide6.QtGui import QDoubleValidator
from PySide6.QtCore import Qt, Signal, QThread
from PySide6.QtGui import QPalette, QColor
from nojoin.utils.config_manager import (
    config_manager,
    get_available_whisper_model_sizes,
    get_available_processing_devices,
    get_available_input_devices,
    get_available_output_devices,
    get_available_themes,
    get_available_notes_font_sizes,
    get_available_ui_scale_modes,
    get_default_model_for_provider
)
from nojoin.utils.theme_utils import apply_theme_to_widget
from nojoin.utils.backup_restore import BackupRestoreManager, get_default_backup_filename
from .update_dialog import CheckForUpdatesDialog
import logging

class SettingsDialog(QDialog):
    # Signal emitted when settings (potentially theme) are saved
    settings_saved = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Settings")
        
        # Use scaled minimum width and height
        from nojoin.utils.ui_scale_manager import get_ui_scale_manager
        ui_scale_manager = get_ui_scale_manager()
        min_width, min_height = ui_scale_manager.get_scaled_minimum_sizes()['settings_dialog']
        self.setMinimumWidth(min_width)
        self.setMinimumHeight(min_height)
        self.setModal(True)
        
        # Make dialog resizable for user control
        self.setSizeGripEnabled(True)
        self.parent_window = parent  # Store reference to check recording state
        self._init_ui()
        self._load_config()
        # Apply theme on construction (ensure after UI setup)
        apply_theme_to_widget(self, config_manager.get("theme", "dark"))

    def _is_recording_in_progress(self):
        """Check if the parent window is currently recording."""
        if self.parent_window and hasattr(self.parent_window, 'is_recording'):
            return self.parent_window.is_recording
        return False

    def _update_button_states(self):
        """Update the enabled state of buttons based on recording status."""
        recording_in_progress = self._is_recording_in_progress()
        
        # Disable backup, restore, and update check buttons during recording
        if hasattr(self, 'backup_button'):
            self.backup_button.setEnabled(not recording_in_progress)
            if recording_in_progress:
                self.backup_button.setToolTip("Cannot create backup while recording is in progress")
            else:
                self.backup_button.setToolTip("Create a backup of your data with optional audio file inclusion")
        
        if hasattr(self, 'restore_button'):
            self.restore_button.setEnabled(not recording_in_progress)
            if recording_in_progress:
                self.restore_button.setToolTip("Cannot restore backup while recording is in progress")
            else:
                self.restore_button.setToolTip("Restore data from a backup file (non-destructive)")
        
        if hasattr(self, 'check_updates_button'):
            self.check_updates_button.setEnabled(not recording_in_progress)
            if recording_in_progress:
                self.check_updates_button.setToolTip("Cannot check for updates while recording is in progress")
            else:
                self.check_updates_button.setToolTip("Check for available updates to Nojoin")

    def _init_ui(self):
        # Create main layout for the dialog
        main_layout = QVBoxLayout()
        self.setLayout(main_layout)
        main_layout.setContentsMargins(0, 0, 0, 0)
        main_layout.setSpacing(0)
        
        # Create scroll area
        scroll_area = QScrollArea()
        scroll_area.setWidgetResizable(True)
        scroll_area.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        scroll_area.setVerticalScrollBarPolicy(Qt.ScrollBarAsNeeded)
        scroll_area.setFrameShape(QScrollArea.NoFrame)
        
        # Create content widget that will be scrolled
        self.content_widget = QWidget()
        scroll_area.setWidget(self.content_widget)
        
        # Create form layout on the content widget
        layout = QFormLayout()
        self.content_widget.setLayout(layout)
        layout.setLabelAlignment(Qt.AlignRight)
        layout.setFormAlignment(Qt.AlignHCenter | Qt.AlignTop)
        layout.setSpacing(14)
        layout.setContentsMargins(15, 15, 15, 15)  # Add padding around the form content
        self._form_layout = layout  # Store reference for label/widget hiding

        # Add scroll area to main layout
        main_layout.addWidget(scroll_area)

        # --- Advanced Section Container ---
        self.advanced_container = QWidget()
        self.advanced_layout = QFormLayout(self.advanced_container)
        self.advanced_layout.setLabelAlignment(Qt.AlignRight)
        layout.setFormAlignment(Qt.AlignHCenter | Qt.AlignTop)
        self.advanced_layout.setSpacing(10)

        # Whisper model size (advanced)
        self.model_size_combo = QComboBox()
        self.model_size_combo.addItems(get_available_whisper_model_sizes())
        self.advanced_layout.addRow("Whisper Model Size:", self.model_size_combo)

        # Processing device (advanced)
        self.device_combo = QComboBox()
        self.available_devices = get_available_processing_devices()
        self.device_combo.addItems(self.available_devices)
        if "cuda" not in self.available_devices:
            self.device_combo.setEnabled(False)
            self.device_combo.setToolTip("CUDA (GPU) not available. Install CUDA 12.8 and compatible GPU drivers.")
        else:
            self.device_combo.setToolTip("Requires CUDA 12.8 and compatible GPU drivers.")
        self.advanced_layout.addRow("Processing Device:", self.device_combo)

        # Log verbosity (advanced)
        self.log_verbosity_combo = QComboBox()
        self.log_verbosity_combo.addItems(["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"])
        self.advanced_layout.addRow("Log Verbosity:", self.log_verbosity_combo)

        self.advanced_container.setVisible(False)

        # Theme selection
        self.theme_combo = QComboBox()
        self.available_themes = get_available_themes()
        self.theme_combo.addItems(self.available_themes)

        # Notes font size selection
        self.notes_font_size_combo = QComboBox()
        self.available_notes_font_sizes = get_available_notes_font_sizes()
        self.notes_font_size_combo.addItems(self.available_notes_font_sizes)
        self.notes_font_size_combo.setToolTip("Font size for meeting notes display area only")

        # LLM Provider selection
        self.llm_provider_combo = QComboBox()
        self.llm_provider_combo.addItems(["gemini", "openai", "anthropic"])
        self.llm_provider_combo.currentTextChanged.connect(self._on_llm_provider_changed)
        layout.addRow("LLM Provider:", self.llm_provider_combo)
        # Track row indices for API key fields
        self._gemini_row = layout.rowCount()
        self.gemini_api_key_edit = QLineEdit()
        self.gemini_api_key_edit.setPlaceholderText("Enter your Google Gemini API key")
        self.gemini_api_key_edit.setEchoMode(QLineEdit.Password)
        self.gemini_api_key_reveal = QPushButton("Show/Hide")
        self.gemini_api_key_reveal.setCheckable(True)
        self.gemini_api_key_reveal.setToolTip("Show/Hide API Key")
        self.gemini_api_key_reveal.toggled.connect(lambda checked: self.gemini_api_key_edit.setEchoMode(QLineEdit.Normal if checked else QLineEdit.Password))
        gemini_api_layout = QHBoxLayout()
        gemini_api_layout.addWidget(self.gemini_api_key_edit)
        gemini_api_layout.addWidget(self.gemini_api_key_reveal)
        self.gemini_api_widget = QWidget()
        self.gemini_api_widget.setLayout(gemini_api_layout)
        layout.addRow("Gemini API Key:", self.gemini_api_widget)
        # Gemini Model
        self._gemini_model_row = layout.rowCount()
        self.gemini_model_edit = QLineEdit()
        self.gemini_model_edit.setPlaceholderText(f"e.g. {get_default_model_for_provider('gemini')}")
        layout.addRow("Gemini Model:", self.gemini_model_edit)
        self._openai_row = layout.rowCount()
        self.openai_api_key_edit = QLineEdit()
        self.openai_api_key_edit.setPlaceholderText("Enter your OpenAI API key")
        self.openai_api_key_edit.setEchoMode(QLineEdit.Password)
        self.openai_api_key_reveal = QPushButton("Show/Hide")
        self.openai_api_key_reveal.setCheckable(True)
        self.openai_api_key_reveal.setToolTip("Show/Hide API Key")
        self.openai_api_key_reveal.toggled.connect(lambda checked: self.openai_api_key_edit.setEchoMode(QLineEdit.Normal if checked else QLineEdit.Password))
        openai_api_layout = QHBoxLayout()
        openai_api_layout.addWidget(self.openai_api_key_edit)
        openai_api_layout.addWidget(self.openai_api_key_reveal)
        self.openai_api_widget = QWidget()
        self.openai_api_widget.setLayout(openai_api_layout)
        layout.addRow("OpenAI API Key:", self.openai_api_widget)
        # OpenAI Model
        self._openai_model_row = layout.rowCount()
        self.openai_model_edit = QLineEdit()
        self.openai_model_edit.setPlaceholderText(f"e.g. {get_default_model_for_provider('openai')}")
        layout.addRow("OpenAI Model:", self.openai_model_edit)
        self._anthropic_row = layout.rowCount()
        self.anthropic_api_key_edit = QLineEdit()
        self.anthropic_api_key_edit.setPlaceholderText("Enter your Anthropic API key")
        self.anthropic_api_key_edit.setEchoMode(QLineEdit.Password)
        self.anthropic_api_key_reveal = QPushButton("Show/Hide")
        self.anthropic_api_key_reveal.setCheckable(True)
        self.anthropic_api_key_reveal.setToolTip("Show/Hide API Key")
        self.anthropic_api_key_reveal.toggled.connect(lambda checked: self.anthropic_api_key_edit.setEchoMode(QLineEdit.Normal if checked else QLineEdit.Password))
        anthropic_api_layout = QHBoxLayout()
        anthropic_api_layout.addWidget(self.anthropic_api_key_edit)
        anthropic_api_layout.addWidget(self.anthropic_api_key_reveal)
        self.anthropic_api_widget = QWidget()
        self.anthropic_api_widget.setLayout(anthropic_api_layout)
        layout.addRow("Anthropic API Key:", self.anthropic_api_widget)
        # Anthropic Model
        self._anthropic_model_row = layout.rowCount()
        self.anthropic_model_edit = QLineEdit()
        self.anthropic_model_edit.setPlaceholderText(f"e.g. {get_default_model_for_provider('anthropic')}")
        layout.addRow("Anthropic Model:", self.anthropic_model_edit)
        # Hide all API key/model widgets except the selected provider
        self._on_llm_provider_changed(self.llm_provider_combo.currentText())

        # Default input device
        self.input_device_combo = QComboBox()
        self.input_devices = get_available_input_devices()
        self.input_device_combo.addItem("System Default", None)
        for idx, name in self.input_devices:
            self.input_device_combo.addItem(name, idx)

        # Default output device
        self.output_device_combo = QComboBox()
        self.output_devices = get_available_output_devices()
        self.output_device_combo.addItem("System Default", None)
        for idx, name in self.output_devices:
            self.output_device_combo.addItem(name, idx)

        # Auto-transcribe on recording finish
        self.auto_transcribe_checkbox = QCheckBox("Automatically transcribe new recordings when finished")

        # --- Advanced Toggle (at bottom) ---
        self.advanced_toggle = QCheckBox("Show Advanced Settings")
        self.advanced_toggle.setChecked(False)
        self.advanced_toggle.toggled.connect(self._toggle_advanced)

        # Dialog buttons
        self.button_box = QDialogButtonBox(QDialogButtonBox.Save | QDialogButtonBox.Cancel)
        self.button_box.accepted.connect(self._on_accept)
        self.button_box.rejected.connect(self.reject)
        
        # Center the buttons
        self.button_box.setCenterButtons(True)

        # --- UI Scaling Settings ---
        self.ui_scale_mode_combo = QComboBox()
        self.ui_scale_mode_combo.addItems(["Auto", "Manual"])
        self.ui_scale_mode_combo.setToolTip("Auto: Scale based on screen resolution, Manual: Set custom scale factor")
        self.ui_scale_mode_combo.currentTextChanged.connect(self._on_ui_scale_mode_changed)
        
        self.ui_scale_factor_widget = QWidget()
        scale_factor_layout = QHBoxLayout(self.ui_scale_factor_widget)
        scale_factor_layout.setContentsMargins(0, 0, 0, 0)
        
        self.ui_scale_factor_edit = QLineEdit()
        self.ui_scale_factor_edit.setPlaceholderText("1.0")
        self.ui_scale_factor_edit.setToolTip("Custom scale factor (0.5 - 2.0)")
        self.ui_scale_factor_edit.setValidator(QDoubleValidator(0.5, 2.0, 2))
        
        self.ui_scale_info_label = QLabel()
        self.ui_scale_info_label.setWordWrap(True)
        self.ui_scale_info_label.setStyleSheet("color: #666; font-size: 11px;")
        
        scale_factor_layout.addWidget(self.ui_scale_factor_edit)
        scale_factor_layout.addStretch()
        
        # Add all main settings first (theme, devices, transcript, etc.)
        layout.addRow("Theme:", self.theme_combo)
        layout.addRow("UI Scale Mode:", self.ui_scale_mode_combo)
        layout.addRow("Scale Factor:", self.ui_scale_factor_widget)
        layout.addRow("", self.ui_scale_info_label)
        layout.addRow("Notes Font Size:", self.notes_font_size_combo)
        layout.addRow("Default Input Device:", self.input_device_combo)
        layout.addRow("Default Output Device:", self.output_device_combo)
        layout.addRow("Auto Transcribe:", self.auto_transcribe_checkbox)

        # --- Minimum Meeting Length Setting ---
        self.min_meeting_length_combo = QComboBox()
        self.min_meeting_length_options = [
            ("1 second", 1),
            ("1 minute", 60),
            ("2 minutes", 120),
            ("5 minutes", 300),
            ("10 minutes", 600),
        ]
        for label, seconds in self.min_meeting_length_options:
            self.min_meeting_length_combo.addItem(label, seconds)
        layout.addRow("Minimum Meeting Length:", self.min_meeting_length_combo)

        # --- Update Management Section ---
        # Add version info and check updates button together
        update_container = QWidget()
        update_container_layout = QVBoxLayout(update_container)
        update_container_layout.setContentsMargins(0, 0, 0, 0)
        update_container_layout.setSpacing(8)
        
        # Version info layout
        version_layout = QHBoxLayout()
        version_layout.setContentsMargins(0, 0, 0, 0)
        
        # Import version manager here to get current version
        from nojoin.utils.version_manager import version_manager
        current_version = version_manager.get_current_version()
        
        version_label = QLabel(f"Current Version: {current_version}")
        version_label.setStyleSheet("color: #666; font-size: 11px;")
        
        self.check_updates_button = QPushButton("Check for Updates")
        self.check_updates_button.setToolTip("Check for available updates to Nojoin")
        self.check_updates_button.clicked.connect(self._check_for_updates)
        
        version_layout.addWidget(version_label)
        version_layout.addStretch()
        version_layout.addWidget(self.check_updates_button)
        
        version_widget = QWidget()
        version_widget.setLayout(version_layout)
        update_container_layout.addWidget(version_widget)
        
        layout.addRow("Updates:", update_container)

        # Now add advanced toggle and advanced container
        layout.addRow("", self.advanced_toggle)
        layout.addRow("", self.advanced_container)
        
        # --- Backup and Restore Section (moved to bottom) ---
        self.backup_button = QPushButton("Create Backup")
        self.backup_button.setToolTip("Create a backup of your data with optional audio file inclusion")
        self.backup_button.clicked.connect(self._create_backup)
        
        self.restore_button = QPushButton("Restore Backup")
        self.restore_button.setToolTip("Restore data from a backup file (non-destructive)")
        self.restore_button.clicked.connect(self._restore_backup)
        
        backup_restore_layout = QHBoxLayout()
        backup_restore_layout.addWidget(self.backup_button)
        backup_restore_layout.addWidget(self.restore_button)
        backup_restore_widget = QWidget()
        backup_restore_widget.setLayout(backup_restore_layout)
        layout.addRow("Data Management:", backup_restore_widget)
        
        # Add button box to main layout outside scroll area
        main_layout.addWidget(self.button_box)
        
        # Update button states based on recording status
        self._update_button_states()
        
        # Set maximum height to prevent dialog from getting too tall on small screens
        from nojoin.utils.ui_scale_manager import get_ui_scale_manager
        ui_scale_manager = get_ui_scale_manager()
        screen_info = ui_scale_manager.get_screen_info()
        
        # Set max height to 80% of screen height, but no less than 600px and no more than 800px
        screen_height = screen_info.get('height', 1080)
        max_height = min(max(int(screen_height * 0.8), 600), 800)
        max_height = ui_scale_manager.scale_value(max_height)
        self.setMaximumHeight(max_height)

    def showEvent(self, event):
        """Override showEvent to update button states when dialog is shown."""
        super().showEvent(event)
        self._update_button_states()

    def _toggle_advanced(self, checked):
        self.advanced_container.setVisible(checked)
        # Update the content size and let the scroll area handle the rest
        if hasattr(self, 'content_widget'):
            self.content_widget.adjustSize()
        # Ensure the dialog maintains reasonable size bounds
        self.adjustSize()

    def _on_llm_provider_changed(self, provider):
        # Hide/show both label and widget for each API key/model row
        layout = self._form_layout
        # Gemini
        gemini_label = layout.itemAt(self._gemini_row, QFormLayout.LabelRole)
        gemini_widget = layout.itemAt(self._gemini_row, QFormLayout.FieldRole)
        gemini_model_label = layout.itemAt(self._gemini_model_row, QFormLayout.LabelRole)
        gemini_model_widget = layout.itemAt(self._gemini_model_row, QFormLayout.FieldRole)
        show_gemini = provider == "gemini"
        if gemini_label: gemini_label.widget().setVisible(show_gemini)
        if gemini_widget: gemini_widget.widget().setVisible(show_gemini)
        if gemini_model_label: gemini_model_label.widget().setVisible(show_gemini)
        if gemini_model_widget: gemini_model_widget.widget().setVisible(show_gemini)
        # OpenAI
        openai_label = layout.itemAt(self._openai_row, QFormLayout.LabelRole)
        openai_widget = layout.itemAt(self._openai_row, QFormLayout.FieldRole)
        openai_model_label = layout.itemAt(self._openai_model_row, QFormLayout.LabelRole)
        openai_model_widget = layout.itemAt(self._openai_model_row, QFormLayout.FieldRole)
        show_openai = provider == "openai"
        if openai_label: openai_label.widget().setVisible(show_openai)
        if openai_widget: openai_widget.widget().setVisible(show_openai)
        if openai_model_label: openai_model_label.widget().setVisible(show_openai)
        if openai_model_widget: openai_model_widget.widget().setVisible(show_openai)
        # Anthropic
        anthropic_label = layout.itemAt(self._anthropic_row, QFormLayout.LabelRole)
        anthropic_widget = layout.itemAt(self._anthropic_row, QFormLayout.FieldRole)
        anthropic_model_label = layout.itemAt(self._anthropic_model_row, QFormLayout.LabelRole)
        anthropic_model_widget = layout.itemAt(self._anthropic_model_row, QFormLayout.FieldRole)
        show_anthropic = provider == "anthropic"
        if anthropic_label: anthropic_label.widget().setVisible(show_anthropic)
        if anthropic_widget: anthropic_widget.widget().setVisible(show_anthropic)
        if anthropic_model_label: anthropic_model_label.widget().setVisible(show_anthropic)
        if anthropic_model_widget: anthropic_model_widget.widget().setVisible(show_anthropic)

    def _on_ui_scale_mode_changed(self, mode):
        """Handle UI scale mode change."""
        is_manual = mode.lower() == "manual"
        self.ui_scale_factor_widget.setVisible(is_manual)
        
        # Update info label based on mode
        if is_manual:
            self.ui_scale_info_label.setText("Manual mode: Enter a custom scale factor between 0.5 and 2.0")
        else:
            # Get current screen info and tier
            try:
                from nojoin.utils.ui_scale_manager import get_ui_scale_manager
                scale_manager = get_ui_scale_manager()
                screen_info = scale_manager.get_screen_info()
                tier_info = scale_manager.get_tier_info()
                self.ui_scale_info_label.setText(
                    f"Auto mode: Using {tier_info['name']} scale for {screen_info['width']}x{screen_info['height']} screen"
                )
            except Exception:
                self.ui_scale_info_label.setText("Auto mode: Scale automatically determined by screen resolution")
        
        self.adjustSize()

    def _load_config(self):
        cfg = config_manager.get_all()
        self.model_size_combo.setCurrentText(cfg.get("whisper_model_size", "turbo"))
        self.device_combo.setCurrentText(cfg.get("processing_device", "cpu"))
        self.llm_provider_combo.setCurrentText(cfg.get("llm_provider", "gemini"))
        self.gemini_api_key_edit.setText(cfg.get("gemini_api_key", ""))
        self.openai_api_key_edit.setText(cfg.get("openai_api_key", ""))
        self.anthropic_api_key_edit.setText(cfg.get("anthropic_api_key", ""))
        self.gemini_model_edit.setText(cfg.get("gemini_model", get_default_model_for_provider("gemini")))
        self.openai_model_edit.setText(cfg.get("openai_model", get_default_model_for_provider("openai")))
        self.anthropic_model_edit.setText(cfg.get("anthropic_model", get_default_model_for_provider("anthropic")))
        # Set log verbosity
        log_verbosity = cfg.get("advanced", {}).get("log_verbosity", "INFO")
        self.log_verbosity_combo.setCurrentText(log_verbosity.upper())
        # Set theme
        self.theme_combo.setCurrentText(cfg.get("theme", "dark"))
        # Set notes font size
        self.notes_font_size_combo.setCurrentText(cfg.get("notes_font_size", "Medium"))
        # Set input device
        input_idx = cfg.get("default_input_device_index", None)
        if input_idx is None:
            self.input_device_combo.setCurrentIndex(0)
        else:
            for i in range(1, self.input_device_combo.count()):
                if self.input_device_combo.itemData(i) == input_idx:
                    self.input_device_combo.setCurrentIndex(i)
                    break
        # Set output device
        output_idx = cfg.get("default_output_device_index", None)
        if output_idx is None:
            self.output_device_combo.setCurrentIndex(0)
        else:
            for i in range(1, self.output_device_combo.count()):
                if self.output_device_combo.itemData(i) == output_idx:
                    self.output_device_combo.setCurrentIndex(i)
                    break
        self.auto_transcribe_checkbox.setChecked(cfg.get("auto_transcribe_on_recording_finish", True))
        # Set minimum meeting length
        min_length = cfg.get("min_meeting_length_seconds", 1)
        idx = 0  # Default to '1 second'
        for i in range(self.min_meeting_length_combo.count()):
            if self.min_meeting_length_combo.itemData(i) == min_length:
                idx = i
                break
        self.min_meeting_length_combo.setCurrentIndex(idx)
        
        # Set UI scaling settings
        ui_scale_config = cfg.get("ui_scale", {})
        scale_mode = ui_scale_config.get("mode", "auto")
        self.ui_scale_mode_combo.setCurrentText(scale_mode.capitalize())
        
        scale_factor = ui_scale_config.get("scale_factor", 1.0)
        self.ui_scale_factor_edit.setText(str(scale_factor))
        
        # Trigger UI update
        self._on_ui_scale_mode_changed(scale_mode.capitalize())

    def _on_accept(self):
        # Validate and save settings
        model_size = self.model_size_combo.currentText()
        device = self.device_combo.currentText()
        input_idx = self.input_device_combo.currentData()
        output_idx = self.output_device_combo.currentData()
        auto_transcribe = self.auto_transcribe_checkbox.isChecked()
        selected_theme = self.theme_combo.currentText()
        notes_font_size = self.notes_font_size_combo.currentText()
        log_verbosity = self.log_verbosity_combo.currentText().upper()
        llm_provider = self.llm_provider_combo.currentText()
        gemini_api_key = self.gemini_api_key_edit.text().strip()
        openai_api_key = self.openai_api_key_edit.text().strip()
        anthropic_api_key = self.anthropic_api_key_edit.text().strip()
        gemini_model = self.gemini_model_edit.text().strip()
        openai_model = self.openai_model_edit.text().strip()
        anthropic_model = self.anthropic_model_edit.text().strip()
        # Remove required API key checks; allow blank
        # Validate API key for selected provider if provided (not blank)
        logger = logging.getLogger(__name__)
        if llm_provider == "gemini" and gemini_api_key:
            if not gemini_model:
                QMessageBox.critical(self, "Model Required", "Please set a Gemini model in settings.")
                return
            try:
                from google import genai
                client = genai.Client(api_key=gemini_api_key)
                prompt = "Return only the string 200 if you can see this test message."
                response = client.models.generate_content(
                    model=gemini_model,
                    contents=prompt,
                )
                if not hasattr(response, 'text') or not response.text:
                    raise Exception("No valid response from Gemini API.")
            except Exception as e:
                logger.error(f"Gemini API validation error: {e}", exc_info=True)
                QMessageBox.critical(self, "API Key Invalid", "Google Gemini API key is invalid or unauthorized. Please check your key and model.")
                return
        if llm_provider == "openai" and openai_api_key:
            if not openai_model:
                QMessageBox.critical(self, "Model Required", "Please set an OpenAI model in settings.")
                return
            try:
                import openai
                openai.api_key = openai_api_key
                prompt = "Return only the string 200 if you can see this test message."
                response = openai.ChatCompletion.create(
                    model=openai_model,
                    messages=[{"role": "user", "content": prompt}],
                    temperature=0.0,
                    max_tokens=5
                )
                if not response or not response["choices"] or not response["choices"][0]["message"]["content"]:
                    raise Exception("No valid response from OpenAI API.")
            except Exception as e:
                logger.error(f"OpenAI API validation error: {e}", exc_info=True)
                QMessageBox.critical(self, "API Key Invalid", "OpenAI API key is invalid or unauthorized. Please check your key and model.")
                return
        if llm_provider == "anthropic" and anthropic_api_key:
            if not anthropic_model:
                QMessageBox.critical(self, "Model Required", "Please set an Anthropic model in settings.")
                return
            try:
                import anthropic
                client = anthropic.Anthropic(api_key=anthropic_api_key)
                prompt = "Return only the string 200 if you can see this test message."
                response = client.messages.create(
                    model=anthropic_model,
                    max_tokens=5,
                    messages=[{"role": "user", "content": prompt}],
                    temperature=0.0,
                )
                text = response.content[0].text if hasattr(response.content[0], 'text') else response.content[0]
                if not text:
                    raise Exception("No valid response from Anthropic API.")
            except Exception as e:
                logger.error(f"Anthropic API validation error: {e}", exc_info=True)
                QMessageBox.critical(self, "API Key Invalid", "Anthropic API key is invalid or unauthorized. Please check your key and model.")
                return
        if device == "cuda" and "cuda" not in self.available_devices:
            QMessageBox.critical(self, "CUDA Not Available", "CUDA device is not available. Please install CUDA 12.8 and compatible drivers.")
            return
        # Save to config
        config_manager.set("llm_provider", llm_provider)
        config_manager.set("gemini_api_key", gemini_api_key)
        config_manager.set("openai_api_key", openai_api_key)
        config_manager.set("anthropic_api_key", anthropic_api_key)
        config_manager.set("whisper_model_size", model_size)
        config_manager.set("processing_device", device)
        config_manager.set("default_input_device_index", input_idx)
        config_manager.set("default_output_device_index", output_idx)
        config_manager.set("auto_transcribe_on_recording_finish", auto_transcribe)
        config_manager.set("theme", selected_theme)
        config_manager.set("notes_font_size", notes_font_size)
        config_manager.set("gemini_model", gemini_model)
        config_manager.set("openai_model", openai_model)
        config_manager.set("anthropic_model", anthropic_model)
        # Save log verbosity under advanced
        cfg = config_manager.get_all()
        advanced = cfg.get("advanced", {})
        advanced["log_verbosity"] = log_verbosity
        config_manager.set("advanced", advanced)
        # Save minimum meeting length
        min_length = self.min_meeting_length_combo.currentData()
        config_manager.set("min_meeting_length_seconds", min_length)
        
        # Save UI scaling settings
        ui_scale_mode = self.ui_scale_mode_combo.currentText().lower()
        ui_scale_factor = 1.0
        
        if ui_scale_mode == "manual":
            try:
                ui_scale_factor = float(self.ui_scale_factor_edit.text() or "1.0")
                if not (0.5 <= ui_scale_factor <= 2.0):
                    QMessageBox.critical(self, "Invalid Scale Factor", "Scale factor must be between 0.5 and 2.0")
                    return
            except ValueError:
                QMessageBox.critical(self, "Invalid Scale Factor", "Please enter a valid number for scale factor")
                return
        
        ui_scale_config = {
            "mode": ui_scale_mode,
            "scale_factor": ui_scale_factor
        }
        config_manager.set("ui_scale", ui_scale_config)
        
        # Apply UI scaling changes
        try:
            from nojoin.utils.ui_scale_manager import get_ui_scale_manager
            scale_manager = get_ui_scale_manager()
            if ui_scale_mode == "manual":
                scale_manager.set_user_override(ui_scale_factor)
            else:
                scale_manager.set_user_override(None)
                scale_manager.refresh_screen_detection()
        except Exception as e:
            logger.error(f"Failed to apply UI scaling changes: {e}")
        
        self.settings_saved.emit()
        self.accept()
    
    def _ask_include_audio(self):
        """
        Ask user if they want to include audio files in the backup.
        
        Returns:
            True if user wants to include audio, False if not, None if cancelled
        """
        msg_box = QMessageBox(self)
        msg_box.setWindowTitle("Backup Options")
        msg_box.setText("Do you want to include audio recordings in your backup?")
        msg_box.setInformativeText(
            "Including audio files will make the backup significantly larger "
            "but allows complete restoration of all your recordings.\n\n"
            "Without audio files, only transcripts, notes, and metadata will be backed up."
        )
        
        # Add custom buttons
        include_button = msg_box.addButton("Include Audio", QMessageBox.YesRole)
        skip_button = msg_box.addButton("Skip Audio", QMessageBox.NoRole)
        cancel_button = msg_box.addButton("Cancel", QMessageBox.RejectRole)
        
        msg_box.setDefaultButton(skip_button)  # Default to skip audio
        msg_box.exec()
        
        clicked_button = msg_box.clickedButton()
        if clicked_button == include_button:
            return True
        elif clicked_button == skip_button:
            return False
        else:  # Cancel
            return None
    
    def _create_backup(self):
        """Handle backup creation with progress dialog."""
        # Ask user if they want to include audio files
        include_audio = self._ask_include_audio()
        if include_audio is None:  # User cancelled
            return
        
        # Get backup file path from user
        default_filename = get_default_backup_filename()
        backup_path, _ = QFileDialog.getSaveFileName(
            self,
            "Create Backup",
            default_filename,
            "Zip Files (*.zip);;All Files (*)"
        )
        
        if not backup_path:
            return
        
        # Create progress dialog
        progress = QProgressDialog("Creating backup...", "Cancel", 0, 100, self)
        progress.setWindowModality(Qt.WindowModal)
        progress.setAutoClose(True)
        progress.setAutoReset(True)
        progress.show()
        
        def progress_callback(percentage, message):
            if percentage < 0:  # Error case
                progress.close()
                QMessageBox.critical(self, "Backup Failed", message)
                return
            progress.setValue(percentage)
            progress.setLabelText(message)
            if progress.wasCanceled():
                return
        
        # Create backup
        backup_manager = BackupRestoreManager()
        success = backup_manager.create_backup(backup_path, include_audio, progress_callback)
        
        progress.close()
        
        if success:
            backup_type = "complete backup (with audio)" if include_audio else "database backup (without audio)"
            QMessageBox.information(
                self, 
                "Backup Complete", 
                f"Your {backup_type} was created successfully at:\n{backup_path}"
            )
        else:
            QMessageBox.critical(
                self, 
                "Backup Failed", 
                "Failed to create backup. Check the logs for details."
            )
    
    def _restore_backup(self):
        """Handle backup restoration with progress dialog."""
        # Get backup file from user
        backup_path, _ = QFileDialog.getOpenFileName(
            self,
            "Select Backup File",
            "",
            "Zip Files (*.zip);;All Files (*)"
        )
        
        if not backup_path:
            return
        
        # Confirm restoration
        reply = QMessageBox.question(
            self,
            "Restore Backup",
            "This will restore data from the backup file and merge it with your existing data.\n\n"
            "Existing recordings and notes will not be overwritten.\n\n"
            "Continue with restore?",
            QMessageBox.Yes | QMessageBox.No,
            QMessageBox.No
        )
        
        if reply != QMessageBox.Yes:
            return
        
        # Create progress dialog
        progress = QProgressDialog("Restoring backup...", "Cancel", 0, 100, self)
        progress.setWindowModality(Qt.WindowModal)
        progress.setAutoClose(True)
        progress.setAutoReset(True)
        progress.show()
        
        def progress_callback(percentage, message):
            if percentage < 0:  # Error case
                progress.close()
                QMessageBox.critical(self, "Restore Failed", message)
                return
            progress.setValue(percentage)
            progress.setLabelText(message)
            if progress.wasCanceled():
                return
        
        # Restore backup
        backup_manager = BackupRestoreManager()
        success = backup_manager.restore_backup(backup_path, progress_callback)
        
        progress.close()
        
        if success:
            QMessageBox.information(
                self, 
                "Restore Complete", 
                "Backup restored successfully! You may need to restart the application to see all changes."
            )
        else:
            QMessageBox.critical(
                self, 
                "Restore Failed", 
                "Failed to restore backup. Check the logs for details."
            )
    
    def _check_for_updates(self):
        """Handle check for updates button click."""
        try:
            dialog = CheckForUpdatesDialog(self)
            dialog.exec()
        except Exception as e:
            logger.error(f"Error checking for updates: {e}")
            QMessageBox.critical(
                self,
                "Update Check Failed",
                f"Failed to check for updates:\n{str(e)}"
            ) 