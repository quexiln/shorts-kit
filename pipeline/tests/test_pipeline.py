"""Offline, fast tests (no network, TTS, whisper or render). Run from repo root:
python -m unittest discover -s pipeline/tests"""
import copy
import json
import os
import shutil
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import checks  # noqa: E402
import cleanup  # noqa: E402
import make_video  # noqa: E402
import meta  # noqa: E402
from common import DEFAULT_CHANNEL, ROOT, Job, PipelineError, expand_spoken, lower, norm, parse_text, script_tokens, \
    tts_line, upper  # noqa: E402
from timeline import build_props, split_window, validate_props  # noqa: E402

DEMO = json.loads((ROOT / "examples" / "demo" / "job.json").read_text(encoding="utf-8"))

# props contract, copied from the spec (remotion/src/schema.ts)
TOP = {"durationSec", "theme", "voice", "music", "sfx", "words", "captionsOff", "hook", "shots", "overlays", "outro"}
REQUIRED = {"durationSec", "theme", "voice", "words", "hook", "shots", "overlays", "outro"}
THEME = {"accent", "text", "dark", "font", "channelName", "handle", "illustrativeLabel"}
SHOT = {"src", "kind", "at", "dur", "from", "focusX", "focusY", "zoom", "illustrative", "credit"}
OVERLAY = {"source": ({"type", "at", "dur", "source"}, {"date"}),
           "stat": ({"type", "at", "dur", "title", "items"}, {"unit"}),
           "quote": ({"type", "at", "dur", "quote", "name"}, {"role", "highlight"})}


def fake_words(data, wps=0.33, gap=0.35, lang="en"):
    """words.json-like timing: each script word wps seconds, gap between beats."""
    t, words = 0.1, []
    for tok in script_tokens(data):
        if words and words[-1]["beat"] != tok["beat"]:
            t += gap
        words.append({"text": tok["display"], "start": round(t, 3), "end": round(t + wps - 0.03, 3),
                      "key": tok["key"], "beat": tok["beat"]})
        t += wps
    beats = []
    for b in data["beats"]:
        ws = [w for w in words if w["beat"] == b["id"]]
        beats.append({"id": b["id"], "start": ws[0]["start"], "end": ws[-1]["end"]})
    return {"duration": round(t + 0.2, 3), "words": words, "beats": beats}


CATALOG = [{"id": f"v0{i}", "file": f"video/v0{i}.mp4", "kind": "video", "from": 0, "to": 6.0, "credit": "Demo",
            "rights": "CC0", "srcUrl": "https://example.com", "illustrative": i == 4} for i in range(1, 5)]


class TempJob:
    def __init__(self, data=None):
        self.tmp = Path(tempfile.mkdtemp())
        self.dir = self.tmp / "jobs" / "2026-10-08" / "demo"
        self.dir.mkdir(parents=True)
        (self.dir / "job.json").write_text(json.dumps(data or DEMO), encoding="utf-8")
        self.job = Job(self.dir, channel=copy.deepcopy(DEFAULT_CHANNEL))

    def close(self):
        shutil.rmtree(self.tmp, ignore_errors=True)


class TextTests(unittest.TestCase):
    def test_parse_keys_alias_tags(self):
        t = parse_text("The *new rule* starts *Monday*, *40%|forty_percent* <short pause> more New_York|new_york.")
        self.assertEqual([x["display"] for x in t],
                         ["The", "new", "rule", "starts", "Monday,", "40%", "more", "New York."])
        self.assertEqual([x["key"] for x in t], [False, True, True, False, True, True, False, False])
        self.assertEqual(t[5]["tts"], "forty percent")
        self.assertEqual(t[7]["tts"], "new york.")

    def test_tts_line_keeps_tags(self):
        self.assertEqual(tts_line("Hi *there* <Short  Pause> 5|five."), "Hi there <short pause> five.")

    def test_locale_casing(self):
        self.assertEqual(upper("istanbul", "tr"), "İSTANBUL")
        self.assertEqual(lower("ILIK", "tr"), "ılık")
        self.assertEqual(upper("istanbul", "en"), "ISTANBUL")
        self.assertEqual(norm("Çağrı,", "tr"), "cagri")

    def test_number_spelling(self):
        self.assertEqual(expand_spoken("%40", "tr"), ["yüzde", "kırk"])
        self.assertEqual(expand_spoken("1.500", "tr"), ["bin", "beş", "yüz"])
        self.assertEqual(expand_spoken("40%", "en")[-1], "percent")
        with mock.patch.dict(sys.modules, {"num2words": None}):
            self.assertEqual(expand_spoken("1,500", "en"), ["1500"])


