# vulturif-kit

What every Vulturif app shares — updater, signature check, opt-in statistics,
error reports, encrypted credentials — once, instead of a copy per app; and
`vkit`, which takes a release from version bump to vulturif.com in one command.

## In the app

```python
from vulturif_kit import App
from version import __version__

kit = App("Goonwall", __version__, assets=("Goonwall.exe", "GoonwallHost.exe"))

kit.errors.install()                       # entry point only
kit.analytics.start(settings["usage_stats"])
kit.analytics.track("search:pornhub")      # fixed names only, free text is dropped
day = kit.analytics.mark_day(settings.get("usage_stats_day"))
if day: settings["usage_stats_day"] = day

kit.license.refresh_if_due()               # worker thread, once a start; never raises
update = kit.updater.check(license=kit.license.current())   # worker; None or an Update
update = kit.check_update()                # the same two lines in one
kit.updater.apply(update, on_progress)     # worker thread; swap on next start
update.public_at                           # early access: when everyone gets it, else None

kit.license.load()                         # the stored key (License), expired or not
kit.license.set(text)                      # check and keep a pasted key; BadLicense if not one
kit.license.clear()

kit.errors.send(description, contact)      # after the user pressed Send
store = kit.store(b"VideoSelector/securestore/1")   # keep the app's old entropy!
creds = kit.credentials(CREDENTIAL_FIELDS)          # .env seeds the first run only
```

| Module | Was | From |
|---|---|---|
| `updater.py` | check, download, all-or-nothing swap, `strip_bootloader_env` | Goonwall (multi-file) |
| `signature.py` | Ed25519 verify in plain Python, SHA256SUMS write + read | both, identical |
| `analytics.py` | opt-in counts, no id of any kind, batched | Goonwall |
| `errorreport.py` | in-memory errors, scrubbed, sent on Send | both |
| `securestore.py` | DPAPI JSON files, entropy per app | Goonwall |
| `credentials.py` | credential fields, DPAPI, .env as first-run seed | Leashware |
| `license.py` | supporter keys: offline check, renewal, early-access channel | new |

Endpoints follow from the slug: `/api/<slug>/latest/`, `/collect/<slug>/`,
`/report/<slug>/` on vulturif.com. `PUBLIC_KEYS` (app.py) are the keys an
update may be signed with — while the signing key changes, the new one and
the old one; the comment there has the steps. `vkit sign` refuses a key that
belongs to none of them.

