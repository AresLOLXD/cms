## MODIFIED Requirements

### Requirement: archive_at gets a visible default
When a group has no `archive_at` that an operator wrote, has at least one contest, and 24 hours after the latest `stop` of the participation groups of its contests is in the future, ProxyService SHALL store that instant in the group's `archive_at`. When such a group has an `archive_at` that is still in the future and that computed instant is later, ProxyService SHALL move `archive_at` to it. It SHALL NOT move an `archive_at` earlier, SHALL NOT change an `archive_at` that is already in the past, and SHALL leave it empty in every other case. ProxyService SHALL NOT fill, move or clear the `archive_at` of a group whose `archive_at` an operator wrote in AWS (including by clearing it). Updating an existing database SHALL leave `archive_at` empty and not operator-written for every existing group.

#### Scenario: A group with a contest that has not ended gets the default
- **WHEN** a group has no `archive_at` and its contests end at T, with T plus 24 hours in the future
- **THEN** its `archive_at` becomes T plus 24 hours

#### Scenario: The last contest decides
- **WHEN** a group has two contests, and the second ends later than the first
- **THEN** its `archive_at` is the end of the second plus 24 hours

#### Scenario: A past archive_at is kept
- **WHEN** a group has an `archive_at` that is already in the past
- **THEN** ProxyService does not change it, even if a later default could be computed

#### Scenario: A contest added later postpones a future archive_at
- **WHEN** a group has an automatic `archive_at` in the future and a contest is added to it, or the times of a contest are moved, so that the computed default is later
- **THEN** ProxyService moves `archive_at` to the later default

#### Scenario: A later archive_at is not moved earlier
- **WHEN** a group has an `archive_at` in the future that is later than the computed default
- **THEN** ProxyService does not change it

#### Scenario: Contests that already ended do not archive a group by surprise
- **WHEN** a group has no `archive_at` and its last contest ended so long ago that the end plus 24 hours is in the past
- **THEN** its `archive_at` stays empty and the group is not archived

#### Scenario: A group with no contests
- **WHEN** a group has no `archive_at` and no contests
- **THEN** its `archive_at` stays empty

#### Scenario: Updating an existing database
- **WHEN** the database update adds the `archive_at` column and the operator-written marker to a database that has ranking groups
- **THEN** every existing group has an empty `archive_at` and is not marked as operator-written

#### Scenario: A time written by the operator is kept
- **WHEN** an operator writes an `archive_at` in the future in AWS, earlier than the computed default, and ProxyService runs a sweep
- **THEN** the `archive_at` is the one the operator wrote

#### Scenario: An archive_at cleared by the operator stays empty
- **WHEN** an operator empties the `archive_at` of a group in AWS and ProxyService runs a sweep while the computed default is in the future
- **THEN** the `archive_at` stays empty

## ADDED Requirements

### Requirement: AWS shows the archive state of a ranking group
The page of a ranking group in AWS SHALL show whether the group is archived now, and its `archive_at` in UTC with the local time next to it, or that none is set.

#### Scenario: An archived group
- **WHEN** the operator opens the page of a group whose `archive_at` is in the past
- **THEN** the page says that the group is archived, and shows its `archive_at`

#### Scenario: A group that will be archived
- **WHEN** the operator opens the page of a group whose `archive_at` is in the future
- **THEN** the page says that the group is not archived and shows when it will be

#### Scenario: A group with no archive_at
- **WHEN** the operator opens the page of a group with no `archive_at`
- **THEN** the page says that the group is never archived

### Requirement: The operator can move archive_at in AWS
The group page SHALL have an `archive_at` field in UTC, like the hide and freeze times. Saving the page with a changed value SHALL store it, and saving it empty SHALL clear it. A value that the page was not changed from SHALL NOT overwrite what somebody else saved since the page was shown. A malformed value SHALL save nothing and tell the operator.

#### Scenario: Setting archive_at
- **WHEN** the operator writes a valid UTC time in the field and saves
- **THEN** the group's `archive_at` is that time

#### Scenario: Clearing archive_at
- **WHEN** the operator empties the field and saves
- **THEN** the group's `archive_at` is empty

#### Scenario: A stale page does not undo a change
- **WHEN** the operator saves a page whose `archive_at` field still shows the value it was rendered with, and the stored value changed since
- **THEN** the stored value is kept

#### Scenario: A malformed time
- **WHEN** the operator writes a time that is not a valid UTC time and saves
- **THEN** nothing is saved and AWS shows a notification

### Requirement: A time written in AWS is not changed by ProxyService
Every save in AWS that changes the `archive_at` of a group (editing the field, clearing it, **Archive now** or **Unarchive**) SHALL mark the group's `archive_at` as written by the operator. A save that leaves `archive_at` as it was SHALL NOT change the mark.

#### Scenario: Editing marks it
- **WHEN** the operator changes or clears the `archive_at` field and saves
- **THEN** the group is marked as operator-written

#### Scenario: Archive now and Unarchive mark it
- **WHEN** the operator presses **Archive now** or **Unarchive**
- **THEN** the group is marked as operator-written

