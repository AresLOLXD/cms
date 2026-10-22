# Ranking Visibility Polish Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Close the rough edges that the MC-2 reviews left on the hidden and frozen pages of RWS and on the ranking group pages of AWS (issue #14, all items but 8).

**Architecture:** Almost everything is in `VisibilityGuard` (`cmsranking/visibility.py`), the WSGI middleware that decides what a group serves. A view change wakes the open event streams with the `reload` event of the group's `DataWatcher`; the write guard already replaces data by a reload for a client whose view changed, and now applies that to the staff too. AWS gets a row lock and a better "next:" label.

**Tech Stack:** Python 3.12, gevent `pywsgi`, werkzeug, SQLAlchemy, unittest. `cmsranking` tests run in the gevent pytest group, AWS tests in the asyncio group.

**Spec:** `docs/superpowers/specs/2026-10-21-ranking-visibility-polish-design.md`

## Global Constraints

- Code, comments, test names and commits in English; the user-facing RWS strings stay in Spanish, as the existing ones.
- Python 3.12, per-worktree `.venv`; run tests with `CMS_CONFIG=/tmp/claude-1000/-var-home-areslolxd-Documentos-cms/ea26d07b-cc2a-4e41-a2a9-aba4c85e00c4/scratchpad/cms-loadtest-criteria.toml .venv/bin/pytest -p no:cacheprovider <file> -q`, one file at a time. Never run a directory or a bare pytest (gevent monkey-patching). `uvx -q pyflakes <changed files>` clean.
- Lines of at most 79 columns; docstrings in the project's format; a `shortcut: <limit>, <when to upgrade>` comment for a known limit.
- No new dependency. `FROZEN_ROUTES` and the event cache are untouched.
- One commit per task, Conventional Commits, trailer `Co-Authored-By: Claude Sonnet 5.5 <noreply@anthropic.com>`. Never push or merge.

## Review Focus

- A view change must send each stream exactly one reload and then close it; a page told to reload must never get another reload for it. (Task 3 counts `event:reload` in what a stream receives.)
- A change of the staff password alone is not a view change: no stream gets a reload. (Task 3)
- The login counter must come back down when the password check raises or the response is a 401. (Task 4)
- `HEAD` on the index paths of a hidden group gets the notice headers, with no body. (Task 1)
- A future end without its start, and two fields at the same instant, must not break the "next:" label. (Task 6)

---

### Task 1: The index paths get the banner and the notice (items 1, 2)

**Files:**
- Modify: `cmsranking/visibility.py` (`VisibilityGuard.__call__`)
- Test: `cmstestsuite/unit_tests/cmsranking/test_visibility.py`

**Interfaces:**
- Produces: nothing new. `INDEX_PATHS = ("/", "/Ranking.html")` (already in the module) is the one rule for "the index page".

- [ ] **Step 1: Tests.** Remove `"Ranking.html"` from `DATA_PATHS` (it is the one entry that is no longer a 403 for a hidden group). In `TestHiddenGroup` add `test_ranking_html_shows_the_notice`: for method in GET and HEAD, `/olim/Ranking.html` gives 200, `text/html`, `Cache-Control: no-store`; the GET body contains `<title>Ranking oculto</title>` and `action="staff-login"`; the HEAD body is empty. In the class with `test_staff_cookie_grants_access_and_banner` add `test_staff_get_the_banner_at_ranking_html`: with the staff cookie, `/olim/Ranking.html` contains `Vista staff: este ranking está oculto al público`. In `TestFrozenPublicView` extend `test_staff_get_the_frozen_banner` to loop over `("", "Ranking.html")`.
- [ ] **Step 2:** Run the file; the three tests fail.
- [ ] **Step 3:** In `VisibilityGuard.__call__` change the two conditions `path == "/"` (the staff banner branch, the hidden-public notice branch) to `path in INDEX_PATHS`, keeping the methods they check today.
- [ ] **Step 4:** Run the whole file: all pass. Commit `fix(rws): show the hidden notice and the staff banner at /Ranking.html`.

### Task 2: A public answer for the view, and the notice polls it (item 5)

