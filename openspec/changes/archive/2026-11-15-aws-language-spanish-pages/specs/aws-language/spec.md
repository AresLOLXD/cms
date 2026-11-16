## ADDED Requirements

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
