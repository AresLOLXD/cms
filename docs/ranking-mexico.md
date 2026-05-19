# Ranking: Mexican State Flags and Auto-Team Registration

This fork ships two enhancements to the Ranking Web Server targeted at the OMI:

1. **Bundled flags** — real flag images for all 32 Mexican states, served
   automatically by the ranking server.
2. **Auto-team registration** — teams are created from the flag images found
   in `lib_dir/flags/` every time the ranking server starts.

## How auto-team registration works

On startup, `cmsRankingWebServer` calls `seed_flags_and_teams`, which:

1. Copies the 32 bundled state flag PNG files into `lib_dir/flags/` (default:
   `.venv/lib/ranking/flags/`), without overwriting files you have placed there
   manually.
2. Scans `lib_dir/flags/` for image files (`.png`, `.jpg`, `.gif`, `.bmp`) and
   creates a **team** entry for each filename stem not yet registered.

The team name is resolved from the filename stem: if it matches a known Mexican
state code (e.g. `JAL`) the full state name is used (`Jalisco`); otherwise the
stem itself becomes the team name.

Teams are only *created*, never automatically updated or deleted. Once a team
exists it can be renamed or modified through the admin interface without losing
changes on the next restart.

## State codes

| Code | State | Code | State |
|------|-------|------|-------|
| AGU | Aguascalientes | MOR | Morelos |
| BCN | Baja California | NAY | Nayarit |
| BCS | Baja California Sur | NLE | Nuevo León |
| CAM | Campeche | OAX | Oaxaca |
| CHP | Chiapas | PUE | Puebla |
| CHH | Chihuahua | QUE | Querétaro |
| CMX | Ciudad de México | ROO | Quintana Roo |
| COA | Coahuila | SLP | San Luis Potosí |
| COL | Colima | SIN | Sinaloa |
| DUR | Durango | SON | Sonora |
| GUA | Guanajuato | TAB | Tabasco |
| GRO | Guerrero | TAM | Tamaulipas |
| HID | Hidalgo | TLA | Tlaxcala |
| JAL | Jalisco | VER | Veracruz |
| MEX | Estado de México | YUC | Yucatán |
| MIC | Michoacán | ZAC | Zacatecas |

## Replacing a bundled state flag

Replace the file at `lib_dir/flags/<CODE>.png` while the server is running.
The new image is served immediately — no restart required.

To make the replacement permanent across server restarts, also replace the
source file inside the package installation:

```
<venv>/lib/python3.x/site-packages/cmsranking/flags/<CODE>.png
```

## Adding a custom team with a flag

1. Place an image file at `lib_dir/flags/<YOUR_CODE>.png`.
2. Restart `cmsRankingWebServer`.
3. A team with key `YOUR_CODE` is created automatically. To set a friendlier
   display name, edit the team from the admin interface.

## Regenerating flag images from Wikimedia Commons

If you want to refresh the bundled flags from their original Wikimedia Commons
sources, use the contrib script:

```bash
.venv/bin/python3 cmscontrib/DownloadMexicanStateFlags.py
```

The script downloads each flag at 160 px width, resizes to exactly 160×100 px,
and overwrites `cmsranking/flags/<CODE>.png`. Requires the optional `Pillow`
dependency:

```bash
pip install "cms[contrib]"
```

## Custom logo

To replace the default CMS logo shown in the ranking server, see the
[Custom logo](RankingWebServer.rst#custom-logo) section in
`docs/RankingWebServer.rst`.
