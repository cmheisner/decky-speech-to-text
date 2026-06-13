import decky
import asyncio
import os
import sys
import socket
import subprocess
import tempfile

# SpeechRecognition is bundled under lib/ because Decky's embedded Python doesn't include it. https://github.com/Uberi/speech_recognition (MIT, Anthony Zhang)
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "lib"))
import speech_recognition as sr


def _find_user_uid() -> int:
    """Return the UID of the logged-in user that owns a PulseAudio socket."""
    try:
        for uid_dir in os.listdir("/run/user"):
            try:
                uid = int(uid_dir)
                if os.path.exists(f"/run/user/{uid}/pulse/native"):
                    return uid
            except ValueError:
                continue
    except Exception:
        pass
    return 1000  # Steam Deck default


def _user_env() -> dict:
    """Returns the environment variables needed to reach the user's audio and display session. Decky runs as root, but PulseAudio and the display server live in the user session, so the right paths must be passed explicitly."""
    uid = _find_user_uid()
    xdg = f"/run/user/{uid}"
    env = os.environ.copy()
    env["XDG_RUNTIME_DIR"]    = xdg
    env["PULSE_RUNTIME_PATH"] = f"{xdg}/pulse"
    # XWayland is always :0 in both gaming and desktop mode
    env.setdefault("DISPLAY", ":0")
    # gamescope-0 in gaming mode, wayland-0 in desktop mode
    if "WAYLAND_DISPLAY" not in env:
        for candidate in ("gamescope-0", "wayland-1", "wayland-0"):
            if os.path.exists(f"{xdg}/{candidate}"):
                env["WAYLAND_DISPLAY"] = candidate
                break
        else:
            env["WAYLAND_DISPLAY"] = "gamescope-0"
    env.setdefault("HOME", "/home/deck")
    decky.logger.debug(
        f"_user_env: uid={uid} XDG={xdg} DISPLAY={env['DISPLAY']} "
        f"WAYLAND={env['WAYLAND_DISPLAY']}"
    )
    return env


