"""Command-line interface for running, diagnosing, and recovering the bridge."""

from __future__ import annotations

import argparse
import json

from . import __version__
from .config import Settings
from .errors import BridgeError
from .journal import print_pending
from .muse import MuseAdapter
from .worker import BridgeWorker


def _print_json(payload: dict) -> None:
    print(json.dumps(payload, indent=2, ensure_ascii=False))


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="muse-airi-bridge",
        description="Local OpenAI-compatible bridge from AIRI to your existing Muse main chat.",
    )
    parser.add_argument("--version", action="version", version=__version__)
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("serve", help="Start the local OpenAI-compatible HTTP server")
    commands.add_parser(
        "doctor",
        help="Verify Muse connectivity by reading history (never posts or modifies config)",
    )
    commands.add_parser(
        "pending",
        help="List pending or uncertain jobs without displaying conversation text",
    )
    reconcile = commands.add_parser(
        "reconcile",
        help="Match a pending job against recent Muse history (stop the server first)",
    )
    reconcile.add_argument("job_id", help="ID of the uncertain job to reconcile")
    clear = commands.add_parser(
        "clear-pending",
        help="Abandon an uncertain job so new turns can proceed (stop the server first)",
    )
    clear.add_argument("job_id", help="ID of the uncertain job to clear")
    clear.add_argument(
        "--acknowledge-muse-may-still-be-working",
        action="store_true",
        required=True,
        help="Confirm that clearing local state does not cancel an in-flight Muse reply",
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
            _print_json(
                {
                    "status": "ready",
                    "muse": "connected",
                    "muse_cli": "0.3.10",
                    "message_sent": False,
                    "config_modified": False,
                }
            )
        elif args.command == "pending":
            print_pending(settings.state_dir)
        else:
            worker = BridgeWorker(settings)
            try:
                if args.command == "reconcile":
                    job = worker.reconcile(args.job_id)
                    _print_json({"job_id": job.job_id, "state": job.state, "message_sent": False})
                else:
                    worker.clear_pending(args.job_id)
                    _print_json({"job_id": args.job_id, "state": "cleared", "message_sent": False})
            finally:
                worker.close()
    except BridgeError as error:
        _print_json(error.payload())
        return 1
    except (ValueError, OSError):
        _print_json(
            {
                "error": {
                    "code": "bridge_configuration_error",
                    "message": "Check bridge environment variables, directory paths, and port.",
                }
            }
        )
        return 1
    return 0
