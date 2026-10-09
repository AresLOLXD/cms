"""Unit tests for docker/generate_config.py."""

import configparser
import os
import re
import sys
import tomllib
from urllib.parse import unquote, urlsplit
import pytest
import yaml

sys.path.insert(0, os.path.dirname(__file__))
import generate_config as gc

VALID_ENV = {
    "CMS_DB_URL": "postgresql+psycopg2://cms:secret@db:5432/cmsdb",
    "CMS_SECRET_KEY": "abcdef0123456789abcdef0123456789",
    "CMS_CONTEST_ID": "1",
}


def _set(monkeypatch, extra=None):
    for k, v in VALID_ENV.items():
        monkeypatch.setenv(k, v)
    for k, v in (extra or {}).items():
        monkeypatch.setenv(k, v)


def test_validate_missing_db_url(monkeypatch):
    monkeypatch.delenv("CMS_DB_URL", raising=False)
    monkeypatch.setenv("CMS_SECRET_KEY", "abcdef0123456789abcdef0123456789")
    with pytest.raises(SystemExit):
        gc.validate_required()


def test_validate_insecure_secret_key(monkeypatch):
    monkeypatch.setenv("CMS_DB_URL", "postgresql+psycopg2://x:y@z/db")
    monkeypatch.setenv("CMS_SECRET_KEY", gc.INSECURE_SECRET_KEY)
    with pytest.raises(SystemExit):
        gc.validate_required()


def test_validate_passes_with_valid_env(monkeypatch):
    _set(monkeypatch)
    gc.validate_required()  # must not raise


def test_cms_toml_defaults(monkeypatch):
    _set(monkeypatch)
    toml = gc.generate_cms_toml()
    assert 'url = "postgresql+psycopg2://cms:secret@db:5432/cmsdb"' in toml
    assert "listen_port = [8888]" in toml
    assert "listen_port = 8889" in toml
    assert 'Worker = [["localhost", 26000]]' in toml
    assert 'ContestWebServer = [["localhost", 21000]]' in toml
    assert 'AdminWebServer = [["localhost", 21100]]' in toml


def test_cms_toml_data_dir_is_a_dedicated_directory(monkeypatch):
    # data_dir holds the submission copies (submit_local_copy_path is
    # "%s/submissions/"), the user-test copies (tests_local_copy_path is
    # "%s/tests/") and the Telegram bot state. It must not be under cms/lib,
    # the install prefix's lib: that is where site-packages lives, and a volume
    # mounted there shadows the installed code.
    _set(monkeypatch)
    data_dir = tomllib.loads(gc.generate_cms_toml())["global"]["data_dir"]
    assert data_dir == "/home/cmsuser/cms/data"


def test_prod_compose_mounts_cms_data_on_the_configured_data_dir(monkeypatch):
    # Docker fills a named volume from the image the first time it is used and
    # never refreshes it. Mounted over cms/lib, cms-data froze the installed
    # code at the first build and every rebuild kept running the old one.
    _set(monkeypatch)
    data_dir = tomllib.loads(gc.generate_cms_toml())["global"]["data_dir"]
    with open(os.path.join(os.path.dirname(__file__), "docker-compose.prod.yml")) as f:
        cms_service = yaml.safe_load(f)["services"]["cms"]

    mounts = {}
    for volume in cms_service["volumes"]:
        source, target = volume.split(":")[:2]
        mounts[target] = source

    assert mounts[data_dir] == "cms-data"
    install_lib = "/home/cmsuser/cms/lib"
    for target in mounts:
        assert target != "/home/cmsuser/cms"
        assert target != install_lib and not target.startswith(install_lib + "/")


def _prod_compose_services():
    with open(os.path.join(os.path.dirname(__file__), "docker-compose.prod.yml")) as f:
        return yaml.safe_load(f)["services"]


@pytest.mark.parametrize("service", ["cms", "ranking"])
def test_prod_compose_publishes_ports_on_localhost_only(service):
    # Docker-published ports bypass ufw. The reverse proxy on the host is the
    # only entry point, so nothing may be reachable on the public address.
    ports = _prod_compose_services()[service]["ports"]
    assert ports
    for port in ports:
        assert port.startswith("127.0.0.1:"), port


@pytest.mark.parametrize("service", ["cms", "ranking"])
def test_prod_compose_raises_the_open_files_limit(service):
    nofile = _prod_compose_services()[service]["ulimits"]["nofile"]
    assert nofile == {"soft": 65536, "hard": 65536}


