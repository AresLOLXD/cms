# AWS admin site: Spanish translation guide (Change C)

Status: design panel notes, read-only. Nothing in the repo was edited.
Files in this directory:
- `aws-es-translation-guide.md` (this file)
- `aws-es-glossary.json` (machine copy of the glossary; same entries as section 2)
- `aws-es-check.py` (checker prototype; see section 4 for its status)

Limits of this seat: it has no shell, so the checker was written but NOT executed,
and the string count in section 0 was not measured. Section 4 gives the output I
derived by hand from the file; run the command there to confirm it.

## 0. Scope facts (read from the worktree)

- The `aws` domain is extracted from `cms/server/admin/**.py` and `templates/**.html`
  (`babel_mapping_aws.cfg`, `setup.cfg` domain `cms aws`). `aws.po` has 258 msgids.
- Only some fork pages are wrapped: 174 uses of `_()` / `{% trans %}` in
  12 templates (`contest_users_import`, `export`, `ranking_group`, `ranking_groups`,
  `add_ranking_group`, `users_bulk_remove`, ...).
- Most upstream admin pages are NOT wrapped. They are plain English in the HTML:
  `contest.html` ("Contest configuration", "Allowed localizations", ...), `base.html`
  (`<title>Admin</title>`, "Administration"), `task.html`, `submission.html`, `user.html`,
  `overview.html`, and so on. Even fork pages are not fully wrapped:
  `ranking_group_remove.html` has `<h1>Remove ranking group</h1>` in English.
- Consequence: "translate ALL remaining English" is two jobs, not one.
  (A) Wrap every English string of the admin templates and handlers in `{% trans %}` /
  `_()` without changing the English output. That is a code change: each lane must
  render the English page and compare it with the original. (B) Translate the new msgids
  into `aws.po`, then recompile `aws.mo` (the `.mo` is in the repo).
- The 1,100-1,300 figure was not verified. A regex over the 64 templates matches 386
  lines with text outside tags, and `contest.html` alone gives 130 multi-word matches,
  but those include attributes, CSS and JS. Count it after step (A) with
  `pybabel extract` against `babel_mapping_aws.cfg`, not before.
- Recommended order: wrap first, in one reviewed batch with a rendering test; freeze the
  msgids (including the typo in section 5); then translate by lane; then run the checker
  on each lane and on the merged file.

## 1. Register

Rule: `tú`, imperative for instructions, with no `usted` and no `vosotros`. The register
is neutral Latin American. Pronouns and forms of `tú` for the reader: `tú`, `te`, `tu`,
`tus`, `puedes`, `tienes`, `eres`. Use `su`/`sus` only for a third party (the import, the
account), never for the reader.

Imperatives (tú): "Elige", "Escribe", "Revisa", "Marca", "Corrige", "Deja", "Espera", "Vuelve".
Pronoun-attached imperatives: "Divídela", "Déjala", "quítala", "trátalo". Accent rules:
"quítala", "Déjala", "Vuelve" (Mexican tú forms; vosotros forms such as "Pulsad" are never used).

Gender-neutral wording: prefer generic nouns for people ("concursante", "persona", "quien").
For generic masculine plurals used elsewhere in the catalog ("los administradores") keep the
existing usage; do not add "-e", "@" or "x" forms.

Ten examples from the current `aws.po` (line of the msgstr):

1. L211 `elige un archivo CSV` (msgid "choose a CSV file"): imperative tú, lowercase because it is a fragment.
2. L860 `Escribe %(count)s para confirmar:` (msgid "Type %(count)s to confirm:"): imperative tú.
3. L171 `revisa la lista y confirma otra vez.`: two imperatives in one sentence.
4. L178 `Divídela en listas más pequeñas.`: pronoun attached to the imperative.
5. L741 `Déjala vacía para conservar la contraseña actual.`: agreement of the attached pronoun.
6. L271 `Define una nueva contraseña de staff o quítala, no ambas.`: imperative pair.
7. L580 `Exportando… puedes cerrar esta página y volver desde la lista de exportaciones.`: `puedes` (tú) with an impersonal verb earlier in the same message.
8. L205 `Ya tenías una importación en curso en este concurso; este es su progreso. El archivo que acabas de enviar no se importó.`: past tú (`tenías`, `acabas`); `su` refers to the import.
9. L552 `trátalo como información confidencial.`: attached object pronoun.
10. L419 `No se aplicó nada. Corrige estos errores y vuelve a subir el archivo:`: impersonal passive for the status, imperative tú for the action.

