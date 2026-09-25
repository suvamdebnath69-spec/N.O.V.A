"""
test_research_mode.py — Research Mode tests.

Real pipeline logic with MOCKED web fetchers (deterministic, offline-safe,
no network flakiness) + real rule routing + a REAL PDF written to a temp
dir. Nothing touches the user's actual Desktop except live verification.
"""

import os
import sys
import tempfile
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import actions.research as research  # noqa: E402
from core.fast_path import fast_path  # noqa: E402


FAKE_SECTIONS = [
    ("Overview — Solar panel", "A solar panel converts sunlight into electricity. "
     "It is assembled from photovoltaic cells. Efficiency depends on the cells."),
    ("Related — Photovoltaics", "Photovoltaics is the conversion of light into "
     "electricity using semiconducting materials."),
]
FAKE_SOURCES = [
    ("How solar panels work", "https://example.com/solar", "Solar panels convert light."),
    ("Solar guide", "https://example.org/guide", "A guide to solar energy."),
]


class TestRouting(unittest.TestCase):
    def test_mode_keywords(self):
        cases = {
            "enter research mode": "RESEARCH_MODE_ON",
            "please enter research mode": "RESEARCH_MODE_ON",
            "start research mode": "RESEARCH_MODE_ON",
            "open research mode": "RESEARCH_MODE_ON",
            "research mode": "RESEARCH_MODE_ON",
            "exit research mode": "RESEARCH_OFF",
            "leave research mode": "RESEARCH_OFF",
            "stop research mode": "RESEARCH_OFF",
        }
        for text, want in cases.items():
            u = fast_path.understand(text)
            self.assertEqual(u["intent"], want, f"{text!r}")
            self.assertEqual(u["source"], "rule", f"{text!r} should be instant")

    def test_bare_research_topic(self):
        u = fast_path.understand("research quantum computing")
        self.assertEqual(u["intent"], "RESEARCH")
        self.assertEqual(
            research.clean_topic(u["params"].get("topic3", "")),
            "quantum computing",
        )

    def test_research_mode_not_parsed_as_topic(self):
        u = fast_path.understand("enter research mode")
        self.assertNotEqual(u["intent"], "RESEARCH")

    def test_topic_cleaning(self):
        self.assertEqual(research.clean_topic("research quantum computing"), "quantum computing")
        self.assertEqual(research.clean_topic("prepare research on solar panels"), "solar panels")
        self.assertEqual(research.clean_topic("please research black holes for me"), "black holes")


class TestPdf(unittest.TestCase):
    def test_write_pdf_real_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            with mock.patch.object(research, "desktop_dir", return_value=tmp):
                path = research.write_pdf("solar panels", "Sunlight becomes power.",
                                          FAKE_SECTIONS, FAKE_SOURCES)
                self.assertTrue(os.path.exists(path))
                self.assertTrue(path.endswith(".pdf"))
                with open(path, "rb") as f:
                    head = f.read(5)
                self.assertEqual(head, b"%PDF-", "not a valid PDF header")
                self.assertIn("Research_solar_panels_", os.path.basename(path))

    def test_pdf_survives_unicode(self):
        """Em-dashes, curly quotes etc. must not crash fpdf2's latin-1 fonts."""
        with tempfile.TemporaryDirectory() as tmp:
            with mock.patch.object(research, "desktop_dir", return_value=tmp):
                path = research.write_pdf(
                    "unicode topic", "Curly \u2019quotes\u2019 and \u2014 dashes.",
                    [("S \u2014 fancy", "Bullet \u2022 point")], FAKE_SOURCES)
                self.assertTrue(os.path.exists(path))

    def test_safe_filename(self):
        self.assertEqual(research.safe_filename("Solar Panels: A Study!"), "solar_panels_a_study")


class TestPipeline(unittest.TestCase):
    def _fake_wiki(self, topic):
        return ("Solar panel", "A panel makes electricity.", ["Photovoltaics", "Solar energy"])

    def test_run_blocking_composes_and_saves(self):
        with tempfile.TemporaryDirectory() as tmp:
            with mock.patch.object(research, "fetch_wikipedia_summary", self._fake_wiki), \
                 mock.patch.object(research, "fetch_related_extracts",
                                   lambda titles: FAKE_SECTIONS[1:]), \
                 mock.patch.object(research, "fetch_web_sources",
                                   lambda topic, limit=3: FAKE_SOURCES), \
                 mock.patch.object(research, "compose_summary",
                                   lambda topic, sections: "Summary text."), \
                 mock.patch.object(research, "desktop_dir", return_value=tmp), \
                 mock.patch.object(research, "open_file"):
                reply = research.run_blocking("research solar panels")
        self.assertIn("complete", reply.lower())
        self.assertIn(".pdf", reply.lower())

    def test_run_blocking_no_material(self):
        with mock.patch.object(research, "fetch_wikipedia_summary", lambda t: None), \
             mock.patch.object(research, "fetch_web_sources", lambda t, limit=3: []):
            reply = research.run_blocking("gibberish topic xyz")
        self.assertIn("nothing", reply.lower())

    def test_compose_summary_extractive_fallback(self):
        """With Ollama unreachable, summary still comes from the material."""
        with mock.patch("requests.post", side_effect=Exception("offline")):
            summary = research.compose_summary("solar", FAKE_SECTIONS)
        self.assertTrue(summary.strip())
        self.assertIn("solar", summary.lower())


if __name__ == "__main__":
    unittest.main(verbosity=2)
