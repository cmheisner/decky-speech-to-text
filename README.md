# SpeechToText — Decky Loader Plugin

A microphone recorder that translates your voice to text and types it wherever your cursor is - just like the mic button for smartphones.

## Install

Requires [Decky Loader](https://github.com/SteamDeckHomebrew/decky-loader) already installed on your Steam Deck.

### Option 1: Decky Plugin Store *(coming soon)*

Once approved, it will be available directly in the Decky store:

1. Press the **⋮ (Quick Access)** button on your Steam Deck.
2. Go to the **Decky** tab and open the **Store**.
3. Search for **SpeechToText** and tap **Install**.

**Dependencies are installed automatically in the background on first load — nothing else required.*

### Option 2: Manual install

In desktop mode:

1. Make sure **Decky Loader** is installed ([install guide](https://github.com/SteamDeckHomebrew/decky-loader)).
2. Open **Konsole** or your preferred CLI with sudo.
3. Set a `sudo` password if you haven't already
4. Run the installer:

```bash
curl -L https://github.com/cmheisner/decky-speech-to-text/releases/latest/download/install.sh | bash
```

Or clone and install manually:

```bash
git clone https://github.com/cmheisner/decky-speech-to-text.git
cd decky-speech-to-text
bash install.sh
```

**If Node.js/npm isn't installed, the script will install it automatically via [nvm](https://github.com/nvm-sh/nvm).*

Then reload Decky via Quick Access Menu > Decky > ··· > Reload plugins

## Features

- **Start and stop recording** — records for as long as you want, then transcribes via Google Speech Recognition
- **Auto-pastes at your cursor** — close the menu after stopping and the transcript is typed into whatever is focused
- **All controls in the Quick Access Menu** — start/stop recording, view last transcript, copy or clear

## How to Use

1. Press **⋮** (Quick Access) > decky icon > tap **SpeechToText**.
2. Press **Start Recording** — the status bar turns red.
3. Speak naturally.
4. Press **Stop Recording** — the status bar turns orange while transcribing.
5. Close the menu. After ~2.5 seconds the transcript is automatically typed where your cursor is focused.
6. The last transcript is saved in the panel — tap **Copy to Clipboard** or **Clear** any time.

## Requirements

| Requirement                                                    | Notes                                                                 |
| -------------------------------------------------------------- | --------------------------------------------------------------------- |
| [Decky Loader](https://github.com/SteamDeckHomebrew/decky-loader) | Plugin host                                                           |
| Internet connection                                            | Speech recognition uses Google's API                                  |
| `ydotool` + `ydotoold`                                     | Primary text injection (Wayland/Gamescope) — installed automatically |
| `xdotool`                                                    | Fallback text injection (X11) — installed automatically              |
| `wl-clipboard`                                               | Clipboard fallback — installed automatically                         |

System dependencies (`ydotool`, `xdotool`, `wl-clipboard`) are installed automatically on first load. A `sudo` password and internet connection are required.

## How It Works

- **Frontend** (TypeScript/React): Renders the Quick Access Menu panel. Sends recording commands to the backend via Decky's RPC bridge.
- **Backend** (Python): Uses `parecord` to capture microphone audio, sends it to Google's Speech Recognition API via the `SpeechRecognition` library, then types the result using `ydotool type` (Gamescope/Wayland), falling back to `xdotool type` (X11) or clipboard if needed.

## Troubleshooting

**Text isn't being typed**
Make sure `ydotoold` is running: `sudo systemctl enable --now ydotoold`. If you installed from the store, this is handled automatically on first load.

**Speech not recognized**
Make sure you have an internet connection — recognition is done via Google's servers.

**"ydotoold is not running"**
Run `sudo systemctl enable --now ydotoold` in a terminal, or re-run `install.sh`.

## License

MIT — see [LICENSE](LICENSE)
