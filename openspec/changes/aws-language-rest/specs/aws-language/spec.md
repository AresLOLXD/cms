## ADDED Requirements

### Requirement: Every admin page follows the AWS language
Every page of AWS, in each page group (chrome and sidebar, contest, task, dataset, submissions and user tests, users and teams and groups, participations and activity and ranking, system and admins), SHALL show its user-visible texts (text, titles, placeholders, alternative texts, button labels, confirmation questions, table headers, messages built by scripts) in the language chosen for the request, except for the documented exceptions: names and values that come from the data, language names, evaluation plugin names and parameters, and technical identifiers. With English chosen, the visible texts and the values a page posts SHALL be exactly what they were before this change.

#### Scenario: A page group in Spanish
- **WHEN** a page of any group is requested with Spanish chosen
- **THEN** it shows no English text outside the documented exceptions

#### Scenario: A page in English is unchanged
- **WHEN** a page is requested with English chosen
- **THEN** its visible texts and the values it posts equal those it had before this change

#### Scenario: A new untranslated text
- **WHEN** a text is added to a template, a script or a handler without gettext
- **THEN** the check fails and names the file and the text

### Requirement: Operation buttons post the same value in every language
A button or control that sends an operation name to the server SHALL post the same value whatever the language of the page; only its visible label SHALL be translated. A handler SHALL accept the operation posted from a page in any language.

#### Scenario: An operation posted from a Spanish page
- **WHEN** an admin presses the Spanish button that moves a contest task up by one
- **THEN** the page posts the language-independent value and the task moves

#### Scenario: No translated posted values
- **WHEN** the pages are rendered with every text marked as translated
- **THEN** no named control posts a translated value

### Requirement: Notifications and form errors use the admin's language
A notification raised by an admin action and a form error shown for an invalid field SHALL be written in the language of the request that raised them. The technical detail of an error (the text of a database or library exception) SHALL stay as it is.

#### Scenario: Invalid field in Spanish
- **WHEN** an admin with Spanish chosen saves a form with a value that is not a number
- **THEN** the notification and the error text are in Spanish

#### Scenario: Invalid field in English
- **WHEN** an admin with English chosen saves the same form
- **THEN** the notification and the error text are the English texts they had before

#### Scenario: A notification read by another admin
- **WHEN** an admin with Spanish chosen raises a notification and an admin with English chosen polls the notifications first
- **THEN** the text keeps the language of the admin who raised it

### Requirement: Script texts follow the AWS language
The texts that the admin scripts show (alerts, confirmations, job and status descriptions, times, notifications built in the browser) SHALL come from the server in the language of the page.

#### Scenario: A shared script message in Spanish
- **WHEN** a page is shown with Spanish chosen and the shared script builds a message
- **THEN** the message is in Spanish

#### Scenario: A confirmation in Spanish
- **WHEN** an admin with Spanish chosen presses a button that asks for confirmation to reevaluate
- **THEN** the question is in Spanish

### Requirement: Evaluation outcomes and sizes appear in the admin's language
Evaluation outcome texts, sizes and `N/A` shown in the admin pages SHALL use the Spanish of the contestant site when AWS has no text of its own for them, and SHALL be unchanged in English.

#### Scenario: Outcome in Spanish
- **WHEN** a submission page shows an outcome such as «Output is correct» with Spanish chosen
- **THEN** the outcome shows its Spanish text

#### Scenario: Outcome in English
- **WHEN** the same page is shown with English chosen
- **THEN** the outcome shows the English text it had before

### Requirement: Quick answers are labeled in the admin's language and stored unchanged
The labels of the quick answers in the question form SHALL be shown in the language of the request, and the answer stored for the contestant SHALL be the same text whatever the admin's language.

#### Scenario: A quick answer from a Spanish page
- **WHEN** an admin with Spanish chosen answers a question with the quick answer labeled «Sí»
- **THEN** the stored reply subject is the English «Yes»

## MODIFIED Requirements

### Requirement: The AWS catalog is built and checked
The build SHALL compile the AWS catalog together with the contestant-site catalog. A test SHALL fail when the AWS Spanish catalog has an untranslated or fuzzy message, when a message in the AWS code or templates is missing from the AWS template or the Spanish catalog, and when a Spanish message breaks the mechanical rules of the style guide (register, punctuation, placeholders and markup kept).

#### Scenario: Incomplete catalog
- **WHEN** the AWS Spanish catalog has an untranslated or fuzzy message
- **THEN** the check fails and lists it

#### Scenario: A message missing from the catalog
- **WHEN** a message is added to an AWS template or handler without refreshing the AWS catalog
- **THEN** the check fails and lists it

#### Scenario: Both catalogs compile
- **WHEN** the catalogs are compiled
- **THEN** both the contestant-site and the AWS compiled catalogs exist for Spanish

#### Scenario: Spanish register
- **WHEN** a Spanish message uses the formal register, three dots instead of the ellipsis character, an unpaired inverted mark, or loses a placeholder or a markup tag of its English message
- **THEN** the check fails and lists it
