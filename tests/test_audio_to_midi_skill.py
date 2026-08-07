"""Regression tests for the audio-to-midi skill script.

Found 2026-08-07 while checking a (false) drift alarm that named this skill:
the module imported fine, but every actual transcription had been failing.
Three stacked breakages, none of which the readiness probe can see, since it
only checks that `import basic_pitch` succeeds:

  1. `predict_and_save` has required `model_or_model_path` since basic-pitch
     0.3.0. The script never passed it, so every call raised TypeError.
  2. basic-pitch defaults that argument to its TensorFlow SavedModel whenever
     TensorFlow is importable, and TF 2.16 dropped the format it was saved in,
     so the default is unloadable on a current TensorFlow.
  3. basic-pitch calls `scipy.signal.gaussian`, removed in SciPy 1.13.

These run on a bare CI runner: standard library only. basic-pitch, scipy and
onnxruntime are faked, so what is pinned here is the call the script makes and
the file it leaves behind, not the transcription itself.
"""

import enum
import importlib.util
import sys
import tempfile
import types
import unittest
from pathlib import Path

SCRIPT = (Path(__file__).resolve().parent.parent / "skills" / "audio-to-midi"
          / "scripts" / "audio2midi.py")

TF_MODEL = Path("/models/icassp_2022/nmp")
ONNX_MODEL = Path("/models/icassp_2022/nmp.onnx")


class _Suffix(enum.Enum):
    tf = "nmp"
    coreml = "nmp.mlpackage"
    tflite = "nmp.tflite"
    onnx = "nmp.onnx"


def _load_script():
    spec = importlib.util.spec_from_file_location("audio2midi_under_test", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class AudioToMidiTest(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.tmp = Path(self._tmp.name)
        self.audio = self.tmp / "take.wav"
        self.audio.write_bytes(b"RIFF")

        self.calls = []
        self.unloadable = set()
        self._saved = {}
        self._fake_basic_pitch()
        self._fake_scipy()
        self._fake_module("onnxruntime")
        self.script = _load_script()

    def tearDown(self):
        for name, previous in self._saved.items():
            if previous is None:
                sys.modules.pop(name, None)
            else:
                sys.modules[name] = previous
        self._tmp.cleanup()

    def _fake_module(self, name):
        self._saved.setdefault(name, sys.modules.get(name))
        module = types.ModuleType(name)
        sys.modules[name] = module
        return module

    def _fake_basic_pitch(self):
        basic_pitch = self._fake_module("basic_pitch")
        basic_pitch.FilenameSuffix = _Suffix
        basic_pitch.build_icassp_2022_model_path = (
            lambda suffix: Path("/models/icassp_2022") / suffix.value
        )
        # basic-pitch picks the TF SavedModel whenever TensorFlow is importable.
        basic_pitch.ICASSP_2022_MODEL_PATH = TF_MODEL

        inference = self._fake_module("basic_pitch.inference")
        inference.predict_and_save = self._predict_and_save
        basic_pitch.inference = inference

    def _fake_scipy(self):
        scipy = self._fake_module("scipy")
        signal = self._fake_module("scipy.signal")
        windows = self._fake_module("scipy.signal.windows")
        windows.gaussian = lambda n, std=1: [0.0] * n
        signal.windows = windows
        scipy.signal = signal
        # SciPy >= 1.13: no scipy.signal.gaussian at all.
        self.assertFalse(hasattr(signal, "gaussian"))

    def _predict_and_save(self, audio_paths, output_directory, **kwargs):
        self.calls.append((audio_paths, output_directory, kwargs))
        model = kwargs.get("model_or_model_path")
        if model is None:
            raise TypeError(
                "predict_and_save() missing 1 required positional argument: "
                "'model_or_model_path'"
            )
        if str(model) in self.unloadable:
            raise ValueError(f"File {model} cannot be loaded into either "
                             "TensorFlow, CoreML, TFLite or ONNX.")
        stem = Path(audio_paths[0]).stem
        target = Path(output_directory) / f"{stem}_basic_pitch.mid"
        target.write_bytes(b"MThd")

    def test_model_is_passed_and_onnx_build_preferred(self):
        result = self.script.transcribe_audio(str(self.audio), str(self.tmp))

        self.assertNotIn("error", result, result.get("error"))
        self.assertEqual(len(self.calls), 1)
        _, _, kwargs = self.calls[0]
        self.assertEqual(
            kwargs.get("model_or_model_path"), ONNX_MODEL,
            "the TF SavedModel default is unloadable on TF >= 2.16",
        )

    def test_unloadable_model_falls_through_to_the_next_build(self):
        self.unloadable.add(str(ONNX_MODEL))

        result = self.script.transcribe_audio(str(self.audio), str(self.tmp))

        self.assertNotIn("error", result, result.get("error"))
        tried = [str(kw["model_or_model_path"]) for _, _, kw in self.calls]
        self.assertEqual(tried, [str(ONNX_MODEL), str(TF_MODEL)])

    def test_every_model_unloadable_reports_the_real_reason(self):
        self.unloadable.update({str(ONNX_MODEL), str(TF_MODEL)})

        result = self.script.transcribe_audio(str(self.audio), str(self.tmp))

        self.assertIn("cannot be loaded", result.get("error", ""))

    def test_explicit_output_filename_is_honoured(self):
        wanted = self.tmp / "renders" / "take.mid"

        result = self.script.transcribe_audio(str(self.audio), str(wanted))

        self.assertNotIn("error", result, result.get("error"))
        self.assertTrue(wanted.exists(), "the requested filename must be written")
        self.assertEqual(result["output"], str(wanted))
        self.assertFalse(
            (wanted.parent / "take_basic_pitch.mid").exists(),
            "the basic-pitch default name should not be left behind",
        )

    def test_directory_output_keeps_the_basic_pitch_name(self):
        result = self.script.transcribe_audio(str(self.audio), str(self.tmp))

        self.assertEqual(result["output"], str(self.tmp / "take_basic_pitch.mid"))
        self.assertTrue((self.tmp / "take_basic_pitch.mid").exists())

    def test_missing_output_argument_writes_beside_the_input(self):
        result = self.script.transcribe_audio(str(self.audio))

        self.assertEqual(result["output"], str(self.tmp / "take_basic_pitch.mid"))

    def test_scipy_gaussian_alias_is_restored(self):
        self.script.transcribe_audio(str(self.audio), str(self.tmp))

        import scipy.signal
        self.assertIs(scipy.signal.gaussian, scipy.signal.windows.gaussian)

    def test_missing_input_file_is_reported(self):
        result = self.script.transcribe_audio(str(self.tmp / "nope.wav"))

        self.assertIn("File not found", result.get("error", ""))
        self.assertEqual(self.calls, [])


if __name__ == "__main__":
    unittest.main()
