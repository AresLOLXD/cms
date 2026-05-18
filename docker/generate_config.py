#!/usr/bin/env python3
"""Generate cms.toml, cms_ranking.toml, and supervisord.conf from environment variables.

Called by entrypoint.sh at container startup. If CMS_CONFIG already points to
an existing file, cms.toml generation is skipped (preserves dev/test compat).
supervisord.conf is only generated when CMS_CONTEST_ID is set.
"""

import os
import sys
from urllib.parse import quote as _url_quote

INSECURE_SECRET_KEY = "8e045a51e4b102ea803c06f92841a1fb"


def _toml_str(value: str) -> str:
    return value.replace("\\", "\\\\").replace('"', '\\"')


def _require(var: str) -> str:
    val = os.environ.get(var)
    if not val:
        print(f"ERROR: required environment variable {var!r} is not set.", file=sys.stderr)
        sys.exit(1)
    return val


def _get(var: str, default: str) -> str:
    return os.environ.get(var) or default


def _get_int(var: str, default: int) -> int:
    return int(os.environ.get(var) or default)


def validate_required() -> None:
    _require("CMS_DB_URL")
    secret_key = _require("CMS_SECRET_KEY")
    if secret_key == INSECURE_SECRET_KEY:
        print(
            "ERROR: CMS_SECRET_KEY is set to the public example value.\n"
            "Generate a real key with:\n"
            "  python3 -c 'from cmscommon import crypto; print(crypto.get_hex_random_key())'",
            file=sys.stderr,
        )
        sys.exit(1)


def generate_cms_toml() -> str:
    db_url = _require("CMS_DB_URL")
    secret_key = _require("CMS_SECRET_KEY")
    listen_addr = _get("CMS_LISTEN_ADDRESS", "0.0.0.0")

    raw_log = _get("CMS_LOG_DEBUG", "false").lower()
    if raw_log not in ("true", "false"):
        print(
            f"ERROR: CMS_LOG_DEBUG must be 'true' or 'false', got {raw_log!r}.",
            file=sys.stderr,
        )
        sys.exit(1)
    log_debug = raw_log

    raw_proxies = os.environ.get("CMS_NUM_PROXIES_USED", "0")
    try:
        num_proxies = int(raw_proxies)
    except ValueError:
        print(
            f"ERROR: CMS_NUM_PROXIES_USED must be an integer, got {raw_proxies!r}.",
            file=sys.stderr,
        )
        sys.exit(1)

    cws_count = _get_int("CMS_CWS_COUNT", 1)
    worker_count = _get_int("CMS_WORKER_COUNT", 1)
    cws_http_port = _get_int("CMS_CWS_HTTP_PORT", 8888)
    cws_rpc_port = _get_int("CMS_CWS_RPC_PORT", 21000)
    aws_http_port = _get_int("CMS_AWS_HTTP_PORT", 8889)
    aws_rpc_port = _get_int("CMS_AWS_RPC_PORT", 21100)
    rws_http_port = _get_int("CMS_RWS_HTTP_PORT", 8890)
    rws_username = _get("CMS_RWS_USERNAME", "rws")
    rws_password = _get("CMS_RWS_PASSWORD", "")

    bot_token = os.environ.get("CMS_TELEGRAM_BOT_TOKEN", "").strip()
    chat_id = os.environ.get("CMS_TELEGRAM_CHAT_ID", "").strip()
    telegram_configured = bool(bot_token and chat_id)
    telegram_service_line = (
        '\nTelegramBot = [["localhost", 27000]]' if telegram_configured else ""
    )

    worker_entries = ", ".join(f'["localhost", {26000 + i}]' for i in range(worker_count))
    cws_entries = ", ".join(f'["localhost", {cws_rpc_port + i}]' for i in range(cws_count))
    cws_ports = "[" + ", ".join(str(cws_http_port + i) for i in range(cws_count)) + "]"
    cws_addrs = "[" + ", ".join(f'"{listen_addr}"' for _ in range(cws_count)) + "]"

    toml = f"""\
[global]
file_log_debug = {log_debug}
stream_log_detailed = false

[services]
LogService = [["localhost", 29000]]
ResourceService = [["localhost", 28000]]
ScoringService = [["localhost", 28500]]
Checker = [["localhost", 22000]]
EvaluationService = [["localhost", 25000]]
Worker = [{worker_entries}]
ContestWebServer = [{cws_entries}]
AdminWebServer = [["localhost", {aws_rpc_port}]]
ProxyService = [["localhost", 28600]]{telegram_service_line}

[database]
url = "{_toml_str(db_url)}"
debug = false

[worker]
keep_sandbox = false

[web_server]
secret_key = "{_toml_str(secret_key)}"
tornado_debug = false

[contest_web_server]
listen_address = {cws_addrs}
listen_port = {cws_ports}
num_proxies_used = {num_proxies}

[admin_web_server]
listen_address = "{listen_addr}"
listen_port = {aws_http_port}
num_proxies_used = {num_proxies}

[proxy_service]
rankings = ["http://{_url_quote(rws_username, safe="")}:{_url_quote(rws_password, safe="")}@localhost:{rws_http_port}/"]
"""

    if telegram_configured:
        toml += (
            f'\n[telegram_bot]\n'
            f'bot_token = "{_toml_str(bot_token)}"\n'
            f'chat_id = "{_toml_str(chat_id)}"\n'
        )
    return toml


