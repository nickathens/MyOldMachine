#!/usr/bin/env python3
"""
Sound synthesis engine for programmatic sound design
"""
import argparse
import os
import subprocess
import sys
import tempfile
import uuid
from pathlib import Path

import numpy as np
from scipy import signal
from scipy.io import wavfile

SAMPLE_RATE = 44100

def normalize(audio, target_db=-3):
    """Normalize audio to target dB level"""
    if np.max(np.abs(audio)) == 0:
        return audio
    current_max = np.max(np.abs(audio))
    target_linear = 10 ** (target_db / 20)
    return audio * (target_linear / current_max)

def to_int16(audio):
    """Convert float audio to int16"""
    audio = np.clip(audio, -1, 1)
    return (audio * 32767).astype(np.int16)

ENCODERS = {
    '.mp3': ['-c:a', 'libmp3lame', '-q:a', '2'],
    '.flac': ['-c:a', 'flac'],
    '.ogg': ['-c:a', 'libvorbis', '-q:a', '6'],
    '.m4a': ['-c:a', 'aac', '-b:a', '256k'],
    '.opus': ['-c:a', 'libopus', '-b:a', '160k'],
}


def save_wav(audio, path, sample_rate=SAMPLE_RATE):
    """Save audio: WAV directly, other formats encoded from it with ffmpeg.

    Every extension used to get WAV bytes, so out.mp3 was a WAV file with the
    wrong name (Linux bot review 2026-09-27).
    """
    path = Path(path)
    ext = path.suffix.lower()
    path.parent.mkdir(parents=True, exist_ok=True)
    if ext in ('', '.wav'):
        wavfile.write(path, sample_rate, to_int16(audio))
    elif ext in ENCODERS:
        with tempfile.NamedTemporaryFile(suffix='.wav', delete=False) as tmp:
            wav = tmp.name
        try:
            wavfile.write(wav, sample_rate, to_int16(audio))
            r = subprocess.run(['ffmpeg', '-v', 'error', '-y', '-i', wav, *ENCODERS[ext], str(path)],
                               capture_output=True, text=True)
            if r.returncode != 0:
                sys.exit(f"Error: ffmpeg could not write {path}: {r.stderr.strip()[-400:]}")
        finally:
            os.unlink(wav)
    else:
        sys.exit(f"Error: unsupported format {ext}: use .wav, {', '.join(ENCODERS)}")
    print(f"Saved: {path}")


