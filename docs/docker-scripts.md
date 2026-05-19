# Docker Scripts Guide

## Introduction

These six scripts handle everything needed to start, stop, and manage the contest system. Instead of typing one very long command with confusing flags (easy to mistype and hard to remember), you run a simple script. Each script does one thing and gives you clear feedback about what's happening.

## Before you start

- **Docker must be installed.** Check by opening a terminal and running `docker --version`. It should print a version number. If you see "command not found," install Docker from [https://docs.docker.com/get-docker/](https://docs.docker.com/get-docker/).

- **Create a `.env` configuration file.** Copy the example file with this command:
  ```bash
  cp .env.example .env
  ```
  Then open `.env` in a text editor (nano, gedit, VS Code, or whatever you normally use) and change every value marked `CHANGE_ME`. At minimum: `CMS_DB_URL` password, `POSTGRES_PASSWORD`, `CMS_SECRET_KEY`, `CMS_ADMIN_USER`, and `CMS_ADMIN_PASSWORD`.

## Quick start

Follow these steps to start the contest system for the first time:

1. **Open a terminal** in the folder where the scripts live (the project root).

2. **Run the startup script:**
   ```bash
   ./up.sh
   ```

3. **Answer the first question:** `Use local database (Docker)? [y/N]`
   - Answer **`y`** if you want these scripts to manage the database (most common for new setups).
   - Answer **`n`** only if you already have a PostgreSQL database running somewhere else.

4. **Answer the second question:** `Rebuild image? [y/N]`
   - Answer **`n`** (faster). Only answer **`y`** after updating the code.

5. **Wait for startup.** You'll see output like:
   ```
   [+] Running 4/4
    ✓ Container cms-prod-db-1           Created
    ✓ Container cms-prod-db-init-1      Created
    ✓ Container cms-prod-cms-1          Created
    ✓ Container cms-prod-cws-1          Created
   ```
   The system takes about 30-60 seconds to fully start. Services may not respond immediately.

6. **Check the status** once the script finishes:
   ```bash
   ./status.sh
   ```
   You should see several containers with status `Up`. If any say `Exit` or `Exited`, something went wrong — check `./logs.sh` for error messages.

7. **Open your browser** and try these URLs (assuming default ports):
   - **Contestants log in here:** [http://localhost:8888](http://localhost:8888)
   - **Contest administration:** [http://localhost:8889](http://localhost:8889)
   - **Public scoreboard:** [http://localhost:8890](http://localhost:8890)

If the pages don't load, the system might still be starting up. Wait another 30 seconds and try again, or run `./logs.sh` to see what's happening.

## Scripts reference

### up.sh

Starts the contest system. Asks two questions before starting: whether to use a local database and whether to rebuild the Docker image. Run this when the system is stopped.

```bash
./up.sh
```

### down.sh

Stops all running services. The data is preserved — you can run `./up.sh` again to start back where you left off.

```bash
./down.sh
```

### status.sh

Shows whether all services are running correctly. Lists each container (a lightweight virtual environment) with its current state. All should say `Up`.

```bash
./status.sh
```

### logs.sh

Shows a live stream of what every service is doing — useful for debugging problems. Press `Ctrl+C` to stop watching and return to the terminal prompt.

```bash
./logs.sh
```

### restart.sh

Stops and immediately starts the system again. Use this after editing the `.env` config file to apply changes, or if services seem stuck.

```bash
./restart.sh
```

### contest.sh

Changes which contest is currently active. It shows you the list of contests already in the system and lets you pick one by ID. After changing, it can automatically restart the services to apply the change.

```bash
./contest.sh
```

### clear-ranking.sh

Clears ranking data from the running container. Asks what to delete — results (submissions and subchanges), users, or tasks and contests — and whether to regenerate the ranking from the current contest data in the database. Only affects the scoreboard; contestant submissions and scores stored in PostgreSQL are never touched.

```bash
./clear-ranking.sh
```

If you choose to regenerate, `ProxyService` is restarted and will re-push all scored submissions to the ranking. Scores appear on the scoreboard within ~6 minutes.

## Configuring the project name

The `CMS_PROJECT_NAME` variable in `.env` (default: `cms-prod`) is used to group Docker containers on your machine. If you run only one copy of CMS, leave it as is. If you need to run two separate CMS setups on the same machine (for example, testing and production), change this to a different short name for the second one — something like `cms-test` or `cms-staging`. This prevents containers from different instances from conflicting. Keep it short and use lowercase letters and hyphens only.

## Troubleshooting

### The page doesn't load in my browser

The services might still be starting up — they can take 30-60 seconds to fully initialize. Run `./status.sh` to see if all containers say `Up`. If they do, wait a bit longer and try the page again. If some containers show `Exit` or `Exited`, run `./logs.sh` to see what error caused them to fail.

### contest.sh shows no contests

No contests have been imported yet. Use the Admin interface at [http://localhost:8889](http://localhost:8889) to create a new contest or import an existing one. Once you've imported a contest, run `./contest.sh` again and you should see it in the list.

### Permission denied when running a script

The script file doesn't have execute permission. Fix it by running:
```bash
chmod +x <script-name>.sh
```
For example: `chmod +x up.sh`. After this, you can run the script normally.
