# Design: clear-ranking.sh

**Date:** 2026-05-19
**Status:** Approved

## Summary

Add a `clear-ranking.sh` script to the repo root that interactively erases ranking
data stored in the production Docker container, following the same pattern as
`up.sh`, `down.sh`, and the other management scripts. The script also supports
regenerating the ranking from the current contest data in the database.

## Background

`RankingWebServer` persists its data as JSON files under
`/home/cmsuser/cms/lib/ranking/` inside the `cms` container (part of the
`cms-data` volume). The stores are:

| Directory      | Contents                                      |
|----------------|-----------------------------------------------|
| `contests/`    | Contest metadata (name, start, end)           |
| `tasks/`       | Task list and max scores                      |
| `teams/`       | Team definitions (auto-seeded from mx_states) |
| `users/`       | Contestant profiles                           |
| `submissions/` | Per-submission score records                  |
| `subchanges/`  | Per-testcase score detail records             |

Scores are computed in-memory by `ScoringStore` from submissions and subchanges;
they have no own directory.

The ranking process runs inside the `cms` container under supervisord with the
program name `cmsrankingwebserver`.

`ProxyService` (program name `cmsproxyservice`) is the bridge between the CMS
database and `RankingWebServer`. It is read-only with respect to the database: it
reads scored submissions and HTTP-PUTs them to the ranking. It tracks which
submissions it has already forwarded in an in-memory set; restarting it resets
that set, causing its sweeper (interval ~347 s) to re-push all scored submissions.

## Behavior

### Interactive questions (all default to `n`)

```
Clear results? (submissions and subchanges) [y/N]
Clear users? [y/N]
Clear tasks and contests? [y/N]
Regenerate ranking from current contest data? [y/N]
```

All four are independent yes/no prompts using the existing `ask_yes_no` helper
from `docker/_lib.sh`.

### Guard: nothing selected

If the user answers `n` to all four questions, the script prints:

```
Nothing selected, exiting.
```

and exits 0 without touching any service.

### Guard: inconsistency warning

If the user selects **tasks and contests** but does NOT select **results**, the
script prints a warning before proceeding:

```
Warning: clearing tasks and contests without clearing results will leave
orphaned submission records in the ranking. Consider also clearing results.
```

The script continues regardless — it is a warning, not a hard stop.

### Execution order

1. Stop the ranking process:
   `supervisorctl stop cmsrankingwebserver`
2. Delete selected JSON files inside the container:
   - Results selected → `rm -f .../submissions/*.json .../subchanges/*.json`
   - Users selected → `rm -f .../users/*.json`
   - Tasks and contests selected → `rm -f .../tasks/*.json .../contests/*.json`
3. Start the ranking process:
   `supervisorctl start cmsrankingwebserver`
4. If regenerate selected → restart ProxyService:
   `supervisorctl restart cmsproxyservice`
   Print notice: `Scores will appear in the ranking within ~6 minutes.`

All `supervisorctl` calls are executed inside the container via
`"${COMPOSE_CMD[@]}" exec cms supervisorctl ...`.

All `rm` calls are executed inside the container via
`"${COMPOSE_CMD[@]}" exec cms sh -c 'rm -f ...'`.

The base path for ranking data is `/home/cmsuser/cms/lib/ranking`.

### Safety

- Restarting `cmsrankingwebserver` only affects the scoreboard display.
- Restarting `cmsproxyservice` only affects scoreboard delivery; it never writes
  to the database. Scores are committed to PostgreSQL before ProxyService is
  notified, so no data is lost if ProxyService is down during the restart.
- Neither restart interrupts contestant submissions or evaluation.

## Files changed

| File | Change |
|------|--------|
| `clear-ranking.sh` | New script (executable) |
| `docs/docker-scripts.md` | Add `clear-ranking.sh` section to the Scripts reference |

## Documentation

A new section `### clear-ranking.sh` is appended to the **Scripts reference**
section of `docs/docker-scripts.md`, following the style of existing entries:
one-sentence description, code block with the command, and a short note about
the regenerate option and the ~6-minute delay.
