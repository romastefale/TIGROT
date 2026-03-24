import os
import sys
from pathlib import Path
from unittest.mock import Mock, patch

from telegram.ext import Application

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

os.environ.setdefault("TELEGRAM_TOKEN", "test-token")

import pidrofmbot as bot


def test_escape_markdown():
    assert bot.escape_markdown("a_b*c[`") == r"a\_b\*c\[\`"


def test_normalize_query():
    assert bot.normalize_query("Daft---Punk__ Harder  Better") == "Daft Punk Harder Better"


def test_score_track_prefers_exact_match():
    track = {"title": "Hello", "artist": {"name": "Adele"}}
    assert bot.score_track(track, "hello") > 0


def test_normalize_webhook_url_adds_https():
    assert bot._normalize_webhook_url("example.com/") == "https://example.com"


def test_load_settings_auto_mode_webhook_when_url_present():
    with patch.dict(
        os.environ,
        {
            "TELEGRAM_TOKEN": "123:abc",
            "RUN_MODE": "auto",
            "WEBHOOK_URL": "pidrofmbot-v2-production.up.railway.app",
        },
        clear=False,
    ):
        settings = bot.load_settings()

    assert settings.run_mode == "webhook"
    assert settings.webhook_url == "https://pidrofmbot-v2-production.up.railway.app"


def test_track_cache_roundtrip():
    bot.selection_cache.clear()
    key = bot.save_track({"title": "S", "artist": "A", "album": "B", "deezer_url": "x", "lyrics_url": ""})
    assert bot.get_track(key)["title"] == "S"
def test_normalize_webhook_url_adds_https_and_trims_slash():
    assert bot.normalize_webhook_url("pidrofmbot-v2-production.up.railway.app/") == "https://pidrofmbot-v2-production.up.railway.app"


def test_detect_railway_public_url_prefers_explicit_webhook_url():
    with patch.dict(os.environ, {"WEBHOOK_URL": "https://custom.example.com", "RAILWAY_PUBLIC_DOMAIN": "railway.example.com"}, clear=False):
        assert bot.detect_railway_public_url() == "https://custom.example.com"


def test_build_webhook_target_url():
    assert bot.build_webhook_target_url("https://pidrofmbot-v2-production.up.railway.app", "123:abc") == "https://pidrofmbot-v2-production.up.railway.app/123:abc"


def test_score_track_prefers_exact_title():
    track = {"title": "Hello", "artist": {"name": "Adele"}}
    assert bot.score_track(track, "hello") > 0


def test_cache_roundtrip():
    bot.cache.clear()
    bot.set_cache("demo", [{"title": "Song"}])
    assert bot.get_cache("demo") == [{"title": "Song"}]


def test_music_cache_roundtrip():
    bot.music_cache.clear()
    key = bot.store_music({"title": "Song", "artist": "Artist", "album": "Album", "url": "u", "deezer_url": "d"})
    assert bot.get_music(key)["title"] == "Song"


def test_search_session_roundtrip():
    bot.search_sessions.clear()
    sid = bot.save_search_session("hello", [{"title": "Hello"}])
    assert bot.get_search_session(sid)["query"] == "hello"


def test_search_deezer_sync_parses_payload():
    fake = Mock()
    fake.status_code = 200
    fake.json.return_value = {
free        "data": [
            {
                "title": "Hello",
                "artist": {"name": "Adele"},
                "album": {"title": "25", "cover_big": "https://example.com/cover.jpg"},
                "link": "https://deezer.example/hello",
                "preview": "https://preview.example/hello.mp3",
            }
        ]
    }

    with patch.object(bot.http, "get", return_value=fake):
        result = bot._search_deezer_sync("hello")

    assert result[0]["title"] == "Hello"
    assert result[0]["artist"] == "Adele"


def test_fetch_lyrics_sync_fallback_lyrics_ovh():
    track = {"title": "Hello", "artist": "Adele", "lyrics_url": ""}

    genius_resp = Mock(status_code=404)
    ovh_resp = Mock(status_code=200)
    ovh_resp.json.return_value = {"lyrics": "line1\nline2"}

    with patch.object(bot.http, "get", side_effect=[ovh_resp]):
        lyrics, source = bot._fetch_lyrics_sync(track)

    assert "line1" in lyrics
    assert source == "lyrics.ovh"


def test_build_application_registers_handlers():
    settings = bot.Settings(
        telegram_token="123:abc",
        run_mode="polling",
        webhook_url=None,
        webhook_secret="secret",
        port=8443,
        genius_api_key=None,
        openai_api_key=None,
    )
    app = bot.build_application(settings)

    assert isinstance(app, Application)
    assert app.handlers


def test_run_webhook_path_calls_run_webhook():
    settings = bot.Settings(
        telegram_token="123:abc",
        run_mode="webhook",
        webhook_url="https://pidrofmbot-v2-production.up.railway.app",
        webhook_secret="secret",
        port=8443,
        genius_api_key=None,
        openai_api_key=None,
    )

    app = Mock()
    with patch.object(bot, "build_application", return_value=app):
        bot.run(settings)

    app.run_webhook.assert_called_once()
def test_run_bot_uses_webhook_when_public_url_exists():
    app = Mock()
    settings = bot.Settings(
        token="123:abc",
        genius_api_key=None,
        openai_api_key=None,
        webhook_url="https://pidrofmbot-v2-production.up.railway.app",
        webhook_secret="secret",
        port=8443,
        railway_public_domain="https://pidrofmbot-v2-production.up.railway.app",
    )

    bot.run_bot(app, settings)

    app.run_webhook.assert_called_once()
    app.run_polling.assert_not_called()
