"""vulturif.toml: what one app's release is made of.

    app = "Goonwall"                    # site name, SHA256SUMS, User-Agent
    slug = "goonwall"                   # default: app in lower case
    version_file = "version.py"         # holds __version__ = "1.2.3"
    release_repo = "Vulturif/Goonwall"  # the public repo the site syncs from

    [build]
    spec = "Goonwall.spec"              # PyInstaller one-file build, or
    command = ["{python}", "tools/build_exe.py"]   # the app's own build
    dist = "dist"
    assets = ["Goonwall.exe", "GoonwallHost.exe"]  # main exe first
    zip = { "Goonwall-extension.zip" = "extension" }  # name = folder in dist

    [docs]                              # mirrored into the public repo
    readme = "README.md"
    screenshots = "docs/screenshots"

    [sync]
    ssh = "vulturif@diphda.uberspace.de"
    dir = "~/publicdistribution"
    wait = 600                          # seconds to wait for the site

Only `app` is required; a spec named "<App>.spec" is found by itself.
"""

import re
import sys
import tomllib
from dataclasses import dataclass, field
from pathlib import Path

FILE = "vulturif.toml"
_VERSION = re.compile(r"""__version__\s*=\s*["']([^"']+)["']""")


class ConfigError(Exception):
    pass


@dataclass
class Config:
    root: Path
    app: str
    slug: str
    version_file: Path
    release_repo: str
    spec: Path | None = None
    command: list | None = None
    dist: Path = None
    assets: list = field(default_factory=list)
    zips: dict = field(default_factory=dict)
    readme: Path | None = None
    screenshots: Path | None = None
    ssh: str = ""
    site_dir: str = "~/publicdistribution"
    wait: int = 600
    site: str = "https://vulturif.com"

    @property
    def version(self) -> str:
        m = _VERSION.search(self.version_file.read_text(encoding="utf-8"))
        if not m:
            raise ConfigError(f"no __version__ = \"…\" in {self.version_file}")
        return m.group(1)

    @property
    def tag(self) -> str:
        return f"v{self.version}"

    def released_files(self) -> list[Path]:
        """Everything that goes into the release, main exe first."""
        return [self.dist / name for name in [*self.assets, *self.zips]]

    def build_command(self) -> list[str]:
        return [part.replace("{python}", sys.executable) for part in self.command]


def find(start=None) -> Path:
    here = Path(start or Path.cwd()).resolve()
    for folder in (here, *here.parents):
        if (folder / FILE).exists():
            return folder / FILE
    raise ConfigError(f"no {FILE} here or above — `vkit init` writes one")


def load(path=None) -> Config:
    path = Path(path) if path else find()
    try:
        data = tomllib.loads(path.read_text(encoding="utf-8"))
    except tomllib.TOMLDecodeError as e:
        raise ConfigError(f"{path}: {e}") from None
    root = path.parent
    app = data.get("app")
    if not app:
        raise ConfigError(f"{path}: `app` is required")
    build, docs, sync = data.get("build", {}), data.get("docs", {}), data.get("sync", {})

    spec = build.get("spec")
    command = build.get("command")
    if isinstance(command, str):
        command = command.split()
    if not (spec or command) and (root / f"{app}.spec").exists():
        spec = f"{app}.spec"
    if not (spec or command):
        raise ConfigError(f"{path}: [build] needs `spec` or `command`")

    config = Config(
        root=root, app=app, slug=data.get("slug") or app.lower(),
        version_file=root / data.get("version_file", "version.py"),
        release_repo=data.get("release_repo") or f"Vulturif/{app}",
        spec=root / spec if spec else None, command=command,
        dist=root / build.get("dist", "dist"),
        assets=list(build.get("assets") or [f"{app}.exe"]),
        zips=dict(build.get("zip") or {}),
        readme=root / docs["readme"] if docs.get("readme") else None,
        screenshots=root / docs["screenshots"] if docs.get("screenshots") else None,
        ssh=sync.get("ssh", ""), site_dir=sync.get("dir", "~/publicdistribution"),
        wait=int(sync.get("wait", 600)),
        site=(data.get("site") or "https://vulturif.com").rstrip("/"),
    )
    if not config.version_file.exists():
        raise ConfigError(f"{path}: no {config.version_file.name} at {config.version_file}")
    return config


def set_version(config: Config, version: str):
    """Write `version` into the version file, keeping everything around it."""
    if not re.fullmatch(r"\d+\.\d+\.\d+", version):
        raise ConfigError(f"{version!r} is not three numbers — the updater "
                          "compares versions part by part")
    text = config.version_file.read_text(encoding="utf-8")
    new, n = _VERSION.subn(f'__version__ = "{version}"', text, count=1)
    if not n:
        raise ConfigError(f"no __version__ in {config.version_file}")
    config.version_file.write_text(new, encoding="utf-8")
