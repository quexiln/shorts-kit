"""Offline tests: direct-URL footage, letterbox vs dark footage, transient QA findings, multi-beat sources."""
import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import media  # noqa: E402
import meta  # noqa: E402
import qa  # noqa: E402
from common import Job  # noqa: E402


def frame(top=0, blob=False, h=480, w=270):
    fr = np.full((h, w, 3), 90, np.uint8)
    fr[:top] = 0
    if blob:  # dark scene: black space with a lit object that reaches up to row 120
        fr[:] = 0
        fr[120:300, 80:190] = 140
    return fr


class Bars(unittest.TestCase):
    def test_letterbox_is_black_bar(self):
        self.assertIn("black bar top", qa.frame_checks(frame(top=40)))

    def test_dark_footage_is_ambiguous(self):
        out = qa.frame_checks(frame(blob=True))
        self.assertTrue(any(m.startswith("dark band at top") and "dark footage" in m for m in out), out)
        self.assertNotIn("black bar top", out)

    def test_clean_frame(self):
        self.assertEqual(qa.frame_checks(frame()), [])

    def test_transient_dropped_unless_neighbour_agrees(self):
        notes = {3: ["black bar top"]}
        with mock.patch.object(qa, "media_size", return_value=(270, 480)), \
                mock.patch.object(qa, "_grab_at", return_value=frame()):
            self.assertEqual(qa.drop_transient("v.mp4", notes, 10), {})
        with mock.patch.object(qa, "media_size", return_value=(270, 480)), \
                mock.patch.object(qa, "_grab_at", return_value=frame(top=40)):
            self.assertEqual(qa.drop_transient("v.mp4", notes, 10), notes)
        with mock.patch.object(qa, "_grab_at", side_effect=AssertionError):  # adjacent sampled frames: no recheck
            self.assertEqual(qa.drop_transient("v.mp4", {3: ["x"], 4: ["x"]}, 10), {3: ["x"], 4: ["x"]})


class DirectUrl(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        Path(self.tmp.name, "job.json").write_text(json.dumps({"slug": "t", "beats": []}), encoding="utf-8")
        self.job = Job(self.tmp.name, channel={})

    def tearDown(self):
        self.tmp.cleanup()

    def test_format_has_best_fallback(self):
        self.assertTrue(media.YTDLP_FORMAT.endswith("/b"))

    def test_direct_mp4_uses_ffmpeg_not_ytdlp(self):
        calls = []

        def run(cmd, **kw):
            calls.append(cmd)
            Path(cmd[-1]).write_bytes(b"x")
            return mock.Mock(returncode=0, stderr="")
        with mock.patch.object(media.subprocess, "run", run), mock.patch.object(media, "media_size",
                                                                                return_value=(1280, 720)):
            rec = media.get(self.job, "https://cdn.example.org/a/clip.mp4?x=1", "0:02", "0:07", "PD", "NASA")
        self.assertEqual(calls[0][0], "ffmpeg")
        self.assertEqual(calls[0][calls[0].index("-ss") + 1], "2.00")
        self.assertEqual((rec["width"], rec["height"], rec["kind"]), (1280, 720, "video"))

    def test_image_extension_downloads_directly(self):
        class R:
            headers = {"Content-Type": "application/octet-stream"}
            def __enter__(self): return self
            def __exit__(self, *a): pass
            def read(self, n): return b"img"
        with mock.patch.object(media.urllib.request, "urlopen", return_value=R()), \
                mock.patch.object(media, "media_size", return_value=(100, 100)):
            rec = media.get(self.job, "https://x.org/p.jpg", rights="PD", credit="X")
        self.assertEqual((rec["kind"], rec["file"]), ("image", "img/p01.jpg"))


class Sources(unittest.TestCase):
    def test_every_beat_listed(self):
        with tempfile.TemporaryDirectory() as d:
            s = {"name": "City Gazette", "date": "2026-10-07", "url": "https://g.example/a"}
            beats = [{"id": "b1", "source": s}, {"id": "b2"}, {"id": "b3", "source": dict(s)},
                     {"id": "b4", "source": {"name": "Other", "url": "u"}}]
            Path(d, "job.json").write_text(json.dumps({"slug": "t", "date": "2026-10-07", "beats": beats}), "utf-8")
            job = Job(d, channel={})
            src = meta.collect_sources(job)
            self.assertEqual([x["beats"] for x in src], [["b1", "b3"], ["b4"]])
            meta.write_sources_md(job, {"slug": "t", "date": "2026-10-07", "sources": src, "media": []})
            self.assertIn("- City Gazette (2026-10-07) — b1, b3: https://g.example/a",
                          Path(d, "sources.md").read_text("utf-8"))


if __name__ == "__main__":
    unittest.main()
