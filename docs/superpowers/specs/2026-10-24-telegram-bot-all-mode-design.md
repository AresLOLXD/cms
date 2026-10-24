# Telegram bot for every contest, `CMS_CONTEST_ID=ALL` (issue #43) — Design

**Date:** 2026-10-24
**Status:** Decisions taken by a read-only specialist panel (python-pro,
architect-reviewer) and reconciled by the coordinator; approved by the owner
as a consolidated design on 2026-10-24.

## Intent

Production runs with `CMS_CONTEST_ID=ALL`, and `docker/generate_config.py`
does not start the Telegram bot in that mode, so production has no bot even
though it is configured. `cmscontrib/TelegramBot.py` already accepts
`-c ALL` (`contest_id = None`) but that mode is incomplete.

## Facts verified in the code (the issue text is partly stale)

- The N+1 of #11 S7 is already fixed: `_read_questions_and_announcements`
  eager-loads user and participation with `joinedload`, and
  `cmstestsuite/unit_tests/cmscontrib/TelegramBotTest.py` counts SELECTs.
  What is left is the unbounded history.
- `Question` has no `updated_at`; answers arrive from AWS and from the bot's
  own buttons (answer, ignore, "Remove answer"), and `reply_timestamp`
  becomes None on Ignore and Remove, so no column says "changed since".

## Goals

1. The bot starts when `CMS_CONTEST_ID=ALL`.
2. Each question message shows its contest and a hashtag.
3. `/announcement` works in ALL mode.
4. The first start does not flood the chat with past questions.
5. The polling query is bounded.
6. A single bad message does not crash-loop the bot.

## Non-goals (decided)

A contest-to-chat map; an inline keyboard for announcements; an `updated_at`
column or a "changed since" cursor; push events or `LISTEN/NOTIFY`;
echoing AWS-created announcements in ALL mode (it would replay every
contest's history); a default contest when the name is omitted; changing the
1 s poll interval; `to_thread`.

## Decisions

### 1. One chat
Keep `config.telegram_bot.chat_id` and the `CMS_TELEGRAM_CHAT_ID` /
`CMS_TELEGRAM_BOT_TOKEN` environment variables; no new config shape. The
contest hashtag lets the chat be filtered. In `docker/generate_config.py`
(~lines 257-262 and 324-330) remove the ALL warning branch and emit the same
`cmsTelegramBot 0 -c ALL` line as for a single contest, so the branch
collapses to `if telegram_configured`. Update `docs/multi-contest.md`
(Limitations, about line 632), the `HELP_MESSAGE` line "announcements are not
supported in multi-contest mode", and any generate_config test asserting the
warning.

### 2. Contest in the message
ALL mode only: add `contest_name` to the question dict (eager-load
`Participation.contest` or select `Contest.name`, so the number of queries
stays constant). In single-contest mode do **not** add the key: a new key
makes `_store` see a change and call `edit_message_text` with identical
text on every old message (Telegram answers "message is not modified").
Header: `#todo #<contest>` with `"#" + re.sub(r"\W", "_", name)` (hashtags
do not allow `-`), plus a line `Contest: <b>name</b>`. Use the name, not the
description.

### 3. `/announcement` in ALL mode
Syntax: `/announcement <contest_name> Subject` on the first line, body after.
`Contest.name` is a unique codename without spaces, the same token shown in
question messages. Register the command also when `contest_id is None`;
single-contest syntax is unchanged. "Running" means the contest has a
`Group` with `start <= now <= stop` (UTC, `make_datetime()`). A missing,
unknown or not-running name gets a reply listing the running names. In ALL
mode `db_loop` does not echo announcements; `send_announcement` replies
"Announcement sent to #name" after the commit. Pure helpers, easy to test:
`running_contest_names(session, now)` and
`parse_announcement(text, running_names)`.

### 4. First-start cutoff and bounded query
A persisted file `telegram/cutoff` (in the bot's state directory) holding a
UTC timestamp.
- File missing and the state directory has no sent questions: write `now`.
- File missing and the state is not empty (a legacy single-contest
  install): use the epoch, do not write the file (old behaviour).
- A question not yet in state is posted only if
  `question_timestamp >= cutoff`; the query itself is bounded by
  `WHERE question_timestamp >= cutoff`, so the working set is "questions
  since the bot first started" and edits made from AWS or the bot keep
  working. Wiping `telegram/` wipes the cutoff too: the bot restarts from
  "now" and does not resend history; say so in `docs/docker-deployment.md`
  (about line 487). Check that `data_dir/telegram` is on a persistent
  volume in the compose files and say so in the report.

### 5. Robustness of `db_loop`
It has no `try/except`: any DB or Telegram error ends `run()` and
supervisord restarts it. `_store` compares every column of `get_attrs()`
(including `admin_id`); when an admin answers in AWS `has_changed` is true
but the rendered text may be identical, `edit_message_text` raises
BadRequest "message is not modified", and that repeats on every restart (a
poison pill; this is an inference from the code, so write a test that
reproduces it before fixing it, and if it does not reproduce say so).
- Wrap each item in `try/except`; treat "message is not modified" as
  success; log the rest and continue.
- On Telegram `RetryAfter`, sleep `retry_after` and retry the same item
  (do not skip it, order matters). The end-of-contest burst will hit the
  ~20 messages per minute group limit.
- A crash between `send_message` and writing the state file may re-post one
  question after a restart: accepted.
- Never run a per-contest bot alongside the ALL bot (document).

## Tests (no Telegram needed)

Extend `cmstestsuite/unit_tests/cmscontrib/TelegramBotTest.py`
(`TelegramBot.__new__` plus `DatabaseMixin`; `bot.application = MagicMock()`,
`bot.send_message = AsyncMock`):
- read: with ALL every row has `contest_name`; with a single contest the key
  is absent; the SELECT count stays constant.
- cutoff: rows before it are not returned; missing file with empty state
  creates it; missing file with non-empty state gives the epoch.
- helpers: `running_contest_names` with three groups (past, running,
  future); `parse_announcement` with missing, unknown and not-running names.
- rendering: the text contains `#todo` and the contest hashtag; the
  single-mode text is byte-identical to today's.
- robustness: "not modified" does not stop the loop; `RetryAfter` retries
  the same item.
- `docker/test_generate_config.py`: ALL plus telegram configured gives a
  `cmstelegrambot` program with `-c ALL`.

## Docs

`docs/multi-contest.md`, `docs/docker-deployment.md`, the `HELP_MESSAGE`,
and the Spanish `.po` entries by hand.

## Tasks

1. Query/cutoff/contest name + tests.
2. Announcement helpers and command + tests.
3. Robustness of the loop + tests.
4. generate_config + docs; docs check.