**Files:**
- Modify: `cmsranking/visibility.py` (`NOTICE_POLL`, its comment, `VisibilityGuard.__call__`)
- Test: `cmstestsuite/unit_tests/cmsranking/test_visibility.py`

**Interfaces:**
- Produces: `GET <group>/visibility`, answered by the guard to anybody, before the staff, hidden and frozen branches: 200, `text/plain`, `Cache-Control: no-store`, a body that is exactly `hidden`, `frozen` or `live` (the public view, as `VisibilitySettings.public_view` says). `PUT /visibility` is unchanged. Other methods on that path behave as today.

- [ ] **Step 1: Tests.** Change `test_visibility_is_only_special_for_put`: the GET part now expects 200 (see the new test below); POST and DELETE keep their 403. Change `test_notice_comes_back_by_itself`: the notice contains `fetch("visibility", {cache: "no-store"})` and `location.reload()`, and no longer `fetch("config"`. Add `test_visibility_answers_the_public_view`: `GET /olim/visibility` with no credentials returns `hidden` for a hidden group, `live` after `put_visibility("olim", False)`, and `frozen` for a group frozen from the epoch (use `put_freeze`-style PUT with `freeze_at` 1); each with `Cache-Control: no-store` and mimetype `text/plain`; with a valid staff cookie the answer is the same public view.
- [ ] **Step 2:** Run; fails.
- [ ] **Step 3:** In `__call__`, after the `PUT /visibility` branch add the `GET /visibility` branch returning the word. Change `NOTICE_POLL` to fetch `visibility` and reload when the answer is not `hidden` (a failed or non-OK fetch is treated as still hidden); rewrite the comment above it to say so.
- [ ] **Step 4:** Run the whole file: pass. Commit `feat(rws): let the hidden notice poll a public answer instead of a 403`.

### Task 3: Staff streams reload on a view change, and a change wakes the streams (items 3, 4)

**Files:**
- Modify: `cmsranking/visibility.py` (`_guard_writes`, `_update`), `cmsranking/RankingWebServer.py` (`build_ranking_app`)
- Test: `cmstestsuite/unit_tests/cmsranking/test_visibility.py` (class `TestStreamsAcrossTransitions`)

**Interfaces:**
- Produces: `build_ranking_app(...)` returns the WSGI app with an attribute `event_source` (the `DataWatcher`, i.e. `toplevel_handler.event_handler`). `VisibilityGuard._update` calls `getattr(self.app, "event_source", None)` and, if there is one and the public view differs before and after `update_settings` (compare `settings.public_view(time.time())` of the state before and after), calls its `send("reload", "")`. An app without `event_source` is left alone.
- Changes: in `_guard_writes.guarded_write`, a staff client (`self._is_staff(request)`) whose stream was opened before the last change of the public view (`self.state.last_change(now) > opened_at`) is cut exactly as a public client is: `_close_connection`, `write(RELOAD_EVENT)` for an event stream, then `ConnectionAbortedError`. The conditions for the public client stay as they are.

- [ ] **Step 1: Tests**, all in `TestStreamsAcrossTransitions` (default ping, 15 s), each reading the stream with `drain_for(stream, 3)`:
  - Replace `test_staff_stream_survives_hiding` (in `TestRealEventStream`) by `test_staff_stream_is_told_to_reload_when_the_group_gets_hidden`: staff stream opened on a visible group, `put_visibility("olim", True)`, no other write; received contains `event:reload` exactly once, and `closed` is true.
  - `test_public_stream_reloads_at_once_when_the_group_gets_hidden`: same with a public stream (this one fails before the change: nothing arrives before the ping).
  - `test_public_stream_reloads_at_once_when_the_group_is_unfrozen`: stream opened on a group frozen from the epoch (`put_freeze(1)`), then `put_freeze(1, unfreeze_at=2)`: one reload, closed.
  - `test_a_password_change_alone_sends_no_reload`: public stream on a visible group, `put_visibility("olim", False, staff_password=<another hash>)`, then `put_second_contest()`: received contains `data:create c2` and does not contain `event:reload`.
  - Keep `test_staff_stream_is_closed_when_the_password_changes` and `test_staff_stream_of_a_hidden_group_delivers` passing as they are.
