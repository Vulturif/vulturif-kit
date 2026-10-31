"""What every Vulturif app shares, in one place instead of a copy per app.

Leashware and Goonwall each carried their own updater.py, updatesig.py,
analytics.py, errorreport.py and a DPAPI store, kept "equal by hand". This is
those modules once, with the app's name, version and file names passed in
rather than imported from a version.py next to them:

    from vulturif_kit import App
    from version import __version__

    kit = App("Goonwall", __version__, assets=("Goonwall.exe", "GoonwallHost.exe"))
    kit.errors.install()
    kit.analytics.start(settings.get("usage_stats", False))
    update = kit.updater.check()          # on a worker

Everything here is Qt-free and leaves the asking to the app's own dialogs.
The release side (build, sign, GitHub release, vulturif.com sync) is the
`vkit` command in vulturif_kit.release, which the exe never imports.
"""

from .app import PUBLIC_KEY, SITE, App
from .signature import BadSignature
from .updater import Asset, Update

__version__ = "0.1.0"

__all__ = ["App", "Asset", "BadSignature", "PUBLIC_KEY", "SITE", "Update",
           "__version__"]
