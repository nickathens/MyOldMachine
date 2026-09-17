# Voice engines: when the models load

Two heavy models sit behind voice mode on macOS:

| engine | model | resident | warm-up |
|---|---|---|---|
| listening (speech to text) | Whisper large-v3-turbo, MLX | ~2.5 GB | ~2 s |
| speaking (text to speech) | Chatterbox | ~5 GB | ~15 s |

Warm-up figures are from this machine's own daemon logs, not estimates: the
listening engine reports `WARM and ready ... in 1.8s` / `2.0s`, and the
speaking engine `model loaded in 10.1s` followed by a warm-up pass, ready about
15 s after launch.

## The two modes

**On demand (default).** No LaunchAgent. `data/stt/hear.py` and
`data/chatterbox/say.py` start their daemon the first time they are called and
wait for it to warm; the daemon exits by itself after `*_DAEMON_IDLE` seconds
(15 minutes by default) with no requests. Cost: the warm-up above, once, on the
first voice message after a quiet spell. Everything inside the idle window runs
at full speed.

**Always warm.** Two LaunchAgents with `RunAtLoad` + `KeepAlive`, and the idle
timeout overridden to 100 years. Both models load at login and never leave.
Cost: ~7.5 GB held whether or not anyone speaks all day.

The two settings in that second mode are a pair, and this is the trap worth
knowing about: `KeepAlive` on its own would defeat the idle timeout anyway,
because launchd restarts a daemon within seconds of it exiting, so the model
would reload for nobody. That is why the idle override is there, and why
`plist_starts_at_boot()` reads either flag as "loads at boot".

## Switching

```bash
python3 install/voice_agents.py status
python3 install/voice_agents.py on-demand      # remove the agents, free the RAM
python3 install/voice_agents.py always-warm    # put them back
```

`on-demand` unloads and removes both agents, keeping a copy of each under
`data/voice_agents_removed/`, then stops whatever they left resident. A daemon
that is mid-request is left alone and allowed to idle out on its own: its CPU
is sampled rather than trusted, because neither daemon reports whether it is
busy, and a transcription cut off halfway is a lost voice message.

`always-warm` renders the agents from `install/templates/` and loads them.

## Why the default changed

On a 24 GB machine, the two models are about a third of memory, held all day
for a feature used in bursts. That plus an editing application left open is
most of the reason macOS reports memory pressure on an otherwise idle box. The
on-demand path was already built -- both clients started their daemon and both
daemons self-exited -- and the LaunchAgents were the only thing overriding it.
