# Plan: group 5 of aws-archive-controls (operator-written archive_at) and review fixes

Source of truth: `design.md` (decisions 5, 9, 10 and Risks), the MODIFIED requirement and the new "A time written in AWS is not changed by ProxyService" requirement of `specs/ranking-group-archiving/spec.md`, and `tasks.md` group 5. TDD. Name or comment each test with its Scenario. Tick `tasks.md` boxes 5.1-5.4 in the commit of their task. Do NOT touch `docs/` (a separate step updates it). Commits: one per task 5.1, 5.2+5.3 together is fine if the sweep test needs the AWS side, otherwise one each; plus one `fix` commit for the review items below.

## 5.1 Column and updater
- `RankingGroup.archive_at_manual = Column(Boolean, nullable=False, default=False)` in `cms/db/rankinggroup.py`, next to `archive_at`, with a comment pointing to the spec. Also `server_default`? Match what `cmsInitDB` creates and what the updater creates: `schema_diff_test` compares them, so either both have a server default `false` or neither, and a NOT NULL column added to a table with rows needs the default in the ALTER. Use `server_default=expression.false()` in the model if that is what makes the two schemas equal; check how other Boolean NOT NULL columns of the fork are declared (e.g. `RankingGroup.hidden`) and follow them.
- `cmscontrib/updaters/fork_ranking_group_archive.py`: extend the SQL constant with a second catalog-guarded block that adds `archive_at_manual boolean NOT NULL DEFAULT false`, keeping the "no lock once the column exists" property and the docstring honest. Existing databases get false for every row.
- Tests: extend the existing `SetupDBTest` class for the archive column (it drops `archive_at` and re-applies the update): the new column is added with the same type/NOT NULL/default as `cmsInitDB` makes, existing rows get false, and a second run takes no lock. `schema_diff_test` must pass (it needs a pg_dump matching the server; it always fails locally against the podman PG: run it, and if it fails with the pg_dump version mismatch say so, do not "fix" it).

## 5.2 AWS sets the mark
- In `read_ranking_group_visibility` (and where `archive_at` is applied in `RankingGroupHandler._post_sync`): whenever the resulting `attrs["archive_at"]` differs from the stored value, set `attrs["archive_at_manual"] = True`. Compare with the stored value (not with "was typed"), so a stale-field save that keeps the stored value does not set it. The add-group form (AddRankingGroupHandler) has no archive_at field; leave it. Check `get_attrs()/set_attrs()` carry the new column.
- Tests for the three Scenarios (editing/clearing, Archive now/Unarchive, unchanged save).

## 5.3 Sweep skips marked groups
- `ProxyService._fill_archive_defaults`: exclude groups with `archive_at_manual` true from the query that selects groups to fill or postpone (and re-check the flag where the group is re-read/written, so a group marked between the SELECT and the write is not overwritten if that is cheap with the existing `no_autoflush` structure; otherwise leave a `shortcut:` comment). Unmarked groups behave exactly as before; all existing ProxyService archive tests must stay green.
- Tests (in `proxyservice_groups_test.py`): marked group with empty archive_at stays empty; marked group with a future archive_at earlier than the default is kept; unmarked group still postpones.

## 5.4 Confirm, wording, tooltip
- `ranking_group.html`: the «Archivar ahora» button gets `onclick="return confirm('...')"` only when `view.hidden_now or view.frozen_now`, text saying that the hidden or frozen ranking will be shown in full to the public. Tests for the two Scenarios (render the `core` block as the existing template tests do).
- `contest.py` refusal message: "Ranking group %r is archived: unarchive it before assigning a contest to it." (update the test that asserts on the text, if any).
- The Unarchive info tooltip (the `title` of the span) or button `title`: add "If ProxyService was down when you pressed it, press Regenerate for this group."

## Review fixes (one `fix` commit)
- S2: rewrap the docstring line over 79 characters in `read_ranking_group_visibility` (rankinggroup.py ~line 99); remove the extra blank line before `def archive` in `contest_test.py` (~171).
- N1: add one test with the real database (DatabaseMixin) that `RegenerateRankingHandler` looks the group up by `name`: a group named "olim" with description "x" archived, a request for group "x" must NOT be ignored and a request for "olim" must be. Keep the existing mocked tests, or convert them if simpler.
- N5: in `BaseHandler.render_params` move the `archived_group` computation after the `ranking_group_list` query, so `contest.ranking_group` is found in the session and costs no extra SELECT. Add a short comment saying why the order matters.

## Verify
Run, one process at a time, the touched test files: `server/admin/rankinggroup_test.py`, `server/admin/contest_test.py`, `service/proxyservice_groups_test.py` (alone), `cmscontrib/SetupDBTest.py` (gevent group: alone), cmstestsuite/unit_tests/schema_diff_test.py, and the admin render tests that import `base.py` (`pool_exhaustion_test.py`, `contestactivity_test.py`, `server_test.py`). pyflakes with `uvx -q pyflakes <touched files>`.
