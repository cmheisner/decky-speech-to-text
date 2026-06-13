# Smoke tests for the Speech-to-Text plugin backend.
# Run: python -m pytest tests/ -v
# Skip network tests: python -m pytest tests/ -v -m "not network"
import sys
import os
import io
import socket
import subprocess
import asyncio
import wave
import tempfile
from unittest.mock import MagicMock

import pytest

PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
LIB_PATH = os.path.join(PROJECT_ROOT, "lib")


class TestLibraryImports:
    """Layer 1: Verify the bundled speech_recognition library loads correctly."""

    def test_lib_directory_exists(self):
        assert os.path.isdir(LIB_PATH), (
            f"lib/ directory not found at {LIB_PATH}. "
            "Run: pip install --target=lib --no-compile SpeechRecognition audioop-lts"
        )

    def test_speech_recognition_importable(self):
        import speech_recognition as sr
        assert sr is not None

    def test_recognizer_class_present(self):
        import speech_recognition as sr
        assert hasattr(sr, "Recognizer"), "sr.Recognizer missing"

    def test_audio_data_class_present(self):
        import speech_recognition as sr
        assert hasattr(sr, "AudioData"), "sr.AudioData missing"

    def test_audio_file_class_present(self):
        import speech_recognition as sr
        assert hasattr(sr, "AudioFile"), "sr.AudioFile missing"

    def test_exception_classes_present(self):
        import speech_recognition as sr
        assert hasattr(sr, "UnknownValueError"), "sr.UnknownValueError missing"
        assert hasattr(sr, "RequestError"), "sr.RequestError missing"

    def test_recognizer_instantiation(self):
        import speech_recognition as sr
        r = sr.Recognizer()
        assert r is not None
        assert hasattr(r, "recognize_google")

    def test_speech_recognition_version_readable(self):
        import speech_recognition as sr
        version = getattr(sr, "__version__", None)
        assert version is not None, "Could not read SpeechRecognition __version__"
        print(f"\n  SpeechRecognition version: {version}")