**Supporter keys** (`license.py`): vulturif.com gives supporters a key
(`VLT1-…`) that the app checks offline against `LICENSE_PUBLIC_KEYS` and keeps
in `<data dir>\license.dat`. With one, the update check also offers
early-access releases and the download shows the key; without one, or once it
has lapsed, the updates are the public ones. Nothing is ever switched off.
The app needs a place to paste the key (a field or a small dialog saying
valid until …, ran out on …, or how to get one with a link to
`https://vulturif.com/supporter/`; Goonwall's `ui_qt/dialogs/supporter.py`) and one line in its update
question when `update.public_at` is set.

**Compatibility to keep when porting:** Goonwall's store entropy is
`b"VideoSelector/securestore/1"`; Leashware's credentials live in
`%APPDATA%\ctrlEm` with no entropy (`App(..., data_dir_name="ctrlEm")`,
`kit.credentials(fields, entropy=None)`). The kit reads Leashware's old bare
DPAPI file and rewrites it in its own format on the next save.

## Releasing

```
pip install -e ../Vulturif_Kit[release]    # in the app's venv
vkit init                                  # vulturif.toml, guessed from *.spec
vkit bump 1.2.0
git commit -am "1.2.0"
vkit release --notes "- Fixed the thing"
vkit release --early                       # early access: on the private source repo
vkit promote                               # that release (downloaded into dist/) goes public
```

`vkit release` runs, and stops at the first failure:

1. **preflight** — three-part version, clean tree, makes the annotated tag
   `v1.2.0` (its message is the release notes), `gh` logged in, signing key
   present. All before a ten-minute build.
2. **build** — empties `dist/`, PyInstaller with the spec (or `[build] command`),
   zips folders listed in `[build.zip]`, checks every asset exists.
3. **sign** — SHA256SUMS + .sig from `$UPDATE_SIGNING_KEY` or `--key-file`,
   verified against the pinned public key.
4. **publish** — `gh release create` on the public repo, or `upload --clobber`
   on a re-run.
5. **docs** — README and screenshots into the public repo (`[docs]` only).
6. **sync** — `ssh` to the server, `manage.py sync_github_releases --project
   <slug>`, then asks `/api/<slug>/latest/` exactly like an installed copy and
   checks every hash and the signature against `dist/`.

Every step also runs on its own (`vkit sign`, `vkit sync`, …), `--skip STEP`
leaves one out, `--dry-run` changes nothing. `vkit verify` only does the last
check.

**CI instead:** `vkit init --workflow` writes `.github/workflows/release.yml`
and `promote.yml`; release.yml runs `vkit release --ci` on a pushed tag. Release locally *or* push the
tag — not both. In CI the sync step calls vulturif.com's release hook with
`$VULTURIF_HOOK_TOKEN` (the server's `RELEASE_HOOK_TOKEN`) and then waits
for the site to serve the release; without the token it leaves it to the
site's cron.

**From vulturif.com:** the same workflow also takes a `workflow_dispatch`
with `bump` (patch/minor/major), `notes` and `early`, which is what "New
release…" on a project in the site's admin sends, on the branch picked there
(it needs the project's source repo and `GITHUB_DISPATCH_TOKEN` on the
server). The run starts with `vkit cut <bump> --ci --push`: version file
counted up, committed and tagged on that branch, pushed with the run's own
token so the tag starts no second run. `early` is only sent when ticked, so
an older branch whose release.yml lacks the input still takes a release.

**Early access:** `vkit release --early` (the workflow's `early` input) puts
the same build and signature on the *private source repo* instead of the
public one, with the run's own token, and mirrors no README. vulturif.com
syncs it for supporters only for 21 days, then starts the app's
`promote.yml`: that downloads the early release into `dist/`, and `vkit
promote` checks the files against their signed SHA256SUMS, publishes exactly
those on the public repo, mirrors the README and syncs. Nothing is rebuilt.
A fix of the public version meanwhile is an ordinary release from a
`release/x.y` branch (made from the last public tag), never a reason to end
an embargo early.

## A new app

From nothing to a release on vulturif.com, supporters-first if wanted. Leashware
and Goonwall are the worked examples; copy from them.

**In the app's repo (private, the "source repo")**

1. `requirements.txt`: `vulturif-kit @ git+https://github.com/Vulturif/vulturif-kit@vX.Y.Z`,
   the newest tag. Nowhere else: not in requirements-dev, not in the workflow.
2. `kit.py`: `APP = App("Name", __version__, assets=("Name.exe", ...))` — main
   exe first; `data_dir_name=` only to keep an existing folder. The rest of
   the app imports `APP` from here.
3. `updater.py` (or wherever the update check runs): `APP.license.refresh_if_due()`,
   then `APP.updater.check(license=APP.license.current())`. If the app leaves
   out assets that are not installed, rebuild the Update with
   `dataclasses.replace`.
4. A supporter key field (settings dialog or menu entry → small dialog) on
   `APP.license.load/set/clear`, showing until when the key is valid, and
   "How to get a key" linking to `https://vulturif.com/supporter/`.
5. The update question: one line when `update.public_at` is set ("Early
   access — everyone else gets it on …").
6. Error reports and stats as the other apps do (`APP.errors`, `APP.analytics`,
   opt-in, off by default).
7. `vkit init --workflow`: `vulturif.toml`, `release.yml`, `promote.yml`. In
   release.yml keep the "The app imports" step pointed at the modules that use
   the kit.
8. Actions secrets: `RELEASE_TOKEN` (fine-grained, Contents: Read and write on
   the public repo), `UPDATE_SIGNING_KEY` (the one key pair for all apps),
   `VULTURIF_HOOK_TOKEN` (the server's `RELEASE_HOOK_TOKEN`).
9. A test that pastes a key, checks the update question's early line, and
   that the filtered Update keeps `public_at` (Goonwall's
   `tests/test_supporter.py`).

**On GitHub**

10. The public release repo `Vulturif/Name` (README comes from the app via
    `[docs]`, topic `nsfw` for an 18+ app). It may stay private until launch.
11. The server's `GITHUB_TOKEN`: Contents: Read on the public repo *and* the
    source repo (early releases). The dispatch token: Actions: Read and write
    on the source repo.

**On vulturif.com (admin → Projects → Add)**

12. GitHub repo = the public repo, Source repo = the private one. For a
    supporters-first launch set **Public from** to the launch date: until then
    the app is on no public page, the update check and downloads exist only
    for supporters, and they download it from their page on /supporter/.
13. "New release…" → tick Early access for the first build. Patrons get it
    at once; on the Public-from date it appears everywhere and promote.yml
    puts it on the public repo.

## Develop

```
.venv/Scripts/python.exe -m pip install -e .[dev]
.venv/Scripts/python.exe -m pytest
```
