## MODIFIED Requirements

### Requirement: Each server has a switch with three options
Every page of CWS and AWS, and the ranking page itself (the scoreboard), SHALL offer a switch with the options System, Light and Dark, showing the current choice. The labels are «Sistema», «Claro» and «Oscuro», except on AWS pages shown in English, where they are «System», «Light» and «Dark». The switch SHALL be present on CWS pages also when the contest has a single language. The notice and staff-login pages that RWS builds itself SHALL have no switch, and follow the saved choice.

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

#### Scenario: The AWS switch follows the page language
- **WHEN** an AWS page is shown in English or in Spanish
- **THEN** the theme switch labels are «System», «Light», «Dark» or «Sistema», «Claro», «Oscuro» respectively
