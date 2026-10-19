from PySide6.QtGui import QColor, QPalette
from PySide6.QtWidgets import QApplication

ACCENT = "#ff0033"
BG = "#0f0f0f"
SURFACE = "#212121"
SURFACE_HOVER = "#3f3f3f"
TEXT = "#f1f1f1"
MUTED = "#aaaaaa"

_QSS = f"""
QWidget {{ font-family: "Segoe UI", "Roboto", sans-serif; font-size: 13px; }}
QToolTip {{ background: {SURFACE}; color: {TEXT}; border: 1px solid #3a3a3a; padding: 4px; }}

QLabel#PageTitle {{ font-size: 22px; font-weight: 700; }}
QLabel#CardTitle {{ font-size: 15px; font-weight: 600; }}
QLabel#VideoTitle {{ font-size: 19px; font-weight: 700; }}
QLabel#SectionTitle {{ font-size: 15px; font-weight: 700; }}
QLabel#Muted {{ color: {MUTED}; }}
QLabel#Thumb {{ border-top-left-radius: 12px; border-top-right-radius: 12px; color: white;
                font-size: 30px; font-weight: 800; }}

QPushButton {{ background: {SURFACE}; color: {TEXT}; border: none; border-radius: 14px;
               padding: 6px 16px; font-weight: 600; }}
QPushButton:hover {{ background: {SURFACE_HOVER}; }}
QPushButton:checked {{ background: {TEXT}; color: {BG}; }}
QPushButton:disabled {{ color: #5a5a5a; }}
QPushButton#Primary {{ background: {TEXT}; color: {BG}; border-radius: 14px; }}
QPushButton#Primary:hover {{ background: #d9d9d9; }}
QPushButton#Ghost {{ background: transparent; }}
QPushButton#Ghost:hover {{ background: {SURFACE}; }}

QLineEdit, QComboBox {{ background: {BG}; border: 1px solid #303030; border-radius: 8px; padding: 6px 10px; }}
QLineEdit:focus {{ border: 1px solid #3ea6ff; }}

QProgressBar {{ border: none; background: #3f3f3f; border-radius: 2px; }}
QProgressBar::chunk {{ background: {ACCENT}; border-radius: 2px; }}

QFrame#CourseCard {{ background: transparent; border-radius: 12px; }}
QFrame#CourseCard:hover {{ background: {SURFACE}; }}
QFrame#SidePanel {{ background: {SURFACE}; border-radius: 12px; }}
QFrame#NotesPanel {{ background: {SURFACE}; border-radius: 12px; }}
QLabel#QuizQuestion {{ font-size: 16px; font-weight: 600; }}
QLabel#QuizScore {{ font-size: 26px; font-weight: 800; }}
QLabel#QuizExplain {{ color: {MUTED}; }}
QFrame#QuizOption {{ background: {SURFACE}; border: 1px solid #303030; border-radius: 10px; }}
QFrame#QuizOption:hover {{ border: 1px solid #5a5a5a; }}
QFrame#QuizOption[state="selected"] {{ border: 1px solid #3ea6ff; background: rgba(62, 166, 255, 0.12); }}
QFrame#QuizOption[state="correct"] {{ border: 1px solid #2ba640; background: rgba(43, 166, 64, 0.18); }}
QFrame#QuizOption[state="wrong"] {{ border: 1px solid #ff4d6a; background: rgba(255, 0, 51, 0.15); }}
QLabel#QuizOptionText {{ background: transparent; font-size: 14px; }}
QPlainTextEdit#NoteInput {{ background: {BG}; border: 1px solid #303030; border-radius: 8px; padding: 2px 4px; }}
QPlainTextEdit#NoteInput:focus {{ border: 1px solid #3ea6ff; }}
QScrollArea#NotesScroll, QWidget#NotesList {{ background: transparent; }}
QFrame#NoteCard {{ background: #2b2b2b; border-radius: 10px; }}
QFrame#NoteCard:hover {{ background: #333333; }}
QLabel#NoteText {{ background: transparent; color: {TEXT}; font-size: 13px; }}
QPlainTextEdit#NoteCode {{ background: #0d0d0d; color: #e6e6e6; border: 1px solid #303030; border-radius: 8px;
                           selection-background-color: #264f78;
                           font-family: "Cascadia Mono", "Consolas", "Courier New", monospace; font-size: 13px; }}
QPlainTextEdit#NoteCode QScrollBar:horizontal {{ background: transparent; height: 8px; margin: 0 4px 2px 4px; }}
QPlainTextEdit#NoteCode QScrollBar::handle:horizontal {{ background: #4a4a4a; border-radius: 4px; min-width: 30px; }}
QPlainTextEdit#NoteCode QScrollBar::add-line:horizontal, QPlainTextEdit#NoteCode QScrollBar::sub-line:horizontal
    {{ width: 0; }}
QPushButton#NoteTime {{ background: rgba(62, 166, 255, 0.16); color: #3ea6ff; border-radius: 10px;
                        padding: 2px 10px; font-weight: 700; }}
QPushButton#NoteTime:hover {{ background: rgba(62, 166, 255, 0.3); }}
QPushButton#NoteAction, QPushButton#NoteDelete {{ background: transparent; color: {MUTED}; border-radius: 10px;
                                                  padding: 2px 10px; font-weight: 600; }}
QPushButton#NoteAction:hover {{ background: {SURFACE_HOVER}; color: {TEXT}; }}
QPushButton#NoteDelete:hover {{ background: rgba(255, 0, 51, 0.18); color: #ff4d6a; }}
QFrame#UpNext {{ background: {SURFACE}; border-radius: 12px; }}
QFrame#TopBar {{ background: {BG}; border-bottom: 1px solid #272727; }}

QTreeWidget, QListWidget {{ background: transparent; border: none; outline: 0; }}
QTreeWidget::item {{ padding: 7px 4px; }}
QTreeWidget::item:hover, QListWidget::item:hover {{ background: #303030; }}
QTreeWidget::item:selected {{ background: #3a3a3a; color: {TEXT}; }}
QTreeWidget::branch {{ background: transparent; }}
QListWidget::item {{ padding: 8px 6px; border-radius: 8px; }}
QListWidget::item:selected {{ background: #3a3a3a; color: {TEXT}; }}
QListWidget#ChapterList {{ border-bottom: 1px solid #303030; }}
QListWidget#ChapterList::item {{ padding: 5px 6px; font-weight: 600; }}

QScrollBar:vertical {{ background: transparent; width: 10px; margin: 0; }}
QScrollBar::handle:vertical {{ background: #4a4a4a; border-radius: 5px; min-height: 30px; }}
QScrollBar::add-line, QScrollBar::sub-line {{ height: 0; }}
QScrollBar::add-page, QScrollBar::sub-page {{ background: transparent; }}
QSplitter::handle {{ background: transparent; }}

QFrame#OverlayPill {{ background: rgba(0, 0, 0, 0.55); border-radius: 22px; }}
QFrame#UpNextCard {{ background: rgba(18, 18, 18, 0.94); border: 1px solid rgba(255, 255, 255, 0.14);
                     border-radius: 14px; }}
QFrame#UpNextCard QLabel {{ background: transparent; }}
QLabel#UpNextThumb {{ border-radius: 8px; }}
QLabel#UpNextKicker {{ color: {ACCENT}; font-size: 11px; font-weight: 800; }}
QLabel#UpNextTitle {{ color: {TEXT}; font-size: 16px; font-weight: 700; }}
QPushButton#PlayCircle {{ background: rgba(0, 0, 0, 0.55); border-radius: 22px; padding: 0; }}
QPushButton#PlayCircle:hover {{ background: rgba(60, 60, 60, 0.85); }}
QPushButton#OverlayButton {{ background: transparent; border-radius: 18px; padding: 0; }}
QPushButton#OverlayButton:hover {{ background: rgba(255, 255, 255, 0.15); }}
QPushButton#OverlayButton:checked {{ background: rgba(255, 255, 255, 0.25); }}
QLabel#PlayerTime {{ color: {TEXT}; font-weight: 600; background: transparent; }}
QLabel#OverlayStatus {{ background: rgba(0, 0, 0, 0.65); color: {TEXT}; border-radius: 10px; padding: 8px 18px;
                        font-size: 14px; font-weight: 600; }}
QFrame#SeekPreview {{ background: transparent; }}
QLabel#SeekPreviewTime {{ background: rgba(0, 0, 0, 0.7); color: {TEXT}; border-radius: 10px; padding: 3px 12px;
                          font-size: 13px; font-weight: 700; }}
QSlider#VolumeSlider::groove:horizontal {{ height: 4px; background: rgba(255, 255, 255, 0.3); border-radius: 2px; }}
QSlider#VolumeSlider::sub-page:horizontal {{ background: {TEXT}; border-radius: 2px; }}
QSlider#VolumeSlider::handle:horizontal {{ background: {TEXT}; width: 12px; height: 12px; margin: -4px 0;
                                           border-radius: 6px; }}
"""


