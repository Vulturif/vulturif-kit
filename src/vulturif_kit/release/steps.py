"""The steps of a release, in the order `vkit release` runs them.

    preflight  version, tag, clean tree, gh login, signing key — everything
               that would otherwise fail after a ten-minute build
    build      PyInstaller (or the app's own build), then the zips
    sign       SHA256SUMS + SHA256SUMS.sig, checked against the pinned key
    publish    the GitHub release on the public repo, created or replaced
    docs       README and screenshots mirrored into the public repo
    sync       vulturif.com imports the release, and its answer is checked
               the way an installed copy will check it

Each step can run on its own (`vkit build`, `vkit sign`, …), which is what
the CI workflow does, and none of them keeps state between runs beyond the
files in dist/.
"""

import base64
import hashlib
import os
import shutil
import subprocess
import sys
import tempfile
import time
from dataclasses import dataclass
from pathlib import Path

from .. import signature
from ..app import PUBLIC_KEY
from .config import Config

ORDER = ("preflight", "build", "sign", "publish", "docs", "sync")
KEY_ENV = "UPDATE_SIGNING_KEY"
SUMS, SIG = "SHA256SUMS", "SHA256SUMS.sig"


class StepError(Exception):
    pass


@dataclass
class Context:
    dry_run: bool = False
    ci: bool = False
    notes: str | None = None
    key_file: Path | None = None
    allow_unsigned: bool = False
    allow_dirty: bool = False
    push_tag: bool = False
    public_key: str = PUBLIC_KEY

    def say(self, text: str):
        print(text, flush=True)

    def run(self, cmd, cwd=None, check=True, capture=False, changes=True):
        """Run `cmd`. A dry run only prints the ones that change something."""
        shown = " ".join(str(c) for c in cmd)
        if changes and self.dry_run:
            self.say(f"   (dry run) {shown}")
            return subprocess.CompletedProcess(cmd, 0, "", "")
        if not capture:
            self.say(f"   $ {shown}")
        try:
            result = subprocess.run([str(c) for c in cmd], cwd=cwd, text=True,
                                    capture_output=capture, encoding="utf-8",
                                    errors="replace")
        except FileNotFoundError:
            raise StepError(f"{cmd[0]} is not installed or not on PATH") from None
        if check and result.returncode:
            detail = (result.stderr or result.stdout or "").strip() if capture else ""
            raise StepError(f"{shown} failed ({result.returncode})"
                            + (f": {detail}" if detail else ""))
        return result


def _git(ctx, config, *args, **kw):
    return ctx.run(["git", *args], cwd=config.root, capture=True, **kw)


def _tag_message(ctx, config) -> str | None:
    """An annotated tag's message, which is what the release notes are."""
    if _git(ctx, config, "cat-file", "-t", config.tag, check=False,
            changes=False).stdout.strip() != "tag":
        return None
    text = _git(ctx, config, "tag", "-l", "--format=%(contents)", config.tag,
                changes=False).stdout.strip()
    return text or None


def notes(ctx, config) -> str:
    return ctx.notes or _tag_message(ctx, config) or f"{config.app} {config.tag}"


def signing_key(ctx) -> bytes | None:
    if ctx.key_file:
        text = Path(ctx.key_file).read_text(encoding="utf-8").strip()
    else:
        text = os.environ.get(KEY_ENV, "").strip()
    if not text:
        return None
    try:
        key = base64.b64decode(text, validate=True)
    except ValueError:
        raise StepError(f"{KEY_ENV} is not base64") from None
    if len(key) != 32:
        raise StepError(f"{KEY_ENV} is {len(key)} bytes, an Ed25519 key is 32")
    return key


# --- preflight ---------------------------------------------------------------

def preflight(ctx, config: Config):
    version = config.version
    if not all(p.isdigit() for p in version.split(".")) or version.count(".") != 2:
        raise StepError(f"version {version!r} is not three numbers")
    ctx.say(f"   {config.app} {config.tag} -> {config.release_repo}")

    if ctx.ci:
        # The updater compares the version with the latest tag. A build whose
        # version is behind its own tag would offer itself as an update to
        # itself at every start, so a mismatch fails here instead.
        ref = os.environ.get("GITHUB_REF_NAME", "")
        if os.environ.get("GITHUB_REF_TYPE") == "tag" and ref != config.tag:
            raise StepError(f"tag {ref!r} does not match the version file "
                            f"(expected {config.tag!r}) — bump it first")
    else:
        _local_tag(ctx, config)

    if ctx.run(["gh", "auth", "status"], capture=True, check=False,
               changes=False).returncode:
        raise StepError("gh is not logged in (gh auth login, or GH_TOKEN)")
    if signing_key(ctx) is None:
        if not ctx.allow_unsigned:
            raise StepError(f"no signing key: set {KEY_ENV} or pass --key-file. "
                            "The apps refuse unsigned releases; --allow-unsigned "
                            "only for the one release that brings signing")
        ctx.say("   warning: this release goes out unsigned")


