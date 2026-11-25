# Tasks

## 1. The menu choice and the ranking healthcheck

- [x] 1.1 Add choice 8 to `_do_up` (menu text, prompt `[1-8]`, build first, ranking step with the error and exit, then the usual `up`) and verify with `bash docker/test_lib.sh` (tests for the order, the failed ranking step, the failed build, the local database and one revision)
- [x] 1.2 Add the ranking healthcheck to `docker/docker-compose.prod.yml` and verify that `docker compose config` accepts it (done) and that `up --wait` fails for a crash-looping ranking and passes for a healthy one on a scratch project
- [x] 1.3 Run `docker/test_lib.sh` in CI by adding it to `docker/_cms-test-internal.sh` and verify it passes in the same conditions as the CI container

## 2. Documentation

- [x] 2.1 Describe choice 8 in `docs/docker-scripts.md`, `docs/docker-deployment.md` and the "Deployment" section of `docs/multi-contest.md` (keep the manual 3 then 4 as the explanation), regenerate the `.pot` and Spanish `.po` files, and verify `docs/check_translations.py` and the English and Spanish Sphinx builds pass

## 3. Verification

- [x] 3.1 Run choice 8 once on a scratch compose project with the local rootful Docker (normal run, ranking not recreated by the second step, crash-looping ranking stops the run) and `openspec validate up-safe-order --strict`
