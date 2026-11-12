"""
Maschine Mikro MK2 control surface for Ableton Live 10 / 11 / 12.

Receives buttons, encoder and clip-mode pads from mikrobe.py on the
"Mikrobe Controls" port, and sends LED states, pad colours and the text for the
128x64 screen back on the "Mikrobe Feedback" port as SysEx.
"""
from __future__ import absolute_import, print_function, unicode_literals

import Live
from _Framework.ControlSurface import ControlSurface
from _Framework.ButtonElement import ButtonElement
from _Framework.EncoderElement import EncoderElement
from _Framework.InputControlElement import MIDI_CC_TYPE, MIDI_NOTE_TYPE

# ---- protocol shared with mikrobe.py (keep in sync) -------------------

CH_SESSION = 1
CH_CONTROL = 15
CC_ENCODER = 10
CC_BUTTON_BASE = 20

SYSEX_HEAD = (0xF0, 0x7D, 0x4D, 0x4B)
SX_LINE, SX_BAR, SX_PAD, SX_BUTTONS, SX_MODE, SX_GROUP = 1, 2, 3, 4, 5, 6
SX_HEARTBEAT, SX_HELLO = 0x7E, 0x7F

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

PAD_NOTE, PAD_CLIP = 0, 1
OFF, DIM, ON = 0, 1, 2

MODE_TITLES = {
    "volume": "VOLUME", "pan": "PAN", "send": "SEND", "device": "DEVICE",
    "tempo": "TEMPO", "track": "TRACK",
}
MODE_BUTTONS = {
    "f1": "volume", "f2": "pan", "f3": "send",
    "control": "device", "nav": "tempo", "main": "track",
}

NOTE_NAMES = ["C", "C#", "D", "D#", "E", "F", "F#", "G", "G#", "A", "A#", "B"]
PREFIX = "Mikro_"


