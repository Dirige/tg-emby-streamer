"""发布工具回归：全部使用临时假库，不读写生产数据库或 Telegram。"""

import hashlib
import io
import json
import sqlite3
import tarfile
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
import yaml

from scripts import check_release, deploy, envfile, library_bundle, upload_tg

SCHEMA = """
CREATE TABLE media (
 id INTEGER PRIMARY KEY, chat_id VARCHAR NOT NULL, message_id INTEGER NOT NULL,
 category VARCHAR, title VARCHAR, caption VARCHAR, file_name VARCHAR,
 file_id VARCHAR, file_unique_id VARCHAR, strm_path VARCHAR, nfo_path VARCHAR,
 poster_path VARCHAR, backdrop_path VARCHAR, source_link VARCHAR
);
CREATE TABLE categories (id INTEGER PRIMARY KEY, name VARCHAR UNIQUE, strm_dir VARCHAR, sort_order INTEGER);
CREATE TABLE channel_rules (
 id INTEGER PRIMARY KEY, channel_id VARCHAR UNIQUE, channel_name VARCHAR, category VARCHAR,
 title_rule VARCHAR, episode_rule VARCHAR, season INTEGER, season_rule VARCHAR, year_rule VARCHAR,
 filename_rule VARCHAR, filename_template VARCHAR, enabled BOOLEAN, strict_import BOOLEAN
);
CREATE TABLE unrecognized_media (id INTEGER PRIMARY KEY, caption VARCHAR);
"""


def config_at(path: Path, url="http://recipient.example:8001"):
    deploy.init_config(path, url)
    data = yaml.safe_load(path.read_text())
    return data


@pytest.fixture
def sample_bundle(tmp_path):
    path = tmp_path / "source/config/config.yaml"
    data = config_at(path, "http://source.example:8001")
    data["telegram"].update(api_id=123, api_hash="fake-source-api-secret", monitor_channels="-10011")
    path.write_text(yaml.safe_dump(data))
    folder = Path(data["storage"]["strm_dir"]) / "电影/Test"
    folder.mkdir(parents=True)
    strm = folder / "Test.strm"
    strm.write_text("http://source.example:8001/stream/-10011/7?token=" + data["server"]["stream_token"])
    (folder / "poster.jpg").write_bytes(b"\xff\xd8\xff" + b"image-fixture" * 10)
    nfo = folder / "Test.nfo"
    nfo.write_text(f'<movie><title>测试电影</title><thumb aspect="poster">{folder}/poster.jpg</thumb></movie>')
    db = library_bundle.database_path(data)
    with sqlite3.connect(db) as conn:
        conn.executescript(SCHEMA)
        conn.execute("INSERT INTO categories VALUES (1,'电影','电影',1)")
        conn.execute("INSERT INTO channel_rules (id,channel_id,channel_name,category,enabled,strict_import) VALUES (1,'-10011','测试频道','电影',1,0)")
        conn.execute("INSERT INTO media (id,chat_id,message_id,category,title,caption,file_name,file_id,file_unique_id,strm_path,nfo_path,poster_path,backdrop_path) VALUES (1,?,?,?,?,?,?,?,?,?,?,?,?)", (
            "-10011", 7, "电影", "测试电影", "fake-source-api-secret", "Test.mp4",
            "old-file-id", "old-unique-id", str(strm), str(nfo), str(folder / "poster.jpg"), None))
        conn.execute("INSERT INTO unrecognized_media VALUES (1,'不应分享的调试记录')")
    archive = library_bundle.export_bundle(path, tmp_path / "share", part_size_mb=1)
    return SimpleNamespace(archive=archive, source=path, data=data)


def test_init_paths_and_credentials_are_recipient_specific(tmp_path):
    first = tmp_path / "one/config/config.yaml"
    second = tmp_path / "two/config/config.yaml"
    a = config_at(first)
    b = config_at(second)
    assert a["storage"]["strm_dir"] == str(tmp_path / "one/strm")
    assert a["storage"]["media_path"] == str(tmp_path / "one/cache/media")
    assert a["server"]["stream_token"] != b["server"]["stream_token"]
    before = first.read_bytes()
    assert deploy.init_config(first) is False
    assert first.read_bytes() == before
    assert first.stat().st_mode & 0o777 == 0o600


@pytest.mark.parametrize("url", ["localhost:8001", "http://user:pass@example.com", "http://example.com/path", "https://example.com/?token=x"])
def test_url_validation_rejects_ambiguous_addresses(url):
    with pytest.raises(ValueError):
        deploy.validate_url(url)


def test_export_sanitizes_database_nfo_and_session_values(sample_bundle, tmp_path):
    stage = tmp_path / "verified"
    stage.mkdir()
    manifest = library_bundle.extract_verified(sample_bundle.archive, stage)
    assert manifest["summary"]["media_count"] == 1
    assert (stage / "strm/电影/Test/Test.strm").read_text() == "http://tgemby:8001/stream/-10011/7"
    assert "poster.jpg" in (stage / "strm/电影/Test/Test.nfo").read_text()
    assert "/app/" not in (stage / "strm/电影/Test/Test.nfo").read_text()
    library_bundle.assert_no_secrets(stage, sample_bundle.data)
    with sqlite3.connect(stage / "config/media.db") as conn:
        assert conn.execute("SELECT file_id,file_unique_id FROM media").fetchone() == (None, None)
        assert conn.execute("SELECT COUNT(*) FROM unrecognized_media").fetchone()[0] == 0


