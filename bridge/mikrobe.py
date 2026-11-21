#!/usr/bin/env python3
"""
Mikrobe - Maschine Mikro MK2  <->  Ableton Live bridge
======================================================

Talks to the Native Instruments Maschine Mikro MK2 directly over USB HID
(no Maschine software, no NI Controller Editor, no NIHardwareAgent) and
exposes it to Ableton Live as a pair of MIDI ports:

    "Mikrobe Controls"    bridge -> Live   (pads, buttons, encoder)
    "Mikrobe Feedback"  Live -> bridge   (LEDs, pad colours, screen text)

The companion Ableton Remote Script ("Mikrobe" control surface) drives the
LEDs and the 128x64 screen through SysEx on the Feedback port.

Usage:
    python3 mikrobe.py                 run the bridge
    python3 mikrobe.py --monitor       print raw pad/button/encoder events (no MIDI)
    python3 mikrobe.py --test display  draw a test pattern on the screen
    python3 mikrobe.py --test leds     walk through every LED
    python3 mikrobe.py --list          list Native Instruments HID devices

HID protocol reference: reverse-engineered by the open-source projects
timsavage/maschine-rs (MIT) and Ardour's maschine2 surface (GPL, Robin Gareus).
"""

from __future__ import print_function

import argparse
import json
import os
import sys
import threading
import time

# --------------------------------------------------------------------------
# Hardware constants
# --------------------------------------------------------------------------

__version__ = "0.1.3"

VENDOR_ID = 0x17CC          # Native Instruments
PRODUCT_ID = 0x1200         # Maschine Mikro MK2

REPORT_BUTTONS = 0x01       # in:  4 bytes button bitfield + 1 byte encoder (0..15)
REPORT_PADS = 0x20          # in:  16 x (lo, hi) ; pad = hi>>4, pressure = (hi&0xF)<<8 | lo
REPORT_LEDS = 0x80          # out: 78 bytes of LED brightness (0..127)
REPORT_DISPLAY = 0xE0       # out: 9 byte header + 256 bytes (2 pages of 128 columns)

# Bit index in the 4-byte button field  ->  button name
BUTTON_BITS = {
    0: "shift", 1: "erase", 2: "rec", 3: "play", 4: "grid",
    5: "transport_right", 6: "transport_left", 7: "restart",
    11: "encoder_push", 12: "note_repeat", 13: "sampling", 14: "browse", 15: "group",
    16: "main", 17: "arrow_right", 18: "arrow_left", 19: "nav", 20: "control",
    21: "f3", 22: "f2", 23: "f1",
    24: "mute", 25: "solo", 26: "select", 27: "duplicate", 28: "view",
    29: "pad_mode", 30: "pattern", 31: "scene",
}
BUTTON_INDEX = dict((v, k) for k, v in BUTTON_BITS.items())

# Button name -> offset in the 78 byte LED report (mono LEDs)
BUTTON_LED = {
    "f1": 0x00, "f2": 0x01, "f3": 0x02, "control": 0x03, "nav": 0x04,
    "arrow_left": 0x05, "arrow_right": 0x06, "main": 0x07,
    # 0x08..0x0A = Group button RGB
    "browse": 0x0B, "sampling": 0x0C, "note_repeat": 0x0D, "restart": 0x0E,
    "transport_left": 0x0F, "transport_right": 0x10, "grid": 0x11, "play": 0x12,
    "rec": 0x13, "erase": 0x14, "shift": 0x15, "scene": 0x16, "pattern": 0x17,
    "pad_mode": 0x18, "view": 0x19, "duplicate": 0x1A, "select": 0x1B,
    "solo": 0x1C, "mute": 0x1D,
}
LED_GROUP_RGB = 0x08
LED_PADS_RGB = 0x1E          # 16 x RGB, hardware pad order (top-left first, row by row)
LED_COUNT = 78

DISPLAY_W, DISPLAY_H = 128, 64

# --------------------------------------------------------------------------
# MIDI protocol shared with the Ableton Remote Script (keep in sync!)
# --------------------------------------------------------------------------

PORT_TO_LIVE = "Mikrobe Controls"
PORT_FROM_LIVE = "Mikrobe Feedback"

CH_NOTES = 0                 # MIDI ch 1  : pads in NOTE mode  -> armed track
CH_SESSION = 1               # MIDI ch 2  : pads in CLIP mode  -> script (notes 0..15)
CH_CONTROL = 15              # MIDI ch 16 : buttons + encoder  -> script
CC_ENCODER = 10              # relative, two's complement (1 = +1, 127 = -1)
CC_BUTTON_BASE = 20          # CC = 20 + bit index (20..51), 127 press / 0 release

SYSEX_ID = [0x7D, 0x4D, 0x4B]   # 0x7D = non-commercial manufacturer ID, "MK"
SX_LINE = 0x01       # line(0-3) ascii...        ; '\t' splits left / right aligned text
SX_BAR = 0x02        # type(0 none,1 uni,2 bipolar) value(0-127)
SX_PAD = 0x03        # pad(0-15, 0=bottom-left) r g b anim(0 static,1 blink,2 pulse)
SX_BUTTONS = 0x04    # 32 bytes, indexed by button bit: 0 off, 1 dim, 2 on
SX_MODE = 0x05       # pad_mode(0 note,1 clip) base_note
SX_GROUP = 0x06      # r g b  (selected track colour, 0-127)
SX_PLAYING = 0x08    # note... : notes currently sounding on the selected track (sent ~10x/s)
SX_HEARTBEAT = 0x7E  # script -> bridge, ~1/s
SX_HELLO = 0x7F      # bridge -> script: "please resend everything"

