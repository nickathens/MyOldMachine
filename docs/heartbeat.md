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

- Schedule it through the bot's own scheduler and the pings stop the instant the
  bot process stops (frozen, crashed, or the machine is down), because the
  scheduler stops with it. That is the dead-man's-switch.
- Or schedule it with a system timer (systemd or cron) so pings survive a bot
  crash and stop only when the machine or network is down.

The script uses only the standard library, sends no data beyond the ping, and
never raises. With `HEARTBEAT_URL` unset it is a no-op, so it is safe to leave
disabled.

## Enable it

1. Create a check on any uptime monitor that works by receiving pings (for
   example a free healthchecks.io check) and copy its ping URL. Set its period
   and grace to match your interval; a 2 minute ping with an 8 minute grace
   catches a real outage without firing on one slow ping.
2. Add the URL to your `.env`:
   ```
   HEARTBEAT_URL=https://hc-ping.com/your-check-uuid
   HEARTBEAT_INTERVAL_MIN=2
   ```
3. Schedule the script. Pick one of the two variants below.

### Variant A: through the bot scheduler (simplest, cross-platform)

Ask the bot to run it on an interval:

```
/schedule every 2 minutes | /path/to/venv/bin/python /path/to/MyOldMachine/utils/heartbeat.py
```

Pings stop when the bot stops, so this detects a frozen or dead bot and a dead
machine. It cannot ping while the bot is down, which is the point.

### Variant B: a system timer (survives a bot crash)

Use this when you want to be alerted even if only the bot process dies while the
machine stays up. On a systemd host, install a service plus timer (adjust the
paths and the User):

```ini
# /etc/systemd/system/mom-heartbeat.service
[Unit]
Description=MyOldMachine heartbeat ping
# Only ping while the bot service is running, so a dead bot stops the pings:
Requisite=mom.service
After=mom.service

[Service]
Type=oneshot
User=youruser
EnvironmentFile=/path/to/MyOldMachine/.env
ExecStart=/path/to/venv/bin/python /path/to/MyOldMachine/utils/heartbeat.py
```

```ini
# /etc/systemd/system/mom-heartbeat.timer
[Unit]
Description=Run the MyOldMachine heartbeat every 2 minutes

[Timer]
OnBootSec=2min
OnUnitActiveSec=2min

[Install]
WantedBy=timers.target
```

```bash
sudo systemctl daemon-reload
sudo systemctl enable --now mom-heartbeat.timer
```

The `Requisite=mom.service` line gates the ping on the bot being up, so this
timer alerts you when the bot dies even though the machine is still running. Drop
that line if you only want to detect the machine or network going down.

On a non-systemd host use cron and gate on the bot yourself, or use Variant A.

## Test it

```bash
python utils/heartbeat.py                     # uses HEARTBEAT_URL from .env
python utils/heartbeat.py --url https://hc-ping.com/your-check-uuid
```

The first run flips your monitor to "up". Stop the schedule and the monitor
should alert after its grace period. That end-to-end test is the only proof the
alert path actually works, so do it once when you set this up.
