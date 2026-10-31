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

update = kit.updater.check()               # worker thread; None or an Update
kit.updater.apply(update, on_progress)     # worker thread; swap on next start

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

Endpoints follow from the slug: `/api/<slug>/latest/`, `/collect/<slug>/`,
`/report/<slug>/` on vulturif.com. `PUBLIC_KEYS` (app.py) are the keys an
update may be signed with — while the signing key changes, the new one and
the old one; the comment there has the steps. `vkit sign` refuses a key that
belongs to none of them.

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

**CI instead:** `vkit init --workflow` writes `.github/workflows/release.yml`,
which runs `vkit release --ci` on a pushed tag. Release locally *or* push the
tag — not both. In CI the sync step calls vulturif.com's release hook with
`$VULTURIF_HOOK_TOKEN` (the server's `RELEASE_HOOK_TOKEN`) and then waits
for the site to serve the release; without the token it leaves it to the
site's cron.

**From vulturif.com:** the same workflow also takes a `workflow_dispatch`
with `bump` (patch/minor/major) and `notes`, which is what "New release…" on
a project in the site's admin sends (it needs the project's source repo and
`GITHUB_DISPATCH_TOKEN` on the server). The run starts with
`vkit cut <bump> --ci --push`: version file counted up, committed and tagged
on the default branch, pushed with the run's own token so the tag starts no
second run.

## Develop

```
.venv/Scripts/python.exe -m pip install -e .[dev]
.venv/Scripts/python.exe -m pytest
```