# --------------------------------------------------------------------------
# Configuration
# --------------------------------------------------------------------------

DEFAULT_CONFIG = {
    "pad_on_threshold": 400,      # 0..4095 pressure to trigger a note
    "pad_off_threshold": 250,     # pressure below which the note is released
    "pad_full_scale": 3000,       # pressure that gives velocity 127
    "velocity_curve": 0.7,        # <1 = more sensitive, >1 = harder to reach high velocities
    "velocity_attack_ms": 3,      # wait this long after onset to capture the peak (0 = instant)
    "fixed_velocity": 0,          # 1..127 to ignore pressure, 0 = off
    "aftertouch": "off",          # "off" | "poly" | "channel"
    "base_note": 36,              # pad 1 (bottom-left) in NOTE mode; C1 = drum rack default
    "encoder_invert": False,
    "led_on": 127,
    "led_dim": 10,
    "pad_note_mode_dim": 0.12,    # unlit pad brightness in NOTE mode (fraction of track colour)
    "playback_flash_ms": 150,     # how long a pad stays lit after Live reports a playing note
    "display_invert": False,
    "display_fps": 25,
    "font_size": 11,
    "splash": True,               # boot animation on the screen + pads when the Mikro connects
}


def load_config(path):
    cfg = dict(DEFAULT_CONFIG)
    if path and os.path.exists(path):
        with open(path) as f:
            user = json.load(f)
        unknown = set(user) - set(cfg)
        if unknown:
            print("config: ignoring unknown keys: %s" % ", ".join(sorted(unknown)))
        cfg.update(dict((k, v) for k, v in user.items() if k in cfg))
    return cfg


# --------------------------------------------------------------------------
# Pad geometry helpers
#   hardware index : order in the pad report / LED report, 0 = top-left, row by row
#   pad number     : Maschine numbering minus one, 0 = bottom-left ("pad 1")
# --------------------------------------------------------------------------

def hw_to_padnum(hw):
    row_from_top, col = divmod(hw, 4)
    return (3 - row_from_top) * 4 + col


def padnum_to_hw(n):
    row_from_bottom, col = divmod(n, 4)
    return (3 - row_from_bottom) * 4 + col


def full_bright(rgb, whiten=0.0):
    """Scale a colour so its brightest channel is 127, optionally blending toward white."""
    m = max(rgb) or 1
    out = [c * 127.0 / m for c in rgb]
    return tuple(int(c + (127 - c) * whiten) for c in out)


NOTE_NAMES = ["C", "C#", "D", "D#", "E", "F", "F#", "G", "G#", "A", "A#", "B"]


