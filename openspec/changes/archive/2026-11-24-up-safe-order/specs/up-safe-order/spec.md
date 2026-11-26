# Spec Delta

## Purpose

Lets the operator update a deployment that has a ranking server and a CMS in the order the software needs (ranking first), with one menu choice, and stops without rebuilding or starting the CMS when the ranking does not come up.

## ADDED Requirements

### Requirement: The rebuild menu offers a safe-order choice
The rebuild menu of `./up.sh` SHALL list the choice `8) Ranking, then CMS (safe order)` and SHALL accept 1 to 8. Choices 1 to 7 SHALL keep the commands they run today, in the same order (what they wait for changes only through the ranking healthcheck, see below).

#### Scenario: The menu lists the new choice
- **WHEN** the operator runs `./up.sh` and reaches the rebuild question
- **THEN** the menu lists `8) Ranking, then CMS (safe order)` and the prompt says `Choice [1-8]`

#### Scenario: The old choices are unchanged
- **WHEN** the operator picks any of the choices 1 to 7
- **THEN** the script builds and starts exactly what it did before this change

### Requirement: Everything is built before anything is started
Choice 8 SHALL build the ranking, CMS and db-init images before it starts or recreates any container, so that a failed build leaves the running deployment untouched.

#### Scenario: Order of build and start
- **WHEN** the operator picks choice 8 and everything succeeds
- **THEN** one build of the ranking, cms and db-init images runs first, then the ranking alone is started and waited for, and then the remaining services are started

#### Scenario: A failed build touches nothing
- **WHEN** the operator picks choice 8 and the build fails
- **THEN** the script exits non-zero and no container is started or recreated

### Requirement: The CMS is not started when the ranking does not come up
Choice 8 SHALL start the CMS side only after the ranking container is healthy. When the ranking does not become healthy, the script SHALL print `ERROR: the ranking did not come up; the CMS was not rebuilt or started by this run.` to standard error, exit non-zero and neither start nor recreate any other service.

#### Scenario: The ranking does not come up
- **WHEN** the operator picks choice 8 and starting the ranking fails or times out
- **THEN** the script prints the error to standard error, exits non-zero and does not start the CMS

#### Scenario: A crash-looping ranking counts as not up
- **WHEN** the ranking container starts but its process keeps exiting
- **THEN** the ranking container is never reported healthy, so choice 8 stops as in the previous scenario

### Requirement: Both steps use the same revision and database setting
Choice 8 SHALL use one image revision label and the operator's local database answer for both the ranking step and the CMS step, so the second step does not recreate the ranking.

#### Scenario: One revision for both steps
- **WHEN** the operator picks choice 8
- **THEN** every command of that run sees the same revision label

#### Scenario: The local database applies to both steps
- **WHEN** the operator answers yes to the local PostgreSQL question and picks choice 8
- **THEN** every compose command of that run carries the local database profile

### Requirement: Starting waits for a healthy ranking
Every start that includes the ranking (choices 1, 2, 4, 5 and 7, and the second step of choice 8) SHALL also wait for the ranking container to be healthy, and SHALL exit non-zero when it is not. Choices 3 and 6 SHALL wait for the same healthcheck.

#### Scenario: A plain start with an unhealthy ranking
- **WHEN** the operator picks choice 1, 2, 4, 5 or 7 and the start fails because the ranking is not healthy
- **THEN** the script exits non-zero, even though the CMS side was started by that same command
