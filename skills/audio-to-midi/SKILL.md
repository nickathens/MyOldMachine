# Audio to MIDI

Transcribe audio (vocals, instruments) to MIDI using Spotify's Basic Pitch AI model.

## Usage

```bash
# Transcribe audio file to MIDI
python skills/audio-to-midi/scripts/audio2midi.py input.mp3

# Specify output location
python skills/audio-to-midi/scripts/audio2midi.py input.wav output.mid
```

## Supported Formats

Input: mp3, wav, flac, ogg, m4a, aac
Output: Standard MIDI file (.mid)

## How It Works

Uses Spotify's Basic Pitch neural network to detect:
- Note pitches
- Note timing (onset/offset)
- Note velocity

Works best with:
- Monophonic melodies (single notes)
- Clear recordings without heavy effects
- Vocals, piano, guitar, bass, etc.

## Notes

- The model ships inside the basic-pitch wheel, in four builds: TensorFlow,
  ONNX, CoreML and TFLite. The script prefers the ONNX build when onnxruntime
  is installed, because basic-pitch's own default is the TensorFlow SavedModel
  and TensorFlow 2.16 dropped the format it was saved in. If neither loads,
  install onnxruntime: `pip install onnxruntime`
- Processing runs on CPU (may take 30-60 seconds per minute of audio)
- Output MIDI can be imported into any DAW
- For polyphonic content, some notes may be missed