def test_prod_compose_gives_cms_time_to_stop_gracefully(monkeypatch):
    # Docker SIGKILLs the container after stop_grace_period. supervisord stops
    # its programs one after another (each [program] is its own group, waited
    # for in priority order), so the worst case is the SUM of the stop times,
    # not the longest one: every ContestWebServer may use its full stopwaitsecs.
    _set(monkeypatch, {"CMS_CWS_COUNT": "4"})
    grace = _prod_compose_services()["cms"]["stop_grace_period"]
    assert grace.endswith("s")
    programs = _supervisord_programs(gc.generate_supervisord_conf())
    cws_stop_total = sum(
        int(program["stopwaitsecs"])
        for name, program in programs.items()
        if name.startswith("cmscontestwebserver")
    )
    assert cws_stop_total == 4 * 40
    assert int(grace[:-1]) > cws_stop_total


def test_dockerfile_creates_the_data_dir_as_cmsuser(monkeypatch):
    # A named volume inherits the ownership of the image's directory the first
    # time it is created, so the directory must belong to cmsuser (the user
    # that runs the services) and not to root.
    _set(monkeypatch)
    data_dir = tomllib.loads(gc.generate_cms_toml())["global"]["data_dir"]
    mkdir = re.compile(r"RUN mkdir( -p)? " + re.escape(data_dir))
    dockerfile = os.path.join(os.path.dirname(__file__), "..", "Dockerfile")
    if not os.path.isfile(dockerfile):
        pytest.skip("Dockerfile is not in this tree: .dockerignore keeps it out "
                    "of the image that runs the tests in CI")
    with open(dockerfile) as f:
        lines = [line.strip() for line in f]

    user = "root"
    users_at_mkdir = []
    for line in lines:
        if line.startswith("FROM "):
            user = "root"
        elif line.startswith("USER "):
            user = line.split()[1]
        elif mkdir.fullmatch(line):
            users_at_mkdir.append(user)

    assert users_at_mkdir == ["cmsuser"]


def test_cms_toml_multiple_cws(monkeypatch):
    _set(monkeypatch, {"CMS_CWS_COUNT": "3", "CMS_CWS_HTTP_PORT": "8888"})
    toml = gc.generate_cms_toml()
    assert "listen_port = [8888, 8889, 8890]" in toml
    assert '["localhost", 21000], ["localhost", 21001], ["localhost", 21002]' in toml


def test_cms_toml_multiple_workers(monkeypatch):
    _set(monkeypatch, {"CMS_WORKER_COUNT": "3"})
    toml = gc.generate_cms_toml()
    assert '["localhost", 26000], ["localhost", 26001], ["localhost", 26002]' in toml


def test_cms_toml_custom_aws_port(monkeypatch):
    _set(monkeypatch, {"CMS_AWS_HTTP_PORT": "9000"})
    toml = gc.generate_cms_toml()
    assert "listen_port = 9000" in toml


def test_cms_toml_cookie_duration_default(monkeypatch):
    # The code default (30 minutes) logs contestants out in the middle of a
    # contest; the deployment default is 5 hours.
    _set(monkeypatch)
    cws = tomllib.loads(gc.generate_cms_toml())["contest_web_server"]
    assert cws["cookie_duration"] == 18000


def test_cms_toml_cookie_duration_custom(monkeypatch):
    _set(monkeypatch, {"CMS_CWS_COOKIE_DURATION": "7200"})
    cws = tomllib.loads(gc.generate_cms_toml())["contest_web_server"]
    assert cws["cookie_duration"] == 7200


def test_cms_toml_cookie_duration_empty_uses_default(monkeypatch):
    # An empty "CMS_CWS_COOKIE_DURATION=" line in .env must behave as unset,
    # like every other optional variable.
    _set(monkeypatch, {"CMS_CWS_COOKIE_DURATION": ""})
    cws = tomllib.loads(gc.generate_cms_toml())["contest_web_server"]
    assert cws["cookie_duration"] == 18000


@pytest.mark.parametrize("value", ["abc", "1.5", "0", "-60"])
def test_cms_toml_cookie_duration_invalid(monkeypatch, capsys, value):
    _set(monkeypatch, {"CMS_CWS_COOKIE_DURATION": value})
    with pytest.raises(SystemExit):
        gc.generate_cms_toml()
    assert "CMS_CWS_COOKIE_DURATION" in capsys.readouterr().err


def test_request_time_header_default_is_off(monkeypatch):
    _set(monkeypatch, {})
    cws = tomllib.loads(gc.generate_cms_toml())["contest_web_server"]
    assert cws["request_time_header"] == ""