def generate_cms_ranking_toml() -> str:
    listen_addr = _get("CMS_LISTEN_ADDRESS", "0.0.0.0")
    rws_http_port = _get_int("CMS_RWS_HTTP_PORT", 8890)
    rws_username = _get("CMS_RWS_USERNAME", "rws")
    rws_password = _get("CMS_RWS_PASSWORD", "")

    return f"""\
bind_address = "{listen_addr}"
http_port = {rws_http_port}
username = "{_toml_str(rws_username)}"
password = "{_toml_str(rws_password)}"
realm_name = "Scoreboard"
buffer_size = 100

[public]
show_id_column = false
"""


def generate_supervisord_conf() -> str:
    cws_count = _get_int("CMS_CWS_COUNT", 1)
    worker_count = _get_int("CMS_WORKER_COUNT", 1)
    contest_id = _require("CMS_CONTEST_ID")
    contest_flag = f" -c {contest_id}" if contest_id != "ALL" else ""
    bot_token = os.environ.get("CMS_TELEGRAM_BOT_TOKEN", "").strip()
    chat_id = os.environ.get("CMS_TELEGRAM_CHAT_ID", "").strip()
    telegram_configured = bool(bot_token and chat_id)

    def program(name: str, command: str, priority: int) -> str:
        return (
            f"[program:{name}]\n"
            f"priority={priority}\n"
            f"command={command}\n"
            "autostart=true\n"
            "autorestart=true\n"
            "stdout_logfile=/dev/stdout\n"
            "stdout_logfile_maxbytes=0\n"
            "stderr_logfile=/dev/stderr\n"
            "stderr_logfile_maxbytes=0\n"
        )

    blocks = [
        "[supervisord]\n"
        "nodaemon=true\n"
        "logfile=/dev/null\n"
        "logfile_maxbytes=0\n"
        "\n"
        "[unix_http_server]\n"
        "file=/tmp/supervisor.sock\n"
        "\n"
        "[supervisorctl]\n"
        "serverurl=unix:///tmp/supervisor.sock\n"
        "\n"
        "[rpcinterface:supervisor]\n"
        "supervisor.rpcinterface_factory=supervisor.rpcinterface:make_main_rpcinterface\n",
        program("cmslogservice", "cmsLogService 0", 10),
        program("cmsresourceservice", "cmsResourceService 0", 20),
        program("cmsscoringservice", "cmsScoringService 0", 30),
        program("cmsevaluationservice", f"cmsEvaluationService 0{contest_flag}", 30),
    ]

    for i in range(worker_count):
        blocks.append(program(f"cmsworker{i}", f"cmsWorker {i}", 40))

    blocks.append(program("cmsproxyservice", f"cmsProxyService 0{contest_flag}", 50))
    blocks.append(program("cmsrankingwebserver", "cmsRankingWebServer", 55))

    for i in range(cws_count):
        blocks.append(
            program(f"cmscontestwebserver{i}", f"cmsContestWebServer {i} -c {contest_id}", 60)
        )

    blocks.append(program("cmsadminwebserver", "cmsAdminWebServer 0", 60))

    if telegram_configured:
        blocks.append(
            program("cmstelegrambot", f"cmsTelegramBot 0 -c {contest_id}", 65)
        )

    loader_secret = os.environ.get("CMS_LOADER_SESSION_SECRET", "")
    loader_user = os.environ.get("CMS_LOADER_ADMIN_USER", "")
    loader_pass = os.environ.get("CMS_LOADER_ADMIN_PASSWORD", "")
    loader_port = _get_int("CMS_LOADER_PORT", 9995)

    if loader_secret and loader_user and loader_pass:
        env_str = (
            f'SESSION_SECRET="%(ENV_CMS_LOADER_SESSION_SECRET)s",'
            f'ADMIN_USER="%(ENV_CMS_LOADER_ADMIN_USER)s",'
            f'ADMIN_PASSWORD="%(ENV_CMS_LOADER_ADMIN_PASSWORD)s",'
            f'PORT="{loader_port}",'
            f'NODE_ENV="production"'
        )
        blocks.append(
            f"[program:cmsloader]\n"
            f"priority=70\n"
            f"directory=/home/cmsuser/cms-loader\n"
            f"command=node dist/index.js\n"
            f"environment={env_str}\n"
            "autostart=true\nautorestart=true\n"
            "stdout_logfile=/dev/stdout\nstdout_logfile_maxbytes=0\n"
            "stderr_logfile=/dev/stderr\nstderr_logfile_maxbytes=0\n"
        )

    return "\n".join(blocks)


