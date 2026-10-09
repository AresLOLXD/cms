# Importing users and participations

The Admin Web Server (AWS) can load the contestants of a contest from one CSV
file. For each row it creates the user if it does not exist and registers the
user in the contest with that day's password. It can also create the user
accounts alone, with their own password, from the global Users list (see
"Importing accounts" below). It replaces [CMS-Loader](cms-loader.md), which
will be removed after 2026-10-10.

The import page is in Spanish. The texts below are quoted as they appear on
the page.

## Where

**Contest → Users → "Importar CSV"**, to register the contestants of a contest.

**Users → "Importar CSV"**, to create or update user accounts alone (see
"Importing accounts" below).

Both links are only shown to admins with full permission. Other admins do not
see them and cannot open the pages.

## The file

- The **first row must be the header row**, with the name of each column.
  Without one, no selector is filled in and "Solo validar" usually answers
  "falta asignar la columna para …". If you then pick the columns by hand, the
  first contestant is taken as the header and silently not imported, so check
  the counts of **"Solo validar"**.
- **UTF-8**, with commas or semicolons between the cells. Both work.
- In Excel, save it as **"CSV UTF-8"**. A plain "CSV" saves accented letters in
  another encoding, and the page then answers "el archivo no está en UTF-8".
  This only happens when the file has accented or other non-ASCII characters.
- At most **2 MB** (2 MiB, 2 × 1024 × 1024 bytes) and **5000 rows** of data per
  file. The header and completely blank rows do not count. For more
  contestants, use several files.
- One file per contest, imported from that contest's page.

| Field | Required | What it holds | Headers recognised automatically |
|-------|----------|---------------|----------------------------------|
| `username` | yes | The login. It is unique in the whole of CMS. Only letters A-Z a-z, digits, `_` and `-` (no spaces, dots, accents or `@`). Capitals matter: `Ana01` and `ana01` are different users. | `username`, `usuario`, `user` |
| `first_name` | yes | First name | `first_name`, `nombre`, `nombres` |
| `last_name` | yes | Last name | `last_name`, `apellido`, `apellidos` |
| `password` | no | The day password (see below). At most 72 bytes. Without this column, see "Without a password column" below. | `password`, `contraseña`, `contrasena`, `clave` |
| `team` | no | The team code, for example `JAL`. | `team`, `equipo`, `estado` |
| `group` | no | The name of a group of this contest. | `group`, `grupo` |

Headers are matched without capitals or accents. When you choose the file, the
page shows one selector per field, filled in with the matching column. Each
selector is labelled in Spanish with the name of the field in brackets, for
example "Usuario (username)" for `username` and "Contraseña del día (password)"
for `password`. If your headers are different, pick the right column yourself.
Required fields are marked with `*`, and the page explains it with the legend
"* obligatorio". **"(sin asignar)"** means no column. A column can fill several
fields, for example one column used as both the team and the group. Only the
password column cannot be used for any other field.

Cells are stripped of surrounding spaces, the password included, because CWS
strips what the contestant types at the login. A password may have spaces
inside, but no control characters, not even a tab or a line break. The 72-byte
limit counts the bytes left after stripping, not letters: a letter with an
accent takes two.

Example:

```text
username,first_name,last_name,password,team,group
ana01,Ana,Pérez,Q7m2xKp9,JAL,
beto02,Beto,López,Zt4wR8nc,JAL,
carla03,Carla,Ruiz,h6Fy3LsB,,
```

## The day password

In OMI contests each contest day has its own password. In CMS that is the
**participation password**: the password a contestant has in one contest, which
takes priority over their account password there.

The `password` column is the participation password of this contest. Upload
**one file per contest day**, with that day's passwords.

New accounts get a random account password that nobody knows, so only the day
password logs in. To give the contestants an account password instead, import
the accounts first on **Users → "Importar CSV"** (see "Importing accounts"
below), then import the contest without a password column.

### Without a password column

Leave the password selector on **"(sin asignar)"** to register users who
already have an account password:

- A new participation gets no password of its own, so the contestant logs
  in with the account password.
- An existing participation keeps its password, whatever it is.
- Every user in the file must already exist. Otherwise the row gets
  "fila N: el usuario X no existe; sin columna de contraseña solo se pueden
  inscribir usuarios que ya existen" and nothing is applied: a new account
  would get a random password and could never log in.

