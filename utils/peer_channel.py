#!/usr/bin/env python3
"""
peer_channel.py — agent-to-agent side channel for MyOldMachine.

A "peer" is another agent (for example, a bot running on a different machine)
that talks to this bot directly instead of through Telegram. Messages flow
through the normal prompt/session/history pipeline under the peer's own
identity, so the bot answers with its full context (skills, memory,
instructions) and remembers the conversation across calls. The peer is fenced
off from the Telegram allow-list and admin list by its non-numeric ID — see
core.users.register_peer for the details of that fencing.

Usage:
    # Register or update a peer identity (run once per peer, or again to
    # refresh the `about` text — re-registering is idempotent and keeps
    # the peer's conversation history):
    python utils/peer_channel.py register --peer alice --name "Alice" \
        --about-file /path/to/alice_persona.txt

    # Send a message to this bot AS the peer and print the reply. The message
    # is read from STDIN, never the argv, so it is injection-safe:
    echo "What do you think of this plan?" | \
        python utils/peer_channel.py send --peer alice

Exit codes:
    0  success
    1  bad usage, empty message, or registration failure
    2  unknown peer, or the identity is a real user rather than a peer
"""

import argparse
import asyncio
import sys
from pathlib import Path

from dotenv import load_dotenv

BOT_DIR = Path(__file__).parent.parent
sys.path.insert(0, str(BOT_DIR))
load_dotenv(BOT_DIR / ".env")


def cmd_register(args) -> int:
    from core.users import register_peer

    about = ""
    if args.about_file:
        about_path = Path(args.about_file).expanduser()
        if not about_path.is_file():
            print(f"Error: --about-file not found: {about_path}", file=sys.stderr)
            return 1
        try:
            about = about_path.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError) as exc:
            print(f"Error: cannot read --about-file: {exc}", file=sys.stderr)
            return 1

    ok, message = register_peer(args.peer, args.name, about, role=args.role)
    if not ok:
        print(f"Error: {message}", file=sys.stderr)
        return 1
    print(message)
    return 0


def _read_stdin_message() -> str:
    """Read the peer's message from STDIN. Empty when attached to a TTY."""
    if sys.stdin.isatty():
        return ""
    return sys.stdin.read()


async def _run_send(peer_id: str, message: str) -> str:
    """Bootstrap the provider like bot.main() and route one message through the
    normal pipeline under the peer's identity, persisting the exchange."""
    from core.config import (
        get_llm_api_key,
        get_llm_model,
        get_llm_provider,
        get_ollama_base_url,
    )
    from core.llm import create_provider
    import bot

    provider_name = get_llm_provider()
    kwargs = {}
    if provider_name == "ollama":
        kwargs["base_url"] = get_ollama_base_url()
    bot._llm_provider = create_provider(
        provider_name, get_llm_model(), get_llm_api_key(), **kwargs
    )
    # The startup health-check is intentionally skipped: it spawns an extra CLI
    # process and adds latency. call_llm() only consults last_health when it is
    # set, so leaving it None lets a misconfigured provider surface its real
    # error inside the reply instead of as a separate pre-flight failure.

    reply = await bot.call_llm(peer_id, message, chat=None)
    # Persist the turn so the bot remembers talking to this peer on later calls.
    bot._save_and_send(peer_id, message, reply)
    return reply


def cmd_send(args) -> int:
    from core.users import get_user

    profile = get_user(args.peer)
    if profile is None:
        print(
            f"Error: unknown peer {args.peer!r}. Register it first with "
            f"`peer_channel.py register --peer {args.peer} --name ...`.",
            file=sys.stderr,
        )
        return 2
    if not profile.get("is_peer"):
        print(
            f"Error: {args.peer!r} is a registered user, not a peer. Refusing "
            f"to send under a non-peer identity.",
            file=sys.stderr,
        )
        return 2

    message = _read_stdin_message()
    if not message.strip():
        print(
            "Error: empty message. Provide the message on STDIN.",
            file=sys.stderr,
        )
        return 1

    reply = asyncio.run(_run_send(args.peer, message))
    print(reply)
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Agent-to-agent side channel for MyOldMachine."
    )
    sub = parser.add_subparsers(dest="command", required=True)

    p_reg = sub.add_parser("register", help="Register or update a peer identity.")
    p_reg.add_argument(
        "--peer", required=True, help="Non-numeric peer ID, e.g. 'alice'."
    )
    p_reg.add_argument("--name", required=True, help="Display name for the peer.")
    p_reg.add_argument(
        "--about-file",
        help="Path to a text file describing the peer (its person-model).",
    )
    p_reg.add_argument(
        "--role",
        choices=("user", "admin"),
        default="user",
        help="Capability role. Default 'user' (no sudo exposure).",
    )

    p_send = sub.add_parser(
        "send", help="Send a STDIN message to the bot as a peer and print the reply."
    )
    p_send.add_argument("--peer", required=True, help="Peer ID to send as.")

    return parser


def main(argv=None) -> int:
    args = build_parser().parse_args(argv)
    if args.command == "register":
        return cmd_register(args)
    if args.command == "send":
        return cmd_send(args)
    return 1


if __name__ == "__main__":
    sys.exit(main())
