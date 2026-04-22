#!/usr/bin/env python3
"""PlayStealth CLI - Weltbeste CLI für Hacker (Anti-Detection Automation)."""

import argparse
import asyncio
import json
import os
import sys

# Pre-Flight Check vor allem anderen
from playstealth_actions.config_validator import run_full_validation


def preflight_check():
    """Blockiert CLI-Start bei kritischen Fehlern."""
    validation = run_full_validation(
        plugin_modules=[
            "playstealth_actions.plugins.hey_piggy",
            "playstealth_actions.plugins.qualtrics",
        ]
    )

    if not validation["valid"]:
        print("🚨 PlayStealth Pre-Flight Check FAILED:")
        for comp, msg in validation["errors"].items():
            print(f"   ❌ {comp.upper()}: {msg}")
        print("\n💡 Behebe die Fehler und starte erneut.")
        sys.exit(1)

    if validation["warnings"]:
        print("⚠️  PlayStealth Warnings:")
        for w in validation["warnings"]:
            print(f"   ⚠️  {w}")


def create_parser():
    parser = argparse.ArgumentParser(
        prog="playstealth",
        description="🔐 PlayStealth CLI - Weltbeste CLI für Hacker (Anti-Detection Automation)",
    )

    # Globale Resilience-Flags
    parser.add_argument(
        "--auto-report",
        action="store_true",
        default=None,
        help="Enable auto GitHub issues on failure",
    )
    parser.add_argument(
        "--no-auto-report",
        action="store_false",
        dest="auto_report",
        help="Disable auto GitHub issues",
    )
    parser.add_argument(
        "--fail-fast",
        action="store_true",
        default=None,
        help="Stop flow immediately on any module failure",
    )
    parser.add_argument(
        "--no-issue-dedup", action="store_true", help="Disable 24h deduplication for auto-issues"
    )

    subparsers = parser.add_subparsers(dest="command", help="Verfügbare Befehle")

    # run-survey
    run_parser = subparsers.add_parser("run-survey", help="Survey ausführen")
    run_parser.add_argument("--index", type=int, default=0, help="Survey-Index")
    run_parser.add_argument("--max-steps", type=int, default=10, help="Maximale Schritte")
    run_parser.add_argument(
        "--url", type=str, default=None, help="Survey URL (optional, sonst Plugin-default)"
    )
    run_parser.add_argument(
        "--strategy",
        choices=["random", "consistent", "persona"],
        default="persona",
        help="Antwort-Strategie",
    )
    run_parser.add_argument(
        "--persona",
        choices=["optimistic", "critical", "neutral"],
        default="neutral",
        help="Persona-Profil",
    )
    run_parser.add_argument(
        "--dashboard-url",
        default=os.getenv(
            "PLAYSTEALTH_DASHBOARD_URL", "https://www.heypiggy.com/login?page=dashboard"
        ),
        help="Dashboard URL",
    )
    # resume-survey
    resume_parser = subparsers.add_parser("resume-survey", help="Survey fortsetzen")
    resume_parser.add_argument("--session-id", required=True, help="Session-ID")
    resume_parser.add_argument("--max-steps", type=int, default=10)

    # diagnose
    diag_parser = subparsers.add_parser("diagnose", help="Diagnose-Tools")
    diag_sub = diag_parser.add_subparsers(dest="subcommand")
    diag_sub.add_parser("benchmark", help="Stealth-Benchmark durchführen")
    diag_sub.add_parser("check-webgl", help="WebGL-Leaks prüfen")
    diag_sub.add_parser("check-headless", help="Headless-Indikatoren prüfen")
    diag_sub.add_parser("inspect-page", help="Aktuelle Seite inspizieren")
    diag_sub.add_parser("detect-traps", help="Honeypots und Attention Checks erkennen")
    diag_sub.add_parser("telemetry-summary", help="Telemetrie-Zusammenfassung anzeigen")
    diag_sub.add_parser("ban-risk", help="Ban-Risk aus Telemetrie berechnen")

    # manifest
    manifest_parser = subparsers.add_parser("manifest", help="Manifest generieren/anzeigen")
    manifest_parser.add_argument(
        "--benchmark", action="store_true", help="Stealth-Benchmark vorher durchführen"
    )

    # profile
    profile_parser = subparsers.add_parser(
        "profile", help="Survey URL analysieren & Plugin-Stub generieren"
    )
    profile_parser.add_argument("url", help="Target Survey URL")
    profile_parser.add_argument(
        "--output", default=None, help="Plugin Name (default: abgeleitet von Domain)"
    )
    profile_parser.add_argument("--json", action="store_true", help="Nur rohes JSON ausgeben")

    # metrics
    metrics_parser = subparsers.add_parser("metrics", help="Telemetrie-Zusammenfassung anzeigen")
    metrics_parser.add_argument(
        "--export", action="store_true", help="Rohdaten (JSONL) exportieren"
    )

    # tui
    tui_parser = subparsers.add_parser("tui", help="Live-Dashboard für Telemetrie & Fortschritt")
    tui_parser.add_argument("--session", default="live", help="Session-ID zum Beobachten")
    tui_parser.add_argument("--max-steps", type=int, default=10, help="Maximale Schritte")

    # create-plugin
    plugin_parser = subparsers.add_parser(
        "create-plugin", help="Neues Survey-Plugin-Gerüst erstellen"
    )
    plugin_parser.add_argument("name", help="Plugin-Name (lowercase, z.B. survey_monkey)")

    # demo
    demo_parser = subparsers.add_parser("demo", help="Vollständige End-to-End-Demo ausführen")
    demo_parser.add_argument("--url", default="https://example.com", help="Test-URL")
    demo_parser.add_argument("--max-steps", type=int, default=3, help="Maximale Schritte")

    return parser


