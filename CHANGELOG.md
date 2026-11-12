# Changelog

## [0.1.1] - 2026-11-12
- Pads now light up for notes playing back from a MIDI clip on the selected track (NOTES mode, Session and Arrangement). Short drum hits flash too.
- Optional sample-accurate playback lights: route a MIDI track to the `Mikrobe Feedback` port
- Lit pads use full-brightness track colour; pads you hit are brighter still
- New setting: `playback_flash_ms`

## [0.1.0] - 2026-11-12
First public release of Mikrobe.
- HID bridge: velocity pads with optional aftertouch, buttons, encoder, button and pad LEDs, 128×64 screen renderer
- Ableton control surface: transport, mixer, sends, device control, tempo, track and scene navigation, 4×4 clip launching with colours
- macOS installer with a LaunchAgent, a control panel script and an uninstaller
- Hardware test modes: `--monitor`, `--test display`, `--test leds`, `--list`
- Simulation test and GitHub Actions CI