def note_name(n):
    return "%s%d" % (NOTE_NAMES[n % 12], n // 12 - 2)   # Ableton convention: C3 = 60


# --------------------------------------------------------------------------
# HID device wrapper (supports both the "hidapi" (cython) and "hid" (ctypes) packages)
# --------------------------------------------------------------------------

class MikroDevice(object):
    def __init__(self, path=None):
        try:
            import hid
        except ImportError:
            sys.exit("Missing dependency: pip install hidapi")
        self._hid = hid
        self._dev = None
        self._path = path

    @staticmethod
    def enumerate_devices():
        import hid
        return [d for d in hid.enumerate() if d.get("vendor_id") == VENDOR_ID]

    def open(self):
        hid = self._hid
        path = self._path
        if path is None:
            matches = [d for d in hid.enumerate(VENDOR_ID, PRODUCT_ID)]
            if not matches:
                raise IOError("Maschine Mikro MK2 not found (VID 17cc PID 1200)")
            path = matches[0]["path"]
        if hasattr(hid, "device"):                 # cython-hidapi ("pip install hidapi")
            dev = hid.device()
            dev.open_path(path)
            dev.set_nonblocking(0)
            self._read = lambda n, t: dev.read(n, t)
            self._write = lambda data: dev.write(list(data))
        else:                                      # ctypes "hid" package
            dev = hid.Device(path=path)
            self._read = lambda n, t: list(dev.read(n, t) or b"")
            self._write = lambda data: dev.write(bytes(bytearray(data)))
        self._dev = dev
        return self

    def close(self):
        if self._dev is not None:
            try:
                self._dev.close()
            except Exception:
                pass
            self._dev = None

    def read(self, timeout_ms=5):
        data = self._read(128, timeout_ms)
        return data if data else None

    def write(self, data):
        n = self._write(data)
        if n is not None and n < 0:
            raise IOError("HID write failed")

    def write_leds(self, leds):
        self.write([REPORT_LEDS] + [max(0, min(127, int(v))) for v in leds])

    def write_display(self, buf):
        """buf: 1024 bytes, 8 pages x 128 columns, LSB = top pixel of each page."""
        for page in range(0, 8, 2):
            header = [REPORT_DISPLAY, 0x00, 0x00, page, 0x00, 0x80, 0x00, 0x02, 0x00]
            start = page * 128
            self.write(header + list(buf[start:start + 256]))


# --------------------------------------------------------------------------
# Screen renderer
# --------------------------------------------------------------------------

class Screen(object):
    """Renders 4 text lines + an optional value bar into the device's page format."""

    LINE_Y = (0, 13, 26, 39)

    def __init__(self, font_size=11, invert=False):
        try:
            from PIL import Image, ImageDraw, ImageFont
        except ImportError:
            sys.exit("Missing dependency: pip install pillow")
        self._Image, self._ImageDraw = Image, ImageDraw
        self.font = None
        try:
            self.font = ImageFont.load_default(size=font_size)   # Pillow >= 10.1
        except TypeError:
            self.font = ImageFont.load_default()
        self.invert = invert
        self.lines = ["", "", "", ""]
        self.bar = (0, 0)

    def _text_w(self, draw, text):
        try:
            return int(draw.textlength(text, font=self.font))
        except AttributeError:
            return draw.textsize(text, font=self.font)[0]

    def _fit(self, draw, text, width):
        if self._text_w(draw, text) <= width:
            return text
        while text and self._text_w(draw, text + "..") > width:
            text = text[:-1]
        return text + ".."

    def _draw_line(self, draw, y, text, fg):
        if "\t" in text:
            left, right = text.split("\t", 1)
        else:
            left, right = text, ""
        right = self._fit(draw, right, 70)
        rw = self._text_w(draw, right) if right else 0
        left = self._fit(draw, left, DISPLAY_W - 4 - rw - (6 if right else 0))
        draw.text((2, y), left, font=self.font, fill=fg)
        if right:
            draw.text((DISPLAY_W - 2 - rw, y), right, font=self.font, fill=fg)

    def render_image(self):
        img = self._Image.new("1", (DISPLAY_W, DISPLAY_H), 0)
        d = self._ImageDraw.Draw(img)
        # header line: inverted
        d.rectangle((0, 0, DISPLAY_W - 1, 11), fill=1)
        self._draw_line(d, self.LINE_Y[0] - 1, self.lines[0], 0)
        for i in (1, 2, 3):
            self._draw_line(d, self.LINE_Y[i], self.lines[i], 1)
        kind, value = self.bar
        if kind:
            x0, x1, y0, y1 = 2, DISPLAY_W - 3, 54, 61
            d.rectangle((x0, y0, x1, y1), outline=1, fill=0)
            inner = (x1 - 2) - (x0 + 2)
            if kind == 1:
                w = int(round(inner * value / 127.0))
                if w > 0:
                    d.rectangle((x0 + 2, y0 + 2, x0 + 2 + w, y1 - 2), fill=1)
            else:
                mid = x0 + 2 + inner // 2
                pos = x0 + 2 + int(round(inner * value / 127.0))
                d.line((mid, y0 + 1, mid, y1 - 1), fill=1)
                d.rectangle((min(mid, pos), y0 + 2, max(mid, pos), y1 - 2), fill=1)
        return img

    @staticmethod
    def image_to_pages(img, invert=False):
        px = img.load()
        buf = bytearray(DISPLAY_W * DISPLAY_H // 8)
        for y in range(DISPLAY_H):
            bit = 1 << (y & 7)
            base = (y >> 3) * DISPLAY_W
            for x in range(DISPLAY_W):
                if bool(px[x, y]) != invert:
                    buf[base + x] |= bit
        return bytes(buf)

    def render(self):
        return self.image_to_pages(self.render_image(), self.invert)



# --------------------------------------------------------------------------
# Boot animation: "MASCHINE / MIKRO / MK2"  ->  "MIKRO" + "BE" = "MIKROBE"
# --------------------------------------------------------------------------

BAYER4 = ((0, 8, 2, 10), (12, 4, 14, 6), (3, 11, 1, 9), (15, 7, 13, 5))


def _ease_out(x):
    x = max(0.0, min(1.0, x))
    return 1 - (1 - x) ** 3


def _ease_in(x):
    x = max(0.0, min(1.0, x))
    return x ** 3


def _bounce(x):
    """Drop with two small bounces (0 -> 1)."""
    x = max(0.0, min(1.0, x))
    n, d = 7.5625, 2.75
    if x < 1 / d:
        return n * x * x
    if x < 2 / d:
        x -= 1.5 / d
        return n * x * x + 0.75
    if x < 2.5 / d:
        x -= 2.25 / d
        return n * x * x + 0.9375
    x -= 2.625 / d
    return n * x * x + 0.984375


def _seg(t, a, b):
    """Progress 0..1 of t through the time segment [a, b]."""
    return max(0.0, min(1.0, (t - a) / float(b - a)))


class Splash(object):
    """1-bit boot animation for the 128x64 screen, plus a matching pad light show."""

    DURATION = 6.2                  # ~2.5 s hold on the finished MIKROBE screen before fading out
    TAGLINE = "for Ableton Live"

    def __init__(self):
        from PIL import Image, ImageDraw, ImageFont
        self._Image, self._ImageDraw = Image, ImageDraw

        def font(size):
            try:
                return ImageFont.load_default(size=size)
            except TypeError:                      # Pillow < 10.1: single small bitmap font
                return ImageFont.load_default()

        self.f_big, self.f_small, self.f_tag = font(25), font(11), font(10)
        probe = ImageDraw.Draw(Image.new("1", (1, 1)))
        self.w_mikro = self._w(probe, "MIKRO", self.f_big, 1)
        self.w_be = self._w(probe, "BE", self.f_big, 1)
        self.w_maschine = self._w(probe, "MASCHINE", self.f_small, 0)
        self.w_mk2 = self._w(probe, "MK2", self.f_small, 0)
        self.w_tag = self._w(probe, self.TAGLINE, self.f_tag, 0)
        self.h_big = self._h(probe, "MIKROBE", self.f_big, 1)

    @staticmethod
    def _w(d, text, f, stroke):
        box = d.textbbox((0, 0), text, font=f, stroke_width=stroke)
        return box[2] - box[0]

    @staticmethod
    def _h(d, text, f, stroke):
        box = d.textbbox((0, 0), text, font=f, stroke_width=stroke)
        return box[3] - box[1]

    @staticmethod
    def _text(d, xy, text, f, stroke=0):
        # textbbox has a font-dependent top offset; draw so the visible top lands at xy[1]
        top = d.textbbox((0, 0), text, font=f, stroke_width=stroke)[1]
        d.text((xy[0], xy[1] - top), text, font=f, fill=1, stroke_width=stroke, stroke_fill=1)

    # icon pads: 0 top-left, 1 top-right, 2 bottom-left, 3 bottom-right
    PRESS_START = 3.6          # when the icon starts "playing"
    PRESS_STEP = 0.2           # seconds per step
    PRESS_PATTERN = (2, None, 1, 2, 3, None, 0, 1)   # a little beat; None = rest

    def pressed_pad(self, t):
        """Which icon pad (0-3) is held down at time t, or None."""
        if t < self.PRESS_START:
            return None
        step, frac = divmod((t - self.PRESS_START) / self.PRESS_STEP, 1.0)
        if frac > 0.6:                         # released for the last part of each step
            return None
        return self.PRESS_PATTERN[int(step) % len(self.PRESS_PATTERN)]

    def _pads_icon(self, d, cx, cy, t):
        """A 2x2 pad grid (12x12 px). The pressed pad fills in and sinks by a pixel."""
        held = self.pressed_pad(t)
        size, gap = 5, 2
        x0, y0 = cx - (2 * size + gap) // 2, cy - (2 * size + gap) // 2
        for q in range(4):
            r, c = divmod(q, 2)
            x, y = x0 + c * (size + gap), y0 + r * (size + gap)
            if q == held:
                d.rectangle((x + 1, y + 1, x + size - 1, y + size), fill=1)     # pressed: filled, sunk 1 px
            else:
                d.rectangle((x, y, x + size - 1, y + size - 1), outline=1, fill=0)

    @staticmethod
    def _dither(img, amount, invert=False):
        """Ordered-dither reveal: amount 0 = blank, 1 = full image."""
        if amount >= 1.0:
            return img
        px = img.load()
        lvl = amount * 16
        for y in range(img.height):
            row = BAYER4[y & 3]
            for x in range(img.width):
                if px[x, y] and row[x & 3] >= lvl:
                    px[x, y] = 0
        return img

    def image(self, t):
        img = self._Image.new("1", (DISPLAY_W, DISPLAY_H), 0)
        d = self._ImageDraw.Draw(img)
        big_y = (DISPLAY_H - self.h_big) // 2 - 4

        # --- phase 1 (0.0-1.3 s): device splash, dithers in
        # --- phase 2 (1.3-1.8 s): MASCHINE flies up, MK2 drops away
        out = _ease_in(_seg(t, 1.3, 1.8))
        if out < 1.0:
            self._text(d, ((DISPLAY_W - self.w_maschine) // 2, int(4 - 22 * out)), "MASCHINE", self.f_small)
            self._text(d, ((DISPLAY_W - self.w_mk2) // 2, int(49 + 22 * out)), "MK2", self.f_small)

        # --- phase 3 (1.7-2.2 s): MIKRO slides left to make room
        total = self.w_mikro + self.w_be
        x_start = (DISPLAY_W - self.w_mikro) // 2
        x_end = (DISPLAY_W - total) // 2
        x_mikro = int(x_start + (x_end - x_start) * _ease_out(_seg(t, 1.7, 2.2)))
        self._text(d, (x_mikro, big_y), "MIKRO", self.f_big, 1)

        # --- phase 4 (2.0-2.75 s): BE drops in from above with a bounce
        if t >= 2.0:
            y_be = int(-self.h_big - 4 + (big_y + self.h_big + 4) * _bounce(_seg(t, 2.0, 2.75)))
            self._text(d, (x_mikro + self.w_mikro, y_be), "BE", self.f_big, 1)

        # --- phase 5 (2.75-3.1 s): underline grows from the centre, flash
        if t >= 2.75:
            half = int((total // 2 + 2) * _ease_out(_seg(t, 2.75, 3.1)))
            uy = big_y + self.h_big + 4
            d.line((DISPLAY_W // 2 - half, uy, DISPLAY_W // 2 + half, uy), fill=1)

        # --- phase 6 (3.0-3.6 s): tagline types out, pad icon slides in, then starts "playing"
        if t >= 3.0:
            n = int(len(self.TAGLINE) * _seg(t, 3.0, 3.55))
            tag = self.TAGLINE[:n]
            group = self.w_tag + 6 + 12                 # tagline + gap + pad icon
            tx = (DISPLAY_W - group) // 2
            self._text(d, (tx, 52), tag, self.f_tag)
            home = tx + self.w_tag + 6 + 6                # icon centre when it settles
            ix = int(DISPLAY_W + 10 - (DISPLAY_W + 10 - home) * _ease_out(_seg(t, 3.1, 3.6)))
            self._pads_icon(d, ix, 56, t)

        # quick invert flash when BE lands
        if 2.75 <= t < 2.83:
            from PIL import ImageChops
            img = ImageChops.invert(img.convert("L")).convert("1")

        # fade in at the start, fade out at the end (ordered dither)
        if t < 0.6:
            img = self._dither(img, _seg(t, 0.0, 0.6))
        elif t > self.DURATION - 0.4:
            img = self._dither(img, 1.0 - _seg(t, self.DURATION - 0.4, self.DURATION))
        return img

    def pad_rgb(self, t, hw):
        """Pad light show: a ripple from the centre during the splash, then a sweep as BE lands."""
        import math
        row, col = divmod(hw, 4)
        dist = math.hypot(row - 1.5, col - 1.5)
        if t < 1.8:                                      # slow breathing ripple, cyan
            k = max(0.0, math.sin(t * 7 - dist * 2.2)) * min(1.0, t / 0.6)
            return (0, int(60 * k), int(110 * k))
        if t < 2.75:                                     # sweep left -> right, follows MIKRO/BE
            k = max(0.0, 1 - abs(col - (t - 1.8) / 0.95 * 5 + 0.5))
            return (int(40 * k), int(90 * k), int(127 * k))
        if t < 3.2:                                      # all flash white when BE lands, then decay
            k = 1 - _seg(t, 2.75, 3.2)
            return (int(127 * k), int(127 * k), int(127 * k))
        fade = 1 - _seg(t, self.DURATION - 0.4, self.DURATION)
        held = self.pressed_pad(t)
        if held is not None and row // 2 == held // 2 and col // 2 == held % 2:
            return (int(90 * fade), int(127 * fade), int(110 * fade))   # matching corner "hit"
        k = fade * min(1.0, _seg(t, 3.2, 3.6) + 0.15)
        return (0, int(28 * k), int(16 * k))                            # soft green base


# --------------------------------------------------------------------------
# Pad state machine
# --------------------------------------------------------------------------

IDLE, ATTACK, HELD = 0, 1, 2


class Pad(object):
    __slots__ = ("state", "t0", "peak", "pressure", "note", "channel", "last_at")

    def __init__(self):
        self.state = IDLE
        self.t0 = 0.0
        self.peak = 0
        self.pressure = 0
        self.note = None
        self.channel = None
        self.last_at = -1


# --------------------------------------------------------------------------
# The bridge
# --------------------------------------------------------------------------

class Bridge(object):
    def __init__(self, cfg, device_path=None, monitor=False):
        self.cfg = cfg
        self.monitor = monitor
        self.dev = MikroDevice(device_path)
        self.screen = Screen(cfg["font_size"], cfg["display_invert"])
        self.lock = threading.RLock()

        self.pads = [Pad() for _ in range(16)]
        self.buttons_raw = 0
        self.pressed = set()
        self.encoder_pos = None

        # state pushed from Live
        self.pad_mode = 0
        self.base_note = cfg["base_note"]
        self.button_levels = [0] * 32
        self.group_rgb = (40, 40, 40)
        self.session_pads = [((0, 0, 0), 0)] * 16
        self.last_heartbeat = 0.0
        self.live_lines = None
        self.playing_until = {}       # note -> time until which its pad stays lit (playback)
        self.playing_held = set()     # notes held on via MIDI note-on from a routed track

        self._last_leds = None
        self._last_frame = None
        self.splash = Splash() if cfg.get("splash", True) else None
        self.splash_start = None
        self._last_display_t = 0.0

        self.out = None
        self.inp = None

    # ---------------- MIDI ports ----------------

    def open_midi(self):
        try:
            import mido
        except ImportError:
            sys.exit("Missing dependency: pip install mido python-rtmidi")
        self.mido = mido

        def find(names, want):
            for n in names:
                if n.lower().startswith(want.lower()) or want.lower() in n.lower():
                    return n
            return None

        try:
            self.out = mido.open_output(PORT_TO_LIVE, virtual=True)
            self.inp = mido.open_input(PORT_FROM_LIVE, virtual=True, callback=self.on_midi)
            print("Created virtual MIDI ports '%s' and '%s'" % (PORT_TO_LIVE, PORT_FROM_LIVE))
        except (NotImplementedError, IOError, OSError):
            # Windows: no virtual ports -> use loopMIDI ports with the same names
            out_name = find(mido.get_output_names(), PORT_TO_LIVE)
            in_name = find(mido.get_input_names(), PORT_FROM_LIVE)
            if not out_name or not in_name:
                sys.exit(
                    "Could not create virtual MIDI ports.\n"
                    "On Windows install loopMIDI (tobias-erichsen.de) and create two ports named\n"
                    "  '%s'  and  '%s'\nthen run the bridge again." % (PORT_TO_LIVE, PORT_FROM_LIVE))
            self.out = mido.open_output(out_name)
            self.inp = mido.open_input(in_name, callback=self.on_midi)
            print("Using MIDI ports '%s' (out) and '%s' (in)" % (out_name, in_name))

    def send(self, msg_type, **kw):
        if self.monitor or self.out is None:
            return
        self.out.send(self.mido.Message(msg_type, **kw))

    def send_hello(self):
        if self.out is not None and not self.monitor:
            self.out.send(self.mido.Message("sysex", data=SYSEX_ID + [SX_HELLO]))

    # ---------------- MIDI from Live ----------------

    def on_midi(self, msg):
        if msg.type in ("note_on", "note_off"):
            # optional: a MIDI track routed to the Feedback port (sample-accurate playback lights)
            with self.lock:
                if msg.type == "note_on" and msg.velocity > 0:
                    self.playing_held.add(msg.note)
                    self.playing_until[msg.note] = time.time() + self.cfg["playback_flash_ms"] / 1000.0
                else:
                    self.playing_held.discard(msg.note)
            return
        if msg.type != "sysex":
            return
        d = list(msg.data)
        if d[:3] != SYSEX_ID or len(d) < 4:
            return
        cmd, p = d[3], d[4:]
        with self.lock:
            self.last_heartbeat = time.time()
            if self.live_lines is None:
                self.live_lines = ["", "", "", ""]
            if cmd == SX_LINE and p:
                if 0 <= p[0] < 4:
                    self.live_lines[p[0]] = "".join(chr(c) for c in p[1:] if c == 9 or 32 <= c < 127)
            elif cmd == SX_BAR and len(p) >= 2:
                self.screen.bar = (p[0], p[1])
            elif cmd == SX_PAD and len(p) >= 5 and p[0] < 16:
                self.session_pads[p[0]] = ((p[1], p[2], p[3]), p[4])
            elif cmd == SX_BUTTONS:
                for i, v in enumerate(p[:32]):
                    self.button_levels[i] = v
            elif cmd == SX_MODE and len(p) >= 2:
                if p[0] != self.pad_mode or p[1] != self.base_note:
                    self.release_all_pads()
                self.pad_mode, self.base_note = p[0], p[1]
            elif cmd == SX_GROUP and len(p) >= 3:
                self.group_rgb = (p[0], p[1], p[2])
            elif cmd == SX_PLAYING:
                until = time.time() + self.cfg["playback_flash_ms"] / 1000.0
                for note in p:
                    self.playing_until[note] = until

    # ---------------- HID input ----------------

    def handle_report(self, data, now):
        rid = data[0]
        if rid == REPORT_BUTTONS and len(data) >= 6:
            self.handle_buttons(data[1:5], data[5])
        elif rid == REPORT_PADS and len(data) >= 33:
            for i in range(16):
                lo, hi = data[1 + 2 * i], data[2 + 2 * i]
                self.handle_pad(hi >> 4, ((hi & 0x0F) << 8) | lo, now)

    def handle_buttons(self, b, enc):
        raw = b[0] | (b[1] << 8) | (b[2] << 16) | (b[3] << 24)
        changed = raw ^ self.buttons_raw
        self.buttons_raw = raw
        for bit in range(32):
            if not changed & (1 << bit):
                continue
            down = bool(raw & (1 << bit))
            name = BUTTON_BITS.get(bit, "unknown_%d" % bit)
            if down:
                self.pressed.add(name)
            else:
                self.pressed.discard(name)
            if self.monitor:
                print("button %-16s bit %2d  %s" % (name, bit, "DOWN" if down else "up"))
            self.send("control_change", channel=CH_CONTROL,
                      control=CC_BUTTON_BASE + bit, value=127 if down else 0)

        enc &= 0x0F
        if self.encoder_pos is None:
            self.encoder_pos = enc
        elif enc != self.encoder_pos:
            delta = (enc - self.encoder_pos) & 0x0F
            if delta > 8:
                delta -= 16
            self.encoder_pos = enc
            if self.cfg["encoder_invert"]:
                delta = -delta
            if self.monitor:
                print("encoder %+d  (pos %d)" % (delta, enc))
            delta = max(-63, min(63, delta))
            self.send("control_change", channel=CH_CONTROL, control=CC_ENCODER, value=delta & 0x7F)

    def velocity(self, peak):
        cfg = self.cfg
        if cfg["fixed_velocity"]:
            return max(1, min(127, int(cfg["fixed_velocity"])))
        span = max(1.0, float(cfg["pad_full_scale"] - cfg["pad_on_threshold"]))
        x = max(0.0, min(1.0, (peak - cfg["pad_on_threshold"]) / span))
        return max(1, min(127, int(round(1 + 126 * (x ** cfg["velocity_curve"])))))

    def handle_pad(self, hw, value, now):
        if hw > 15:
            return
        p = self.pads[hw]
        p.pressure = value
        on_t, off_t = self.cfg["pad_on_threshold"], self.cfg["pad_off_threshold"]
        if p.state == IDLE:
            if value >= on_t:
                p.state, p.t0, p.peak = ATTACK, now, value
                if self.cfg["velocity_attack_ms"] <= 0:
                    self.pad_on(hw)
        elif p.state == ATTACK:
            p.peak = max(p.peak, value)
            if value < off_t:
                self.pad_on(hw)
                self.pad_off(hw)
            elif (now - p.t0) * 1000.0 >= self.cfg["velocity_attack_ms"]:
                self.pad_on(hw)
        elif p.state == HELD:
            if value < off_t:
                self.pad_off(hw)
            else:
                self.pad_aftertouch(hw, value)

    def pad_on(self, hw):
        p = self.pads[hw]
        n = hw_to_padnum(hw)
        vel = self.velocity(p.peak)
        if self.pad_mode == 0:
            p.channel, p.note = CH_NOTES, max(0, min(127, self.base_note + n))
        else:
            p.channel, p.note = CH_SESSION, n
        p.state, p.last_at = HELD, -1
        if self.monitor:
            print("pad %2d (hw %2d) ON  peak %4d -> velocity %3d" % (n + 1, hw, p.peak, vel))
        self.send("note_on", channel=p.channel, note=p.note, velocity=vel)

    def pad_off(self, hw):
        p = self.pads[hw]
        if p.state == HELD and p.note is not None:
            if self.monitor:
                print("pad %2d (hw %2d) off" % (hw_to_padnum(hw) + 1, hw))
            self.send("note_off", channel=p.channel, note=p.note, velocity=0)
            if self.cfg["aftertouch"] == "channel" and p.channel == CH_NOTES:
                self.send("aftertouch", channel=CH_NOTES, value=0)
        p.state, p.note, p.peak = IDLE, None, 0

    def pad_aftertouch(self, hw, value):
        mode = self.cfg["aftertouch"]
        p = self.pads[hw]
        if mode == "off" or p.channel != CH_NOTES:
            return
        at = max(0, min(127, value >> 5))
        if abs(at - p.last_at) < 2:
            return
        p.last_at = at
        if mode == "poly":
            self.send("polytouch", channel=CH_NOTES, note=p.note, value=at)
        else:
            self.send("aftertouch", channel=CH_NOTES, value=at)

    def release_all_pads(self):
        for hw in range(16):
            if self.pads[hw].state == HELD:
                self.pad_off(hw)
            self.pads[hw].state = IDLE

    def check_attack_timeouts(self, now):
        for hw, p in enumerate(self.pads):
            if p.state == ATTACK and (now - p.t0) * 1000.0 >= self.cfg["velocity_attack_ms"]:
                self.pad_on(hw)

    # ---------------- LED + screen output ----------------

    def live_connected(self, now):
        return self.live_lines is not None and (now - self.last_heartbeat) < 3.0

    def compose_leds(self, now):
        t = self.splashing(now)
        if t is not None:
            leds = [0] * LED_COUNT
            for hw in range(16):
                off = LED_PADS_RGB + 3 * hw
                leds[off:off + 3] = list(self.splash.pad_rgb(t, hw))
            if t > 2.75:
                leds[LED_GROUP_RGB:LED_GROUP_RGB + 3] = [20, 110, 60]
            return leds
        cfg = self.cfg
        leds = [0] * LED_COUNT
        connected = self.live_connected(now)
        level_map = {0: 0, 1: cfg["led_dim"], 2: cfg["led_on"]}
        for name, off in BUTTON_LED.items():
            lvl = self.button_levels[BUTTON_INDEX[name]] if connected else 0
            leds[off] = level_map.get(lvl, 0)
            if name in self.pressed:
                leds[off] = cfg["led_on"]
        if not connected:
            leds[BUTTON_LED["pad_mode"]] = cfg["led_on"] if self.pad_mode else cfg["led_dim"]

        gr, gg, gb = self.group_rgb if connected else (30, 30, 30)
        leds[LED_GROUP_RGB:LED_GROUP_RGB + 3] = [gr, gg, gb]

        blink_on = int(now * 4) % 2 == 0
        pulse = 0.35 + 0.65 * abs(((now * 2) % 2) - 1)
        for hw in range(16):
            n = hw_to_padnum(hw)
            held = self.pads[hw].state != IDLE
            if self.pad_mode == 0 or not connected:
                base = (gr, gg, gb) if any((gr, gg, gb)) else (60, 60, 60)
                note = self.base_note + n
                playing = note in self.playing_held or self.playing_until.get(note, 0) > now
                if held:
                    rgb = full_bright(base, whiten=0.35)
                elif playing:
                    rgb = full_bright(base)
                else:
                    k = cfg["pad_note_mode_dim"]
                    rgb = tuple(int(c * k) for c in base)
            else:
                (r, g, b), anim = self.session_pads[n]
                if anim == 1 and not blink_on:
                    r, g, b = r // 6, g // 6, b // 6
                elif anim == 2:
                    r, g, b = int(r * pulse), int(g * pulse), int(b * pulse)
                rgb = (r, g, b)
                if held:
                    rgb = (110, 110, 110)
            off = LED_PADS_RGB + 3 * hw
            leds[off:off + 3] = list(rgb)
        return leds

    def splashing(self, now):
        if self.splash_start is None:
            return None
        t = now - self.splash_start
        if t >= Splash.DURATION:
            self.splash_start = None
            return None
        return t

    def compose_screen(self, now):
        t = self.splashing(now)
        if t is not None:
            return Screen.image_to_pages(self.splash.image(t), self.screen.invert)
        if self.live_connected(now):
            self.screen.lines = list(self.live_lines)
        else:
            mode = ("NOTE %s" % note_name(self.base_note)) if self.pad_mode == 0 else "CLIPS"
            self.screen.lines = [
                "MIKROBE",
                "Waiting for Live...",
                "Pads\t%s" % mode,
                "Surface: Mikrobe",
            ]
            self.screen.bar = (0, 0)
        return self.screen.render()

    def flush_output(self, now):
        with self.lock:
            leds = self.compose_leds(now)
            if leds != self._last_leds:
                self.dev.write_leds(leds)
                self._last_leds = leds
            if now - self._last_display_t >= 1.0 / max(1, self.cfg["display_fps"]):
                self._last_display_t = now
                frame = self.compose_screen(now)
                if frame != self._last_frame:
                    self.dev.write_display(frame)
                    self._last_frame = frame

    # ---------------- main loop ----------------

    def run(self):
        if not self.monitor:
            self.open_midi()
        while True:
            try:
                self.dev.open()
            except (IOError, OSError) as e:
                print("Waiting for Maschine Mikro MK2... (%s)" % e)
                time.sleep(2.0)
                continue
            print("Maschine Mikro MK2 connected.")
            self._last_leds = self._last_frame = None
            if self.splash is not None:
                self.splash_start = time.time()
            self.send_hello()
            try:
                self.loop()
            except (IOError, OSError, ValueError) as e:
                print("Device error: %s - reconnecting" % e)
                with self.lock:
                    self.release_all_pads()
                self.dev.close()
                time.sleep(1.0)

    def loop(self):
        last_out = 0.0
        while True:
            data = self.dev.read(timeout_ms=4)
            now = time.time()
            if data:
                with self.lock:
                    self.handle_report(data, now)
            with self.lock:
                self.check_attack_timeouts(now)
            if now - last_out >= 1.0 / 60:
                last_out = now
                self.flush_output(now)


# --------------------------------------------------------------------------
# Test helpers
# --------------------------------------------------------------------------

def test_display(dev, cfg):
    from PIL import ImageDraw
    scr = Screen(cfg["font_size"], cfg["display_invert"])
    scr.lines = ["DISPLAY TEST\tOK", "Top-left is here", "Arrow -> right edge", "Bottom line"]
    scr.bar = (1, 64)
    img = scr.render_image()
    d = ImageDraw.Draw(img)
    d.rectangle((0, 0, 127, 63), outline=1)
    dev.write_display(Screen.image_to_pages(img, cfg["display_invert"]))
    print("If the text reads correctly, the screen works. Ctrl+C to quit.")
    while True:
        time.sleep(1)


def test_splash(dev, cfg):
    """Loop the boot animation on the screen and pads."""
    splash = Splash()
    print("Playing the boot animation in a loop. Ctrl+C to quit.")
    while True:
        t0 = time.time()
        while True:
            t = time.time() - t0
            if t >= Splash.DURATION + 0.6:
                break
            if t < Splash.DURATION:
                dev.write_display(Screen.image_to_pages(splash.image(t), cfg["display_invert"]))
                leds = [0] * LED_COUNT
                for hw in range(16):
                    leds[LED_PADS_RGB + 3 * hw:LED_PADS_RGB + 3 * hw + 3] = list(splash.pad_rgb(t, hw))
                dev.write_leds(leds)
            else:
                dev.write_display(bytes(1024))
                dev.write_leds([0] * LED_COUNT)
            time.sleep(1.0 / 30)


def test_leds(dev):
    names = dict((v, k) for k, v in BUTTON_LED.items())
    for off in range(LED_COUNT):
        leds = [0] * LED_COUNT
        leds[off] = 127
        if off >= LED_PADS_RGB:
            label = "pad hw %d %s" % ((off - LED_PADS_RGB) // 3, "RGB"[(off - LED_PADS_RGB) % 3])
        elif LED_GROUP_RGB <= off < LED_GROUP_RGB + 3:
            label = "group %s" % "RGB"[off - LED_GROUP_RGB]
        else:
            label = names.get(off, "?")
        print("LED %2d  %s" % (off, label))
        dev.write_leds(leds)
        time.sleep(0.25)
    dev.write_leds([0] * LED_COUNT)


def main():
    here = os.path.dirname(os.path.abspath(__file__))
    ap = argparse.ArgumentParser(description="Mikrobe: Maschine Mikro MK2 <-> Ableton Live bridge")
    ap.add_argument("--config", default=os.path.join(here, "config.json"))
    ap.add_argument("--monitor", action="store_true", help="print hardware events, no MIDI")
    ap.add_argument("--test", choices=["display", "leds", "splash"])
    ap.add_argument("--list", action="store_true", help="list NI HID devices")
    ap.add_argument("--path", help="explicit HID device path")
    ap.add_argument("--version", action="version", version="%(prog)s " + __version__)
    args = ap.parse_args()

    cfg = load_config(args.config)

    if args.list:
        for d in MikroDevice.enumerate_devices():
            print("%04x:%04x  %-30s  interface %s  path %r" % (
                d["vendor_id"], d["product_id"], d.get("product_string"),
                d.get("interface_number"), d["path"]))
        return

    if args.test:
        dev = MikroDevice(args.path).open()
        try:
            {"display": test_display, "leds": lambda d, c: test_leds(d),
             "splash": test_splash}[args.test](dev, cfg)
        except KeyboardInterrupt:
            pass
        finally:
            dev.close()
        return

    bridge = Bridge(cfg, args.path, monitor=args.monitor)
    try:
        bridge.run()
    except KeyboardInterrupt:
        print("\nbye")
        try:
            bridge.release_all_pads()
            bridge.dev.write_leds([0] * LED_COUNT)
            bridge.dev.write_display(bytes(1024))
        except Exception:
            pass
        bridge.dev.close()


if __name__ == "__main__":
    main()
