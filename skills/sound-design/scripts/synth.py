#!/usr/bin/env python3
"""
Sound synthesis engine for programmatic sound design
"""
import numpy as np
from scipy import signal
from scipy.io import wavfile
import argparse
import os
import uuid

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

def save_wav(audio, path, sample_rate=SAMPLE_RATE):
    """Save audio as WAV file"""
    wavfile.write(path, sample_rate, to_int16(audio))
    print(f"Saved: {path}")

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
        white = np.random.uniform(-1, 1, samples)
        return np.cumsum(white) / 100
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
    t = np.linspace(0, duration, int(duration * SAMPLE_RATE), endpoint=False)

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

if __name__ == '__main__':
    parser = argparse.ArgumentParser(description='Sound synthesis')
    parser.add_argument('sound', choices=['kick', 'snare', 'hihat', 'pad', 'bass',
                                          'sweep', 'drone', 'noise', 'tone'])
    parser.add_argument('--freq', type=float, default=220, help='Frequency in Hz')
    parser.add_argument('--duration', type=float, default=1.0, help='Duration in seconds')
    parser.add_argument('--waveform', default='sine', choices=['sine', 'square', 'saw', 'triangle'])
    parser.add_argument('--output', '-o', default=f'/tmp/synth_output_{uuid.uuid4().hex[:8]}.wav')

    args = parser.parse_args()

    if args.sound == 'kick':
        audio = synth_kick(args.freq, args.duration)
    elif args.sound == 'snare':
        audio = synth_snare(args.duration)
    elif args.sound == 'hihat':
        audio = synth_hihat(args.duration)
    elif args.sound == 'pad':
        audio = synth_pad(args.freq, args.duration)
    elif args.sound == 'bass':
        audio = synth_bass(args.freq, args.duration)
    elif args.sound == 'sweep':
        audio = synth_sweep(100, args.freq, args.duration)
    elif args.sound == 'drone':
        audio = synth_drone(args.freq, args.duration)
    elif args.sound == 'noise':
        audio = noise(args.duration, 'white')
        audio = normalize(audio)
    elif args.sound == 'tone':
        audio = oscillator(args.freq, args.duration, args.waveform)
        env = envelope_adsr(args.duration)
        audio = normalize(audio * env)

    save_wav(audio, args.output)