class JobTests(unittest.TestCase):
    def test_demo_valid(self):
        e, w = checks.validate_job(DEMO, "en", "What do you think?")
        self.assertEqual(e, [])
        self.assertEqual(w, [])

    def test_errors(self):
        d = copy.deepcopy(DEMO)
        d["slug"] = "Bad Slug"
        d["beats"][0]["text"] = "Visit New York|new_york today."
        d["beats"][2]["overlay"]["quote"] = "We want the building admired."
        d["beats"][1]["source"] = {"date": "2026-01-01"}
        e, _ = checks.validate_job(d, "en")
        txt = "\n".join(e)
        for part in ("kebab-case", "one unit", "verbatim", "source needs"):
            self.assertIn(part, txt)

    def test_closing_line_warning(self):
        _, w = checks.validate_job(DEMO, "en", "See you tomorrow")
        self.assertTrue(any("closing line" in x for x in w))

    def test_framing_limits(self):
        sizes = {"a": (1920, 1080), "b": (1280, 720), "c": (1280, 720), "d": (854, 480), "e": (1920, 1080)}
        items = [{"id": "a", "zoom": 1.1}, {"id": "b"}, {"id": "c", "zoom": 1.05}, {"id": "d"}, {"id": "e", "zoom": 1.2}]
        e, w = checks.check_framing(items, lambda c: sizes[c["id"]])
        self.assertEqual(sorted({x.split(":")[0] for x in e}), ["c", "d", "e"])
        self.assertEqual([x.split(":")[0] for x in w], ["b"])


class TimelineTests(unittest.TestCase):
    def setUp(self):
        self.t = TempJob()

    def tearDown(self):
        self.t.close()

    def props(self, data=None):
        data = data or self.t.job.data
        return build_props(self.t.job, fake_words(data), copy.deepcopy(CATALOG))

    def test_props_match_contract(self):
        p = self.props()
        self.assertEqual(validate_props(p), [])
        self.assertTrue(REQUIRED <= set(p) <= TOP, set(p))
        self.assertEqual(set(p["theme"]), THEME)
        self.assertEqual(set(p["hook"]), {"title", "until"})
        self.assertEqual(set(p["outro"]), {"at", "text"})
        self.assertEqual(set(p["voice"]) - {"volume"}, {"src"})
        for w in p["words"]:
            self.assertTrue({"text", "start", "end"} <= set(w) <= {"text", "start", "end", "key"})
        for r in p["captionsOff"]:
            self.assertEqual(set(r), {"from", "to"})
        for s in p["shots"]:
            self.assertTrue({"src", "kind", "at", "dur"} <= set(s) <= SHOT, s)
        types = set()
        for o in p["overlays"]:
            req, opt = OVERLAY[o["type"]]
            self.assertTrue(req <= set(o) <= req | opt, o)
            types.add(o["type"])
            if o["type"] == "stat":
                for it in o["items"]:
                    self.assertTrue({"label", "value"} <= set(it) <= {"label", "value", "highlight"})
        self.assertEqual(types, {"source", "stat", "quote"})

    def test_shots_contiguous_on_frame_grid(self):
        p = self.props()
        pos = 0
        for s in p["shots"]:
            self.assertAlmostEqual(s["at"] * 30, round(s["at"] * 30), places=2)
            self.assertLessEqual(round(s["at"] * 30) - pos, 8)
            self.assertGreaterEqual(round(s["at"] * 30), pos)
            pos = round((s["at"] + s["dur"]) * 30)
        self.assertTrue(any(s.get("illustrative") for s in p["shots"]))

    def test_outro_at_closing_line(self):
        wd = fake_words(self.t.job.data)
        p = build_props(self.t.job, wd, copy.deepcopy(CATALOG))
        what = [w for w in wd["words"] if w["text"] == "What"][-1]
        self.assertAlmostEqual(p["outro"]["at"], what["start"] - 0.05, places=2)
        self.assertEqual(p["outro"]["text"], "What do you think?")
        self.assertGreaterEqual(p["durationSec"], p["outro"]["at"] + 2.0)
        self.assertTrue(any(abs(r["from"] - p["outro"]["at"]) < 0.01 for r in p["captionsOff"]))

    def test_cue_anchors_asset(self):
        wd = fake_words(self.t.job.data)
        p = build_props(self.t.job, wd, copy.deepcopy(CATALOG))
        monday = next(w for w in wd["words"] if w["text"].startswith("Monday"))
        v02 = [s for s in p["shots"] if s["src"].endswith("v02.mp4")][0]
        self.assertLessEqual(abs(v02["at"] - monday["start"]), 0.12)

    def test_cards_turn_captions_off_and_until(self):
        p = self.props()
        stat = next(o for o in p["overlays"] if o["type"] == "stat")
        self.assertTrue(any(abs(r["from"] - stat["at"]) < 0.01 for r in p["captionsOff"]))
        about = next(w for w in p["words"] if w["text"] == "about")
        self.assertLessEqual(stat["at"] + stat["dur"], about["start"] + 0.01)

    def test_split_window(self):
        words = [{"start": i * 0.3, "end": i * 0.3 + 0.25} for i in range(20)]
        b = split_window(0, 180, words)
        self.assertEqual((b[0], b[-1]), (0, 180))
        for x, y in zip(b, b[1:]):
            self.assertTrue(30 <= y - x <= 75)

    def test_validate_catches_gap(self):
        p = self.props()
        p["shots"][1]["at"] += 0.5
        self.assertTrue(any("gap" in e for e in validate_props(p)))


