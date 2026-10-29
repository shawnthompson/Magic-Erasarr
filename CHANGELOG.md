# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/).

## [1.12.0] - 2026-10-29

### Added

- **Requester watch status** — The dashboard and item details show Seerr requesters, requester-specific watch status, last-watched dates, and the threshold used for each result.
- **Stable requester matching** — Seerr Plex IDs are resolved and matched to Tautulli users before using conservative name fallback; unresolved identities and unavailable history remain explicitly unknown.
- **Item detail pages** — Added read-only `/item/<rating_key>` pages with posters, metadata, matching identifiers, viewer statistics, and TV episode details, while preserving safe same-origin return links.
- **TV request progress** — Item details show requested seasons, requested-content progress, whole-series progress, episode watched states, and missing requested content.
- **TV availability and air dates** — Requested episodes distinguish available, unavailable, unaired, and unresolved content, with next-air or last-air dates and series status.
- **Settings page** — Added `/settings` for browser-local library visibility, Light/Dark/System themes, watched-threshold preferences, integration status, and setup guidance.
- **Configurable watched threshold** — Dashboard and item-detail requester status now use the saved Settings value, while blank settings retain Tautulli's configured threshold and 85% fallback behavior.
- **Deployment workflow** — Added a test-gated self-hosted deployment workflow for the production `main` branch.
- **Docker environment loading** — Docker Compose now passes the repository-root `.env` file into the container instead of relying on hard-coded placeholder values.
- **Configurable Docker port** — The host port is now controlled by `MAGIC_ERASARR_PORT` in `.env`, while the container continues listening on port 5000.

### Changed

- **Library views** — Combined library results can exclude selected Tautulli libraries by stable section ID; TV episode summaries are loaded separately with bounded concurrency and short-lived caching so the initial response stays fast.
- **TV summaries** — TV rows show episode totals and combined file size; episode play counts and last-played values come from per-user and history data when available.
- **Removal safety** — Movie removal requires an explicit Radarr library-copy selection and removes only that library entry with files preserved; TV and music removal continue to delete their managed files after confirmation.
- **CI** — Docker tests run on pull requests and `main`, use Python 3.14, and only publish images for non-PR pushes.
- **Repository ownership** — Application links, Docker image metadata, Compose image name, and historical release links now point to `shawnthompson/Magic-Erasarr`.
- **Credentials** — Settings reports integration configuration without exposing API keys or tokens; credentials remain environment-managed.
- **Documentation** — README now documents the Settings page, requester and TV detail behavior, and the different file behavior for movie, TV, and music removal. The item-detail architecture is documented separately.

### Fixed

- **Library visibility safety** — The visibility save control stays disabled until library discovery succeeds, preventing a failed or empty library load from clearing existing preferences.
- **TV metadata** — Season and episode identifiers, sort values, nested movie media metadata, and ended-show air dates now have reliable fallbacks.
- **Removal identity** — Movie and requester matching use stable IDs where available and report ambiguity instead of guessing.

## [1.11.0] - 2026-10-28

### Added

- **TV availability detail** — Requested episodes now distinguish available, unavailable, unaired, and unresolved content using Seerr season catalogs and Tautulli library records.
- **TV playback detail** — Episode and viewer last-watched dates are shown, with requester-specific whole-series progress separated from requested-content progress.

## [1.10.0] - 2026-10-28

### Added

- **TV request progress** — Item details now show each Seerr requester, requested seasons, available and unavailable requested content, per-episode watched states, and requested-content progress.
- **Conservative TV matching** — Stable requester identities are preferred, while unresolved identities and unavailable history remain explicitly unknown.

## [1.9.0] - 2026-10-26

### Fixed

- **TV library loading** — TV episode summaries no longer block the initial library response; summaries load separately with bounded concurrency and short-lived caching.
- **Episode play counts** — Episode totals now come from per-user Tautulli playback statistics instead of unavailable children metadata fields.
- **Episode viewer loading** — Per-episode viewer lookups now use bounded concurrency.

## [1.8.0] - 2026-10-26

### Changed

- **TV show file summary** — TV rows now show the total episode count and combined episode file size instead of the generic `On Disk` label.

## [1.7.0] - 2026-10-25

### Added

- **TV episode viewer breakdown** — TV show details now load seasons, episodes, per-episode play counts, and viewer play counts from Tautulli.

## [1.6.0] - 2026-02-16

### Added

- **Plex library refresh** — After removing items from Radarr/Sonarr/Lidarr, the app triggers a Plex library scan for affected sections (optional; `PLEX_URL` and `PLEX_TOKEN`).
- **Tautulli media info refresh** — After Plex refresh, the app waits 20 seconds then triggers a Tautulli media info refresh so removed items disappear from Tautulli’s cache; the page reloads when done.
- **Removal flow toast** — A single centered modal toast shows progress for the whole flow (removing items, Plex refresh, Tautulli refresh) with a dimmed page background.
- **TV show warning** — When TV shows are removed, the toast shows a flashing warning that Tautulli may take several minutes to update (full library rescan).
- **Tautulli “calculating file sizes” alert** — When Tautulli is calculating file sizes for a library, a yellow alert banner appears at the top. **Data shown may not be up to date** until calculation completes; file sizes and other fields can be missing or stale.
- **Debug endpoint** — With `DEBUG=true`, `GET /api/debug/tautulli-raw-response?type=show` returns the raw Tautulli library response for troubleshooting.

### Changed