- [ ] **Step 2:** Run; the new and the replaced tests fail (the password test passes already).
- [ ] **Step 3:** Implement as in Interfaces. In `build_ranking_app` set the attribute on the returned `wsgi_app` just before returning.
- [ ] **Step 4:** Run the whole file: pass. Commit `feat(rws): reload open pages when the view changes, staff pages included`.

### Task 4: Bound the queue of staff-password checks (item 10)

**Files:**
- Modify: `cmsranking/visibility.py` (constant `LOGIN_MAX_PENDING = 16`, module counter, `VisibilityGuard._login`)
- Test: `cmstestsuite/unit_tests/cmsranking/test_visibility.py`

**Interfaces:**
- Produces: `LOGIN_MAX_PENDING`. Past it, `_login` answers `503`, `Retry-After: 1`, `Cache-Control: no-store`, `text/plain`, body `Demasiados intentos de acceso. Intenta de nuevo en un momento.`, without queuing a check. The count of checks waiting or running goes up before `_login_pool().apply(...)` and goes down in a `finally`; the count is process-wide (the pool is shared by all groups).

- [ ] **Step 1: Tests.** `test_logins_past_the_limit_get_a_503`: patch `LOGIN_MAX_PENDING` to 2 and `validate_password` (in `cmsranking.visibility`) with a function that blocks on an event made with `get_original("threading", "Event")`, since it runs in a real thread of the pool; spawn two `gevent.spawn(self.client.post, "/olim/staff-login", data={"password": "x"})`; once both are inside, a third post returns 503 with `Retry-After: 1` at once and `validate_password` was called twice; release the event, join the two, and a further login with the right password (unblocked patch) gets 303: the count came back to 0. `test_a_failing_check_gives_its_slot_back`: patch `validate_password` to raise `ValueError` and `gevent.sleep`; make `LOGIN_MAX_PENDING + 2` posts one after the other: every one is 401 (none 503).
- [ ] **Step 2:** Run; fails.
- [ ] **Step 3:** Implement. Keep the check after the body is read and before the pool call.
- [ ] **Step 4:** Run the whole file: pass. Commit `fix(rws): answer 503 when too many staff logins are waiting`.

### Task 5: The overlay stops above the banner; check HEAD (items 9, 11)

