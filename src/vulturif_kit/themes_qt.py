"""The Qt half of themes: the View → Theme menu and the font pick.

Kept apart from themes.py so the kit's core stays Qt-free; PySide6 is
imported only when one of these is called, and every Vulturif app has it.

    from vulturif_kit import themes, themes_qt
    import theme as T

    self.theme_actions = themes_qt.add_theme_menu(view_menu, T.THEME, self._set_theme)

    def _set_theme(self, theme_id):
        theme_id = T.apply(theme_id)
        settings["theme"] = theme_id
        themes_qt.restyle(T.qss())
        ...redraw what the app painted itself...
"""

from . import themes


def font_family(namespace: dict) -> str:
    """The first of namespace's FONT_FAMILY / FONT_FALLBACK that is installed
    (needs a QApplication). Falls back to FONT_FALLBACK without asking Qt."""
    want, fallback = namespace["FONT_FAMILY"], namespace["FONT_FALLBACK"]
    try:
        from PySide6.QtGui import QFontDatabase
        fams = set(QFontDatabase.families())
        for f in (want, fallback):
            if f in fams:
                return f
    except Exception:
        pass
    return fallback


def add_theme_menu(menu, current: str, on_pick, title: str = "Theme") -> dict:
    """Add a "Theme" submenu to menu: one checkable entry per palette, exactly
    one checked. on_pick(theme_id) runs when the user picks one. Returns
    {theme_id: QAction} so the app can re-check an entry itself."""
    from PySide6.QtGui import QAction, QActionGroup

    sub = menu.addMenu(title)
    group = QActionGroup(sub)
    group.setExclusive(True)
    sub._vk_theme_group = group          # keep the group alive with the menu
    actions = {}
    current = themes.resolve(current)
    for tid in themes.ids():
        act = QAction(themes.name(tid), sub, checkable=True)
        act.setChecked(tid == current)
        act.triggered.connect(lambda _on=False, t=tid: on_pick(t))
        group.addAction(act)
        sub.addAction(act)
        actions[tid] = act
    return actions


def restyle(qss: str) -> None:
    """Re-set the application stylesheet (after the theme module re-bound) and
    repaint every window, so widgets that paint from tokens pick up the new
    colours too. What the app *built* from a token (a bar's colour, a label's
    own stylesheet) it still rebuilds itself.

    Does nothing when the stylesheet is already this one: re-setting it
    re-polishes every widget in the process, which is slow with many windows
    open and pointless at a start that keeps the default."""
    from PySide6.QtWidgets import QApplication
    app = QApplication.instance()
    if app is None or app.styleSheet() == qss:
        return
    app.setStyleSheet(qss)
    for w in app.topLevelWidgets():
        w.update()