def test_import_rebases_and_preserves_own_config(sample_bundle, tmp_path):
    path = tmp_path / "recipient/config/config.yaml"
    own = config_at(path)
    with sqlite3.connect(library_bundle.database_path(own)) as conn:
        conn.executescript(SCHEMA)
    library_bundle.import_bundle(sample_bundle.archive, path, replace_empty=True,
                                 expected_sha256=library_bundle.sha256_file(sample_bundle.archive))
    result = yaml.safe_load(path.read_text())
    assert result["auth"] == own["auth"]
    assert result["server"] == own["server"]
    assert result["telegram"]["monitor_channels"] == "-10011"
    strm = tmp_path / "recipient/strm/电影/Test/Test.strm"
    assert "http://recipient.example:8001/stream/-10011/7?token=" in strm.read_text()
    assert own["server"]["stream_token"] in strm.read_text()
    assert list(path.parent.glob("media-before-import-*.db"))
    with pytest.raises(ValueError, match="不是空目录"):
        library_bundle.import_bundle(sample_bundle.archive, path, replace_empty=True)


def test_bad_sha_or_address_does_not_write_library(sample_bundle, tmp_path):
    path = tmp_path / "recipient/config/config.yaml"
    own = config_at(path)
    with pytest.raises(ValueError, match="SHA256"):
        library_bundle.import_bundle(sample_bundle.archive, path, expected_sha256="0" * 64)
    own["server"]["local_url"] = "not-a-url"
    path.write_text(yaml.safe_dump(own))
    before = path.read_bytes()
    with pytest.raises(ValueError, match="访问地址"):
        library_bundle.import_bundle(sample_bundle.archive, path)
    assert path.read_bytes() == before
    assert not library_bundle.database_path(own).exists()
    assert not list(Path(own["storage"]["strm_dir"]).iterdir())


def test_seturl_updates_access_file_and_rotate_token(sample_bundle, tmp_path):
    path = tmp_path / "recipient/config/config.yaml"
    config_at(path)
    library_bundle.import_bundle(sample_bundle.archive, path)
    before = yaml.safe_load(path.read_text())["server"]["stream_token"]
    deploy.set_url(path, "https://new.example", "https://new.example")
    assert "https://new.example" in (path.parent / "access.txt").read_text()
    deploy.rotate_token(path)
    after = yaml.safe_load(path.read_text())["server"]["stream_token"]
    assert before != after
    assert after in (tmp_path / "recipient/strm/电影/Test/Test.strm").read_text()


@pytest.mark.parametrize("name", ["../oops.nfo", "/strm/oops.nfo", "strm/../oops.nfo", "config/config.yaml", "strm/x.mp4", "strm\\x.nfo"])
def test_archive_paths_are_allowlisted(name):
    with pytest.raises(ValueError):
        library_bundle.safe_archive_name(name)


def test_symlink_archive_member_is_rejected(tmp_path):
    archive = tmp_path / "bad.tar.gz"
    content = b"x"
    manifest = {"schema_version": 1, "total_bytes": 1,
                "files": {"strm/x.nfo": {"size": 1, "sha256": hashlib.sha256(content).hexdigest()}}}
    data = json.dumps(manifest).encode()
    with tarfile.open(archive, "w:gz") as tar:
        info = tarfile.TarInfo("manifest.json")
        info.size = len(data)
        tar.addfile(info, io.BytesIO(data))
        info = tarfile.TarInfo("strm/x.nfo")
        info.type = tarfile.SYMTYPE
        info.linkname = "/etc/passwd"
        tar.addfile(info)
    with pytest.raises(ValueError, match="链接"):
        library_bundle.extract_verified(archive, tmp_path)


def test_proxy_roundtrip_without_executing_env(tmp_path):
    path = tmp_path / ".env"
    value = "http://fake:fake%24password@[::1]:7890"
    envfile.set_value(path, "SCRAPER_PROXY", value)
    assert envfile.get_value(path, "SCRAPER_PROXY") == value
    parsed = deploy.proxy_from_url(value)
    assert parsed["host"] == "::1" and parsed["password"] == "fake$password"
    assert path.stat().st_mode & 0o777 == 0o600


def test_upload_selects_parts_not_whole_and_requires_contiguous_parts(tmp_path):
    for name in ("manifest.json", "checksums.sha256", "tgemby-library.tar.gz", "tgemby-library.tar.gz.part000", "tgemby-library.tar.gz.part001"):
        (tmp_path / name).write_bytes(b"fixture")
    (tmp_path / "checksums.sha256").write_text("")
    names = [path.name for path in upload_tg.pick_files(tmp_path)]
    assert "tgemby-library.tar.gz" not in names
    assert "tgemby-library.tar.gz.part001" in names
    (tmp_path / "tgemby-library.tar.gz.part000").unlink()
    with pytest.raises(ValueError, match="不连续"):
        upload_tg.pick_files(tmp_path)


