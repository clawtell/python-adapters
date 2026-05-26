"""Daemon helpers that are unit-testable without an event loop:
boot-config log line + deployment-shape advisory.

The subcommand flows (check / discover-chat / send-test) hit network
endpoints, so they're verified manually + via the published-CLI smoke
test, not here."""

from __future__ import annotations

import argparse
import logging

from clawtell_core import daemon


def _ns(**kwargs) -> argparse.Namespace:
    """Minimal Namespace mirroring what argparse would build for the
    default (forwarder) command path."""
    base = dict(
        adapter=None,
        agent_factory=None,
        graph_factory=None,
        forward_only=False,
        default_chat=None,
        factory_timeout=30.0,
        heartbeat_file=None,
        allow_collocated_agent=False,
        telegram_token_env="TG_BOT_TOKEN",
        api_key=None,
        name=None,
        log_level="INFO",
    )
    base.update(kwargs)
    return argparse.Namespace(**base)


def test_boot_log_line_emitted_with_all_fields(caplog):
    caplog.set_level(logging.INFO, logger="clawtell.forwarder")
    daemon._log_boot_config(
        _ns(adapter="clawtell_hermes:HermesAdapter", default_chat="123"),
        tg_token=True,
    )
    line = next(
        r.message for r in caplog.records if r.message.startswith("boot config:")
    )
    assert "mode=full" in line
    assert "adapter=clawtell_hermes:HermesAdapter" in line
    assert "default_chat=123" in line
    assert "factory_timeout=30s" in line
    assert "telegram_token=set" in line


def test_boot_log_marks_inferred_forward_only(caplog):
    """When neither --adapter nor --forward-only is set we infer
    forward-only — the boot line must show that the mode was inferred so
    users notice if they meant to pass --adapter."""
    caplog.set_level(logging.INFO, logger="clawtell.forwarder")
    daemon._log_boot_config(_ns(), tg_token=True)
    line = next(
        r.message for r in caplog.records if r.message.startswith("boot config:")
    )
    assert "inferred" in line


def test_deployment_advisory_warns_when_adapter_set_without_optin(caplog):
    caplog.set_level(logging.WARNING, logger="clawtell.forwarder")
    daemon._deployment_shape_advisory(
        _ns(adapter="clawtell_hermes:HermesAdapter")
    )
    warnings = [r for r in caplog.records if r.levelno == logging.WARNING]
    assert warnings, "expected a WARNING about collocated-agent anti-pattern"
    assert "deployment-shape" in warnings[0].message
    assert "--allow-collocated-agent" in warnings[0].message


def test_deployment_advisory_silent_when_optin(caplog):
    caplog.set_level(logging.WARNING, logger="clawtell.forwarder")
    daemon._deployment_shape_advisory(
        _ns(
            adapter="clawtell_hermes:HermesAdapter",
            allow_collocated_agent=True,
        )
    )
    assert [r for r in caplog.records if r.levelno == logging.WARNING] == []


def test_deployment_advisory_silent_in_forward_only(caplog):
    """Forward-only never spins a second AIAgent so the advisory is
    irrelevant; do not spam the log."""
    caplog.set_level(logging.WARNING, logger="clawtell.forwarder")
    daemon._deployment_shape_advisory(_ns(forward_only=True))
    assert [r for r in caplog.records if r.levelno == logging.WARNING] == []


def _build_parser():
    """Mirror the parser tree that ``main()`` builds — used by the
    subparser-flag tests below without invoking the daemon."""
    p = argparse.ArgumentParser(prog="clawtell-forwarder")
    p.add_argument("--adapter")
    p.add_argument("--agent-factory")
    p.add_argument("--graph-factory")
    p.add_argument("--forward-only", action="store_true")
    p.add_argument("--default-chat")
    p.add_argument("--factory-timeout", type=float, default=30.0)
    p.add_argument("--heartbeat-file")
    p.add_argument("--allow-collocated-agent", action="store_true")
    daemon._add_common_flags(p)
    sub = p.add_subparsers(dest="subcommand", required=False)
    cp = sub.add_parser("check")
    daemon._add_common_flags(cp, on_subparser=True)
    cp.add_argument(
        "--default-chat", default=argparse.SUPPRESS, help="x"
    )
    return p


def test_subparser_does_not_clobber_main_flag_placed_before_subcmd():
    """Regression: ``--api-key X check`` used to silently drop --api-key
    because the subparser's namespace (with default None) overwrote the
    main parser's value. SUPPRESS on subparser-side duplicates fixes it."""
    p = _build_parser()
    ns = p.parse_args(["--api-key", "claw_xxx", "check"])
    assert ns.api_key == "claw_xxx"
    assert ns.subcommand == "check"


def test_subparser_accepts_flag_after_subcmd_too():
    """The fix must NOT break the working order — passing the flag after
    the subcommand keeps working."""
    p = _build_parser()
    ns = p.parse_args(["check", "--api-key", "claw_xxx"])
    assert ns.api_key == "claw_xxx"


def test_subparser_default_chat_before_subcmd_not_clobbered():
    """Same bug for --default-chat which is also on both main and check."""
    p = _build_parser()
    ns = p.parse_args(["--default-chat", "999", "check"])
    assert ns.default_chat == "999"


def test_subparser_omitting_flag_preserves_main_default():
    """When the user passes no flag at all, the main parser's default
    (None / TG_BOT_TOKEN / INFO) must be the final value — subparser
    SUPPRESS means the attribute is NOT written by the subparser."""
    p = _build_parser()
    ns = p.parse_args(["check"])
    assert ns.api_key is None
    assert ns.telegram_token_env == "TG_BOT_TOKEN"
    assert ns.log_level == "INFO"
