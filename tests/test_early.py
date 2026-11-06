"""Early access: `vkit release --early` and `vkit promote`."""
import subprocess

import pytest

from vulturif_kit.release import cli, config as cfg, steps

from test_release import project  # noqa: F401  (the fixture)


class Recorder(steps.Context):
    """A Context whose commands are recorded instead of run."""

    def __init__(self, **kw):
        super().__init__(**kw)
        self.commands = []

    def run(self, cmd, cwd=None, check=True, capture=False, changes=True):
        self.commands.append([str(c) for c in cmd])
        # `gh release view`: no such release yet.
        code = 1 if cmd[:3] == ["gh", "release", "view"] else 0
        return subprocess.CompletedProcess(cmd, code, "", "")


def signed(project, keys, monkeypatch):
    monkeypatch.setenv(steps.KEY_ENV, keys.secret)
    steps.sign(steps.Context(public_keys=(keys.public,)), cfg.load())


@pytest.fixture
def source(project, monkeypatch):  # noqa: F811
    monkeypatch.setenv("GITHUB_REPOSITORY", "Vulturif/demo-source")
    return project


def test_source_repo_from_ci_or_config(project, monkeypatch):  # noqa: F811
    monkeypatch.delenv("GITHUB_REPOSITORY", raising=False)
    assert cfg.load().source_repo == ""
    monkeypatch.setenv("GITHUB_REPOSITORY", "Vulturif/demo-source")
    assert cfg.load().source_repo == "Vulturif/demo-source"
    # Top-level, so before the [build] table.
    toml = project / "vulturif.toml"
    toml.write_text('source_repo = "Vulturif/other"\n' + toml.read_text())
    assert cfg.load().source_repo == "Vulturif/other"


def test_early_publishes_on_the_source_repo(source, keys, monkeypatch):
    signed(source, keys, monkeypatch)
    ctx = Recorder(early=True)
    steps.publish(ctx, cfg.load())
    create = ctx.commands[-1]
    assert create[:3] == ["gh", "release", "create"]
    assert create[create.index("--repo") + 1] == "Vulturif/demo-source"
    assert create[create.index("--title") + 1] == "Demo v1.2.0 (early access)"
    assert any(c.endswith("SHA256SUMS.sig") for c in create)


def test_without_early_it_stays_the_public_repo(source, keys, monkeypatch):
    signed(source, keys, monkeypatch)
    ctx = Recorder()
    steps.publish(ctx, cfg.load())
    create = ctx.commands[-1]
    assert create[create.index("--repo") + 1] == "Vulturif/Demo"


def test_early_needs_a_private_source_repo(project, monkeypatch):  # noqa: F811
    monkeypatch.delenv("GITHUB_REPOSITORY", raising=False)
    with pytest.raises(steps.StepError, match="source_repo"):
        steps.target_repo(steps.Context(early=True), cfg.load())
    monkeypatch.setenv("GITHUB_REPOSITORY", "Vulturif/Demo")
    with pytest.raises(steps.StepError, match="public"):
        steps.target_repo(steps.Context(early=True), cfg.load())


def test_early_mirrors_no_readme_and_checks_no_public_answer(source, monkeypatch):
    import requests

    class R:
        status_code, text = 202, ""

    monkeypatch.setenv(steps.HOOK_ENV, "s3cret")
    posted = []
    monkeypatch.setattr(requests, "post", lambda url, headers, timeout: posted.append(url) or R())
    monkeypatch.setattr(steps, "verify_site", lambda *a, **k: pytest.fail("checked"))
    ctx = Recorder(early=True, ci=True)
    steps.docs(ctx, cfg.load())
    steps.sync(ctx, cfg.load())
    assert ctx.commands == []
    assert posted == ["https://vulturif.com/hook/demo/release/"]


def test_promote_publishes_exactly_the_signed_files(source, keys, monkeypatch):
    signed(source, keys, monkeypatch)
    monkeypatch.delenv(steps.KEY_ENV)            # promote never signs
    ran = []
    for name in ("publish", "docs", "sync"):
        monkeypatch.setattr(steps, name, lambda ctx, c, name=name: ran.append(name))
    steps.promote(steps.Context(public_keys=(keys.public,)), cfg.load())
    assert ran == ["publish", "docs", "sync"]


def test_promote_refuses_a_file_the_early_release_did_not_sign(source, keys, monkeypatch):
    signed(source, keys, monkeypatch)
    (source / "dist" / "Host.exe").write_bytes(b"swapped")
    monkeypatch.setattr(steps, "publish", lambda *a: pytest.fail("published"))
    with pytest.raises(steps.StepError, match="Host.exe"):
        steps.promote(steps.Context(public_keys=(keys.public,)), cfg.load())


def test_promote_refuses_another_key_or_no_manifest(source, keys, monkeypatch):
    from conftest import Keys
    monkeypatch.setattr(steps, "publish", lambda *a: pytest.fail("published"))
    with pytest.raises(steps.StepError, match="download the early release"):
        steps.promote(steps.Context(public_keys=(keys.public,)), cfg.load())
    signed(source, keys, monkeypatch)
    with pytest.raises(steps.StepError, match="does not check out"):
        steps.promote(steps.Context(public_keys=(Keys().public,)), cfg.load())


def test_promote_checks_the_ci_tag(source, keys, monkeypatch):
    signed(source, keys, monkeypatch)
    monkeypatch.setenv("VKIT_TAG", "v9.9.9")
    with pytest.raises(steps.StepError, match="does not match"):
        steps.promote(steps.Context(ci=True, public_keys=(keys.public,)), cfg.load())


def test_cli_knows_early_and_promote(source):
    args = cli.parser().parse_args(["release", "--early", "--ci"])
    assert cli._context(args).early
    assert cli.parser().parse_args(["promote", "--ci"]).func is cli.cmd_promote
    with pytest.raises(SystemExit):              # promote has no --early
        cli.parser().parse_args(["promote", "--early"])


def test_early_refuses_a_source_repo_that_is_not_private(source, monkeypatch):
    class Public(Recorder):
        def run(self, cmd, cwd=None, check=True, capture=False, changes=True):
            out = "PUBLIC" if cmd[:3] == ["gh", "repo", "view"] else ""
            return subprocess.CompletedProcess(cmd, 0, out, "")

    monkeypatch.setenv(steps.KEY_ENV, "A" * 43 + "=")
    with pytest.raises(steps.StepError, match="public"):
        steps.preflight(Public(early=True, ci=True), cfg.load())