Impersonal forms (`se`, `hay`, `no se pudo`) are allowed for statuses and errors, as in
"No se pudo iniciar sesión." Do not use `usted` ("ingrese", "seleccione", "presione"),
even for the buttons. The checker flags those forms (REGISTER).

Note: the contestant catalog `cms.po` mixes `usted` ("Su pregunta ha sido recibida",
"Inténtelo", "envíe") with `tú` ("Tu nombre de usuario es"). This guide does not change
`cms.po`; AWS uses `tú` only.

## 2. Glossary

Columns: # | English | Spanish to use | Source and decision. `CONFLICT` marks a term where
catalogs or docs disagree; `DECIDED` marks the choice made here. The table has 137 rows and
`aws-es-glossary.json` has about 135 rules with the regular expressions the checker uses. Both come
from one list, but I did not cross-check them by script (no shell), so a few rows may be missing from the JSON.

### 2.1 Product terms and conflicts

| # | English | Spanish | Source and decision |
|---|---|---|---|
| 1 | contest | concurso / concursos | CONFLICT. aws.po uses concurso (30 uses). cms.po and the manual say competencia. DECIDED concurso in AWS only |
| 2 | contestant | concursante | manual glossary, aws.po. Replaces "participants" in the export help |
| 3 | participation | participación | cms.po, aws.po |
| 4 | user | usuario | CONFLICT. cms.po Username is "Nombre de usuario". DECIDED usuario |
| 5 | username (prose, login label) | usuario | CONFLICT. aws.po translates it in 15 places but keeps it in one help text. DECIDED usuario. The literal key "(username)" stays |
| 6 | user test | user tests (unchanged) | CONFLICT. Admin UI shows "User tests" in English; manual says prueba de usuario; cms.po says "tests provistos por el usuario". DECIDED to keep English, as the brief says |
| 7 | team | equipo | aws.po, cms.po |
| 8 | group | grupo | aws.po |
| 9 | main group | grupo principal | aws.po |
| 10 | ranking group | grupo de ranking | manual, aws.po |
| 11 | ranking | ranking | manual, aws.po (kept) |
| 12 | scoreboard | marcador | CONFLICT. Manual merges scoreboard and ranking; aws.po splits them. DECIDED marcador = public page, ranking = data |
| 13 | root ranking | ranking raíz | aws.po |
| 14 | token | token | cms.po, manual |
| 15 | dataset | dataset (unchanged) | manual |
| 16 | testcase | caso de prueba | manual |
| 17 | subtask | subtarea | cms.po, manual |
| 18 | task | problema | cms.po, manual (task = problema) |
| 19 | problem (an issue, not a task) | falla | manual: "problema" means task. Use "falla" for an issue |
| 20 | submission | envío | cms.po, aws.po |
| 21 | evaluation | evaluación | cms.po, manual |
| 22 | compilation | compilación | cms.po, manual |
| 23 | score | puntaje | CONFLICT. cms.po uses puntaje and puntuación. DECIDED puntaje |
| 24 | points | puntos | cms.po |
| 25 | announcement | anuncio | cms.po |
| 26 | question | pregunta | cms.po |
| 27 | answer | respuesta | cms.po |
| 28 | statement | enunciado | cms.po, manual |
| 29 | attachment | adjunto | cms.po |
| 30 | language (interface) | idioma | aws.po "Idioma" |
| 31 | programming language | lenguaje de programación | manual, cms.po. Never "idioma" for a programming language |
| 32 | worker | worker (unchanged) | manual |
| 33 | queue | cola | manual |
| 34 | container | contenedor | manual |
| 35 | dependency | dependencia | manual |
| 36 | deployment | despliegue | manual |
| 37 | backup | respaldo | manual. backup.sh and pg_dump stay |
| 38 | restore | restaurar | manual |
| 39 | dump | volcado | aws.po |
| 40 | screening | filtro | manual |
| 41 | two-phase | en dos fases | manual ("Evaluación en dos fases") |
| 42 | analysis mode | fase de análisis | CONFLICT. cms.po and manual say fase; aws.po says modo (3 times). DECIDED fase |
| 43 | running (contest) | en curso | aws.po |
| 44 | freeze | congelar | aws.po, manual. Time-field label stays "Congelar a las" |
| 45 | frozen | congelado | aws.po |
| 46 | unfreeze | descongelar | aws.po, manual |
| 47 | hide | ocultar | aws.po, manual |
| 48 | hidden | oculto | aws.po |
| 49 | show | mostrar | aws.po, manual |
| 50 | visible | visible | aws.po |
| 51 | archive (verb, state label) | archivar / archivado | manual; aws.po uses "Archivado" as a row label. A zip-file "Archive" needs msgctxt (section 3.6) |
| 52 | unarchive | desarchivar | manual, aws.po |
| 53 | staff | staff (unchanged) | manual, aws.po |
| 54 | staff password | contraseña de staff | aws.po uses "de staff" only. The brief's "del staff" does not occur |
| 55 | password | contraseña | CONFLICT. cms.po leaves "Password" in English. DECIDED contraseña |
| 56 | password of the day | contraseña del día | aws.po |
| 57 | log in / Login | iniciar sesión | CONFLICT. cms.po says "Acceder". DECIDED iniciar sesión |
| 58 | log out / Logout | cerrar sesión | CONFLICT. cms.po says "Salir" (ambiguous). DECIDED cerrar sesión |
| 59 | name | nombre | aws.po. CONFLICT with cms.po "Título" for a task name; both correct in their context |
| 60 | first name | nombre | aws.po |
| 61 | last name | apellido / apellidos | aws.po |
| 62 | description | descripción | aws.po |
| 63 | status | estado | cms.po |
| 64 | state | estado | aws.po |
| 65 | time (clock) | hora | aws.po. "a time" = "por vez" |
| 66 | time limit | límite de tiempo | manual, cms.po. Never "tiempo límite" |
| 67 | memory limit | límite de memoria | cms.po |
| 68 | limit | límite | cms.po |
| 69 | local time | hora local | aws.po |
| 70 | end (of a period) | fin / termina | cms.po |
| 71 | start | inicio / comienza | cms.po |
| 72 | row (CSV) | fila | CONFLICT. aws.po uses fila 13 times and renglón 2 times. DECIDED fila |
| 73 | column | columna | aws.po |
| 74 | cell | celda | aws.po |
| 75 | file | archivo | aws.po |
| 76 | hour | hora / horas | aws.po |
| 77 | minute | minuto | general |
| 78 | second | s (abbrev.) | aws.po "40 s" |
| 79 | error | error | aws.po |
| 80 | warning | atención | aws.po "Atención:" |
| 81 | notice | aviso | aws.po |
| 82 | done | listo | aws.po "Listo." |
| 83 | failed | falló | aws.po. The English "Failed to log in." becomes "No se pudo iniciar sesión." |
| 84 | ready | lista | aws.po |
| 85 | ignored | ignorado / ignorados | aws.po |
| 86 | unassigned | sin asignar | aws.po |
| 87 | not set | sin definir | aws.po |
| 88 | set (state) | definida | aws.po |
| 89 | never | nunca | aws.po |
| 90 | next | siguiente | aws.po |
| 91 | now | ahora | aws.po |
| 92 | empty | vacío / vacía; "vaciar" for "empty" as a verb | aws.po |
| 93 | choose / pick | elige; "elegir" in infinitive contexts | aws.po |
| 94 | type (to confirm) | escribe | aws.po |
| 95 | tick (a box) | marca | aws.po |
| 96 | select all | seleccionar todo | aws.po |
| 97 | selected | seleccionados / seleccionadas | aws.po |
| 98 | import | importar / importación | aws.po |
| 99 | export | exportar / exportación | aws.po |
| 100 | download | descargar | aws.po, cms.po |
| 101 | upload | subir | aws.po "vuelve a subir" |
| 102 | confirm | confirmar | aws.po |
| 103 | Continue? | ¿Continuar? | aws.po |
| 104 | cancel | cancelar | aws.po |
| 105 | update (button) | actualizar | aws.po |
| 106 | edit | editar | aws.po |
| 107 | delete (destroy) | borrar | aws.po. Never eliminar. DECIDED |
| 108 | remove (detach) | quitar | aws.po. Quitar = detach a link, Borrar = destroy. See section 5 for the ranking-group exception |
| 109 | reset (clear a form) | limpiar | CONFLICT. cms.po says Borrar. DECIDED limpiar: it clears the form, it deletes nothing |
| 110 | review | revisar | aws.po |
| 111 | validate | validar | aws.po "Solo validar" |
| 112 | preview | vista previa | aws.po |
| 113 | processing | procesando | aws.po |
| 114 | exporting | exportando | aws.po |
| 115 | checking (state) | consultando | aws.po |
| 116 | packing | empaquetando | aws.po |
| 117 | regenerate | regenerar / regeneración | aws.po |
| 118 | overview (AWS) | resumen | CONFLICT. cms.po "Carátula" is the contestant page. DECIDED resumen |
| 119 | details | detalles | cms.po |
| 120 | result | resultado | cms.po |
| 121 | log (noun) | registro | manual; the AWS label "Logs" stays |
| 122 | activity | actividad | aws.po ("registro de actividad") |
| 123 | session | sesión | general |
| 124 | permission | permiso | aws.po |
| 125 | admin | Admin (UI label, unchanged) / administrador (prose) | aws.po keeps "Admin" |
| 126 | schedule | programación | manual |
| 127 | window (time window) | ventana | manual |
| 128 | plain text | texto plano | aws.po |
| 129 | hash (passwords) | cifrar / cifradas | aws.po |
| 130 | confidential | confidencial | aws.po |
| 131 | theme | tema | aws.po |
| 132 | system (theme) | sistema | aws.po |
| 133 | light (theme) | claro | aws.po |
| 134 | dark (theme) | oscuro | aws.po |
| 135 | welcome | bienvenida | aws.po |
| 136 | automatic | automático | aws.po |
| 137 | UTC | UTC (unchanged) | aws.po |