When the file has a column whose header is one of those in the table above
(`password`, `contraseña`, `contrasena`, `clave`), the page assigns it the
first time you choose the file. Set the password selector to "(sin asignar)"
before "Solo validar". After that, the page keeps it unassigned when you
choose the file again (step 3 of "Import step by step") or after errors.
Still check it before pressing "Importar": otherwise the file's passwords
replace the participation passwords.

## Import step by step

1. Open **Contest → Users → "Importar CSV"** and choose the file. Check that
   every selector has the right column.
2. Press **"Solo validar"**. It reads the whole file and writes nothing. If the
   file is fine, the page says "El archivo es válido. Al importarlo:" and
   counts the new and updated users and the new and updated participations.
   Check that the numbers are what you expect. On a fresh contest, "Usuarios
   actualizados" above 0 means those usernames already exist in CMS, and their
   first and last names will be overwritten.

   If the import would also clear teams or change groups, a highlighted
   warning follows the counts, one line for each thing that would happen:
   "N participaciones perderán su equipo" and "N participaciones pasarán al
   grupo principal" (with one participation: "1 participación perderá su
   equipo" and "1 participación pasará al grupo principal"). A line shows only
   when its number is above 0. The first counts the participations that are
   already in this contest with a team, whose row has an empty `team` cell or
   "(sin asignar)". The second counts those that are in a group other than the
   main one, whose row has an empty `group` cell or "(sin asignar)". See the
   warning about empty cells below. If it is not what you want, fix the file
   before importing.
3. **Choose the file again.** The browser cannot keep the file between the two
   steps, so the page asks: "Vuelve a elegir el archivo para importarlo. Se
   conservan las columnas asignadas, salvo la de la contraseña: revísala." Your
   column choices are kept, except the password column, which is guessed again
   from the headers in the table above, unless you left it "(sin asignar)",
   which is kept. **Check it before going on.**
4. Press **"Importar"**. A progress bar shows "Procesando X de N". Both buttons
   are disabled after the first click, so a double click does not send the
   file twice.
5. It ends with **"Listo."**, in bold, and the counts: "Usuarios nuevos: …,
   actualizados: …. Participaciones nuevas: …, actualizadas: ….". If the import
   cleared teams or moved participations to the main group, the same lines as
   in the validation follow, in the past tense: "N participaciones perdieron su
   equipo" and "N participaciones pasaron al grupo principal" (with one
   participation: "1 participación perdió su equipo" and "1 participación pasó
   al grupo principal"). Two links close the page: "Lista de usuarios", the
   Users list of the contest, and "Importar otro archivo", the import page
   again with no file chosen. "Procesando N de N" can show before "Listo.",
   because saving comes after the hashing. Wait for "Listo."; only then is
   everything saved.

Passwords are hashed, which is slow. A new user needs two hashes and an
existing user one. On a 4-core server, 300 new users take about **40 s** and
300 existing users about **20 s**. A 5000-row file of new users takes about 10
minutes, and fewer cores are slower. You can close the tab. The import keeps
running, and the same progress link shows where it is.

**Avoid importing during a running contest.** The hashing keeps 4 cores busy
for a while (about **25 s for 190 new users**) on the server that also runs the
contestants' submissions and logins. Import before the contest starts.

## What the import does

- **New user:** created with the names of the row and a random account
  password.
- **Existing user** (same `username`): the first and last names are replaced by
  the file's. Nothing else about the user changes, and the account password is
  kept.
