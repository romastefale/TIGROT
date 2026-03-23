diff --git a/tests/test_pidrofmbot.py b/tests/test_pidrofmbot.py
new file mode 100644
index 0000000000000000000000000000000000000000..09d1ee09fc4b5a533014c4773bed1955ac327dbe
--- /dev/null
+++ b/tests/test_pidrofmbot.py
@@ -0,0 +1,64 @@
+import importlib
+import os
+import sys
+from pathlib import Path
+from unittest.mock import Mock, patch
+
+ROOT = Path(__file__).resolve().parents[1]
+if str(ROOT) not in sys.path:
+    sys.path.insert(0, str(ROOT))
+
+os.environ.setdefault("TELEGRAM_TOKEN", "test-token")
+
+bot = importlib.import_module("pidrofmbot")
+
+
+def test_escape_markdown():
+    assert bot.escape_markdown("a_b*c[`") == r"a\_b\*c\[\`"
+
+
+def test_normalize_query():
+    assert bot.normalize_query("Daft---Punk__ Harder  Better") == "Daft Punk Harder Better"
+
+
+def test_score_track_prefers_exact_title():
+    track = {"title": "Hello", "artist": {"name": "Adele"}}
+    assert bot.score_track(track, "hello") > 0
+
+
+def test_cache_roundtrip():
+    bot.cache.clear()
+    bot.set_cache("demo", [{"title": "Song"}])
+    assert bot.get_cache("demo") == [{"title": "Song"}]
+
+
+def test_search_deezer_sync_success_uses_api_response():
+    bot.cache.clear()
+    fake_response = Mock()
+    fake_response.status_code = 200
+    fake_response.json.return_value = {
+        "data": [
+            {
+                "title": "Hello",
+                "artist": {"name": "Adele"},
+                "album": {"title": "25", "cover_big": "https://example.com/cover.jpg"},
+            }
+        ]
+    }
+
+    with patch.object(bot.session, "get", return_value=fake_response) as mocked_get:
+        tracks = bot._search_deezer_sync("hello")
+
+    assert len(tracks) == 1
+    mocked_get.assert_called_once()
+
+
+def test_search_deezer_sync_returns_empty_on_non_200():
+    bot.cache.clear()
+    fake_response = Mock()
+    fake_response.status_code = 500
+
+    with patch.object(bot.session, "get", return_value=fake_response):
+        tracks = bot._search_deezer_sync("hello")
+
+    assert tracks == []
