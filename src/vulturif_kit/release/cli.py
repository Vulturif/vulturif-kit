"""vkit: one command from version bump to vulturif.com.

    vkit release              preflight, build, sign, publish, docs, sync
    vkit release --skip docs  the same without one step
    vkit build | sign | publish | docs | sync    one step on its own
    vkit verify               does the site serve what dist/ holds?
    vkit bump 1.2.0           write the version file
    vkit init                 vulturif.toml (+ --workflow) for a new app
    vkit keygen               a new signing key pair, once ever

Run from the app's folder (or below it); vulturif.toml says the rest.
"""

import argparse
import sys
from importlib import resources
from pathlib import Path

from . import config as cfg
from . import steps


def _context(args) -> steps.Context:
    return steps.Context(
        dry_run=getattr(args, "dry_run", False), ci=getattr(args, "ci", False),
        notes=_notes(args), key_file=getattr(args, "key_file", None),
        allow_unsigned=getattr(args, "allow_unsigned", False),
        allow_dirty=getattr(args, "allow_dirty", False),
        push_tag=getattr(args, "push_tag", False))


def _notes(args):
    if getattr(args, "notes_file", None):
        return Path(args.notes_file).read_text(encoding="utf-8").strip()
    return getattr(args, "notes", None)


def _run(names, args):
    config = cfg.load(args.config)
    ctx = _context(args)
    for name in names:
        ctx.say(f"==> {name}")
        steps.STEPS[name](ctx, config)
    ctx.say(f"==> {config.app} {config.tag}: "
            + ("dry run done" if ctx.dry_run else "done"))


def cmd_release(args):
    skip = set(args.skip or ())
    unknown = skip - set(steps.ORDER)
    if unknown:
        raise steps.StepError(f"no step {', '.join(sorted(unknown))}; "
                              f"the steps are {', '.join(steps.ORDER)}")
    _run([s for s in steps.ORDER if s not in skip], args)


def cmd_step(args):
    _run([args.step], args)


def cmd_verify(args):
    config = cfg.load(args.config)
    steps.verify_site(_context(args), config, wait=args.wait)


def cmd_bump(args):
    config = cfg.load(args.config)
    before = config.version
    cfg.set_version(config, args.version)
    print(f"{config.version_file.name}: {before} -> {args.version}")


def cmd_init(args):
    root = Path.cwd()
    target = root / cfg.FILE
    specs = sorted(root.glob("*.spec"))
    app = args.app or (specs[0].stem if specs else root.name)
    if target.exists() and not args.force:
        print(f"{target} exists (--force to overwrite)")
    else:
        text = resources.files(__package__).joinpath("templates/vulturif.toml") \
            .read_text(encoding="utf-8")
        target.write_text(text.replace("{app}", app).replace("{slug}", app.lower()),
                          encoding="utf-8")
        print(f"wrote {target}")
    if args.workflow:
        flow = root / ".github" / "workflows" / "release.yml"
        if flow.exists() and not args.force:
            print(f"{flow} exists (--force to overwrite)")
        else:
            flow.parent.mkdir(parents=True, exist_ok=True)
            flow.write_text(resources.files(__package__)
                            .joinpath("templates/release.yml")
                            .read_text(encoding="utf-8"), encoding="utf-8")
            print(f"wrote {flow}")


def cmd_keygen(args):
    import base64

    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
    from cryptography.hazmat.primitives.serialization import (
        Encoding, NoEncryption, PrivateFormat, PublicFormat)

    key = Ed25519PrivateKey.generate()
    private = key.private_bytes(Encoding.Raw, PrivateFormat.Raw, NoEncryption())
    public = key.public_key().public_bytes(Encoding.Raw, PublicFormat.Raw)
    print("There is one key pair for all the apps, and it exists already —")
    print("a new one only makes sense if the old private key is lost or out.")
    print("Then: every copy out there needs one last release signed with the")
    print("old key that pins the new public one.\n")
    print(f"{steps.KEY_ENV} (secret):  {base64.b64encode(private).decode()}")
    print(f"PUBLIC_KEY (pinned):       {base64.b64encode(public).decode()}")


def parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="vkit", description=__doc__.split("\n")[0],
                                formatter_class=argparse.RawDescriptionHelpFormatter,
                                epilog=__doc__.split("\n", 2)[2])
    p.add_argument("--config", help=f"path to {cfg.FILE} (default: found upwards)")
    sub = p.add_subparsers(dest="command", required=True)

    def step_options(s):
        s.add_argument("--dry-run", action="store_true",
                       help="show what would change, change nothing")
        s.add_argument("--ci", action="store_true",
                       help="running in GitHub Actions: tag from GITHUB_REF_NAME, "
                            "no tag made, no ssh")
        s.add_argument("--key-file", type=Path,
                       help=f"signing key file instead of ${steps.KEY_ENV}")
        s.add_argument("--allow-unsigned", action="store_true")
        s.add_argument("--notes", help="release notes (default: the tag message)")
        s.add_argument("--notes-file")

    r = sub.add_parser("release", help="every step, in order")
    step_options(r)
    r.add_argument("--skip", action="append", metavar="STEP",
                   help=f"leave out a step ({', '.join(steps.ORDER)}); repeatable")
    r.add_argument("--allow-dirty", action="store_true",
                   help="build with uncommitted changes")
    r.add_argument("--push-tag", action="store_true",
                   help="push the source tag to origin (starts CI if it releases too)")
    r.set_defaults(func=cmd_release)

    for name in steps.ORDER:
        s = sub.add_parser(name, help=steps.HELP[name])
        step_options(s)
        if name == "preflight":
            s.add_argument("--allow-dirty", action="store_true")
        s.set_defaults(func=cmd_step, step=name)

    v = sub.add_parser("verify", help="check what vulturif.com serves")
    v.add_argument("--wait", type=int, default=0, help="seconds to wait for it")
    v.set_defaults(func=cmd_verify)

    b = sub.add_parser("bump", help="set the version")
    b.add_argument("version")
    b.set_defaults(func=cmd_bump)

    i = sub.add_parser("init", help=f"write {cfg.FILE} for this app")
    i.add_argument("--app")
    i.add_argument("--workflow", action="store_true",
                   help="also .github/workflows/release.yml")
    i.add_argument("--force", action="store_true")
    i.set_defaults(func=cmd_init)

    k = sub.add_parser("keygen", help="make a signing key pair")
    k.set_defaults(func=cmd_keygen)
    return p


def main(argv=None) -> int:
    args = parser().parse_args(argv)
    try:
        args.func(args)
    except (cfg.ConfigError, steps.StepError) as e:
        print(f"vkit: {e}", file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        return 130
    return 0


if __name__ == "__main__":
    sys.exit(main())
