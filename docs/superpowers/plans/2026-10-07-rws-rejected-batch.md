# RWS Rejected Batch Recovery Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** When RWS refuses a merged batch, ProxyService loses only the
entities RWS refuses on their own. A score refused because RWS lacks its
user or task is sent again within one sweep, without Regenerate.

**Architecture:**
- `ProxyExecutor._execute_sync` splits a refused merged PUT into
  one-entity PUTs. It records the refused entities, and `execute` hands
  them on the event loop to a callback that `ProxyService` passes in.
- `ProxyService` takes the matching submissions out of its "sent" sets and
  marks the refusing namespace for repair.
- The next sweep sends that namespace's contest data and then the
  un-marked scores.

**Tech Stack:** Python 3.12, asyncio plus a worker thread
(`run_in_executor`), `requests` (mocked in tests), unittest/pytest.

**Spec:** `docs/superpowers/specs/2026-10-07-rws-rejected-batch-design.md`

## Global Constraints

- **Where to work.** Only in
  `/var/home/areslolxd/Documentos/cms/.worktrees/rws-fix` (branch
  `fix/rws-rejected-batch`, from origin/beta `c38c82df`, plus the spike
  tests `733c83b2`). Do not push, merge, or use `git stash`.
- **Style.** English code and comments, PEP 8, PEP 484, and the project
  docstring format (`arg (type): ...`, `return (type): ...`).
- **Lint.** `uvx -q pyflakes` on the touched files must show nothing.
  `uvx -q pycodestyle --max-line-length=79` must show nothing new.
- **venv.** `uv venv -q --python 3.12 .venv && uv pip install -q --python .venv/bin/python -c constraints.txt -e ".[devel]"`.
  Run tests with
  `CMS_CONFIG=/home/areslolxd/.claude/jobs/4c257a1a/tmp/cms-test.toml .venv/bin/python -m pytest -p no:cacheprovider <files>`.
  This uses the Postgres container `cms-test-pg` on localhost:55432. Run one
  pytest process at a time.
- **Test groups.**
  - `cmstestsuite/unit_tests/cmsranking/` is in the gevent group: its own
    process.
  - The ProxyService tests in `cmstestsuite/unit_tests/service/` are
    asyncio: `ProxyServiceTest.py`, `ProxyService_test.py`,
    `proxyexecutor_test.py`, `proxyservice_groups_test.py` and
    `proxyservice_rws_rejection_test.py`.
  - Never run a bare `pytest` over the whole suite.
- **Commits.** Conventional Commits. End with a `Refs #13` line, a blank
  line, then `Co-Authored-By: <your model name> <noreply@anthropic.com>`.
- **Exact values.**
  - Data types that can be split: `CONTEST_TYPE`, `TASK_TYPE`,
    `TEAM_TYPE`, `USER_TYPE`, `SUBMISSION_TYPE`, `SUBCHANGE_TYPE`. Never
    `RESET_TYPE` or `VISIBILITY_TYPE`.
  - The WARNING names at most `REFUSED_IDS_SHOWN = 10` ids.
  - Submission key: `"%d" % submission.id`.
  - Subchange data: `data["submission"]`. A score subchange key ends in
    `"s"`, a token subchange key ends in `"t"`.
- **Exact log text** (WARNING, one per refused type per round).
  - Submissions and subchanges: `"Ranking %s refused %d of %d %s of group %s (%s); they will be sent again after the contest data of the group."`
  - Other types: `"Ranking %s refused %d of %d %s of group %s (%s). They will not be sent again: use Regenerate for this group in AWS (Ranking groups) to send its data again."`
  - The `%s` in parentheses is the comma-separated list of at most
    `REFUSED_IDS_SHOWN` ids, followed by `", ..."` if there are more.

## Review Focus

1. **A refused batch of exactly one entity.** It is not split (no extra
   request), and it is reported as refused. Test:
   `test_a_single_refused_entity_is_not_split` (Task 1).
2. **An UNSENT answer in the middle of a split.** The entries not yet
   sent alone go back to the queue and the group stalls, as for an UNSENT
   batch. Test:
   `test_an_unsent_answer_while_splitting_puts_the_rest_back` (Task 1).
