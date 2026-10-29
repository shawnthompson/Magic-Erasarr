# AGENTS.md

## Project overview

Magic-Erasarr is a Flask application that reads media-library information from Tautulli and coordinates read-only lookups and explicitly confirmed removal workflows across Seerr, Radarr, Sonarr, Lidarr, Plex, and Tautulli.

The main application code is organized as follows:

- `app.py`, `wsgi.py`, and `config.py`: application startup, WSGI entry point, and environment-backed configuration.
- `routes/`: Flask blueprints and API endpoints.
- `services/`: clients for external media services.
- `utils/`: small, reusable helpers such as identifier parsing.
- `templates/`: server-rendered HTML and page JavaScript.
- `tests/`: pytest tests for routes, API contracts, matching, and utility behavior.
- `docker/`: the production container and Compose configuration.

## Local setup and verification

Use the existing virtual environment when it is available. The preferred test command for this checkout is:

```bash
./venv313/bin/pytest -q
```

For a new environment, follow the setup in `README.md`:

```bash
python3 -m venv venv
source venv/bin/activate
pip install -r requirements-dev.txt
```

Run the app locally with `DEBUG=true` in `.env`, then use `python app.py`. Do not commit `.env`, API keys, tokens, or other service credentials. Use `.env.example` for configuration documentation.

Before assuming a command exists, inspect `README.md`, `requirements*.txt`, and the relevant test files. This project does not currently use a JavaScript package manager or frontend build step.

## Code conventions

- Keep changes focused and follow the existing plain-Python and Flask patterns.
- Prefer small helpers with clear names and docstrings when logic is shared or non-obvious.
- Preserve the existing API response shapes unless a change explicitly requires a contract update; update tests with intentional contract changes.
- Keep external-service calls in `services/` or the existing route/service boundary. Tests should mock service calls rather than require live Tautulli, Seerr, *arr, Plex, or Tautulli credentials.
- Preserve bounded concurrency and short-lived caching where existing endpoints use them; avoid adding synchronous, unbounded scans to fast library endpoints.
- Treat identifiers from external services as untrusted input. Prefer stable IDs and explicit unresolved or unavailable states over title-based guesses.

## Media and deletion safeguards

Removal is destructive and must remain visibly confirmed in the UI and validated by the API.

- Validate the current selected items and the selected `radarr_instance` at submission time; do not trust stale dialog state.
- Movie removal uses `delete_files=False`, preserving movie files and download/torrent data.
- TV and music removal has different file behavior. Keep warnings accurate for Sonarr and Lidarr actions, including when local files are removed.
- Preserve `unknown`, `unavailable`, `unaired`, and `unresolved` states rather than guessing identity, availability, or requester matches.
- Keep item details and progress lookups read-only. Do not make a display-only endpoint trigger removal or mutate external services.

## Accessibility and security

- Use semantic HTML, associated labels, keyboard-accessible controls, visible focus styles, and meaningful alt text.
- Use ARIA only when native HTML cannot express the behavior.
- Keep destructive actions clear, specific, and keyboard accessible; do not rely on color alone for warnings or status.
- Never expose API keys or service tokens in rendered pages, logs, tests, commits, or error responses.
- Escape or safely serialize values crossing from URL parameters or service responses into HTML or JavaScript.

## Change workflow

1. Check `git status` and inspect the relevant route, service, template, and tests before editing.
2. Make the smallest focused change and add or update regression tests for behavior changes.
3. Run `./venv313/bin/pytest -q` (or the narrowest relevant pytest target while iterating), then run the full suite before handoff.
4. Review the diff for accidental credentials, generated files, unrelated edits, and accessibility regressions.
5. Use a concise commit message without `Co-authored-by` trailers. The optional `.githooks/commit-msg` hook can be enabled with `git config core.hooksPath .githooks`.

For changes that affect Docker behavior, inspect `docker/Dockerfile` and `docker/docker-compose.yaml` and verify the image or Compose configuration as appropriate. Do not delete containers, images, volumes, or host data without explicit scope and confirmation.