def test_request_time_header_custom(monkeypatch):
    _set(monkeypatch, {"CMS_CWS_REQUEST_TIME_HEADER": "X-Request-Start"})
    cws = tomllib.loads(gc.generate_cms_toml())["contest_web_server"]
    assert cws["request_time_header"] == "X-Request-Start"


@pytest.mark.parametrize("value", ["X Request", "X-Request:", "a\"b", "é"])
def test_request_time_header_invalid(monkeypatch, capsys, value):
    _set(monkeypatch, {"CMS_CWS_REQUEST_TIME_HEADER": value})
    with pytest.raises(SystemExit):
        gc.generate_cms_toml()
    assert "CMS_CWS_REQUEST_TIME_HEADER" in capsys.readouterr().err


def test_cms_toml_proxy_url_contains_rws_creds(monkeypatch):
    _set(monkeypatch, {"CMS_RWS_USERNAME": "myuser", "CMS_RWS_PASSWORD": "mypass"})
    toml = gc.generate_cms_toml()
    assert "myuser:mypass@localhost" in toml


def test_ranking_toml_defaults(monkeypatch):
    _set(monkeypatch)
    toml = gc.generate_cms_ranking_toml()
    assert 'bind_address = "0.0.0.0"' in toml
    assert "http_port = 8890" in toml


def test_ranking_toml_custom(monkeypatch):
    _set(monkeypatch, {
        "CMS_RWS_USERNAME": "myuser",
        "CMS_RWS_PASSWORD": "mypassword",
        "CMS_RWS_HTTP_PORT": "9090",
    })
    toml = gc.generate_cms_ranking_toml()
    assert 'username = "myuser"' in toml
    assert 'password = "mypassword"' in toml
    assert "http_port = 9090" in toml


def test_ranking_toml_always_shows_the_username_column(monkeypatch):
    _set(monkeypatch)
    toml = gc.generate_cms_ranking_toml()
    assert tomllib.loads(toml)["public"]["show_id_column"] is True


def test_supervisord_single_cws_worker(monkeypatch):
    _set(monkeypatch)
    conf = gc.generate_supervisord_conf()
    assert "cmsLogService 0" in conf
    assert "cmsWorker 0" in conf
    assert "cmsContestWebServer 0 -c 1" in conf
    assert "cmsAdminWebServer 0" in conf
    # LogService must have the lowest priority number (starts first)
    log_priority = int(conf.split("cmsLogService")[0].rsplit("priority=", 1)[-1].split("\n")[0])
    assert log_priority <= 20


def test_supervisord_multiple_cws(monkeypatch):
    _set(monkeypatch, {"CMS_CWS_COUNT": "2", "CMS_CONTEST_ID": "5"})
    conf = gc.generate_supervisord_conf()
    assert "cmsContestWebServer 0 -c 5" in conf
    assert "cmsContestWebServer 1 -c 5" in conf


def test_supervisord_multiple_workers(monkeypatch):
    _set(monkeypatch, {"CMS_WORKER_COUNT": "2"})
    conf = gc.generate_supervisord_conf()
    assert "cmsWorker 0" in conf
    assert "cmsWorker 1" in conf


def _supervisord_programs(conf):
    parser = configparser.ConfigParser(interpolation=None)
    parser.read_string(conf)
    return {name[len("program:"):]: parser[name]
            for name in parser.sections() if name.startswith("program:")}


def test_supervisord_contest_web_servers_wait_for_the_activity_log_flush(monkeypatch):
    # ContestWebServer flushes the participant activity log on shutdown, for
    # up to SHUTDOWN_FLUSH_TIMEOUT (30 s); supervisord's default stopwaitsecs
    # (10 s) would SIGKILL it in the middle of the flush.
    _set(monkeypatch, {"CMS_CWS_COUNT": "3"})
    programs = _supervisord_programs(gc.generate_supervisord_conf())
    cws_names = {f"cmscontestwebserver{i}" for i in range(3)}
    assert cws_names <= set(programs)
    for name, program in programs.items():
        if name in cws_names:
            assert program["stopwaitsecs"] == "40"
        else:
            assert "stopwaitsecs" not in program


def test_cms_toml_no_telegram_by_default(monkeypatch):
    _set(monkeypatch)
    toml = gc.generate_cms_toml()
    assert "[telegram_bot]" not in toml
    assert "TelegramBot" not in toml