3. **An entry with several entities.** For example the initial users dict:
   the entry counts as sent only if all of its entities went in. Test:
   `test_an_entry_is_sent_only_if_all_its_entities_went_in` (Task 1).
4. **No refusal at all.** Exactly the same requests as before: one PUT
   per type and namespace. Test:
   `test_no_refusal_sends_one_put_per_type` (Task 1).
5. **A submission scored again between the refusal and the hand-off.** The
   un-mark is harmless: at worst one extra resend. Covered by the
   idempotent sweep test `test_the_sweep_repairs_a_group_once` (Task 2).

---

### Task 1: Split a refused batch in ProxyExecutor and report what was refused

**Files:**
- Modify: `cms/service/ProxyService.py`:
  - `ProxyExecutor.__init__` (around line 347): add the parameter
    `on_refused`;
  - `ProxyExecutor.execute` (around line 424): call it;
  - `ProxyExecutor._execute_sync` (around lines 514-617): the data loop;
  - `_send` (around lines 652-724): new keyword `quiet=False`.
- Test: `cmstestsuite/unit_tests/service/proxyexecutor_test.py`. Read it
  first and follow its patterns for patching `requests.put` and building
  entries.

**Interfaces:**
- Produces:
  - `ProxyExecutor.__init__(self, ranking: str, on_refused: Callable[[list[RefusedEntity]], None] | None = None)`;
  - a module-level `RefusedEntity = typing.NamedTuple("RefusedEntity", [("group", str | None), ("type_", int), ("key", str), ("data", dict)])`.
    Use a `class RefusedEntity(typing.NamedTuple)` with those four fields,
    plus a one-line docstring;
  - `ProxyExecutor.REFUSED_IDS_SHOWN = 10`;
  - `_execute_sync` keeps returning the unsent entries (list). It also
    stores the refused entities of the round in `self._refused_in_round`
    (a list). `execute` reads it after `run_in_executor` returns, clears
    it, and calls `self._on_refused(refused)` if the list is non-empty
    and the callback is set.

- [ ] **Step 1: Write the failing tests** in `proxyexecutor_test.py`, using
  the existing helpers or a fake `requests.put` that refuses some keys.
  The fake records each PUT's resource and decoded body, and answers 400
  if the body holds a key in a "refused" set, else 200.
  - `test_a_refused_batch_is_split_and_only_the_refused_entities_are_dropped`:
    - submissions `{"1": ok, "2": refused, "3": ok}` in three entries, one
      round;
    - expect: one merged PUT (400), then three single PUTs;
    - `_execute_sync` returns `[]` (nothing to resend);
    - `_refused_in_round == [RefusedEntity(None, SUBMISSION_TYPE, "2", {...})]`;
    - one WARNING containing `"refused 1 of 3 submissions"` and
      `"they will be sent again after the contest data"`.
  - `test_a_single_refused_entity_is_not_split`: one submission, refused.
    Expect exactly one PUT, the entity in `_refused_in_round`, and the
    WARNING.
  - `test_an_unsent_answer_while_splitting_puts_the_rest_back`: three
    submissions in three entries; the merged PUT gets 400, the first single
    PUT succeeds, the second gets 503.
    - The returned unsent entries are the second and the third, in order.
    - The group is stalled: a later type in the same group is not sent.
  - `test_an_entry_is_sent_only_if_all_its_entities_went_in`: one entry
    with users `{"a": ok, "b": refused}` and another entry with
    `{"c": ok}`.
    - After the split, nothing is returned for resend.
    - `_refused_in_round` holds `"b"`.
    - The WARNING uses the "use Regenerate" form for users.
  - `test_reset_and_visibility_are_never_split`: a refused visibility and
    a refused reset keep their current behaviour, with no single PUTs.
    Mirror the existing tests of those paths.
  - `test_no_refusal_sends_one_put_per_type`: users plus submissions plus
    subchanges in one round, all accepted. Expect exactly 3 PUTs and
    `_refused_in_round == []`.
  - `test_execute_hands_refused_entities_to_the_callback` (async): build
    the executor with `on_refused=MagicMock()`, run `execute` on a refused
    batch, and assert the callback got the list once, on the loop.
    - Assert also: with no refusal, the callback is not called.
