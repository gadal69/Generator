"""Native, local-first operations dashboard built with QtPy."""
from __future__ import annotations

import json
import os
import sys
import tempfile
import time
from pathlib import Path

from qtpy import QtCore, QtGui, QtWidgets

from app.chief.approval_gate import ApprovalGate
from app.models.adapter_factory import create_local_adapter
from app.models.fake_multi_agent_adapter import FakeMultiAgentAdapter
from app.models.ollama_adapter import OllamaAdapter
from app.orchestrator.orchestrator import Orchestrator
from app.storage.database import Database
from app.tools.project_file_tool import ProjectFileTool
from app.utils.path_manager import PathManager
from app.runtime.run_control import RunControl, RunCancelled, ControlledAdapter
from app.runtime.checkpoints import Checkpoints


ROOT = Path(__file__).resolve().parents[2]
Signal = QtCore.Signal

COLORS = {
    "canvas": "#09111f", "panel": "#111d31", "raised": "#1a2942",
    "line": "#2a3c58", "text": "#f3f7fc", "muted": "#9fb0c7",
    "teal": "#45dfc4", "blue": "#78adff", "amber": "#f5bc58",
    "red": "#ff727b", "green": "#52d39a",
}

PAGE_SIDE_MARGIN = 22
PAGE_TOP_MARGIN = 16


