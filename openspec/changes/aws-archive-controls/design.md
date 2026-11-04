## Context

The core change (`archive-ranking-groups`, archived) added `RankingGroup.archive_at`, `is_archived_at(now)` and the ProxyService behavior. This change is the AWS half, in `cms/server/admin`:

- `handlers/rankinggroup.py` already has the pattern to reuse: `WINDOW_FIELDS` (name and label of the time fields), `read_ranking_group_visibility` (the `<name>_shown` stale-form rule and the `visibility_action` buttons, evaluated under `SELECT ... FOR UPDATE`), `visibility_view` (fields, summary, next change) and `RankingGroupHandler._post_sync` (commit, then `reinitialize` through `schedule_rpc`).
- `handlers/contest.py` sets the group of a contest through `attrs["ranking_group"]` in `ContestHandler._post_sync`; it is the only AWS path that assigns a contest to a group (adding a contest sets none).
- `base.html` is the page shell of every AWS page; `render_params` knows `self.contest`.
- `RegenerateRankingHandler` schedules `proxy_service.regenerate_ranking`; ProxyService already ignores an archived group and logs a WARNING.

## Goals / Non-Goals

**Goals:**
- Operate the grace period from AWS: see, move, archive now, unarchive.
- A late correction is published by unarchiving, with no separate step to remember.
- No silent loss: refuse the two edits that an archived group would swallow, and warn on every page where a change will not be published.

**Non-Goals:**
- The static snapshot of an archived group (stays open in #51).
- A state column in the group list.
- Refusing to move a contest out of an archived group (its data stays in the namespace; known limit of the core change).
- Any change to what ProxyService sends.

## Decisions

1. **`archive_at` is one more time field of the visibility form.** It joins the fields rendered and read like `hide_at`: `<name>_shown`, UTC text, empty clears it. It is kept apart from `WINDOW_FIELDS` because it has no window partner and no `check_window`; the reading loop is shared by giving it a small helper. Alternative: a separate form and endpoint for archiving; rejected because the stale-page rule and the row lock would be written twice.
2. **Buttons are `visibility_action` values** (`archive_now`, `unarchive`), validated in the same function against the locked, current state ("The group is already archived." / "The group is not archived."). Alternative: separate POST endpoints; rejected for the same reason.
3. **Regenerate follows the transition, not the button.** `_post_sync` computes `was_archived` from the group before the edit and `now_archived` after it, both at the same `now`; when `was_archived and not now_archived` and the commit succeeded, it schedules `regenerate_ranking(group=name)` after the existing `reinitialize`. Both the **Unarchive** button and a hand edit of `archive_at` therefore publish the corrections, and nothing else does. Alternative: regenerate only on the button; rejected because moving the date by hand would leave the public ranking stale, since the sweep sends only what the ranking is missing, not what changed. Regenerate empties the namespace and sends it again, so it is the heaviest operation of the page; the confirmation text of the button says so.
4. **Unarchive sets `archive_at = now + ARCHIVE_GRACE`.** The group archives itself again; the operator can move the time on the same page. `ARCHIVE_GRACE` moves to `cms/db/rankinggroup.py` (importing `cms.service.ProxyService` from the web server would pull in a whole service) and ProxyService imports it, so `ProxyService.ARCHIVE_GRACE` keeps working.
5. **The sweep respects what the operator wrote.** The reviewer showed that, while the default (last contest end + 24 h) is still in the future, the sweep refills an `archive_at` the operator cleared and postpones one the operator set earlier. Decision (user): add `RankingGroup.archive_at_manual` (Boolean, NOT NULL, default false). Every AWS save that changes `archive_at` (field edit, clearing, **Archive now**, **Unarchive**) sets it to true; `ProxyService._fill_archive_defaults` skips groups where it is true and behaves as before for the rest. Existing databases get false from a `fork_*.py` updater run by `cmsSetupDB` (idempotent, covered by `schema_diff_test` and `SetupDBTest`, same shape as `fork_ranking_group_archive.py`). There is no way back to automatic in this change: clearing the field means never. Alternative rejected: only documenting the limit (the operator would have to know that the sweep corrects what they write).
6. **Refusals check the stored state.** A rename is refused when the group is archived at the moment of the request (state read under the row lock, before the edit is applied), so a request that both unarchives and renames is refused and the operator does it in two saves. A contest is refused only when the chosen group is archived and differs from the contest's current group, so editing other fields of a contest already in an archived group still works. Both refusals use the existing "Invalid field(s)" notification with a message naming the group.
7. **The banner is one block in `base.html`**, fed by `render_params`: when `self.contest` has a ranking group archived at the page's `timestamp`, it passes the group; the template renders it above the page content with a link to the group page. No handler is touched. The state is read on every page render, so it costs the contest's group row, already a many-to-one loaded with the contest.
8. **Regenerate from AWS on an archived group** does not call the RPC: the handler reads the group by name and, if archived, shows the notification only. ProxyService keeps its own check as a guard for other callers.
9. **Archive now on a hidden or frozen group asks first.** Archiving sends one final visibility with no windows, so a hidden or frozen ranking becomes public in full (this is the intended core behavior). Because one click does that and cannot be undone, the **Archive now** button carries a `confirm()` only while the group is hidden or frozen now; the visible case stays one click.
10. **Text language.** Labels follow the page they are on: the group page mixes English labels with Spanish visibility buttons («Ocultar ahora»); the new buttons are «Archivar ahora» and «Desarchivar», and the explanatory text is English like the rest of the table. The banner is English like the rest of `base.html`. The refusal for a contest says "unarchive it before assigning a contest to it".

## Risks / Trade-offs

- A regenerate resends the whole group and can take minutes for a large one. It is requested at most once per save and only on the transition.
- A request that unarchives and renames is refused; the operator needs two saves. Accepted to keep the rule simple.
- Moving a contest out of an archived group is still allowed and leaves its data in the public ranking (documented limit).
- The banner depends on `self.contest`; pages with no contest (global lists, the group pages) show none, by design: the group page shows the state itself.
- If ProxyService is down when a save unarchives a group, the regenerate RPC is only logged and lost; the **Unarchive** tooltip says to press Regenerate in that case (score changes are resent when ProxyService restarts, removals are not).
- The sweep writes `archive_at` with a conditional UPDATE (`WHERE archive_at_manual IS false`), without a row lock (FOR UPDATE would conflict with the `contests.ranking_group_id` foreign key locks and block AWS saves). A group the operator marked meanwhile is not overwritten: under READ COMMITTED the WHERE is re-checked after the AWS commit.
- Two operators: the row lock plus `<name>_shown` make the second save see the new state; the "already archived / not archived" errors cover the button races.
