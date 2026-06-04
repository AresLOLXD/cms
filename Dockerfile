# syntax=docker/dockerfile:1
# Supported base images: ubuntu:noble, debian:bookworm.
ARG BASE_IMAGE=ubuntu:noble

# ─── Stage 1: Build rekarel compiler (Node.js) and C++ interpreter ────────────
FROM ${BASE_IMAGE} AS rekarel-builder

RUN --mount=type=cache,target=/var/cache/apt,sharing=locked \
    --mount=type=cache,target=/var/lib/apt,sharing=locked <<EOF
#!/bin/bash -ex
    export DEBIAN_FRONTEND=noninteractive
    rm -f /etc/apt/apt.conf.d/docker-clean
    apt-get update
    apt-get install -y build-essential libexpat-dev curl ca-certificates
    curl -fsSL https://deb.nodesource.com/setup_20.x | bash -
    apt-get install -y nodejs
EOF

# Install rekarel compiler CLI.
# NodeSource on Ubuntu uses /usr as global prefix; override so the COPY
# commands in the runtime stage can reference /usr/local paths consistently.
RUN npm config set prefix /usr/local
RUN npm install -g @rekarel/cli

# Build the C++ Karel interpreter from source (no pre-built binaries for v2.3.1).
# The binary is statically linked (-static flag in the Makefile), so it has
# no runtime library dependencies and copies cleanly to the runtime stage.
RUN mkdir -p /build && \
    curl -fsSL "https://github.com/kishtarn555/rekarel-cpp-interpreter/archive/refs/tags/v2.3.1.tar.gz" \
        | tar xz --strip-components=1 -C /build && \
    cd /build && mkdir -p bin && make karel && \
    ldd bin/karel 2>&1 | grep -q "not a dynamic executable" || \
        { echo "ERROR: karel binary is not statically linked"; exit 1; }

# ─── Stage 2: Build CMS-Loader (Node.js/TypeScript) ──────────────────────────
FROM ${BASE_IMAGE} AS loader-builder

ARG CMS_LOADER_VERSION=main

RUN --mount=type=cache,target=/var/cache/apt,sharing=locked \
    --mount=type=cache,target=/var/lib/apt,sharing=locked <<EOF
#!/bin/bash -ex
    export DEBIAN_FRONTEND=noninteractive
    rm -f /etc/apt/apt.conf.d/docker-clean
    apt-get update
    apt-get install -y curl ca-certificates git
    curl -fsSL https://deb.nodesource.com/setup_20.x | bash -
    apt-get install -y nodejs
    npm install -g pnpm
EOF

RUN git clone --branch "${CMS_LOADER_VERSION}" --depth 1 \
        https://github.com/AresLOLXD/CMS-Loader.git /build && \
    cd /build && \
    pnpm install && \
    pnpm run build && \
    pnpm prune --prod --ignore-scripts

# ─── Stage 3: CMS runtime ─────────────────────────────────────────────────────
FROM ${BASE_IMAGE}

RUN --mount=type=cache,target=/var/cache/apt,sharing=locked \
    --mount=type=cache,target=/var/lib/apt,sharing=locked <<EOF
#!/bin/bash -ex
    export DEBIAN_FRONTEND=noninteractive
    rm -f /etc/apt/apt.conf.d/docker-clean
    apt-get update
    apt-get install -y curl ca-certificates
    curl -fsSL https://deb.nodesource.com/setup_20.x | bash -
    PACKAGES=(
        build-essential
        cppreference-doc-en-html
        default-jdk-headless
        fp-compiler
        ghc
        git
        libcap-dev
        libffi-dev
        libpq-dev
        libyaml-dev
        mono-mcs
        nodejs
        php-cli
        postgresql-client
        pypy3
        python3
        python3-dev
        python3-pip
        python3-venv
        rustc
        shared-mime-info
        sudo
        supervisor
        wait-for-it
        zip
    )
    apt-get install -y "${PACKAGES[@]}"
EOF

RUN --mount=type=cache,target=/var/cache/apt,sharing=locked \
    --mount=type=cache,target=/var/lib/apt,sharing=locked <<EOF
#!/bin/bash -ex
    export DEBIAN_FRONTEND=noninteractive
    CODENAME=$(source /etc/os-release; echo $VERSION_CODENAME)
    echo "deb [arch=amd64 signed-by=/etc/apt/keyrings/isolate.asc]" \
        "http://www.ucw.cz/isolate/debian/ ${CODENAME}-isolate main" \
        >/etc/apt/sources.list.d/isolate.list
    curl -fsSL https://www.ucw.cz/isolate/debian/signing-key.asc \
        >/etc/apt/keyrings/isolate.asc
    apt-get update
    apt-get install -y isolate
    sed -i 's@^cg_root .*@cg_root = /sys/fs/cgroup@' /etc/isolate