def test_upload_detects_missing_last_part_from_checksums(tmp_path):
    (tmp_path / "tgemby-library.tar.gz.part000").write_bytes(b"fixture")
    (tmp_path / "checksums.sha256").write_text("0" * 64 + "  tgemby-library.tar.gz.part001\n")
    with pytest.raises(ValueError, match="最后一卷"):
        upload_tg.pick_files(tmp_path)


def test_upload_rejects_wrong_checksum(tmp_path):
    file = tmp_path / "file.tar.gz"
    file.write_bytes(b"fixture")
    (tmp_path / "checksums.sha256").write_text("0" * 64 + "  file.tar.gz\n")
    with pytest.raises(ValueError, match="不一致"):
        upload_tg.prepare_files([file])


@pytest.mark.asyncio
async def test_telegram_code_logs_do_not_contain_phone_or_hash(monkeypatch, caplog):
    from app.telegram import client as telegram_client

    phone = "+10000000001"
    code_hash = "fixture-private-code-hash"
    client = SimpleNamespace(connect=AsyncMock(), disconnect=AsyncMock(),
                             send_code=AsyncMock(return_value=SimpleNamespace(phone_code_hash=code_hash)))
    monkeypatch.setattr(telegram_client, "_auth_client", None)
    monkeypatch.setattr(telegram_client, "_phone_code_hash", None)
    monkeypatch.setattr(telegram_client, "_make_client", lambda: client)
    caplog.set_level("INFO", logger="app.telegram.client")
    assert (await telegram_client.send_code(phone))["success"]
    assert phone not in caplog.text and code_hash not in caplog.text
    caplog.clear()
    client.send_code.side_effect = RuntimeError(phone + code_hash)
    assert not (await telegram_client.send_code(phone))["success"]
    assert phone not in caplog.text and code_hash not in caplog.text


@pytest.mark.parametrize("value,expected", [("false", False), ("true", True)])
def test_uvicorn_access_logs_respect_privacy_setting(monkeypatch, value, expected):
    import runpy
    import uvicorn

    options = []
    monkeypatch.setenv("TGEMBY_ACCESS_LOG", value)
    monkeypatch.setattr(uvicorn, "run", lambda *args, **kwargs: options.append(kwargs))
    runpy.run_path(str(Path(__file__).parents[1] / "run.py"), run_name="__main__")
    assert options[0]["access_log"] is expected


@pytest.mark.asyncio
async def test_upload_honors_config_and_skips_already_sent_files(tmp_path, monkeypatch):
    import inspect
    import pyrogram

    signature = inspect.signature(pyrogram.Client.send_document)
    config = tmp_path / "private.yaml"
    config.write_text(yaml.safe_dump({"telegram": {"api_id": 1, "api_hash": "fixture-api-hash",
                                                    "session_string": "fixture-session-string"}}))
    file = tmp_path / "file.tar.gz"
    file.write_bytes(b"fixture")
    sender = AsyncMock(return_value=SimpleNamespace(id=7, document=SimpleNamespace(file_size=7)))
    options = []

    async def validated_sender(*args, **kwargs):
        signature.bind(None, *args, **kwargs)  # Use the real library signature, not a permissive mock.
        return await sender(*args, **kwargs)

    class FakeClient:
        def __init__(self, *args, **kwargs):
            options.append(kwargs)
            self.get_chat = AsyncMock(return_value=SimpleNamespace(id=1))
            self.send_document = validated_sender

        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            return None

    monkeypatch.setattr(pyrogram, "Client", FakeClient)
    state = tmp_path / ".upload-state.json"
    hashes = upload_tg.prepare_files([file])
    assert await upload_tg.run("me", [file], "测试", config=config, state_path=state, hashes=hashes) == 1
    assert await upload_tg.run("me", [file], "测试", config=config, state_path=state, hashes=hashes) == 0
    assert sender.await_count == 1
    assert options[0]["session_string"] == "fixture-session-string"
    assert "fixture-session-string" not in state.read_text()
    assert state.stat().st_mode & 0o777 == 0o600


def test_source_bundle_omits_config_and_scrubs_ui_example(tmp_path):
    source = tmp_path / "source"
    page = source / "app/static/index.html"
    page.parent.mkdir(parents=True)
    page.write_text('<input id="s_tmdb_key" placeholder="example-private-value">')
    (source / "config.yaml").write_text("password: not-to-be-shared")
    check_release.bundle(source, tmp_path / "release")
    with tarfile.open(tmp_path / "release/tgemby-source.tar.gz") as tar:
        assert "tgemby/config.yaml" not in tar.getnames()
        assert b"example-private-value" not in tar.extractfile("tgemby/app/static/index.html").read()
    assert "example-private-value" in page.read_text()  # The author's live tree is untouched.
