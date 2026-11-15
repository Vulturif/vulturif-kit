"""The Vulturif palettes, shared by every app, and switching between them.

Each app keeps its own `theme.py` with its own `qss()` (the rules differ per
app), but no longer its own colours: the tokens come from here. An app's
theme module binds itself once, and re-binds on a switch:

    # theme.py
    from vulturif_kit import themes as _themes

    def apply(theme_id) -> str:
        return _themes.apply(globals(), theme_id)

    apply(_themes.DEFAULT)

    def qss() -> str:
        return f"QWidget {{ background: {BG_DEEP}; ... border-radius: {RADIUS_CTRL}px }}"

apply() writes every token (BG_DEEP, ACCENT, CYAN, …) into the namespace it
is given, plus:

    THEME              the id that is on
    IS_DARK            False for a light palette (the title bar follows it)
    RADIUS_CARD        cards, scroll areas, dialogs' inner wells
    RADIUS_CTRL        buttons, inputs, list rows
    RADIUS_MID         tabs and lists, between the two
    RADIUS_SMALL       checkbox boxes, menu items
    FONT_FAMILY        the preferred face; FONT_FALLBACK when it is missing
    ACCENT_FILL        the primary button's background: ACCENT, or a gradient
    ACCENT_FILL_HOVER  the same, hovered

Widgets read tokens as module attributes (`T.ACCENT`) when they build or
paint, so after apply() the app re-sets its stylesheet and redraws what it
painted itself. The Qt side (the View → Theme menu) is themes_qt.

Token names keep the shared palette's slot names whatever hue a palette puts
there: CYAN is "the selection colour", MAGENTA "the third accent". So no widget
needs to know which palette is on. Qt-free, like the rest of the kit's core.
"""

DEFAULT = "night-city"
SETTINGS_KEY = "theme"           # what every app calls the setting