#### Scenario: Saving without touching archive_at
- **WHEN** the operator saves the page with the `archive_at` field unchanged
- **THEN** the mark is as it was

### Requirement: Archive now
The group page SHALL have an **Archive now** button, offered while the group is not archived, that sets `archive_at` to the current time. Pressing it on a group that is already archived SHALL save nothing and tell the operator.

#### Scenario: Archiving now
- **WHEN** the operator presses **Archive now** on a group that is not archived
- **THEN** the group's `archive_at` is the current time and the group is archived

#### Scenario: Archive now on a hidden or frozen group asks first
- **WHEN** the page of a group that is hidden or frozen now is rendered
- **THEN** the **Archive now** button asks the operator to confirm, saying that the hidden or frozen ranking will be shown in full

#### Scenario: Archive now on a visible group does not ask
- **WHEN** the page of a group that is neither hidden nor frozen is rendered
- **THEN** the **Archive now** button asks for no confirmation

#### Scenario: Archive now on an archived group
- **WHEN** the operator presses **Archive now** on a page that was stale and the group is already archived
- **THEN** nothing is saved and AWS shows a notification

### Requirement: Unarchive
The group page SHALL have an **Unarchive** button, offered while the group is archived, that sets `archive_at` to the current time plus 24 hours. Pressing it on a group that is not archived SHALL save nothing and tell the operator.

#### Scenario: Unarchive button
- **WHEN** the operator presses **Unarchive** on an archived group
- **THEN** the group's `archive_at` is 24 hours from now and the group is not archived

#### Scenario: Unarchive on a group that is not archived
- **WHEN** the operator presses **Unarchive** on a page that was stale and the group is not archived
- **THEN** nothing is saved, no regenerate is requested, and AWS shows a notification

### Requirement: Unarchiving publishes the corrections
Whenever a save takes a group from archived to not archived, by the **Unarchive** button or by editing `archive_at`, AWS SHALL ask ProxyService to regenerate that group once, after the save is committed. A save that leaves the group archived, or that leaves it not archived, SHALL NOT ask for a regenerate.

#### Scenario: Unarchive button regenerates
- **WHEN** the operator presses **Unarchive** on an archived group
- **THEN** a regenerate of the group is requested once

#### Scenario: Unarchiving by editing archive_at
- **WHEN** the operator moves the `archive_at` of an archived group to the future, or clears it, and saves
- **THEN** the group is not archived and a regenerate of the group is requested once

#### Scenario: Saving a group that stays as it is
- **WHEN** the operator saves a group that was not archived and stays not archived, or was archived and stays archived
- **THEN** no regenerate is requested

### Requirement: AWS refuses changes that an archived group would swallow
AWS SHALL refuse, saving nothing and showing a notification, to assign a contest to a ranking group that is archived, and to rename a ranking group that is archived. Saving a contest that stays in the archived group it is already in SHALL NOT be refused.

#### Scenario: Assigning a contest to an archived group
- **WHEN** the operator saves a contest with an archived group chosen, and the contest was not in that group
- **THEN** nothing is saved and AWS shows a notification that the group is archived and the contest was not assigned

#### Scenario: Editing a contest of an archived group
- **WHEN** the operator saves a contest that is already in an archived group, without changing the group
- **THEN** the contest is saved

#### Scenario: Renaming an archived group
- **WHEN** the operator saves an archived group with a different name
- **THEN** nothing is saved and AWS shows a notification that the group is archived and must be unarchived first

#### Scenario: Renaming a group that is not archived
- **WHEN** the operator saves a group that is not archived with a different name
- **THEN** the group is renamed as before

### Requirement: AWS warns that the public ranking will not show a change
Every AWS page of a contest whose ranking group is archived now SHALL show a banner that names the group, says that changes made there do not reach the public ranking until the group is unarchived, and links to the group page. A contest with no group, or whose group is not archived, SHALL show no banner.

#### Scenario: A contest of an archived group
- **WHEN** the operator opens any page of a contest whose group is archived
- **THEN** the banner is shown with the group name and a link to its page

#### Scenario: A contest of a group that is not archived
- **WHEN** the operator opens a page of a contest whose group is not archived
- **THEN** no banner is shown

#### Scenario: A contest with no group
- **WHEN** the operator opens a page of a contest that has no ranking group
- **THEN** no banner is shown

### Requirement: AWS says when a regenerate does nothing
When the operator asks AWS to regenerate the ranking of a group that is archived, the notification SHALL say that the group is archived and nothing was sent, instead of saying that the regeneration was requested. For any other ranking it SHALL say what it says today.

#### Scenario: Regenerate on an archived group
- **WHEN** the operator presses **Regenerate** for an archived group
- **THEN** the notification says that the group is archived and the ranking was not regenerated

#### Scenario: Regenerate on a group that is not archived
- **WHEN** the operator presses **Regenerate** for a group that is not archived, or for the root ranking
- **THEN** the notification says that the regeneration was requested, as before