def note_name(n):
    return "%s%d" % (NOTE_NAMES[n % 12], n // 12 - 2)


def to_ascii(text, limit=40):
    try:
        text = "%s" % (text,)
    except Exception:
        text = "?"
    out = []
    for ch in text:
        o = ord(ch)
        out.append(o if (32 <= o < 127 or o == 9) else 63)
    return out[:limit]


def rgb7(color):
    try:
        c = int(color)
    except Exception:
        return [40, 40, 40]
    return [((c >> 16) & 0xFF) >> 1, ((c >> 8) & 0xFF) >> 1, (c & 0xFF) >> 1]


def clamp(v, lo, hi):
    return lo if v < lo else hi if v > hi else v


class Mikrobe(ControlSurface):

    def __init__(self, c_instance):
        ControlSurface.__init__(self, c_instance)
        self._mk_shift = False
        self._mk_enc_mode = "volume"
        self._mk_send_index = 0
        self._mk_param_index = 1
        self._mk_pad_mode = PAD_NOTE
        self._mk_base_note = 36
        self._mk_flash = None
        self._mk_flash_until = 0
        self._mk_ticks = 0
        self._mk_cache = {}
        self._mk_highlight = None
        self._mk_last_track_off = 0
        with self.component_guard():
            self._mk_setup_controls()
        self.show_message("Mikrobe ready")
        self.log_message("Mikrobe: loaded")

    # ------------------------------------------------------------------ setup

    def _mk_setup_controls(self):
        self._mk_controls = []
        for bit, name in BUTTON_BITS.items():
            b = ButtonElement(True, MIDI_CC_TYPE, CH_CONTROL, CC_BUTTON_BASE + bit,
                              name=PREFIX + name)
            b.add_value_listener(self._mk_on_button, identify_sender=True)
            self._mk_controls.append(b)
        enc = EncoderElement(MIDI_CC_TYPE, CH_CONTROL, CC_ENCODER,
                             Live.MidiMap.MapMode.relative_two_compliment,
                             name=PREFIX + "encoder")
        enc.add_value_listener(self._mk_on_encoder)
        self._mk_controls.append(enc)
        for n in range(16):
            p = ButtonElement(True, MIDI_NOTE_TYPE, CH_SESSION, n, name="%spad_%d" % (PREFIX, n))
            p.add_value_listener(self._mk_on_pad, identify_sender=True)
            self._mk_controls.append(p)

    def disconnect(self):
        try:
            self._mk_send([SX_LINE, 0] + to_ascii("MIKROBE"))
            self._mk_send([SX_LINE, 1] + to_ascii("Live disconnected"))
        except Exception:
            pass
        ControlSurface.disconnect(self)

    # ------------------------------------------------------------------ helpers

    def _mk_send(self, payload):
        self._send_midi(SYSEX_HEAD + tuple(int(x) & 0x7F for x in payload) + (0xF7,))

    def _mk_send_cached(self, key, payload):
        if self._mk_cache.get(key) != payload:
            self._mk_cache[key] = payload
            self._mk_send(payload)

    def _mk_flash_msg(self, text, ticks=15):
        self._mk_flash = text
        self._mk_flash_until = self._mk_ticks + ticks

    def _mk_app(self):
        return Live.Application.get_application()

    def _mk_all_tracks(self):
        s = self.song()
        return list(s.visible_tracks) + list(s.return_tracks) + [s.master_track]

    def _mk_index(self, seq, item):
        for i, x in enumerate(seq):
            if x == item:
                return i
        return -1

    def _mk_device(self):
        t = self.song().view.selected_track
        d = t.view.selected_device
        if d is None and len(t.devices):
            d = t.devices[0]
        return d

    def _mk_target_param(self):
        t = self.song().view.selected_track
        mode = self._mk_enc_mode
        md = t.mixer_device
        if mode == "volume":
            return md.volume
        if mode == "pan":
            return md.panning
        if mode == "send":
            sends = list(md.sends)
            if not sends:
                return None
            self._mk_send_index = self._mk_send_index % len(sends)
            return sends[self._mk_send_index]
        if mode == "device":
            d = self._mk_device()
            if d is None:
                return None
            params = list(d.parameters)
            if not params:
                return None
            self._mk_param_index = clamp(self._mk_param_index, 0, len(params) - 1)
            return params[self._mk_param_index]
        return None

    def _mk_grid_offsets(self):
        s = self.song()
        tracks = list(s.visible_tracks)
        idx = self._mk_index(tracks, s.view.selected_track)
        if idx >= 0:
            self._mk_last_track_off = (idx // 4) * 4
        scenes = list(s.scenes)
        sidx = max(0, self._mk_index(scenes, s.view.selected_scene))
        return self._mk_last_track_off, (sidx // 4) * 4

    def _mk_slot_for_pad(self, n):
        """n: 0 = bottom-left pad. Top row = first scene of the 4x4 block."""
        s = self.song()
        t_off, s_off = self._mk_grid_offsets()
        row_from_bottom, col = divmod(n, 4)
        ti, si = t_off + col, s_off + (3 - row_from_bottom)
        tracks = list(s.visible_tracks)
        if ti >= len(tracks):
            return None, None
        track = tracks[ti]
        slots = list(track.clip_slots)
        if si >= len(slots):
            return None, track
        return slots[si], track

    # ------------------------------------------------------------------ input

    def _mk_on_button(self, value, sender):
        name = sender.name[len(PREFIX):]
        pressed = value > 0
        if name == "shift":
            self._mk_shift = pressed
            return
        if not pressed:
            return
        handler = getattr(self, "_mk_btn_" + name, None)
        if handler is not None:
            try:
                handler()
            except Exception as e:
                self.log_message("Mikrobe: %s failed: %s" % (name, e))

    def _mk_on_encoder(self, value):
        delta = value - 128 if value >= 64 else value
        if not delta:
            return
        s = self.song()
        mode = self._mk_enc_mode
        try:
            if mode == "tempo":
                step = 0.1 if self._mk_shift else 1.0
                s.tempo = clamp(s.tempo + delta * step, 20.0, 999.0)
            elif mode == "track":
                self._mk_move_track(delta)
            else:
                self._mk_adjust(self._mk_target_param(), delta)
        except Exception as e:
            self.log_message("Mikrobe: encoder failed: %s" % e)

    def _mk_adjust(self, p, delta):
        if p is None or not p.is_enabled:
            return
        if p.is_quantized:
            p.value = clamp(p.value + (1 if delta > 0 else -1), p.min, p.max)
        else:
            step = (p.max - p.min) / (800.0 if self._mk_shift else 80.0)
            p.value = clamp(p.value + delta * step, p.min, p.max)

    def _mk_on_pad(self, value, sender):
        if not value:
            return
        n = int(sender.name.rsplit("_", 1)[1])
        slot, track = self._mk_slot_for_pad(n)
        if slot is None:
            return
        s = self.song()
        if self._mk_shift:
            s.view.selected_track = track
            s.view.highlighted_clip_slot = slot
        else:
            slot.fire()

    def handle_sysex(self, midi_bytes):
        if tuple(midi_bytes[:4]) == SYSEX_HEAD and len(midi_bytes) > 5 and midi_bytes[4] == SX_HELLO:
            self._mk_cache = {}
            self.log_message("Mikrobe: bridge (re)connected")

    # ------------------------------------------------------------------ buttons

    def _mk_btn_play(self):
        s = self.song()
        if self._mk_shift:
            s.continue_playing()
        elif s.is_playing:
            s.stop_playing()
        else:
            s.start_playing()

    def _mk_btn_restart(self):
        s = self.song()
        if self._mk_shift:
            s.loop = not s.loop
            self._mk_flash_msg("Loop %s" % ("ON" if s.loop else "OFF"))
        else:
            s.current_song_time = 0.0
            if not s.is_playing:
                s.start_playing()

    def _mk_btn_rec(self):
        s = self.song()
        if self._mk_shift:
            s.record_mode = not s.record_mode
            self._mk_flash_msg("Arrangement rec %s" % ("ON" if s.record_mode else "OFF"))
        else:
            s.session_record = not s.session_record

    def _mk_btn_erase(self):
        s = self.song()
        if self._mk_shift:
            if s.can_redo:
                s.redo()
                self._mk_flash_msg("Redo")
        elif s.can_undo:
            s.undo()
            self._mk_flash_msg("Undo")

    def _mk_btn_grid(self):
        s = self.song()
        s.metronome = not s.metronome
        self._mk_flash_msg("Metronome %s" % ("ON" if s.metronome else "OFF"))

    def _mk_btn_note_repeat(self):
        s = self.song()
        s.tap_tempo()
        self._mk_flash_msg("Tap  %.1f BPM" % s.tempo)

    def _mk_btn_sampling(self):
        s = self.song()
        if self._mk_shift:
            s.session_automation_record = not s.session_automation_record
            self._mk_flash_msg("Automation %s" % ("ON" if s.session_automation_record else "OFF"))
        elif getattr(s, "can_capture_midi", False):
            s.capture_midi()
            self._mk_flash_msg("Captured MIDI")
        else:
            self._mk_flash_msg("Nothing to capture")

    def _mk_btn_transport_left(self):
        self._mk_transport_step(-1)

    def _mk_btn_transport_right(self):
        self._mk_transport_step(1)

    def _mk_transport_step(self, direction):
        if self._mk_shift and self._mk_pad_mode == PAD_NOTE:
            self._mk_base_note = clamp(self._mk_base_note + 16 * direction, 0, 112)
            self._mk_flash_msg("Pads %s-%s" % (note_name(self._mk_base_note),
                                               note_name(self._mk_base_note + 15)))
            return
        s = self.song()
        scenes = list(s.scenes)
        idx = max(0, self._mk_index(scenes, s.view.selected_scene))
        step = 4 if self._mk_shift else 1
        s.view.selected_scene = scenes[clamp(idx + direction * step, 0, len(scenes) - 1)]

    def _mk_btn_arrow_left(self):
        self._mk_arrow(-1)

    def _mk_btn_arrow_right(self):
        self._mk_arrow(1)

    def _mk_arrow(self, direction):
        if self._mk_enc_mode == "device":
            if self._mk_shift:
                self._mk_move_device(direction)
            else:
                d = self._mk_device()
                if d is not None:
                    self._mk_param_index = clamp(self._mk_param_index + direction,
                                                 0, len(d.parameters) - 1)
        else:
            self._mk_move_track(direction * (4 if self._mk_shift else 1))

    def _mk_move_track(self, delta):
        s = self.song()
        tracks = self._mk_all_tracks()
        idx = self._mk_index(tracks, s.view.selected_track)
        s.view.selected_track = tracks[clamp(idx + delta, 0, len(tracks) - 1)]

    def _mk_move_device(self, delta):
        s = self.song()
        t = s.view.selected_track
        devs = list(t.devices)
        if not devs:
            return
        idx = max(0, self._mk_index(devs, self._mk_device()))
        s.view.select_device(devs[clamp(idx + delta, 0, len(devs) - 1)])
        self._mk_param_index = 1

    def _mk_btn_browse(self):
        v = self._mk_app().view
        if v.is_view_visible("Browser"):
            v.hide_view("Browser")
        else:
            v.show_view("Browser")

    def _mk_btn_group(self):
        s = self.song()
        if self._mk_shift:
            s.view.selected_track.stop_all_clips()
        else:
            slot = s.view.highlighted_clip_slot
            if slot is not None:
                slot.fire()

    def _mk_set_mode(self, mode):
        if mode == "send" and self._mk_enc_mode == "send":
            self._mk_send_index += 1
        self._mk_enc_mode = mode

    def _mk_btn_f1(self):
        self._mk_set_mode("volume")

    def _mk_btn_f2(self):
        self._mk_set_mode("pan")

    def _mk_btn_f3(self):
        self._mk_set_mode("send")

    def _mk_btn_control(self):
        self._mk_set_mode("device")

    def _mk_btn_nav(self):
        self._mk_set_mode("tempo")

    def _mk_btn_main(self):
        self._mk_set_mode("track")

    def _mk_btn_encoder_push(self):
        s = self.song()
        mode = self._mk_enc_mode
        if mode == "tempo":
            s.tap_tempo()
        elif mode == "track":
            slot = s.view.highlighted_clip_slot
            if slot is not None:
                slot.fire()
        else:
            p = self._mk_target_param()
            if p is not None and p.is_enabled and not p.is_quantized:
                p.value = p.default_value
                self._mk_flash_msg("Reset %s" % p.name)

    def _mk_btn_scene(self):
        s = self.song()
        scenes = list(s.scenes)
        idx = max(0, self._mk_index(scenes, s.view.selected_scene))
        if self._mk_shift:
            s.create_scene(idx + 1)
            self._mk_flash_msg("New scene")
        else:
            scenes[idx].fire()

    def _mk_btn_pattern(self):
        self.song().stop_all_clips()
        self._mk_flash_msg("Stop all clips")

    def _mk_btn_pad_mode(self):
        if self._mk_shift:
            self._mk_base_note = 36
            self._mk_flash_msg("Pads reset to C1")
            return
        self._mk_pad_mode = PAD_CLIP if self._mk_pad_mode == PAD_NOTE else PAD_NOTE
        self._mk_flash_msg("Pads: %s" % ("CLIP LAUNCH" if self._mk_pad_mode else "NOTES"))

    def _mk_btn_view(self):
        v = self._mk_app().view
        if v.is_view_visible("Session"):
            v.show_view("Arranger")
        else:
            v.show_view("Session")

    def _mk_btn_duplicate(self):
        s = self.song()
        scenes = list(s.scenes)
        idx = max(0, self._mk_index(scenes, s.view.selected_scene))
        if self._mk_shift:
            s.duplicate_scene(idx)
            self._mk_flash_msg("Scene duplicated")
            return
        t = s.view.selected_track
        slots = list(getattr(t, "clip_slots", []))
        if idx < len(slots) and slots[idx].has_clip:
            t.duplicate_clip_slot(idx)
            self._mk_flash_msg("Clip duplicated")

    def _mk_btn_select(self):
        t = self.song().view.selected_track
        if t.can_be_armed:
            t.arm = not t.arm

    def _mk_btn_solo(self):
        t = self.song().view.selected_track
        if t != self.song().master_track:
            t.solo = not t.solo

    def _mk_btn_mute(self):
        t = self.song().view.selected_track
        if t != self.song().master_track:
            t.mute = not t.mute

    # ------------------------------------------------------------------ output

    def update_display(self):
        ControlSurface.update_display(self)
        self._mk_ticks += 1
        try:
            self._mk_sync()
        except Exception as e:
            if self._mk_ticks % 50 == 0:
                self.log_message("Mikrobe: sync error: %s" % e)

    def _mk_sync(self):
        s = self.song()
        track = s.view.selected_track
        if self._mk_ticks % 10 == 0:
            self._mk_send([SX_HEARTBEAT])

        self._mk_send_cached("mode", [SX_MODE, self._mk_pad_mode, self._mk_base_note])
        try:
            color = track.color
        except Exception:
            color = 0x505050
        self._mk_send_cached("group", [SX_GROUP] + rgb7(color))

        lines, bar = self._mk_screen()
        for i, text in enumerate(lines):
            self._mk_send_cached("line%d" % i, [SX_LINE, i] + to_ascii(text))
        self._mk_send_cached("bar", [SX_BAR, bar[0], bar[1]])
        self._mk_send_cached("buttons", [SX_BUTTONS] + self._mk_button_levels())

        if self._mk_pad_mode == PAD_CLIP:
            for n in range(16):
                self._mk_send_cached("pad%d" % n, [SX_PAD, n] + self._mk_pad_state(n))
            offsets = self._mk_grid_offsets()
            if offsets != self._mk_highlight:
                self._mk_highlight = offsets
                try:
                    self._c_instance.set_session_highlight(offsets[0], offsets[1], 4, 4, False)
                except Exception:
                    pass

    def _mk_screen(self):
        s = self.song()
        track = s.view.selected_track
        mode = self._mk_enc_mode
        pads = "CLIPS" if self._mk_pad_mode == PAD_CLIP else note_name(self._mk_base_note)
        title = MODE_TITLES[mode]
        line1 = track.name
        line2 = ""
        bar = (0, 0)

        if mode == "tempo":
            line2 = "Tempo\t%.2f" % s.tempo
            bar = (1, int(clamp((s.tempo - 60.0) / 140.0, 0.0, 1.0) * 127))
        elif mode == "track":
            slot = s.view.highlighted_clip_slot
            clip = slot.clip if (slot is not None and slot.has_clip) else None
            line2 = "%s\t%s" % (s.view.selected_scene.name or "Scene",
                                clip.name if clip else "-")
        else:
            if mode == "send":
                title = "SEND %s" % chr(65 + self._mk_send_index % 26)
            if mode == "device":
                d = self._mk_device()
                line1 = d.name if d is not None else "%s: no device" % track.name
                if d is not None:
                    title = "DEVICE %d/%d" % (self._mk_param_index + 1, len(d.parameters))
            p = self._mk_target_param()
            if p is not None:
                try:
                    val = p.str_for_value(p.value)
                except Exception:
                    val = "%.2f" % p.value
                line2 = "%s\t%s" % (p.name, val)
                rng = (p.max - p.min) or 1.0
                pos = int(clamp((p.value - p.min) / rng, 0.0, 1.0) * 127)
                bipolar = p.min < 0 and abs(p.min + p.max) < 1e-6
                bar = (2 if bipolar else 1, pos)
            elif mode == "send":
                line2 = "No return tracks"

        if self._mk_flash and self._mk_ticks < self._mk_flash_until:
            line2 = self._mk_flash
        else:
            self._mk_flash = None

        return ["%s\t%s" % (title, pads), line1, line2, self._mk_transport_text()], bar

    def _mk_transport_text(self):
        s = self.song()
        rec = s.session_record or s.record_mode
        try:
            t = s.get_current_beats_song_time()
            pos = "%d.%d.%d" % (t.bars, t.beats, t.sub_division)
        except Exception:
            pos = ""
        state = "REC" if (rec and s.is_playing) else (">" if s.is_playing else "||")
        return "%.1f BPM\t%s %s" % (s.tempo, pos, state)

    def _mk_button_levels(self):
        s = self.song()
        app_view = self._mk_app().view
        t = s.view.selected_track
        is_master = t == s.master_track
        lv = [OFF] * 32

        def put(name, on, available=True):
            lv[BUTTON_INDEX[name]] = ON if on else (DIM if available else OFF)

        put("shift", self._mk_shift)
        put("play", s.is_playing)
        put("rec", s.session_record or s.record_mode)
        put("restart", s.loop)
        put("grid", s.metronome)
        put("note_repeat", False)
        put("sampling", False, getattr(s, "can_capture_midi", False))
        put("erase", False, s.can_undo)
        put("transport_left", False)
        put("transport_right", False)
        put("arrow_left", False)
        put("arrow_right", False)
        for btn, mode in MODE_BUTTONS.items():
            put(btn, self._mk_enc_mode == mode)
        put("browse", app_view.is_view_visible("Browser"))
        put("scene", False)
        put("pattern", False)
        put("pad_mode", self._mk_pad_mode == PAD_CLIP)
        put("view", not app_view.is_view_visible("Session"))
        put("duplicate", False)
        put("select", t.can_be_armed and t.arm, t.can_be_armed)
        put("solo", (not is_master) and t.solo, not is_master)
        put("mute", (not is_master) and t.mute, not is_master)
        return lv

    def _mk_pad_state(self, n):
        slot, track = self._mk_slot_for_pad(n)
        if slot is None:
            return [0, 0, 0, 0]
        if slot.has_clip:
            clip = slot.clip
            rgb = rgb7(clip.color)
            if clip.is_recording:
                return [127, 0, 0, 2]
            if clip.is_triggered:
                return rgb + [1]
            if clip.is_playing:
                return rgb + [0]
            return [int(c * 0.22) for c in rgb] + [0]
        if slot.is_triggered:
            return [50, 50, 50, 1]
        if track is not None and track.can_be_armed and track.arm:
            return [25, 0, 0, 0]
        return [0, 0, 0, 0]
