# Importing users and participations

The Admin Web Server (AWS) can load the contestants of a contest from one CSV
file. For each row it creates the user if it does not exist and registers the
user in the contest with that day's password. It replaces
[CMS-Loader](cms-loader.md), which will be removed after 2026-10-10.

The import page is in Spanish. The texts below are quoted as they appear on
the page.

## Where

**Contest → Users → "Importar CSV"**

The link is only shown to admins with full permission. Other admins do not see
it and cannot open the page.

## The file

- The **first row must be the header row**, with the name of each column. A
  file without one does not work: the first contestant would be read as the
  header.
- **UTF-8**, with commas or semicolons between the cells. Both work.
- In Excel, save it as **"CSV UTF-8"**. A plain "CSV" is not UTF-8, and the
  page answers "el archivo no está en UTF-8".
- At most **2 MB** and **5000 rows** per file. For more contestants, use
  several files.
- One file per contest, imported from that contest's page.

| Field | Required | What it holds | Headers recognised automatically |
|-------|----------|---------------|----------------------------------|
| `username` | yes | The login. It is unique in the whole of CMS. | `username`, `usuario`, `user` |
| `first_name` | yes | First name | `first_name`, `nombre`, `nombres` |
| `last_name` | yes | Last name | `last_name`, `apellido`, `apellidos` |
| `password` | yes | The day password (see below). At most 72 bytes. | `password`, `contraseña`, `contrasena`, `clave` |
| `team` | no | The team code, for example `JAL`. | `team`, `equipo`, `estado` |
| `group` | no | The name of a group of this contest. | `group`, `grupo` |

Headers are matched without capitals or accents. When you choose the file, the
page shows one selector per field, filled in with the matching column. If your
headers are different, pick the right column yourself. Required fields are
marked with `*`; **"(sin asignar)"** means no column. A column cannot be used
for two fields.

Cells are stripped of surrounding spaces, except the password, which is used
exactly as it is typed. The 72-byte limit counts bytes, not letters: a letter
with an accent takes two.

Example:

    username,first_name,last_name,password,team,group
    ana01,Ana,Pérez,Q7m2xKp9,JAL,
    beto02,Beto,López,Zt4wR8nc,JAL,
    carla03,Carla,Ruiz,h6Fy3LsB,,

## The day password

In OMI contests each contest day has its own password. In CMS that is the
**participation password**: the password a contestant has in one contest, which
takes priority over their account password there.

The `password` column is the participation password of this contest. Upload
**one file per contest day**, with that day's passwords.

New accounts get a random password that nobody knows, so only the day password
logs in.

## Import step by step

1. Open **Contest → Users → "Importar CSV"** and choose the file. Check that
   every selector has the right column.
2. Press **"Solo validar"**. It reads the whole file and writes nothing. If the
   file is fine, the page says "El archivo es válido. Al importarlo:" and
   counts the new and updated users and the new and updated participations.
   Check that the numbers are what you expect.
3. **Choose the file again.** The browser cannot keep the file between the two
   steps, so the page asks: "Vuelve a elegir el archivo para importarlo; se
   conservan las columnas asignadas." Your column choices are kept, except the
   password column, which is guessed again from the headers in the table above.
   **Check it before going on.**
4. Press **"Importar"**. A progress bar shows "Procesando X de N".
5. It ends with "Listo." and the counts: "Usuarios nuevos: …, actualizados: ….
   Participaciones nuevas: …, actualizadas: ….".

Passwords are hashed, which is slow: it takes about **30 s for every 300
rows**. You can close the tab. The import keeps running, and the same progress
link shows where it is.

## What the import does

- **New user:** created with the names of the row and a random account
  password.
- **Existing user** (same `username`): the first and last names are replaced by
  the file's. Nothing else about the user changes, and the account password is
  kept.
- **Participation:** if the user is not yet in the contest, they are added. If
  they are already in it, their participation is updated. In both cases the
  password, the team and the group are those of the row.
- Nothing else changes. The other participations of the user, and the users or
  participations that are not in the file, are left as they are. So are the
  other settings of a participation: Hidden participation, Unrestricted
  participation, IP address or subnet, Delay and Extra time.

To fix a mistake, correct the file and import it again: what already exists is
updated. Nothing is ever deleted.

> **Warning: empty cells replace.** An empty `team` cell **removes** the team
> of that participation, and an empty `group` cell puts it in the contest's
> **main group**. The same happens when `team` or `group` is left on
> "(sin asignar)", for example because the file has no such column. So
> uploading again a file **without the team column** clears the team of every
> contestant in it. Include the team and group columns in every file you
> upload.

### Teams and groups must exist first

The import does not create teams or groups. Create them before importing:

- Teams: **Teams** in the main menu, "(create new team...)". The `team` cell
  holds the team code.
- Groups: **Contest → Groups**. The `group` cell holds the name of a group of
  this contest.

Both must be written exactly as they are in AWS, capitals included. An unknown
team or group is an error.

## When there are errors

**All or nothing.** If anything is wrong, nothing is written, and the page says
"No se aplicó nada. Corrige estos errores y vuelve a subir el archivo:" with
the list of errors. Errors about one row start with "fila N:", where N is the
position of the row in the file, counting the header as row 1. Other errors are
about the file as a whole. No error ever shows a password.

Fix the file, choose it again and press **"Solo validar"** until it is valid.
Some errors only show once the others are fixed: for example, unknown teams and
groups are checked after the rest of the file.

| Error | What to do |
|-------|------------|
| "el archivo no está en UTF-8" | Save the file as "CSV UTF-8". |
| "el archivo pasa de 2 MB", "el archivo pasa de 5000 filas" | Split the file. |
| "falta asignar la columna para …" (followed by the field) | Pick the column in the selector of that field. |
| "la columna X está asignada a más de un campo" | Give each field its own column. |
| "fila N: el usuario está vacío" (also for `el nombre`, `el apellido` and "la contraseña está vacía") | Fill in the cell. |
| "fila N: el usuario X está repetido (fila M)" | Each username may appear only once per file. |
| "fila N: el equipo X no existe" | Create the team in **Teams**, or fix the code. |
| "fila N: el grupo X no existe en este concurso" | Create the group in **Contest → Groups**, or fix the name. |
| "fila N: la contraseña pasa de 72 bytes" | Use a shorter password. |

## Progress problems

- **Only one import per contest can run at once.** If you start another while
  one is running, the page says "ya hay una importación en curso para este
  concurso". Wait for the first to end.
- **A progress page lives 1 hour**, counted from the start of the import.
  After that, or if the link is not yours (only the admin who started an
  import can see its progress), the page says "No se encontró la
  importación…". The import may or may not have been applied. **Check the
  contest's Users list.** Do **not** re-run the import blindly: it would
  overwrite any change made since.
- **If AWS restarts during an import, nothing from that import is saved**:
  the database is only written at the very end, in a single step. Run the
  import again.
- "Error: …" instead of "Listo." means the import failed and nothing was saved.
  Run it again, and if it fails again, ask whoever manages the server to look
  at the AWS log.
- "No se pudo consultar el progreso; recarga la página." means the page lost
  contact with AWS. Reload the page.

## Keep the original file

Passwords are stored hashed, so they cannot be read back, and AWS has **no
download or export**. Keep the original file safe: nothing in AWS can give the
day passwords back.