def apply_theme(app: QApplication) -> None:
    app.setStyle("Fusion")
    palette = QPalette()
    colors = {
        QPalette.ColorRole.Window: BG,
        QPalette.ColorRole.WindowText: TEXT,
        QPalette.ColorRole.Base: BG,
        QPalette.ColorRole.AlternateBase: SURFACE,
        QPalette.ColorRole.Text: TEXT,
        QPalette.ColorRole.Button: SURFACE,
        QPalette.ColorRole.ButtonText: TEXT,
        QPalette.ColorRole.Highlight: "#3a3a3a",
        QPalette.ColorRole.HighlightedText: TEXT,
        QPalette.ColorRole.ToolTipBase: SURFACE,
        QPalette.ColorRole.ToolTipText: TEXT,
        QPalette.ColorRole.PlaceholderText: "#717171",
        QPalette.ColorRole.Link: "#3ea6ff",
    }
    for role, color in colors.items():
        palette.setColor(role, QColor(color))
    for role in (QPalette.ColorRole.Text, QPalette.ColorRole.ButtonText, QPalette.ColorRole.WindowText):
        palette.setColor(QPalette.ColorGroup.Disabled, role, QColor("#5a5a5a"))
    app.setPalette(palette)
    app.setStyleSheet(_QSS)
