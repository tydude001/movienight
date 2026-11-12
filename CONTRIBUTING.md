# Contributing to Movie Night

Issues and pull requests are welcome. Movie Night is maintained by one person
in their spare time, so a reply can take a while.

## Before you start

For anything bigger than a bug fix, open an issue first. The README says what
the app does, and `CLAUDE.md` describes the architecture and why it is the way
it is; a change that fights the rules below will usually be turned down however
good the code is.

## The rules a pull request is checked against

- **Both Letterboxd readers ship off.** `LETTERBOXD_WATCHLIST_SCRAPE` and
  `LETTERBOXD_SCORES` default to off because Letterboxd's terms prohibit
  automated access, and `test_both_scrapers_ship_disabled` holds them there.
  Nothing new may read letterboxd.com by default; the export is the default path.
- **No third party on a page-render path.** External scores, TMDb lookups and
  Plex syncs run from a background job or a lazy fragment; pages render from the
  database. Poster images are the one exception, sized to their box.
- **One taste model.** Every fit, match and dashboard figure comes from
  `app/taste.py`; the detail card explains the number the badge shows rather
  than recomputing it, so the two can never disagree.
- **Page loads stay CDN-free.** htmx, Chart.js and the fonts are vendored in
  `app/static/`; vendor anything new the same way.
- **`/admin` fails closed.** Every admin route needs the login and answers 503
  while none is set; `test_admin_auth.py` sweeps the route table for one that
  doesn't.
- **No real viewing data in fixtures, issues or pull requests.** Tests use
  invented films and the users `alice` and `bob`; `scripts/seed_demo.py` is the
  model. Someone's diary is a record of their evenings.
- **Don't edit an existing test to make it pass.** If your change and a test
  disagree, say so in the pull request — the test may be the one that's right.

## Running the checks

```sh
python -m venv .venv
.venv/bin/pip install -r requirements.txt -r requirements-dev.txt
.venv/bin/python -m ruff check .
.venv/bin/python -m pytest -q
```

The suite needs no network and no keys.

## Commits

Prefix commit messages with `feat:`, `fix:`, `chore:` or `docs:`.

## How a pull request lands

GitHub is a read-only mirror of the maintainer's own git server, so a pull
request is never merged with GitHub's merge button. The maintainer fetches
your branch, merges it on their side, and the next mirror sync carries it up;
GitHub then marks the pull request merged on its own once your head commit
reaches `main`. If the merge has to be squashed or reworked, the SHAs change
and the pull request is closed by hand with a note naming the commit your
work landed in. Either way, nothing is lost and you'll be credited in the
commit.

By contributing, you agree your contribution is licensed under this
project's [MIT licence](LICENSE).
