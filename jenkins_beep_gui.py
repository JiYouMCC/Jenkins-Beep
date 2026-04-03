"""
jenkins_beep_gui.py - Qt desktop GUI for Jenkins Beep Monitor.

Usage:
    python jenkins_beep_gui.py
"""

import sys
import time
import threading
import platform
import subprocess
import requests

_PLATFORM = platform.system()

if _PLATFORM == "Windows":
    import winsound

from PyQt5.QtWidgets import (
    QApplication, QMainWindow, QWidget, QVBoxLayout, QHBoxLayout,
    QLabel, QLineEdit, QPushButton, QSpinBox, QTextEdit,
    QGroupBox, QFormLayout, QSizePolicy, QListWidget, QListWidgetItem,
    QToolButton, QCheckBox,
)
from PyQt5.QtCore import Qt, QThread, pyqtSignal, QPropertyAnimation, QEasingCurve
from PyQt5.QtGui import QFont, QColor, QTextCursor

from jenkins_beep import (
    JENKINS_TOKEN_DEFAULT_USERNAME,
    JENKINS_TOKEN,
    JENKINS_DEFAULT_ROOT_URL,
    SUCCESS_MSG_TEMPLATE,
    FAILURE_MSG_TEMPLATE,
    RUNNING_MSG_TEMPLATE,
    WAITING_MSG_TEMPLATE,
    WAITING_ENABLED_DEFAULT,
    WAITING_INTERVAL_MIN_DEFAULT,
    _CFG_VOICE,
    fetch, get_last_build, get_build,
    beep_success, beep_failure, beep_running, beep_waiting,
    configure_voice, list_voices, save_config,
    _fmt_duration, _speak,
)


# ── Collapsible section widget ─────────────────────────────────────────────────

class CollapsibleBox(QWidget):
    """A titled section that can be collapsed/expanded by clicking the header."""

    def __init__(self, title: str, collapsed: bool = False, parent=None):
        super().__init__(parent)
        self._btn = QToolButton()
        self._btn.setCheckable(True)
        self._btn.setChecked(not collapsed)
        self._btn.setText(f"  {title}")
        self._btn.setToolButtonStyle(Qt.ToolButtonTextBesideIcon)
        self._btn.setArrowType(Qt.DownArrow if not collapsed else Qt.RightArrow)
        self._btn.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        self._btn.setStyleSheet(
            "QToolButton { font-weight: bold; border: none; "
            "padding: 4px 2px; text-align: left; }"
        )
        self._btn.clicked.connect(self._toggle)

        self._content = QWidget()
        self._content.setVisible(not collapsed)

        vbox = QVBoxLayout(self)
        vbox.setSpacing(2)
        vbox.setContentsMargins(0, 0, 0, 0)
        vbox.addWidget(self._btn)
        vbox.addWidget(self._content)

    def set_content_layout(self, layout):
        self._content.setLayout(layout)

    def _toggle(self, checked: bool):
        self._btn.setArrowType(Qt.DownArrow if checked else Qt.RightArrow)
        self._content.setVisible(checked)


# ── Worker thread ──────────────────────────────────────────────────────────────