async def run_command(args):
    """Führt den gewählten Befehl aus."""

    if args.command == "run-survey":
        from playstealth_actions.simple_survey_runner import execute_survey_flow
        from playstealth_actions.dashboard_flow import run_dashboard_flow
        from playstealth_actions.telemetry import generate_session_id
        from playwright.async_api import async_playwright
        from playstealth_actions.stealth_enhancer import inject_advanced_stealth

        session_id = generate_session_id()
        if args.url:
            survey_url = args.url
            async with async_playwright() as p:
                browser = await p.chromium.launch(headless=True)
                ctx = await browser.new_context(
                    user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
                    locale="de-DE",
                    timezone_id="Europe/Berlin",
                    viewport={"width": 1920, "height": 1080},
                )

                page = await ctx.new_page()
                await inject_advanced_stealth(page)

                print(f"🚀 Starte Survey (Session: {session_id}, URL: {survey_url})")

                result = await execute_survey_flow(
                    page=page,
                    context=ctx,
                    start_url=survey_url,
                    max_steps=args.max_steps,
                    session_id=session_id,
                )

                await browser.close()

            if result["success"]:
                print(f"✅ Survey abgeschlossen: {result['steps_completed']} Schritte")
                print(f"💾 Session-ID: {result['session_id']}")
            else:
                print(f"❌ Survey fehlgeschlagen: {result.get('error', 'Unbekannter Fehler')}")
        else:
            email = os.getenv("HEYPIGGY_EMAIL", "")
            password = os.getenv("HEYPIGGY_PASSWORD", "")
            login_selectors = None
            if email and password:
                login_selectors = {
                    "email": os.getenv("PLAYSTEALTH_LOGIN_EMAIL_TARGET", "email"),
                    "password": os.getenv("PLAYSTEALTH_LOGIN_PASSWORD_TARGET", "password"),
                    "submit": os.getenv("PLAYSTEALTH_LOGIN_SUBMIT_TARGET", "Anmelden"),
                    "email_val": email,
                    "password_val": password,
                }

            print(f"🚀 Starte Survey Flow (Strategy: {args.strategy}, Persona: {args.persona})")
            await run_dashboard_flow(
                dashboard_url=args.dashboard_url,
                login_selectors=login_selectors,
                max_surveys=1,
                max_steps_per_survey=args.max_steps,
                strategy_name=args.strategy,
                strategy_persona=args.persona,
            )
            print("✅ Survey Flow beendet")

    elif args.command == "resume-survey":
        from playstealth_actions.simple_survey_runner import resume_survey_flow
        from playstealth_actions.state_store import load_browser_context, list_sessions
        from playwright.async_api import async_playwright

        print(f"🔄 Resume Survey für Session: {args.session_id}")

        available_sessions = list_sessions()
        if args.session_id not in available_sessions:
            print(f"❌ Session '{args.session_id}' nicht gefunden")
            print(
                f"   Verfügbare Sessions: {', '.join(available_sessions) if available_sessions else 'Keine'}"
            )
            return

        async with async_playwright() as p:
            browser = await p.chromium.launch(headless=True)

            try:
                ctx = await load_browser_context(browser, args.session_id)
                page = ctx.pages[0] if ctx.pages else await ctx.new_page()

                result = await resume_survey_flow(
                    page=page,
                    context=ctx,
                    session_id=args.session_id,
                    max_steps=args.max_steps,
                )

                if result["success"]:
                    print(
                        f"✅ Resume erfolgreich: {result.get('steps_completed', 0)} weitere Schritte"
                    )
                    print(f"💾 Session-ID: {result['session_id']}")
                else:
                    print(f"❌ Resume fehlgeschlagen: {result.get('error', 'Unbekannter Fehler')}")

            except FileNotFoundError as e:
                print(f"❌ State nicht gefunden: {e}")
            except Exception as e:
                print(f"❌ Fehler beim Resume: {e}")
            finally:
                await browser.close()
    elif args.command == "profile":
        from playstealth_actions.survey_profiler import profile_survey

        print(f"🔍 Profiliere Survey: {args.url}")
        report = await profile_survey(args.url, args.output)

        if args.json:
            print(json.dumps(report, indent=2, ensure_ascii=False))
        else:
            print("\n🔍 Survey Profiler Report")
            print(f"   URL           : {report.get('url')}")
            print(f"   Plugin        : {report.get('generated_plugin')}")
            print(f"   Questions     : {len(report.get('dom_structure', {}).get('questions', []))}")
            print(f"   Options       : {len(report.get('dom_structure', {}).get('options', []))}")
            print(f"   Types         : {report.get('question_types')}")
            print(f"   Navigation    : {report.get('navigation_buttons')} buttons found")
            print(f"   Consent       : {report.get('consent_buttons')} banners found")
            print(f"   Honeypots     : {report.get('honeypots_detected')}")

            if report.get("plugin_path"):
                print(f"\n✅ Plugin generated: {report['plugin_path']}")
                print(f"🧪 Test generated : {report['test_path']}")
                print("👉 Review selectors & run: pytest tests/ -v")
            if report.get("warning"):
                print(f"\n⚠️  {report['warning']}")
            if report.get("error"):
                print(f"\n❌ {report['error']}")

    elif args.command == "metrics":
        from playstealth_actions.telemetry import get_summary, telemetry_file
        from playstealth_actions.ban_risk_monitor import calculate_ban_risk

        if args.export:
            file_path = telemetry_file()
            if file_path.exists():
                print(file_path.read_text())
            else:
                print("Keine Telemetrie-Daten vorhanden.")
        else:
            summary = get_summary()
            payload = {"summary": summary, "ban_risk": calculate_ban_risk()}
            print(json.dumps(payload, indent=2))

    elif args.command == "tui":
        from playstealth_actions.tui_dashboard import TUIDashboard

        dash = TUIDashboard(session_id=args.session, max_steps=args.max_steps)

        async def run():
            await asyncio.gather(dash.tail_telemetry(), asyncio.to_thread(dash.run_live))

        try:
            await run()
        except KeyboardInterrupt:
            print("\n👋 TUI beendet")

    elif args.command == "create-plugin":
        from playstealth_actions.plugin_scaffolder import create_plugin

        try:
            res = create_plugin(args.name)
            print(f"✅ Plugin '{res['class_name']}' erfolgreich erstellt!")
            print(f"📄 Plugin: {res['plugin_path']}")
            print(f"🧪 Tests:  {res['test_path']}")
            print("\n👉 Nächste Schritte:")
            for step in res["next_steps"]:
                print(f"   • {step}")
        except Exception as e:
            print(f"❌ Fehler: {e}")
            sys.exit(1)

    elif args.command == "manifest":
        from playstealth_actions.manifest_generator import save_manifest, print_manifest_cli

        stealth_data = None
        if args.benchmark:
            from playstealth_actions.diagnose_benchmark import diagnose_benchmark
            from playwright.async_api import async_playwright

            async with async_playwright() as p:
                browser = await p.chromium.launch(headless=True)
                ctx = await browser.new_context()
                page = await ctx.new_page()
                await page.goto("about:blank")
                stealth_data = await diagnose_benchmark(page)
                await browser.close()

        data = await save_manifest(stealth_data)
        print_manifest_cli(data)
        print(f"💾 Gespeichert unter: {data['config']['manifest_path']}")

    elif args.command == "diagnose":
        from playstealth_actions.diagnostic_common import (
            ban_risk_summary,
            detect_traps,
            inspect_page,
            telemetry_summary,
        )

        if args.subcommand == "benchmark":
            from playstealth_actions.diagnose_benchmark import diagnose_benchmark
            from playwright.async_api import async_playwright

            async with async_playwright() as p:
                browser = await p.chromium.launch(headless=True)
                ctx = await browser.new_context()
                page = await ctx.new_page()
                await page.goto("about:blank")
                report = await diagnose_benchmark(page)
                await browser.close()

            print(json.dumps(report, indent=2))
        elif args.subcommand == "inspect-page":
            from playwright.async_api import async_playwright

            async with async_playwright() as p:
                browser = await p.chromium.launch(headless=True)
                ctx = await browser.new_context()
                page = await ctx.new_page()
                await page.goto("about:blank")
                print(json.dumps(await inspect_page(page), indent=2, ensure_ascii=False))
                await browser.close()
        elif args.subcommand == "detect-traps":
            from playwright.async_api import async_playwright

            async with async_playwright() as p:
                browser = await p.chromium.launch(headless=True)
                ctx = await browser.new_context()
                page = await ctx.new_page()
                await page.goto("about:blank")
                print(json.dumps(await detect_traps(page), indent=2, ensure_ascii=False))
                await browser.close()
        elif args.subcommand == "telemetry-summary":
            print(json.dumps(telemetry_summary(), indent=2, ensure_ascii=False))
        elif args.subcommand == "ban-risk":
            print(json.dumps(ban_risk_summary(), indent=2, ensure_ascii=False))
        else:
            print(f"Diagnose-Subcommand '{args.subcommand}' noch nicht implementiert")

    elif args.command == "demo":
        from demo_flow import run_demo

        await run_demo(survey_url=args.url, max_steps=args.max_steps)

    else:
        print("❌ Unbekannter Befehl. Nutze 'playstealth --help' für verfügbare Commands.")


def main():
    preflight_check()
    parser = create_parser()
    args = parser.parse_args()

    # Resilience-Konfiguration global setzen
    from playstealth_actions.resilience_config import ResilienceConfig, set_global_config

    cfg = ResilienceConfig.from_env_or_args(args)
    set_global_config(cfg)

    if not args.command:
        parser.print_help()
        sys.exit(0)

    asyncio.run(run_command(args))


if __name__ == "__main__":
    main()
