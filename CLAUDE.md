# vulturif-kit

What every Vulturif desktop app shares (updater, signed releases, supporter
keys, opt-in stats, error reports, DPAPI stores) and `vkit`, the release
CLI. The apps are Leashware (`../ctrlEm_api_integration`, source repo
`Vulturif/ctrl_them_api`) and Goonwall (`../VideoSelector`, source repo
`Vulturif/GoonerWall`); the site they talk to is vulturif.com
(`../PublicDistribution`, Django). README.md says how to use each part; this
file is what to keep in mind when changing the kit or adding an app.

## Run and test

- `.venv/Scripts/python.exe -m pytest` — about 75 tests, a few seconds.
- The apps install this repo editable on the dev machine (`pip install -e
  ../Vulturif_Kit[release]`), so a change here is live in their venvs at
  once — and in their builds only after a tag (below).

## Releasing the kit itself

- The apps pin a **tag**: `vulturif-kit @ git+https://github.com/Vulturif/vulturif-kit@vX.Y.Z`
  in their `requirements.txt`. Bump `version` in `pyproject.toml` *and*
  `__version__` in `src/vulturif_kit/__init__.py`, commit, `git tag -a
  vX.Y.Z`, push with `--follow-tags`, and only then raise the apps' pin.
  An app whose pin names a tag that is not pushed yet fails its build.
- Never move a tag that is out: the same version would mean different code.
- The local branch is `master`, tracking `origin/main`.

## Rules that cost a broken release when forgotten

- **One pin, in requirements.txt.** An app's workflow (or requirements-dev)
  must not install `vulturif-kit[...] @ ...@vOld` a second time: pip then
  replaces the kit the app is built with. Leashware v1.1.0 shipped kit 0.2.0
  that way and could not start (`cannot import name 'BadLicense'`). The
  release side needs only `cryptography` on top.
- **Every workflow imports the app before building** ("The app imports"
  step): a name missing in the kit fails the run, not the user's start.
- **Update fields travel with the Update.** An app that filters an update's
  assets builds the new one with `dataclasses.replace(update, assets=...)`,
  never `Update(version, notes, assets)`: that drops `public_at` and `auth`,
  and an early-access download is then refused.
- **Two key pairs, never mixed.** `PUBLIC_KEYS` (app.py) check updates; their
  private half is only the source repos' `UPDATE_SIGNING_KEY` secret.
  `LICENSE_PUBLIC_KEYS` check supporter keys; their private half is
  `LICENSE_SIGNING_KEY` on the server, which issues the keys. A test holds
  them apart.
- **`--early` only to a private repo.** preflight refuses a source repo that
  is not private, and one equal to the public release repo.
- **`promote` rebuilds nothing.** It publishes exactly the early release's
  files after checking them against its signed SHA256SUMS; hashes and
  signature supporters already have stay valid.

## Supporter keys (license.py) in one paragraph

vulturif.com issues `VLT1-<payload>.<sig>` keys (its `showcase/licenses.py`
has the format) through Patreon or by hand. The app checks them offline,
keeps one in `<data dir>\license.dat`, renews it at `/api/license/refresh/`
when under ten days are left (`refresh_if_due`, once a start), and passes
`license.current()` to `Updater.check(license=...)`, which then asks
`?channel=early` and sends the key only for an early-access download. A
lapsed key means the normal releases again; nothing is ever disabled.

## Adding a new app

The full checklist is in README.md, "A new app". Keep it current when a step
changes; it is what the next app is set up from.
