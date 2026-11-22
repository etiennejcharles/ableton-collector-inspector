from __future__ import annotations

# A compact, contrast-checked studio palette. Status colors are intentionally
# paired with text labels throughout the UI; they are never the only signal.
COLORS = {
    "background": "#141518",
    "workspace": "#17191D",
    "surface": "#1E2126",
    "surface_raised": "#25292F",
    "surface_hover": "#30353D",
    "surface_pressed": "#393128",
    "divider": "#3B414A",
    "divider_soft": "#2C3037",
    "text": "#F3F1EC",
    "muted": "#ADB2BC",
    "subtle": "#7F8691",
    "amber": "#F0AA4B",
    "amber_hover": "#F7BA67",
    "amber_pressed": "#D99336",
    "amber_text": "#21170B",
    "green": "#62CC8F",
    "yellow": "#E6C75A",
    "pink": "#F184A2",
    "red": "#F07178",
    "blue": "#8DAAF8",
    "focus": "#FFD28A",
    "selection": "#4A3A25",
    "selection_hover": "#55442D",
    "white": "#FFFFFF",
    # Backwards-compatible semantic aliases used by report HTML.
    "chrome": "#141518",
    "toolbar": "#1E2126",
    "panel": "#25292F",
    "error": "#F07178",
}

STATUS_COLORS = {
    "collectable": COLORS["yellow"],
    "safe": COLORS["green"],
    "blocked_missing": COLORS["pink"],
    "blocked": COLORS["pink"],
    "error": COLORS["red"],
}

STATUS_LABELS = {
    "collectable": "READY",
    "safe": "SAFE",
    "blocked_missing": "BLOCKED",
    "blocked": "BLOCKED",
    "error": "ERROR",
}