class TestAudioPipeline:
    """Layer 2: Verify audio data creation and format conversion work end-to-end."""

    def test_flac_binary_exists(self):
        # The bundled FLAC binary is required for audio conversion before sending to Google.
        import speech_recognition as sr
        sr_dir = os.path.dirname(sr.__file__)
        bundled = os.path.join(sr_dir, "flac-linux-x86_64")
        system_flac_ok = False
        try:
            r = subprocess.run(["flac", "--version"], capture_output=True, timeout=5)
            system_flac_ok = r.returncode == 0
        except FileNotFoundError:
            pass
        assert os.path.isfile(bundled) or system_flac_ok, (
            f"No FLAC binary found at {bundled} and 'flac' not on PATH. "
            "FLAC conversion (required by Google API) will fail."
        )

    def test_create_audio_data_from_pcm(self, silence_pcm, sample_rate):
        import speech_recognition as sr
        audio = sr.AudioData(silence_pcm, sample_rate=sample_rate, sample_width=2)
        assert audio.sample_rate == sample_rate
        assert audio.sample_width == 2

    def test_get_raw_data_roundtrip(self, silence_audio, silence_pcm):
        raw = silence_audio.get_raw_data()
        assert isinstance(raw, bytes)
        assert raw == silence_pcm

    def test_get_wav_data_valid_header(self, silence_audio):
        wav = silence_audio.get_wav_data()
        assert isinstance(wav, bytes)
        assert wav[:4] == b"RIFF", "Not a valid RIFF/WAV file"
        assert wav[8:12] == b"WAVE", "Missing WAVE chunk marker"

    def test_get_flac_data_valid_magic(self, silence_audio):
        flac = silence_audio.get_flac_data()
        assert isinstance(flac, bytes)
        assert len(flac) > 0, "FLAC output is empty"
        assert flac[:4] == b"fLaC", (
            f"Expected FLAC magic bytes b'fLaC', got {flac[:4]!r}. "
            "The bundled flac binary may be missing or non-executable."
        )

    def test_get_flac_data_from_speech(self, speech_audio):
        flac = speech_audio.get_flac_data()
        assert flac[:4] == b"fLaC"
        assert len(flac) > 200, "FLAC output suspiciously small for speech audio"

    def test_flac_binary_is_executable(self):
        import speech_recognition as sr
        sr_dir = os.path.dirname(sr.__file__)
        flac_path = os.path.join(sr_dir, "flac-linux-x86_64")
        if not os.path.isfile(flac_path):
            pytest.skip("Bundled flac binary not present (may use system flac)")
        assert os.access(flac_path, os.X_OK), (
            f"{flac_path} exists but is not executable. "
            "Run: chmod +x lib/speech_recognition/flac-linux-x86_64"
        )

    def test_audio_data_sample_rate_conversion(self, sample_rate):
        import speech_recognition as sr
        raw_8k = b"\x00\x00" * 8000
        audio = sr.AudioData(raw_8k, sample_rate=8000, sample_width=2)
        flac = audio.get_flac_data(convert_rate=sample_rate)
        assert flac[:4] == b"fLaC"

    def test_audio_file_read_from_wav_bytes(self, sample_rate):
        import speech_recognition as sr

        with io.BytesIO() as wav_buf:
            with wave.open(wav_buf, "w") as wf:
                wf.setnchannels(1)
                wf.setsampwidth(2)
                wf.setframerate(sample_rate)
                wf.writeframes(b"\x00\x00" * sample_rate)
            wav_bytes = wav_buf.getvalue()

        with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as f:
            f.write(wav_bytes)
            tmp_path = f.name

        try:
            recognizer = sr.Recognizer()
            with sr.AudioFile(tmp_path) as source:
                audio = recognizer.record(source)
            assert isinstance(audio, sr.AudioData)
        finally:
            os.unlink(tmp_path)


@pytest.mark.network
class TestGoogleAPIConnectivity:
    """Layer 3: Verify network connectivity to Google's servers."""

    def test_dns_resolves_google(self):
        try:
            infos = socket.getaddrinfo("www.google.com", 80, socket.AF_INET, socket.SOCK_STREAM)
            assert len(infos) > 0
        except socket.gaierror as e:
            pytest.fail(f"DNS resolution of www.google.com failed: {e}")

    def test_tcp_connect_to_google(self):
        try:
            s = socket.create_connection(("www.google.com", 80), timeout=10)
            s.close()
        except OSError as e:
            pytest.fail(f"TCP connection to www.google.com:80 failed: {e}")


