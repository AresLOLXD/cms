# Design

## Context

`_do_up` in `docker/_lib.sh` asks the local database question, an optional rebuild choice, and then runs `up -d --wait`. Choices 3 and 6 start only the ranking (`--no-deps`) so the CMS is not recreated from an older image. The documented rule for multi-contest updates is ranking first (see `docs/multi-contest.md`, "Deployment"); today it means two runs of the script. `./restart.sh` calls `_do_up` too, so it gets the new choice for free. The `ranking` service has `restart: unless-stopped` and no healthcheck; `cms` has one.

## Goals / Non-Goals

**Goals:**
- One choice that encodes the order, and refuses to go on when the ranking is not up.
- A failed build must not leave a half-updated deployment.

**Non-Goals:**
- No no-cache variant, no separate script, no change to choices 1 to 7.
- No backup, `git pull` or post-deploy checks inside `up.sh`.

## Decisions

- **Build all three images first** (`build ranking cms db-init`), then start. The long step is the CMS build; if it fails, nothing was replaced. Alternative: ranking build, ranking start, CMS build; rejected because a CMS build failure would then come after the ranking was already replaced. The build list is choice 4's (`cms db-init`) plus `ranking`.
- **Start the ranking with the command choice 3 uses** (`up -d --no-deps --wait --wait-timeout 90 ranking`), then the usual `up -d --wait --wait-timeout 90`. `GIT_SHA` is exported once at the top of `_do_up`, so both steps see the same build args and the second `up` does not recreate the ranking. The profile for the local database is already in the compose command both steps share.
- **A failing ranking step ends the script** with the message on stderr and `exit 1`, so the second `up` never runs.
- **Add a ranking healthcheck.** Measured on the local rootful Docker with a scratch project: a service with `restart: unless-stopped`, no healthcheck and a command that always exits made `up --wait` return 0 ("Healthy") while its restart count kept growing. With a healthcheck that fails, `up --wait` returned 1 ("is unhealthy") at once. The check is `python3` with `urllib` against the ranking's own HTTP port (`/` serves the scoreboard page without credentials); the image has python3 and no curl. Interval 5 s, timeout 5 s, 5 retries, start period 30 s.
- **Tests** extend `docker/test_lib.sh` (a stub `docker` records the calls) and that file is added to the CI test script, which did not run it before.

## Risks / Trade-offs

- Choices 3 and 6 now wait for the ranking healthcheck as well → their wait is still bounded by the existing 90 s.
- A ranking that loads a very large data set and needs more than 90 s to listen would make choice 8 stop with the error → the ranking is then already running the new code; re-run `./up.sh` and choose 4 once it is healthy. The ranking's startup is a few seconds today.
- The first deploy after this change recreates the ranking container (its configuration changed) → same as any ranking update.
- The wait covers "healthy within 90 s", not "stays healthy"; a ranking that dies later is handled by `restart: unless-stopped` and the watchdog, as before.
