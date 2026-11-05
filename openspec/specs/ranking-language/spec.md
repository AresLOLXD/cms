# ranking-language Specification

## Purpose
TBD - created by archiving change ranking-language. Update Purpose after archive.

## Requirements

### Requirement: The ranking shows Spanish or English
The ranking page SHALL show all of its user-visible texts in Spanish or in English. With no saved choice it SHALL use Spanish when the browser's preferred language starts with `es` and English in every other case, including when the browser reports none.

#### Scenario: Browser in Spanish
- **WHEN** a person with no saved choice opens the ranking and the browser language is `es-MX`
- **THEN** the page texts are in Spanish and the page language is `es`

#### Scenario: Browser in English or another language
- **WHEN** a person with no saved choice opens the ranking and the browser language is `en-US`, `fr` or none
- **THEN** the page texts are in English and the page language is `en`

### Requirement: A switch chooses the language
The ranking top bar SHALL offer a language switch with the options «Español» and «English», showing the current language, next to the theme switch. Choosing an option SHALL show the page in that language at once, whatever the browser language is.

#### Scenario: The switch is on the ranking page
- **WHEN** the ranking page is shown
- **THEN** the top bar has the language switch with the current language selected

#### Scenario: Choosing a language
- **WHEN** the person chooses English while the browser language is Spanish, or Spanish while it is English
- **THEN** the page is shown in the chosen language

### Requirement: The choice is kept in the browser
The choice SHALL be kept in the browser's local storage under one key, SHALL apply to every later visit to the same ranking in that browser, and SHALL NOT be sent to the server or kept in a cookie. When local storage is unavailable the page SHALL work and use the browser language. It SHALL be independent of the theme choice.

#### Scenario: The choice survives a reload
- **WHEN** the person chooses a language and reloads the page
- **THEN** the page is shown in that language

#### Scenario: Local storage is unavailable
- **WHEN** reading or writing local storage fails
- **THEN** the page works and uses the browser language

#### Scenario: Theme and language are independent
- **WHEN** the person changes the language
- **THEN** the saved theme choice is unchanged, and the other way around

### Requirement: Every user-visible text is in the catalog
The texts of the ranking page (labels, table headers, the search placeholder, alt texts, the connection status messages, the elapsed, remaining and current time labels, the global and per-user views, the overview chart labels and the page title) SHALL exist in both languages. The two catalogs SHALL have the same keys and no empty text. Contest, task, team and user names, numbers and times SHALL be shown as they are today. Log messages SHALL stay in English.

#### Scenario: Both catalogs are complete
- **WHEN** the catalogs are checked
- **THEN** both have the same keys, none is empty, and every key that the page and the scripts use is defined

#### Scenario: Connection messages keep their links
- **WHEN** the connection is lost or the local data is outdated
- **THEN** the message is shown in the chosen language with its «try to connect» or «reload the page» link working

#### Scenario: Data names are untouched
- **WHEN** the scoreboard shows a contest, task, team or user name
- **THEN** the name is shown as the data has it, in either language

### Requirement: The pages built by the ranking server are bilingual
The hidden-ranking notice, the staff login and the failed-login notice, the staff and frozen banners, and their page titles SHALL carry Spanish and English text and show the language that the page language rule picks (saved choice, else browser language, else English). Without JavaScript they SHALL show Spanish. Their Spanish texts SHALL stay as they are today.

#### Scenario: The hidden notice in English
- **WHEN** the ranking of a hidden group is requested by a visitor whose chosen language is English
- **THEN** the notice shows its English text and not the Spanish one

#### Scenario: The hidden notice without JavaScript
- **WHEN** the same page is shown by a browser that runs no scripts
- **THEN** the notice shows its Spanish text

#### Scenario: The banners follow the language
- **WHEN** the staff banner or a frozen banner is shown on the ranking page
- **THEN** it is in the chosen language

### Requirement: Built-in pages stay safe
The group name that a built-in page shows SHALL be shown once, reduced to its safe characters (lowercase letters, digits, hyphen and underscore) exactly as today, and times SHALL stay escaped as today. No text that a user controls SHALL reach the script that picks the language.

#### Scenario: A group name with markup
- **WHEN** a hidden notice is built for a group whose name contains markup characters
- **THEN** only the safe characters of the name are shown, once, and nothing of the name reaches the language script

### Requirement: The script is served in every ranking state
The language script SHALL be served by a ranking whose visibility is live or frozen, like the other static files of the ranking page.

#### Scenario: Frozen ranking
- **WHEN** the language script is requested from a frozen group
- **THEN** it is served