- **Remove flow** — No longer modifies Tautulli directly; removal is Seerr + *arrs, then Plex refresh, then Tautulli refresh. Tautulli updates after Plex scans.
- **Library combined API** — Uses full Tautulli response to detect “calculating file sizes” (success response with `total_file_size`/`filtered_file_size` 0 and records present) and sets `tautulli_calculating_file_sizes` in the response so the frontend can show the banner.
- **README** — Documents Plex token, removal flow, Tautulli calculating banner, and TV show refresh behavior. Version set to 1.6.0.
- **Docker** — Dockerfile and docker-compose use version 1.6.0; docker-compose includes optional `PLEX_URL` and `PLEX_TOKEN`.

## [1.5.0] - 2026-02-15

### Added

- **Combined view by type** — Single dropdown: Movies, TV Shows, or Music; one merged list from all Tautulli libraries of that type.
- **Library column** — Table shows which Tautulli library each item belongs to; column is sortable.
- **Filter by library** — When a type is loaded, a "Filter by library" dropdown limits the list to one Tautulli library.

### Changed

- **Table layout** — Variable-width table with wrapping and horizontal scroll so columns are readable.
- **Error handling** — API client handles non-JSON (e.g. HTML) responses without crashing; non-API 404s (e.g. favicon) no longer surface as 500.

### Removed

- **Library-by-name env** — Per-instance `*_LIBRARY_NAME` mapping removed; removal runs against all configured *arr instances.

## [1.4.0] - 2026-02-15

### Added

- **Multi-arch Docker** — GitHub Actions builds and pushes `linux/amd64` and `linux/arm64` images (RPi-friendly).
- **Unit tests** — Pytest suite for app routes and ID extraction; CI runs tests before building the image.

## [1.3.0] - 2026-02-15

### Added

- **Footer** — App footer with version and link to GitHub repo.

### Changed

- **Requested by search** — Filter is client-side on the current page only (avoids backend timeout). Second search box for "Requested by" appears after the column is populated.
- **Docker** — Dockerfile version label (ARG VERSION) for image metadata.

## [1.2.0] - 2026-02-15

### Added

- **Search** — Search box appears when a library is loaded; searches only within the selected library and shows only matching items.
- **Production WSGI** — `wsgi.py` and gunicorn; with `DEBUG=false`, `python app.py` runs gunicorn instead of the Flask dev server. Docker image uses gunicorn in CMD.
- **DEBUG and STAT env flags** — `DEBUG` enables Flask debug mode and `/api/debug` routes; `STAT` enables `/api/status`. Both configurable via `.env` and Docker.

### Changed

- **Show library file column** — For TV show libraries, file column shows "—" when Tautulli does not provide file size (avoids incorrect "Missing" when files exist).
- **README and Docker** — Document DEBUG/STAT, standalone vs Docker run; compose and Dockerfile set production defaults; workflow triggers on `docker/**` and app files.

## [1.1.0] - 2026-02-15

### Changed

- **Seerr branding** — UI and docs use "Seerr" (Overseerr-compatible); request server chip and toasts show name from API.
- **Status chip names** — All service chips (Tautulli, Seerr, Radarr, Sonarr, Lidarr) show name from respective API or instance config.
- **Codebase refactor** — App split into config, services (tautulli, overseerr, radarr, sonarr, lidarr), routes (main, api), and utils; Flask app factory.

### Fixed

- **app.py** — Removed duplicate legacy code; entry point is create_app only.

## [1.0.0] - 2026-02-15

### Added

- **Lidarr support** — Remove music from Lidarr instances (primary and optional secondary), with column sorting for library view.
- **Docker support** — Alpine-based image and GitHub Actions workflow to build and push to `ghcr.io`. Docker Compose example uses pre-built image.
- **Column sorting** — Sort library by title, year, added date, last played, play count, size, or requested by (click column headers).
- **Screenshot and README** — Screenshot section and images folder for documentation.
- **Debug endpoint** — Endpoint for troubleshooting metadata resolution (IMDB/TMDB IDs).

### Changed

- **ID resolution** — Extract IMDB IDs from Tautulli guids and legacy Plex agent formats; Radarr lookup falls back to IMDB when TMDB is unavailable.
- **Graceful skip** — When no IDs can be resolved, Seerr and *arr steps are skipped instead of failing; Tautulli cleanup still runs.
- **Artist library display** — Correct file badge and Seerr skip behavior for music libraries.

### Fixed

- Missing Plex metadata no longer blocks removal; Tautulli history and cache are still cleaned when Plex metadata is unavailable.

[1.12.0]: https://github.com/shawnthompson/Magic-Erasarr/releases/tag/v1.12.0
[1.6.0]: https://github.com/shawnthompson/Magic-Erasarr/releases/tag/v1.6.0
[1.5.0]: https://github.com/shawnthompson/Magic-Erasarr/releases/tag/v1.5.0
[1.4.0]: https://github.com/shawnthompson/Magic-Erasarr/releases/tag/v1.4.0
[1.3.0]: https://github.com/shawnthompson/Magic-Erasarr/releases/tag/v1.3.0
[1.2.0]: https://github.com/shawnthompson/Magic-Erasarr/releases/tag/v1.2.0
[1.1.0]: https://github.com/shawnthompson/Magic-Erasarr/releases/tag/v1.1.0
[1.0.0]: https://github.com/shawnthompson/Magic-Erasarr/releases/tag/v1.0.0
