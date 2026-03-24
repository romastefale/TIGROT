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


def test_search_session_roundtrip():
    bot.search_sessions.clear()
    sid = bot.save_search_session("hello", [{"title": "Hello"}])
    assert bot.get_search_session(sid)["query"] == "hello"


def test_search_deezer_sync_parses_payload():
    fake = Mock()
    fake.status_code = 200
    fake.json.return_value = {
        "data": [
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
