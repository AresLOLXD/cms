## ADDED Requirements

### Requirement: The theme follows the system unless the user chooses
Each of CWS, AWS and RWS SHALL show its pages in a light or a dark theme. With no saved choice, a page SHALL use the theme the browser reports through the system color-scheme preference; when the browser reports none, it SHALL use the light theme.

#### Scenario: System prefers dark
- **WHEN** a person with no saved choice opens a page and the system prefers dark
- **THEN** the page is shown in the dark theme

#### Scenario: System prefers light or nothing
- **WHEN** a person with no saved choice opens a page and the system prefers light or reports no preference
- **THEN** the page is shown in the light theme

### Requirement: Each server has a switch with three options
Every page of CWS and AWS, and the ranking page itself (the scoreboard), SHALL offer a switch with the options System, Light and Dark, labelled «Sistema», «Claro» and «Oscuro», showing the current choice. The switch SHALL be present on CWS pages also when the contest has a single language. The notice and staff-login pages that RWS builds itself SHALL have no switch, and follow the saved choice.

#### Scenario: The switch is on every page
- **WHEN** a page of CWS or AWS, or the RWS scoreboard page, is shown
- **THEN** it has the three-option switch with the current choice selected

#### Scenario: The ranking built-in pages have no switch
- **WHEN** the hidden-ranking notice or the staff login page of RWS is shown
- **THEN** it has no switch and shows the saved theme

#### Scenario: CWS with a single language
- **WHEN** a CWS page is shown for a contest that has only one available language
- **THEN** the theme switch is still present

#### Scenario: Choosing Dark or Light
- **WHEN** the person picks Dark or Light in the switch
- **THEN** the page changes to that theme at once, whatever the system prefers

#### Scenario: Choosing System
- **WHEN** the person picks System
- **THEN** the page follows the system preference again

### Requirement: The choice is kept in the browser, per server
The choice SHALL be kept in the browser's local storage under one key, SHALL apply to every later page of the same server in that browser, and SHALL NOT be sent to the server or stored in the database. When local storage is unavailable, the pages SHALL work and follow the system preference.

#### Scenario: The choice survives a reload and other pages
- **WHEN** the person picks Dark and then opens another page of the same server, or reloads
- **THEN** that page is in the dark theme

#### Scenario: Another server is independent
- **WHEN** the person picks Dark on one server
- **THEN** the other servers keep their own choice

#### Scenario: Local storage is unavailable
- **WHEN** reading or writing local storage fails
- **THEN** the page is shown following the system preference and no error stops it

### Requirement: The theme is applied before the first paint
The theme (saved or system) SHALL be applied before the page content is first painted, so a person who chose dark never sees a light page flash.

#### Scenario: A saved dark choice with a light system
- **WHEN** the system prefers light, the saved choice is Dark, and a page loads
- **THEN** the theme attribute is already set when the body starts rendering

### Requirement: Native controls follow the theme
In the dark theme, the browser's native controls (form fields, scrollbars) SHALL be rendered dark too.

#### Scenario: Dark native controls
- **WHEN** a page is shown in the dark theme
- **THEN** its declared color scheme is dark

### Requirement: The three servers cover their pages
In the dark theme the following SHALL use dark surfaces with readable text: CWS pages (navbar, tables, forms, buttons, alerts, task statements and submissions); AWS pages (sidebar, content, tables, forms, notification and archive banners, code highlighting); RWS pages (scoreboard and its up/down score animations, side panel, user detail frame, search, footer) and the pages that RWS builds itself (hidden-ranking notice, staff login, staff banner).

#### Scenario: The ranking built-in pages
- **WHEN** the hidden-ranking notice, the staff login or the staff banner is shown in dark theme
- **THEN** it uses the dark colors, not the light ones

#### Scenario: Score cells keep their meaning
- **WHEN** the scoreboard is shown in dark theme
- **THEN** zero, improved, worsened and selected scores remain distinguishable from each other and from the background

### Requirement: Icons that assume a light background stay visible
Icons and sprites that are drawn for a light background SHALL remain visible in the dark theme.

#### Scenario: CWS chevrons and RWS ticks
- **WHEN** the CWS submission toggles or the RWS tick and close icons are shown in dark theme
- **THEN** they are visible against the dark background

### Requirement: Charts keep a light panel
Canvas and SVG charts (the AWS resources charts, the RWS user detail chart and the RWS overview) SHALL be drawn as today, on a light panel, in both themes.

#### Scenario: A chart in dark theme
- **WHEN** a chart is shown in the dark theme
- **THEN** it is drawn on a light background panel

### Requirement: Both themes meet contrast AA
For each server, the declared text, link and status colors SHALL have a contrast ratio of at least 4.5:1 against the surface they are drawn on, in the light and in the dark theme.

#### Scenario: Contrast in every theme
- **WHEN** the declared color pairs of a server are checked
- **THEN** every pair has a contrast ratio of at least 4.5:1 in the light set and in the dark set
