# Spec Delta

## ADDED Requirements

### Requirement: The ranking group list shows the archive state
The list of ranking groups in AWS SHALL have an Archive column that says, for each group, whether it is archived now and since when, or when it will be archived, or that it is never archived. The text SHALL be computed from `archive_at` when the page is built.

#### Scenario: An archived group in the list
- **WHEN** the operator opens the list and a group has `archive_at` in the past
- **THEN** its row says that the group is archived and since when

#### Scenario: A group that will be archived in the list
- **WHEN** the operator opens the list and a group has `archive_at` in the future
- **THEN** its row says when the group will be archived

#### Scenario: A group with no archive_at in the list
- **WHEN** the operator opens the list and a group has no `archive_at`
- **THEN** its row says that the group is never archived

## MODIFIED Requirements

### Requirement: AWS refuses changes that an archived group would swallow
AWS SHALL refuse, saving nothing and showing a notification, to assign a contest to a ranking group that is archived, to move a contest out of a ranking group that is archived (to another group or to none), and to rename a ranking group that is archived. Saving a contest that stays in the archived group it is already in SHALL NOT be refused.

#### Scenario: Assigning a contest to an archived group
- **WHEN** the operator saves a contest with an archived group chosen, and the contest was not in that group
- **THEN** nothing is saved and AWS shows a notification that the group is archived and the contest was not assigned

#### Scenario: Moving a contest out of an archived group
- **WHEN** the operator saves a contest that is in an archived group with another group chosen
- **THEN** nothing is saved and AWS shows a notification that the group is archived and the contest was not moved out of it

#### Scenario: Clearing the group of a contest in an archived group
- **WHEN** the operator saves a contest that is in an archived group with the group emptied
- **THEN** nothing is saved and AWS shows a notification that the group is archived and the contest was not moved out of it

#### Scenario: Moving a contest between groups that are not archived
- **WHEN** the operator saves a contest that is in a group that is not archived with another group that is not archived chosen, or with the group emptied
- **THEN** the contest is saved with its new group, as before

#### Scenario: Editing a contest of an archived group
- **WHEN** the operator saves a contest that is already in an archived group, without changing the group
- **THEN** the contest is saved

#### Scenario: Renaming an archived group
- **WHEN** the operator saves an archived group with a different name
- **THEN** nothing is saved and AWS shows a notification that the group is archived and must be unarchived first

#### Scenario: Renaming a group that is not archived
- **WHEN** the operator saves a group that is not archived with a different name
- **THEN** the group is renamed as before