### 2.2 Terms that stay in English on purpose

- `username` only as the literal key in parentheses, "(username)", and in code. Prose uses "usuario".
- `user tests` (DECIDED, section 2.1 item 6). Keep the English words in every message; do not
  translate `(user_tests)` placeholders.
- `dataset`, `worker`, `staff`, `Admin` (as UI label), `token`, `ranking` (used as a Spanish
  word), `UTC`, `MB`, `GB`, `bytes`, `.zip`, `CSV`, `URL`, `RWS`, `AWS`, `ProxyService`,
  `Admin Web Server`, `Contest Web Server`, `Ranking Web Server`, `Evaluation Service`.
- Code and tools: `cmsDumpExporter`, `cmsDumpImporter`, `backup.sh`, `pg_dump`, `temp_dir`,
  `logs.sh`.
- CSV keys and column headers: `username`, `first_name`, `last_name`, `password`, `team`,
  `group`, `first_name (first_name)` labels keep the key in parentheses.
- Data formats and placeholders: `YYYY-MM-DD HH:MM:SS`, `/<name>/`, `<group>`.
- Native language names (Español, English), if any appear.
- Export flags: `-F`, `-f`, `-G`, `-S`, `-U`, `-X` (keep them inside `<code>`).

### 2.3 Words that the English admin pages use but the catalogs do not translate yet