APP_STYLE = f"""
* {{
    font-family: system-ui, -apple-system, "Segoe UI", sans-serif;
    font-size: 12px;
    color: {COLORS["text"]};
}}

QMainWindow,
QDialog {{
    background: {COLORS["background"]};
}}

QWidget#launchPage,
QWidget#workspacePage,
QWidget#historyPage,
QWidget#centralCanvas {{
    background: {COLORS["workspace"]};
}}

QWidget#topBar {{
    background: {COLORS["surface"]};
    border-bottom: 1px solid {COLORS["divider_soft"]};
}}

QLabel#brandLabel {{
    color: {COLORS["text"]};
    font-size: 11px;
    font-weight: 700;
    letter-spacing: 1.2px;
}}

QLabel#sourceLabel {{
    color: {COLORS["muted"]};
    font-size: 12px;
}}

QLabel#mutedLabel,
QLabel#selectionSummary,
QLabel#dialogDetail {{
    color: {COLORS["muted"]};
}}

QLabel#scanState {{
    color: {COLORS["muted"]};
    font-size: 10px;
    font-weight: 700;
    letter-spacing: 0.8px;
    padding: 4px 8px;
    background: {COLORS["surface_raised"]};
    border: 1px solid {COLORS["divider"]};
    border-radius: 10px;
}}

QFrame#dropFrame {{
    background: {COLORS["surface"]};
    border: 1px solid {COLORS["divider"]};
    border-radius: 16px;
}}

QFrame#dropFrame[dropActive="true"] {{
    background: #27251F;
    border: 2px solid {COLORS["amber"]};
}}

QLabel#heroEyebrow {{
    color: {COLORS["amber"]};
    font-size: 10px;
    font-weight: 700;
    letter-spacing: 1.5px;
}}

QLabel#dropTitle,
QLabel#dialogTitle {{
    color: {COLORS["text"]};
    font-size: 22px;
    font-weight: 700;
}}

QLabel#dropHint {{
    color: {COLORS["muted"]};
    font-size: 13px;
    line-height: 1.4;
}}

QLabel#reassuranceLabel {{
    color: {COLORS["muted"]};
    font-size: 11px;
    padding: 6px 9px;
    background: {COLORS["surface_raised"]};
    border: 1px solid {COLORS["divider_soft"]};
    border-radius: 12px;
}}

QPushButton {{
    min-height: 30px;
    padding: 2px 12px;
    background: {COLORS["surface_raised"]};
    border: 1px solid {COLORS["divider"]};
    border-radius: 6px;
}}

QPushButton:hover {{
    background: {COLORS["surface_hover"]};
    border-color: #59616D;
}}

QPushButton:pressed {{
    background: {COLORS["surface_pressed"]};
}}

QPushButton:focus {{
    border: 2px solid {COLORS["focus"]};
    padding: 1px 11px;
}}

QPushButton:disabled {{
    color: {COLORS["subtle"]};
    background: #202329;
    border-color: #343942;
}}

QPushButton#primaryButton,
QPushButton#heroPrimaryButton {{
    background: {COLORS["amber"]};
    color: {COLORS["amber_text"]};
    border-color: {COLORS["amber"]};
    font-weight: 700;
}}

QPushButton#heroPrimaryButton {{
    min-height: 40px;
    font-size: 13px;
    border-radius: 8px;
}}

QPushButton#heroSecondaryButton {{
    min-height: 38px;
    font-weight: 650;
    border-radius: 8px;
}}

QPushButton#heroTertiaryButton {{
    min-height: 34px;
    color: {COLORS["muted"]};
    background: transparent;
    border-color: transparent;
}}

QPushButton#heroTertiaryButton:hover {{
    color: {COLORS["text"]};
    background: {COLORS["surface_raised"]};
    border-color: {COLORS["divider"]};
}}

QPushButton#primaryButton:hover,
QPushButton#heroPrimaryButton:hover {{
    background: {COLORS["amber_hover"]};
    border-color: {COLORS["amber_hover"]};
}}

QPushButton#primaryButton:pressed,
QPushButton#heroPrimaryButton:pressed {{
    background: {COLORS["amber_pressed"]};
    border-color: {COLORS["amber_pressed"]};
}}

QPushButton#primaryButton:disabled {{
    color: {COLORS["subtle"]};
    background: #2A2C2F;
    border-color: #3A3D42;
}}

QPushButton#filterButton {{
    min-width: 0;
    min-height: 28px;
    padding: 1px 11px;
    color: {COLORS["muted"]};
    background: transparent;
    border: 1px solid {COLORS["divider"]};
    border-radius: 14px;
    font-size: 11px;
    font-weight: 650;
}}

QPushButton#filterButton:hover {{
    color: {COLORS["text"]};
    background: {COLORS["surface_raised"]};
}}

QPushButton#filterButton:checked {{
    color: {COLORS["amber_text"]};
    background: {COLORS["amber"]};
    border-color: {COLORS["amber"]};
}}

QWidget#metricStrip,
QWidget#filterStrip {{
    background: {COLORS["workspace"]};
}}

QWidget#actionStrip {{
    background: {COLORS["surface"]};
    border-top: 1px solid {COLORS["divider"]};
}}

QFrame#metricCell {{
    background: {COLORS["surface"]};
    border: 1px solid {COLORS["divider_soft"]};
    border-radius: 10px;
}}

QLabel#metricValue {{
    color: {COLORS["text"]};
    font-size: 23px;
    font-weight: 700;
}}

QLabel#metricName {{
    color: {COLORS["muted"]};
    font-size: 10px;
    font-weight: 700;
    letter-spacing: 0.7px;
}}

QFrame#issueBanner {{
    background: #34242A;
    border: 1px solid #6E3A49;
    border-radius: 8px;
}}

QLabel#issueBannerText {{
    color: #FFC1D1;
}}

QFrame#tableCard,
QFrame#inspectorCard {{
    background: {COLORS["surface"]};
    border: 1px solid {COLORS["divider_soft"]};
    border-radius: 10px;
}}

QLabel#panelTitle {{
    color: {COLORS["muted"]};
    font-size: 10px;
    font-weight: 700;
    letter-spacing: 1px;
}}

QLabel#historyTitle {{
    color: {COLORS["amber"]};
    font-size: 11px;
    font-weight: 700;
    letter-spacing: 1px;
}}

QLabel#historyWarning {{
    margin: 10px 16px 0 16px;
    padding: 9px 12px;
    color: #FFC1D1;
    background: #34242A;
    border: 1px solid #6E3A49;
    border-radius: 8px;
}}

QListWidget#historyList,
QTextBrowser#historyInspector {{
    color: {COLORS["text"]};
    background: {COLORS["surface"]};
    border: 1px solid {COLORS["divider_soft"]};
    border-radius: 10px;
    outline: 0;
}}

QListWidget#historyList {{
    alternate-background-color: #22262B;
    padding: 4px;
}}

QListWidget#historyList::item {{
    padding: 7px 9px;
    border-bottom: 1px solid {COLORS["divider_soft"]};
}}

QListWidget#historyList::item:hover {{
    background: {COLORS["surface_hover"]};
}}

QListWidget#historyList::item:selected {{
    color: {COLORS["text"]};
    background: {COLORS["selection"]};
}}

QTextBrowser#historyInspector {{
    padding: 11px;
}}

QTableView {{
    background: {COLORS["surface"]};
    alternate-background-color: #22262B;
    border: 0;
    border-radius: 9px;
    gridline-color: transparent;
    selection-background-color: {COLORS["selection"]};
    selection-color: {COLORS["text"]};
    outline: 0;
}}

QTableView::item {{
    min-height: 38px;
    padding: 3px 7px;
    border-bottom: 1px solid {COLORS["divider_soft"]};
}}

QTableView::item:hover {{
    background: #292D33;
}}

QTableView::item:selected {{
    background: {COLORS["selection"]};
    color: {COLORS["text"]};
}}

QTableView:focus {{
    border: 2px solid {COLORS["focus"]};
}}

QHeaderView::section {{
    height: 32px;
    padding: 2px 7px;
    color: {COLORS["muted"]};
    background: {COLORS["surface_raised"]};
    border: 0;
    border-right: 1px solid {COLORS["divider_soft"]};
    border-bottom: 1px solid {COLORS["divider"]};
    font-size: 10px;
    font-weight: 700;
}}

QWidget#tableState {{
    background: {COLORS["surface"]};
}}

QLabel#stateSymbol {{
    color: {COLORS["amber"]};
    font-size: 26px;
    font-weight: 700;
}}

QLabel#stateTitle {{
    color: {COLORS["text"]};
    font-size: 16px;
    font-weight: 700;
}}

QLabel#stateBody {{
    color: {COLORS["muted"]};
}}

QTextBrowser#inspectorBrowser,
QTextBrowser#pluginsInspectorBrowser,
QTextBrowser#dialogBrowser {{
    background: transparent;
    border: 0;
    padding: 4px;
}}

QWidget#pluginFilterBar {{
    border-bottom: 1px solid {COLORS["divider_soft"]};
}}

QLineEdit#pluginSearch,
QComboBox#pluginTypeFilter {{
    min-height: 28px;
    color: {COLORS["text"]};
    background: {COLORS["surface_raised"]};
    border: 1px solid {COLORS["divider"]};
    border-radius: 6px;
    padding: 1px 8px;
}}

QComboBox#pluginTypeFilter {{
    min-width: 88px;
    padding-right: 22px;
}}

QComboBox#pluginTypeFilter QAbstractItemView {{
    color: {COLORS["text"]};
    background: {COLORS["surface_raised"]};
    border: 1px solid {COLORS["divider"]};
    selection-color: {COLORS["text"]};
    selection-background-color: {COLORS["selection"]};
    outline: 0;
}}

QLineEdit#pluginSearch:focus,
QComboBox#pluginTypeFilter:focus {{
    border-color: {COLORS["amber"]};
}}

QLineEdit#pluginSearch:disabled,
QComboBox#pluginTypeFilter:disabled {{
    color: {COLORS["muted"]};
    background: {COLORS["surface"]};
}}

QLabel#pluginResultsLabel {{
    min-width: 58px;
    color: {COLORS["muted"]};
    font-size: 10px;
    font-weight: 700;
}}

QTabWidget#inspectorTabs::pane {{
    border: 0;
    border-top: 1px solid {COLORS["divider_soft"]};
}}

QTabWidget#inspectorTabs QTabBar::tab {{
    min-height: 28px;
    padding: 2px 12px;
    color: {COLORS["muted"]};
    background: transparent;
    border: 0;
    border-bottom: 2px solid transparent;
    font-size: 11px;
    font-weight: 700;
}}

QTabWidget#inspectorTabs QTabBar::tab:hover {{
    color: {COLORS["text"]};
    background: {COLORS["surface_raised"]};
}}

QTabWidget#inspectorTabs QTabBar::tab:selected {{
    color: {COLORS["amber"]};
    border-bottom-color: {COLORS["amber"]};
}}

QTextBrowser#dialogBrowser {{
    background: {COLORS["surface"]};
    border: 1px solid {COLORS["divider_soft"]};
    border-radius: 8px;
    padding: 9px;
}}

QSplitter::handle {{
    width: 10px;
    background: transparent;
}}

QProgressBar {{
    min-height: 12px;
    max-height: 12px;
    border: 1px solid {COLORS["divider"]};
    border-radius: 6px;
    background: {COLORS["surface_raised"]};
    text-align: center;
    color: transparent;
}}

QProgressBar::chunk {{
    background: {COLORS["amber"]};
    border-radius: 5px;
}}

QProgressBar#scanProgress {{
    min-width: 76px;
    max-width: 76px;
    min-height: 6px;
    max-height: 6px;
    border-radius: 3px;
}}

QProgressBar#scanProgress::chunk {{
    border-radius: 2px;
}}

QFrame#warningCard {{
    background: #322B1F;
    border: 1px solid #695537;
    border-radius: 8px;
}}

QLabel#warningText {{
    color: #F5D69B;
}}

QScrollBar:vertical {{
    width: 11px;
    background: transparent;
    margin: 2px;
}}

QScrollBar::handle:vertical {{
    min-height: 26px;
    background: #535A65;
    border-radius: 4px;
}}

QScrollBar::handle:vertical:hover {{
    background: #68717E;
}}

QScrollBar::add-line:vertical,
QScrollBar::sub-line:vertical {{
    height: 0;
}}

QToolTip {{
    color: {COLORS["text"]};
    background: {COLORS["surface_raised"]};
    border: 1px solid {COLORS["divider"]};
    padding: 5px;
}}

QMessageBox {{
    background: {COLORS["background"]};
}}
"""