def test_cms_toml_telegram_section(monkeypatch):
    _set(monkeypatch, {
        "CMS_TELEGRAM_BOT_TOKEN": "123456:ABC-DEF",
        "CMS_TELEGRAM_CHAT_ID": "-1001234567890",
    })
    toml = gc.generate_cms_toml()
    assert "[telegram_bot]" in toml
    assert 'bot_token = "123456:ABC-DEF"' in toml
    assert 'chat_id = "-1001234567890"' in toml
    assert 'TelegramBot = [["localhost", 27000]]' in toml


def test_cms_toml_telegram_partial(monkeypatch):
    # Only one var set — no telegram block should appear
    _set(monkeypatch, {"CMS_TELEGRAM_BOT_TOKEN": "123456:ABC-DEF"})
    toml = gc.generate_cms_toml()
    assert "[telegram_bot]" not in toml
    assert "TelegramBot" not in toml


def test_supervisord_no_telegram_by_default(monkeypatch):
    _set(monkeypatch)
    conf = gc.generate_supervisord_conf()
    assert "cmstelegrambot" not in conf
    assert "cmsTelegramBot" not in conf


def test_supervisord_telegram_program(monkeypatch):
    _set(monkeypatch, {
        "CMS_TELEGRAM_BOT_TOKEN": "123456:ABC-DEF",
        "CMS_TELEGRAM_CHAT_ID": "-1001234567890",
        "CMS_CONTEST_ID": "3",
    })
    conf = gc.generate_supervisord_conf()
    assert "cmstelegrambot" in conf
    assert "cmsTelegramBot 0 -c 3" in conf


def test_supervisord_evaluation_and_proxy_get_contest_flag(monkeypatch):
    _set(monkeypatch, {"CMS_CONTEST_ID": "23"})
    conf = gc.generate_supervisord_conf()
    assert "cmsEvaluationService 0 -c 23" in conf
    assert "cmsProxyService 0 -c 23" in conf


def test_supervisord_evaluation_and_proxy_no_flag_when_all(monkeypatch):
    _set(monkeypatch, {"CMS_CONTEST_ID": "ALL"})
    conf = gc.generate_supervisord_conf()
    assert "cmsEvaluationService 0 -c" not in conf
    assert "cmsProxyService 0 -c" not in conf
    # services still present without flag
    assert "cmsEvaluationService 0" in conf
    assert "cmsProxyService 0" in conf


def test_supervisord_no_cmsloader_by_default(monkeypatch):
    # CMS-Loader must NOT appear when credentials are absent.
    _set(monkeypatch)
    conf = gc.generate_supervisord_conf()
    assert "cmsloader" not in conf
    assert "cms-loader" not in conf


def test_supervisord_cmsloader_all_vars_set(monkeypatch):
    # CMS-Loader appears when all three credentials are provided.
    _set(monkeypatch, {
        "CMS_LOADER_SESSION_SECRET": "supersecret32charslongenoughXXXX",
        "CMS_LOADER_ADMIN_USER": "admin",
        "CMS_LOADER_ADMIN_PASSWORD": "hunter2",
    })
    conf = gc.generate_supervisord_conf()
    assert "[program:cmsloader]" in conf
    assert "/home/cmsuser/cms-loader/node_modules/.bin/tsx src/index.ts" in conf
    assert "SESSION_SECRET=" in conf
    assert "ADMIN_USER=" in conf
    assert "ADMIN_PASSWORD=" in conf
    assert 'NODE_ENV="production"' in conf
    assert "directory=/home/cmsuser/cms-loader" in conf
    assert 'PORT="9995"' in conf


def test_supervisord_cmsloader_partial_vars_skipped(monkeypatch):
    # Missing password → CMS-Loader must not appear.
    _set(monkeypatch, {
        "CMS_LOADER_SESSION_SECRET": "supersecret32charslongenoughXXXX",
        "CMS_LOADER_ADMIN_USER": "admin",
        # CMS_LOADER_ADMIN_PASSWORD intentionally omitted
    })
    conf = gc.generate_supervisord_conf()
    assert "cmsloader" not in conf


def test_supervisord_cmsloader_partial_no_secret(monkeypatch):
    # Missing session secret → CMS-Loader must not appear.
    _set(monkeypatch, {
        "CMS_LOADER_ADMIN_USER": "admin",
        "CMS_LOADER_ADMIN_PASSWORD": "hunter2",
        # CMS_LOADER_SESSION_SECRET intentionally omitted
    })
    conf = gc.generate_supervisord_conf()
    assert "cmsloader" not in conf


