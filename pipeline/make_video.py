"""Job folder -> final.mp4 + meta.json.
  python pipeline/make_video.py <job_dir> [--check] [--from S] [--to S] [--keep] [--force] [--voice FILE]
Stages: tts, align, sfx, timeline, render, qa, meta, cleanup. Stages skip when their inputs are unchanged
(signatures in build/*_info.json). --from S reruns from S; --force ignores all caches (and is required to rerun
build stages on a cleaned job: keep/ files are restored). --voice FILE uses your narration instead of TTS.
Exit: 0 ok, 1 stage/check error, 2 job.json invalid, 3 QA findings unreviewed."""
from __future__ import annotations

import argparse
import sys
import time

from common import LIVE_STAGES, STAGES, Job, PipelineError, load_json, log, parse_text, qa_ok, setup_logging

EXIT_OK, EXIT_STAGE, EXIT_JOB, EXIT_QA = 0, 1, 2, 3


def run_check(job: Job) -> int:
    """Validate job.json, platform texts and (if present) the catalog. Writes nothing, spends no quota."""
    from checks import validate_job
    from meta import platform_texts, validate_meta
    E, W = validate_job(job.data, job.lang, job.channel.get("closingLine"))
    if "meta" in job.data:
        me, mw = validate_meta(platform_texts(job.data["meta"]), job.channel)
        E, W = E + [f"meta: {e}" for e in me], W + [f"meta: {w}" for w in mw]
    cat = "no catalog yet"
    if job.a("catalog.json").exists():
        from media import check_catalog
        ce, cw, rows = check_catalog(job)
        E, W, cat = E + [f"media: {e}" for e in ce], W + [f"media: {w}" for w in cw], f"{len(rows)} catalog items"
    for w in W:
        print(f"WARN {w}")
    for e in E:
        print(f"ERROR {e}")
    n = sum(len(t["tts"].split()) for b in job.beats for t in parse_text(b.get("text", "")))
    print(f"check {job.data.get('slug')}: {len(job.beats)} beats, {n} spoken words (~{n / 2.6:.0f}-{n / 2.2:.0f} s), "
          f"{cat}: {'OK' if not E else f'{len(E)} errors'}, {len(W)} warnings")
    return EXIT_STAGE if E else EXIT_OK


def run_stage(stage: str, job: Job, force: bool, voice: str | None) -> None:
    if stage == "tts":
        from tts import run_tts, use_voice_file
        use_voice_file(job, voice) if voice else run_tts(job, force)
    elif stage == "align":
        from align import run_align
        run_align(job, force)
    elif stage == "sfx":
        from sfx import run_sfx
        run_sfx(job, force)
    elif stage == "timeline":
        from timeline import run_timeline
        run_timeline(job)
    elif stage == "render":
        from render import run_render
        run_render(job, force)
    elif stage == "qa":
        from qa import run_qa
        run_qa(job, force)
    elif stage == "meta":
        from meta import run_meta
        run_meta(job)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="shorts-kit video pipeline")
    ap.add_argument("job_dir")
    ap.add_argument("--check", action="store_true", help="validate only (no TTS, no render)")
    ap.add_argument("--from", dest="frm", choices=STAGES, help="rerun from this stage")
    ap.add_argument("--to", choices=STAGES, help="stop after this stage")
    ap.add_argument("--keep", action="store_true", help="skip cleanup")
    ap.add_argument("--force", action="store_true", help="ignore caches; allow rebuilding a cleaned job")
    ap.add_argument("--voice", metavar="FILE", help="your own narration (wav/mp3) instead of TTS")
    a = ap.parse_args(argv)
    setup_logging()
    try:
        job = Job(a.job_dir)
    except (PipelineError, ValueError) as e:
        log.error(str(e))
        return EXIT_STAGE if a.check else EXIT_JOB
    if a.check:
        return run_check(job)
    from checks import validate_job
    errors, warns = validate_job(job.data, job.lang, job.channel.get("closingLine"))
    for w in warns:
        log.warning(f"job: {w}")
    if errors:
        for e in errors:
            log.error(f"job: {e}")
        return EXIT_JOB
    stages = STAGES[STAGES.index(a.frm) if a.frm else 0:STAGES.index(a.to) + 1 if a.to else len(STAGES)]
    live = [s for s in stages if s in LIVE_STAGES]
    if job.is_cleaned and live:
        if not a.force:
            log.error(f"job was cleaned; rerunning {', '.join(live)} needs --force (keep/ files are restored)")
            return EXIT_STAGE
        from cleanup import restore_keep
        log.info(f"restored from keep/: {', '.join(restore_keep(job)) or '-'}")
    t_all = time.time()
    for stage in stages:
        t0 = time.time()
        try:
            if stage == "cleanup":
                if a.keep or (not job.assets.exists() and not job.build.exists()):
                    log.info("cleanup: skipped")
                    continue
                rp = job.find("qa_report.json")
                rep = load_json(rp) if rp else None
                if not qa_ok(rep):
                    log.warning("cleanup: QA findings unreviewed; check build/qa_sheet.png, then fix and rebuild or "
                                f"run: python pipeline/qa.py {job.dir} --review \"reason\"")
                    return EXIT_QA
                from cleanup import run_cleanup
                run_cleanup(job)
                continue
            run_stage(stage, job, a.force or a.frm is not None, a.voice)
        except PipelineError as e:
            log.error(f"{stage}: {e}")
            return EXIT_STAGE
        except KeyboardInterrupt:
            log.error(f"{stage}: interrupted")
            return 130
        except Exception as e:  # noqa: BLE001
            log.exception(f"{stage}: unexpected error: {e}")
            return EXIT_STAGE
        log.debug(f"{stage}: {time.time() - t0:.1f} s")
    rp = job.find("qa_report.json")
    if "qa" in stages and rp and not qa_ok(load_json(rp)):
        log.warning(f"QA findings (build/qa_report.json); false alarm? python pipeline/qa.py {job.dir} --review \"...\"")
        return EXIT_QA
    log.info(f"done in {time.time() - t_all:.0f} s")
    return EXIT_OK


if __name__ == "__main__":
    sys.exit(main())
