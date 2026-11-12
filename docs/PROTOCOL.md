# Protocol reference

## 1. Mikro MK2 USB HID (VID `0x17CC`, PID `0x1200`)

### Input report `0x01`: buttons and encoder (6 bytes)
| Byte | Content |
|---|---|
| 0 | `0x01` |
| 1–4 | 32-bit little-endian button bitfield (bit set = pressed) |
| 5 | Encoder position, 0–15, wraps around |

| Bit | Button | Bit | Button | Bit | Button | Bit | Button |
|---|---|---|---|---|---|---|---|
| 0 | SHIFT | 8–10 | – | 16 | MAIN | 24 | MUTE |
| 1 | ERASE | 11 | encoder push | 17 | ▶ (screen) | 25 | SOLO |
| 2 | REC | 12 | NOTE REPEAT | 18 | ◀ (screen) | 26 | SELECT |
| 3 | PLAY | 13 | SAMPLING | 19 | NAV | 27 | DUPLICATE |
| 4 | GRID | 14 | BROWSE | 20 | CONTROL | 28 | VIEW |
| 5 | transport ▶ | 15 | GROUP | 21 | F3 | 29 | PAD MODE |
| 6 | transport ◀ | | | 22 | F2 | 30 | PATTERN |
| 7 | RESTART | | | 23 | F1 | 31 | SCENE |

> Ardour's driver lists bits 8–15 and 16–23 in reverse order compared with maschine-rs. This project follows maschine-rs. Reports from real hardware are welcome; use `--monitor`.

### Input report `0x20`: pads
16 two-byte pairs `(lo, hi)`: `pad = hi >> 4`, `pressure = ((hi & 0x0F) << 8) | lo` (0–4095).
Pad index 0 is the **top-left** pad; the indexes run row by row to the bottom-right.

### Output report `0x80`: LEDs (79 bytes)
`0x80` followed by 78 brightness bytes (0–127):

| Offset | LED |
|---|---|
| 0–7 | F1, F2, F3, CONTROL, NAV, ◀, ▶, MAIN |
| 8–10 | GROUP (R, G, B) |
| 11–29 | BROWSE, SAMPLING, NOTE REPEAT, RESTART, transport ◀, transport ▶, GRID, PLAY, REC, ERASE, SHIFT, SCENE, PATTERN, PAD MODE, VIEW, DUPLICATE, SELECT, SOLO, MUTE |
| 30–77 | 16 pads × RGB, top-left first, row by row |

### Output report `0xE0`: display (4 × 265 bytes)
The screen is 128×64 at 1 bit per pixel, split into 8 pages of 8 rows. Each byte is one 8-pixel vertical strip within a page, with the LSB as the top pixel. Each report sends two pages:

```
E0 00 00 <page 0|2|4|6> 00 80 00 02 00  + 256 bytes (2 pages × 128 columns)
```

## 2. Bridge ↔ Live MIDI

| Direction | Message | Meaning |
|---|---|---|
| → Live | Note on/off, ch 1 | Pads in NOTE mode (base note + pad, pad 1 = bottom-left) |
| → Live | Note on/off, ch 2, notes 0–15 | Pads in CLIP mode (0 = bottom-left) |
| → Live | CC 20+bit, ch 16 | Button (127 = pressed, 0 = released) |
| → Live | CC 10, ch 16 | Encoder, relative two's complement |
| → Live | SysEx `F0 7D 4D 4B 7F F7` | Bridge (re)connected; the script resends its full state |

SysEx from Live → bridge always starts `F0 7D 4D 4B <cmd>`:

| cmd | Payload | Meaning |
|---|---|---|
| `01` | line(0–3) ascii… | Screen line. A TAB character splits it into left-aligned and right-aligned text. |
| `02` | type(0 none, 1 unipolar, 2 bipolar) value(0–127) | Value bar |
| `03` | pad(0–15) r g b anim(0 static, 1 blink, 2 pulse) | Clip-mode pad colour |
| `04` | 32 levels (0 off, 1 dim, 2 on), indexed by button bit | Button LEDs |
| `05` | pad_mode(0 note, 1 clip) base_note | Pad mode |
| `06` | r g b | Selected track colour (GROUP LED, NOTE-mode pads) |
| `7E` | – | Heartbeat (~1/s). With no heartbeat for 3 s, the bridge shows "Waiting for Live". |

The `BUTTON_BITS` tables in `bridge/mikrobe.py` and `ableton/Mikrobe/Mikrobe.py` must stay identical.
