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

### Requirement: Archive now
The group page SHALL have an **Archive now** button, offered while the group is not archived, that sets `archive_at` to the current time. Pressing it on a group that is already archived SHALL save nothing and tell the operator.

#### Scenario: Archiving now
- **WHEN** the operator presses **Archive now** on a group that is not archived
- **THEN** the group's `archive_at` is the current time and the group is archived

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
- **THEN** nothing is saved and AWS shows a notification that the group is archived

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