class CenteredRuleTabBar(QtWidgets.QTabBar):
    """Draw a strong horizontal rule from the last tab toward the right edge."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setDrawBase(False)
        self.model_selector = None
        self.engine_selector = None

    def set_selectors(self, engine_selector, model_selector):
        self.engine_selector = engine_selector
        self.model_selector = model_selector
        for selector, width in ((engine_selector, 130), (model_selector, 190)):
            selector.setParent(self)
            selector.setFixedWidth(width)
            selector.setFixedHeight(34)
            selector.show()
        self._place_model_selector()

    def _place_model_selector(self):
        if not self.engine_selector or not self.model_selector or self.count() == 0:
            return
        last_tab = self.tabRect(self.count() - 1)
        y = max(0, (self.height() - self.model_selector.height()) // 2)
        gap = last_tab.width() * 2
        engine_x = last_tab.right() + 1 + gap
        self.engine_selector.move(engine_x, y)
        self.model_selector.move(engine_x + self.engine_selector.width() + 8, y)
        self.engine_selector.raise_()
        self.model_selector.raise_()

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self._place_model_selector()

    def paintEvent(self, event):
        super().paintEvent(event)
        if self.count() < 2:
            return
        selected_tab = self.tabRect(self.currentIndex())
        approval_tab = self.tabRect(self.count() - 1)
        painter = QtGui.QPainter(self)
        painter.setRenderHint(QtGui.QPainter.RenderHint.Antialiasing)
        # Remove the style-drawn hairline to the right of the tabs, then redraw
        # the requested green rule at the approval tab's center height.
        painter.fillRect(
            QtCore.QRect(approval_tab.right() + 1, 0, self.width() - approval_tab.right(), 3),
            QtGui.QColor(COLORS["canvas"]),
        )
        glow = QtGui.QColor("#38d6bd")
        for padding, alpha, width in ((6, 20, 5), (4, 32, 3), (2, 46, 1)):
            color = QtGui.QColor(glow)
            color.setAlpha(alpha)
            painter.setPen(QtGui.QPen(color, width))
            painter.setBrush(QtCore.Qt.BrushStyle.NoBrush)
            painter.drawRoundedRect(QtCore.QRectF(selected_tab).adjusted(-padding, -padding, padding, padding), 9 + padding, 9 + padding)
        painter.setRenderHint(QtGui.QPainter.RenderHint.Antialiasing, False)
        pen = QtGui.QPen(QtGui.QColor("#52f5a7"), 3)
        pen.setCapStyle(QtCore.Qt.PenCapStyle.FlatCap)
        painter.setPen(pen)
        painter.drawLine(approval_tab.right() + 7, approval_tab.center().y(), self.width() - 2, approval_tab.center().y())


class CenteredRuleTabWidget(QtWidgets.QTabWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setTabBar(CenteredRuleTabBar(self))

    def paintEvent(self, event):
        super().paintEvent(event)
        if self.count() < 2:
            return
        tab_bar = self.tabBar()
        approval_tab = tab_bar.tabRect(self.count() - 1)
        line_start = tab_bar.mapTo(self, QtCore.QPoint(approval_tab.right() + 1, 0)).x()
        line_y = tab_bar.mapTo(self, QtCore.QPoint(0, 0)).y()
        painter = QtGui.QPainter(self)
        painter.fillRect(
            QtCore.QRect(line_start, line_y, self.width() - line_start, 2),
            QtGui.QColor(COLORS["canvas"]),
        )


class ModelSelectorComboBox(QtWidgets.QComboBox):
    """Content-sized selectors with a fully measured, screen-bounded popup."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setStyleSheet("QComboBox { combobox-popup: 0; }")
        view = QtWidgets.QListView(self)
        view.setItemDelegate(QtWidgets.QStyledItemDelegate(view))
        view.setTextElideMode(QtCore.Qt.TextElideMode.ElideNone)
        view.setWordWrap(False)
        view.setHorizontalScrollBarPolicy(QtCore.Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        view.setVerticalScrollBarPolicy(QtCore.Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        view.setVerticalScrollMode(QtWidgets.QAbstractItemView.ScrollMode.ScrollPerPixel)
        self.setView(view)
        self.setSizeAdjustPolicy(QtWidgets.QComboBox.SizeAdjustPolicy.AdjustToContents)
        self.setMaxVisibleItems(1000)
        self.model().dataChanged.connect(self._update_content_width)
        self.model().rowsInserted.connect(self._update_content_width)
        self.model().rowsRemoved.connect(self._update_content_width)

    def _content_width(self):
        metrics = self.fontMetrics()
        return max((metrics.horizontalAdvance(self.itemText(i)) for i in range(self.count())), default=0)

    def sizeHint(self):
        hint = super().sizeHint()
        # Reserve the existing text padding, arrow area and border separately.
        hint.setWidth(max(hint.width(), self._content_width() + 64))
        return hint

    def minimumSizeHint(self):
        return self.sizeHint()

    def _update_content_width(self, *_):
        self.updateGeometry()

    def changeEvent(self, event):
        super().changeEvent(event)
        if event.type() in (QtCore.QEvent.Type.FontChange, QtCore.QEvent.Type.StyleChange):
            self._update_content_width()

    def showPopup(self):
        self.setMaxVisibleItems(max(1, self.count()))
        self.view().doItemsLayout()
        super().showPopup()
        self._style_and_position_popup()

    def _style_and_position_popup(self):
        popup = self.view().window()
        screen = QtGui.QGuiApplication.screenAt(self.mapToGlobal(self.rect().center())) or self.screen()
        if not popup or not screen:
            return

        popup.setObjectName("comboPopup")
        popup.setContentsMargins(0, 0, 0, 0)
        if popup.layout():
            popup.layout().setContentsMargins(1, 1, 1, 1)
            popup.layout().setSpacing(0)
        popup.setStyleSheet(
            "QWidget#comboPopup { background: #10192c; "
            "border: 1px solid #34445e; border-radius: 6px; }"
        )
        self.view().setFrameShape(QtWidgets.QFrame.Shape.NoFrame)
        self.view().setContentsMargins(0, 0, 0, 0)
        self.view().doItemsLayout()

        available = screen.availableGeometry()
        origin = self.mapToGlobal(QtCore.QPoint(0, self.height()))
        row_heights = [self.view().sizeHintForRow(index) for index in range(self.count())]
        margins = self.view().contentsMargins()
        desired_height = sum(max(height, self.fontMetrics().height() + 12) for height in row_heights)
        desired_height += margins.top() + margins.bottom() + 16
        width = max(self.width(), self._content_width() + 64, self.view().sizeHintForColumn(0) + 16)
        oversized = width > available.width()
        self.view().setHorizontalScrollBarPolicy(
            QtCore.Qt.ScrollBarPolicy.ScrollBarAsNeeded if oversized
            else QtCore.Qt.ScrollBarPolicy.ScrollBarAlwaysOff
        )
        if oversized:
            desired_height += self.style().pixelMetric(QtWidgets.QStyle.PixelMetric.PM_ScrollBarExtent)
        top = self.mapToGlobal(QtCore.QPoint(0, 0)).y()
        below = max(0, available.bottom() - origin.y() + 1)
        above = max(0, top - available.top())
        if desired_height <= below:
            y, height = origin.y(), desired_height
        elif desired_height <= above:
            y, height = top - desired_height, desired_height
        else:
            # Use the whole screen before resorting to scrolling on small displays.
            height = min(desired_height, available.height())
            y = min(max(available.top(), origin.y()), available.bottom() - height + 1)
        width = min(width, available.width())
        x = min(max(origin.x(), available.left()), available.right() - width + 1)
        popup.setGeometry(x, y, width, height)
        self.view().scrollTo(self.model().index(self.currentIndex(), self.modelColumn()))


def make_database() -> Database:
    paths = PathManager(ROOT / "config" / "app_config.json")
    paths.ensure_directories()
    return Database(str(paths.database / "itni_ai_team.db"))


def rows(db: Database, sql: str, args=()):
    conn = db.connect()
    try:
        return conn.execute(sql, args).fetchall()
    finally:
        conn.close()


class RunWorker(QtCore.QThread):
    succeeded = Signal(dict)
    failed = Signal(str)
    progress = Signal(dict)

    def __init__(self, request: str, model: str, qa_mode: str, approval_mode: str, images=None, mode="execute", analysis_override=None, gemini_review=False, gemini_code_review=False, cloud_approval_ids=None, resume_project_id=None, parent=None):
        super().__init__(parent)
        self.request = request
        self.model = model
        self.qa_mode = qa_mode
        self.approval_mode = approval_mode
        self.images = list(images or [])
        self.mode = mode
        self.analysis_override = analysis_override
        self.gemini_review = gemini_review
        self.gemini_code_review = gemini_code_review
        self.cloud_approval_ids = cloud_approval_ids or {}
        self.resume_project_id = resume_project_id
        self.control = RunControl(self.progress.emit)

    def request_cancel(self):
        self.control.cancel()

    def run(self):
        try:
            if self.mode == "health":
                adapter = OllamaAdapter(model=self.model)
                data = adapter.health()
                models = [item.get("name") for item in data.get("models", []) if item.get("name")]
                self.succeeded.emit({
                    "mode": "health", "base_url": adapter.base_url, "model": self.model,
                    "models": models, "model_ready": self.model in models,
                    "selectable_models": [item['name'] for item in data.get('models', []) if item.get('name') and (
                        not item.get('capabilities') or 'completion' in item['capabilities'])],
                })
                return
            if self.mode == "fake":
                temp_parent = ROOT / "data" / "temp"
                temp_parent.mkdir(parents=True, exist_ok=True)
                with tempfile.TemporaryDirectory(prefix="itni_gui_selftest_", dir=temp_parent) as temp_dir:
                    temp_root = Path(temp_dir)
                    orchestrator = Orchestrator(Database(str(temp_root / "selftest.db")))
                    orchestrator.project_file_tool = ProjectFileTool(temp_root)
                    orchestrator.tool_registry.register(
                        "write_project_file", orchestrator.project_file_tool.write_file,
                        "Write self-test files only inside the temporary workspace.",
                    )
                    FakeMultiAgentAdapter.proposal_calls = 0
                    result = orchestrator.run_user_request(
                        self.request, model_override="offline-self-test", qa_mode="agent",
                        approval_mode="auto", max_reworks=1, verbose=False,
                        adapter_factory=lambda model: FakeMultiAgentAdapter(model),
                        control=self.control,
                    )
                    result["mode"] = "fake"
                    result["temporary_workspace"] = True
                    self.succeeded.emit(result)
                return
            request = self.request
            original_request = request
            image_summary = ""
            if self.mode == 'resume':
                _,saved=Checkpoints(make_database()).load(self.resume_project_id)
                request=saved['request']
                self.gemini_code_review=saved.get('gemini_code_review',False)
                self.cloud_approval_ids=saved.get('cloud_approval_ids',{})
            if self.images:
                adapter = ControlledAdapter(create_local_adapter(model=self.model),self.control)
                self.control.emit('images')
                image_manifest_lines = []
                for image_path in self.images:
                    reader = QtGui.QImageReader(image_path)
                    image_format = reader.format().data().decode("ascii", errors="ignore").upper()
                    dimensions = reader.size()
                    image_manifest_lines.append(
                        f"- {Path(image_path).name} (파일 형식: {image_format or Path(image_path).suffix.lower().lstrip('.') or '알 수 없음'}, "
                        f"크기: {dimensions.width()} × {dimensions.height()} px)"
                    )
                image_manifest = "\n".join(image_manifest_lines)
                image_summary = adapter.generate(
                    "첨부 이미지와 아래 정보를 함께 검토하세요. 이미지별 실제 파일 형식과 크기를 기록하고, 보이는 문서 종류·주요 내용·읽을 수 있는 텍스트를 요약하세요. 사용자의 텍스트 요구사항과 이미지의 관계, 요구사항에 반영할 조건도 정리하세요. 보이지 않거나 불확실한 내용은 추측하지 마세요.\n\n사용자 텍스트 요구사항:\n"
                    + (original_request or "텍스트 요구사항 없이 첨부 이미지 내용을 분석합니다.")
                    + "\n\n파일 정보:\n" + image_manifest,
                    images=self.images,
                )
                request = (
                    "[대표자 텍스트 요구사항]\n"
                    + (original_request.strip() or "텍스트 요구사항 없음")
                    + "\n\n[첨부 이미지 VLM 분석]\n"
                    + image_summary
                )
            orchestrator = Orchestrator(make_database())
            if self.mode == "analyze":
                decision = orchestrator.team_runner.analyze_user_request(
                    request, model_override=self.model, gemini_review=self.gemini_review,control=self.control
                )
                self.succeeded.emit({"mode": "analyze", "request": original_request, "chief_decision": decision, "image_analysis": image_summary, "gemini_review": self.gemini_review, "gemini_code_review": self.gemini_code_review})
                return
            result = orchestrator.run_user_request(
                request, model_override=self.model, qa_mode=self.qa_mode,
                approval_mode=self.approval_mode, verbose=False, analysis_override=self.analysis_override,
                gemini_code_review=self.gemini_code_review,
                cloud_approval_ids=self.cloud_approval_ids,resume_project_id=self.resume_project_id,
                control=self.control,resume=self.mode=='resume',
                max_reworks=saved.get('max_reworks') if self.mode=='resume' else None,
            )
            self.succeeded.emit(result)
        except RunCancelled:
            self.succeeded.emit({'status':'CANCELLED','mode':self.mode})
        except Exception as exc:
            self.failed.emit(str(exc))


class RequirementInput(QtWidgets.QTextEdit):
    files_dropped = Signal(list)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setAcceptDrops(True)
        self.setMinimumHeight(220)

    def dragEnterEvent(self, event):
        if event.mimeData().hasUrls():
            event.acceptProposedAction()
        else:
            super().dragEnterEvent(event)

    def dropEvent(self, event):
        if event.mimeData().hasUrls():
            paths = [url.toLocalFile() for url in event.mimeData().urls() if url.isLocalFile()]
            if paths:
                self.files_dropped.emit(paths)
                event.acceptProposedAction()
                return
        super().dropEvent(event)


class MetricCard(QtWidgets.QFrame):
    def __init__(self, title: str, value: str = "—", caption: str = "", accent: str = "teal"):
        super().__init__()
        self.setObjectName("metricCard")
        self.accent = accent
        layout = QtWidgets.QVBoxLayout(self)
        layout.setContentsMargins(18, 16, 18, 16)
        layout.setSpacing(8)
        self.title = QtWidgets.QLabel(title)
        self.value = QtWidgets.QLabel(value)
        self.value.setObjectName("metricValue")
        self.caption = QtWidgets.QLabel(caption)
        self.caption.setObjectName("muted")
        layout.addWidget(self.title)
        layout.addWidget(self.value)
        layout.addWidget(self.caption)
        self.setMinimumHeight(122)
        self.setStyleSheet(f"QFrame#metricCard {{ border-top: 3px solid {COLORS[accent]}; }}")

    def set_value(self, value: str, caption: str | None = None):
        self.value.setText(str(value))
        if caption is not None:
            self.caption.setText(caption)


class Dashboard(QtWidgets.QMainWindow):
    def __init__(self):
        super().__init__()
        self.db = make_database()
        self.worker = None
        self.last_result = None
        self.pending_analysis = None
        self.analysis_request = None
        self.approval_process = None
        self._selected_approval_key = None
        config = json.loads((ROOT / "config" / "app_config.json").read_text(encoding="utf-8"))
        configured_model = config["ai"].get("default_model", "gemma4:12b")
        self.engine_name = str(config["ai"].get("provider", "ollama")).lower()
        if self.engine_name not in {"ollama", "vllm"}:
            self.engine_name = "ollama"
        vllm_model = config.get("ai", {}).get("vllm", {}).get("default_model")
        options = ["gemma4:12b", "gemma4:e2b"]
        if configured_model and configured_model not in options:
            options.append(configured_model)
        if vllm_model and vllm_model not in options:
            options.append(vllm_model)
        self.model_options = options
        self.model_name = configured_model or "gemma4:12b"
        self._approval_details = {}
        self.setWindowTitle("ITNI AI Team | Operations")
        self.setMinimumSize(1120, 760)
        self.resize(1460, 920)
        self.setStyleSheet(self.stylesheet())
        self._build_ui()
        self.refresh_data()
        self.refresh_timer = QtCore.QTimer(self)
        self.refresh_timer.timeout.connect(self.refresh_data)
        self.refresh_timer.start(8000)

    @staticmethod
    def stylesheet():
        c = COLORS
        return f"""
        QWidget {{ background: {c['canvas']}; color: {c['text']}; font-family: 'Malgun Gothic'; font-size: 10pt; }}
        QMainWindow {{ background: {c['canvas']}; }}
        QFrame#panel, QFrame#metricCard {{ background: {c['panel']}; border: 1px solid {c['line']}; border-radius: 14px; }}
        QFrame#controlBar {{ background: #0e1b2d; border: 1px solid #263a56; border-radius: 12px; }}
        QWidget#approvalActions {{ background: transparent; }}
        QLabel {{ background: transparent; }}
        QLabel#pageTitle {{ font-size: 22pt; font-weight: 700; }}
        QLabel#appTitle {{ font-family: 'Arial'; font-size: 22pt; font-weight: 900; letter-spacing: 1px; color: #ffffff; }}
        QLabel#metricValue {{ font-size: 25pt; font-weight: 700; }}
        QLabel#muted {{ color: {c['muted']}; }}
        QLabel#copyright {{ color: {c['muted']}; font-size: 8pt; }}
        QLabel#sectionTitle {{ font-size: 12pt; font-weight: 650; }}
        QLabel#statusPill {{ background: #172943; color: #b9cbe3; border: 1px solid #30496b; border-radius: 10px; padding: 7px 11px; }}
        QLabel#eyebrow {{ color: #62e6cf; font-size: 8pt; font-weight: 700; letter-spacing: 1px; }}
        QLabel#fieldLabel {{ color: #91a5bf; font-size: 9pt; font-weight: 600; }}
        QTabWidget::pane {{ border: none; background: {c['canvas']}; }}
        QWidget#dashboardTabPage, QScrollArea#dashboardTabScroll, QWidget#dashboardTabContent {{ background: #0c1827; }}
        QTabBar {{ border: none; background: transparent; }}
        QTabBar::tab {{ background: transparent; color: {c['muted']}; padding: 12px 20px; margin-right: 5px; border: 1px solid transparent; border-radius: 9px; }}
        QTabBar::tab:hover {{ background: #14243b; color: {c['text']}; }}
        QTabBar::tab:selected {{ background: #18354a; color: #75f0d9; border: 1px solid #2b6570; }}
        QPushButton {{ background: {c['raised']}; border: 1px solid {c['line']}; border-radius: 9px; padding: 9px 15px; font-weight: 600; }}
        QPushButton:hover {{ background: #213553; border-color: {c['teal']}; }}
        QPushButton:pressed {{ background: #13243b; }}
        QPushButton:disabled {{ background: #111c2c; color: #61728a; border-color: #223149; }}
        QPushButton#primary {{ background: #24383a; color: {c['text']}; border: 1px solid #3d5d5b; font-weight: 700; }}
        QPushButton#primary:hover {{ background: #2d4849; border-color: #5d8580; }}
        QPushButton#primary:disabled {{ background: #192431; color: #8291a5; border-color: #2a394b; }}
        QPushButton#danger {{ background: #2b252f; color: {c['text']}; border-color: #4a3d4c; }}
        QPushButton#danger:hover {{ background: #3a2d39; border-color: #725364; }}
        QPushButton#warning {{ background: #2d2b26; color: {c['text']}; border-color: #504a3b; }}
        QPushButton#warning:hover {{ background: #3b382e; border-color: #74694e; }}
        QPushButton#warning:disabled {{ background: #192431; color: #8291a5; border-color: #2a394b; }}
        QPushButton#requestAction {{ background: #173a4a; color: #c9fff4; border: 1px solid #2d7b7e; font-weight: 700; }}
        QPushButton#requestAction:hover {{ background: #205365; border-color: #51e2c9; }}
        QPushButton#utility {{ background: #182b3d; color: {c['text']}; border-color: #35506a; }}
        QPushButton#utility:hover {{ background: #203b50; border-color: #56809d; }}
        QLineEdit, QTextEdit, QPlainTextEdit, QListWidget {{ background: #0b1728; border: 1px solid {c['line']}; border-radius: 9px; padding: 9px; selection-background-color: #256c73; }}
        QLineEdit:focus, QTextEdit:focus, QPlainTextEdit:focus, QListWidget:focus, QComboBox:focus {{ border: 1px solid {c['teal']}; }}
        QComboBox {{
            background: #0d182b;
            color: #42c9ff;
            border: 1px solid #168bc8;
            border-radius: 5px;
            padding: 7px 34px 7px 12px;
            min-height: 20px;
            font-weight: 700;
            selection-background-color: #182942;
        }}
        QComboBox:hover {{ border-color: #32baf5; background: #101d32; }}
        QComboBox:focus, QComboBox:on {{ border: 1px solid #32baf5; background: #101d32; }}
        QComboBox::drop-down {{
            width: 30px;
            border: 0;
            border-left: 1px solid #172b47;
            background: #0b1628;
            border-top-right-radius: 5px;
            border-bottom-right-radius: 5px;
        }}
        QComboBox QAbstractItemView {{
            background: #10192c;
            color: #f4f7fc;
            border: 1px solid #34445e;
            border-radius: 6px;
            padding: 2px;
            selection-background-color: #182942;
            selection-color: #ffffff;
            outline: 0;
            font-weight: 700;
        }}
        QComboBox QAbstractItemView::item {{
            min-height: 30px;
            padding: 6px 11px;
            border: 0;
            border-left: 3px solid transparent;
        }}
        QComboBox QAbstractItemView::item:hover {{ background: #16263d; color: #ffffff; }}
        QComboBox QAbstractItemView::item:selected {{ background: #182942; color: #ffffff; border-left: 3px solid #149fe5; }}
        QCheckBox {{ background: transparent; color: #cbd7e6; spacing: 9px; padding: 4px 0; }}
        QCheckBox::indicator {{ width: 17px; height: 17px; border: 1px solid #40536f; border-radius: 5px; background: #0b1728; }}
        QCheckBox::indicator:checked {{ background: {c['teal']}; border-color: {c['teal']}; }}
        QTableWidget {{ background: {c['panel']}; alternate-background-color: #142137; border: 1px solid {c['line']}; border-radius: 8px; gridline-color: {c['line']}; }}
        QHeaderView::section {{ background: #172740; color: #adc0d8; padding: 10px; border: 0; border-right: 1px solid {c['line']}; font-weight: 700; }}
        QTableWidget::item {{ padding: 7px; }}
        QTableWidget::item:selected {{ background: #214b5d; color: #ffffff; }}
        QProgressBar {{ background: #142239; border: 0; border-radius: 4px; }}
        QProgressBar::chunk {{ background: {c['teal']}; border-radius: 4px; }}
        QScrollBar:vertical {{ background: {c['canvas']}; width: 10px; }}
        QScrollBar::handle:vertical {{ background: #344760; border-radius: 5px; min-height: 25px; }}
        QToolTip {{ background: {c['raised']}; color: {c['text']}; border: 1px solid {c['line']}; padding: 6px; }}
        """

    def _build_ui(self):
        shell = QtWidgets.QWidget()
        root = QtWidgets.QHBoxLayout(shell)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)
        self.setCentralWidget(shell)

        self.tabs = QtWidgets.QTabWidget()

        content = QtWidgets.QWidget()
        content_layout = QtWidgets.QVBoxLayout(content)
        content_layout.setContentsMargins(26, 20, 26, 22)
        content_layout.setSpacing(14)
        header = QtWidgets.QHBoxLayout()
        titlebox = QtWidgets.QVBoxLayout()
        eyebrow = QtWidgets.QLabel("LOCAL-FIRST AI OPERATIONS")
        eyebrow.setObjectName("eyebrow")
        title = QtWidgets.QLabel("ITNI AI Team")
        title.setObjectName("appTitle")
        subtitle = QtWidgets.QLabel("업무를 입력하고 승인을 처리합니다.")
        subtitle.setObjectName("muted")
        titlebox.addWidget(eyebrow)
        titlebox.addWidget(title)
        titlebox.addWidget(subtitle)
        header.addLayout(titlebox)
        header.addStretch(1)
        self.updated_label = QtWidgets.QLabel("업데이트 대기")
        self.updated_label.setObjectName("statusPill")
        self.updated_label.setFixedHeight(36)
        self.updated_label.setAlignment(QtCore.Qt.AlignmentFlag.AlignCenter)
        header.addWidget(self.updated_label)
        refresh_button = QtWidgets.QPushButton("↻  새로고침")
        refresh_button.setFixedHeight(38)
        refresh_button.clicked.connect(self.refresh_data)
        header.addWidget(refresh_button)
        content_layout.addLayout(header)

        control_bar = QtWidgets.QFrame()
        control_bar.setObjectName("controlBar")
        control_layout = QtWidgets.QVBoxLayout(control_bar)
        control_layout.setContentsMargins(16, 10, 16, 10)
        control_layout.setSpacing(8)
        environment_row = QtWidgets.QHBoxLayout()
        environment_row.setSpacing(10)
        environment_title = QtWidgets.QLabel("실행 환경")
        environment_title.setObjectName("sectionTitle")
        environment_hint = QtWidgets.QLabel("모든 기본 추론은 로컬에서 실행됩니다")
        environment_hint.setObjectName("muted")
        environment_row.addWidget(environment_title)
        environment_row.addWidget(environment_hint)
        environment_row.addStretch(1)
        self.engine_selector = ModelSelectorComboBox()
        self.engine_selector.addItem("Ollama", "ollama")
        self.engine_selector.addItem("vLLM", "vllm")
        self.engine_selector.setCurrentIndex(self.engine_selector.findData(self.engine_name))
        self.engine_selector.setToolTip("업무 실행에 사용할 로컬 추론 엔진을 선택합니다.")
        self.engine_selector.currentIndexChanged.connect(self._on_engine_changed)
        self.model_selector = ModelSelectorComboBox()
        self.model_selector.addItems(self.model_options)
        self.model_selector.setCurrentText(self.model_name)
        self.model_selector.setToolTip(
            "로컬 LLM을 선택하세요. 목록을 열어 모델을 선택할 수 있습니다. "
            "설치 명령: ollama pull gemma4:12b / ollama pull gemma4:e2b"
        )
        self.model_selector.currentTextChanged.connect(self._on_model_changed)
        engine_label = QtWidgets.QLabel("엔진")
        engine_label.setObjectName("fieldLabel")
        model_label = QtWidgets.QLabel("모델")
        model_label.setObjectName("fieldLabel")
        self.engine_selector.setMinimumWidth(130)
        self.model_selector.setMinimumWidth(190)
        environment_row.addWidget(engine_label)
        environment_row.addWidget(self.engine_selector)
        environment_row.addSpacing(8)
        environment_row.addWidget(model_label)
        environment_row.addWidget(self.model_selector)
        control_layout.addLayout(environment_row)

        options_row = QtWidgets.QHBoxLayout()
        options_row.setSpacing(10)
        self.qa_selector = ModelSelectorComboBox()
        self.qa_selector.addItem("AI Agent 검토", "agent")
        self.qa_selector.addItem("규칙 기반 검토", "rule")
        self.qa_selector.setMinimumWidth(150)
        self.approval_mode_selector = ModelSelectorComboBox()
        self.approval_mode_selector.addItem("대표 수동 승인", "manual")
        self.approval_mode_selector.addItem("자동 최종 승인", "auto")
        self.approval_mode_selector.setMinimumWidth(160)
        qa_label = QtWidgets.QLabel("QA")
        qa_label.setObjectName("fieldLabel")
        approval_label = QtWidgets.QLabel("최종 승인")
        approval_label.setObjectName("fieldLabel")
        options_row.addWidget(qa_label)
        options_row.addWidget(self.qa_selector)
        options_row.addSpacing(8)
        options_row.addWidget(approval_label)
        options_row.addWidget(self.approval_mode_selector)
        options_row.addStretch(1)
        self.health_button = QtWidgets.QPushButton("Ollama 연결 확인")
        self.health_button.setObjectName("utility")
        self.health_button.clicked.connect(self.check_ollama_health)
        options_row.addWidget(self.health_button)
        self.selftest_button = QtWidgets.QPushButton("오프라인 전체 점검")
        self.selftest_button.setObjectName("utility")
        self.selftest_button.clicked.connect(self.run_offline_selftest)
        options_row.addWidget(self.selftest_button)
        control_layout.addLayout(options_row)
        content_layout.addWidget(control_bar)
        self.tabs.setDocumentMode(True)
        self.tabs.addTab(self._overview_page(), "업무 지시")
        self.tabs.addTab(self._approvals_page(), "대표 승인")
        content_layout.addWidget(self.tabs, 1)
        footer = QtWidgets.QLabel("Copyright © 2026 ITNI Inc. All rights reserved.")
        footer.setObjectName("copyright")
        footer.setAlignment(QtCore.Qt.AlignmentFlag.AlignRight | QtCore.Qt.AlignmentFlag.AlignVCenter)
        content_layout.addWidget(footer)
        root.addWidget(content, 1)

        shortcut = QtGui.QShortcut(QtGui.QKeySequence("Ctrl+Return"), self)
        shortcut.activated.connect(self.submit_request)

    def _on_model_changed(self, model_name):
        model_name = str(model_name or "").strip()
        if not model_name or model_name not in self.model_options or model_name == self.model_name:
            return
        self.model_name = model_name
        config_path = ROOT / "config" / "app_config.json"
        config = json.loads(config_path.read_text(encoding="utf-8"))
        config.setdefault("ai", {})["default_model"] = model_name
        config_path.write_text(json.dumps(config, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        if self.pending_analysis:
            self.pending_analysis = None
            self.execute_button.setEnabled(False)
            self.run_state.setText("모델이 변경되었습니다. 새 모델로 업무를 다시 분석해 주세요.")
        else:
            self.run_state.setText(f"선택 모델: {model_name} · 다음 분석과 실행에 적용됩니다.")

    def _on_engine_changed(self, index):
        provider = str(self.engine_selector.itemData(index) or "").strip().lower()
        if provider not in {"ollama", "vllm"} or provider == self.engine_name:
            return
        self.engine_name = provider
        config_path = ROOT / "config" / "app_config.json"
        config = json.loads(config_path.read_text(encoding="utf-8"))
        config.setdefault("ai", {})["provider"] = provider
        config_path.write_text(json.dumps(config, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        if self.pending_analysis:
            self.pending_analysis = None
            self.execute_button.setEnabled(False)
            self.run_state.setText("엔진이 변경되었습니다. 새 엔진으로 업무를 다시 분석해 주세요.")
        else:
            self.run_state.setText(f"선택 엔진: {provider} · 다음 분석과 실행에 적용됩니다.")

    def _panel(self, title: str):
        frame = QtWidgets.QFrame()
        frame.setObjectName("panel")
        layout = QtWidgets.QVBoxLayout(frame)
        layout.setContentsMargins(17, 15, 17, 17)
        layout.setSpacing(11)
        heading = QtWidgets.QLabel(title)
        heading.setObjectName("sectionTitle")
        layout.addWidget(heading)
        return frame, layout

    def _overview_page(self):
        page = QtWidgets.QWidget()
        page.setObjectName("dashboardTabPage")
        layout = QtWidgets.QVBoxLayout(page)
        layout.setContentsMargins(0, 0, 0, 0)
        scroll = QtWidgets.QScrollArea()
        scroll.setObjectName("dashboardTabScroll")
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QtWidgets.QFrame.Shape.NoFrame)
        body = QtWidgets.QWidget()
        body.setObjectName("dashboardTabContent")
        body_layout = QtWidgets.QVBoxLayout(body)
        body_layout.setContentsMargins(
            PAGE_SIDE_MARGIN, PAGE_TOP_MARGIN, PAGE_SIDE_MARGIN, 22
        )
        body_layout.setSpacing(12)
        scroll.setWidget(body)
        layout.addWidget(scroll)
        subtitle = QtWidgets.QLabel("업무 내용을 적고 참고 이미지를 끌어놓으세요. 비서실장이 필요한 팀과 실행 방법을 정합니다.")
        subtitle.setWordWrap(True)
        subtitle.setObjectName("muted")
        body_layout.addWidget(subtitle)

        request_panel, request_layout = self._panel("대표자 요구사항")
        request_layout.setSpacing(12)
        self.request_input = RequirementInput()
        self.request_input.setPlaceholderText(
            "업무 요구사항을 입력하세요.\n\n"
            "이미지를 첨부하려면 PNG / JPG / WEBP 파일을 이 입력 영역 안으로 끌어놓으세요.\n"
            "예: 매일 아침 8시에 나라장터 DB 구축 공고를 취합해 메일로 보내줘"
        )
        self.request_input.files_dropped.connect(self.add_images)
        request_layout.addWidget(self.request_input)

        self.selected_images = []
        self.image_list = QtWidgets.QListWidget()
        self.image_list.setMaximumHeight(132)
        self.image_list.setVisible(False)
        self.image_list.setSelectionMode(QtWidgets.QAbstractItemView.SelectionMode.SingleSelection)
        self.image_list.setViewMode(QtWidgets.QListView.ViewMode.IconMode)
        self.image_list.setIconSize(QtCore.QSize(92, 72))
        self.image_list.setGridSize(QtCore.QSize(126, 112))
        self.image_list.setResizeMode(QtWidgets.QListView.ResizeMode.Adjust)
        self.image_list.setContextMenuPolicy(QtCore.Qt.ContextMenuPolicy.CustomContextMenu)
        self.image_list.customContextMenuRequested.connect(self._image_context_menu)
        request_layout.addWidget(self.image_list)

        self.gemini_review_checkbox = QtWidgets.QCheckBox("Gemini 독립 검토 포함 (요구사항 텍스트를 Google에 전송)")
        self.gemini_review_checkbox.setToolTip(
            "선택하면 이메일·전화번호·키 등 알려진 민감정보를 마스킹하고 첨부 파일 본문을 제외한 요청문을 Gemini에 보냅니다."
        )
        request_layout.addWidget(self.gemini_review_checkbox)
        self.analysis_gemini_review = False
        self.gemini_code_review_checkbox = QtWidgets.QCheckBox(
            "Gemini 코드 QA 및 수정 (요구사항과 작성 코드가 Google에 전송됨)"
        )
        self.gemini_code_review_checkbox.setToolTip(
            "선택하면 파일 산출물을 Gemini에 보내 검토·개선합니다. 알려진 키·개인정보는 마스킹되며, 수정 코드는 기존 정적 검사와 로컬 QA도 거칩니다."
        )
        request_layout.addWidget(self.gemini_code_review_checkbox)
        self.analysis_gemini_code_review = False

        runrow = QtWidgets.QHBoxLayout()
        self.run_button = QtWidgets.QPushButton("비서실장 업무 분석")
        self.run_button.setObjectName("requestAction")
        self.run_button.setMinimumHeight(46)
        self.run_button.clicked.connect(self.analyze_request)
        runrow.addWidget(self.run_button, 0)
        self.execute_button = QtWidgets.QPushButton("분석 확인 후 실행")
        self.execute_button.setObjectName("requestAction")
        self.execute_button.setMinimumHeight(46)
        self.execute_button.setEnabled(False)
        self.execute_button.clicked.connect(self.execute_approved_analysis)
        runrow.addWidget(self.execute_button, 0)
        self.cancel_button=QtWidgets.QPushButton('중단')
        self.cancel_button.setObjectName('requestAction')
        self.cancel_button.setEnabled(False)
        self.cancel_button.clicked.connect(self.cancel_run)
        runrow.addWidget(self.cancel_button,0)
        self.resume_button=QtWidgets.QPushButton('중단 작업 재개')
        self.resume_button.setObjectName('requestAction')
        self.resume_button.clicked.connect(self.resume_run)
        runrow.addWidget(self.resume_button,0)
        self.run_state = QtWidgets.QLabel("준비됨 · 분석/개발 Ollama 로컬 · 외부 자료는 필요 시 Gemini 검색")
        self.run_state.setWordWrap(True)
        self.run_state.setObjectName("statusPill")
        runrow.addWidget(self.run_state, 1)
        request_layout.addLayout(runrow)
        self.run_progress = QtWidgets.QProgressBar()
        self.run_progress.setRange(0, 0)
        self.run_progress.setTextVisible(False)
        self.run_progress.setMaximumHeight(9)
        self.run_progress.setVisible(False)
        request_layout.addWidget(self.run_progress)
        analysis_heading = QtWidgets.QLabel("비서실장 분석")
        analysis_heading.setObjectName("sectionTitle")
        request_layout.addWidget(analysis_heading)
        self.decision_summary = QtWidgets.QPlainTextEdit()
        self.decision_summary.setReadOnly(True)
        self.decision_summary.setPlaceholderText("업무를 분석하면 실행 판단, 구성 에이전트와 역할, 업무 단계를 표시합니다.")
        self.decision_summary.setMinimumHeight(190)
        request_layout.addWidget(self.decision_summary, 1)
        body_layout.addWidget(request_panel, 1)
        return page

    def _projects_page(self):
        page = QtWidgets.QWidget()
        layout = QtWidgets.QVBoxLayout(page)
        layout.setContentsMargins(0, 14, 0, 0)
        toolbar = QtWidgets.QHBoxLayout()
        self.project_search = QtWidgets.QLineEdit()
        self.project_search.setPlaceholderText("프로젝트 또는 에이전트 검색")
        self.project_search.textChanged.connect(self._filter_projects)
        self.project_status_filter = ModelSelectorComboBox()
        self.project_status_filter.addItems(["전체 상태", "ANALYSIS", "PLANNING", "DEVELOPMENT", "FINAL_REVIEW", "APPROVAL_PENDING", "COMPLETED", "BLOCKED"])
        self.project_status_filter.currentTextChanged.connect(self._filter_projects)
        toolbar.addWidget(self.project_search, 1)
        toolbar.addWidget(self.project_status_filter)
        layout.addLayout(toolbar)
        self.projects_table = self._table(["프로젝트", "이름", "상태", "생성"])
        layout.addWidget(self.projects_table, 1)
        self.agents_table = self._table(["에이전트", "이름", "역할", "종류", "상태", "모델"])
        layout.addWidget(self.agents_table, 1)
        return page

    def _tasks_page(self):
        page = QtWidgets.QWidget()
        layout = QtWidgets.QVBoxLayout(page)
        layout.setContentsMargins(0, 14, 0, 0)
        toolbar = QtWidgets.QHBoxLayout()
        self.task_search = QtWidgets.QLineEdit()
        self.task_search.setPlaceholderText("업무 또는 담당 에이전트 검색")
        self.task_search.textChanged.connect(self._filter_tasks)
        self.task_status_filter = ModelSelectorComboBox()
        self.task_status_filter.addItems(["전체 상태", "WAITING", "READY", "IN_PROGRESS", "COMPLETED", "ERROR"])
        self.task_status_filter.currentTextChanged.connect(self._filter_tasks)
        toolbar.addWidget(self.task_search, 1)
        toolbar.addWidget(self.task_status_filter)
        layout.addLayout(toolbar)
        self.tasks_table = self._table(["업무", "프로젝트", "이름", "상태", "담당자", "생성"])
        layout.addWidget(self.tasks_table, 1)
        return page

    def _approvals_page(self):
        page = QtWidgets.QWidget()
        page.setObjectName("dashboardTabPage")
        layout = QtWidgets.QVBoxLayout(page)
        layout.setContentsMargins(0, 0, 0, 0)
        scroll = QtWidgets.QScrollArea()
        scroll.setObjectName("dashboardTabScroll")
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QtWidgets.QFrame.Shape.NoFrame)
        body = QtWidgets.QWidget()
        body.setObjectName("dashboardTabContent")
        body_layout = QtWidgets.QVBoxLayout(body)
        body_layout.setContentsMargins(
            PAGE_SIDE_MARGIN, PAGE_TOP_MARGIN, PAGE_SIDE_MARGIN, 22
        )
        body_layout.setSpacing(12)
        scroll.setWidget(body)
        layout.addWidget(scroll)
        heading = QtWidgets.QLabel("요청을 선택해 아래 실행 결과를 확인한 뒤 승인 또는 반려하세요. 각 행에서 삭제도 할 수 있습니다.")
        heading.setObjectName("muted")
        body_layout.addWidget(heading)
        approval_panel, approval_layout = self._panel("대표 승인")
        approval_layout.setSpacing(12)
        self.approvals_table = self._table(["번호", "작업일시", "프로젝트 ID", "업무 요약", "승인 필요 이유", "처리 상태", "대표 확인"])
        self.approvals_table.setMinimumHeight(220)
        self.approvals_table.horizontalHeader().setSectionResizeMode(0, QtWidgets.QHeaderView.ResizeMode.ResizeToContents)
        self.approvals_table.horizontalHeader().setSectionResizeMode(1, QtWidgets.QHeaderView.ResizeMode.ResizeToContents)
        self.approvals_table.horizontalHeader().setSectionResizeMode(2, QtWidgets.QHeaderView.ResizeMode.ResizeToContents)
        self.approvals_table.horizontalHeader().setSectionResizeMode(3, QtWidgets.QHeaderView.ResizeMode.Stretch)
        self.approvals_table.horizontalHeader().setSectionResizeMode(4, QtWidgets.QHeaderView.ResizeMode.Stretch)
        self.approvals_table.horizontalHeader().setSectionResizeMode(5, QtWidgets.QHeaderView.ResizeMode.ResizeToContents)
        self.approvals_table.horizontalHeader().setSectionResizeMode(6, QtWidgets.QHeaderView.ResizeMode.Fixed)
        self.approvals_table.setColumnWidth(6, 240)
        approval_layout.addWidget(self.approvals_table, 3)
        result_heading = QtWidgets.QLabel("요청사항 실행 결과")
        result_heading.setObjectName("sectionTitle")
        approval_layout.addWidget(result_heading)
        self.approval_result = QtWidgets.QPlainTextEdit()
        self.approval_result.setReadOnly(True)
        self.approval_result.setPlaceholderText("승인 내역을 선택하면 요청사항 실행 결과가 여기에 표시됩니다.")
        self.approval_result.setMinimumHeight(140)
        approval_layout.addWidget(self.approval_result, 2)
        body_layout.addWidget(approval_panel, 1)
        self.approvals_table.itemSelectionChanged.connect(self._show_selected_approval_result)
        return page

    @staticmethod
    def _table(headers):
        table = QtWidgets.QTableWidget(0, len(headers))
        table.setHorizontalHeaderLabels(headers)
        table.setAlternatingRowColors(True)
        table.setEditTriggers(QtWidgets.QAbstractItemView.EditTrigger.NoEditTriggers)
        table.setSelectionBehavior(QtWidgets.QAbstractItemView.SelectionBehavior.SelectRows)
        table.setSelectionMode(QtWidgets.QAbstractItemView.SelectionMode.SingleSelection)
        table.verticalHeader().setVisible(False)
        table.verticalHeader().setDefaultSectionSize(48)
        table.horizontalHeader().setSectionResizeMode(QtWidgets.QHeaderView.ResizeMode.Stretch)
        table.setShowGrid(False)
        table.setMinimumHeight(170)
        return table

    @staticmethod
    def _fill(table, records):
        table.setRowCount(len(records))
        for row_no, record in enumerate(records):
            for col_no, value in enumerate(record):
                item = QtWidgets.QTableWidgetItem("—" if value is None else str(value))
                item.setFlags(QtCore.Qt.ItemFlag.ItemIsSelectable | QtCore.Qt.ItemFlag.ItemIsEnabled)
                table.setItem(row_no, col_no, item)
        table.resizeRowsToContents()

    def refresh_data(self):
        try:
            self._refresh_approvals()
            self.updated_label.setText("업데이트 " + QtCore.QTime.currentTime().toString("HH:mm:ss"))
        except Exception as exc:
            self.run_state.setText(f"데이터 읽기 실패: {exc}")

    def _filter_projects(self, *_):
        if not hasattr(self, "projects_table"):
            return
        query = self.project_search.text().casefold()
        status = self.project_status_filter.currentText()
        projects = [p for p in self.project_records if (status == "전체 상태" or p[2] == status) and query in " ".join(str(v or "") for v in p).casefold()]
        self._fill(self.projects_table, projects)
        agents = [a for a in self.agent_records if query in " ".join(str(v or "") for v in a).casefold()]
        self._fill(self.agents_table, [a[:6] for a in agents])

    def _filter_tasks(self, *_):
        if not hasattr(self, "tasks_table"):
            return
        query = self.task_search.text().casefold()
        status = self.task_status_filter.currentText()
        tasks = [t for t in self.task_records if (status == "전체 상태" or t[3] == status) and query in " ".join(str(v or "") for v in t).casefold()]
        self._fill(self.tasks_table, tasks)

    def _refresh_approvals(self):
        details = {}
        conn = self.db.connect()
        try:
            for row in conn.execute("""SELECT m.approval_id,m.project_id,p.name,m.purpose,m.scope,m.data_scope,m.estimated_cost,m.status,m.created_at,
                (SELECT user_request FROM team_runs t WHERE t.project_id=m.project_id ORDER BY started_at DESC LIMIT 1)
                FROM model_approvals m LEFT JOIN projects p ON p.project_id=m.project_id ORDER BY m.created_at DESC LIMIT 100"""):
                key = "cloud:" + row[0]
                details[key] = {"type":"클라우드 접근", "id":row[0], "project_id":row[1], "project_name":row[2] or "프로젝트명 없음",
                    "request":row[9] or "업무 요청 내용 없음", "reason":row[3] or "클라우드 AI 처리가 요청되었습니다.",
                    "status":row[7], "scope":row[4], "data_scope":row[5], "cost":row[6], "created":row[8]}
            for row in conn.execute("""SELECT a.approval_id,a.project_id,p.name,a.task_id,a.result_id,a.status,a.created_at,r.summary,
                (SELECT user_request FROM team_runs t WHERE t.project_id=a.project_id ORDER BY started_at DESC LIMIT 1),
                (SELECT summary FROM qa_reviews q WHERE q.task_id=a.task_id ORDER BY q.created_at DESC LIMIT 1)
                FROM approvals a LEFT JOIN projects p ON p.project_id=a.project_id LEFT JOIN results r ON r.result_id=a.result_id
                ORDER BY a.created_at DESC LIMIT 100"""):
                key = "final:" + row[0]
                details[key] = {"type":"최종 결과", "id":row[0], "project_id":row[1], "project_name":row[2] or "프로젝트명 없음", "task_id":row[3],
                    "result":(("최신 QA 재검증 실패 — 승인할 수 없습니다.\n"+(row[9] or '')+"\n\n[기존 결과 기록]\n") if row[5]=='INVALIDATED' else '')+(row[7] or "결과 요약이 없습니다."), "request":row[8] or "업무 요청 내용 없음", "status":row[5], "created":row[6]}
        finally:
            conn.close()
        self._approval_details = details
        ordered = sorted(details.items(), key=lambda item:(item[1]["status"] != "PENDING", item[1]["created"] or ""))
        self.approvals_table.clearContents()
        self.approvals_table.setRowCount(len(ordered))
        for row_index, (key, item) in enumerate(ordered):
            reason = item["reason"] if item["type"] == "클라우드 접근" else "QA 검토를 마친 최종 결과를 대표가 확인하고 확정합니다."
            if item['status']=='INVALIDATED':
                reason='QA 재검증 실패로 승인 요청이 무효화되었습니다. 수정 후 재검증이 필요합니다.'
            status_label = {"PENDING":"승인 대기", "APPROVED":"승인 완료", "REJECTED":"반려", "INVALIDATED":"QA 재검증 실패"}.get(item["status"], item["status"])
            created = str(item["created"] or "").replace("T", " ")[:19]
            values = (str(row_index + 1), created, item["project_id"],
                      self._short_text(item["request"], 100), self._short_text(reason, 100), status_label)
            for col, value in enumerate(values):
                cell = QtWidgets.QTableWidgetItem(value)
                cell.setFlags(QtCore.Qt.ItemFlag.ItemIsSelectable | QtCore.Qt.ItemFlag.ItemIsEnabled)
                if col == 0:
                    cell.setData(QtCore.Qt.ItemDataRole.UserRole, key)
                self.approvals_table.setItem(row_index, col, cell)
            action_widget = QtWidgets.QWidget(self.approvals_table)
            action_widget.setObjectName("approvalActions")
            action_layout = QtWidgets.QHBoxLayout(action_widget)
            action_layout.setContentsMargins(4, 2, 4, 2)
            action_layout.setSpacing(4)
            status_font = self.approvals_table.item(row_index, 5).font()
            delete_button = QtWidgets.QPushButton("삭제")
            delete_button.setObjectName("danger")
            delete_button.setFont(status_font)
            delete_button.setMinimumWidth(68)
            delete_button.clicked.connect(lambda checked=False, approval_key=key: self.delete_selected_approval(approval_key))
            action_layout.addWidget(delete_button)
            reject_button = QtWidgets.QPushButton("반려")
            reject_button.setObjectName("warning")
            reject_button.setFont(status_font)
            reject_button.setMinimumWidth(68)
            reject_button.setEnabled(item["status"] == "PENDING")
            reject_button.clicked.connect(lambda checked=False, approval_key=key: self.decide_selected_approval("REJECTED", approval_key))
            action_layout.addWidget(reject_button)
            approve_button = QtWidgets.QPushButton("승인")
            approve_button.setFont(status_font)
            approve_button.setMinimumWidth(68)
            approve_button.setObjectName("primary")
            approve_button.setEnabled(item["status"] == "PENDING")
            approve_button.clicked.connect(lambda checked=False, approval_key=key: self.decide_selected_approval("APPROVED", approval_key))
            action_layout.addWidget(approve_button)
            self.approvals_table.setCellWidget(row_index, 6, action_widget)
        for row_index in range(self.approvals_table.rowCount()):
            self.approvals_table.setRowHeight(row_index, 64)
        selected_row = next(
            (index for index, (key, _) in enumerate(ordered) if key == self._selected_approval_key),
            -1,
        )
        if selected_row >= 0:
            self.approvals_table.selectRow(selected_row)
            self._selected_approval_key = ordered[selected_row][0]
            self._show_approval_result(self._selected_approval_key)
        else:
            self._selected_approval_key = None
            self.approvals_table.clearSelection()
            self.approvals_table.setCurrentCell(-1, -1)
            self.approval_result.clear()

    def _show_selected_approval_result(self):
        row = self.approvals_table.currentRow()
        cell = self.approvals_table.item(row, 0) if row >= 0 else None
        key = cell.data(QtCore.Qt.ItemDataRole.UserRole) if cell else None
        if key:
            self._selected_approval_key = key
        self._show_approval_result(key)

    def _show_approval_result(self, approval_key):
        detail = self._approval_details.get(approval_key) if approval_key else None
        if not detail:
            self.approval_result.clear()
            return
        header = (
            f"프로젝트명: {detail['project_name']}\n"
            f"프로젝트 ID: {detail['project_id']}\n"
            f"업무 요청: {detail['request']}\n\n"
        )
        if detail["type"] == "최종 결과":
            result_text = detail.get("result") or "저장된 최종 결과 내용이 없습니다."
        else:
            result_text = (
                "클라우드 AI 사용 승인 요청입니다. 아직 업무 실행 결과는 생성되지 않았습니다.\n"
                f"승인 필요 이유: {detail.get('reason', '')}\n"
                f"처리 범위: {detail.get('scope', '')}\n"
                f"대상 데이터: {detail.get('data_scope', '')}"
            )
        self.approval_result.setPlainText(header + result_text)

    @staticmethod
    def _short_text(value, limit):
        compact = " ".join(str(value or "").split())
        return compact if len(compact) <= limit else compact[:limit - 1] + "…"

    def decide_selected_approval(self, status, approval_key=None):
        if approval_key is None:
            row = self.approvals_table.currentRow()
            cell = self.approvals_table.item(row, 0) if row >= 0 else None
            approval_key = cell.data(QtCore.Qt.ItemDataRole.UserRole) if cell else None
        detail = self._approval_details.get(approval_key) if approval_key else None
        if not detail or detail["status"] != "PENDING":
            QtWidgets.QMessageBox.information(self, "승인 처리", "처리 대기 중인 승인 내역이 아닙니다.")
            return
        self._selected_approval_key = approval_key
        self._show_approval_result(approval_key)
        comment = ""
        rework_request = None
        if status == "REJECTED" and str(approval_key).startswith("final:"):
            answer = QtWidgets.QMessageBox.question(
                self,
                "삭제 안내 및 재작업 확인",
                f"기존 코드와 결과물은 삭제되지 않고 보존됩니다.\n"
                f"반려 후 재작업 결과는 새 프로젝트 폴더에 생성됩니다.\n\n"
                f"프로젝트: {detail.get('project_name', '')}\n"
                f"프로젝트 ID: {detail.get('project_id', '')}\n\n"
                "재작업을 계속하시겠습니까?",
                QtWidgets.QMessageBox.StandardButton.Yes | QtWidgets.QMessageBox.StandardButton.No,
                QtWidgets.QMessageBox.StandardButton.No,
            )
            if answer != QtWidgets.QMessageBox.StandardButton.Yes:
                return
            comment, accepted = QtWidgets.QInputDialog.getMultiLineText(
                self,
                "재작업 요청",
                "반려 사유와 수정 요청을 입력하세요:",
                "대표 반려에 따른 최종 재작업 요청",
            )
            if not accepted:
                return
            prior_result = detail.get("result") or ""
            rework_request = (
                f"{detail.get('request', '')}\n\n"
                f"[재작업 원 프로젝트 ID: {detail.get('project_id', '')}]\n"
                "[대표 반려 후 재작업 지시]\n"
                f"수정 요청: {comment.strip() or '대표 반려 내용을 반영해 결과물을 개선하세요.'}\n\n"
                f"[이전 결과 참고]\n{prior_result}"
            )
        try:
            if detail["type"] == "클라우드 접근":
                ApprovalGate(self.db).decide(detail["id"], status, comment)
            else:
                Orchestrator(self.db).approve(detail["id"], status, comment)
            self.run_state.setText("승인 처리 완료" if status == "APPROVED" else "반려 처리 완료")
            self.refresh_data()
            if status == "APPROVED" and detail["type"] == "클라우드 접근" and self.last_result and self.last_result.get("project_id") == detail["project_id"]:
                scope_key={"code_qa":"code_qa","public_web_search":"search","task_execution":"task"}.get(detail.get("scope"),"task")
                self.run_state.setText("대표 승인 확인 · 클라우드 작업을 재개합니다.")
                self.run_progress.setVisible(True)
                self.worker=RunWorker(self.analysis_request,self.model_name,"agent","manual",mode="execute",
                    analysis_override=self.pending_analysis,gemini_code_review=self.analysis_gemini_code_review,
                    cloud_approval_ids={scope_key:detail["id"]},resume_project_id=detail["project_id"],parent=self)
                self.worker.succeeded.connect(self._run_succeeded); self.worker.failed.connect(self._run_failed); self._start_worker()
                return
            if rework_request is not None:
                self.run_state.setText("대표 반려 내용을 반영해 재작업 및 QA를 진행하고 있습니다.")
                self.run_progress.setVisible(True)
                self.worker = RunWorker(
                    rework_request, self.model_name, "agent", "manual",
                    mode="rework", parent=self,
                )
                self.worker.succeeded.connect(self._run_succeeded)
                self.worker.failed.connect(self._run_failed)
                self._start_worker()
                return
            if status == "APPROVED" and detail["type"] == "최종 결과":
                QtWidgets.QMessageBox.information(
                    self,
                    "구축 완료",
                    f"대표 승인이 완료되어 구축이 완료되었습니다.\n\n프로젝트명: {detail['project_name']}\n프로젝트 ID: {detail['project_id']}",
                )
                self._clear_request_views()
        except Exception as exc:
            QtWidgets.QMessageBox.critical(self, "승인 처리 실패", str(exc))

    def delete_selected_approval(self, approval_key=None):
        if approval_key is None:
            row = self.approvals_table.currentRow()
            cell = self.approvals_table.item(row, 0) if row >= 0 else None
            approval_key = cell.data(QtCore.Qt.ItemDataRole.UserRole) if cell else None
        detail = self._approval_details.get(approval_key) if approval_key else None
        if not detail:
            self.run_state.setText("삭제할 승인 내역을 먼저 선택해 주세요.")
            return
        answer = QtWidgets.QMessageBox.question(
            self,
            "승인 내역 삭제",
            f"선택한 {detail['type']} 내역을 영구 삭제할까요?\n\n프로젝트 ID: {detail['project_id']}\n프로젝트명: {detail['project_name']}\n승인 ID: {detail['id']}\n삭제 후 복구할 수 없습니다.",
            QtWidgets.QMessageBox.StandardButton.Yes | QtWidgets.QMessageBox.StandardButton.No,
            QtWidgets.QMessageBox.StandardButton.No,
        )
        if answer != QtWidgets.QMessageBox.StandardButton.Yes:
            return
        table = "model_approvals" if detail["type"] == "클라우드 접근" else "approvals"
        conn = self.db.connect()
        try:
            cursor = conn.execute(f"DELETE FROM {table} WHERE approval_id=?", (detail["id"],))
            if cursor.rowcount != 1:
                raise RuntimeError("선택한 승인 내역을 찾을 수 없습니다.")
            conn.commit()
        except Exception as exc:
            conn.rollback()
            QtWidgets.QMessageBox.critical(self, "승인 내역 삭제 실패", str(exc))
            return
        finally:
            conn.close()
        self.run_state.setText("선택한 승인 내역을 삭제했습니다.")
        self.refresh_data()

    def submit_request(self):
        self.analyze_request()

    def _start_worker(self):
        self.worker.progress.connect(self._run_progress)
        self.worker.finished.connect(self._worker_finished)
        self._stage='처리 중';self._stage_started=time.monotonic()
        self.cancel_button.setEnabled(self.worker.mode!='health')
        self.resume_button.setEnabled(False)
        self.health_button.setEnabled(False);self.selftest_button.setEnabled(False)
        self.model_selector.setEnabled(False);self.engine_selector.setEnabled(False)
        if not hasattr(self,'elapsed_timer'):
            self.elapsed_timer=QtCore.QTimer(self)
            self.elapsed_timer.timeout.connect(self._show_elapsed)
        self.elapsed_timer.start(1000)
        self.worker.start()

    def _run_progress(self,event):
        names={'images':'이미지 분석','analysis':'요구사항 분석','planning':'업무 분장',
               'implementation':'프로그램 작성','qa':'QA 검토','verification':'검증',
               'dependency_preparation':'Docker 패키지 준비','dependency_ready':'패키지 검사 통과',
               'dependency_failed':'패키지 준비 미완료',
               'rework':'재작업','integration':'전체 통합 검사','integration_rework':'통합 재작업',
               'task_complete':'담당 작업 완료','saving':'파일 저장','complete':'완료'}
        self._stage=names.get(event['stage'],event['stage'])
        if event.get('total'):
            self._stage+=f" · 작업 {event.get('completed',0)}/{event['total']}"
        self._show_elapsed()

    def _show_elapsed(self):
        self.run_state.setText(f'{self._stage} · 경과 {int(time.monotonic()-self._stage_started)}초')

    def cancel_run(self):
        if self.worker and self.worker.isRunning():
            self.worker.request_cancel();self.cancel_button.setEnabled(False)
            self._stage='중단 처리 중';self._show_elapsed()

    def _worker_finished(self):
        self.elapsed_timer.stop();self.cancel_button.setEnabled(False);self.resume_button.setEnabled(True)
        self.model_selector.setEnabled(True);self.engine_selector.setEnabled(True)
        if getattr(self,'_closing',False):self.close()

    def closeEvent(self,event):
        if self.worker and self.worker.isRunning():
            self._closing=True;self.cancel_run();event.ignore();return
        if hasattr(self,'elapsed_timer'):self.elapsed_timer.stop()
        super().closeEvent(event)

    def resume_run(self):
        if self.worker and self.worker.isRunning():return
        with self.db.connection() as conn:
            row=conn.execute("SELECT project_id FROM workflow_checkpoints WHERE status IN ('CANCELLED','ERROR') ORDER BY updated_at DESC LIMIT 1").fetchone()
        if not row:
            self.run_state.setText('재개할 중단 작업이 없습니다. 재계획 상태는 새 분석이 필요합니다.');return
        _,saved=Checkpoints(self.db).load(row[0])
        if saved.get('model')!=self.model_name:
            self.run_state.setText('재개하려면 기존 모델을 선택하세요: '+saved.get('model',''));return
        self.request_input.setPlainText(saved['request'])
        self.worker=RunWorker(saved['request'],saved['model'],saved.get('qa_mode','agent'),saved.get('approval_mode','manual'),
                              mode='resume',resume_project_id=row[0],parent=self)
        self.worker.succeeded.connect(self._run_succeeded);self.worker.failed.connect(self._run_failed)
        self.run_button.setEnabled(False);self.execute_button.setEnabled(False);self.run_progress.setVisible(True)
        self._start_worker()

    def check_ollama_health(self):
        if self.worker and self.worker.isRunning():
            return
        self.health_button.setEnabled(False)
        self.selftest_button.setEnabled(False)
        self.run_state.setText("Ollama 연결과 선택 모델을 확인하고 있습니다.")
        self.worker = RunWorker("", self.model_name, "agent", "manual", mode="health", parent=self)
        self.worker.succeeded.connect(self._run_succeeded)
        self.worker.failed.connect(self._run_failed)
        self._start_worker()

    def run_offline_selftest(self):
        if self.worker and self.worker.isRunning():
            return
        request = self.request_input.toPlainText().strip() or "공공사업 기록물 DB 구축 제안서를 작성해줘."
        self.health_button.setEnabled(False)
        self.selftest_button.setEnabled(False)
        self.run_state.setText("임시 DB와 임시 폴더에서 전체 흐름을 점검하고 있습니다.")
        self.worker = RunWorker(request, "offline-self-test", "agent", "auto", mode="fake", parent=self)
        self.worker.succeeded.connect(self._run_succeeded)
        self.worker.failed.connect(self._run_failed)
        self._start_worker()

    def analyze_request(self):
        request = self.request_input.toPlainText().strip()
        if not request and not self.selected_images:
            self.run_state.setText("업무 지시를 입력하거나 이미지를 첨부해 주세요.")
            self.request_input.setFocus()
            return
        if not request:
            request = "첨부 이미지의 내용과 핵심 정보를 분석해 정리해 주세요."
        if self.worker and self.worker.isRunning():
            return
        self.pending_analysis = None
        self.analysis_request = request
        self.analysis_images = list(self.selected_images)
        self.execute_button.setEnabled(False)
        self.run_button.setEnabled(False)
        self.run_button.setText("분석 중…")
        if self.selected_images and self.request_input.toPlainText().strip():
            self.run_state.setText(f"텍스트 요구사항과 이미지 {len(self.selected_images)}개를 함께 분석하고 있습니다.")
        elif self.selected_images:
            self.run_state.setText(f"첨부 이미지 {len(self.selected_images)}개를 VLM으로 분석하고 있습니다.")
        else:
            self.run_state.setText(
                "로컬 비서실장과 Gemini가 요구사항을 각각 검토하고 비교 중입니다."
                if self.gemini_review_checkbox.isChecked()
                else "비서실장이 업무 범위와 필요한 에이전트를 분석하고 있습니다."
            )
        self.run_progress.setVisible(True)
        self.worker = RunWorker(
            request, self.model_name, self.qa_selector.currentData(), self.approval_mode_selector.currentData(), self.selected_images,
            mode="analyze", gemini_review=self.gemini_review_checkbox.isChecked(),
            gemini_code_review=self.gemini_code_review_checkbox.isChecked(), parent=self,
        )
        self.worker.succeeded.connect(self._run_succeeded)
        self.worker.failed.connect(self._run_failed)
        self._start_worker()

    def execute_approved_analysis(self):
        if not self.pending_analysis or not self.analysis_request:
            self.run_state.setText("먼저 비서실장 분석을 완료해 주세요.")
            return
        if self.gemini_review_checkbox.isChecked() != self.analysis_gemini_review:
            self.pending_analysis = None
            self.execute_button.setEnabled(False)
            self.run_state.setText("Gemini 검토 설정이 바뀌었습니다. 다시 분석해 주세요.")
            return
        if self.gemini_code_review_checkbox.isChecked() != self.analysis_gemini_code_review:
            self.pending_analysis = None
            self.execute_button.setEnabled(False)
            self.run_state.setText("Gemini 코드 QA 설정이 바뀌었습니다. 다시 분석해 주세요.")
            return
        if self.request_input.toPlainText().strip() != self.analysis_request:
            self.pending_analysis = None
            self.execute_button.setEnabled(False)
            self.run_state.setText("업무 지시가 바뀌었습니다. 변경된 지시를 다시 분석해 주세요.")
            return
        if self.selected_images != getattr(self, "analysis_images", []):
            self.pending_analysis = None
            self.execute_button.setEnabled(False)
            self.run_state.setText("첨부 이미지가 바뀌었습니다. 변경된 이미지를 다시 분석해 주세요.")
            return
        self.run_button.setEnabled(False)
        self.execute_button.setEnabled(False)
        self.execute_button.setText("실행 중…")
        self.run_state.setText("확인한 분석에 따라 실행하고 QA 검토를 진행합니다.")
        self.run_progress.setVisible(True)
        self.worker = RunWorker(self.analysis_request, self.model_name, self.qa_selector.currentData(), self.approval_mode_selector.currentData(), self.selected_images,
                                mode="execute", analysis_override=self.pending_analysis,
                                gemini_code_review=self.gemini_code_review_checkbox.isChecked(), parent=self)
        self.worker.succeeded.connect(self._run_succeeded)
        self.worker.failed.connect(self._run_failed)
        self._start_worker()

    def _run_succeeded(self, result):
        if getattr(self,'_closing',False):return
        if hasattr(self,'elapsed_timer'):self.elapsed_timer.stop()
        self.health_button.setEnabled(True)
        self.selftest_button.setEnabled(True)
        if result.get('status')=='CANCELLED':
            self.run_progress.setVisible(False);self.run_button.setEnabled(True)
            self.run_button.setText('비서실장 업무 분석');self.execute_button.setEnabled(bool(self.pending_analysis))
            self.execute_button.setText('분석 확인 후 실행')
            self.run_state.setText('중단됨 · 완료된 작업은 저장했습니다. 중단 작업 재개로 이어갈 수 있습니다.' if result.get('project_id') else '분석을 중단했습니다. 다시 분석할 수 있습니다.')
            self.refresh_data();return
        if result.get("mode") == "health":
            # Preserve existing choices and expose any other installed Ollama model.
            with QtCore.QSignalBlocker(self.model_selector):
                for model in result.get('selectable_models', result.get('models')) or []:
                    if model not in self.model_options:
                        self.model_options.append(model)
                        self.model_selector.addItem(model)
            ready = result.get("model_ready")
            message = (
                f"Ollama 연결 성공\n주소: {result.get('base_url')}\n"
                f"선택 모델: {result.get('model')} ({'사용 가능' if ready else '설치되지 않음'})\n"
                f"설치 모델: {', '.join(result.get('models') or []) or '(없음)'}"
            )
            self.run_state.setText("Ollama 연결 확인 완료" + (" · 선택 모델 사용 가능" if ready else " · 선택 모델 설치 필요"))
            QtWidgets.QMessageBox.information(self, "Ollama 연결 확인", message)
            return
        if result.get("mode") == "fake":
            passed = result.get("status") == "APPROVED" and any(row[5] == "FAIL" for row in result.get("qa_reviews", [])) and any(row[5] == "PASS" for row in result.get("qa_reviews", []))
            self.run_state.setText("오프라인 전체 점검 통과 · 임시 데이터는 자동 정리됨" if passed else "오프라인 전체 점검 실패")
            title = "오프라인 전체 점검"
            message = "Chief → 조직 구성 → 실행 → QA 실패 → 재작업 → QA 통과 → 최종 승인 흐름을 임시 환경에서 확인했습니다." if passed else "전체 흐름의 기대 결과를 확인하지 못했습니다."
            (QtWidgets.QMessageBox.information if passed else QtWidgets.QMessageBox.warning)(self, title, message)
            return
        if result.get("mode") == "analyze":
            self.run_progress.setVisible(False)
            self.pending_analysis = result.get("chief_decision") or {}
            self.analysis_request = result.get("request")
            self.analysis_gemini_review = bool(result.get("gemini_review"))
            self.analysis_gemini_code_review = bool(result.get("gemini_code_review"))
            self.analysis_images = list(self.selected_images)
            self._display_decision({
                "chief_decision": self.pending_analysis,
                "request": result.get("request"),
                "image_analysis": result.get("image_analysis"),
                "mode": "analyze",
            })
            self.run_state.setText("업무 분석 완료 · 내용을 확인한 뒤 실행을 누르세요.")
            self.run_button.setEnabled(True)
            self.run_button.setText("다시 분석")
            self.execute_button.setEnabled(True)
            self.execute_button.setText("분석 확인 후 실행")
            return
        self.last_result = result
        self.run_progress.setVisible(False)
        self._display_decision(result)
        self.run_state.setText(self._status_copy(result))
        if result.get("status") == "CLOUD_APPROVAL_PENDING":
            self.run_button.setEnabled(True)
            self.execute_button.setEnabled(False)
            self.refresh_data()
            self.tabs.setCurrentIndex(1)
            QtWidgets.QMessageBox.information(self,"클라우드 승인 대기",self._status_copy(result))
            return
        self.run_button.setEnabled(True)
        self.run_button.setText("비서실장 업무 분석")
        self.execute_button.setEnabled(False)
        self.execute_button.setText("분석 확인 후 실행")
        self.pending_analysis = None
        self.analysis_request = None
        self.request_input.clear()
        self.selected_images = []
        self.image_list.clear()
        self.image_list.setVisible(False)
        self.refresh_data()
        self.tabs.setCurrentIndex(1 if result.get("status") in {"PENDING_APPROVAL", "CLOUD_APPROVAL_PENDING"} else 0)
        QtWidgets.QMessageBox.information(self, "업무 처리 완료", self._status_copy(result))
        self._clear_request_views()

    def _clear_request_views(self):
        """Clear the completed request and its displayed analysis/results after confirmation."""
        self.request_input.clear()
        self.decision_summary.clear()
        self.approval_result.clear()
        self.pending_analysis = None
        self.analysis_request = None
        self.analysis_images = []
        self.selected_images = []
        self.image_list.clear()
        self.image_list.setVisible(False)
        self._selected_approval_key = None
        self.approvals_table.blockSignals(True)
        self.approvals_table.clearSelection()
        self.approvals_table.setCurrentCell(-1, -1)
        self.approvals_table.blockSignals(False)
        self.run_state.setText("준비됨 · 분석/개발 Ollama 로컬 · 외부 자료는 필요 시 Gemini 검색")
        self.run_button.setEnabled(True)
        self.run_button.setText("비서실장 업무 분석")
        self.execute_button.setEnabled(False)
        self.execute_button.setText("분석 확인 후 실행")

    @staticmethod
    def _korean_agent_name(name, specialty="", role=""):
        value = str(name or "").strip()
        specialty_text = str(specialty or "").strip()
        role_text = str(role or "").strip()
        value_folded = f"{value} {specialty_text} {role_text}".casefold()
        if any("가" <= char <= "힣" for char in value):
            return value
        translations = (
            (("g2b", "procurement", "조달"), "나라장터 조달 데이터 에이전트"),
            (("automation", "workflow", "자동화"), "업무 자동화 에이전트"),
            (("email", "mail", "이메일"), "이메일 연동 에이전트"),
            (("ocr", "document", "문서"), "문서 인식·OCR 에이전트"),
            (("gis", "spatial", "공간정보"), "공간정보 분석 에이전트"),
            (("database", "data engineer", "db specialist"), "데이터베이스 구축 에이전트"),
            (("security", "privacy", "보안"), "보안·개인정보 검토 에이전트"),
            (("software", "developer", "programmer", "coder", "개발"), "소프트웨어 개발 에이전트"),
            (("proposal", "rfp", "제안서"), "제안서 분석 에이전트"),
            (("research", "researcher", "조사"), "조사·분석 에이전트"),
            (("design", "ui", "ux", "디자인"), "화면·디자인 에이전트"),
        )
        for terms, translated in translations:
            if any(term in value_folded for term in terms):
                return translated
        if any("가" <= char <= "힣" for char in role_text):
            return role_text
        return "업무 전문 에이전트"

    @staticmethod
    def _korean_domain(specialty):
        value = str(specialty or "").casefold()
        if any(term in value for term in ("g2b", "procurement", "조달")):
            return "조달 데이터"
        if any(term in value for term in ("automation", "workflow", "자동화")):
            return "업무 자동화"
        if any(term in value for term in ("email", "mail", "이메일")):
            return "이메일 연동"
        if any(term in value for term in ("ocr", "document", "문서")):
            return "문서 인식·OCR"
        if any(term in value for term in ("gis", "spatial", "공간정보")):
            return "공간정보"
        if any(term in value for term in ("database", "data engineer", "db", "데이터")):
            return "데이터베이스·데이터 관리"
        if any(term in value for term in ("software", "developer", "programming", "개발")):
            return "소프트웨어 개발"
        if any(term in value for term in ("security", "privacy", "보안")):
            return "보안·개인정보"
        if any(term in value for term in ("proposal", "rfp", "제안서")):
            return "제안서 분석"
        if any(term in value for term in ("research", "조사")):
            return "조사·자료 분석"
        if any("가" <= char <= "힣" for char in str(specialty or "")):
            return str(specialty)
        return "업무 분석"

    def _display_decision(self, result):
        decision = result.get("chief_decision") or {}
        if decision.get("cloud_ai_required"):
            decision["execution_mode"] = "gemini"
        mode = "Gemini 외부 AI 작업 (실행 버튼으로 전송 동의)" if decision.get("cloud_ai_required") else "AI 분석·작업: 로컬 Ollama"
        core_roles = decision.get("core_roles") or []
        if isinstance(core_roles, str):
            core_roles = [core_roles]
        specialists = [item for item in (decision.get("specialists") or []) if isinstance(item, dict)]
        participants = ["비서실장", "QA"]
        participants.extend(self._korean_agent_name(role, role) for role in core_roles if role)
        participants.extend(
            self._korean_agent_name(item.get("name"), item.get("specialty"), item.get("role"))
            for item in specialists
        )
        participants = list(dict.fromkeys(participants))
        lines = [f"AI 분석 방식: {mode}"]
        if decision.get("cloud_ai_required"):
            lines.append("Gemini 전송 범위: " + str(decision.get("cloud_data_scope") or "분석된 업무 요청"))
            lines.append("Gemini 사용 목적: " + str(decision.get("cloud_purpose") or "업무 결과 생성") + "; 예상 비용: " + str(decision.get("estimated_cost") or "미산정"))
            lines.append("실행 버튼을 누르면 위 범위의 업무 내용이 Gemini API로 전송됩니다.")
        external_plan = decision.get("external_data_plan") or {}
        if decision.get("external_data_access") or external_plan.get("required"):
            lines.append(f"외부 데이터 계획: 원천={external_plan.get('source') or '요청에 지정된 외부 사이트/API'} · 방식={external_plan.get('method') or '공식 API 우선'} · 작업={external_plan.get('operation') or '외부 조회'}")
            lines.append(f"자료 수집 도구: Gemini Google Search · 상태: {external_plan.get('execution_status') or '실행 확인 후 수집 예정'}")
            lines.append("전송 범위: 공개 검색에 필요한 요청 문구만 Gemini로 전송합니다. 첨부 이미지 분석·대상 파일 내용은 검색 요청에서 제외합니다.")
            lines.append("Gemini API 사용량에 따라 Google 측 과금이 발생할 수 있습니다.")
            if external_plan.get("execution_note"):
                lines.append(external_plan["execution_note"])
            for source in (external_plan.get("search_sources") or [])[:5]:
                if isinstance(source, dict) and source.get("url"):
                    lines.append(f"• 검색 출처: {source.get('title') or source['url']} — {source['url']}")
        else:
            lines.append("외부 데이터 계획: 요청에 외부 사이트/API 조회가 명시되지 않음")
        lines.append(f"담당 팀(참여 에이전트): {', '.join(participants)}")
        lines.extend(["", "업무 분석(분야별):"])
        if decision.get("work_summary"):
            lines.append(f"• 업무 개요: {decision['work_summary']}")
        if decision.get("rationale"):
            lines.append(f"• 비서실장 판단: {decision['rationale']}")
        model_reviews = decision.get("model_reviews") or {}
        if model_reviews:
            local_review = model_reviews.get("local") or {}
            lines.extend(["", "로컬 LLM·Gemini 교차 검토"])
            lines.append("• 로컬 LLM 1차 검토: " + str(local_review.get("rationale") or local_review.get("work_summary") or "완료"))
            lines.append("• Gemini 독립 검토: " + str(model_reviews.get("gemini") or "결과 없음"))
            lines.append("• 비서실장 비교·최종 분석: " + str(decision.get("comparison_summary") or "비교 요약 없음"))
        requirements = decision.get("runtime_requirements") or []
        if requirements:
            lines.extend(["", "실행 전 준비사항:"])
            for item in requirements:
                state = "준비됨" if item.get("installed") else "설치/설정 필요"
                blocking = "실행 전 필수" if item.get("blocking") else "결과물 실행 시 필요"
                lines.append(f"• {item.get('name')}: {state} ({blocking}; {item.get('when_needed', '')})")
                if not item.get("installed"):
                    lines.append(f"  준비 방법: {item.get('install_command', '')}")
                    if item.get("setup_url"):
                        lines.append(f"  키 발급: {item['setup_url']}")
        for specialist in specialists:
            domain = self._korean_domain(specialist.get("specialty"))
            role_text = specialist.get("role") or specialist.get("deliverable") or "요청 분야 분석 및 산출물 작성"
            if not any("가" <= char <= "힣" for char in str(role_text)):
                role_text = f"{domain} 분야 분석 및 결과물 작성"
            lines.append(f"• {domain}: {role_text}")
        for role in core_roles:
            lines.append(f"• {self._korean_domain(role)}: {self._korean_agent_name(role, role)}가 담당")
        if result.get("image_analysis"):
            lines.extend(["", "첨부 이미지 VLM 분석:", result["image_analysis"]])
        steps = decision.get("work_steps") or []
        if steps:
            localized_steps = []
            for step in steps:
                step_text = str(step)
                for role in core_roles:
                    if isinstance(role, str) and role:
                        step_text = step_text.replace(role, self._korean_agent_name(role, role))
                localized_steps.append(step_text)
            lines.extend(["", "수행 단계:"] + [f"{i}. {step}" for i, step in enumerate(localized_steps, 1)])
        if decision.get('acceptance_criteria'):
            lines.extend(['', '합격 기준:']+[f"• {item}" for item in decision['acceptance_criteria']])
        if decision.get('verification_contract'):
            basis=decision['verification_contract'].get('source','UNSPECIFIED')
            basis_label={'PROPOSED':'모델 제안 — 대표 확인 전에는 실행·통과 판정하지 않음',
                         'USER':'대표가 직접 지정한 검증 기준',
                         'REPRESENTATIVE_CONFIRMED':'모델 제안을 대표가 확인한 검증 기준',
                         'LEGACY_EXPLICIT':'대표가 입력한 명시적 예제'}.get(basis,'검증 기준 미지정')
            lines.extend(['', '검증 기준 상태: '+basis_label,
                          '입력 유형: normal=정상 / invalid=잘못된 입력 / boundary=경계값'])
            lines.extend(['','검증 계약 · 실행 확인 시 아래 검증 기준도 확정됩니다:',
                          json.dumps(decision['verification_contract'],ensure_ascii=False,indent=2)])
        lines.extend(["", "QA 에이전트가 결과를 검토합니다."])
        self.decision_summary.setPlainText("\n".join(lines))

    def add_images(self, paths):
        previous_images = list(self.selected_images)
        supported = {".png", ".jpg", ".jpeg", ".webp"}
        total_bytes = sum(Path(value).stat().st_size for value in self.selected_images if Path(value).is_file())
        rejected = 0
        for value in paths:
            path = Path(value)
            if path.suffix.lower() not in supported or not path.is_file():
                rejected += 1
                continue
            absolute = str(path.resolve())
            if absolute not in self.selected_images and len(self.selected_images) < 10:
                reader = QtGui.QImageReader(absolute)
                size = path.stat().st_size
                if not reader.canRead() or size > 20 * 1024 * 1024 or total_bytes + size > 40 * 1024 * 1024:
                    rejected += 1
                    continue
                self.selected_images.append(absolute)
                total_bytes += size
        self.image_list.clear()
        for value in self.selected_images:
            path = Path(value)
            reader = QtGui.QImageReader(str(path))
            reader.setScaledSize(self.image_list.iconSize())
            image = reader.read()
            item = QtWidgets.QListWidgetItem(path.name)
            if not image.isNull():
                item.setIcon(QtGui.QIcon(QtGui.QPixmap.fromImage(image).scaled(
                    self.image_list.iconSize(), QtCore.Qt.AspectRatioMode.KeepAspectRatio,
                    QtCore.Qt.TransformationMode.SmoothTransformation,
                )))
                size = reader.size()
                item.setToolTip(f"{reader.format().data().decode('ascii', errors='ignore').upper()} · {size.width()} × {size.height()} px\n{path}")
            self.image_list.addItem(item)
        self.image_list.setVisible(bool(self.selected_images))
        if rejected:
            self.run_state.setText("읽을 수 없거나 용량 제한을 넘은 이미지는 제외했습니다.")
        if self.selected_images != previous_images:
            self.pending_analysis = None
            self.execute_button.setEnabled(False)

    def _image_context_menu(self, position):
        item = self.image_list.itemAt(position)
        if item is None:
            return
        row = self.image_list.row(item)
        menu = QtWidgets.QMenu(self.image_list)
        delete_action = menu.addAction("이 이미지 삭제")
        if menu.exec(self.image_list.mapToGlobal(position)) == delete_action:
            self.selected_images.pop(row)
            self.pending_analysis = None
            self.execute_button.setEnabled(False)
            self.add_images([])

    @staticmethod
    def _status_copy(result):
        status = result.get("status", "")
        if status == "AUTOMATION_SETUP_REQUIRED":
            return result.get("final_result") or "자동화 프로그램을 준비했습니다. API 키와 메일 설정을 등록한 뒤 실행 및 예약 등록을 진행하세요."
        if status == "CLOUD_APPROVAL_PENDING":
            return f"클라우드 승인 대기 · 요청 {result.get('cloud_approval_id', '—')} · 승인 전까지 외부 호출은 없습니다."
        if status == "APPROVED":
            return "완료 · QA 검토와 최종 승인이 끝났습니다."
        if status == "PENDING_APPROVAL":
            return "완료 · 대표 최종 승인을 기다립니다."
        return f"실행 상태: {status}"

    def _run_failed(self, message):
        if getattr(self,'_closing',False):return
        if hasattr(self,'elapsed_timer'):self.elapsed_timer.stop()
        self.health_button.setEnabled(True)
        self.selftest_button.setEnabled(True)
        self.run_progress.setVisible(False)
        self.run_button.setEnabled(True)
        self.run_button.setText("비서실장 업무 분석")
        self.execute_button.setEnabled(bool(self.pending_analysis))
        self.execute_button.setText("분석 확인 후 실행")
        self.run_state.setText("업무 실행 중 오류가 발생했습니다.")
        QtWidgets.QMessageBox.critical(self, "업무 실행 실패", message)

    def open_tk_approval_desk(self):
        if self.approval_process and self.approval_process.state() != QtCore.QProcess.ProcessState.NotRunning:
            self.approval_process.terminate()
        self.approval_process = QtCore.QProcess(self)
        self.approval_process.finished.connect(lambda *_: self.refresh_data())
        script = ROOT / "app" / "gui" / "tk_approval_desk.py"
        pythonw = Path(sys.executable).with_name("pythonw.exe")
        executable = str(pythonw if pythonw.exists() else Path(sys.executable))
        self.approval_process.start(executable, [str(script), "--db-path", self.db.db_path])
        if not self.approval_process.waitForStarted(3000):
            QtWidgets.QMessageBox.warning(self, "승인 작업대", "Tk 승인 창을 시작하지 못했습니다. tkinter 설치 여부를 확인해 주세요.")


def main():
    app = QtWidgets.QApplication.instance() or QtWidgets.QApplication(sys.argv)
    app.setApplicationName("ITNI AI Team")
    app.setStyle("Fusion")
    font_path = Path(os.environ.get("WINDIR", "C:/Windows")) / "Fonts" / "malgun.ttf"
    if font_path.exists():
        QtGui.QFontDatabase.addApplicationFont(str(font_path))
    family = "Malgun Gothic" if any("Malgun Gothic" in f for f in QtGui.QFontDatabase.families()) else "Segoe UI"
    app.setFont(QtGui.QFont(family, 10))
    window = Dashboard()
    window.show()
    window.raise_()
    window.activateWindow()
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())
