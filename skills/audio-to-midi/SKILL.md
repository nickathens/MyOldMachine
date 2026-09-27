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
  ONNX, CoreML and TFLite. Nothing is downloaded on first run. The script asks
  for the ONNX build when onnxruntime is installed, because basic-pitch's own
  default is the TensorFlow SavedModel and TensorFlow 2.16 dropped the format
  it was saved in. If neither loads, install onnxruntime: `pip install onnxruntime`
- Transcribing the same file again replaces the earlier MIDI. basic-pitch itself refuses to overwrite its own output, which made every rerun fail until 2026-09-27; it now writes into a scratch folder first. An output name without .mid is taken as a folder
- The result is performance timing, slightly off the grid: the sheet-music skill tidies it before engraving
- Processing runs on CPU (may take 30-60 seconds per minute of audio)
- Output MIDI can be imported into any DAW
- For polyphonic content, some notes may be missed
