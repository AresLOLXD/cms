"""Unit tests for docker/generate_config.py."""

import os
import sys
import pytest

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


def test_supervisord_single_cws_worker(monkeypatch):
    _set(monkeypatch)
    conf = gc.generate_supervisord_conf()
    assert "cmsLogService 0" in conf
    assert "cmsWorker 0" in conf
    assert "cmsContestWebServer 0 1" in conf
    assert "cmsAdminWebServer 0" in conf
    # LogService must have the lowest priority number (starts first)
    log_priority = int(conf.split("cmsLogService")[0].rsplit("priority=", 1)[-1].split("\n")[0])
    assert log_priority <= 20


def test_supervisord_multiple_cws(monkeypatch):
    _set(monkeypatch, {"CMS_CWS_COUNT": "2", "CMS_CONTEST_ID": "5"})
    conf = gc.generate_supervisord_conf()
    assert "cmsContestWebServer 0 5" in conf
    assert "cmsContestWebServer 1 5" in conf


def test_supervisord_multiple_workers(monkeypatch):
    _set(monkeypatch, {"CMS_WORKER_COUNT": "2"})
    conf = gc.generate_supervisord_conf()
    assert "cmsWorker 0" in conf
    assert "cmsWorker 1" in conf


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
