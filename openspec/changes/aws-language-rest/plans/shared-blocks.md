# Shared sentence blocks of aws-language-rest

Blocks of text that two templates have word for word. One lane owns each block
(it writes the msgids and the Spanish); the other lane reuses the owner's msgids
and Spanish verbatim, after the owner is merged. Line numbers are those of the
templates at the start of the change; compare the texts, not the numbers.

The owner of all of them is **L2** (task, wave 1). L1 (contest) and L3
(dataset) are wave 2 and reuse what L2 merged.

## Token parameters (contest.html and task.html)

| Template | Lines | Texts |
|---|---|---|
| `task.html` (owner) | 159-222 | "Tokens parameters (", "documentation", "Token mode", "Disabled", "Finite", "Infinite", "Maximum number of tokens", "Minimum interval between tokens", "Initial number of tokens", "Token generation number", "Token generation period", "Maximum accumulated tokens" and the tooltips |
| `contest.html` (reuses) | 172-262 | the same |

"Disabled" is also a seed of aws.po (written in P).

## Submission limits (contest.html and task.html)

Only the labels are shared: "Limits", "Maximum number of submissions", "Maximum
number of user tests", "Minimum interval between submissions", "Minimum interval
between user tests". The tooltips differ ("...for this task..."), so each lane
writes its own.

| Template | Lines |
|---|---|
| `task.html` (owner) | 223-257 |
| `contest.html` (reuses the labels) | 263-302 |

## Small blocks (contest.html and task.html)

"Allowed programming languages" (task.html:138, contest.html:64), "Score decimal
places" and its tooltip (task.html:260-262, contest.html:101-103), "Name".
"Update", "Reset" and "Remove" are already in aws.po.

## Dataset settings (task.html and add_dataset.html)

| Block | `task.html` (owner) | `add_dataset.html` (reuses) | Texts |
|---|---|---|---|
| Limits | 355-370 | 52-67 | "Limits", "Time limit", "Total maximum time for each evaluation, in seconds.", "second(s)", "Memory limit", "Total maximum memory usage for each evaluation, in MiB (2^20 bytes).", "MiB" |
| Task type settings | 371-424 | 68-115 | "Task type settings (", "documentation", "Task type", "Please see the task type documentation." |
| Score type settings | 425-449 | 116-139 | "Score type settings (", "Score Type", "Please see the score type documentation.", "Score Parameters", and the tooltip that mentions subtask dependencies |

"Time limit" and "Memory limit" are seeds of aws.po (written in P).
The names and descriptions of the task and score type parameters come from
`cms/grading` and are not translated.