- [ ] **Step 2: Run the tests; they fail** (unknown parameter or
  attribute; no split).
- [ ] **Step 3: Implement.**
  - **`_send(self, group, type_, data, quiet: bool = False)`.** When
    `quiet` is True and the ranking refuses, it returns REJECTED without
    logging the "rejected … use Regenerate" or "cannot be encoded"
    WARNING. The `_check_status` warning still logs: that one is per
    request and stays as it is.
  - **In the data loop of `_execute_sync`,** replace
    `outcome = self._send(group, type_, data)` with a call to a new helper:

```python
                if group not in stalled:
                    outcome, unsent_entries = self._send_type(
                        group, type_, type_entries, data)
                    if outcome is not SendOutcome.UNSENT:
                        continue
                    stalled.add(group)
                    unsent.update(id(entry) for entry in unsent_entries)
                    continue
                unsent.update(id(entry) for entry in type_entries)
```

  - **The helper:**

```python
    def _send_type(
        self, group: str | None, type_: int,
        type_entries: list[QueueEntry[ProxyOperation]], data: dict,
    ) -> tuple[SendOutcome, list[QueueEntry[ProxyOperation]]]:
        """Send the merged data of one type, splitting it if refused.

        Runs inside loop.run_in_executor.

        A ranking refuses a whole list if one of its entities is
        refused (RWS checks them all before it stores any), so a
        refused list of more than one entity is sent again one entity
        at a time, and only what is refused on its own is dropped. The
        refused entities are added to self._refused_in_round.

        group (str|None): the namespace.
        type_ (int): a data type (not RESET_TYPE nor VISIBILITY_TYPE).
        type_entries ([QueueEntry]): the entries the data comes from.
        data (dict): their merged data, by id.

        return ((SendOutcome, [QueueEntry])): SENT if everything was
            taken or refused (nothing to send again), UNSENT if the
            ranking could not be reached, with the entries to send
            again in that case (else an empty list).

        """
        outcome = self._send(group, type_, data, quiet=True)
        if outcome is SendOutcome.SENT:
            return SendOutcome.SENT, []
        if outcome is SendOutcome.UNSENT:
            return SendOutcome.UNSENT, list(type_entries)
        refused: dict[str, dict] = dict()
        if len(data) == 1:
            refused = dict(data)
        else:
            done: set[str] = set()
            for key, value in data.items():
                single = self._send(group, type_, {key: value}, quiet=True)
                if single is SendOutcome.UNSENT:
                    # What was not sent alone yet goes back to the queue.
                    left = [entry for entry in type_entries
                            if not set(entry.item.data) <= done]
                    self._report_refused(group, type_, refused, len(data))
                    return SendOutcome.UNSENT, left
                if single is SendOutcome.REJECTED:
                    refused[key] = value
                done.add(key)
        self._report_refused(group, type_, refused, len(data))
        return SendOutcome.SENT, []
```

  - **`_report_refused(group, type_, refused, total)`:**
    - appends a `RefusedEntity(group, type_, key, value)` for each refused
      key to `self._refused_in_round`;
    - logs the one WARNING from the Global Constraints. Use the plural
      resource name from `RESOURCE_PATHS[type_]`, the sorted ids
      truncated to `REFUSED_IDS_SHOWN`, and the submissions/subchanges
      form or the Regenerate form;
    - does nothing if `refused` is empty.
  - **Encoding.** If `_send` returned REJECTED because of an
    `UnencodableError`, the split sends each entity alone too, and the ones
    that cannot be encoded are refused alone. Keep the "cannot be encoded"
    advice: log it once in `_report_refused` when any refusal came from
    encoding. Track this with a flag set by `_send` (for example
    `self._last_refusal_unencodable`), or simply keep calling `_send` with
    `quiet=False` for the single PUT of an unencodable entity. Pick the
    simpler option, and test that the existing encoding test still passes.
  - **`execute`.** Initialise `self._refused_in_round = []` before
    `run_in_executor`. After it returns, take the list, reset it, and call
    the callback if any.
  - **`__init__`.** Store `on_refused` and initialise
    `self._refused_in_round: list[RefusedEntity] = []`.
  - **The class docstring.** Update "what it refuses is dropped" to: what
    it refuses is split, and only the entities it refuses on their own are
    dropped and reported.
