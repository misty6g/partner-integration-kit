"""Onboarding CLI for Partner Integration Kit."""

from __future__ import annotations

import argparse
import json
import sys

import httpx

from pik_api import __version__
from pik_cli.checks import run_checks


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="pik", description="Partner Integration Kit onboarding tools.")
    sub = parser.add_subparsers(dest="command", required=True)

    check = sub.add_parser("check", help="Check API key, webhook reachability, signatures, and TLS.")
    check.add_argument("--base-url", default="http://127.0.0.1:8400", help="Partner API base URL.")
    check.add_argument("--api-key", required=True, help="Partner API key.")
    check.add_argument("--webhook-url", help="Partner webhook URL to probe and sign.")
    check.add_argument("--insecure", action="store_true", help="Do not verify TLS when probing the webhook URL.")
    check.add_argument("--timeout", type=float, default=5.0)
    check.add_argument("--json", action="store_true", dest="as_json", help="Print the report as JSON.")

    help_cmd = sub.add_parser("help", help="Suggest a fix for an integration error.")
    help_cmd.add_argument("query", help="Error message, status, or payload.")
    help_cmd.add_argument("--json", action="store_true", dest="as_json")

    sub.add_parser("version", help="Print the kit version.")

    args = parser.parse_args(argv)
    if args.command == "version":
        print(__version__)
        return 0
    if args.command == "help":
        return _help(args.query, as_json=args.as_json)
    return _check(args)


def _check(args: argparse.Namespace) -> int:
    try:
        with httpx.Client(base_url=args.base_url.rstrip("/"), timeout=args.timeout, headers={"X-API-Key": args.api_key}) as api:
            report = run_checks(
                api,
                api_key=args.api_key,
                webhook_url=args.webhook_url,
                insecure=args.insecure,
                timeout=args.timeout,
            )
    except httpx.HTTPError as exc:
        print(f"Partner Integration Kit — setup check\n\n  API unreachable  FAIL  {exc.__class__.__name__}: {exc}", file=sys.stderr)
        return 1
    if args.as_json:
        print(json.dumps(report.as_dict(), indent=2))
    else:
        print(report.render())
    return 1 if report.failed else 0


def _help(query: str, *, as_json: bool) -> int:
    from pik_bot.bot import answer

    result = answer(query)
    if as_json:
        print(json.dumps(result.as_dict(), indent=2))
        return 0
    print(f"Mode: {result.mode}")
    if result.sections:
        top = result.sections[0]
        print(f"Section: {top.title} ({top.id})")
    print()
    print(result.suggestion)
    if len(result.sections) > 1:
        print("\nAlso see:")
        for section in result.sections[1:]:
            print(f"  - {section.title} [{section.id}]")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
