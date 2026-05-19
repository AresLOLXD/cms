# Rekarel

This fork bundles two Karel tools directly inside the Docker image — no
installation required.

| Tool | What it is | Source |
|------|-----------|--------|
| `rekarel` | Compiler for the Karel programming language (Node.js CLI) | [@rekarel/cli](https://github.com/kishtarn555/rekarel-js) |
| `karel` | Karel interpreter written in C++ (statically linked binary) | [rekarel-cpp-interpreter v2.3.1](https://github.com/kishtarn555/rekarel-cpp-interpreter) |

Both tools are in PATH inside the container and are available to CMS workers
when evaluating Karel submissions.

## Verifying the tools are available

Run a shell inside the running container and check both tools:

```bash
docker compose -f docker/docker-compose.prod.yml --env-file .env \
    exec cms bash -c "rekarel --version && karel"
```

Expected output: the rekarel version string followed by the karel interpreter
usage message.

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
| `rekarel-cpp-interpreter` | v2.3.1 |

To pin `@rekarel/cli` to a specific version, modify the `RUN npm install -g`
line in the `rekarel-builder` stage of `Dockerfile` and rebuild.
