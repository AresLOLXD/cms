# Spec Delta

## Purpose
A ranking group can be archived: from a given instant on, ProxyService sends it nothing more, so its public standings stay fixed and it stops costing work. The instant gets a default: 24 hours after the end of the group's last contest.

## ADDED Requirements

### Requirement: A ranking group is archived from its archive_at instant
A ranking group SHALL have an optional `archive_at` instant (naive UTC). The group SHALL be archived at every instant `now` with `archive_at <= now`. A group whose `archive_at` is empty SHALL never be archived.

#### Scenario: Before archive_at
- **WHEN** a group has `archive_at` in the future
- **THEN** the group is not archived

#### Scenario: At and after archive_at
- **WHEN** the current time reaches `archive_at`
- **THEN** the group is archived, and stays archived while `archive_at` is not moved to the future

#### Scenario: No archive_at
- **WHEN** a group has no `archive_at`
- **THEN** the group is never archived

### Requirement: archive_at gets a visible default
When a group has no `archive_at`, has at least one contest, and 24 hours after the latest `stop` of the participation groups of its contests is in the future, ProxyService SHALL store that instant in the group's `archive_at`. It SHALL NOT overwrite an `archive_at` that is set, and SHALL leave it empty in every other case. Updating an existing database SHALL leave `archive_at` empty for every existing group.

#### Scenario: A group with a contest that has not ended gets the default
- **WHEN** a group has no `archive_at` and its contests end at T, with T plus 24 hours in the future
- **THEN** its `archive_at` becomes T plus 24 hours

#### Scenario: The last contest decides
- **WHEN** a group has two contests, and the second ends later than the first
- **THEN** its `archive_at` is the end of the second plus 24 hours

#### Scenario: A set archive_at is kept
- **WHEN** a group already has an `archive_at`
- **THEN** ProxyService does not change it

#### Scenario: Contests that already ended do not archive a group by surprise
- **WHEN** a group has no `archive_at` and its last contest ended so long ago that the end plus 24 hours is in the past
- **THEN** its `archive_at` stays empty and the group is not archived

#### Scenario: A group with no contests
- **WHEN** a group has no `archive_at` and no contests
- **THEN** its `archive_at` stays empty

#### Scenario: Updating an existing database
- **WHEN** the database update adds the `archive_at` column to a database that has ranking groups
- **THEN** every existing group has an empty `archive_at`

### Requirement: Groups not yet archived behave as before
Until a group is archived, ProxyService SHALL send its scores, tokens, contest data and visibility settings exactly as it does today, so same-day corrections still reach the public scoreboard.

#### Scenario: Correction during the grace period
- **WHEN** a submission of a contest in a group with `archive_at` in the future is re-scored
- **THEN** ProxyService sends the new score to the group's ranking namespace

### Requirement: ProxyService sends nothing to an archived group
For an archived group, ProxyService SHALL NOT send scores, tokens, contest data or visibility settings, neither from a score, token or dataset event nor from a sweep, an initialize or a reinitialize, except the one final visibility of the next requirement. A contest assigned to an archived group SHALL send nothing until the group is not archived any more.

#### Scenario: A score event for an archived group
- **WHEN** a submission of a contest in an archived group is scored
- **THEN** ProxyService enqueues no operation for it

#### Scenario: A token event for an archived group
- **WHEN** a submission of a contest in an archived group gets a token
- **THEN** ProxyService enqueues no operation for it

#### Scenario: A dataset update for an archived group
- **WHEN** the active dataset of a task of a contest in an archived group is updated
- **THEN** ProxyService enqueues no operation for it

#### Scenario: The sweep skips an archived group
- **WHEN** the sweeper looks for missing data and a group is archived
- **THEN** it enqueues nothing for that group, and still sends what the other groups miss

#### Scenario: Initialize and reinitialize leave an archived group alone
- **WHEN** ProxyService initializes or reinitializes the rankings
- **THEN** it sends nothing to an archived group except its final visibility, and does not reset its namespace

#### Scenario: A contest added to an archived group
- **WHEN** a contest is assigned to an archived group
- **THEN** ProxyService sends nothing for that contest

#### Scenario: Unarchiving resumes the sending
- **WHEN** the `archive_at` of an archived group is moved to the future or cleared
- **THEN** the group is not archived, and the next sweep sends its visibility and what it misses as for any other group

### Requirement: An archived group shows its final standings
When a group becomes archived, ProxyService SHALL send its visibility once with no hide window and no freeze window, so the group is shown and not frozen, and SHALL send it no visibility afterwards while it stays archived.

#### Scenario: A hidden group is archived
- **WHEN** a group that is hidden becomes archived
- **THEN** ProxyService sends one visibility for it with no hide and no freeze window

#### Scenario: A frozen group is archived
- **WHEN** a group that is frozen becomes archived
- **THEN** ProxyService sends one visibility for it with no hide and no freeze window

#### Scenario: The final visibility is sent once
- **WHEN** ProxyService runs several sweeps while the group stays archived
- **THEN** it sends the final visibility once and nothing more

#### Scenario: Unarchiving forgets the final visibility
- **WHEN** an archived group is unarchived and becomes archived again
- **THEN** ProxyService sends its final visibility again

### Requirement: The ranking keeps the final standings of an archived group
Archiving a group SHALL NOT remove or change the scores, tokens or contest data its ranking namespace already holds.

#### Scenario: Group archived while the ranking holds its data
- **WHEN** a group becomes archived and ProxyService then runs a sweep and a reinitialize
- **THEN** no reset, no delete and no put of scores, tokens or contest data reaches the group's namespace

### Requirement: Regenerate does nothing for an archived group
Regenerating the ranking of an archived group SHALL send no reset and no data, and SHALL log a WARNING that names the group and says that it is archived.

#### Scenario: Regenerate on an archived group
- **WHEN** an admin asks to regenerate the ranking of an archived group
- **THEN** ProxyService enqueues no operation for it and logs a WARNING that the group is archived

#### Scenario: Regenerate on a group that is not archived
- **WHEN** an admin asks to regenerate the ranking of a group that is not archived
- **THEN** ProxyService regenerates it as it does today
