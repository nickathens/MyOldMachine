# Agent-to-Agent Peer Channel

A **peer** is another agent (for example, a second bot running on a different
machine) that talks to this bot directly instead of through Telegram. The peer
channel lets that agent send a message into the normal prompt/session/history
pipeline under its own identity, so the bot answers with its full context
(skills, memory, instructions) and remembers the conversation across calls.

This is for dialogue and second opinions between agents. It is deliberately
separate from a one-shot compute handoff: a peer holds a standing conversation,
with its own person-model and its own history, the same way a human Telegram
user does.

## What a peer is, and how it is fenced

A peer is stored in `data/users.json` exactly like a normal user, which is why
the standard pipeline applies to it unchanged. The one hard rule is that a
peer's ID **must be non-numeric** (letters, digits, hyphen, underscore, but not
a value that `int()` accepts).

That single rule is the entire fencing model:

- The Telegram allow-list (`config.get_allowed_users`) and the admin list
  (`users.get_admin_telegram_ids`) both `int()`-parse the keys of
  `users.json` and skip anything that does not parse. A non-numeric peer ID can
  never land in either list.
- The peer ID also becomes a directory name under `data/users/<id>/`, so it is
  validated as a safe slug (no path separators, no traversal).

The result: a peer can hold a conversation, but it can never be mistaken for a
Telegram user, never gains access through the allow-list, and never appears as
an admin.

## Registering a peer

Run once per peer. Re-running is an idempotent update: it refreshes the name,
role, and bio in place and keeps the peer's conversation history.

```bash
python utils/peer_channel.py register \
    --peer alice \
    --name "Alice" \
    --about-file /path/to/alice_persona.txt
```

- `--peer` is the non-numeric ID (becomes the `users.json` key and data dir).
- `--name` is the display name.
- `--about-file` points at a text file holding the peer's person-model: a
  description of who this agent is, which is injected into the system prompt as
  "About the person you are talking to". It is read from a file rather than the
  command line so long bios are not exposed in process listings or shell
  history.
- `--role` is `user` (default) or `admin`. See the security note below.

## Sending a message as a peer

The message is read from **STDIN**, never from argv, so it is injection-safe and
not exposed in process listings:

```bash
echo "What do you think of this plan?" | \
    python utils/peer_channel.py send --peer alice
```

The reply is printed to STDOUT. The exchange is persisted to the peer's history,
so the bot remembers the conversation the next time the peer sends a message.

`send` refuses to run under an identity that is not a registered peer. If the
ID belongs to a real (numeric) Telegram user, or is unknown, it exits without
sending.

### Exit codes

| Code | Meaning |
| ---- | ------- |
| 0 | Success |
| 1 | Bad usage, empty message, or registration failure |
| 2 | Unknown peer, or the identity is a real user rather than a peer |

## Transport

The CLI is local: it reads a message on STDIN and prints the reply on STDOUT. A
remote peer reaches it over whatever shell transport you already trust, with SSH
as the intended path (the remote agent SSHes in and pipes its message to
`peer_channel.py send`). The channel adds no auth of its own, so the ability to
run the CLI on this host is the trust boundary. Grant that access the same way
you would grant a shell account.

## Security posture

- **Role defaults to `user`.** A `user` peer has `can_install` and
  `can_restart` set to false, so it carries no privileged-action capability.
  Only register a peer as `admin` if you intend it to have the same elevated
  capabilities as a human admin.
- **No Telegram exposure.** The non-numeric ID keeps the peer out of the
  allow-list and admin list permanently (see the fencing model above).
- **No data on the wire it does not need.** The bio is supplied via a file and
  the message via STDIN, so neither is visible in process listings.
- **The peer profile and its bio are local data.** They live in
  `data/users.json`, which is not committed, so a peer's person-model stays on
  the machine that registered it.