These appear in the admin templates without wrapping. Use the glossary above when wrapping:
"Remove" (ranking-group page title), "Overview", "Settings", "Administration",
"Contest configuration", "Allowed localizations", "Ranking group" (as a select label),
"Active" (contest flag), "Submissions" (contest page), "Time limit".

## 3. UI rules

3.1 Button labels: infinitive, as in "Guardar", "Importar", "Cancelar", "Iniciar sesión", "Borrar",
"Archivar ahora". Imperatives belong in prose, not in buttons.

3.2 Column headers and row labels: nouns or adjectives, no verb. "Nombre", "Estado",
"Concursos", "Archivado", "Oculto". Time-field labels follow the existing pattern
"<verb> a las" ("Congelar a las", "Archivar a las"), and "Ocultar desde" stays as it is.

3.3 Confirmations: a question with ¿ and ?, short: "¿Continuar?", "¿Vaciar y regenerar el ranking %s?".
The English question mark is not optional.

3.4 Error sentences: say what failed, then what to do, in tú imperative. Field errors are
lowercase fragments after a row prefix ("fila %d: el usuario %s está repetido (fila %d)").
Notification subjects are sentence case ("No se leyó la lista"). Tú for the action.

3.5 Placeholders:
- Keep every placeholder. Same count, same spelling. The checker reports PLACEHOLDER.
- Named `%(name)s` may be reordered; positional `%s`, `%d`, `%r` must keep their order (PLACEHOLDER-ORDER).
- Plural pairs use `%(count)s` in both forms. Count placeholders are written by Babel from
  `{{ count }}` in `{% trans %}` blocks; never rename a variable without renaming its placeholder.