class MetaTests(unittest.TestCase):
    def test_demo_ok(self):
        e, _ = meta.validate_meta(meta.platform_texts(DEMO["meta"]), DEFAULT_CHANNEL)
        self.assertEqual(e, [])

    def test_limits(self):
        m = copy.deepcopy(DEMO["meta"])
        m["youtube"]["title"] = "x" * 101 + " #Shorts"
        m["instagram"]["hashtags"] = ["#a", "#b", "#c", "#d", "#e", "#f"]
        m["tiktok"]["caption"] = "y" * 2300
        ch = copy.deepcopy(DEFAULT_CHANNEL)
        ch["bannedPublicPhrases"] = ["midnight"]
        e = "\n".join(meta.validate_meta(meta.platform_texts(m), ch)[0])
        for part in ("youtube.title", "instagram has 6", "tiktok text", "banned phrase"):
            self.assertIn(part, e)

    def test_title_needs_shorts(self):
        m = copy.deepcopy(DEMO["meta"])
        m["youtube"]["title"] = "No tag"
        self.assertTrue(any("#Shorts" in x for x in meta.validate_meta(meta.platform_texts(m), DEFAULT_CHANNEL)[0]))


class CleanupTests(unittest.TestCase):
    def setUp(self):
        self.t = TempJob()
        j = self.t.job
        for p in (j.a("video", "v01.mp4"), j.a("catalog.json"), j.a("voice.wav"), j.b("props.json")):
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_bytes(b"0" * 100)

    def tearDown(self):
        self.t.close()

    def test_refuses_without_final(self):
        with self.assertRaises(PipelineError):
            cleanup.run_cleanup(self.t.job)
        self.assertTrue(self.t.job.assets.exists())

    def test_guard_rejects_repo_root(self):
        fake = mock.Mock(dir=ROOT, json_path=ROOT / "README.md", assets=ROOT / "assets", build=ROOT / "build",
                         keep=ROOT / "keep")
        with self.assertRaises(PipelineError):
            cleanup.guard(fake)

    def test_cleanup_keeps_small_files(self):
        j = self.t.job
        (j.dir / "final.mp4").write_bytes(b"0" * 20_000)
        (j.dir / "meta.json").write_text("{}", encoding="utf-8")
        cleanup.run_cleanup(j)
        self.assertFalse(j.assets.exists() or j.build.exists())
        self.assertEqual(sorted(p.name for p in j.keep.iterdir()), ["catalog.json", "props.json", "voice.wav"])
        self.assertTrue(j.is_cleaned)
        self.assertEqual(sorted(cleanup.restore_keep(j)), ["assets/catalog.json", "assets/voice.wav",
                                                           "build/props.json"])

    def test_unreviewed_qa_blocks_cleanup(self):
        j = self.t.job
        (j.dir / "final.mp4").write_bytes(b"0" * 20_000)
        j.b("qa_report.json").write_text(json.dumps({"ok": False, "problems": ["x"]}), encoding="utf-8")
        with mock.patch("common.load_channel", return_value=copy.deepcopy(DEFAULT_CHANNEL)):
            code = make_video.main([str(j.dir), "--from", "cleanup", "--to", "cleanup"])
        self.assertEqual(code, 3)
        self.assertTrue(j.assets.exists())


class KeyTests(unittest.TestCase):
    def test_env_file_single_line(self):
        import keys
        with tempfile.TemporaryDirectory() as d:
            env = Path(d) / ".env"
            env.write_text("OTHER=1\nGEMINI_API_KEY=\"abc123\"\n", encoding="utf-8")
            with mock.patch.dict(os.environ, {}, clear=True):
                self.assertEqual(keys.find_key(env), ("abc123", ".env:GEMINI_API_KEY"))
        self.assertEqual(keys.scrub("x key=ABCDEFGHIJKLMNOPQRSTUVWX y"), "x key=*** y")


if __name__ == "__main__":
    unittest.main()
