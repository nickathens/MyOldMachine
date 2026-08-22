# External heartbeat (opt-in)

An always-on assistant that goes dark should not do so silently. The heartbeat is
a small opt-in ping that lets an outside monitor tell you within minutes when the
machine, the bot, or its network has stopped, instead of you discovering it hours
or days later.

It is the reporting half of resilience. A hardware watchdog can reboot a frozen
box but tells you nothing; the heartbeat tells you but fixes nothing. Run both.

## How it works

`utils/heartbeat.py` sends a short GET to a URL you configure (`HEARTBEAT_URL`).
You schedule it to run on an interval. As long as the pings keep arriving, your
monitor stays quiet. The moment they stop, the monitor alerts you.

The trick is that "stops pinging" is exactly the failure you want to catch:

- A system timer (systemd or launchd) runs the script independently of the bot,
  so a bot pinned under load never delays a ping. On its own such a timer keeps
  pinging while the bot is dead, which would only ever catch "machine or network
  down", so the installer gates it with `--require-service`: the script checks
  the bot first and skips the ping when the bot is not running. Pings stopping
  is then the alert, and it covers a dead bot as well as a dead machine.
- Or schedule it through the bot's own scheduler, where no gate is needed
  because the scheduler stops with the bot.

The script sends no data beyond the ping and never raises. With `HEARTBEAT_URL`
unset it is a no-op, so it is safe to leave disabled.

## Enable it

Re-run the installer and answer yes to **Down alert (external heartbeat)**:

```bash
python install/wizard.py --repo-dir /path/to/MyOldMachine
```

It asks for the ping URL and an interval, installs the schedule for your
platform, writes `HEARTBEAT_URL` to `.env`, and sends one real ping so you can
see the check flip to "up" before you walk away.

Get the URL first: create a check on any dead-man's-switch monitor (a free
healthchecks.io check works), set its period to your interval and its grace to
a few times that, and copy the ping URL. A 2 minute ping with an 8 minute grace
catches a real outage without firing on one slow ping.

What gets installed:

| Platform | Installed | Gated by |
|---|---|---|
| Linux | `/etc/systemd/system/myoldmachine-heartbeat.{service,timer}` | `--require-service myoldmachine.service` |
| macOS | `~/Library/LaunchAgents/com.myoldmachine.heartbeat.plist` | `--require-service com.myoldmachine.bot` |

Only the timer is enabled on Linux. The `.service` is a oneshot and stays
unenabled so it fires from the timer and nowhere else.

### The gate, and why it is not in the unit file

`--require-service NAME` makes the script check the bot before pinging, and
exit 0 without pinging when the bot is down. That is what turns a
machine-is-alive monitor into a bot-is-alive monitor.

The obvious alternative, `Requisite=myoldmachine.service` in the unit, is
wrong: `Requisite=` makes the ping unit **fail** rather than skip, so every
interval during an outage adds an entry to `systemctl --failed` and a red line
in the journal, during exactly the incident you want a clean signal from.
launchd has no equivalent of `Requisite=` at all, so a gate in the unit file
could never have covered both platforms. A skip that exits 0 is silent on both.

When the service manager cannot answer at all (no systemd, no launchd, an
unreadable launchctl), the script pings anyway and says so on stderr. That
degrades to machine-and-network monitoring, which is what an ungated schedule
gives you. The other choice, staying silent, would page you every interval on a
host we simply cannot read, and an alert that cries wolf gets muted.

### Doing it by hand

If you would rather not re-run the installer, the rendered templates are in
`install/templates/`: `myoldmachine-heartbeat.service`,
`myoldmachine-heartbeat.timer`, and `com.myoldmachine.heartbeat.plist`.
Substitute the `{{...}}` placeholders, drop them in the paths above, and:

```bash
sudo systemctl daemon-reload
sudo systemctl enable --now myoldmachine-heartbeat.timer
```

### Variant: through the bot scheduler

No timer at all, and no gate needed, because the bot's own scheduler stops
when the bot stops:

```
/schedule every 2 minutes | /path/to/venv/bin/python /path/to/MyOldMachine/utils/heartbeat.py
```

Simpler, and cross-platform on hosts with neither systemd nor launchd. The
tradeoff is that the ping now depends on the bot's event loop staying
responsive, so a long blocking turn can delay a ping and page you for an
outage that is not happening.

## Test it

```bash
python utils/heartbeat.py                     # uses HEARTBEAT_URL from .env
python utils/heartbeat.py --url https://hc-ping.com/your-check-uuid
```

The first run flips your monitor to "up". Stop the schedule and the monitor
should alert after its grace period. That end-to-end test is the only proof the
alert path actually works, so do it once when you set this up.
