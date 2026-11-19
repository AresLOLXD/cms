# aws-language Specification

## Purpose
TBD - created by archiving change aws-language-machinery. Update Purpose after archive.

## Requirements

### Requirement: AWS shows Spanish or English per request
AWS SHALL show each page in one language chosen for that request: the language saved in the visitor's `aws_language` cookie when it is one of the languages AWS offers; otherwise the best match between the browser's `Accept-Language` preferences and the offered languages; otherwise English. The response SHALL carry that language in its `Content-Language` header and the page SHALL declare it in its `lang` attribute.

#### Scenario: Browser in Spanish
- **WHEN** a visitor with no cookie requests a page with `Accept-Language: es-MX`
- **THEN** the page is in Spanish, with `Content-Language: es` and `lang="es"`

#### Scenario: Browser in English or another language
- **WHEN** a visitor with no cookie requests a page with `Accept-Language: en-US`, `fr` or none
- **THEN** the page is in English, with `Content-Language: en` and `lang="en"`

#### Scenario: The cookie wins
- **WHEN** a visitor with `aws_language=en` and a Spanish browser requests a page, or the other way around
- **THEN** the page is in the cookie's language

#### Scenario: A bad cookie is ignored
- **WHEN** the `aws_language` cookie holds a value that is not an offered language
- **THEN** the language is chosen from `Accept-Language` as if there were no cookie

### Requirement: Only languages with an AWS catalog are offered
AWS SHALL offer English and every language that has a compiled AWS catalog. A language that has a contestant-site catalog but no AWS catalog SHALL NOT be offered. Starting AWS SHALL NOT log an error or a traceback for languages that have no AWS catalog.

#### Scenario: Offered languages
- **WHEN** AWS starts with a Spanish AWS catalog and 24 other languages that only have contestant-site catalogs
- **THEN** it offers English and Spanish only

#### Scenario: A quiet start
- **WHEN** AWS starts and loads its catalogs
- **THEN** no warning or traceback is logged for the languages without an AWS catalog

### Requirement: A selector on every AWS page
Every AWS page, including the login page and the error pages, SHALL have a language selector with an «Automatic» option showing the language chosen from the browser, and one option per offered language named in its own language. Choosing an option SHALL save it in the `aws_language` cookie (path `/`, one year) and reload the page; choosing «Automatic» SHALL remove the cookie.

#### Scenario: The selector is on the login and error pages
- **WHEN** the login page or an error page is shown
- **THEN** it has the language selector

#### Scenario: Choosing a language
- **WHEN** the visitor chooses Spanish in the selector
- **THEN** the cookie `aws_language=es` is saved with path `/` and a one-year expiry, and the page reloads in Spanish

#### Scenario: Choosing Automatic
- **WHEN** the visitor chooses «Automatic»
- **THEN** the cookie is removed and the page reloads in the language of the browser

### Requirement: The first pages are translated end to end
The login page, the error page, the language selector and the theme switch SHALL be fully available in Spanish and in English. The other AWS pages are covered by the requirement that every admin page follows the AWS language.

#### Scenario: The login page in Spanish
- **WHEN** the login page is requested with Spanish chosen
- **THEN** its texts and the theme and language selectors are in Spanish

#### Scenario: The error page in Spanish
- **WHEN** an error page is shown with Spanish chosen
- **THEN** its title and text are in Spanish

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

### Requirement: The Spanish-only pages follow the page language
The ranking group pages, the bulk removal pages, the users CSV import pages and the contests export pages SHALL show all of their texts, including tooltips, button labels, confirmation questions, table headers, previews and error messages, in the language chosen for the request. Their Spanish SHALL be the text that they showed before this change, except for the wording that the style guide replaced (the pairs of `MAIN_TERMS` in `aws_catalog.py`, for example «fase de análisis» for «modo de análisis» and «user tests» for «pruebas de usuario»).

#### Scenario: A page in English
- **WHEN** one of these pages is requested with English chosen
- **THEN** its texts are in English

#### Scenario: A page in Spanish
- **WHEN** the same page is requested with Spanish chosen
- **THEN** its texts are the Spanish texts that the page showed before this change, with the terms of the style guide in place of the replaced wording

#### Scenario: Ranking group state in both languages
- **WHEN** the ranking group page shows a hidden, frozen, visible or archived group
- **THEN** the state summary, the archive summary and the action buttons are in the chosen language

### Requirement: Counts use the right plural form
Messages that contain a count (users, participations, errors, rows, contests, megabytes) SHALL use the singular or plural form that the chosen language requires for that number.

#### Scenario: One and several
- **WHEN** a message reports 1 item or 2 items in English or Spanish
- **THEN** it uses the singular form for 1 and the plural form for 2

#### Scenario: Zero
- **WHEN** a message reports 0 items
- **THEN** it uses the plural form in English and in Spanish

### Requirement: Notifications and jobs use the language of the admin who starts them
A notification raised by an admin action SHALL be written in the language of the request that raised it. The errors and status texts of an import or export job SHALL be written in the language of the request that started the job, and SHALL keep that language when any admin polls the job.

#### Scenario: A notification
- **WHEN** an admin with Spanish chosen removes users and the removal raises a notification
- **THEN** the notification is in Spanish

#### Scenario: A job polled by another admin
- **WHEN** an admin with Spanish chosen starts an import that fails, and an admin with English chosen polls its status
- **THEN** the error text is in Spanish

### Requirement: The scripts of the import and export pages use page-language texts
The texts that the scripts of the users import page and the contests export page show (progress, results, errors, buttons they create) SHALL come from the server in the page language.

#### Scenario: Script texts in English
- **WHEN** the import or export page is shown with English chosen
- **THEN** the texts the script can show are in English

#### Scenario: Script texts in Spanish
- **WHEN** the same page is shown with Spanish chosen
- **THEN** the texts the script can show are in Spanish

### Requirement: Data keys and CSV aliases do not change
The names of the data fields that the pages and scripts exchange, and the CSV header aliases that the import accepts, SHALL stay as they are, in whatever language the page is shown.

#### Scenario: The import summary in English
- **WHEN** an import summary is shown in English
- **THEN** it is built from the same data fields, with the labels in English

#### Scenario: CSV aliases
- **WHEN** a CSV with the Spanish header «contraseña» is imported while the page is in English
- **THEN** the column is still recognized as the password

### Requirement: Every admin page follows the AWS language
Every page of AWS SHALL show its user-visible texts (text, titles, placeholders, alternative texts, button labels, confirmation questions, table headers, messages built by scripts) in the language chosen for the request. The exceptions are names and values that come from the data, language names, evaluation plugin names and parameters, and technical identifiers. With English chosen, the visible texts and the values a page posts SHALL be what they were before this change.

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
