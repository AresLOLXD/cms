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
The login page, the error page, the language selector and the theme switch SHALL be fully available in Spanish and in English. Strings of other AWS pages are not covered by this requirement.

#### Scenario: The login page in Spanish
- **WHEN** the login page is requested with Spanish chosen
- **THEN** its texts and the theme and language selectors are in Spanish

#### Scenario: The error page in Spanish
- **WHEN** an error page is shown with Spanish chosen
- **THEN** its title and text are in Spanish

### Requirement: The AWS catalog is built and checked
The build SHALL compile the AWS catalog together with the contestant-site catalog. A test SHALL fail when the AWS Spanish catalog has an untranslated or fuzzy message, and when a message in the AWS code or templates is missing from the AWS template or the Spanish catalog.

#### Scenario: Incomplete catalog
- **WHEN** the AWS Spanish catalog has an untranslated or fuzzy message
- **THEN** the check fails and lists it

#### Scenario: A message missing from the catalog
- **WHEN** a message is added to an AWS template or handler without refreshing the AWS catalog
- **THEN** the check fails and lists it

#### Scenario: Both catalogs compile
- **WHEN** the catalogs are compiled
- **THEN** both the contestant-site and the AWS compiled catalogs exist for Spanish

### Requirement: The Spanish-only pages follow the page language
The ranking group pages, the bulk removal pages, the users CSV import pages and the contests export pages SHALL show all of their texts, including tooltips, button labels, confirmation questions, table headers, previews and error messages, in the language chosen for the request. Spanish SHALL be exactly the text that these pages show today.

#### Scenario: A page in English
- **WHEN** one of these pages is requested with English chosen
- **THEN** its texts are in English

#### Scenario: A page in Spanish
- **WHEN** the same page is requested with Spanish chosen
- **THEN** its texts are the Spanish texts that the page showed before this change

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
