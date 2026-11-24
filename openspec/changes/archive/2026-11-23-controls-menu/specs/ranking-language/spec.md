## MODIFIED Requirements

### Requirement: A switch chooses the language
The ranking top bar SHALL offer a language switch with the options «Español» and «English», showing the current language, in the controls menu of the top bar, together with the theme switch. Choosing an option SHALL show the page in that language at once, whatever the browser language is.

#### Scenario: The switch is on the ranking page
- **WHEN** the ranking page is shown
- **THEN** the controls menu of the top bar has the language switch with the current language selected

#### Scenario: Choosing a language
- **WHEN** the person chooses English while the browser language is Spanish, or Spanish while it is English
- **THEN** the page is shown in the chosen language
