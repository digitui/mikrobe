# Contributing

Thanks for helping! The most useful contributions right now are **reports from real hardware**.

## Bug reports
Please include:
- macOS or Windows version, Live version, and Python version (`python3 --version`)
- the output of `python3 bridge/mikrobe.py --list`
- the bridge log. On macOS it's `~/Library/Logs/Mikrobe.log`; otherwise it's the terminal output.
- Live's `Log.txt` lines that start with `Mikrobe`

## Button mapping
If a button triggers the wrong action:
1. Stop the background bridge. On macOS, use `scripts/macos/bridge_control_mac.command`.
2. Run `python3 bridge/mikrobe.py --monitor`.
3. Press each button and note the printed name and bit number.
4. Open a *Button mapping report* issue with a list of **physical label → printed name / bit**.

The fix belongs in `BUTTON_BITS`, which appears **in both** `bridge/mikrobe.py` and `ableton/Mikrobe/Mikrobe.py`. Keep the two copies identical.

## Pull requests
- Keep the bridge dependency-light: hidapi, mido, python-rtmidi and Pillow.
- The Remote Script must stay compatible with Live's embedded Python. Avoid f-strings and Python 3.8+-only syntax there (Live 10 uses Python 2.7).
- Run `python tests/test_e2e.py` before you submit. If you change behaviour, extend the test.
- If you change the MIDI/SysEx protocol, update `docs/PROTOCOL.md`.
