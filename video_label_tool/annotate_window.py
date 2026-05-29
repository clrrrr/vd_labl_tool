"""Annotate window — video player + Process Name / Parts Involved form.

Layout: horizontal splitter with a video player on the left and the annotation
form on the right. Saving runs in a background QThread so the UI never blocks
on ffmpeg, and the file handle held by QMediaPlayer is released before ffmpeg
rewrites the file (required on Windows where the rename would otherwise fail).
"""

from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import (
    QObject,
    Qt,
    QThread,
    QTimer,
    QUrl,
    Signal,
)
from PySide6.QtGui import QKeyEvent
from PySide6.QtMultimedia import QAudioOutput, QMediaPlayer
from PySide6.QtMultimediaWidgets import QVideoWidget
from PySide6.QtWidgets import (
    QComboBox,
    QDialog,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMessageBox,
    QPushButton,
    QSizePolicy,
    QSlider,
    QSplitter,
    QVBoxLayout,
    QWidget,
)

from . import ui_strings as S
from .metadata import Annotation, MetadataError, read_annotation, write_annotation


# --- Worker -----------------------------------------------------------------

class _SaveWorker(QObject):
    """Runs the metadata write on a worker thread."""

    finished = Signal(object)  # None on success, Exception on failure

    def __init__(self, video_path: Path, annotation: Annotation) -> None:
        super().__init__()
        self._video_path = video_path
        self._annotation = annotation

    def run(self) -> None:
        err: Exception | None = None
        try:
            write_annotation(self._video_path, self._annotation)
        except Exception as e:  # noqa: BLE001 — we report any failure to the UI
            err = e
        self.finished.emit(err)


# --- Window -----------------------------------------------------------------

