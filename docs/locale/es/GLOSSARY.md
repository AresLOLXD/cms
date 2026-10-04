# Spanish translation of the manual

The manual is written in English. The Spanish version is in the `.po` files
of `LC_MESSAGES/`, one per page.

## After changing an English page

    rm -rf /tmp/pot
    sphinx-build -W --keep-going -b gettext docs /tmp/pot
    sphinx-intl update -p /tmp/pot -l es -d docs/locale
    python docs/check_translations.py /tmp/pot docs/locale/es/LC_MESSAGES

Translate every entry the checker lists, then delete the `#, fuzzy` line
above any entry you have checked. A new `.po` file also starts with
`#, fuzzy` in its header: delete that line too, or Sphinx ignores the whole
file. Build the Spanish manual with
`sphinx-build -W --keep-going -b html -D language=es docs /tmp/html-es`.

Delete `/tmp/pot` before the gettext build, as above: a stale `.pot` of a
deleted or renamed page stays there and hides the checker's "no page
produces it".

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
- Never start a translation, or a line of it, like a list item, heading or
  quote (`1. `, `2) `, `- `, `* `, `+ `, `> `, `# `): Sphinx parses it as a
  different element and keeps the English without a warning, even when the
  English starts the same way. Escape the marker: `1\. Respalda todo`
  (written `"1\\. Respalda todo"` in the `.po` file). The checker reports it.
- Keep a trailing ` #noqa` where a translation has one (`API.po`, the
  message about the impersonation endpoint): Sphinx strips it from the page,
  and it silences the `i18n.inconsistent_references` warning that a
  translated internal reference (`` `Suplantación de usuarios`_ ``) raises.
- Straight double quotes in a `msgstr`; the Spanish build turns them into
  « ». A parenthetical em dash has no spaces: "—a propósito—".

## Titles and quoted headings

Links to a page use its Spanish title as their text (`:doc:` links without
text take it automatically). When you retitle a page, grep its old title in
every `.po` and change every link that quotes it.

When you change the translation of a heading, grep its old text in every
`.po` too: other pages quote headings. `contest-day.po` quotes headings of
`multi-contest`, `subtask-dependencies` and `docker-scripts` ("Ventanas",
"Reversión", "Verificar una programación", "Cuando un ranking está mal",
"Eliminar un ranking antiguo", 'Los botones "ahora"', "Modo de fallo",
"Escalar el Contest Web Server", "Avisos de ProxyService que necesitan a un
operador"); `docker-deployment.po` quotes "Despliegue" (a heading of
`multi-contest`) and "Scripts auxiliares" (its own heading);
`docker-scripts.po` quotes "Antes de un día de examen" (`multi-contest`) and
"1. Respalda todo" (`migrating-to-multi-contest`); `importing-users.po`
quotes "Cuando un ranking está mal: Regenerate" (`multi-contest`);
`participant-activity.po` quotes "Configuración de nginx" (`docker-scripts`);
`contest-day.po` also quotes "Después de la competencia" (a heading of
`participant-activity`).

| Page | English title | Spanish title |
|------|---------------|---------------|
| `index` | CMS (OMI fork) | CMS (fork de la OMI) |
| `API` | API for ContestWebServer | API del ContestWebServer |
| `Configuring a contest` | Configuring a contest | Configurar una competencia |
| `Creating a contest` | Creating a contest | Crear una competencia |
| `Data model` | Data model | Modelo de datos |
| `Detailed timing configuration` | Detailed timing configuration | Configuración detallada de tiempos |
| `Docker image` | Docker image | Imagen de Docker |
| `External contest formats` | External contest formats | Formatos externos de competencia |
| `Installation` | Installation | Instalación |
| `Internals` | Internals | Funcionamiento interno |
| `Introduction` | Introduction | Introducción |
| `Localization` | Localization | Localización |
| `RankingWebServer` | RankingWebServer | RankingWebServer |
| `Running CMS` | Running CMS | Ejecutar CMS |
| `Score types` | Score types | Tipos de puntaje |
| `Task types` | Task types | Tipos de problema |
| `Task versioning` | Task versioning | Versionado de problemas |
| `Troubleshooting` | Troubleshooting | Solución de problemas |
| `cms-loader` | CMS-Loader | CMS-Loader |
| `contest-day` | Contest day | Día de la competencia |
| `docker-deployment` | Docker deployment | Despliegue con Docker |
| `docker-scripts` | Docker Scripts Guide | Guía de scripts de Docker |
| `importing-users` | Importing users and participations | Importar usuarios y participaciones |
| `migrating-to-multi-contest` | Migrating from one deployment per contest to a single multi-contest deployment | Migrar de un despliegue por competencia a un único despliegue de varias competencias |
| `multi-contest` | Running several contests at once | Ejecutar varias competencias a la vez |
| `participant-activity` | Participant activity | Actividad de los participantes |
| `ranking-mexico` | Ranking: Mexican State Flags and Auto-Team Registration | Ranking: banderas de los estados mexicanos y registro automático de equipos |
| `rekarel` | Rekarel | Rekarel |
| `subtask-dependencies` | Subtask dependencies | Dependencias entre subtareas |
| `two-phase-grading` | Two-phase grading | Evaluación en dos fases |

The captions of the table of contents (`index`): Primeros pasos / Preparar
una competencia / Ejecutar una competencia / Referencia / Desarrollo.

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
| logs | registros (the script `logs.sh` and the AWS label **Logs** stay) |
| rebuild | reconstruir |
| container | contenedor |
| task type | tipo de problema |
| score type | tipo de puntaje |
| time limit | límite de tiempo (never "tiempo límite") |
| schedule (hide/freeze) | programación |
| window (time window) | ventana |
| screening (first phase of two-phase) | filtro; the first time "filtro (screening)" |
| exam / exam day | examen / día de examen |
| freeze (noun), freeze time | congelamiento, hora de congelamiento |
| problem, issue (not a task) | falla (never "problema", which means task) |
| default | por defecto or predeterminado (both fine) |
| dataset, manager, checker, grader, codename, shard, script, sandbox, fork, upstream | unchanged |