- Keep `%r` as a repr, not translated.

3.6 Disambiguation with msgctxt: "Archive" is a row label (ranking state, "Archivado") and,
in the upstream template `add_testcases.html`, the label of a zip-file upload ("Archivo").
Give the upload label its own msgctxt when it is wrapped. Same for "Remove": the ranking-group
button and the participation list use the same msgid (section 5).

3.7 Plurals: nplurals=2, plural=(n != 1). Zero takes the plural form ("0 usuarios"). Write the
singular form with the singular noun and the plural form with the plural noun and agreement:
"%(count)s participación perderá su equipo" / "%(count)s participaciones perderán su equipo".
Use "se borró" / "se borraron" with the count, as the catalog does.

3.8 Capitalization: sentence case for labels, buttons, titles and notifications. Lowercase only
for fragments that follow a prefix or sit inside a list ("fila %d:", "no existe"). Never title case.

3.9 Punctuation:
- Ellipsis is "…" (U+2026), never "...". Used in "Exportando…", "Empaquetando el .zip…".
- Spanish inverted marks: "¿…?" and "¡…!" whenever the English is a question or exclamation.
- No space before ":" ";" "!" "?" ".". The English colon becomes a Spanish colon with no space before it.
- Decimal numbers do not occur; use "s" for seconds ("40 s") and keep the space before units ("2 MB").
- Units: MB, GB, bytes, s, UTC are kept as they are.

3.10 Quotes: UI labels quoted inside prose take «» ("Un botón «… ahora» reemplaza…").
Code-like tokens keep straight single quotes, as aws.po does ("'-' y '_'"). Never straight double quotes.

3.11 Tooltips (title attributes): full sentences, sentence case, final period, tú.
The upstream tooltips in `contest.html` are English and must be wrapped as ordinary text.

3.12 Placeholder text in inputs: keep the format string ("YYYY-MM-DD HH:MM:SS") untouched.

3.13 Markup: keep every HTML tag, its attributes and the content of `<code>` (identifiers,
flags, file names). Translate only the words between tags: "Solo la base de datos, sin archivos
(<code>-F</code>)". Keep entities (`&lt;`, `&gt;`) as they are. The checker compares tags
and entities (MARKUP). Do not treat `<name>` in a placeholder path as a tag.

