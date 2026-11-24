# controls-menu Specification

## Purpose

TBD - created by archiving change controls-menu. Update Purpose after archive.

## Requirements

### Requirement: The personal controls live in one corner menu
Every page of AWS, CWS and the ranking SHALL offer the personal controls it has today (log out, theme, language, and on AWS the greeting) in one menu opened by a small icon in the top right corner of the page, and SHALL NOT show those controls anywhere else. A page that has no log out or no language control today SHALL NOT gain one.

#### Scenario: A logged in AWS page
- **WHEN** a logged in administrator opens any AWS page
- **THEN** the page has the corner icon, the sidebar has no log out button, theme switch or language selector, and the menu holds the greeting, the log out button, the theme switch and the language selector

#### Scenario: The AWS login page
- **WHEN** a visitor opens the AWS login page or an AWS error page
- **THEN** the page has the corner icon and its menu holds the theme switch and the language selector, without a log out button

#### Scenario: A contest page of CWS
- **WHEN** a logged in contestant opens a CWS contest page
- **THEN** the navbar has the corner icon holding the theme switch, the language selector and the log out button, and still shows who is logged in

#### Scenario: A CWS page without the contest navbar
- **WHEN** a visitor opens a CWS page that has no contest navbar, such as the list of contests
- **THEN** the page has the corner icon holding the theme switch, as today without a language selector or a log out button

#### Scenario: One language on CWS
- **WHEN** a CWS contest offers only one language
- **THEN** the menu holds no language selector

#### Scenario: The ranking page
- **WHEN** the ranking scoreboard is shown
- **THEN** its top panel has the corner icon holding the theme switch and the language switch, and no other copy of them

### Requirement: The menu opens and closes
The icon SHALL open the menu when activated with the mouse or the keyboard and close it when activated again. The menu SHALL also close when the person presses Escape or clicks outside it. Closing with Escape SHALL return the keyboard focus to the icon. Choosing a theme SHALL leave the menu open.

#### Scenario: Open and close with the icon
- **WHEN** the person activates the icon twice
- **THEN** the menu opens and then closes

#### Scenario: Close with Escape
- **WHEN** the menu is open and the person presses Escape
- **THEN** the menu closes and the icon has the keyboard focus

#### Scenario: Close by clicking outside
- **WHEN** the menu is open and the person clicks anywhere outside it
- **THEN** the menu closes

#### Scenario: Open and close without scripts
- **WHEN** scripts are disabled in the browser
- **THEN** the icon still opens and closes the menu

### Requirement: Every control keeps working from the menu
Each control SHALL behave exactly as before this change: the theme choice SHALL apply at once and be kept in the browser, the language choice SHALL be kept in the cookie or storage that the server already uses and SHALL show the page in that language, and the log out button SHALL end the session with the same protected request as before.

#### Scenario: Choosing a theme in the menu
- **WHEN** the person picks Dark in the menu
- **THEN** the page switches to the dark theme at once and later visits in that browser keep it

#### Scenario: Choosing a language in the menu
- **WHEN** the person picks a language in the menu on AWS, CWS or the ranking
- **THEN** the page is shown in that language, and the choice is kept as it was before this change

#### Scenario: Logging out from the menu
- **WHEN** a logged in administrator or contestant presses the log out button in the menu
- **THEN** the session ends and the visitor is taken to the login page

### Requirement: The menu is accessible, translated and readable in both themes
The icon SHALL have an accessible name in the language of the page, SHALL be reachable and operable with the keyboard, and SHALL be drawn with enough contrast to its background in the light and in the dark theme. The menu SHALL open over the page without moving the page content.

#### Scenario: The icon has a name in the page language
- **WHEN** a page is shown in Spanish or in English
- **THEN** the icon's accessible name is in that language

#### Scenario: Contrast of the icon and the menu
- **WHEN** the light or the dark theme is active
- **THEN** the icon and the menu text meet the contrast of the color theme requirement

#### Scenario: The menu does not move the page
- **WHEN** the menu opens and closes
- **THEN** the position of the page content does not change