PALETTES = {
    # The original shared palette (Leashware → Goonwall → Stash-Lock → …),
    # every value given outright so the default look stays exactly as shipped.
    "night-city": {
        "name": "Night City", "dark": True, "font": "mono",
        "radius_card": 8, "radius_ctrl": 4, "gloss": False,
        "tokens": {
            # Surfaces
            "BG_DEEP": "#08080b",      # window background, behind everything
            "BG_PANEL": "#101018",     # cards / dialogs
            "BG_ELEV": "#181822",      # inputs, chips, list rows sitting on a panel
            "BG_HOVER": "#22222e",     # hover state for elevated surfaces
            "BG_SUNKEN": "#0b0b10",    # scroll areas, log/readout wells
            "BORDER": "#2b2b3a",       # default hairline
            "BORDER_DIM": "#1e1e28",   # barely-there separator
            # Accents
            "ACCENT": "#fcee0a",       # primary actions
            "ACCENT_HOVER": "#d9cd00",
            "ACCENT_DIM": "#7a7305",
            "ON_ACCENT": "#08080b",    # text on top of ACCENT fills
            "CYAN": "#00f0ff",         # secondary — selection, links, focus
            "CYAN_HOVER": "#00c4d1",
            "CYAN_SURFACE": "#0a2f38",
            "CYAN_SURFACE_HOVER": "#0e4653",
            "MAGENTA": "#b026ff",      # tertiary
            "MAGENTA_SURFACE": "#2c0a3d",
            "MAGENTA_SURFACE_HOVER": "#48106b",
            "ORANGE": "#ff5e1a",
            "ORANGE_SURFACE": "#43170a",
            "ORANGE_SURFACE_HOVER": "#66220d",
            # Status
            "OK": "#00ff9f",
            "OK_SURFACE": "#05362c",
            "OK_SURFACE_HOVER": "#0a5a49",
            "WARN": "#ffb300",
            "DANGER": "#ff2a6d",
            "DANGER_SURFACE": "#5c0a24",
            "DANGER_SURFACE_HOVER": "#8a0f36",
            # Text
            "TEXT": "#e6e6f0",
            "TEXT_DIM": "#8b8b9e",
            "TEXT_MUTED": "#565667",
            "TEXT_DISABLED": "#3d3d4a",
        },
    },
    # Oxblood, rose gold and a soft rose for selection; softer corners, sans.
    "velvet": {
        "name": "Velvet Room", "dark": True, "font": "sans",
        "radius_card": 12, "radius_ctrl": 7, "gloss": False,
        "tokens": {
            "BG_DEEP": "#120709", "BG_PANEL": "#1c0d11", "BG_ELEV": "#281519",
            "BG_HOVER": "#341c22", "BG_SUNKEN": "#0d0507",
            "BORDER": "#3f232a", "BORDER_DIM": "#2b171c",
            "ACCENT": "#eaa58e", "ACCENT_HOVER": "#d68d76", "ON_ACCENT": "#1a0a0d",
            "CYAN": "#ff7aa2", "CYAN_SURFACE": "#3a1422",
            "MAGENTA": "#c99bf0", "MAGENTA_SURFACE": "#2b1838",
            "ORANGE": "#ff8a5c", "ORANGE_SURFACE": "#3d1a10",
            "OK": "#7fd8a6", "WARN": "#f2c14e", "DANGER": "#ff4d6d",
            "TEXT": "#f4e7ea", "TEXT_DIM": "#b39299", "TEXT_MUTED": "#74585f",
        },
    },
    # Warm charcoal, brushed steel, oxblood stitching, brass; square corners.
    "leather": {
        "name": "Leather & Steel", "dark": True, "font": "mono",
        "radius_card": 2, "radius_ctrl": 2, "gloss": False,
        "tokens": {
            "BG_DEEP": "#0c0b0a", "BG_PANEL": "#161412", "BG_ELEV": "#201d1a",
            "BG_HOVER": "#2b2723", "BG_SUNKEN": "#090807",
            "BORDER": "#38322c", "BORDER_DIM": "#25211d",
            "ACCENT": "#c9ced6", "ACCENT_HOVER": "#aab1bc", "ON_ACCENT": "#0c0b0a",
            "CYAN": "#dc4a4a", "CYAN_SURFACE": "#3a1515",
            "MAGENTA": "#c9a34a", "MAGENTA_SURFACE": "#33280f",
            "ORANGE": "#d9773a", "ORANGE_SURFACE": "#3a1f0e",
            "OK": "#8fbf7a", "WARN": "#e0b252", "DANGER": "#ff5a4f",
            "TEXT": "#ece8e2", "TEXT_DIM": "#9b948b", "TEXT_MUTED": "#625b53",
        },
    },
    # One hue at many brightnesses, like an amber CRT; no rounding at all.
    "amber": {
        "name": "Amber Terminal", "dark": True, "font": "mono",
        "radius_card": 0, "radius_ctrl": 0, "gloss": False,
        "tokens": {
            "BG_DEEP": "#0a0703", "BG_PANEL": "#120d05", "BG_ELEV": "#1c1508",
            "BG_HOVER": "#271d0b", "BG_SUNKEN": "#070502",
            "BORDER": "#3d2c10", "BORDER_DIM": "#281d0b",
            "ACCENT": "#ffb000", "ACCENT_HOVER": "#e09a00", "ON_ACCENT": "#0a0703",
            "CYAN": "#ffd27a", "CYAN_SURFACE": "#33240a",
            "MAGENTA": "#ff8a2a", "MAGENTA_SURFACE": "#3a1f07",
            "ORANGE": "#e8603a", "ORANGE_SURFACE": "#3a170a",
            "OK": "#c9e85a", "WARN": "#ffd000", "DANGER": "#ff5533",
            "TEXT": "#ffcf85", "TEXT_DIM": "#b38c4c", "TEXT_MUTED": "#735a2b",
        },
    },
    # Wet-look black, hot pink and lavender; the primary button is glossy.
    "latex": {
        "name": "Latex", "dark": True, "font": "sans",
        "radius_card": 14, "radius_ctrl": 9, "gloss": True,
        "tokens": {
            "BG_DEEP": "#050506", "BG_PANEL": "#0f0d13", "BG_ELEV": "#18161e",
            "BG_HOVER": "#221f2b", "BG_SUNKEN": "#08080a",
            "BORDER": "#2d2937", "BORDER_DIM": "#1d1a24",
            "ACCENT": "#ff2e93", "ACCENT_HOVER": "#e61f7e", "ON_ACCENT": "#16000b",
            "CYAN": "#c4a1ff", "CYAN_SURFACE": "#261a3d",
            "MAGENTA": "#5ce1e6", "MAGENTA_SURFACE": "#0d2f33",
            "ORANGE": "#ff9f43", "ORANGE_SURFACE": "#3a2209",
            "OK": "#4be3a0", "WARN": "#ffc94a", "DANGER": "#ff4757",
            "TEXT": "#f5f1fb", "TEXT_DIM": "#9b93ac", "TEXT_MUTED": "#5c5669",
        },
    },
    # The light one: bone paper, ink-blue selection, a brass primary.
    "bone": {
        "name": "Brass & Bone", "dark": False, "font": "sans",
        "radius_card": 8, "radius_ctrl": 5, "gloss": False,
        "tokens": {
            "BG_DEEP": "#e9e3d8", "BG_PANEL": "#f7f3ec", "BG_ELEV": "#e6dfd2",
            "BG_HOVER": "#dbd2c3", "BG_SUNKEN": "#efe9df",
            "BORDER": "#cbc0ae", "BORDER_DIM": "#dcd3c4",
            "ACCENT": "#8a5d14", "ACCENT_HOVER": "#744d0f", "ON_ACCENT": "#fffaf0",
            "CYAN": "#2b5a88", "CYAN_SURFACE": "#d5dfea",
            "MAGENTA": "#8a3b6b", "MAGENTA_SURFACE": "#eedbe5",
            "ORANGE": "#b3502a", "ORANGE_SURFACE": "#f1ddd0",
            "OK": "#2c7a4c", "WARN": "#9c6400", "DANGER": "#b8233f",
            "TEXT": "#211c16", "TEXT_DIM": "#675d50", "TEXT_MUTED": "#94897a",
        },
    },
}

