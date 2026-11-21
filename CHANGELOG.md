# Changelog

## [0.1.3] - 2026-11-22
- Boot animation: when the Mikro connects, the device-name splash animates into MIKROBE with a matching pad light show, then holds for a couple of seconds. Pads and buttons stay usable while it plays.
- `--test splash` loops the animation; set `"splash": false` in config.json to turn it off

## [0.1.2] - 2026-11-13
- The macOS installer removes the old pre-rename `MikroMK2` install, which could keep hold of the controller

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
