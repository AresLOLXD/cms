# Spanish translation of the manual

The manual is written in English. The Spanish version is in the `.po` files
of `LC_MESSAGES/`, one per page.

## After changing an English page

    sphinx-build -W --keep-going -b gettext docs /tmp/pot
    sphinx-intl update -p /tmp/pot -l es -d docs/locale
    python docs/check_translations.py /tmp/pot docs/locale/es/LC_MESSAGES

Translate every entry the checker lists, then delete the `#, fuzzy` line
above any entry you have checked. A new `.po` file also starts with
`#, fuzzy` in its header: delete that line too, or Sphinx ignores the whole
file. Build the Spanish manual with
`sphinx-build -W --keep-going -b html -D language=es docs /tmp/html-es`.

## Rules

- Keep verbatim: everything between backticks (commands, paths, variables,
  code), URLs, the targets of links and roles (`:doc:`, `{doc}`), and the
  names of reST/MyST constructs. Translate the visible text of links and
  roles.
- Texts of an interface quote it as the interface shows it. The contestant
  interface is translated (use its Spanish text, from
  `cms/locale/es/LC_MESSAGES/cms.po`); the Admin Web Server and most of the
  Ranking Web Server are in English or in their own Spanish labels
  ("Ocultar ahora"): quote them unchanged.
- Address the reader as "tú".

## Terms

| English | Spanish |
|---------|---------|
| contest | competencia |
| task | problema |
| submission | envío |
| user test | prueba de usuario |
| subtask | subtarea |
| testcase | caso de prueba |
| score | puntaje |
| statement | enunciado |
| attachment | adjunto |
| token | token |
| contestant | concursante |
| scoreboard, ranking | ranking |
| ranking group | grupo de ranking |
| worker | worker |
| queue | cola |
| evaluation | evaluación |
| compilation | compilación |
| two-phase screening | evaluación en dos fases |
| dependency | dependencia |
| deployment | despliegue |
| backup | respaldo |
| staff | staff |
| freeze / unfreeze | congelar / descongelar |
| hide / show | ocultar / mostrar |
| analysis mode | fase de análisis |
| Admin Web Server, Contest Web Server, Ranking Web Server, Evaluation Service, … | unchanged |