def fade_edges(audio, seconds=0.005):
    """Short fades so a hard start or stop does not click."""
    n = min(int(seconds * SAMPLE_RATE), len(audio) // 2)
    if n:
        ramp = np.linspace(0, 1, n)
        audio = audio.copy()
        audio[:n] *= ramp
        audio[-n:] *= ramp[::-1]
    return audio


def reverb(audio, seconds=1.5, wet=0.3):
    """Convolution with a synthetic room: noise decaying 60 dB over `seconds`.

    The output grows by the tail, so the reverb is not cut off.
    """
    n = int(seconds * SAMPLE_RATE)
    t = np.arange(n) / SAMPLE_RATE
    impulse = np.random.uniform(-1, 1, n) * np.exp(-6.9078 * t / seconds)
    impulse = lowpass_filter(impulse, 8000)
    impulse /= np.sqrt(np.sum(impulse ** 2))
    tail = signal.fftconvolve(audio, impulse)
    dry = np.pad(audio, (0, len(tail) - len(audio)))
    return (1 - wet) * dry + wet * tail * (np.max(np.abs(audio)) / max(np.max(np.abs(tail)), 1e-12))

def envelope_adsr(length, attack=0.01, decay=0.1, sustain=0.7, release=0.2):
    """Generate ADSR envelope"""
    samples = int(length * SAMPLE_RATE)
    attack_samples = int(attack * SAMPLE_RATE)
    decay_samples = int(decay * SAMPLE_RATE)
    release_samples = int(release * SAMPLE_RATE)
    sustain_samples = samples - attack_samples - decay_samples - release_samples

    if sustain_samples < 0:
        sustain_samples = 0

    attack_env = np.linspace(0, 1, attack_samples)
    decay_env = np.linspace(1, sustain, decay_samples)
    sustain_env = np.ones(sustain_samples) * sustain
    release_env = np.linspace(sustain, 0, release_samples)

    envelope = np.concatenate([attack_env, decay_env, sustain_env, release_env])

    # Pad or trim to exact length
    if len(envelope) < samples:
        envelope = np.pad(envelope, (0, samples - len(envelope)))
    else:
        envelope = envelope[:samples]

    return envelope

def oscillator(freq, duration, waveform='sine', sample_rate=SAMPLE_RATE):
    """Generate basic waveform"""
    t = np.linspace(0, duration, int(duration * sample_rate), endpoint=False)

    if waveform == 'sine':
        return np.sin(2 * np.pi * freq * t)
    elif waveform == 'square':
        return signal.square(2 * np.pi * freq * t)
    elif waveform == 'saw':
        return signal.sawtooth(2 * np.pi * freq * t)
    elif waveform == 'triangle':
        return signal.sawtooth(2 * np.pi * freq * t, width=0.5)
    else:
        return np.sin(2 * np.pi * freq * t)

def noise(duration, noise_type='white'):
    """Generate noise"""
    samples = int(duration * SAMPLE_RATE)

    if noise_type == 'white':
        return np.random.uniform(-1, 1, samples)
    elif noise_type == 'pink':
        # Simple pink noise approximation
        white = np.random.uniform(-1, 1, samples)
        b = [0.049922035, -0.095993537, 0.050612699, -0.004408786]
        a = [1, -2.494956002, 2.017265875, -0.522189400]
        return signal.lfilter(b, a, white)
    elif noise_type == 'brown':
        # A leaky integrator plus a 20 Hz high pass: a plain cumulative sum
        # wanders off as a random walk and turns into DC
        white = np.random.uniform(-1, 1, samples)
        return highpass_filter(signal.lfilter([1.0], [1.0, -0.997], white), 20)
    else:
        return np.random.uniform(-1, 1, samples)

def lowpass_filter(audio, cutoff, order=4):
    """Apply lowpass filter"""
    nyquist = SAMPLE_RATE / 2
    normalized_cutoff = min(cutoff / nyquist, 0.99)
    b, a = signal.butter(order, normalized_cutoff, btype='low')
    return signal.filtfilt(b, a, audio)

def highpass_filter(audio, cutoff, order=4):
    """Apply highpass filter"""
    nyquist = SAMPLE_RATE / 2
    normalized_cutoff = min(cutoff / nyquist, 0.99)
    b, a = signal.butter(order, normalized_cutoff, btype='high')
    return signal.filtfilt(b, a, audio)

def bandpass_filter(audio, low_cutoff, high_cutoff, order=4):
    """Apply bandpass filter"""
    nyquist = SAMPLE_RATE / 2
    low = min(low_cutoff / nyquist, 0.99)
    high = min(high_cutoff / nyquist, 0.99)
    b, a = signal.butter(order, [low, high], btype='band')
    return signal.filtfilt(b, a, audio)

def distortion(audio, amount=0.5):
    """Apply soft clipping distortion"""
    return np.tanh(audio * (1 + amount * 10))

def bitcrush(audio, bits=8):
    """Reduce bit depth"""
    levels = 2 ** bits
    return np.round(audio * levels) / levels

def pitch_sweep(start_freq, end_freq, duration, waveform='sine'):
    """Generate frequency sweep"""
    t = np.linspace(0, duration, int(duration * SAMPLE_RATE), endpoint=False)
    freq_curve = np.linspace(start_freq, end_freq, len(t))
    phase = np.cumsum(2 * np.pi * freq_curve / SAMPLE_RATE)
    return np.sin(phase)

# Sound presets

def synth_kick(pitch=60, duration=0.5):
    """Synthesize a kick drum"""
    # Pitch envelope: starts high, drops quickly
    t = np.linspace(0, duration, int(duration * SAMPLE_RATE), endpoint=False)
    pitch_env = pitch * np.exp(-t * 30) + 40  # Drop from pitch to ~40Hz

    phase = np.cumsum(2 * np.pi * pitch_env / SAMPLE_RATE)
    osc = np.sin(phase)

    # Amplitude envelope
    amp_env = np.exp(-t * 8)

    # Add click transient
    click = noise(0.01, 'white') * np.exp(-np.linspace(0, 1, int(0.01 * SAMPLE_RATE)) * 50)
    click = np.pad(click, (0, len(osc) - len(click)))

    kick = osc * amp_env + click * 0.3
    return normalize(kick)

def synth_snare(duration=0.3):
    """Synthesize a snare drum"""
    t = np.linspace(0, duration, int(duration * SAMPLE_RATE), endpoint=False)

    # Tone component
    tone_env = np.exp(-t * 20)
    tone = oscillator(200, duration, 'sine') * tone_env

    # Noise component
    noise_env = np.exp(-t * 15)
    noise_sound = noise(duration, 'white')
    noise_sound = bandpass_filter(noise_sound, 1000, 8000)
    noise_sound = noise_sound * noise_env

    snare = tone * 0.5 + noise_sound * 0.8
    return normalize(snare)

def synth_hihat(duration=0.1, open_hat=False):
    """Synthesize a hihat"""
    if open_hat:
        duration = 0.3

    t = np.linspace(0, duration, int(duration * SAMPLE_RATE), endpoint=False)

    # Filtered noise
    noise_sound = noise(duration, 'white')
    noise_sound = highpass_filter(noise_sound, 6000)

    # Envelope
    decay = 10 if not open_hat else 3
    env = np.exp(-t * decay)

    hihat = noise_sound * env
    return normalize(hihat)

def synth_pad(freq=220, duration=4.0, voices=4, detune=0.02):
    """Synthesize an ambient pad"""
    pad = np.zeros(int(duration * SAMPLE_RATE))

    for i in range(voices):
        detune_factor = 1 + (i - voices/2) * detune
        voice = oscillator(freq * detune_factor, duration, 'saw')
        pad += voice / voices

    # Lowpass filter for warmth
    pad = lowpass_filter(pad, 2000)

    # Long envelope
    env = envelope_adsr(duration, attack=0.5, decay=0.3, sustain=0.7, release=1.0)
    pad = pad * env

    return normalize(pad)

def synth_bass(freq=55, duration=1.0):
    """Synthesize a bass sound"""
    # Sub + harmonics
    sub = oscillator(freq, duration, 'sine')
    harm = oscillator(freq * 2, duration, 'square') * 0.3

    bass = sub + harm
    bass = lowpass_filter(bass, 800)

    env = envelope_adsr(duration, attack=0.01, decay=0.2, sustain=0.6, release=0.2)
    bass = bass * env

    return normalize(bass)

def synth_sweep(start_freq=100, end_freq=2000, duration=2.0, direction='up'):
    """Synthesize a frequency sweep"""
    if direction == 'down':
        start_freq, end_freq = end_freq, start_freq

    sweep = pitch_sweep(start_freq, end_freq, duration)
    env = envelope_adsr(duration, attack=0.1, decay=0.1, sustain=0.8, release=0.5)

    return normalize(sweep * env)

def synth_drone(freq=110, duration=10.0):
    """Synthesize an ambient drone"""
    t = np.linspace(0, duration, int(duration * SAMPLE_RATE), endpoint=False)

    # Multiple detuned oscillators
    drone = np.zeros_like(t)
    for mult in [1.0, 1.002, 0.998, 2.0, 1.5]:
        drone += oscillator(freq * mult, duration, 'sine') * (1 / mult)

    # Slow LFO modulation
    lfo = 1 + 0.1 * np.sin(2 * np.pi * 0.1 * t)
    drone = drone * lfo

    drone = lowpass_filter(drone, 500)
    env = envelope_adsr(duration, attack=2.0, decay=1.0, sustain=0.8, release=2.0)

    return normalize(drone * env)

def main():
    parser = argparse.ArgumentParser(description='Sound synthesis (mono, 44.1 kHz)')
    parser.add_argument('sound', choices=['kick', 'snare', 'hihat', 'pad', 'bass',
                                          'sweep', 'drone', 'noise', 'tone'])
    parser.add_argument('--freq', type=float, default=220,
                        help='Frequency in Hz (kick: start pitch above 40 Hz; sweep: the end)')
    parser.add_argument('--start-freq', type=float, default=100, help='Sweep start in Hz')
    parser.add_argument('--direction', choices=['up', 'down'], default='up', help='Sweep direction')
    parser.add_argument('--duration', type=float, default=1.0, help='Duration in seconds')
    parser.add_argument('--waveform', default='sine', choices=['sine', 'square', 'saw', 'triangle'])
    parser.add_argument('--noise-type', default='white', choices=['white', 'pink', 'brown'])
    parser.add_argument('--open', action='store_true', help='Open hi-hat (longer decay)')
    parser.add_argument('--lowpass', type=float, help='Low pass cutoff in Hz')
    parser.add_argument('--highpass', type=float, help='High pass cutoff in Hz')
    parser.add_argument('--distortion', type=float, help='Soft clip drive, 0 to 1')
    parser.add_argument('--bitcrush', type=int, help='Bit depth to crush to (e.g. 8)')
    parser.add_argument('--reverb', type=float, help='Reverb decay in seconds (adds that much tail)')
    parser.add_argument('--wet', type=float, default=0.3, help='Reverb mix, 0 to 1')
    parser.add_argument('--seed', type=int, help='Random seed for the noise based sounds')
    parser.add_argument('--output', '-o', default=f'/tmp/synth_output_{uuid.uuid4().hex[:8]}.wav',
                        help='Output file: .wav, .mp3, .flac, .ogg, .m4a or .opus')

    args = parser.parse_args()
    if args.duration <= 0 or args.freq <= 0 or args.start_freq <= 0:
        parser.error('--duration, --freq and --start-freq must be above 0')
    if not 0 <= args.wet <= 1:
        parser.error('--wet must be between 0 and 1')
    if args.seed is not None:
        np.random.seed(args.seed)

    if args.sound == 'kick':
        audio = synth_kick(args.freq, max(args.duration, 0.02))
    elif args.sound == 'snare':
        audio = synth_snare(args.duration)
    elif args.sound == 'hihat':
        audio = synth_hihat(args.duration, open_hat=args.open)
    elif args.sound == 'pad':
        audio = synth_pad(args.freq, args.duration)
    elif args.sound == 'bass':
        audio = synth_bass(args.freq, args.duration)
    elif args.sound == 'sweep':
        audio = synth_sweep(args.start_freq, args.freq, args.duration, args.direction)
    elif args.sound == 'drone':
        audio = synth_drone(args.freq, args.duration)
    elif args.sound == 'noise':
        audio = normalize(fade_edges(noise(args.duration, args.noise_type)))
    else:
        audio = oscillator(args.freq, args.duration, args.waveform)
        env = envelope_adsr(args.duration)
        audio = normalize(audio * env)

    if args.lowpass:
        audio = lowpass_filter(audio, args.lowpass)
    if args.highpass:
        audio = highpass_filter(audio, args.highpass)
    if args.distortion:
        audio = distortion(audio, args.distortion)
    if args.bitcrush:
        audio = bitcrush(audio, args.bitcrush)
    if args.reverb:
        audio = reverb(audio, args.reverb, args.wet)
    save_wav(normalize(audio), args.output)


if __name__ == '__main__':
    main()
