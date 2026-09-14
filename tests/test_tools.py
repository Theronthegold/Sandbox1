"""lib.tools 검증. 네트워크/ffmpeg 없이 돌아가는 순수 로직만 테스트."""

import tempfile
import unittest
from pathlib import Path

from lib.tools import music, stock, subtitles
from lib.tools.ffmpeg import escape_filter_path
from lib.tools.tts import TTSResult, WordTiming, absolute_words


class TTSTimelineTest(unittest.TestCase):
    def test_absolute_words_offsets_by_prev_durations(self):
        r0 = TTSResult(Path("a.mp3"), 2.0, [WordTiming("a", 0.1, 0.5)])
        r1 = TTSResult(Path("b.mp3"), 3.0, [WordTiming("b", 0.2, 0.6), WordTiming("c", 0.7, 1.0)])
        out = absolute_words([r0, r1])
        self.assertAlmostEqual(out[0][0].start, 0.1)
        self.assertAlmostEqual(out[1][0].start, 2.2)
        self.assertAlmostEqual(out[1][1].end, 3.0)


class SubtitlesTest(unittest.TestCase):
    def words(self):
        return [WordTiming(t, i * 0.5, i * 0.5 + 0.4) for i, t in enumerate(["매달", "월급이", "통장을", "스쳐", "지나가는"])]

    def test_chunks_into_lines_of_max_words(self):
        ass = subtitles.build_ass([subtitles.SceneWords(self.words(), preset="pop")])  # max_words=3
        lines = [l for l in ass.splitlines() if l.startswith("Dialogue")]
        self.assertEqual(len(lines), 2)
        self.assertIn("매달", lines[0])
        self.assertIn("스쳐", lines[1])

    def test_karaoke_and_emphasis_tags(self):
        ass = subtitles.build_ass([subtitles.SceneWords(self.words(), emphasis=["월급이"])])
        self.assertIn("\\k", ass)
        self.assertIn(f"\\1c{subtitles.YELLOW}}}월급이", ass)
        self.assertIn(f"\\1c{subtitles.WHITE}}}매달", ass)

    def test_timestamps_format(self):
        self.assertEqual(subtitles._ts(0), "0:00:00.00")
        self.assertEqual(subtitles._ts(65.5), "0:01:05.50")
        self.assertEqual(subtitles._ts(3600 + 1.234), "1:00:01.23")

    def test_unknown_preset_falls_back(self):
        ass = subtitles.build_ass([subtitles.SceneWords(self.words(), preset="nope")])
        self.assertIn("Dialogue", ass)

    def test_braces_escaped(self):
        ass = subtitles.build_ass([subtitles.SceneWords([WordTiming("a{b}", 0, 1)])])
        self.assertIn("a(b)", ass)


class MusicTest(unittest.TestCase):
    def test_none_when_dir_missing_or_empty(self):
        self.assertIsNone(music.pick_track("calm", music_dir=Path("does/not/exist")))
        with tempfile.TemporaryDirectory() as d:
            self.assertIsNone(music.pick_track("calm", music_dir=Path(d)))

    def test_mood_prefix_preferred_then_fallback(self):
        with tempfile.TemporaryDirectory() as d:
            (Path(d) / "calm_a.mp3").write_bytes(b"x")
            (Path(d) / "upbeat_b.mp3").write_bytes(b"x")
            (Path(d) / "readme.txt").write_text("ignore")
            self.assertEqual(music.pick_track("calm", music_dir=Path(d)).name, "calm_a.mp3")
            self.assertEqual(music.pick_track("upbeat", music_dir=Path(d)).name, "upbeat_b.mp3")
            picked = music.pick_track("tense", music_dir=Path(d), seed=1)   # 없는 mood → 아무거나
            self.assertIn(picked.suffix, music.AUDIO_EXT)


class StockTest(unittest.TestCase):
    def test_pick_file_prefers_smallest_portrait_at_least_1920(self):
        files = [
            {"width": 1920, "height": 1080, "link": "land"},
            {"width": 720, "height": 1280, "link": "small"},
            {"width": 1080, "height": 1920, "link": "hd"},
            {"width": 2160, "height": 3840, "link": "uhd"},
        ]
        self.assertEqual(stock._pick_file(files)["link"], "hd")

    def test_pick_file_largest_portrait_when_none_reach_1920(self):
        files = [{"width": 540, "height": 960, "link": "s"}, {"width": 720, "height": 1280, "link": "m"}]
        self.assertEqual(stock._pick_file(files)["link"], "m")

    def test_pick_file_none_without_portrait(self):
        self.assertIsNone(stock._pick_file([{"width": 1920, "height": 1080, "link": "x"}]))

    def test_client_requires_key(self):
        import os
        saved = os.environ.pop("PEXELS_API_KEY", None)
        try:
            with self.assertRaises(stock.StockError):
                stock.PexelsClient(cache_dir=Path(tempfile.mkdtemp()))
        finally:
            if saved:
                os.environ["PEXELS_API_KEY"] = saved


class FFmpegUtilTest(unittest.TestCase):
    def test_escape_filter_path_windows_drive(self):
        out = escape_filter_path(r"C:\work\subs.ass")
        self.assertTrue(out.startswith("C\\:/"))
        self.assertTrue(out.endswith("subs.ass"))


if __name__ == "__main__":
    unittest.main()
