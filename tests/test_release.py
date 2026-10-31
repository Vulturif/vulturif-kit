import hashlib
import subprocess

import pytest

from vulturif_kit import signature
from vulturif_kit.release import cli, config as cfg, steps


@pytest.fixture
def project(tmp_path, monkeypatch):
    (tmp_path / "version.py").write_text('"""v"""\n\n__version__ = "1.2.0"\n')
    (tmp_path / "Demo.spec").write_text("")
    (tmp_path / "vulturif.toml").write_text(
        'app = "Demo"\n[build]\nassets = ["Demo.exe", "Host.exe"]\n'
        'zip = { "Demo-ext.zip" = "ext" }\n')
    dist = tmp_path / "dist"
    (dist / "ext").mkdir(parents=True)
    (dist / "Demo.exe").write_bytes(b"main")
    (dist / "Host.exe").write_bytes(b"host")
    (dist / "Demo-ext.zip").write_bytes(b"zip")
    monkeypatch.chdir(tmp_path)
    return tmp_path


def test_config_defaults(project):
    c = cfg.load()
    assert (c.app, c.slug, c.tag, c.release_repo) == ("Demo", "demo", "v1.2.0", "Vulturif/Demo")
    assert c.spec == project / "Demo.spec"
    assert [p.name for p in c.released_files()] == ["Demo.exe", "Host.exe", "Demo-ext.zip"]


def test_bump(project):
    assert cli.main(["bump", "1.3.0"]) == 0
    assert '__version__ = "1.3.0"' in (project / "version.py").read_text()
    assert cli.main(["bump", "1.3"]) == 1


def test_sign_with_the_pinned_key(project, keys, monkeypatch):
    monkeypatch.setenv(steps.KEY_ENV, keys.secret)
    ctx = steps.Context(public_keys=(keys.public,))
    steps.sign(ctx, cfg.load())
    text = (project / "dist" / "SHA256SUMS").read_text()
    sig = (project / "dist" / "SHA256SUMS.sig").read_text().strip()
    hashes = signature.signed_hashes(
        {"version": "1.2.0", "manifest": text, "signature": sig}, "Demo", keys.public)
    assert hashes["Host.exe"] == hashlib.sha256(b"host").hexdigest()
    assert set(hashes) == {"Demo.exe", "Host.exe", "Demo-ext.zip"}


def test_sign_refuses_a_key_the_apps_do_not_pin(project, keys, monkeypatch):
    monkeypatch.setenv(steps.KEY_ENV, keys.secret)
    with pytest.raises(steps.StepError, match="pinned"):
        steps.sign(steps.Context(), cfg.load())      # the real PUBLIC_KEYS
    assert not (project / "dist" / "SHA256SUMS").exists()


def test_no_key_no_release(project, monkeypatch):
    monkeypatch.delenv(steps.KEY_ENV, raising=False)
    with pytest.raises(steps.StepError, match="signing key"):
        steps.sign(steps.Context(), cfg.load())


def test_ci_tag_must_match(project, monkeypatch):
    monkeypatch.setenv("GITHUB_REF_TYPE", "tag")
    monkeypatch.setenv("GITHUB_REF_NAME", "v1.1.9")
    with pytest.raises(steps.StepError, match="does not match"):
        steps.preflight(steps.Context(ci=True), cfg.load())


def test_local_tag_is_made_on_a_clean_tree(project, monkeypatch):
    for var in ("GIT_AUTHOR_NAME", "GIT_COMMITTER_NAME"):
        monkeypatch.setenv(var, "t")
    for var in ("GIT_AUTHOR_EMAIL", "GIT_COMMITTER_EMAIL"):
        monkeypatch.setenv(var, "t@t")
    git = lambda *a: subprocess.run(["git", *a], cwd=project, check=True,
                                    capture_output=True, text=True).stdout
    git("init", "-q")
    git("add", "version.py", "vulturif.toml")
    git("commit", "-qm", "x")
    ctx = steps.Context(notes="- the notes")
    c = cfg.load()
    steps._local_tag(ctx, c)
    assert steps.notes(steps.Context(), c) == "- the notes"
    (project / "version.py").write_text('__version__ = "1.2.0"  # changed\n')
    with pytest.raises(steps.StepError, match="uncommitted"):
        steps._local_tag(ctx, c)


