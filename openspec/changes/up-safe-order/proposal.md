# Proposal

## Why

Updating a multi-contest deployment has a fixed order: the ranking must run the new code before the CMS does, because the new ProxyService sends visibility windows that the old ranking rejects (docs/multi-contest.md, "Deployment"). Today that is two manual runs of `./up.sh` (choice 3, then choice 4), and choice 2 or 7 ("All services") breaks the rule. Nothing stops an operator from doing it in the wrong order, and a long CMS build that fails after the ranking was already replaced leaves the deployment half updated. See issue #85.

## What Changes

- `./up.sh` (and `./restart.sh`, which shares the menu) gets choice `8) Ranking, then CMS (safe order)`. It builds the ranking, CMS and db-init images first, starts the ranking alone, and only if the ranking came up starts the rest.
- If the ranking does not come up, the script prints an error to stderr, exits non-zero and does not start or recreate the CMS.
- The ranking container gets a healthcheck (HTTP on its own port). Without it the "came up" test would only mean "the container is running", and a ranking that crashes in a loop would pass. Measured locally: `up --wait` returns 0 for such a ranking when there is no healthcheck.
- Choices 1 to 7 keep their behavior. No no-cache variant, no new script.
- Docs (English and Spanish) describe the new choice.

## Capabilities

### New Capabilities
- `up-safe-order`: the operator menu choice that updates the ranking first and the CMS second, and what it guarantees when the ranking does not come up.

### Modified Capabilities
<!-- None: the choices 1 to 7 and their behavior do not change. -->

## Impact

- `docker/_lib.sh` (menu and `case` in `_do_up`), `docker/docker-compose.prod.yml` (ranking healthcheck).
- `docker/test_lib.sh` (new tests; wired into CI through `docker/_cms-test-internal.sh`).
- `docs/docker-scripts.md`, `docs/docker-deployment.md`, `docs/multi-contest.md` and their Spanish `.po` files.
- Choices 3 and 6 now also wait for the ranking healthcheck (up to the existing 90 s wait) instead of only "running". The first deploy after this change recreates the ranking container because its configuration changed.
