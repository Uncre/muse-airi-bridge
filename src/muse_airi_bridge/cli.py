from __future__ import annotations

import argparse
import json

from . import __version__
from .config import Settings
from .errors import BridgeError
from .journal import print_pending
from .muse import MuseAdapter
from .worker import BridgeWorker


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="muse-airi-bridge")
    parser.add_argument("--version", action="version", version=__version__)
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("serve", help="Serve the local OpenAI-compatible API")
    commands.add_parser(
        "doctor", help="Check Muse connection without posting or changing CLI config"
    )
    commands.add_parser(
        "pending", help="List pending job IDs without printing conversation content"
    )
    reconcile = commands.add_parser(
        "reconcile", help="Recover a pending response; stop the server first"
    )
    reconcile.add_argument("job_id")
    clear = commands.add_parser(
        "clear-pending", help="Abandon an uncertain job; stop the server first"
    )
    clear.add_argument("job_id")
    clear.add_argument(
        "--acknowledge-muse-may-still-be-working", action="store_true", required=True
    )
    args = parser.parse_args(argv)
    try:
        settings = Settings.from_env()
        if args.command == "serve":
            import uvicorn

            from .api import create_app

            uvicorn.run(
                create_app(settings),
                host=settings.host,
                port=settings.port,
                workers=1,
                log_level="info",
            )
        elif args.command == "doctor":
            with MuseAdapter(settings, "airi-readonly-doctor").open() as session:
                session.history()
            print(
                json.dumps(
                    {
                        "status": "ready",
                        "muse": "connected",
                        "muse_cli": "0.3.10",
                        "message_sent": False,
                        "config_modified": False,
                    }
                )
            )
        elif args.command == "pending":
            print_pending(settings.state_dir)
        else:
            worker = BridgeWorker(settings)
            try:
                if args.command == "reconcile":
                    job = worker.reconcile(args.job_id)
                    print(
                        json.dumps(
                            {"job_id": job.job_id, "state": job.state, "message_sent": False}
                        )
                    )
                else:
                    worker.clear_pending(args.job_id)
                    print(
                        json.dumps(
                            {"job_id": args.job_id, "state": "cleared", "message_sent": False}
                        )
                    )
            finally:
                worker.close()
    except BridgeError as error:
        print(json.dumps(error.payload(), ensure_ascii=False))
        return 1
    except (ValueError, OSError):
        print(
            json.dumps(
                {
                    "error": {
                        "code": "bridge_configuration_error",
                        "message": "Check bridge environment, paths and port settings.",
                    }
                }
            )
        )
        return 1
    return 0