- **Participation:** if the user is not yet in the contest, they are added. If
  they are already in it, their participation is updated. In both cases the
  password, the team and the group are those of the row. The exception is a
  password left on "(sin asignar)": a new participation then gets no password
  of its own and an existing one keeps its password (see "Without a password
  column" above).
- Nothing else changes. The other participations of the user, and the users or
  participations that are not in the file, are left as they are. So are the
  other settings of a participation: Hidden participation, Unrestricted
  participation, IP address or subnet, Delay and Extra time.

To fix a mistake, correct the file and import it again: what already exists is
updated. Nothing is ever deleted. A contestant whose password in the file did
not change stays logged in to CWS. A contestant whose password changed is
logged out, and only that contestant: they log in again with the new password.

> **Warning: empty cells replace.** An empty `team` cell **removes** the team
> of that participation, and an empty `group` cell puts it in the contest's
> **main group**. The same happens when `team` or `group` is left on
> "(sin asignar)", for example because the file has no such column. So
> uploading again a file **without the team column** clears the team of every
> contestant in it. Include the team and group columns in every file you
> upload.
>
> The page warns about it in three places: a note beside the "Equipo (team)"
> and "Grupo (group)" selectors ("Vacío o sin asignar: la participación se
> queda sin equipo / en el grupo principal."), and the lines "N participaciones
> perderán su equipo" and "N participaciones pasarán al grupo principal" after
> "Solo validar", which are repeated in the past tense after "Listo.".

### Teams and groups must exist first

The import does not create teams or groups. Create them before importing:

- Teams: click **Administration** (top of the sidebar), then **Teams** →
  "(create new team...)". Inside a contest the sidebar shows the contest menu,
  so the main menu only appears after that click. The `team` cell holds the
  team code.
- Groups: **Contest → Groups**, "Add a new group named" and "Add group". The
  `group` cell holds the name of a group of this contest.

Both must be written exactly as they are in AWS, capitals included. An unknown
team or group is an error.

## When there are errors

**All or nothing.** If anything is wrong, nothing is written, and the page says
"No se aplicó nada. Corrige estos errores y vuelve a subir el archivo:" with
the list of errors. Errors about one row start with "fila N:", where N is the
position of the row in the file, counting the header as row 1. Other errors are
about the file as a whole. No error shows the contents of the password column.

The list starts with the number of errors ("N errores", or "1 error" when there
is one) and shows only the **first 50**. If there are more, it ends with "y N
más". Fix those and validate again to see the next ones.

Fix the file, choose it again and press **"Solo validar"** until it is valid.
Some errors only show once the others are fixed: for example, unknown teams and
groups are checked after the rest of the file.

| Error | What to do |
|-------|------------|
| "el archivo no está en UTF-8" | Save the file as "CSV UTF-8". |
| "el archivo pasa de 2 MB", "el archivo pasa de 5000 filas" | Split the file. |
| "elige un archivo CSV" | Choose the file. |
| "el archivo está vacío" | The file has no content. Check that you chose the right file. |
| "el archivo no tiene filas de datos" | The file has no contestants below the header row. Blank rows do not count. |
| "el archivo no es un CSV válido" | The file cannot be read as CSV. Save it again as "CSV UTF-8". |
| "falta asignar la columna para …" (followed by the field) | Pick the column in the selector of that field. |
| "la columna de la contraseña está asignada a más de un campo" | Give the password its own column: no other field may use it. |
| "la columna X no está en el archivo" (for the password column: "la columna asignada a la contraseña no está en el archivo") | Pick the column again in the selector of that field, using the file you are uploading. |
| "fila N: el usuario está vacío", "fila N: el nombre está vacío", "fila N: el apellido está vacío", "fila N: la contraseña está vacía" | Fill in the cell. |
| "fila N: el usuario X está repetido (fila M)" | Each username may appear only once per file. |
| "fila N: el usuario X tiene caracteres no permitidos (solo letras sin acentos, números, _ y -)" | Change the username to use only those characters. |
| "fila N: el usuario X no existe; sin columna de contraseña solo se pueden inscribir usuarios que ya existen" | Import the account first on Users → "Importar CSV", fix the username if it is mistyped, or assign the password column. |
| "fila N: el equipo X no existe" | Create the team in **Teams**, or fix the code. |
| "fila N: el grupo X no existe en este concurso" | Create the group in **Contest → Groups**, or fix the name. |
| "fila N: la contraseña pasa de 72 bytes" | Use a shorter password. |
| "fila N: la contraseña tiene caracteres no permitidos" | The password has a control character, which is usually invisible and comes from a pasted cell. Type the password again. |
| "fila N: la celda del usuario tiene caracteres no permitidos" (or "del nombre", "del apellido", "del equipo", "del grupo") | The cell has a NUL character, which is invisible. Type the cell again. |
| "Hay una importación de usuarios en curso; espera a que termine." | Another admin is importing accounts. Wait and upload again. |

## Progress problems

- **Only one import per contest can run at once.** If you press "Importar"
  while an import of that contest that you started is still running (for
  example, after you left its progress page, or from another tab), the page
  takes you to the progress of that running import and says: "Ya tenías una
  importación en curso en este concurso; este es su progreso. El archivo que
  acabas de enviar no se importó." **The file you just sent was not
  imported**, and it may not be the one that is running. When that import
  shows "Listo.", import the file you sent. If the running import was started
  by another admin, the page says "Hay una importación en curso para este
  concurso; espera a que termine." This is a notice, not an error: the file is
  fine and nothing was applied by this request, so wait for the other import to
  end and import the file again. Choose the file again, as the page asks.
- **Import one contest at a time when the files share users**, for example day
  1 and day 2 of the same contestants. Imports into different contests can
  run at once, and they save one after the other, so no user is created
  twice. But if both files bring the same new users, the second import may
  count them in "Usuarios nuevos" although the first one created them: it
  only updates them.
- **A progress page lives 1 hour**, counted from the start of the import.
  After that, or if the link is not yours (only the admin who started an
  import can see its progress), the page says "No se encontró la importación:
  no existe o ya expiró. Revisa la lista de usuarios para ver si se aplicó."
  The import may or may not have been applied. **Check the contest's Users
  list.** Do **not** re-run the import blindly: it would overwrite any change
  made since.
- **If AWS restarts during an import**, AWS no longer knows the import: its
  status request answers 404, so the page shows "No se pudo consultar el
  progreso; recarga la página.", and after a reload it shows the "No se
  encontró la importación: …" notice above. Normally nothing from that import
  is saved, because the database is only written at the very end, in a single
  step. But if the restart came at that very end, the import may already be
  saved, so check the contest's Users list. If the users are not there, run the
  import again. Re-running the same file rewrites the same values and keeps
  the passwords that did not change, so it logs nobody out of CWS. It is
  harmless unless someone changed those users or participations since.
- "Error: …" instead of "Listo." means the import failed and nothing was saved.
  The same two links follow it: "Lista de usuarios" and "Importar otro
  archivo". Run it again, and if it fails again, ask whoever manages the
  server to look at the AWS log.
- "No se pudo consultar el progreso; recarga la página." means the page lost
  contact with AWS. The page retries first, so the message only appears after
  several failed attempts in a row. Reload the page. If AWS was restarted, see
  the bullet above.

## Keep the original file

Passwords are stored hashed, so they cannot be read back, and AWS has **no
download or export**. Keep the original file safe: nothing in AWS can give the
day passwords back.

## Importing accounts

**Users → "Importar CSV"** creates or updates user accounts with their
account password, and registers nobody in any contest.

| Field | Required | What it holds |
|-------|----------|---------------|
| `username` | yes | The login, with the same rules as above. |
| `first_name` | yes | First name |
| `last_name` | yes | Last name |
| `password` | yes | The account password. At most 72 bytes, no control characters. |

The file, the column selectors, "Solo validar", "Importar", the progress
bar and the errors work as in the contest import. Other columns, such as
`team` or `group`, are ignored.

- A **new user** is created with the row's names and password.
- An **existing user** gets the row's first name, last name and password.
  If the password did not change and the account password is stored as a
  bcrypt hash, the stored hash is kept, so a contestant logged in with it
  stays logged in. An account stored otherwise, for example one created by
  hand with a plaintext password, is hashed again once. Nothing else of the
  user changes, and no participation changes.

The account password only logs in to the contests where the participation
has no password of its own. To use it for a contest day, import the same
file in the contest with the password left "(sin asignar)" (see
"Without a password column" above).

## Removing users and participations

AWS can remove many users, or many participations, at once, for example to
clean up after a rehearsal or after importing the wrong file. Like the import
page, these pages are in Spanish, and their texts are quoted as they appear.

**A removal cannot be undone.** Before you remove anything:

- Take a backup with `./export.sh` (see [Docker Scripts Guide](docker-scripts.md)).
- Download the activity CSV of the contests concerned. Removing a
  participation deletes its activity log, and the backup does not include it
  (see [Participant activity](participant-activity.md)).
- Do not remove anything while a contest is running: the submissions made
  during the contest are lost with it.

Only admins with full permission can remove. For the others, the buttons are
disabled.

### Two scopes

| Page | What it removes |
|------|-----------------|
| **Users** in the main menu (the "Users list" page) | The users, from the whole platform: each account, with its participations in every contest. |
| A contest's **Users** page (**Contest → Users**) | Only the participations in that contest. The users stay, with their participations in other contests. |

To reach the main menu from inside a contest, click **Administration** at the
top of the sidebar.

### Choosing the users

Both pages offer two ways:

- **Checkboxes.** Tick the users in the table, or tick the checkbox in the
  table header (its tooltip says "Seleccionar todo") to tick them all. Then
  press **"Borrar seleccionados"** on the Users list, or **"Quitar
  seleccionados del concurso"** on a contest.
- **A list of usernames.** Under **"Borrar por lista"** (Users list) or
  **"Quitar por lista"** (contest), paste the usernames in the text box,
  choose a file, or both, and press **"Revisar lista"**. The usernames of the
  box come first, then those of the file.

The list, in the box or in the file:

- One username per line, or a CSV whose first column is the username, with
  commas, semicolons or tabs between the cells. The file of a user import
  works as it is when `username` is its first column.
- A first line whose first cell is `username`, in any case, is taken as the
  header and skipped. Any other header, such as `usuario`, is read as a
  username.
- UTF-8, with or without BOM. Spaces and double quotes around a username are
  removed, blank lines are skipped, and a username that appears twice counts
  once.
- Capitals matter, as in the import: `Ana01` and `ana01` are different users.
- At most 1 MB (1024 × 1024 bytes) for the box and the file together.

When the list cannot be read, the list page comes back with a "No se leyó la
lista" notification that says "la lista pasa de 1 MB" or "la lista no está en
UTF-8", and nothing is removed. With nothing ticked or listed, the
notification is "No elegiste ningún usuario.".

### The confirmation page

These buttons remove nothing yet. They open a page, "Borrar usuarios" (or
"Quitar participaciones de" and the contest name), that shows what would be
removed:

- "Se borrarán N usuarios, S envíos y T user tests." or "Se quitarán N
  participaciones de este concurso, con S envíos y T user tests.", followed by
  "Esta operación no se puede deshacer." On a contest, only the submissions and
  user tests of that contest are counted.
- A table with **Username**, **Nombre**, **Apellido**, **Envíos** and **User
  tests**. On the Users list it also has **Concursos**, the contests each user
  takes part in.
- **"Ignorados:"**, what is left out, each with its reason: "no existe" (no
  user has that username) or, on a contest, "no participa en el concurso" (the
  user exists but is not in this contest). They do not stop the rest.
- In red, when any participation concerned belongs to a contest that is
  running now: "Atención: hay un concurso en curso entre los afectados. Se
  perderán envíos hechos durante el concurso y el ranking se recalculará."
- "No hay nada que borrar." when nobody is left to remove. The page then has
  no button.

To confirm, type the number of users in "Escribe N para confirmar:" and press
**"Borrar"** (or **"Quitar"** on a contest). **"Cancelar"** goes back to the
list.

The page comes back, with nothing removed, when:

- the number typed is wrong: "El número escrito no coincide con el número de
  usuarios. No se borró nada.";
- the selection changed since the page was opened, for example because
  another admin removed one of those users: "La selección cambió desde la
  vista previa: revisa la lista y confirma otra vez. No se borró nada." It
  then shows the selection as it is now.

### What is removed

- **All or nothing.** Everything is removed in one database transaction. If
  it fails, nothing is removed and a notification says so ("No se borró
  nada" or "Operation failed.").
- With each participation go its submissions (with their files, results,
  evaluations and tokens), its user tests (with their files and results), its
  questions and messages, and its activity log. On the Users list, that is
  every participation of each user, and the account too.
- Teams and groups stay.
- On success, AWS goes back to the list with the notification "Se borraron N
  usuarios" ("Se borró 1 usuario") or "Se quitaron N participaciones" ("Se
  quitó 1 participación"). The AWS log records who removed which usernames.

**Then press Regenerate on the ranking group of each contest concerned**, or
on the **Root ranking** row when a single contest is served (see "When a
scoreboard is wrong: Regenerate" in
[Running several contests at once](multi-contest.md)). After a removal AWS
asks ProxyService to send the rankings again, but a scoreboard only adds and
updates what it receives: the removed contestants stay on it, with their
scores, until the group is regenerated.
