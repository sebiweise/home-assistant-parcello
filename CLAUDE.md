# Parcello for Home Assistant

Custom integration (domain `parcello`, distributed via HACS) that polls the
parcels of a Parcello account (parcel tracking with delivery prediction).
Community project, not affiliated with Parcello. Parcello has no public API
for private users: the integration uses the undocumented backend of the
Parcello app and web app (`api-v4.parcello.org/v1/app`), see `api.py`.

## Layout (`custom_components/parcello/`)

- `__init__.py`: setup, services `add_parcel` (returns a response) and
  `remove_parcel`
- `api.py`: `ParcelloApiClient` (login, parcels, add/delete, carrier
  detection), `Parcel` parsing and the API errors. Endpoints and fields are
  taken from the web app; assumptions are marked in the code.
- `coordinator.py`: `ParcelloCoordinator` (polling, single updates of parcels
  the server has not tracked yet, `parcello_status_changed` events,
  visibility of delivered parcels)
- `config_flow.py`: login, reauth (only the token is stored, never the
  password) and options flow (options in `entry.options`)
- Entities: `sensor.py` (account sensors, one sensor per parcel, added and
  removed dynamically), `calendar.py`, shared base in `entity.py`;
  `diagnostics.py` (raw parcels, personal data redacted); constants in
  `const.py`
- `strings.json`, `translations/{en,de}.json`, `services.yaml`, `icons.json`,
  `brand/` (a neutral package icon, not the Parcello logo)
- Tests: `tests/test_init.py`, `tests/test_api.py`, `tests/test_brand.py`,
  `tests/test_translations.py` (en.json equals strings.json, de.json has the
  same keys), `tests/test_min_version.py`

## Workflow

- `scripts/setup` installs the hash-locked test dependencies,
  `scripts/test` runs Ruff check, Ruff format check and pytest (must pass
  before every PR), `scripts/develop` starts Home Assistant on port 8123,
  `scripts/lock` regenerates `requirements_test.txt`.
- The minimum Home Assistant version lives in `hacs.json`; `.coderabbit.yaml`
  repeats it (`tests/test_min_version.py` checks that). The CI job
  `tests-min` runs pytest against it with `requirements_test_min.txt`; after
  raising it, update `requirements_test_min.in` and run `scripts/lock_min`
  (Python 3.12).
- Changes go through a pull request against `master`. Releases bump
  `version` in `manifest.json` (SemVer); `release.yml` tags and publishes
  after the merge.
- New options, entities and services are documented in `strings.json`,
  both translations and the README (German).
- Code, comments and commit messages in English; README, PR descriptions
  and German translations in German.
- The token, the password and the email address must never be logged or end
  up in attributes, events or error messages.
- No `Co-Authored-By` lines in commit messages.

## graphify

`scripts/graphify` builds a code-only knowledge graph in `graphify-out/`
(git-ignored, local AST parsing, no API calls). It runs at session start;
cloud sessions install the pinned graphify release automatically. Locally,
install it with `uv tool install graphifyy==0.9.86` to use it.

When `graphify-out/graph.json` exists:

- For a codebase question, start with `graphify query "<question>"`,
  `graphify explain "<symbol>"` or `graphify path "<A>" "<B>"` before
  grepping or reading whole files, and pass this rule on to subagents that
  explore code. Read raw files for the lines the graph pointed to.
- Read `graphify-out/GRAPH_REPORT.md` only for a broad architecture
  overview.
- After changing code, run `scripts/graphify` (about two seconds) to
  refresh the graph.
