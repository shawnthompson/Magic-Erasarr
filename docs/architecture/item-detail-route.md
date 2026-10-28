# Item detail route

This document records the design for issue #10. It is intentionally planning-only: the route described here must remain read-only and must not add deletion, request, playback-history, download, or other media-changing actions.

## Decision summary

- Use one shared route: `/item/<rating_key>` for movies and TV shows. The media type determines which sections are shown; it does not determine the URL shape.
- Treat the route as the canonical detail view. Keep the library table compact and make the item title a normal keyboard-focusable link to the route.
- Preserve the existing modal as a compatibility path during migration, then remove it after the route has equivalent coverage. The route owns rich details; focused dialogs are reserved for dense secondary information.
- Load the detail shell from Flask and fetch detail data on demand from read-only API endpoints. Do not put credentials, service URLs, or access tokens in the URL or HTML.
- Use the rating key only as a lookup handle. Resolve current metadata before rendering destructive-adjacent actions; this first version has no destructive actions at all.
- Display `Unknown`, `Unavailable`, or `Not returned` when the source cannot establish a fact. Never infer watched state, requester identity, episode availability, or completion from missing data.

## Navigation model

The library title links to `/item/<rating_key>` and preserves the current dashboard context in query parameters:

```text
/item/12345?type=show&library=TV&return=%2F%3Ftype%3Dshow%26library%3DTV
```

The `return` value is an optional same-origin, relative path. The detail page must validate it before using it; invalid, external, or missing values fall back to the dashboard. The route must also work when opened directly, refreshed, bookmarked, or reached with browser back/forward navigation.

The page provides a visible `Back to library` link. The browser's normal Back action remains the primary return mechanism; the link uses the validated return location when one was supplied. The selected type and library filter are context, not identity, so a missing or stale context must not prevent opening the item.

## Shared page structure

Both movies and shows use the same top-level order:

1. Back navigation and a page heading containing title, year, and media type.
2. A short status summary: availability, last played, play count, and any source warning.
3. Requesters and requester-specific watch status, when Seerr and identity data support it.
4. Media and library facts: file status, size, added date, and technical metadata.
5. Playback summary: whole-item viewers and last-played information.
6. Media-specific details.

Movie pages stop after the shared playback and technical sections. TV pages add the following sections in this order:

- `Requested content progress`, with one card per requester and separate requested-season/requested-episode totals.
- `Whole-series progress`, explicitly labeled and calculated independently from requested content.
- `Seasons and episodes`, grouped by season and lazy-loaded or paginated when large.
- Episode status, last-watched date, and viewers where available.

Requester cards must remain separate. A show with multiple requesters must never combine their requested ranges or playback state into one aggregate status.

## Focused sub-modals

The primary route should remain scannable. Use a dialog only for information that is useful on demand but too dense for the main page:

- Episode viewer statistics and playback history details.
- Technical media information when it contains more fields than the summary can show.

Each dialog needs a descriptive heading, an accessible close button, focus returned to its trigger, Escape support, and a clear unavailable/error state. A dialog must not be the only place where the existence of a warning or unresolved identity is communicated.

## Data and loading states

The route should use separate read-only requests so one slow or failed source does not hide the rest of the page:

- Base item metadata and current rating-key validation.
- Seerr requesters and requested ranges.
- Tautulli playback summary.
- TV season/episode details and episode playback data.
- Optional poster/media proxy.

Show skeleton or loading text in each section, not a page-wide indefinite spinner. If a request fails, retain successful sections and label the failed section `Unavailable` with a retry action. Empty results should say `No data returned`, which is distinct from an error. A refreshed detail page should use the same states as first navigation.

The route must account for these cases explicitly:

- Missing or stale rating key: show an item-not-found state with a link back to the library; do not substitute a title search.
- Changed metadata while the page is open: ignore late responses for a previous item and offer reload for the current key.
- Missing media file: show `Missing or unavailable`; do not treat a zero size as proof that the item has no file.
- Ongoing series: count only episodes that are available and known; do not mark the series complete because unaired or unavailable episodes have no history.
- Unresolved or managed-profile identity: show `Unknown`; do not match by display name alone.
- Missing playback history: show `No playback recorded` only when the service responded successfully with no records; use `Unavailable` for a failed lookup.

## Accessibility and responsive behavior

- Use semantic headings, sections, lists, tables, and links; use ARIA only where native semantics do not cover the interaction.
- The item title link and every dialog trigger must be keyboard reachable with a visible focus indicator.
- Give poster images meaningful alt text when they convey content; use empty alt text when the adjacent heading already identifies the same item.
- Ensure episode tables have scoped column headers and do not require horizontal scrolling for essential status information on narrow screens. Less important technical columns may collapse into labeled fields.
- Keep requester status understandable without color alone; use text labels such as `Watched`, `Partially watched`, `Not watched`, `Unknown`, and `Unavailable`.
- Keep loading and error announcements associated with their section and avoid moving focus unexpectedly during background refreshes.
- Dialogs must trap focus while open, restore focus on close, and remain usable at small viewport sizes without hiding the close control.

## Implementation sequence

1. Add the Flask route and a detail-page template with the shared loading/error shell.
2. Extract or add read-only detail API responses with stable response shapes and bounded work for TV episode data.
3. Replace the table's modal-only title action with a normal detail link while retaining a temporary compatibility modal if needed.
4. Add movie and TV fixtures covering complete, ongoing, partial, missing, stale-key, unresolved-identity, and failed-service states.
5. Add keyboard/screen-reader and narrow-viewport checks before removing the compatibility modal.

The follow-up implementation should not add delete buttons or other state-changing controls to this route. Any such action requires a separate issue and explicit review.