class AnnotateWindow(QDialog):
    """Modal annotation editor for one video."""

    SPEED_OPTIONS = [("0.5x", 0.5), ("1.0x", 1.0), ("1.5x", 1.5), ("2.0x", 2.0)]

    def __init__(self, video_path: Path, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._video_path = video_path
        self._save_thread: QThread | None = None
        self._save_worker: _SaveWorker | None = None
        self._user_dragging = False

        self.setWindowTitle(S.ANNO_TITLE_TEMPLATE.format(filename=video_path.name))
        self.resize(1100, 650)

        self._build_ui()
        self._load_existing_annotation()
        # Defer setting source until after the window is shown so the video
        # widget has a valid native handle on every platform.
        QTimer.singleShot(0, self._load_video)

    # --- UI construction ----------------------------------------------------

    def _build_ui(self) -> None:
        splitter = QSplitter(Qt.Horizontal, self)

        splitter.addWidget(self._build_player_pane())
        splitter.addWidget(self._build_form_pane())
        splitter.setStretchFactor(0, 7)
        splitter.setStretchFactor(1, 3)
        splitter.setSizes([760, 340])

        root = QVBoxLayout(self)
        root.setContentsMargins(8, 8, 8, 8)
        root.addWidget(splitter, 1)

        bottom = QHBoxLayout()
        self.lbl_save_status = QLabel("")
        self.lbl_save_status.setStyleSheet("color: #555;")
        bottom.addWidget(self.lbl_save_status, 1)
        self.btn_cancel = QPushButton(S.ANNO_BTN_CANCEL)
        self.btn_cancel.clicked.connect(self.reject)
        self.btn_save = QPushButton(S.ANNO_BTN_SAVE)
        self.btn_save.setDefault(True)
        self.btn_save.clicked.connect(self._on_save_clicked)
        bottom.addWidget(self.btn_cancel)
        bottom.addWidget(self.btn_save)
        root.addLayout(bottom)

    def _build_player_pane(self) -> QWidget:
        w = QWidget()
        v = QVBoxLayout(w)
        v.setContentsMargins(0, 0, 0, 0)

        self.video_widget = QVideoWidget()
        self.video_widget.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        # Black background so the area looks like a player even before load.
        self.video_widget.setStyleSheet("background-color: black;")
        v.addWidget(self.video_widget, 1)

        # Seek row: current time | slider | total time
        seek = QHBoxLayout()
        self.lbl_time = QLabel("00:00")
        self.lbl_time.setMinimumWidth(48)
        self.lbl_duration = QLabel("00:00")
        self.lbl_duration.setMinimumWidth(48)
        self.slider = QSlider(Qt.Horizontal)
        self.slider.setRange(0, 0)
        self.slider.sliderPressed.connect(self._on_slider_pressed)
        self.slider.sliderReleased.connect(self._on_slider_released)
        self.slider.valueChanged.connect(self._on_slider_value_changed)
        seek.addWidget(self.lbl_time)
        seek.addWidget(self.slider, 1)
        seek.addWidget(self.lbl_duration)
        v.addLayout(seek)

        # Controls row: play/pause | speed | volume
        ctrls = QHBoxLayout()
        self.btn_play = QPushButton(S.PLAY_BTN_PLAY)
        self.btn_play.clicked.connect(self._on_play_clicked)
        ctrls.addWidget(self.btn_play)

        ctrls.addSpacing(12)
        ctrls.addWidget(QLabel(S.PLAY_LABEL_SPEED))
        self.cmb_speed = QComboBox()
        for label, _ in self.SPEED_OPTIONS:
            self.cmb_speed.addItem(label)
        self.cmb_speed.setCurrentIndex(1)  # 1.0x
        self.cmb_speed.currentIndexChanged.connect(self._on_speed_changed)
        ctrls.addWidget(self.cmb_speed)

        ctrls.addSpacing(12)
        ctrls.addWidget(QLabel("🔊"))
        self.slider_vol = QSlider(Qt.Horizontal)
        self.slider_vol.setRange(0, 100)
        self.slider_vol.setValue(70)
        self.slider_vol.setFixedWidth(120)
        self.slider_vol.valueChanged.connect(self._on_volume_changed)
        ctrls.addWidget(self.slider_vol)

        ctrls.addStretch(1)
        v.addLayout(ctrls)

        # Media objects
        self.audio = QAudioOutput(self)
        self.audio.setVolume(0.7)
        self.player = QMediaPlayer(self)
        self.player.setAudioOutput(self.audio)
        self.player.setVideoOutput(self.video_widget)
        self.player.positionChanged.connect(self._on_position_changed)
        self.player.durationChanged.connect(self._on_duration_changed)
        self.player.playbackStateChanged.connect(self._on_playback_state)
        self.player.errorOccurred.connect(self._on_player_error)

        return w

    def _build_form_pane(self) -> QWidget:
        w = QWidget()
        v = QVBoxLayout(w)

        v.addWidget(QLabel(S.ANNO_LABEL_PROCESS))
        self.edt_process = QLineEdit()
        self.edt_process.setMaxLength(500)
        v.addWidget(self.edt_process)

        v.addSpacing(8)
        v.addWidget(QLabel(S.ANNO_LABEL_PARTS))
        self.lst_parts = QListWidget()
        self.lst_parts.setSelectionMode(QListWidget.ExtendedSelection)
        v.addWidget(self.lst_parts, 1)

        add_row = QHBoxLayout()
        self.edt_part = QLineEdit()
        self.edt_part.setPlaceholderText(S.ANNO_PART_PLACEHOLDER)
        self.edt_part.returnPressed.connect(self._on_add_part)
        self.btn_add = QPushButton(S.ANNO_BTN_ADD)
        self.btn_add.clicked.connect(self._on_add_part)
        add_row.addWidget(self.edt_part, 1)
        add_row.addWidget(self.btn_add)
        v.addLayout(add_row)

        self.btn_remove = QPushButton(S.ANNO_BTN_REMOVE)
        self.btn_remove.clicked.connect(self._on_remove_part)
        v.addWidget(self.btn_remove)

        return w

    # --- Loading state ------------------------------------------------------

    def _load_existing_annotation(self) -> None:
        try:
            existing = read_annotation(self._video_path)
        except MetadataError:
            existing = None
        if existing is None:
            return
        self.edt_process.setText(existing.process_name)
        for p in existing.parts_involved:
            self.lst_parts.addItem(QListWidgetItem(p))

    def _load_video(self) -> None:
        self.player.setSource(QUrl.fromLocalFile(str(self._video_path)))

    # --- Player slots -------------------------------------------------------

    def _on_play_clicked(self) -> None:
        if self.player.playbackState() == QMediaPlayer.PlayingState:
            self.player.pause()
        else:
            self.player.play()

    def _on_playback_state(self, state: QMediaPlayer.PlaybackState) -> None:
        self.btn_play.setText(
            S.PLAY_BTN_PAUSE if state == QMediaPlayer.PlayingState else S.PLAY_BTN_PLAY
        )

    def _on_speed_changed(self, index: int) -> None:
        if 0 <= index < len(self.SPEED_OPTIONS):
            self.player.setPlaybackRate(self.SPEED_OPTIONS[index][1])

    def _on_volume_changed(self, value: int) -> None:
        self.audio.setVolume(value / 100.0)

    def _on_position_changed(self, pos_ms: int) -> None:
        if not self._user_dragging:
            self.slider.blockSignals(True)
            self.slider.setValue(pos_ms)
            self.slider.blockSignals(False)
        self.lbl_time.setText(_format_ms(pos_ms))

    def _on_duration_changed(self, dur_ms: int) -> None:
        self.slider.setRange(0, dur_ms)
        self.lbl_duration.setText(_format_ms(dur_ms))

    def _on_slider_pressed(self) -> None:
        self._user_dragging = True

    def _on_slider_released(self) -> None:
        self._user_dragging = False
        self.player.setPosition(self.slider.value())

    def _on_slider_value_changed(self, value: int) -> None:
        # While dragging, keep the time label live for feedback.
        if self._user_dragging:
            self.lbl_time.setText(_format_ms(value))

    def _on_player_error(self, error: QMediaPlayer.Error, msg: str) -> None:
        if error == QMediaPlayer.NoError:
            return
        # Don't blow away the form — just inform; user can still annotate
        # if they happened to watch elsewhere.
        QMessageBox.warning(self, S.APP_TITLE, f"播放器错误: {msg}")

    # --- Form slots ---------------------------------------------------------

    def _on_add_part(self) -> None:
        text = self.edt_part.text().strip()
        if not text:
            return
        # Avoid exact duplicates (common annotator mistake).
        existing = {self.lst_parts.item(i).text() for i in range(self.lst_parts.count())}
        if text in existing:
            self.edt_part.clear()
            return
        self.lst_parts.addItem(QListWidgetItem(text))
        self.edt_part.clear()
        self.edt_part.setFocus()

    def _on_remove_part(self) -> None:
        for item in self.lst_parts.selectedItems():
            self.lst_parts.takeItem(self.lst_parts.row(item))

    # --- Save flow ----------------------------------------------------------

    def _on_save_clicked(self) -> None:
        # 1. Validate
        process_name = self.edt_process.text().strip()
        if not process_name:
            QMessageBox.warning(self, S.DLG_VALIDATE_TITLE, S.DLG_VALIDATE_PROCESS_EMPTY)
            self.edt_process.setFocus()
            return
        parts = [self.lst_parts.item(i).text() for i in range(self.lst_parts.count())]
        if not parts:
            QMessageBox.warning(self, S.DLG_VALIDATE_TITLE, S.DLG_VALIDATE_PARTS_EMPTY)
            self.edt_part.setFocus()
            return
        annotation = Annotation(process_name=process_name, parts_involved=parts)

        # 2. Release the file handle held by QMediaPlayer so ffmpeg can
        #    rename over the input on Windows.
        self._release_player()

        # 3. Disable UI and kick off the worker.
        self._set_busy(True)
        self.lbl_save_status.setText("保存中…")

        # Brief delay lets MF finish releasing the file handle asynchronously
        # on Windows. Harmless on macOS/Linux.
        QTimer.singleShot(120, lambda: self._start_save_worker(annotation))

    def _start_save_worker(self, annotation: Annotation) -> None:
        thread = QThread(self)
        worker = _SaveWorker(self._video_path, annotation)
        worker.moveToThread(thread)
        thread.started.connect(worker.run)
        worker.finished.connect(self._on_save_finished)
        # Tear-down chain: worker.finished → thread.quit → thread.finished → cleanup
        worker.finished.connect(thread.quit)
        thread.finished.connect(worker.deleteLater)
        thread.finished.connect(thread.deleteLater)
        self._save_thread = thread
        self._save_worker = worker
        thread.start()

    def _on_save_finished(self, err: object) -> None:
        self._save_thread = None
        self._save_worker = None
        if err is None:
            self.lbl_save_status.setText("")
            self.accept()
            return

        # Failure: re-enable form, optionally re-load video, show error.
        self.lbl_save_status.setText("")
        self._set_busy(False)
        self._load_video()  # restore so user can keep viewing while they retry
        message = str(err) if err else "未知错误"
        QMessageBox.critical(self, S.DLG_SAVE_FAIL_TITLE, message)

    def _release_player(self) -> None:
        self.player.stop()
        self.player.setSource(QUrl())
        # Let Qt and the underlying media backend process the source change.
        from PySide6.QtWidgets import QApplication
        QApplication.processEvents()

    def _set_busy(self, busy: bool) -> None:
        for w in (
            self.btn_save, self.btn_cancel, self.btn_play, self.btn_add,
            self.btn_remove, self.cmb_speed, self.edt_process, self.edt_part,
            self.lst_parts, self.slider, self.slider_vol,
        ):
            w.setEnabled(not busy)

    # --- Window lifecycle ---------------------------------------------------

    def closeEvent(self, event):  # noqa: N802 — Qt API
        # Ensure player releases the file when the window closes (any path).
        self.player.stop()
        self.player.setSource(QUrl())
        super().closeEvent(event)

    def keyPressEvent(self, event: QKeyEvent) -> None:  # noqa: N802 — Qt API
        # Don't let Enter inside a text field close the dialog.
        if event.key() in (Qt.Key_Return, Qt.Key_Enter):
            focus = self.focusWidget()
            if isinstance(focus, QLineEdit):
                event.accept()
                return
        super().keyPressEvent(event)


def _format_ms(ms: int) -> str:
    if ms < 0:
        ms = 0
    total = ms // 1000
    h = total // 3600
    m = (total % 3600) // 60
    s = total % 60
    if h > 0:
        return f"{h:02d}:{m:02d}:{s:02d}"
    return f"{m:02d}:{s:02d}"
