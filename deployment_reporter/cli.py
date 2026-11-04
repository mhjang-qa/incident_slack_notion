import argparse
import json
import logging
from pathlib import Path

from .config import load_settings
from .service import Reporter


def main():
    parser = argparse.ArgumentParser(description="Slack deployment reporter (Preview by default)")
    sub = parser.add_subparsers(dest="command", required=True)
    batch = sub.add_parser("batch")
    batch.add_argument("--days", type=int, choices=range(1, 91), metavar="1..90")
    batch.add_argument("--start-date")
    batch.add_argument("--end-date")
    batch.add_argument("--sync", action="store_true", help="Write Summary + Raw Data to Notion")
    batch.add_argument("--search-only", action="store_true")
    for name in ("preview", "reanalyze", "retry", "sync"):
        p = sub.add_parser(name)
        p.add_argument("event_id")
    test = sub.add_parser("test")
    test.add_argument("provider", choices=["slack", "notion", "gemini"])
    sub.add_parser("schedule")
    sub.add_parser("ui")
    sub.add_parser("demo")
    history = sub.add_parser("backfill")
    history.add_argument("--start-date", required=True)
    history.add_argument("--end-date", required=True)
    history.add_argument("--sync", action="store_true")
    sub.add_parser("backfills")
    sub.add_parser("resume-backfills")
    args = parser.parse_args()
    Path("logs").mkdir(exist_ok=True)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s", handlers=[logging.StreamHandler(), logging.FileHandler("logs/deployment_reporter.log")])
    # HTTP libraries may include request paths; never enable their request logging.
    for name in ("httpx", "httpcore"):
        logging.getLogger(name).setLevel(logging.WARNING)
    if args.command == "demo":
        from .demo import run_demo
        print(json.dumps(run_demo(), ensure_ascii=False, indent=2))
        return
    if args.command == "ui":
        import uvicorn
        from .ui.server import create_app
        uvicorn.run(create_app(), host="127.0.0.1", port=8000)
        return
    reporter = Reporter(load_settings())
    if args.command == "batch":
        if args.search_only and args.sync:
            parser.error("--search-only cannot be combined with --sync")
        if bool(args.start_date) != bool(args.end_date) or (args.days is not None and args.start_date):
            parser.error("날짜는 시작일/종료일을 함께 지정하고 --days와 동시에 사용하지 마세요")
        output = reporter.batch(days=args.days, start_date=args.start_date, end_date=args.end_date, analyze=not args.search_only, sync=args.sync)
    elif args.command == "backfill":
        from .backfill import Backfills
        manager = Backfills(reporter)
        job = manager.create(args.start_date, args.end_date, sync=args.sync)
        output = manager.run(job["id"])
    elif args.command == "backfills":
        output = reporter.repo.backfills()
    elif args.command == "resume-backfills":
        from .backfill import Backfills
        output = Backfills(reporter).run_pending()
    elif args.command == "preview":
        output = reporter.preview(args.event_id)
    elif args.command in {"reanalyze", "retry"}:
        output = reporter.analyze(args.event_id, recollect=args.command == "retry")
    elif args.command == "sync":
        output = reporter.sync(args.event_id)
    elif args.command == "test":
        output = getattr(reporter, args.provider).test()
    else:
        from .scheduler.jobs import Scheduler
        try:
            Scheduler(reporter).run()
        except KeyboardInterrupt:
            pass
        return
    print(json.dumps(output, ensure_ascii=False, indent=2, default=str))


if __name__ == "__main__":
    main()