- [ ] **Step 4: Run all the asyncio ProxyService test files; they pass.**
  If an existing test asserts the old "rejected … It will not be sent
  again" text for a refused submissions batch, update it to the new text.
  Name each test you change in the report. `proxyservice_rws_rejection_test.py`
  still fails at this point (Task 2 flips it). Say so; do not change it
  here.
- [ ] **Step 5: Lint and commit:**
  `fix(proxy): split a batch the ranking refuses and drop only what it refuses on its own`.

---

### Task 2: Repair a namespace that refused scores (ProxyService)

**Files:**
- Modify: `cms/service/ProxyService.py`:
  - `ProxyService.__init__` (around line 743): the executor gets
    `on_refused=self._on_refused`; a new set plus a lock;
  - new `_on_refused`;
  - `_missing_operations_sync` (around line 927).
- Modify: `cmstestsuite/unit_tests/service/proxyservice_rws_rejection_test.py`.
  Flip it to the fixed behaviour.
- Test: the same file, plus `ProxyService_test.py` or
  `proxyservice_groups_test.py` for the new service-level tests. Follow
  whichever file already builds a ProxyService with a DB.

**Interfaces:**
- Consumes: Task 1's `RefusedEntity` and `on_refused`.
- Produces:
  - `ProxyService._groups_to_repair: set[str | None]`;
  - `ProxyService._groups_to_repair_lock: threading.Lock`;
  - `ProxyService._on_refused(self, refused: list[RefusedEntity]) -> None`.

- [ ] **Step 1: Flip the spike test.** `proxyservice_rws_rejection_test.py`
  pins today's loss.
  - Read it, then change its expectations to the spec's Testing section:
    - Alice's valid score is in RWS after the round where Bob's is
      refused;
    - after one sweep (`_missing_operations`) and the rounds it triggers,
      Bob's user and Bob's score are in RWS;
    - no Regenerate.
  - Keep its use of the real RWS stores. Rename the tests so they describe
    the new behaviour, and update the module docstring.
  - Add a test: after the repair, `_groups_to_repair` is empty and a
    second sweep enqueues no contest data for that group.
- [ ] **Step 2: Add the service tests:**
  - `test_refused_scores_leave_the_sent_sets`. Call `_on_refused` with:
    - a submission `"7"`;
    - a score subchange with `data={"submission": "8", ...}` and a key
      ending in `"s"`;
    - a token subchange with `{"submission": "9"}` and a key ending in
      `"t"`.

    Expect 7 and 8 out of `scores_sent_to_rankings`, 9 out of
    `tokens_sent_to_rankings`, and the group in `_groups_to_repair`.
  - `test_refused_contest_data_only_marks_the_group`: a refused user marks
    the group and leaves the sent sets unchanged.
  - `test_the_sweep_repairs_a_group_once`:
    - with a group in `_groups_to_repair`, `_missing_operations_sync`
      calls `_enqueue_contest_data` once for each contest of that group,
      then `_enqueue_submissions(only_missing=True)`, and clears the
      group;
    - a second sweep does not call `_enqueue_contest_data` for it, unless
      the contest is broken, which is the existing rule.
- [ ] **Step 3: Run the tests; they fail.**
- [ ] **Step 4: Implement.**
  - **`__init__`.** Add `self._groups_to_repair: set[str | None] = set()`
    and `self._groups_to_repair_lock = threading.Lock()`. Add a comment:
    the loop adds to it in `_on_refused`, and the sweeper thread takes it
    in `_missing_operations_sync`. Pass `on_refused=self._on_refused` to
    each `ProxyExecutor(...)`.
  - **`_on_refused`:**