3.14 Code identifiers, URLs, CSV keys, language names, flags such as `-f`: never translate.

## 4. Checker prototype

Location: `/tmp/claude-1000/-var-home-areslolxd-Documentos-cms/f53ae869-d35f-4a51-aca9-f0c076e8d093/scratchpad/panel/aws-es-check.py`
(reads `aws-es-glossary.json` from the same directory). Stdlib only, so the repo venv is not needed.

Run it:

    python3 -I /tmp/claude-1000/-var-home-areslolxd-Documentos-cms/f53ae869-d35f-4a51-aca9-f0c076e8d093/scratchpad/panel/aws-es-check.py /var/home/areslolxd/Documentos/cms/.worktrees/aws-es/cms/locale/es/LC_MESSAGES/aws.po
    python3 -I /tmp/claude-1000/-var-home-areslolxd-Documentos-cms/f53ae869-d35f-4a51-aca9-f0c076e8d093/scratchpad/panel/aws-es-check.py --selftest

What it checks, one code per finding:
- GLOSSARY: the msgid contains an English term and the msgstr does not use the decided
  Spanish (or uses a forbidden form). Exemptions for code words (backup.sh, "first name").
- PLACEHOLDER, PLACEHOLDER-ORDER: placeholders missing, extra or reordered (positional only).
- MARKUP: HTML tags (code, b, i, strong, ...) and entities differ.
- SPACE: leading/trailing or double spaces, space before punctuation.
- PUNCT-END: final punctuation differs (., :, ?, !, …).
- INVERTED: ? or ! without ¿ or ¡.
- ELLIPSIS: "..." instead of "…". QUOTES: straight double quotes.
- REGISTER: usted forms (usted, ingrese, seleccione, presione, pulse, ...).
- CASE: first letter capitalization differs (skipped when the message starts with a placeholder).
- FUZZY, EMPTY, PLURAL-FORMS, SAME-AS-SOURCE (allowlist: Admin, Error %(status_code)s, Error: %(error)s).

Not checked: the "Remove" ranking-group exception (section 5), msgctxt, the meaning of a
sentence, and anything outside aws.po.

Expected output on the current `aws.po` (derived by hand from the file, NOT executed):

    .../aws.po:229: [GLOSSARY] 'analysis mode' must be 'fase de análisis', found 'modo de análisis' (msgid: 'These contests are running or in their analysis mode: %s. The dump i')
    .../aws.po:379: [GLOSSARY] 'username' should be 'usuario' (msgid: 'One username per line, or a CSV whose first column is the username.')
    .../aws.po:388: [GLOSSARY] 'row' must be 'fila', found 'renglón' (msgid: 'One row per contestant: it creates the users that are missing and regi')
    .../aws.po:391: [GLOSSARY] 'row' must be 'fila', found 'renglón' (msgid: 'One row per user: it creates the accounts that are missing with their pass')
    .../aws.po:554: [GLOSSARY] 'contestant' should be 'concursante' (msgid: 'The dump may include more than you choose: <code>cmsDumpExporter</code> als')
    .../aws.po:597: [GLOSSARY] 'analysis mode' must be 'fase de análisis', found 'modo de análisis' (msgid: '(running or in analysis mode)')
    .../aws.po:621: [GLOSSARY] 'user test' must be 'user tests (unchanged)', found 'pruebas de usuario' (msgid: 'Do not export the user tests (<code>-U</code>)')
    .../aws.po:627: [GLOSSARY] 'analysis mode' must be 'fase de análisis', found 'modo de análisis' (msgid: 'There are contests running or in analysis mode. The dump is read wit')
    N entries checked; 8 findings (GLOSSARY=8)

The selftest is expected to print `selftest ok`. The 8 findings are the real
inconsistencies in section 5 (items a to e). The checker found no other problems by my reading,
so treat a different output as a checker bug until proven otherwise.