def _local_tag(ctx, config):
    """Build exactly what the tag says: a clean tree, at the tag."""
    if _git(ctx, config, "rev-parse", "--git-dir", check=False,
            changes=False).returncode:
        ctx.say("   warning: not a git repository, no tag is checked or made")
        return
    if not ctx.allow_dirty and _git(ctx, config, "status", "--porcelain",
                                    "--untracked-files=no",
                                    changes=False).stdout.strip():
        raise StepError("uncommitted changes — commit them, or --allow-dirty")
    head = _git(ctx, config, "rev-parse", "HEAD", changes=False).stdout.strip()
    tagged = _git(ctx, config, "rev-parse", "-q", "--verify",
                  f"refs/tags/{config.tag}^{{commit}}", check=False,
                  changes=False).stdout.strip()
    if tagged and tagged != head:
        raise StepError(f"{config.tag} already exists on another commit "
                        f"({tagged[:8]}); check it out or bump the version")
    if not tagged:
        ctx.say(f"   tagging {config.tag} on {head[:8]}")
        _git(ctx, config, "tag", "-a", config.tag, "-m", notes(ctx, config))
    if ctx.push_tag:
        _git(ctx, config, "push", "origin", f"refs/tags/{config.tag}")


# --- build -------------------------------------------------------------------

def build(ctx, config: Config):
    if ctx.dry_run:
        ctx.say(f"   (dry run) build into {config.dist}")
        return
    # Emptied first, so a folder left by an older build cannot be mistaken
    # for part of this one. Errors are not ignored: an exe still running
    # holds its file, and PyInstaller would only fail on it minutes later.
    if config.dist.exists():
        shutil.rmtree(config.dist)
    if config.command:
        ctx.run(config.build_command(), cwd=config.root)
    else:
        # PyInstaller's own scratch goes to a temporary folder: a build/
        # beside the project is nothing anyone hands over.
        with tempfile.TemporaryDirectory(prefix=f"{config.slug}-build-") as work:
            ctx.run([sys.executable, "-m", "PyInstaller",
                     config.spec, "--noconfirm", "--clean",
                     "--distpath", config.dist, "--workpath", work], cwd=config.root)
    for name, folder in config.zips.items():
        source = config.dist / folder
        if not source.is_dir():
            raise StepError(f"[build.zip] {name}: no folder {source}")
        archive = shutil.make_archive(str(config.dist / Path(name).stem), "zip",
                                      root_dir=source)
        os.replace(archive, config.dist / name)
    for path in config.released_files():
        if not path.is_file():
            raise StepError(f"the build produced no {path}")
        ctx.say(f"   {path.name}  {_size(path.stat().st_size)}")


def _size(n: int) -> str:
    return f"{n / 2**20:.1f} MB" if n >= 2**20 else f"{n / 1024:.0f} KB"


# --- sign --------------------------------------------------------------------

def sign(ctx, config: Config):
    files = config.released_files()
    for stale in (SUMS, SIG):
        (config.dist / stale).unlink(missing_ok=True)
    key = signing_key(ctx)
    if key is None:
        if not ctx.allow_unsigned:
            raise StepError(f"no signing key ({KEY_ENV} or --key-file)")
        ctx.say("   unsigned: no SHA256SUMS written")
        return
    if ctx.dry_run:
        ctx.say(f"   (dry run) sign {', '.join(p.name for p in files)}")
        return
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

    data = signature.manifest(config.app, config.tag, files)
    sig = Ed25519PrivateKey.from_private_bytes(key).sign(data)
    # The key the apps have pinned, not merely "a" key: a release signed
    # with the wrong one would be refused by every copy out there.
    if not signature.verify(base64.b64decode(ctx.public_key), data, sig):
        raise StepError("the signing key does not belong to the pinned public "
                        "key — every installed copy would refuse this release")
    (config.dist / SUMS).write_bytes(data)
    (config.dist / SIG).write_text(base64.b64encode(sig).decode() + "\n")
    for line in data.decode().splitlines():
        ctx.say(f"   {line}")


# --- publish -----------------------------------------------------------------

def publish(ctx, config: Config):
    files = config.released_files()
    if (config.dist / SIG).exists():
        files += [config.dist / SUMS, config.dist / SIG]
    elif not ctx.allow_unsigned:
        raise StepError(f"no {SIG} in {config.dist} — run `vkit sign` first")
    missing = [f for f in files if not f.is_file()]
    if missing and not ctx.dry_run:
        raise StepError(f"missing {', '.join(str(f) for f in missing)} — build first")
    repo = ["--repo", config.release_repo]
    # Idempotent: create the release the first time, replace the files on
    # re-runs.
    exists = not ctx.run(["gh", "release", "view", config.tag, *repo],
                         capture=True, check=False, changes=False).returncode
    if exists:
        ctx.run(["gh", "release", "upload", config.tag, *files, *repo, "--clobber"])
        return
    with tempfile.NamedTemporaryFile("w", suffix=".md", delete=False,
                                     encoding="utf-8") as f:
        f.write(notes(ctx, config))
    try:
        ctx.run(["gh", "release", "create", config.tag, *files, *repo,
                 "--title", f"{config.app} {config.tag}", "--notes-file", f.name])
    finally:
        os.unlink(f.name)


