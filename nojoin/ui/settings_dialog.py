import os
from PySide6.QtWidgets import (
    QDialog, QFormLayout, QComboBox, QLineEdit, QPushButton, QDialogButtonBox, QLabel, QFileDialog, QCheckBox, QMessageBox, QWidget, QHBoxLayout
)
from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QPalette, QColor
from nojoin.utils.config_manager import (
    config_manager,
    get_available_whisper_model_sizes,
    get_available_processing_devices,
    get_available_input_devices,
    get_available_output_devices,
    get_available_themes,
    get_available_notes_font_sizes
)
from nojoin.utils.theme_utils import apply_theme_to_widget
import logging

class SettingsDialog(QDialog):
    # Signal emitted when settings (potentially theme) are saved
    settings_saved = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Settings")
        self.setMinimumWidth(420)
        self.setModal(True)
        self._init_ui()
        self._load_config()
        # Apply theme on construction (ensure after UI setup)
        apply_theme_to_widget(self, config_manager.get("theme", "dark"))

    def _init_ui(self):
        layout = QFormLayout()
        self.setLayout(layout)
        layout.setLabelAlignment(Qt.AlignRight)
        layout.setFormAlignment(Qt.AlignHCenter | Qt.AlignTop)
        layout.setSpacing(14)
        self._form_layout = layout  # Store reference for label/widget hiding

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
        self.gemini_model_edit.setPlaceholderText("e.g. gemini-2.5-flash-preview-05-20")
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
        self.openai_model_edit.setPlaceholderText("e.g. gpt-4.1-mini-2025-04-14")
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
        self.anthropic_model_edit.setPlaceholderText("e.g. claude-3-7-sonnet-latest")
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

        # Add all main settings first (theme, devices, transcript, etc.)
        layout.addRow("Theme:", self.theme_combo)
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

        # Now add advanced toggle and advanced container at the bottom
        layout.addRow("", self.advanced_toggle)
        layout.addRow("", self.advanced_container)
        layout.addRow(self.button_box)

    def _toggle_advanced(self, checked):
        self.advanced_container.setVisible(checked)
        # Optionally resize dialog
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

    def _load_config(self):
        cfg = config_manager.get_all()
        self.model_size_combo.setCurrentText(cfg.get("whisper_model_size", "turbo"))
        self.device_combo.setCurrentText(cfg.get("processing_device", "cpu"))
        self.llm_provider_combo.setCurrentText(cfg.get("llm_provider", "gemini"))
        self.gemini_api_key_edit.setText(cfg.get("gemini_api_key", ""))
        self.openai_api_key_edit.setText(cfg.get("openai_api_key", ""))
        self.anthropic_api_key_edit.setText(cfg.get("anthropic_api_key", ""))
        self.gemini_model_edit.setText(cfg.get("gemini_model", "gemini-2.5-flash-preview-05-20"))
        self.openai_model_edit.setText(cfg.get("openai_model", "gpt-4.1-mini-2025-04-14"))
        self.anthropic_model_edit.setText(cfg.get("anthropic_model", "claude-3-7-sonnet-latest"))
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
        self.auto_transcribe_checkbox.setChecked(cfg.get("auto_transcribe_on_recording_finish", False))
        # Set minimum meeting length
        min_length = cfg.get("min_meeting_length_seconds", 1)
        idx = 0  # Default to '1 second'
        for i in range(self.min_meeting_length_combo.count()):
            if self.min_meeting_length_combo.itemData(i) == min_length:
                idx = i
                break
        self.min_meeting_length_combo.setCurrentIndex(idx)

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
        self.settings_saved.emit()
        self.accept() 