@pytest.mark.network
class TestSpeechRecognition:
    """Layer 4: Verify the full recognize_google() call works correctly."""

    def _call_recognize(self, recognizer, audio, timeout_sec=20, **kwargs):
        import speech_recognition as sr
        old = socket.getdefaulttimeout()
        socket.setdefaulttimeout(timeout_sec)
        try:
            return recognizer.recognize_google(audio, **kwargs)
        finally:
            socket.setdefaulttimeout(old)

    def test_silence_raises_unknown_value_error(self, silence_audio):
        # Sending silence must produce UnknownValueError, not a crash — confirms the API is responding.
        import speech_recognition as sr
        r = sr.Recognizer()
        try:
            with pytest.raises(sr.UnknownValueError):
                self._call_recognize(r, silence_audio)
        except sr.RequestError as e:
            pytest.fail(
                f"Google API request failed — possible network issue.\n"
                f"Error: {e}\n"
                f"Run TestGoogleAPIConnectivity tests first to check connectivity."
            )

    def test_speech_returns_non_empty_string(self, speech_audio):
        import speech_recognition as sr
        r = sr.Recognizer()
        try:
            text = self._call_recognize(r, speech_audio)
        except sr.UnknownValueError:
            pytest.fail(
                "Google API returned UnknownValueError for speech audio.\n"
                "The audio may be too quiet or unclear.\n"
                "Try: STT_TEST_WAV=/path/to/clearer_speech.wav python -m pytest tests/"
            )
        except sr.RequestError as e:
            pytest.fail(f"Google API request failed: {e}")

        assert isinstance(text, str), f"Expected str, got {type(text).__name__}"
        assert text.strip(), "Transcription returned an empty string"
        print(f"\n  Transcribed: {text!r}")

    def test_speech_transcription_contains_expected_words(self, speech_audio):
        if os.environ.get("STT_TEST_WAV"):
            pytest.skip("Skipping word-match test when STT_TEST_WAV is set (unknown content)")

        import speech_recognition as sr
        r = sr.Recognizer()
        try:
            text = self._call_recognize(r, speech_audio)
        except sr.UnknownValueError:
            pytest.fail("Google API returned UnknownValueError — espeak audio unclear")
        except sr.RequestError as e:
            pytest.fail(f"Google API request failed: {e}")

        expected_words = {"hello", "world", "testing", "one", "two", "three"}
        found = expected_words & set(text.lower().split())
        print(f"\n  Transcribed: {text!r}  |  matched words: {found}")
        assert found, (
            f"None of the expected words {expected_words} found in {text!r}.\n"
            "Google may have misheard the espeak synthesis — try a real recording."
        )

    def test_show_all_returns_alternatives_list(self, speech_audio):
        import speech_recognition as sr
        r = sr.Recognizer()
        try:
            result = self._call_recognize(r, speech_audio, show_all=True)
        except sr.RequestError as e:
            pytest.fail(f"Google API request failed: {e}")

        if not result:
            pytest.skip("Empty result from Google (speech may have been unclear)")

        assert isinstance(result, dict), f"Expected dict with show_all=True, got {type(result)}"
        alternatives = result.get("alternative", [])
        assert len(alternatives) > 0, f"No alternatives in result: {result}"
        top = alternatives[0]
        assert "transcript" in top, f"Missing 'transcript' key in {top}"
        assert isinstance(top["transcript"], str)
        print(f"\n  Full API response: {result}")

    def test_language_parameter_accepted(self, speech_audio):
        import speech_recognition as sr
        r = sr.Recognizer()
        try:
            text = self._call_recognize(r, speech_audio, language="en-US")
            assert isinstance(text, str)
        except sr.UnknownValueError:
            pass
        except sr.RequestError as e:
            pytest.fail(f"Google API request failed: {e}")

    def test_profanity_filter_parameter_accepted(self, speech_audio):
        import speech_recognition as sr
        r = sr.Recognizer()
        try:
            text = self._call_recognize(r, speech_audio, pfilter=0)
            assert isinstance(text, str)
        except sr.UnknownValueError:
            pass
        except sr.RequestError as e:
            pytest.fail(f"Google API request failed: {e}")