def main() -> None:
    explicit_config = os.environ.get("CMS_CONFIG")
    config_path = explicit_config or "/home/cmsuser/cms/etc/cms.toml"
    ranking_config_path = _get("CMS_RANKING_CONFIG", "/home/cmsuser/cms/etc/cms_ranking.toml")
    supervisord_path = "/home/cmsuser/cms/etc/supervisord.conf"

    if explicit_config and os.path.isfile(config_path):
        print(f"Using existing config: {config_path}", file=sys.stderr)
    else:
        validate_required()
        os.makedirs(os.path.dirname(config_path), exist_ok=True)
        with open(config_path, "w") as f:
            f.write(generate_cms_toml())
        print(f"Generated {config_path}", file=sys.stderr)

    # Always overwrite the ranking config so env var credentials (CMS_RWS_USERNAME/
    # CMS_RWS_PASSWORD) are used instead of the sample credentials baked by install.py.
    os.makedirs(os.path.dirname(ranking_config_path), exist_ok=True)
    with open(ranking_config_path, "w") as f:
        f.write(generate_cms_ranking_toml())
    print(f"Generated {ranking_config_path}", file=sys.stderr)

    if os.environ.get("CMS_CONTEST_ID"):
        os.makedirs(os.path.dirname(supervisord_path), exist_ok=True)
        with open(supervisord_path, "w") as f:
            f.write(generate_supervisord_conf())
        print(f"Generated {supervisord_path}", file=sys.stderr)
    else:
        print("CMS_CONTEST_ID not set — skipping supervisord.conf generation.", file=sys.stderr)


if __name__ == "__main__":
    main()
