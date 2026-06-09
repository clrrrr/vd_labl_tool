"""Annotate window — video player + 工序名 / 物品列表 form.

Layout: horizontal splitter with a video player on the left and the
annotation form on the right. The Save button does NOT block — clicking it
emits ``save_requested`` and immediately closes the dialog. The actual
file write (which can take 10+ seconds on slow disks / network drives)
runs in the FileListView's background save pool, with a persistent
warning banner visible while it's in flight.
"""

from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import (
    Qt,
    QTimer,
    QUrl,
    Signal,
)
from PySide6.QtGui import QKeyEvent
from PySide6.QtMultimedia import QAudioOutput, QMediaPlayer
from PySide6.QtMultimediaWidgets import QVideoWidget
from PySide6.QtWidgets import (
    QApplication,
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
from .metadata import (
    Annotation,
    MetadataError,
    parse_filename_pattern,
    read_annotation,
)


# --- Window -----------------------------------------------------------------

class AnnotateWindow(QDialog):
    """Modal annotation editor for one video.

    Doesn't write the file itself — emits ``save_requested(path, annotation)``
    so the file list view can run the actual ffmpeg call on a background
    thread. The dialog closes immediately on save so the user can move on.
    """

    SPEED_OPTIONS = [("0.5x", 0.5), ("1.0x", 1.0), ("1.5x", 1.5), ("2.0x", 2.0)]

    save_requested = Signal(Path, object)  # path, Annotation

    def __init__(
        self,
        video_path: Path,
        parent: QWidget | None = None,
        *,
        prefilled_parts: list[str] | None = None,
        get_next_video_callback=None,
    ) -> None:
        super().__init__(parent)
        self._video_path = video_path
        self._user_dragging = False
        self._prefilled_parts = list(prefilled_parts) if prefilled_parts else []
        self._seek_step_ms = 5000  # 默认跳跃5秒
        self._get_next_video_callback = get_next_video_callback
        self._pending_save_path = None  # 记录正在保存的视频路径

        self.setWindowTitle(S.ANNO_TITLE_TEMPLATE.format(filename=video_path.name))
        self.resize(1100, 650)

        self._build_ui()
        self._load_existing_annotation()
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

        # Saving status banner
        self.lbl_saving = QLabel("")
        self.lbl_saving.setStyleSheet(
            "color: #b00020; font-weight: bold; padding: 4px 6px; "
            "background-color: #fff4f4; border: 1px solid #f5c2c7; border-radius: 4px;"
        )
        self.lbl_saving.setVisible(False)
        root.addWidget(self.lbl_saving)

        bottom = QHBoxLayout()
        bottom.addStretch(1)
        self.btn_cancel = QPushButton(S.ANNO_BTN_CANCEL)
        self.btn_cancel.clicked.connect(self.reject)
        self.btn_save = QPushButton(S.ANNO_BTN_SAVE)
        self.btn_save.clicked.connect(self._on_save_clicked)
        self.btn_next = QPushButton("下一个(保存当前)")
        self.btn_next.clicked.connect(self._on_save_and_next_clicked)
        bottom.addWidget(self.btn_cancel)
        bottom.addWidget(self.btn_save)
        bottom.addWidget(self.btn_next)
        root.addLayout(bottom)

    def _build_player_pane(self) -> QWidget:
        w = QWidget()
        v = QVBoxLayout(w)
        v.setContentsMargins(0, 0, 0, 0)

        self.video_widget = QVideoWidget()
        self.video_widget.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        # Black background so the area looks like a player even before load.
        self.video_widget.setStyleSheet("background-color: black;")
        # Allow video widget to receive focus so keyboard shortcuts work
        self.video_widget.setFocusPolicy(Qt.ClickFocus)
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
        self.cmb_speed.setCurrentIndex(2)  # 1.5x
        self.cmb_speed.currentIndexChanged.connect(self._on_speed_changed)
        ctrls.addWidget(self.cmb_speed)

        ctrls.addSpacing(12)
        self.lbl_seek_step = QLabel("跳5秒")
        ctrls.addWidget(self.lbl_seek_step)
        self.slider_seek_step = QSlider(Qt.Horizontal)
        self.slider_seek_step.setRange(0, 5)  # 6个档位: 3,5,10,20,30,40秒
        self.slider_seek_step.setValue(1)  # 默认5秒
        self.slider_seek_step.setFixedWidth(120)
        self.slider_seek_step.valueChanged.connect(self._on_seek_step_changed)
        ctrls.addWidget(self.slider_seek_step)

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
        self.edt_process.returnPressed.connect(lambda: self.edt_part.setFocus())
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
        """Populate form fields.

        v0.5 default:
        - 工序名: derived from the filename suffix ({factory_id}_{NNNNN}_{suffix})
          if present. If JSON already has a process_name and the filename
          doesn't, fall back to the JSON value (rare).
        - 物品列表: from the JSON annotation if present, otherwise the
          ``prefilled_parts`` the caller may have supplied (right-click paste
          flow onto an un-annotated video).
        """
        try:
            existing = read_annotation(self._video_path)
        except MetadataError:
            existing = None

        # Process name: extract text after the first underscore in filename.
        # Examples: "00001_安装_镜头" -> "安装_镜头", "factory_安装镜头" -> "安装镜头"
        # If no underscore, leave empty; JSON fallback if no filename suffix.
        stem = self._video_path.stem
        process_from_name = stem.split('_', 1)[1] if '_' in stem else ""
        if process_from_name:
            self.edt_process.setText(process_from_name)
        elif existing is not None and existing.process_name:
            self.edt_process.setText(existing.process_name)

        # Parts list: JSON wins; prefilled is the fallback for un-annotated
        # videos.
        if existing is not None:
            for p in existing.parts_involved:
                self.lst_parts.addItem(QListWidgetItem(p))
        else:
            for p in self._prefilled_parts:
                self.lst_parts.addItem(QListWidgetItem(p))

    def _load_video(self) -> None:
        self.player.setSource(QUrl.fromLocalFile(str(self._video_path)))

    def _reload_video_and_form(self, new_path: Path) -> None:
        """Reload the window with a new video without closing it."""
        # Stop and release current video
        self.player.stop()
        self.player.setSource(QUrl())
        QApplication.processEvents()

        # Update path and window title
        self._video_path = new_path
        self.setWindowTitle(S.ANNO_TITLE_TEMPLATE.format(filename=new_path.name))

        # Clear form
        self.edt_process.clear()
        self.lst_parts.clear()

        # Reload annotation and video
        self._load_existing_annotation()
        self._load_video()

    def on_save_completed(self, saved_path: Path) -> None:
        """Called by parent when a save operation completes."""
        if self._pending_save_path == saved_path:
            self._pending_save_path = None
            self.lbl_saving.setVisible(False)

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

    def _on_seek_step_changed(self, index: int) -> None:
        # 滑块档位对应的跳跃秒数: [3, 5, 10, 20, 30, 40]
        steps = [3, 5, 10, 20, 30, 40]
        if 0 <= index < len(steps):
            seconds = steps[index]
            self._seek_step_ms = seconds * 1000
            self.lbl_seek_step.setText(f"跳{seconds}秒")

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
        # Set focus back to the input field so space key works for play/pause
        self.edt_part.setFocus()

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

        # 3. Hand the annotation off to the background save queue and close
        #    the dialog. The actual file write happens elsewhere — if it
        #    fails, the file list view surfaces the error.
        self.save_requested.emit(self._video_path, annotation)
        self.accept()

    def _on_save_and_next_clicked(self) -> None:
        # Same validation as _on_save_clicked
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

        # Get next video path before releasing player
        if not self._get_next_video_callback:
            QMessageBox.warning(self, S.APP_TITLE, "无法获取下一个视频")
            return
        next_path = self._get_next_video_callback(self._video_path)

        self._release_player()

        # Show saving banner
        self._pending_save_path = self._video_path
        self.lbl_saving.setText(f"正在保存: {self._video_path.name} - 请勿关闭窗口")
        self.lbl_saving.setVisible(True)

        # Emit save request
        self.save_requested.emit(self._video_path, annotation)

        # Load next video or show message
        if next_path:
            self._reload_video_and_form(next_path)
            # Set focus to process name field to avoid space key triggering the button
            self.edt_process.setFocus()
        else:
            QMessageBox.information(self, S.APP_TITLE, "已经是最后一个视频了")
            self.accept()

    def _release_player(self) -> None:
        self.player.stop()
        self.player.setSource(QUrl())
        # Let Qt and the underlying media backend process the source change.
        QApplication.processEvents()

    # --- Window lifecycle ---------------------------------------------------

    def closeEvent(self, event):  # noqa: N802 — Qt API
        # Save is async + decoupled, so closing is always safe.
        self.player.stop()
        self.player.setSource(QUrl())
        super().closeEvent(event)

    def keyPressEvent(self, event: QKeyEvent) -> None:  # noqa: N802 — Qt API
        # Left/Right arrow keys seek backward/forward (higher priority)
        # Don't handle if focus is in the parts list (needs arrows for navigation)
        focus = self.focusWidget()
        if event.key() == Qt.Key_Left and focus != self.lst_parts:
            current = self.player.position()
            new_pos = max(0, current - self._seek_step_ms)
            self.player.setPosition(new_pos)
            event.accept()
            return
        elif event.key() == Qt.Key_Right and focus != self.lst_parts:
            current = self.player.position()
            duration = self.player.duration()
            new_pos = min(duration, current + self._seek_step_ms)
            self.player.setPosition(new_pos)
            event.accept()
            return
        # Don't let Enter inside a text field close the dialog.
        elif event.key() in (Qt.Key_Return, Qt.Key_Enter):
            if isinstance(focus, QLineEdit):
                event.accept()
                return
        # Space key toggles play/pause
        elif event.key() == Qt.Key_Space:
            self._on_play_clicked()
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