class MonitorWorker(QThread):
    log     = pyqtSignal(str, str, str)  # (message, level, job_url)
    done    = pyqtSignal(bool, str)      # (success, job_url)
    stopped = pyqtSignal(str)            # job_url

    def __init__(self, job_url, job_name, auth, build_number, interval,
                 success_msg, fail_msg, running_msg, waiting_msg,
                 waiting_enabled, waiting_interval_min):
        super().__init__()
        self.job_url             = job_url
        self.job_name            = job_name
        self.auth                = auth
        self.build_number        = build_number
        self.interval            = interval
        self.success_msg         = success_msg
        self.fail_msg            = fail_msg
        self.running_msg         = running_msg
        self.waiting_msg         = waiting_msg
        self.waiting_enabled     = waiting_enabled
        self.waiting_interval_min = waiting_interval_min
        self._stop               = False

    def stop(self):
        self._stop = True

    def _log(self, msg, level="info"):
        self.log.emit(f"[{self.job_name}] {msg}", level, self.job_url)

    def run(self):
        job_url  = self.job_url
        auth     = self.auth
        interval = self.interval

        watched_number  = None
        waiting_for_new = False

        if self.build_number:
            watched_number = self.build_number
            self._log(f"📌 Targeting build #{watched_number}")
        else:
            try:
                info = get_last_build(job_url, auth)
            except Exception as e:
                self._log(f"❌ Cannot reach Jenkins: {e}", "err")
                self.stopped.emit(self.job_url)
                return

            number   = info.get("number")
            building = info.get("building", False)

            if building:
                watched_number = number
                self._log(f"⏳ Build #{number} already running — watching it...")
                beep_running(self.job_name, self.running_msg)
            else:
                result = info.get("result", "?")
                self._log(f"💤 Last build #{number} already finished ({result}).")
                self._log("   Waiting for the next build to start...")
                waiting_for_new = True
                watched_number  = number

        tick_counter = 0
        ticks_per_reminder = max(1, (self.waiting_interval_min * 60) // interval)
        while not self._stop:
            time.sleep(interval)
            if self._stop:
                break
            tick_counter += 1

            try:
                if waiting_for_new:
                    info = get_last_build(job_url, auth)
                    if info["number"] != watched_number:
                        watched_number  = info["number"]
                        waiting_for_new = False
                        if info.get("building"):
                            self._log(f"⏳ New build #{watched_number} started — watching...")
                            beep_running(self.job_name, self.running_msg)
                        else:
                            self._finish(info)
                            return
                    else:
                        if tick_counter % ticks_per_reminder == 0:
                            self._log(f"   ... still waiting (last: #{watched_number})")
                            if self.waiting_enabled:
                                beep_waiting(self.job_name, self.waiting_msg)
                else:
                    info = get_build(job_url, watched_number, auth)
                    if info.get("building"):
                        elapsed_s = int(time.time() * 1000 - info.get("timestamp", 0)) // 1000
                        elapsed   = _fmt_duration(elapsed_s) if elapsed_s > 0 else "?"
                        self._log(f"   ⏳ Build #{watched_number} still running ({elapsed} elapsed)...")
                    else:
                        self._finish(info)
                        return

            except Exception as e:
                self._log(f"⚠️  Poll error (will retry): {e}", "warn")

        self.stopped.emit(self.job_url)

    def _finish(self, info):
        number   = info.get("number", "?")
        result   = info.get("result", "UNKNOWN")
        duration = _fmt_duration(info.get("duration", 0) // 1000)
        url      = info.get("url", "")

        if result == "SUCCESS":
            self._log(f"✅  Build #{number} SUCCESS  ({duration})", "ok")
            if url:
                self._log(f"    {url}", "ok")
            beep_success(self.job_name, self.success_msg)
            self.done.emit(True, self.job_url)
        else:
            self._log(f"❌  Build #{number} {result}  ({duration})", "err")
            if url:
                self._log(f"    {url}", "err")
            beep_failure(self.job_name, self.fail_msg)
            self.done.emit(False, self.job_url)


# ── Main window ────────────────────────────────────────────────────────────────

# Status icons shown in the job list
_STATUS = {
    "idle":    "⏸",
    "waiting": "💤",
    "running": "⏳",
    "success": "✅",
    "failed":  "❌",
    "stopped": "⏹",
}

class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("🔔 Jenkins Beep Monitor")
        self.setMinimumWidth(620)
        self.workers: dict[str, MonitorWorker] = {}   # job_url → worker
        self.job_items: dict[str, QListWidgetItem] = {}  # job_url → list item
        self._build_ui()

    def _build_ui(self):
        root = QWidget()
        self.setCentralWidget(root)
        layout = QVBoxLayout(root)
        layout.setSpacing(6)
        layout.setContentsMargins(14, 14, 14, 14)

        # ── Jobs list (always visible) ──
        jobs_group = QGroupBox("Jobs to monitor")
        jobs_vbox  = QVBoxLayout(jobs_group)

        self.job_list = QListWidget()
        self.job_list.setFixedHeight(110)
        self.job_list.setFont(QFont("Consolas", 9))
        jobs_vbox.addWidget(self.job_list)

        add_row = QHBoxLayout()
        self.job_input = QLineEdit()
        self.job_input.setPlaceholderText("my-job  or  folder/my-job  or  full URL")
        self.job_input.returnPressed.connect(self._add_job)
        add_btn    = QPushButton("＋ Add")
        remove_btn = QPushButton("－ Remove")
        add_btn.clicked.connect(self._add_job)
        remove_btn.clicked.connect(self._remove_job)
        add_row.addWidget(self.job_input)
        add_row.addWidget(add_btn)
        add_row.addWidget(remove_btn)
        jobs_vbox.addLayout(add_row)
        layout.addWidget(jobs_group)

        # ── Connection (collapsible) ──
        conn_box  = CollapsibleBox("Connection", collapsed=False)
        conn_form = QFormLayout()
        conn_form.setContentsMargins(4, 4, 4, 8)
        self.root_edit  = QLineEdit(JENKINS_DEFAULT_ROOT_URL)
        self.user_edit  = QLineEdit(JENKINS_TOKEN_DEFAULT_USERNAME)
        self.token_edit = QLineEdit(JENKINS_TOKEN)
        self.token_edit.setEchoMode(QLineEdit.Password)
        conn_form.addRow("Root URL:", self.root_edit)
        conn_form.addRow("Username:", self.user_edit)
        conn_form.addRow("API Token:", self.token_edit)
        conn_box.set_content_layout(conn_form)
        layout.addWidget(conn_box)

        # ── Options (collapsible) ──
        opt_box  = CollapsibleBox("Options", collapsed=False)
        opt_form = QFormLayout()
        opt_form.setContentsMargins(4, 4, 4, 8)

        self.interval_spin = QSpinBox()
        self.interval_spin.setRange(3, 300)
        self.interval_spin.setValue(10)
        self.interval_spin.setSuffix(" s")

        self.build_edit = QLineEdit()
        self.build_edit.setPlaceholderText("leave blank for latest")

        self.running_msg_edit = QLineEdit(RUNNING_MSG_TEMPLATE)
        self.waiting_msg_edit = QLineEdit(WAITING_MSG_TEMPLATE)

        # Waiting reminder controls
        waiting_ctrl_row = QHBoxLayout()
        waiting_ctrl_row.setContentsMargins(0, 0, 0, 0)
        self.waiting_enabled_chk = QCheckBox("Enable")
        self.waiting_enabled_chk.setChecked(WAITING_ENABLED_DEFAULT)
        self.waiting_interval_spin = QSpinBox()
        self.waiting_interval_spin.setRange(1, 120)
        self.waiting_interval_spin.setValue(WAITING_INTERVAL_MIN_DEFAULT)
        self.waiting_interval_spin.setSuffix(" min")
        self.waiting_interval_spin.setFixedWidth(80)
        waiting_ctrl_row.addWidget(self.waiting_enabled_chk)
        waiting_ctrl_row.addSpacing(12)
        waiting_ctrl_row.addWidget(QLabel("every"))
        waiting_ctrl_row.addWidget(self.waiting_interval_spin)
        waiting_ctrl_row.addStretch()
        waiting_ctrl_widget = QWidget()
        waiting_ctrl_widget.setLayout(waiting_ctrl_row)

        # Grey out message field when disabled
        def _sync_waiting_state(enabled):
            self.waiting_msg_edit.setEnabled(enabled)
            self.waiting_interval_spin.setEnabled(enabled)
        self.waiting_enabled_chk.toggled.connect(_sync_waiting_state)
        _sync_waiting_state(WAITING_ENABLED_DEFAULT)

        self.success_msg_edit = QLineEdit(SUCCESS_MSG_TEMPLATE)
        self.fail_msg_edit    = QLineEdit(FAILURE_MSG_TEMPLATE)

        self.voice_edit = QLineEdit(_CFG_VOICE)
        self.voice_edit.setPlaceholderText("e.g. huihui  ting-ting  zira  (blank = system default)")
        voice_row = QHBoxLayout()
        voice_row.setContentsMargins(0, 0, 0, 0)
        voice_row.addWidget(self.voice_edit)
        list_voices_btn = QPushButton("List…")
        list_voices_btn.setFixedWidth(54)
        list_voices_btn.clicked.connect(self._show_voices)
        voice_row.addWidget(list_voices_btn)
        voice_widget = QWidget()
        voice_widget.setLayout(voice_row)

        opt_form.addRow("Poll interval:", self.interval_spin)
        opt_form.addRow("Specific build #:", self.build_edit)
        opt_form.addRow("Running message:", self.running_msg_edit)
        opt_form.addRow("Waiting message:", self.waiting_msg_edit)
        opt_form.addRow("Waiting reminder:", waiting_ctrl_widget)
        opt_form.addRow("Success message:", self.success_msg_edit)
        opt_form.addRow("Fail message:", self.fail_msg_edit)
        opt_form.addRow("TTS voice:", voice_widget)
        opt_box.set_content_layout(opt_form)
        layout.addWidget(opt_box)

        # ── Buttons ──
        btn_row = QHBoxLayout()
        self.start_btn     = QPushButton("▶  Start All")
        self.stop_btn      = QPushButton("⏹  Stop All")
        self.clear_btn     = QPushButton("🗑  Clear log")
        self.test_ok_btn   = QPushButton("🔊 Test ✅")
        self.test_fail_btn = QPushButton("🔊 Test ❌")
        self.stop_btn.setEnabled(False)
        self.start_btn.clicked.connect(self._start_all)
        self.stop_btn.clicked.connect(self._stop_all)
        self.test_ok_btn.clicked.connect(self._test_success)
        self.test_fail_btn.clicked.connect(self._test_failure)
        btn_row.addWidget(self.start_btn)
        btn_row.addWidget(self.stop_btn)
        btn_row.addStretch()
        btn_row.addWidget(self.test_ok_btn)
        btn_row.addWidget(self.test_fail_btn)
        btn_row.addWidget(self.clear_btn)
        layout.addLayout(btn_row)

        # ── Log (collapsible) ──
        log_box_section = CollapsibleBox("Log", collapsed=False)
        log_inner = QVBoxLayout()
        log_inner.setContentsMargins(0, 0, 0, 0)
        self.log_box = QTextEdit()
        self.log_box.setReadOnly(True)
        self.log_box.setFont(QFont("Consolas", 9))
        self.log_box.setMinimumHeight(180)
        log_inner.addWidget(self.log_box)
        log_box_section.set_content_layout(log_inner)
        layout.addWidget(log_box_section)

        self.clear_btn.clicked.connect(self.log_box.clear)

        # Auto-save message/voice fields to ini on edit
        for field, key in [
            (self.running_msg_edit, "running_msg"),
            (self.waiting_msg_edit, "waiting_msg"),
            (self.success_msg_edit, "success_msg"),
            (self.fail_msg_edit,    "failure_msg"),
            (self.voice_edit,       "voice"),
        ]:
            field.editingFinished.connect(
                lambda f=field, k=key: self._save_field_to_ini(k, f.text().strip())
            )
        self.waiting_enabled_chk.toggled.connect(
            lambda v: self._save_field_to_ini("waiting_enabled", str(v).lower())
        )
        self.waiting_interval_spin.valueChanged.connect(
            lambda v: self._save_field_to_ini("waiting_interval_min", str(v))
        )

    # ── Job list management ──

    def _resolve_url(self, job: str) -> tuple[str, str]:
        """Return (url, display_name) for a job string."""
        if job.startswith("http://") or job.startswith("https://"):
            url  = job.rstrip("/")
            name = url.rstrip("/").split("/")[-1]
        else:
            job_path = "/job/".join(job.strip("/").split("/"))
            url  = f"{self.root_edit.text().rstrip('/')}/job/{job_path}"
            name = job.strip("/").split("/")[-1]
        return url, name

    def _add_job(self):
        text = self.job_input.text().strip()
        if not text:
            return
        url, name = self._resolve_url(text)
        if url in self.job_items:
            self._append_log(f"⚠️  Already added: {name}", "warn")
            return
        item = QListWidgetItem(f"{_STATUS['idle']} {name}")
        item.setData(Qt.UserRole, url)
        self.job_list.addItem(item)
        self.job_items[url] = item
        self.job_input.clear()
        # Auto-start if monitoring is already running
        if self.workers:
            self._start_job(url, name)

    def _remove_job(self):
        selected = self.job_list.selectedItems()
        if not selected:
            return
        for item in selected:
            url = item.data(Qt.UserRole)
            if url in self.workers:
                self._append_log(f"⚠️  Stop the monitor before removing a running job.", "warn")
                continue
            row = self.job_list.row(item)
            self.job_list.takeItem(row)
            self.job_items.pop(url, None)

    def _set_item_status(self, job_url: str, status: str):
        item = self.job_items.get(job_url)
        if item:
            name = item.text().split(" ", 1)[1] if " " in item.text() else item.text()
            item.setText(f"{_STATUS.get(status, '?')} {name}")

    # ── Logging ──

    def _save_field_to_ini(self, key: str, value: str):
        try:
            save_config(**{key: value})
            self._append_log(f"💾 Saved {key} = {value!r}", "info")
        except Exception as e:
            self._append_log(f"⚠️  Could not save to ini: {e}", "warn")

    def _append_log(self, msg, level="info"):
        colors = {"info": "#cccccc", "ok": "#6fcf7a", "err": "#f07070", "warn": "#f0c060"}
        color  = colors.get(level, "#cccccc")
        ts     = time.strftime("%H:%M:%S")
        html   = f'<span style="color:#777">[{ts}]</span> <span style="color:{color}">{msg}</span>'
        self.log_box.append(html)
        self.log_box.moveCursor(QTextCursor.End)

    # ── Control ──

    def _test_success(self):
        configure_voice(self.voice_edit.text().strip())
        msg = self.success_msg_edit.text().strip() or SUCCESS_MSG_TEMPLATE
        threading.Thread(target=beep_success, args=("test", msg), daemon=True).start()

    def _test_failure(self):
        configure_voice(self.voice_edit.text().strip())
        msg = self.fail_msg_edit.text().strip() or FAILURE_MSG_TEMPLATE
        threading.Thread(target=beep_failure, args=("test", msg), daemon=True).start()

    def _show_voices(self):
        voices = list_voices()
        if not voices:
            self._append_log("⚠️  pyttsx3 not installed — no voices available.", "warn")
            return
        self._append_log("🎙 Available TTS voices:", "info")
        for _, vname in voices:
            self._append_log(f"   {vname}", "info")

    def _start_job(self, job_url: str, job_name: str):
        """Start a single MonitorWorker using the current UI settings."""
        if job_url in self.workers:
            return
        configure_voice(self.voice_edit.text().strip())
        auth         = (self.user_edit.text().strip(), self.token_edit.text().strip())
        build_number = int(self.build_edit.text()) if self.build_edit.text().strip().isdigit() else None
        interval     = self.interval_spin.value()
        success_msg  = self.success_msg_edit.text().strip() or SUCCESS_MSG_TEMPLATE
        fail_msg     = self.fail_msg_edit.text().strip()    or FAILURE_MSG_TEMPLATE
        running_msg  = self.running_msg_edit.text().strip() or RUNNING_MSG_TEMPLATE
        waiting_msg      = self.waiting_msg_edit.text().strip() or WAITING_MSG_TEMPLATE
        waiting_enabled  = self.waiting_enabled_chk.isChecked()
        waiting_interval = self.waiting_interval_spin.value()

        self._append_log(f"🔔 Starting monitor: {job_url}", "info")
        worker = MonitorWorker(job_url, job_name, auth, build_number, interval,
                               success_msg, fail_msg, running_msg, waiting_msg,
                               waiting_enabled, waiting_interval)
        worker.log.connect(self._on_worker_log)
        worker.done.connect(self._on_done)
        worker.stopped.connect(self._on_stopped)
        worker.start()
        self.workers[job_url] = worker
        self._set_item_status(job_url, "waiting")
        self._refresh_buttons()

    def _start_all(self):
        if self.job_list.count() == 0:
            self._append_log("⚠️  Add at least one job first.", "warn")
            return
        for i in range(self.job_list.count()):
            item    = self.job_list.item(i)
            job_url = item.data(Qt.UserRole)
            _, job_name = self._resolve_url(job_url)
            self._start_job(job_url, job_name)

    def _stop_all(self):
        for worker in list(self.workers.values()):
            worker.stop()
        self._append_log("👋 Stopping all monitors...", "warn")

    def _refresh_buttons(self):
        any_running = bool(self.workers)
        self.start_btn.setEnabled(True)   # always enabled; skips already-running jobs
        self.stop_btn.setEnabled(any_running)

    # ── Slots ──

    def _on_worker_log(self, msg, level, job_url):
        # Update item status hint from log message
        if "still running" in msg or "watching" in msg:
            self._set_item_status(job_url, "running")
        elif "Waiting for the next build" in msg:
            self._set_item_status(job_url, "waiting")
        self._append_log(msg, level)

    def _on_done(self, success: bool, job_url: str):
        self._set_item_status(job_url, "success" if success else "failed")
        self.workers.pop(job_url, None)
        self._refresh_buttons()

    def _on_stopped(self, job_url: str):
        self._set_item_status(job_url, "stopped")
        self._append_log(f"⏹ Stopped: {job_url.split('/')[-1]}", "warn")
        self.workers.pop(job_url, None)
        self._refresh_buttons()


# ── Entry point ────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    app = QApplication(sys.argv)
    app.setStyle("Fusion")

    # Dark palette
    from PyQt5.QtGui import QPalette
    palette = QPalette()
    palette.setColor(QPalette.Window,          QColor(45, 45, 48))
    palette.setColor(QPalette.WindowText,      QColor(220, 220, 220))
    palette.setColor(QPalette.Base,            QColor(30, 30, 30))
    palette.setColor(QPalette.AlternateBase,   QColor(45, 45, 48))
    palette.setColor(QPalette.ToolTipBase,     QColor(220, 220, 220))
    palette.setColor(QPalette.ToolTipText,     QColor(220, 220, 220))
    palette.setColor(QPalette.Text,            QColor(220, 220, 220))
    palette.setColor(QPalette.Button,          QColor(60, 60, 65))
    palette.setColor(QPalette.ButtonText,      QColor(220, 220, 220))
    palette.setColor(QPalette.BrightText,      Qt.red)
    palette.setColor(QPalette.Highlight,       QColor(0, 120, 215))
    palette.setColor(QPalette.HighlightedText, Qt.white)
    app.setPalette(palette)

    win = MainWindow()
    win.show()
    sys.exit(app.exec_())