def test_supervisord_cmsloader_custom_port(monkeypatch):
    # Custom port is reflected in the environment string.
    _set(monkeypatch, {
        "CMS_LOADER_SESSION_SECRET": "supersecret32charslongenoughXXXX",
        "CMS_LOADER_ADMIN_USER": "admin",
        "CMS_LOADER_ADMIN_PASSWORD": "hunter2",
        "CMS_LOADER_PORT": "9000",
    })
    conf = gc.generate_supervisord_conf()
    assert 'PORT="9000"' in conf


def test_cms_toml_proxy_uses_default_localhost_rws_host(monkeypatch):
    _set(monkeypatch)
    toml = gc.generate_cms_toml()
    assert "@localhost:8890/" in toml


def test_cms_toml_proxy_uses_custom_rws_host(monkeypatch):
    _set(monkeypatch, {"CMS_RWS_HOST": "ranking"})
    toml = gc.generate_cms_toml()
    assert "@ranking:8890/" in toml
    assert "@localhost" not in toml


def test_supervisord_no_ranking_webserver(monkeypatch):
    _set(monkeypatch)
    conf = gc.generate_supervisord_conf()
    assert "cmsRankingWebServer" not in conf
    assert "cmsrankingwebserver" not in conf


def test_ranking_only_mode_skips_cms_toml(monkeypatch, tmp_path):
    monkeypatch.setenv("CMS_RANKING_ONLY", "true")
    monkeypatch.setenv("CMS_RANKING_CONFIG", str(tmp_path / "ranking.toml"))
    monkeypatch.delenv("CMS_DB_URL", raising=False)
    monkeypatch.delenv("CMS_SECRET_KEY", raising=False)
    gc.main()  # must not SystemExit
    assert (tmp_path / "ranking.toml").exists()
    assert "http_port" in (tmp_path / "ranking.toml").read_text()


def test_ranking_only_mode_skips_supervisord(monkeypatch, tmp_path):
    monkeypatch.setenv("CMS_RANKING_ONLY", "true")
    monkeypatch.setenv("CMS_CONTEST_ID", "1")
    monkeypatch.setenv("CMS_RANKING_CONFIG", str(tmp_path / "ranking.toml"))
    monkeypatch.delenv("CMS_DB_URL", raising=False)
    monkeypatch.delenv("CMS_SECRET_KEY", raising=False)
    gc.main()
    assert not (tmp_path / "supervisord.conf").exists()


def test_supervisord_no_telegram_bot_when_all(monkeypatch, capsys):
    _set(monkeypatch, {
        "CMS_CONTEST_ID": "ALL",
        "CMS_TELEGRAM_BOT_TOKEN": "123456:ABC-DEF",
        "CMS_TELEGRAM_CHAT_ID": "-1001234567890",
    })
    conf = gc.generate_supervisord_conf()
    assert "cmstelegrambot" not in conf
    assert "Telegram bot" in capsys.readouterr().err


@pytest.mark.parametrize("compose_file, service", [
    ("docker-compose.test.yml", "testcms"),
    ("docker-compose.dev.yml", "devcms"),
])
def test_ranking_credentials_match_the_proxy_service_url(
        monkeypatch, compose_file, service):
    # The testcms and devcms containers regenerate cms_ranking.toml at
    # startup from CMS_RWS_USERNAME / CMS_RWS_PASSWORD (default: "rws" and
    # an empty password), while their cms-testdb.toml / cms-devdb.toml are
    # baked from config/cms.sample.toml, so ProxyService sends the
    # credentials in that file's rankings URL. If the two differ,
    # RankingWebServer answers every push with 401 and the functional tests
    # still pass.
    docker_dir = os.path.dirname(__file__)
    with open(os.path.join(docker_dir, compose_file)) as f:
        environment = yaml.safe_load(f)["services"][service]["environment"]
    with open(os.path.join(docker_dir, "..", "config", "cms.sample.toml"), "rb") as f:
        proxy_url = urlsplit(tomllib.load(f)["proxy_service"]["rankings"][0])

    for name in ("CMS_RWS_USERNAME", "CMS_RWS_PASSWORD"):
        monkeypatch.delenv(name, raising=False)
        if name in environment:
            monkeypatch.setenv(name, str(environment[name]))
    ranking = tomllib.loads(gc.generate_cms_ranking_toml())

    assert ranking["username"] == unquote(proxy_url.username)
    assert ranking["password"] == unquote(proxy_url.password)