## 5. Inconsistencies in the four translated AWS pages, with fixes

Checked against aws.po and the glossary. Items a to e are the fixes the checker catches; f to h need code or a decision.

a. "modo de análisis" (L230, L598, L628) vs "fase de análisis" in cms.po and the manual.
   Fix: "fase de análisis" in the three msgstr.
b. "Un renglón por concursante" (L389) and "Un renglón por usuario" (L392) vs "fila %d" in the 13 error messages.
   Fix: "Una fila por concursante" and "Una fila por usuario".
c. "pruebas de usuario" (L622) vs "user tests" in the three other messages (L824, L825, L830, L831 and
   the "User tests" headers of the admin UI). Fix: "No exportar los user tests (<code>-U</code>)".
   Note: the brief said to keep "user tests"; the one translated instance is the outlier.
d. "Un username por línea, o un CSV cuya primera columna sea el username." (L380) vs "usuario" in every
   other row message. Fix: "Un usuario por línea, o un CSV cuya primera columna sea el usuario." The literal
   key "(username)" stays where it is.
e. "el registro de actividad de los participantes" (L555) vs "concursantes" for the person. Fix: "concursantes".
f. Remove / Quitar vs Borrar. The rule is consistent for participations (quitar = detach from the contest)
   and for users (borrar = delete). The exception is the ranking group. In `rankinggroup.py` the "Remove" operation
   (REMOVE = N_("Remove"), line 273) redirects to the remove page, and that page calls
   `sql_session.delete(group)` (line 431). So the "Quitar" button deletes a ranking group. Fix needs code:
   a separate msgid "Delete" for the ranking-group button (and the page title "Borrar grupo de ranking"), with the
   comparison at line 288 updated. This is not a .po edit.
g. "Contraseña del staff" (the brief's example): it does not occur. aws.po uses "de staff" in all five places.
   No fix needed; keep "contraseña de staff".
h. Source typo in an msgid: L542 "An error occured while the server was handling your request." ("occured" should
   be "occurred"). Fixing it changes the msgid and drops the existing translation to fuzzy; do it before the lanes start.

Cross-catalog differences that are not aws.po bugs (decided, listed so nobody "fixes" them by accident):
Contest (concurso vs competencia), Username (Usuario vs Nombre de usuario), Password (contraseña vs untranslated),
Login (Iniciar sesión vs Acceder), Logout (Cerrar sesión vs Salir), Reset (Limpiar vs Borrar), Overview (Resumen vs Carátula),
Score (puntaje vs puntuación in cms.po), register (tú vs usted in cms.po). Changing cms.po is outside Change C.

## 6. Decisions the user still has to make

1. Contest term across the product: concurso (AWS, chosen here) or competencia (cms.po and manual). Changing AWS to
   competencia touches about 30 strings; changing cms.po touches more.
2. "username" in prose: this guide uses "usuario" (the existing aws.po is inconsistent). Confirm, since the brief said to keep "username".
3. Ranking-group "Remove" becomes "Borrar" with a code change (item f). Confirm before a lane does it.
4. User tests stay English (section 2.1 item 6), as the brief says. Confirm.
5. Order: wrapping the templates (job A) must happen before translation (job B), in its own reviewed batch.

## 7. Lane process (short)

- One owner of this guide and of the glossary. Any new term is added to `aws-es-glossary.json` first, then used.
- Split lanes by file, not by term: a lane owns whole templates or handler modules, so the same message is not translated twice.
- Each lane runs the checker on its output and leaves zero GLOSSARY, PLACEHOLDER, MARKUP and REGISTER findings.
- Before merging, run the checker on the merged `aws.po`, grep the new msgstr for "usted", "renglón", "modo de análisis",
  and "pruebas de usuario", and recompile `aws.mo` with the command the repo uses (verify it in the Makefile or setup.cfg).
- Render the English admin pages after the wrapping step and compare them with the original HTML, so the wrapping
  does not change the English output.