class Plugin:
    _recording_process = None
    _audio_tmp = None

    async def _main(self):
        decky.logger.info("SpeechToText plugin loaded")
        asyncio.ensure_future(Plugin._install_dependencies())

    @staticmethod
    async def _install_dependencies():
        """Install required system packages on first run if missing."""
        packages = {
            "parecord": "pulseaudio-utils",
            "ydotool":  "ydotool",
            "xdotool":  "xdotool",
            "wl-copy":  "wl-clipboard",
        }

        missing = []
        for binary, package in packages.items():
            result = subprocess.run(["which", binary], capture_output=True)
            if result.returncode != 0:
                missing.append(package)

        if missing:
            decky.logger.info(f"Installing missing packages: {missing}")
            try:
                result = subprocess.run(
                    ["pacman", "-S", "--noconfirm", "--needed"] + missing,
                    capture_output=True, text=True, timeout=120,
                )
                if result.returncode == 0:
                    decky.logger.info("Package installation succeeded")
                else:
                    decky.logger.error(f"pacman failed: {result.stderr.strip()}")
            except Exception as e:
                decky.logger.error(f"Dependency install error: {e}")
        else:
            decky.logger.info("All dependencies already installed")

        try:
            result = subprocess.run(
                ["systemctl", "enable", "--now", "ydotoold"],
                capture_output=True, text=True, timeout=15,
            )
            if result.returncode == 0:
                decky.logger.info("ydotoold enabled and started")
            else:
                decky.logger.warning(f"ydotoold service setup: {result.stderr.strip()}")
        except Exception as e:
            decky.logger.warning(f"ydotoold service error: {e}")

    async def _unload(self):
        await Plugin.cancel_recording(self)
        decky.logger.info("SpeechToText plugin unloaded")

    async def start_recording(self) -> str:
        """Start capturing mic audio via parecord. Returns '' on success or an error string."""
        try:
            if self._recording_process:
                self._recording_process.terminate()
                self._recording_process = None
            if self._audio_tmp and os.path.exists(self._audio_tmp):
                os.unlink(self._audio_tmp)
                self._audio_tmp = None

            env = _user_env()
            decky.logger.info(
                f"start_recording: PULSE_RUNTIME_PATH={env['PULSE_RUNTIME_PATH']} "
                f"XDG_RUNTIME_DIR={env['XDG_RUNTIME_DIR']}"
            )

            # Writing to a temp file instead of stdout avoids the 64 KB pipe buffer limit, which would cap recordings to about 2 seconds.
            self._audio_tmp = tempfile.mktemp(suffix=".raw")
            self._recording_process = subprocess.Popen(
                ["parecord", "--raw", "--channels=1", "--rate=16000", "--format=s16le",
                 "--latency-msec=100", self._audio_tmp],
                stderr=subprocess.PIPE,
                env=env,
            )

            await asyncio.sleep(0.25)
            if self._recording_process.poll() is not None:
                _, err = self._recording_process.communicate()
                self._recording_process = None
                msg = err.decode(errors="replace").strip() or "parecord exited immediately"
                decky.logger.error(f"parecord failed on startup: {msg}")
                return f"parecord failed: {msg}"

            decky.logger.info("Recording started (parecord running)")
            return ""

        except FileNotFoundError:
            decky.logger.error("parecord not found")
            return "parecord not found — run: sudo pacman -S pulseaudio-utils"
        except Exception as e:
            decky.logger.error(f"start_recording exception: {e}")
            return str(e)

    async def stop_and_transcribe(self) -> str:
        """Stop recording and return the transcript, '' if nothing heard, or 'ERROR: ...'."""
        proc = self._recording_process
        audio_tmp = self._audio_tmp
        self._recording_process = None
        self._audio_tmp = None

        if not proc:
            decky.logger.warning("stop_and_transcribe: no recording in progress")
            return "ERROR: No recording in progress"

        try:
            # A short sleep lets parecord drain the audio buffer before we terminate it — without this, the last ~200 ms of audio is lost.
            await asyncio.sleep(0.3)
            proc.terminate()
            _, stderr_data = proc.communicate(timeout=3)
        except Exception as e:
            decky.logger.error(f"Error stopping recording: {e}")
            try:
                proc.kill()
            except Exception:
                pass
            return f"ERROR: Failed to stop recording: {e}"

        if stderr_data:
            stderr_str = stderr_data.decode(errors="replace").strip()
            if stderr_str:
                decky.logger.info(f"parecord stderr: {stderr_str}")

        try:
            with open(audio_tmp, "rb") as f:
                raw_data = f.read()
        except Exception as e:
            decky.logger.error(f"Failed to read audio file: {e}")
            return f"ERROR: Failed to read audio: {e}"
        finally:
            try:
                os.unlink(audio_tmp)
            except Exception:
                pass

        if not raw_data:
            decky.logger.warning("No audio data captured")
            return "ERROR: No audio captured — check mic permissions and PulseAudio"

        decky.logger.info(f"Audio captured: {len(raw_data)} bytes — sending to Google")

        try:
            recognizer = sr.Recognizer()
            audio = sr.AudioData(raw_data, sample_rate=16000, sample_width=2)
            old_timeout = socket.getdefaulttimeout()
            socket.setdefaulttimeout(10)
            try:
                try:
                    text = recognizer.recognize_google(audio)
                except AttributeError:
                    # Google occasionally returns a malformed response; retry once
                    decky.logger.warning("recognize_google attribute error, retrying…")
                    await asyncio.sleep(0.4)
                    text = recognizer.recognize_google(audio)
            finally:
                socket.setdefaulttimeout(old_timeout)
            decky.logger.info(f"Transcribed: {text!r}")
            return text
        except sr.UnknownValueError:
            decky.logger.info("Speech not understood")
            return ""
        except sr.RequestError as e:
            decky.logger.error(f"Google Speech API error: {e}")
            return f"ERROR: Google API error: {e}"
        except Exception as e:
            decky.logger.error(f"Transcription error: {e}")
            return f"ERROR: {e}"

    async def cancel_recording(self) -> None:
        """Cancel an in-progress recording without transcribing."""
        if self._recording_process:
            self._recording_process.terminate()
            try:
                self._recording_process.communicate(timeout=2)
            except Exception:
                self._recording_process.kill()
            self._recording_process = None
        if self._audio_tmp:
            try:
                os.unlink(self._audio_tmp)
            except Exception:
                pass
            self._audio_tmp = None

    async def type_text(self, text: str) -> str:
        """Type text at the cursor. Returns '' on success, 'CLIPBOARD' if fell back to clipboard only, or 'ERROR: ...' if all methods failed."""
        env = _user_env()
        decky.logger.info(f"type_text: {text!r}")

        # Primary: copy to clipboard then simulate Ctrl+V via ydotool. This works more reliably than character injection because the target app handles the paste itself.
        clipboard_ready = False
        try:
            cp = subprocess.run(["wl-copy", "--", text], env=env,
                                capture_output=True, timeout=5)
            if cp.returncode == 0:
                clipboard_ready = True
                await asyncio.sleep(0.05)
                paste = subprocess.run(
                    ["ydotool", "key", "ctrl+v"],
                    env=env, capture_output=True, timeout=5,
                )
                if paste.returncode == 0:
                    decky.logger.info("type_text: wl-copy + ydotool ctrl+v succeeded")
                    return ""
                decky.logger.warning(
                    f"ydotool ctrl+v failed (rc={paste.returncode}): "
                    f"{paste.stderr.decode(errors='replace').strip()}"
                )
            else:
                decky.logger.warning(
                    f"wl-copy failed (rc={cp.returncode}): "
                    f"{cp.stderr.decode(errors='replace').strip()}"
                )
        except FileNotFoundError as e:
            decky.logger.info(f"wl-copy or ydotool not found: {e}")
        except Exception as e:
            decky.logger.warning(f"wl-copy + ydotool ctrl+v error: {e}")

        # Fallback: xdotool ctrl+v (X11)
        if clipboard_ready:
            try:
                xenv = env.copy()
                xenv["DISPLAY"] = ":0"
                paste = subprocess.run(
                    ["xdotool", "key", "--clearmodifiers", "ctrl+v"],
                    env=xenv, capture_output=True, timeout=5,
                )
                if paste.returncode == 0:
                    decky.logger.info("type_text: wl-copy + xdotool ctrl+v succeeded")
                    return ""
                decky.logger.warning(
                    f"xdotool ctrl+v failed (rc={paste.returncode}): "
                    f"{paste.stderr.decode(errors='replace').strip()}"
                )
            except FileNotFoundError:
                decky.logger.info("xdotool not found for ctrl+v")
            except Exception as e:
                decky.logger.warning(f"xdotool ctrl+v error: {e}")
            decky.logger.info("type_text: paste simulation failed but clipboard is ready")
            return "CLIPBOARD"

        # Fallback: ydotool type (direct key injection)
        try:
            result = subprocess.run(
                ["ydotool", "type", "--key-delay", "12", "--", text],
                env=env, capture_output=True, timeout=15,
            )
            if result.returncode == 0:
                decky.logger.info("type_text: ydotool type succeeded")
                return ""
            decky.logger.warning(
                f"ydotool type failed (rc={result.returncode}): "
                f"{result.stderr.decode(errors='replace').strip()}"
            )
        except FileNotFoundError:
            decky.logger.info("ydotool not found, trying xdotool")
        except subprocess.TimeoutExpired:
            decky.logger.error("ydotool type timed out")

        # Fallback: xdotool type
        try:
            xenv = env.copy()
            xenv["DISPLAY"] = ":0"
            result = subprocess.run(
                ["xdotool", "type", "--clearmodifiers", "--delay", "12", "--", text],
                env=xenv, capture_output=True, timeout=15,
            )
            if result.returncode == 0:
                decky.logger.info("type_text: xdotool type succeeded")
                return ""
            decky.logger.warning(
                f"xdotool type failed (rc={result.returncode}): "
                f"{result.stderr.decode(errors='replace').strip()}"
            )
        except FileNotFoundError:
            decky.logger.info("xdotool not found, trying xclip clipboard fallback")
        except subprocess.TimeoutExpired:
            decky.logger.error("xdotool type timed out")

        # Last resort: xclip (clipboard only, no auto-paste)
        try:
            result = subprocess.run(
                ["xclip", "-selection", "clipboard"],
                input=text.encode(),
                env=env, capture_output=True, timeout=5,
            )
            if result.returncode == 0:
                decky.logger.info("type_text: xclip clipboard copy succeeded")
                return "CLIPBOARD"
            decky.logger.warning(
                f"xclip failed (rc={result.returncode}): "
                f"{result.stderr.decode(errors='replace').strip()}"
            )
        except FileNotFoundError:
            decky.logger.info("xclip not found")
        except subprocess.TimeoutExpired:
            decky.logger.warning("xclip timed out")
        except Exception as e:
            decky.logger.warning(f"xclip error: {e}")

        decky.logger.error("type_text: all methods failed")
        return "ERROR: No input tool available. Install ydotool or xdotool."
