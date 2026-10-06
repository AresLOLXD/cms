# Rekarel

This fork bundles two Karel tools directly inside the Docker image — no
installation required.

| Tool | What it is | Source |
|------|-----------|--------|
| `rekarel` | Compiler for the Karel programming language (Node.js CLI) | [@rekarel/cli](https://github.com/kishtarn555/rekarel-js) |
| `karel` | Karel interpreter written in C++ (statically linked binary) | [rekarel-cpp-interpreter v2.3.2](https://github.com/kishtarn555/rekarel-cpp-interpreter) |

Both tools are in PATH inside the container and are available to CMS workers
when evaluating Karel submissions.

## Verifying the tools are available

Run a shell inside the running container and check both tools:

```bash
docker compose -f docker/docker-compose.prod.yml --env-file .env \
    exec cms bash -c "npm ls -g --depth=0 | grep rekarel && karel"
```

Expected output: the installed `@rekarel/cli` package version followed by the
karel interpreter usage message. Check the version with `npm ls` rather than
`rekarel --version`, which can report an older number than the installed
package.

## Using Karel as a task type

Karel tasks are evaluated using the standard **Batch** task type in CMS. The
task checker or manager calls `rekarel` (to compile the contestant's `.kp`
source) and `karel` (to run the compiled program against each test case).

Refer to the CMS documentation on
[task types](https://cms.readthedocs.io/en/latest/Task%20types.html) for the
full configuration.

## Versions bundled

| Tool | Version |
|------|---------|
| `@rekarel/cli` | latest at image build time (npm latest) |
| `rekarel-cpp-interpreter` | v2.3.2 |

To pin `@rekarel/cli` to a specific version, modify the `RUN npm install -g`
line in the `rekarel-builder` stage of `Dockerfile`. To move to a new
interpreter release, change the tag in the `rekarel-cpp-interpreter` download
URL in the same stage. Commit the change, then deploy it as described below.

## Updating rekarel on a running server

`@rekarel/cli` (npm latest) and the `cms_rekarel` language plugin (installed
with pip from its Git repository) are downloaded while the image is built, and
Docker caches those build steps. A normal rebuild therefore keeps the old
versions, so a new release needs a rebuild without cache:

1. Get the latest code: `git pull`.
2. Run `./up.sh` and, at the `Rebuild?` question, choose **`5`** (CMS only,
   no cache).
3. Run `./status.sh` and wait until every container says `Up`, then check the
   versions as shown in *Verifying the tools are available* above.

Don't choose **`4`** (CMS only): it reuses the cached steps and keeps the old
compiler and plugin. If the interpreter tag changed too, the image would end
up with a new interpreter and an old compiler.

The same steps apply to single-contest and multi-contest deployments: rekarel
lives only in the CMS image, so the ranking container doesn't need a rebuild.
Rebuilding restarts the CMS services, so don't do it during a contest. See
[Docker scripts](docker-scripts.md) for the other `./up.sh` options.
