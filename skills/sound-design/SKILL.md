# Sound Design

Programmatic sound synthesis and audio generation.

## Usage

```bash
# Synthesize a kick drum
python skills/sound-design/scripts/synth.py kick -o kick.wav

# Synthesize a snare
python skills/sound-design/scripts/synth.py snare -o snare.wav

# Synthesize a hihat
python skills/sound-design/scripts/synth.py hihat -o hihat.wav

# Ambient pad (specify frequency and duration)
python skills/sound-design/scripts/synth.py pad --freq 220 --duration 4.0 -o pad.wav

# Bass sound
python skills/sound-design/scripts/synth.py bass --freq 55 --duration 1.0 -o bass.wav

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

## Output Formats

- WAV (default, lossless)
- Can be converted to MP3 via ffmpeg afterward