# --- docs --------------------------------------------------------------------

def docs(ctx, config: Config):
    if not config.readme:
        ctx.say("   no [docs] in vulturif.toml, nothing to mirror")
        return
    with tempfile.TemporaryDirectory(prefix=f"{config.slug}-public-") as tmp:
        public = Path(tmp) / "public"
        ctx.run(["gh", "repo", "clone", config.release_repo, public, "--",
                 "--depth", "1", "--quiet"], changes=False)
        readme = config.readme.read_text(encoding="utf-8")
        if config.screenshots:
            # The public repo keeps the screenshots at the top level, so the
            # README's links are pointed there in the copy.
            here = config.screenshots.relative_to(config.root).as_posix()
            readme = readme.replace(f"{here}/", "screenshots/")
            shutil.rmtree(public / "screenshots", ignore_errors=True)
            shutil.copytree(config.screenshots, public / "screenshots")
        (public / "README.md").write_text(readme, encoding="utf-8", newline="\n")
        git = ["git", "-C", public]
        ctx.run([*git, "add", "-A"], capture=True, changes=False)
        if not ctx.run([*git, "status", "--porcelain"], capture=True,
                       changes=False).stdout.strip():
            ctx.say("   public README and screenshots already up to date")
            return
        who = (["-c", "user.name=github-actions[bot]", "-c",
                "user.email=41898282+github-actions[bot]@users.noreply.github.com"]
               if ctx.ci else [])
        ctx.run([*git, *who, "commit", "--quiet", "-m",
                 f"README and screenshots from {config.tag}"])
        ctx.run([*git, "push", "--quiet", "origin", "HEAD"])


# --- sync --------------------------------------------------------------------

def sync(ctx, config: Config):
    if config.ssh and not ctx.ci:
        # sync_github_releases holds its own lock: when the cron run is
        # already at it this says "skipping", and the wait below covers it.
        ctx.run(["ssh", config.ssh,
                 f"cd {config.site_dir} && set -a && . ./.env && set +a && "
                 f".venv/bin/python manage.py sync_github_releases "
                 f"--project {config.slug}"])
    else:
        ctx.say("   no [sync] ssh here: waiting for the site's own sync "
                "(every 30 minutes)")
    if ctx.dry_run:
        return
    verify_site(ctx, config, wait=config.wait)


def verify_site(ctx, config: Config, wait: int = 0, interval: int = 15):
    """Ask the site what an installed copy will ask, and check the answer
    against the files in dist/ — hash for hash, and the signature too."""
    import requests
    url = f"{config.site}/api/{config.slug}/latest/"
    deadline = time.monotonic() + wait
    while True:
        try:
            r = requests.get(url, timeout=15,
                             headers={"User-Agent": f"vkit/{config.app}"})
            answer = r.json() if r.ok else {}
        except (requests.RequestException, ValueError):
            answer = {}
        if (answer.get("version") or "").lstrip("v") == config.version:
            break
        if time.monotonic() >= deadline:
            raise StepError(f"{url} still says {answer.get('version') or 'nothing'}"
                            f", not {config.tag}")
        time.sleep(interval)

    site = {a["name"]: a for a in answer.get("assets", [])}
    signed = signature.signed_hashes(answer, config.app, ctx.public_key)
    for path in config.released_files():
        if not path.is_file():
            continue    # a sync run on its own, without the build here
        digest = hashlib.sha256(path.read_bytes()).hexdigest()
        if path.name not in site:
            raise StepError(f"the site has no {path.name}")
        if (site[path.name].get("sha256") or "").lower() != digest:
            raise StepError(f"the site's {path.name} is not the one built here")
        if signed is not None and signed.get(path.name) != digest:
            raise StepError(f"{path.name} does not match the signed manifest")
    ctx.say(f"   vulturif.com serves {config.tag}"
            + (", signature checked" if signed is not None else ", UNSIGNED"))


STEPS = {"preflight": preflight, "build": build, "sign": sign,
         "publish": publish, "docs": docs, "sync": sync}
HELP = {"preflight": "check version, tag, gh login and signing key",
        "build": "PyInstaller (or [build] command), then the zips",
        "sign": "write and sign SHA256SUMS",
        "publish": "create or update the GitHub release",
        "docs": "mirror README and screenshots into the public repo",
        "sync": "have vulturif.com import it, then check what it serves"}