def test_verify_site_checks_hashes_and_signature(project, keys, monkeypatch):
    import requests
    c = cfg.load()
    files = c.released_files()
    text = signature.manifest("Demo", "v1.2.0", files).decode()
    answer = {"version": "v1.2.0", "manifest": text, "signature": keys.sign(text),
              "assets": [{"name": p.name, "sha256": hashlib.sha256(p.read_bytes()).hexdigest()}
                         for p in files]}

    class R:
        ok = True

        def json(self):
            return answer

    monkeypatch.setattr(requests, "get", lambda *a, **kw: R())
    ctx = steps.Context(public_keys=(keys.public,))
    steps.verify_site(ctx, c)

    answer["assets"][1]["sha256"] = "0" * 64
    with pytest.raises(steps.StepError, match="Host.exe"):
        steps.verify_site(ctx, c)

    answer["version"] = "v1.1.0"
    with pytest.raises(steps.StepError, match="still says v1.1.0"):
        steps.verify_site(ctx, c)


def test_init_writes_a_loadable_config(tmp_path, monkeypatch):
    (tmp_path / "Thing.spec").write_text("")
    (tmp_path / "version.py").write_text('__version__ = "0.1.0"\n')
    monkeypatch.chdir(tmp_path)
    assert cli.main(["init", "--workflow"]) == 0
    c = cfg.load()
    assert (c.app, c.slug, c.ssh) == ("Thing", "thing", "vulturif@diphda.uberspace.de")
    assert (tmp_path / ".github" / "workflows" / "release.yml").exists()


def test_release_rejects_unknown_skip(project):
    assert cli.main(["release", "--skip", "nope", "--dry-run"]) == 1


def test_next_version():
    assert cfg.next_version("1.0.9", "patch") == "1.0.10"
    assert cfg.next_version("1.0.9", "minor") == "1.1.0"
    assert cfg.next_version("1.0.9", "major") == "2.0.0"
    assert cfg.next_version("1.0.9", "1.2.3") == "1.2.3"


def _repo(project, monkeypatch):
    for var in ("GIT_AUTHOR_NAME", "GIT_COMMITTER_NAME"):
        monkeypatch.setenv(var, "t")
    for var in ("GIT_AUTHOR_EMAIL", "GIT_COMMITTER_EMAIL"):
        monkeypatch.setenv(var, "t@t")
    git = lambda *a: subprocess.run(["git", *a], cwd=project, check=True,
                                    capture_output=True, text=True).stdout
    git("init", "-q")
    git("add", "version.py", "vulturif.toml", "Demo.spec")
    git("commit", "-qm", "x")
    return git


def test_cut_commits_the_bump_alone_and_tags_it(project, monkeypatch):
    git = _repo(project, monkeypatch)
    (project / "untracked.txt").write_text("not part of it")
    c = cfg.load()
    steps.cut(steps.Context(notes="- the notes"), c, "minor")
    assert c.version == "1.3.0"
    assert git("show", "--name-only", "--format=%s", "HEAD").split() == \
        ["Demo", "v1.3.0", "version.py"]
    assert git("tag", "-l", "--format=%(contents)", "v1.3.0").strip() == "- the notes"
    assert steps.notes(steps.Context(), c) == "- the notes"


def test_cut_refuses_other_changes(project, monkeypatch):
    _repo(project, monkeypatch)
    (project / "Demo.spec").write_text("changed")
    with pytest.raises(steps.StepError, match="uncommitted"):
        steps.cut(steps.Context(), cfg.load(), "patch")
    assert cfg.load().version == "1.2.0"


def test_sync_through_the_hook_then_verifies(project, monkeypatch):
    import requests
    posted, checked = [], []
    monkeypatch.setenv(steps.HOOK_ENV, "s3cret")

    class R:
        status_code, text = 202, ""

    monkeypatch.setattr(requests, "post",
                        lambda url, headers, timeout: posted.append((url, headers)) or R())
    monkeypatch.setattr(steps, "verify_site", lambda ctx, c, wait: checked.append(wait))
    steps.sync(steps.Context(ci=True), cfg.load())
    assert posted[0][0] == "https://vulturif.com/hook/demo/release/"
    assert posted[0][1]["Authorization"] == "Bearer s3cret"
    assert checked == [600]


def test_ci_without_a_hook_does_not_wait_for_the_cron(project, monkeypatch):
    monkeypatch.delenv(steps.HOOK_ENV, raising=False)
    monkeypatch.setattr(steps, "verify_site",
                        lambda *a, **k: pytest.fail("must not wait"))
    steps.sync(steps.Context(ci=True), cfg.load())


def test_bump_by_part(project):
    assert cli.main(["bump", "patch"]) == 0
    assert '__version__ = "1.2.1"' in (project / "version.py").read_text()