```python
    def _on_refused(self, refused: list[RefusedEntity]) -> None:
        """Make what a ranking refused be sent again after a repair.

        Runs on the event loop (ProxyExecutor.execute calls it). A
        ranking refuses a submission whose user or task it does not
        know, and a subchange whose submission it does not know: such
        scores and tokens are taken out of the sets of what was sent,
        and their namespace is marked so that the next sweep sends its
        contest data and then them (see _missing_operations_sync).

        refused ([RefusedEntity]): the entities refused in a round.

        """
        groups: set[str | None] = set()
        for entity in refused:
            groups.add(entity.group)
            if entity.type_ == ProxyExecutor.SUBMISSION_TYPE:
                self.scores_sent_to_rankings.discard(int(entity.key))
            elif entity.type_ == ProxyExecutor.SUBCHANGE_TYPE:
                submission_id = int(entity.data["submission"])
                if entity.key.endswith("t"):
                    self.tokens_sent_to_rankings.discard(submission_id)
                else:
                    self.scores_sent_to_rankings.discard(submission_id)
        with self._groups_to_repair_lock:
            self._groups_to_repair |= groups
```

    Check the subchange key format against `operations_for_score` and
    `operations_for_token` (around lines 1115 and 1150). Adjust the
    suffix test if the token key differs, and pin it in the test.
  - **`_missing_operations_sync`.** At the start, take the groups:

```python
        with self._groups_to_repair_lock:
            to_repair = self._groups_to_repair
            self._groups_to_repair = set()
```

    In the loop over `self._contests_to_send(session)`, before the broken
    contest check, if `self._group_of(contest) in to_repair` and the
    contest is not broken, call
    `counter += self._enqueue_contest_data(contest)`. Keep
    `only_missing=True` for its submissions; the un-marked ones are now
    missing.

    Extend the docstring with one sentence: namespaces that refused
    something get their contest data again, so a user or task the ranking
    lacks arrives before the scores sent again after it.
  - Check that `_group_of` works for both the root (`None`, single-ranking
    mode) and group mode, and that `to_repair` uses the same values the
    executor reports in `RefusedEntity.group`.
- [ ] **Step 5: Run every asyncio ProxyService test file and
  `cmstestsuite/unit_tests/cmsranking/` (gevent, own process); all pass.**
- [ ] **Step 6: Lint and commit:**
  `fix(proxy): send refused scores again after the contest data of their group`.

---

### Task 3: Runbook and multi-contest docs

**Files:**
- Modify: `docs/contest-day.md`, the table row
  `ProxyService logs "rejected … It will not be sent again"` (around line
  201).
- Modify: `docs/multi-contest.md`, its "ProxyService warnings that need
  an operator" section: the entry for that warning.
- Modify: the matching entries in `docs/locale/es/LC_MESSAGES/contest-day.po`
  and `multi-contest.po`.

- [ ] **Step 1: Update the English text.**
  - **contest-day.md:** the table now names two warnings.
    - "refused … they will be sent again after the contest data": no
      action needed; the scores come back within one sweep, about 6
      minutes. If the warning repeats for the same submissions, fix the
      cause, then Regenerate.
    - "refused … use Regenerate" (contests, tasks, teams, users): fix the
      cause, then Regenerate.
  - **multi-contest.md:** explain the split in one paragraph. A ranking
    that refuses a batch now gets it again one entity at a time, so only
    the refused entities are dropped. Scores and tokens refused for a
    missing user or task are sent again after the group's contest data.
- [ ] **Step 2: Update the Spanish .po entries.** The msgid must match the
  new English text exactly. The msgstr must be natural Spanish with
  correct accents and the GLOSSARY.md terms. Log texts stay in English.
  No fuzzy flags.
- [ ] **Step 3: Run the docs CI steps on a copy outside the worktree**
  (from `.github/workflows/docs.yml`):
  - `sphinx-build -W` for en and es;
  - `pytest docs/tests`;
  - the gettext build plus `check_translations.py`.
- [ ] **Step 4: Commit:**
  `docs: say that refused scores come back after the contest data`.
