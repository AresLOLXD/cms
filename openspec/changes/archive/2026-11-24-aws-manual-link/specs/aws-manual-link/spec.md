# Spec Delta

## Purpose

Lets an administrator reach the fork's manual from the Admin Web Server, and lets an operator point that link at their own copy or hide it.

## ADDED Requirements

### Requirement: AWS links to the manual from its navigation
A logged in AWS page SHALL show a sidebar entry named Manual that opens the manual in a new tab, when a manual address is configured. The link SHALL carry `rel="noopener noreferrer"`. The entry SHALL NOT appear on the login page or on the error pages.

#### Scenario: The home page
- **WHEN** an administrator opens the Overview
- **THEN** the sidebar has an entry "Manual" whose link opens the manual in a new tab

#### Scenario: A contest page
- **WHEN** an administrator opens a page of a contest
- **THEN** the sidebar of the contest has the same entry

#### Scenario: The login and error pages
- **WHEN** a visitor opens the AWS login page or an error page
- **THEN** the page has no Manual entry

#### Scenario: The link is safe
- **WHEN** the entry is shown
- **THEN** its link carries `rel="noopener noreferrer"`, so the manual's host gets neither the opener window nor the AWS address

### Requirement: The Manual entry is translated
The text of the entry and its new-tab hint SHALL come from the AWS catalog, so a Spanish page shows them in Spanish.

#### Scenario: A Spanish page
- **WHEN** an administrator reads a page in Spanish
- **THEN** the entry reads "Manual" and its hint reads "Se abre en una pestaña nueva"

### Requirement: The manual address comes from the configuration
`[admin_web_server] manual_url` SHALL set the address of the manual. It SHALL default to the published manual of the fork. An empty value SHALL remove the entry. The text `{language}` in the value SHALL be replaced by `es` on a Spanish page and by `en` on any other page.

#### Scenario: Default address
- **WHEN** `manual_url` is not set
- **THEN** the entry points to the published manual of the fork, in the language of the page

#### Scenario: Own address
- **WHEN** `manual_url` is another address
- **THEN** the entry points to that address

#### Scenario: Language of the page
- **WHEN** the address contains `{language}` and the page is in Spanish, English or any other language
- **THEN** the entry gets `es` for Spanish and `en` for the others

#### Scenario: Address without a placeholder
- **WHEN** the address has no `{language}`, or has other braces
- **THEN** it is used as written and the page still renders

#### Scenario: Hidden when empty
- **WHEN** `manual_url` is an empty string
- **THEN** the sidebar has no Manual entry

### Requirement: Docker deployments set the address with one variable
`CMS_MANUAL_URL` SHALL write `manual_url` into the generated configuration. When it is unset or empty the generated configuration SHALL leave the default in force. The value `off` SHALL hide the entry.

#### Scenario: Variable unset
- **WHEN** `CMS_MANUAL_URL` is unset or empty
- **THEN** the generated configuration has no `manual_url` and the default applies

#### Scenario: Variable set
- **WHEN** `CMS_MANUAL_URL` holds an address
- **THEN** the generated configuration sets `manual_url` to it

#### Scenario: Variable off
- **WHEN** `CMS_MANUAL_URL` is `off`
- **THEN** the generated configuration sets `manual_url` to an empty string and the entry is hidden
