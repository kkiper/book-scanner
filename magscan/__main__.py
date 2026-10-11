"""Command line entry point.

    python -m magscan serve                 # web UI (what the container runs)
    python -m magscan run                   # scan + read covers, leave for review
    python -m magscan run --apply           # ...and apply everything it read
    python -m magscan preview FILE.pdf      # read one cover and print the plan, change nothing
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import sys
from pathlib import Path


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(prog="magscan", description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest="command", required=True)
    serve = sub.add_parser("serve", help="run the web UI")
    serve.add_argument("--host", default=os.environ.get("HOST", "0.0.0.0"))
    serve.add_argument("--port", type=int, default=int(os.environ.get("PORT", "8484")))
    run = sub.add_parser("run", help="scan the library and read new covers")
    run.add_argument("--apply", action="store_true", help="also rename and write metadata for everything read")
    prev = sub.add_parser("preview", help="dry run for one file")
    prev.add_argument("file")
    args = parser.parse_args(argv)

    logging.basicConfig(level=os.environ.get("LOG_LEVEL", "INFO"), format="%(asctime)s %(levelname)s %(name)s: %(message)s")

    from .config import Settings
    settings = Settings.load()

    if args.command == "serve":
        import uvicorn
        from .web import create_app
        uvicorn.run(create_app(settings), host=args.host, port=args.port)
        return 0

    from .processor import Processor
    from .store import Store

    if args.command == "preview":
        from .cover import read_cover
        from .extract import get_extractor
        path = Path(args.file)
        info = get_extractor(settings).extract(read_cover(str(path)), path.name)
        item = {"path": str(path.resolve()), "original_path": str(path.resolve()), "info": info.model_dump(), "edits": {}}
        plan = Processor(settings, store=None).plan(item)
        print(json.dumps(info.model_dump(), indent=2, ensure_ascii=False))
        print(f"\nwould rename to: {plan.target}")
        print(f"title: {plan.metadata.title}")
        return 0

    processor = Processor(settings, Store(settings.db_file))
    print(f"found {processor.discover()} new file(s)")
    for item in processor.store.items(status="new", limit=1_000_000):
        print(f"reading {item['path']}")
        processor.analyze(item["id"])
    if args.apply:
        for item in processor.store.items(status="review", limit=1_000_000):
            processor.apply(item["id"])
    counts = processor.store.counts()
    print(", ".join(f"{k}: {v}" for k, v in sorted(counts.items())))
    return 1 if counts.get("error") else 0


if __name__ == "__main__":
    sys.exit(main())
