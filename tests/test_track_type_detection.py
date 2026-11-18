import os
import tempfile
import unittest

import database
import fetcher

TEST_DB_FILE = os.path.join(tempfile.gettempdir(), "test_track_type_detection.db")

def setUpModule():
    database.DB_NAME = TEST_DB_FILE
    database.init_db()

def tearDownModule():
    database.DB_NAME = str(database.BASE_DIR / "kpop_ratings.db")
    for suffix in ["", "-wal", "-shm"]:
        p = TEST_DB_FILE + suffix
        if os.path.exists(p):
            try:
                os.remove(p)
            except Exception:
                pass


class TestTrackTypeDetection(unittest.TestCase):

    def test_alt_lang_japanese_variations(self):
        """Backlog #14: Japanese edition variations (ed, ver, rel, standalone)."""
        titles = [
            "WAVE -Japanese ed.-",
            "WAVE (Japanese Version)",
            "WAVE (Japanese ver.)",
            "WAVE (JP ed)",
            "WAVE (JPN Edition)",
            "WAVE (Jap. rel)",
            "WAVE (JP)",
            "WAVE -Japanese-",
            "ELEVEN -Japanese ver.-",
            "BEEP (Japan Edition)",
            "Song (Version Japanese)",
            "Pepe (JP)",
            "First Love (JP)",
        ]
        for t in titles:
            self.assertEqual(fetcher.detect_track_type(t), "alt_lang", f"Failed to detect alt_lang for '{t}'")

    def test_alt_lang_english_variations(self):
        """Backlog #14: English edition variations."""
        titles = [
            "Song (Eng. Version)",
            "Song (ENG ver)",
            "Song [ENG]",
            "Song (English)",
            "Baby It’s Both (Tick-Tack English ver.)",
            "Time Lapse (English version)",
            "CAN'T NOBODY (ENG VER)",
        ]
        for t in titles:
            self.assertEqual(fetcher.detect_track_type(t), "alt_lang", f"Failed to detect alt_lang for '{t}'")

    def test_alt_lang_chinese_variations(self):
        """Backlog #14: Chinese edition variations."""
        titles = [
            "Song (Chinese ver.)",
            "Song (CHN Version)",
            "Song [CHN]",
            "Song (Chinese)",
            "HWAA(火/花) (Chinese ver.)",
            "Teddy Bear (Chinese ver.)",
        ]
        for t in titles:
            self.assertEqual(fetcher.detect_track_type(t), "alt_lang", f"Failed to detect alt_lang for '{t}'")

    def test_alt_lang_korean_variations(self):
        """Backlog #14: Korean edition variations."""
        titles = [
            "Song (Korean ver.)",
            "Song (Kor. ed.)",
            "Song [KR]",
            "Song -Korean release-",
            "Ghost Light (Korean)",
            "Will -Korean version-",
            "B.L.E.S.S.E.D [KR ver.]",
            "Poppy (Korean ver.)",
        ]
        for t in titles:
            self.assertEqual(fetcher.detect_track_type(t), "alt_lang", f"Failed to detect alt_lang for '{t}'")

    def test_non_canon_precedence_over_alt_lang(self):
        """Finding: Remixes, sped-up, and instrumental alt-lang tracks must strictly be non_canon."""
        non_canon_titles = [
            "DDU-DU DDU-DU (remix) -JP ver.-",
            "EASY (English ver.) (sped up ver.)",
            "Bubble (sped up) (English ver.)",
            "EASY (English ver.) (slowed + reverb ver.)",
            "Supernova (Inst.) (Japanese ver.)",
        ]
        for t in non_canon_titles:
            self.assertEqual(fetcher.detect_track_type(t), "non_canon", f"Non-canon should take precedence for '{t}'")

    def test_non_canon_standard_patterns(self):
        """Intros, outros, skits, interludes, and instrumental variations must detect as non_canon."""
        standard_non_canon = [
            "Intro : The Beginning",
            "Intro",
            "Outro : Day 1",
            "Outro",
            "Interlude : Shadow",
            "Skit #1",
            "Song (Instrumental)",
            "Song (Inst.)",
            "Song (Acoustic Ver)",
            "Song (Club Ver)",
            "Song (Karaoke)",
            "Prologue",
            "Epilogue",
        ]
        for t in standard_non_canon:
            self.assertEqual(fetcher.detect_track_type(t), "non_canon", f"Failed non_canon detection for '{t}'")

    def test_negative_guardrails_no_false_positives(self):
        """Standard titles containing language words must remain 'core'."""
        core_titles = [
            "Lost in Japan",
            "Englishman in New York",
            "Korean BBQ",
            "Forever Young",
            "Overdrive",
            "Revenge",
            "Love Me",
            "Overdrive (Special Track)",
        ]
        for t in core_titles:
            self.assertEqual(fetcher.detect_track_type(t), "core", f"False positive detected for '{t}'")

    def test_empty_and_none_titles(self):
        """Empty or None titles must safely default to 'core'."""
        self.assertEqual(fetcher.detect_track_type(""), "core")
        self.assertEqual(fetcher.detect_track_type(None), "core")


if __name__ == "__main__":
    unittest.main()
