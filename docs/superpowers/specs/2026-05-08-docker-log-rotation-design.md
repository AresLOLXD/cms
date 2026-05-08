# Docker Log Rotation — Design Spec

**Date:** 2026-05-08

## Goal

Configure Docker log rotation for the CMS production container so logs don't consume unbounded disk space, with defaults sized for a 100-student, 5-hour, 5-problem contest with up to 100 testcases per problem.

## Sizing rationale

| Parameter | Value |
|---|---|
| Students | 100 |
| Submissions per student | up to 100 |
| Problems | 5 |
| Testcases per problem | up to 100 |
| Estimated log per submission | ~33 KB (Worker + EvaluationService + ScoringService) |
| **Worst-case total** | **~350 MB** |

Default capacity: `500m × 10 = 5 GB` — ~14× the worst-case estimate, giving ample headroom for verbose runs, restarts, and unexpected spikes.

## Context

- `supervisord` routes every service's stdout/stderr to `/dev/stdout` and `/dev/stderr` with `maxbytes=0` (no internal rotation).
- Docker's `json-file` driver collects this output. Without a `logging:` config, files grow without limit.
- The `cms-logs` volume (CMS file logs written by each service) is **not** in scope — it is already persistent and independent of this change.

## Decisions

| Decision | Choice | Reason |
|---|---|---|
| Log driver | `json-file` (Docker default) | No infrastructure dependency (no Fluentd, no Loki) |
| Default max-size | `500m` | Balances granularity with headroom |
| Default max-file | `10` | 5 GB total; survives a full contest + restarts |
| Configurable? | Yes — via `.env` | Different deployments have different disk constraints |
| Env var names | `CMS_LOG_MAX_SIZE`, `CMS_LOG_MAX_FILES` | Consistent with `CMS_` prefix convention |

## Changes

### `docker/docker-compose.prod.yml` — `cms` service

Add a `logging:` block:

```yaml
logging:
  driver: "json-file"
  options:
    max-size: "${CMS_LOG_MAX_SIZE:-500m}"
    max-file: "${CMS_LOG_MAX_FILES:-10}"
```

### `.env.example`

Add a new optional section:

```ini
# -----------------------------------------------------------
# LOGGING (optional — defaults shown)
# -----------------------------------------------------------

# Maximum size of each Docker log file before rotation.
# Use Docker size units: 500m = 500 MB, 1g = 1 GB.
CMS_LOG_MAX_SIZE=500m

# Number of rotated log files to keep (total = MAX_SIZE × MAX_FILES).
# Default: 10 files × 500m = 5 GB max on disk.
CMS_LOG_MAX_FILES=10
```

## Out of scope

- Rotating the CMS file logs in the `cms-logs` volume.
- Structured logging or log shipping (Fluentd, Loki, etc.).
- `docker-compose.dev.yml` — development logs don't need rotation.