class TestPluginBackend:
    """Layer 5: Test the Plugin class from main.py in isolation."""

    def _import_plugin(self):
        if "main" in sys.modules:
            del sys.modules["main"]
        if PROJECT_ROOT not in sys.path:
            sys.path.insert(0, PROJECT_ROOT)
        import main as m
        return m

    def _make_mock_proc(self, stdout: bytes, stderr: bytes = b""):
        proc = MagicMock()
        proc.poll.return_value = None
        proc.terminate.return_value = None
        proc.communicate.return_value = (stdout, stderr)
        return proc

    def test_main_imports_cleanly(self, decky_mock):
        m = self._import_plugin()
        assert m is not None

    def test_plugin_class_exists(self, decky_mock):
        m = self._import_plugin()
        assert hasattr(m, "Plugin"), "main.py must define a Plugin class"

    def test_plugin_has_required_api_methods(self, decky_mock):
        m = self._import_plugin()
        for method in ["start_recording", "stop_and_transcribe", "cancel_recording", "type_text"]:
            assert hasattr(m.Plugin, method), f"Plugin missing method: {method!r}"

    def test_stop_and_transcribe_no_recording_returns_error(self, decky_mock):
        m = self._import_plugin()
        plugin = m.Plugin()
        plugin._recording_process = None
        result = asyncio.run(plugin.stop_and_transcribe())
        assert isinstance(result, str)
        assert result.startswith("ERROR:"), (
            f"Expected 'ERROR: ...' when no recording in progress, got: {result!r}"
        )

    def test_stop_and_transcribe_empty_audio_returns_error(self, decky_mock):
        m = self._import_plugin()
        plugin = m.Plugin()
        plugin._recording_process = self._make_mock_proc(stdout=b"")
        result = asyncio.run(plugin.stop_and_transcribe())
        assert isinstance(result, str)
        assert result.startswith("ERROR:"), (
            f"Expected 'ERROR: ...' for empty audio, got: {result!r}"
        )

    @pytest.mark.network
    def test_stop_and_transcribe_silence_returns_empty_string(self, decky_mock, silence_pcm):
        m = self._import_plugin()
        plugin = m.Plugin()
        plugin._recording_process = self._make_mock_proc(stdout=silence_pcm)
        result = asyncio.run(plugin.stop_and_transcribe())
        assert isinstance(result, str)
        assert result == "" or result.startswith("ERROR:"), (
            f"Unexpected result for silence PCM: {result!r}"
        )
        print(f"\n  stop_and_transcribe(silence) → {result!r}")

    @pytest.mark.network
    @pytest.mark.audio
    def test_stop_and_transcribe_speech_returns_transcription(self, decky_mock, speech_pcm):
        m = self._import_plugin()
        plugin = m.Plugin()
        plugin._recording_process = self._make_mock_proc(stdout=speech_pcm)
        result = asyncio.run(plugin.stop_and_transcribe())
        assert isinstance(result, str)
        assert not result.startswith("ERROR:"), (
            f"stop_and_transcribe() returned an error: {result}"
        )
        assert result.strip(), (
            "stop_and_transcribe() returned empty string for speech audio — "
            "Google may not have recognized the audio. "
            "Try a clearer recording via STT_TEST_WAV=."
        )
        print(f"\n  stop_and_transcribe(speech) → {result!r}")

    def test_cancel_recording_with_no_process_is_safe(self, decky_mock):
        m = self._import_plugin()
        plugin = m.Plugin()
        plugin._recording_process = None
        asyncio.run(plugin.cancel_recording())

    def test_cancel_recording_terminates_process(self, decky_mock):
        m = self._import_plugin()
        plugin = m.Plugin()
        mock_proc = self._make_mock_proc(stdout=b"")
        plugin._recording_process = mock_proc
        asyncio.run(plugin.cancel_recording())
        mock_proc.terminate.assert_called_once()
        assert plugin._recording_process is None

    def test_find_user_uid_returns_int(self, decky_mock):
        m = self._import_plugin()
        uid = m._find_user_uid()
        assert isinstance(uid, int)
        assert uid > 0
        print(f"\n  Detected user UID: {uid}")

    def test_user_env_has_required_keys(self, decky_mock):
        m = self._import_plugin()
        env = m._user_env()
        for key in ["XDG_RUNTIME_DIR", "PULSE_RUNTIME_PATH", "DISPLAY", "WAYLAND_DISPLAY", "HOME"]:
            assert key in env, f"_user_env() missing key: {key!r}"
        assert env["XDG_RUNTIME_DIR"].startswith("/run/user/"), (
            f"Unexpected XDG_RUNTIME_DIR: {env['XDG_RUNTIME_DIR']!r}"
        )
        print(f"\n  _user_env() sample: XDG={env['XDG_RUNTIME_DIR']} DISPLAY={env['DISPLAY']}")
