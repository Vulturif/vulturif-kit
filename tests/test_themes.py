import re

import pytest

from vulturif_kit import themes

HEX = re.compile(r"^#[0-9a-f]{6}$")


def test_every_palette_fills_every_token():
    for tid in themes.ids():
        t = themes.tokens(tid)
        assert set(t) == set(themes.TOKEN_NAMES), tid
        for k, v in t.items():
            assert HEX.match(v), f"{tid}.{k} = {v!r}"


def test_default_is_first_and_kept_exactly():
    assert themes.ids()[0] == themes.DEFAULT
    t = themes.tokens(themes.DEFAULT)
    # The shipped look of every app before themes existed: never derived.
    assert t["ACCENT"] == "#fcee0a" and t["ACCENT_DIM"] == "#7a7305"
    assert t["TEXT_DISABLED"] == "#3d3d4a"
    v = themes.values(themes.DEFAULT)
    assert (v["RADIUS_CARD"], v["RADIUS_CTRL"]) == (8, 4)
    assert (v["RADIUS_MID"], v["RADIUS_SMALL"]) == (6, 3)
    assert v["FONT_FAMILY"] == "Cascadia Mono"
    assert v["ACCENT_FILL"] == "#fcee0a"


def test_apply_writes_into_the_namespace():
    ns = {}
    assert themes.apply(ns, "bone") == "bone"
    assert ns["THEME"] == "bone" and ns["IS_DARK"] is False
    assert ns["BG_DEEP"] == themes.PALETTES["bone"]["tokens"]["BG_DEEP"]
    for k in ("RADIUS_CARD", "RADIUS_CTRL", "RADIUS_MID", "RADIUS_SMALL",
              "FONT_FAMILY", "FONT_FALLBACK", "ACCENT_FILL", "ACCENT_FILL_HOVER"):
        assert k in ns


@pytest.mark.parametrize("bad", [None, "", "no-such-theme", 3])
def test_unknown_ids_fall_back_to_default(bad):
    ns = {}
    assert themes.apply(ns, bad) == themes.DEFAULT
    assert ns["ACCENT"] == "#fcee0a"


def test_gloss_is_a_qt_gradient():
    v = themes.values("latex")
    assert v["ACCENT_FILL"].startswith("qlineargradient(")
    assert v["ACCENT_FILL_HOVER"].startswith("qlineargradient(")


def test_names_are_unique():
    names = [themes.name(t) for t in themes.ids()]
    assert len(set(names)) == len(names)


def test_core_stays_qt_free():
    import sys
    import importlib
    before = {m for m in sys.modules if m.startswith("PySide6")}
    importlib.reload(themes)
    after = {m for m in sys.modules if m.startswith("PySide6")}
    assert after == before


def test_theme_menu_when_qt_is_there():
    pytest.importorskip("PySide6")
    import os
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtWidgets import QApplication, QMenu
    from vulturif_kit import themes_qt

    app = QApplication.instance() or QApplication([])
    picked = []
    menu = QMenu()
    acts = themes_qt.add_theme_menu(menu, "velvet", picked.append)
    assert list(acts) == themes.ids()
    assert acts["velvet"].isChecked()
    acts["amber"].trigger()
    assert picked == ["amber"]
    assert acts["amber"].isChecked() and not acts["velvet"].isChecked()
    assert themes_qt.font_family(themes.values("night-city")) in ("Cascadia Mono", "Consolas")
    menu.deleteLater()
    app.processEvents()