TOKEN_NAMES = tuple(PALETTES[DEFAULT]["tokens"])

# Windows faces; FONT_FALLBACK is the one every Windows install has.
FONTS = {"mono": ("Cascadia Mono", "Consolas"),
         "sans": ("Segoe UI Variable Text", "Segoe UI")}


def mix(a: str, b: str, t: float) -> str:
    """Blend #rrggbb a toward b by t (0 = a, 1 = b)."""
    ca = [int(a[i:i + 2], 16) for i in (1, 3, 5)]
    cb = [int(b[i:i + 2], 16) for i in (1, 3, 5)]
    return "#" + "".join(f"{round(x + (y - x) * t):02x}" for x, y in zip(ca, cb))


def ids() -> list[str]:
    """Every palette id, in menu order (the default first)."""
    return list(PALETTES)


def resolve(theme_id) -> str:
    """theme_id if it names a palette, else DEFAULT (an old or hand-edited
    setting must never stop the app starting)."""
    return theme_id if theme_id in PALETTES else DEFAULT


def name(theme_id) -> str:
    return PALETTES[resolve(theme_id)]["name"]


def is_dark(theme_id) -> bool:
    return PALETTES[resolve(theme_id)]["dark"]


def tokens(theme_id) -> dict:
    """The full colour set of one palette: its own colours plus the hover /
    surface shades it doesn't give outright, derived from them."""
    t = dict(PALETTES[resolve(theme_id)]["tokens"])
    derived = {
        "ACCENT_DIM": mix(t["ACCENT"], t["BG_DEEP"], 0.52),
        "CYAN_HOVER": mix(t["CYAN"], t["BG_DEEP"], 0.18),
        "CYAN_SURFACE_HOVER": mix(t["CYAN_SURFACE"], t["CYAN"], 0.15),
        "MAGENTA_SURFACE_HOVER": mix(t["MAGENTA_SURFACE"], t["MAGENTA"], 0.16),
        "ORANGE_SURFACE_HOVER": mix(t["ORANGE_SURFACE"], t["ORANGE"], 0.16),
        "OK_SURFACE": mix(t["OK"], t["BG_PANEL"], 0.80),
        "OK_SURFACE_HOVER": mix(t["OK"], t["BG_PANEL"], 0.68),
        "DANGER_SURFACE": mix(t["DANGER"], t["BG_PANEL"], 0.72),
        "DANGER_SURFACE_HOVER": mix(t["DANGER"], t["BG_PANEL"], 0.58),
        "TEXT_DISABLED": mix(t["TEXT_MUTED"], t["BG_DEEP"], 0.40),
    }
    for k, v in derived.items():
        t.setdefault(k, v)
    return t


def values(theme_id) -> dict:
    """Everything apply() writes: the tokens plus shape, type and the extras."""
    theme_id = resolve(theme_id)
    p = PALETTES[theme_id]
    t = tokens(theme_id)
    rc, rk = p["radius_card"], p["radius_ctrl"]
    v = dict(t)
    v.update(THEME=theme_id, IS_DARK=p["dark"],
             RADIUS_CARD=rc, RADIUS_CTRL=rk,
             RADIUS_MID=round((rc + rk) / 2), RADIUS_SMALL=min(3, rk))
    v["FONT_FAMILY"], v["FONT_FALLBACK"] = FONTS[p["font"]]
    if p["gloss"]:
        # A two-stop gradient reads as a shiny button; everything else stays flat.
        def gloss(c, lift):
            return (f"qlineargradient(x1:0, y1:0, x2:0, y2:1, "
                    f"stop:0 {mix(c, '#ffffff', lift)}, stop:1 {c})")
        v["ACCENT_FILL"] = gloss(t["ACCENT"], 0.28)
        v["ACCENT_FILL_HOVER"] = gloss(t["ACCENT_HOVER"], 0.22)
    else:
        v["ACCENT_FILL"] = t["ACCENT"]
        v["ACCENT_FILL_HOVER"] = t["ACCENT_HOVER"]
    return v


def apply(namespace: dict, theme_id) -> str:
    """Write theme_id's values into namespace (an app's theme module's
    globals()). Unknown ids fall back to DEFAULT. Returns the id applied."""
    v = values(theme_id)
    namespace.update(v)
    return v["THEME"]