**Files:**
- Modify: `cmsranking/visibility.py` (`_banner`'s `<style>`)
- Test: `cmstestsuite/unit_tests/cmsranking/test_visibility.py`

**Interfaces:**
- Changes: the `<style>` of `_banner` gets `#TeamSearch_bg` in the rule that already has `#InnerFrame,#UserDetail_bg{bottom:var(--rws-banner)}`.

- [ ] **Step 1:** Item 11, first: add `test_head_of_the_index_has_a_content_length` in `TestRealEventStream` or `TestStreamsAcrossTransitions` (they have a real server): raw `HEAD /olim/ HTTP/1.1` to a hidden group (the notice) and to a visible one (the static page): status 200, a `Content-Length` header, no body. Run it. If it passes, keep it. If it fails for the notice, fix the notice response. If it fails only for the visible static page, delete the test, change nothing, and say so in the report (it is not ours).
- [ ] **Step 2:** Item 9: add the test `test_banner_lifts_the_search_overlay` asserting `#TeamSearch_bg` is in the rule with `bottom:var(--rws-banner)` for the staff banner and for the public frozen banner; run (fails); add the selector; run (passes).
- [ ] **Step 3:** See it: with the Chromium of the machine (`flatpak run --filesystem=/tmp/claude-1000 org.chromium.Chromium --headless=new --no-sandbox --disable-gpu --screenshot=<file in the scratchpad> --window-size=360,700 <url>`; it works here) take a screenshot of a frozen group's page served by a throwaway script that starts the real app behind `WSGIServer` as `TestRealEventStream.connect` does, before and after the selector. If the search panel cannot be opened in a screenshot, say "not seen" in the report. Put no script in the repository.
- [ ] **Step 4:** Whole file passes. Commit `fix(rws): keep the team search overlay above the status bar`.

### Task 6: AWS: lock the group row, and a "next:" label that changes something (items 6, 7)

**Files:**
- Modify: `cms/server/admin/handlers/rankinggroup.py` (`RankingGroupHandler._post_sync`, `visibility_view`)
- Test: `cmstestsuite/unit_tests/server/admin/rankinggroup_test.py`

**Interfaces:**
- `RankingGroupHandler._post_sync`: right after `group = self.safe_get_item(RankingGroup, group_id)`, `self.sql_session.refresh(group, with_for_update=True)`.
- `visibility_view(group, now)`'s `"next_change"` is the label and times of the earliest future field time `t` for which the pair `(group.is_hidden_at(t), window_is_open(group.freeze_at, group.unfreeze_at, t))` differs from the pair at `now`; `""` if there is none. The text format is unchanged (`"<label>: <UTC> UTC (<local>)"`).

- [ ] **Step 1: Tests.** In `TestRankingGroupHandlerSavesVisibility.test_edit_hides_and_sets_password` set `handler.sql_session = MagicMock()` and assert `handler.sql_session.refresh.assert_called_once_with(group, with_for_update=True)` (add `handler.sql_session = MagicMock()` wherever another test there calls `_post_sync("1")`). In `TestVisibilityView` add: `test_next_change_skips_an_end_without_a_start` (`unfreeze_at=NOW+HOUR` alone, and `show_at=NOW+HOUR` alone, both give `""`); `test_next_change_skips_a_time_that_changes_nothing_shown` (hidden now with `show_at=NOW+3*HOUR`, `freeze_at=NOW+HOUR`, `unfreeze_at=NOW+2*HOUR`: the next change is the freeze at +1 h, and with only `unfreeze_at` after a freeze that ended in the past it is `""`); `test_next_change_with_two_times_at_the_same_instant` (`freeze_at` and `show_at` both `NOW+HOUR` while hidden: the answer names one of them, does not fail). `test_next_change_is_the_earliest_future_time` must pass unchanged.
- [ ] **Step 2:** Run the file; the new tests fail.
- [ ] **Step 3:** Implement both.
- [ ] **Step 4:** Run the file: pass; `uvx -q pyflakes`. Commit `fix(aws): lock the ranking group on edit and list only real changes in "next:"`.

### Task 7: Docs and Spanish catalog

**Files:**
- Modify: `docs/multi-contest.md` (the paragraph "When the public view of a group changes …", about line 392), `docs/locale/es/LC_MESSAGES/multi-contest.po`

- [ ] **Step 1:** Rewrite that paragraph so that it says: a change made by hand (a button, which ProxyService sends to RWS) reaches the open pages, the staff's included, within seconds; a scheduled change within about 15 seconds (the ping interval); on a hide the page shows the notice; the notice checks every 15 seconds (it asks a small public answer, `<group>/visibility`, that says `hidden`, `frozen` or `live`) and comes back by itself when the group is shown again or only frozen; a page reloads itself at most once every 10 seconds. Keep the rest of the paragraph (changing a window still ahead changes nothing; RWS restart). If the hidden-group section lists which public URLs answer what, add that `<group>/visibility` answers the word. Edit the Spanish entries by hand (do not run `sphinx-intl update`, it rewraps unrelated entries): same meaning, code spans verbatim, glossary terms of `docs/locale/es/GLOSSARY.md`, no fuzzy.
- [ ] **Step 2:** Run the docs check: `bash /tmp/claude-1000/-var-home-areslolxd-Documentos-cms/ea26d07b-cc2a-4e41-a2a9-aba4c85e00c4/scratchpad/docs-check-pb.sh` after changing its `SRC` line to this worktree (`/var/home/areslolxd/Documentos/cms/.worktrees/rws-polish`) in a copy; it must end `docs check rc=0`; `msgfmt --statistics` shows 0 fuzzy and 0 untranslated for the file. Commit `docs(rws): describe how open pages learn of a visibility change`.
