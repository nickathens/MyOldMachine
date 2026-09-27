# Sound Design

Programmatic sound synthesis: mono, 44.1 kHz, peak normalised to -3 dBFS.

## Usage

```bash
S=skills/sound-design/scripts/synth.py

python $S kick  --freq 150 -o kick.wav            # pitch drops from 150+40 Hz to 40 Hz
python $S snare --duration 0.3 -o snare.wav
python $S hihat --duration 0.1 -o hat.wav         # --open for an open hat
python $S bass  --freq 55 --duration 1 -o bass.wav
python $S pad   --freq 220 --duration 6 --reverb 2.5 -o pad.wav
python $S drone --freq 110 --duration 20 -o drone.flac
python $S sweep --start-freq 100 --freq 4000 --duration 3 -o riser.wav
python $S sweep --start-freq 100 --freq 4000 --direction down -o fall.wav
python $S noise --noise-type pink --duration 2 -o pink.wav   # white, pink, brown
python $S tone  --freq 440 --waveform saw --duration 1 -o tone.mp3
```

Effects, applied to any sound in this order: `--lowpass HZ`, `--highpass HZ`,
`--distortion 0..1` (soft clip), `--bitcrush BITS`, `--reverb SECONDS` (a
synthetic room decaying 60 dB over that time; the file grows by the tail) with
`--wet 0..1` (default 0.3). `--seed N` makes the noise based sounds repeatable.

Output format follows the extension: `.wav` (16-bit, default), `.mp3`,
`.flac`, `.ogg`, `.m4a`, `.opus` (encoded with ffmpeg). Before 2026-09-27
every extension got WAV bytes, so `out.mp3` was a WAV file with the wrong name.

## How the sounds are made

- kick: sine with an exponential pitch drop, plus a noise click
- snare: 200 Hz sine body plus band passed noise (1 to 8 kHz)
- hihat: high passed noise (6 kHz), short or open decay
- bass: sine plus a square an octave up, low passed at 800 Hz
- pad: four detuned saws, low passed at 2 kHz, slow envelope
- drone: detuned sines with a slow LFO, low passed at 500 Hz
- sweep: linear frequency sweep with an envelope
- noise: white, pink (-3 dB per octave filter) or brown (leaky integrator,
  no DC drift), with 5 ms fades so it does not click

# Frequency sweep
python skills/sound-design/scripts/synth.py sweep --freq 2000 --duration 2.0 -o sweep.wav

# Ambient drone
python skills/sound-design/scripts/synth.py drone --freq 110 --duration 10.0 -o drone.wav

# White noise
python skills/sound-design/scripts/synth.py noise --duration 1.0 -o noise.wav

# Basic tone with waveform selection
python skills/sound-design/scripts/synth.py tone --freq 440 --waveform saw --duration 2.0 -o tone.wav
```

## Waveforms

sine, square, saw, triangle

## Sound Presets

kick, snare, hihat, pad, bass, sweep, drone, noise, tone

Libraries: numpy and scipy (synthesis, filters, convolution); ffmpeg for the
compressed formats.