EOF

# Create cmsuser (uid 2000). Sudo is scoped to /usr/bin/isolate only.
RUN <<EOF
#!/bin/bash -ex
    useradd -ms /bin/bash -u 2000 cmsuser
    usermod -aG isolate cmsuser
    echo 'cmsuser ALL=(ALL) NOPASSWD: /usr/bin/isolate' >> /etc/sudoers
EOF

# Copy rekarel artifacts from the builder stage.
# rekarel: Node.js CLI — copy node_modules then recreate the npm symlink so that
#   Node.js resolves require() relative to the package dir (where commander lives),
#   not /usr/local/bin/ where there are no node_modules.
# karel: statically-linked C++ binary (no runtime deps).
COPY --from=rekarel-builder /usr/local/lib/node_modules /usr/local/lib/node_modules
RUN ln -sf /usr/local/lib/node_modules/@rekarel/cli/dist/commands.cjs /usr/local/bin/rekarel
COPY --from=rekarel-builder /build/bin/karel /usr/local/bin/karel

# Copy CMS-Loader build artifacts from the loader-builder stage.
# CMS-Loader runs TypeScript directly via tsx (no tsc compilation step).
# Copy: server source, pre-built frontend, prod node_modules (tsx stays as a dep).
COPY --from=loader-builder --chown=cmsuser:cmsuser \
    /build/src           /home/cmsuser/cms-loader/src
COPY --from=loader-builder --chown=cmsuser:cmsuser \
    /build/client/dist   /home/cmsuser/cms-loader/client/dist
COPY --from=loader-builder --chown=cmsuser:cmsuser \
    /build/node_modules  /home/cmsuser/cms-loader/node_modules
COPY --from=loader-builder --chown=cmsuser:cmsuser \
    /build/package.json  /home/cmsuser/cms-loader/package.json
COPY --from=loader-builder --chown=cmsuser:cmsuser \
    /build/tsconfig.json /home/cmsuser/cms-loader/tsconfig.json

USER cmsuser
ENV LANG=C.UTF-8

RUN mkdir /home/cmsuser/src
COPY --chown=cmsuser:cmsuser install.py constraints.txt /home/cmsuser/src/

WORKDIR /home/cmsuser/src

RUN --mount=type=cache,target=/home/cmsuser/.cache/pip,uid=2000 ./install.py venv
ENV PATH="/home/cmsuser/cms/bin:$PATH"

COPY --chown=cmsuser:cmsuser . /home/cmsuser/src

# Install CMS without dev dependencies (no pytest, coverage, beautifulsoup4).
RUN --mount=type=cache,target=/home/cmsuser/.cache/pip,uid=2000 ./install.py cms

# Install the cms_rekarel Karel language plugin.
# No pip cache mount: always fetch fresh from GitHub on every build.
RUN pip install --no-cache-dir git+https://github.com/AresLOLXD/cms_rekarel.git

# Copy config-generation script and entrypoint into the image.
COPY --chown=cmsuser:cmsuser docker/generate_config.py /home/cmsuser/generate_config.py
COPY --chown=cmsuser:cmsuser docker/entrypoint.sh /home/cmsuser/entrypoint.sh
RUN chmod +x /home/cmsuser/entrypoint.sh

# Bake dev/test configs for backward compatibility with docker-compose.dev.yml
# and docker-compose.test.yml. These are pre-generated and referenced by
# CMS_CONFIG in those compose files — the entrypoint skips generation when
# CMS_CONFIG already points to an existing file.
RUN <<EOF
#!/bin/bash -ex
    sed 's|/cmsuser:your_password_here@localhost:5432/cmsdb"|/postgres@testdb:5432/cmsdbfortesting"|' \
        ./config/cms.sample.toml >../cms/etc/cms-testdb.toml
    sed -e 's|/cmsuser:your_password_here@localhost:5432/cmsdb"|/postgres@devdb:5432/cmsdb"|' \
        -e 's/127.0.0.1/0.0.0.0/' \
        ./config/cms.sample.toml >../cms/etc/cms-devdb.toml
    sed -i 's/127.0.0.1/0.0.0.0/' ../cms/etc/cms_ranking.toml
EOF

ENTRYPOINT ["/home/cmsuser/entrypoint.sh"]
CMD ["supervisord", "--nodaemon", "-c", "/home/cmsuser/cms/etc/supervisord.conf"]
