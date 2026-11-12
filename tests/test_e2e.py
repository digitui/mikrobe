"""End-to-end simulation: fake HID device + fake Ableton Live API, real bridge + real script."""
"""Run with:  python tests/test_e2e.py   (needs pillow + mido; no hardware, no Live)"""
import sys, types, os, time, tempfile
from contextlib import contextmanager

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OUT = os.environ.get("MIKROBE_TEST_OUT") or os.path.join(tempfile.gettempdir(), "mikrobe_test_out")
sys.path.insert(0, ROOT + "/bridge")

# ------------------------------------------------------------------ fake Live
Live = types.ModuleType("Live")
class _MapMode: relative_two_compliment = 4
Live.MidiMap = types.SimpleNamespace(MapMode=_MapMode)

class AppView:
    def __init__(self): self.visible = {"Session"}
    def is_view_visible(self, v): return v in self.visible
    def show_view(self, v):
        if v in ("Session", "Arranger"): self.visible -= {"Session", "Arranger"}
        self.visible.add(v)
    def hide_view(self, v): self.visible.discard(v)
APP = types.SimpleNamespace(view=AppView())
Live.Application = types.SimpleNamespace(get_application=lambda: APP)
sys.modules["Live"] = Live

class Param:
    def __init__(self, name, mn, mx, val, q=False, default=None):
        self.name, self.min, self.max, self.value = name, mn, mx, val
        self.is_quantized, self.is_enabled = q, True
        self.default_value = val if default is None else default
    def str_for_value(self, v):
        if self.name == "Track Volume": return "%.1f dB" % ((v - 0.85) * 40)
        if self.name == "Track Panning": return "C" if abs(v) < 0.01 else ("%dL" % (-v*50) if v < 0 else "%dR" % (v*50))
        return "%.2f" % v

class Note:
    def __init__(self, pitch, start, dur): self.pitch, self.start_time, self.duration, self.mute = pitch, start, dur, False

class Clip:
    def __init__(self, name, color, playing=False, notes=()):
        self.name, self.color, self.is_playing = name, color, playing
        self.is_triggered = self.is_recording = False
        self.is_midi_clip, self.looping = True, True
        self.loop_start = self.start_marker = 0.0
        self.loop_end = self.end_marker = 4.0
        self.playing_position = 0.0
        self.notes = [Note(*n) for n in notes]
    def get_notes_extended(self, from_pitch, pitch_span, from_time, time_span):
        return [n for n in self.notes if from_pitch <= n.pitch < from_pitch + pitch_span
                and from_time <= n.start_time < from_time + time_span]

class Slot:
    def __init__(self, clip=None): self.clip, self.is_triggered, self.fired = clip, False, 0
    @property
    def has_clip(self): return self.clip is not None
    def fire(self): self.fired += 1

class Device:
    def __init__(self, name):
        self.name = name
        self.parameters = [Param("Device On", 0, 1, 1, True), Param("Filter Freq", 0, 1, 0.5), Param("Resonance", 0, 1, 0.2)]

class Track:
    def __init__(self, name, color, n_scenes=8, armable=True):
        self.name, self.color, self.arm, self.solo, self.mute = name, color, False, False, False
        self.can_be_armed = armable
        self.mixer_device = types.SimpleNamespace(volume=Param("Track Volume", 0, 1, 0.85),
            panning=Param("Track Panning", -1, 1, 0.0), sends=[Param("A-Reverb", 0, 1, 0.0)])
        self.clip_slots = [Slot() for _ in range(n_scenes)]
        self.devices = [Device("Operator"), Device("Auto Filter")]
        self.view = types.SimpleNamespace(selected_device=None)
        self.playing_slot_index = -1
    def stop_all_clips(self): pass
    def duplicate_clip_slot(self, i): pass

class Scene:
    def __init__(self, name): self.name, self.fired = name, 0
    def fire(self): self.fired += 1

class Song:
    def __init__(self):
        self.visible_tracks = [Track("808 Drums", 0xFF3A3A), Track("Bass", 0x3A9BFF), Track("Keys", 0x55FF55), Track("Vox", 0xFFD000)]
        self.visible_tracks[0].clip_slots[0] = Slot(Clip("Beat A", 0xFF3A3A, playing=True,
            notes=[(36, b, 0.05) for b in range(4)] + [(42, b + 0.5, 0.05) for b in range(4)] + [(48, 0.0, 2.0)]))
        self.visible_tracks[0].playing_slot_index = 0
        self.visible_tracks[1].clip_slots[0] = Slot(Clip("Bassline", 0x3A9BFF))
        self.visible_tracks[2].clip_slots[1] = Slot(Clip("Chords", 0x55FF55))
        self.return_tracks = []
        self.master_track = Track("Master", 0xFFFFFF, armable=False)
        self.scenes = [Scene("Intro"), Scene("Verse")] + [Scene("") for _ in range(6)]
        self.view = types.SimpleNamespace(selected_track=self.visible_tracks[0], selected_scene=self.scenes[0],
                                          highlighted_clip_slot=self.visible_tracks[0].clip_slots[0])
        self.view.select_device = lambda d: setattr(self.view.selected_track.view, "selected_device", d)
        self.tempo, self.is_playing, self.metronome, self.loop = 124.0, True, False, False
        self.session_record = self.record_mode = self.session_automation_record = False
        self.can_undo = self.can_redo = True
        self.can_capture_midi = False
        self.undos = 0
    def start_playing(self): self.is_playing = True
    def stop_playing(self): self.is_playing = False
    def continue_playing(self): self.is_playing = True
    def undo(self): self.undos += 1
    def stop_all_clips(self): pass
    def tap_tempo(self): pass
    def get_current_beats_song_time(self): return types.SimpleNamespace(bars=17, beats=3, sub_division=2)
SONG = Song()

# ------------------------------------------------------------------ fake _Framework
REGISTRY = {}
SENT_TO_BRIDGE = []
fw = types.ModuleType("_Framework")
cs_mod = types.ModuleType("_Framework.ControlSurface")
class ControlSurface(object):
    def __init__(self, c_instance): self._c_instance = c_instance
    def song(self): return SONG
    @contextmanager
    def component_guard(self): yield
    def show_message(self, m): print("[Live status bar]", m)
    def log_message(self, m): print("[Live log]", m)
    def _send_midi(self, b): SENT_TO_BRIDGE.append(tuple(b))
    def update_display(self): pass
    def disconnect(self): pass
cs_mod.ControlSurface = ControlSurface
ice_mod = types.ModuleType("_Framework.InputControlElement")
ice_mod.MIDI_NOTE_TYPE, ice_mod.MIDI_CC_TYPE = 0, 1
class _Elem(object):
    def __init__(self, typ, ch, ident, name=""):
        self.name, self.listeners = name, []
        REGISTRY[(typ, ch, ident)] = self
    def add_value_listener(self, cb, identify_sender=False): self.listeners.append((cb, identify_sender))
    def receive(self, v):
        for cb, ids in self.listeners: cb(v, self) if ids else cb(v)
be_mod = types.ModuleType("_Framework.ButtonElement")
class ButtonElement(_Elem):
    def __init__(self, momentary, typ, ch, ident, name=""): _Elem.__init__(self, typ, ch, ident, name)
be_mod.ButtonElement = ButtonElement
ee_mod = types.ModuleType("_Framework.EncoderElement")
class EncoderElement(_Elem):
    def __init__(self, typ, ch, ident, mode, name=""): _Elem.__init__(self, typ, ch, ident, name)
ee_mod.EncoderElement = EncoderElement
for m in (fw, cs_mod, ice_mod, be_mod, ee_mod): sys.modules[m.__name__] = m

sys.path.insert(0, ROOT + "/ableton")
import Mikrobe
script = Mikrobe.create_instance(types.SimpleNamespace(set_session_highlight=lambda *a: print("[Live] session box at", a[:2])))

# ------------------------------------------------------------------ bridge with fakes
sys.modules["hid"] = types.ModuleType("hid")
import mikrobe as mb
import mido

class FakeDev:
    def __init__(self): self.leds, self.frames = None, 0; self.display = bytearray(1024)
    def write_leds(self, l): self.leds = list(l)
    def write_display(self, buf): self.display = bytearray(buf); self.frames += 1

class LiveRouter:
    """bridge -> Live: delivers MIDI to the script's controls (or to 'track input')."""
    def __init__(self): self.track_input = []
    def send(self, msg):
        if msg.type == "control_change":
            el = REGISTRY.get((1, msg.channel, msg.control))
        elif msg.type in ("note_on", "note_off"):
            el = REGISTRY.get((0, msg.channel, msg.note))
            v = msg.velocity if msg.type == "note_on" else 0
        else:
            el = None
        if el is None:
            self.track_input.append(msg); return
        el.receive(msg.value if msg.type == "control_change" else v)

cfg = mb.load_config(None)
br = mb.Bridge(cfg)
br.dev = FakeDev()
br.mido = mido
router = LiveRouter()
br.out = router

def pump_script():
    script.update_display()
    while SENT_TO_BRIDGE:
        b = SENT_TO_BRIDGE.pop(0)
        br.on_midi(mido.Message("sysex", data=b[1:-1]))

state = {"buttons": [0, 0, 0, 0], "enc": 5}
def hid_buttons(names_down):
    raw = 0
    for n in names_down: raw |= 1 << mb.BUTTON_INDEX[n]
    rep = [0x01, raw & 255, (raw >> 8) & 255, (raw >> 16) & 255, (raw >> 24) & 255, state["enc"]]
    br.handle_report(rep, time.time())
def press(name, with_shift=False):
    base = ["shift"] if with_shift else []
    hid_buttons(base + [name]); hid_buttons(base);
    if with_shift: hid_buttons([])
def turn(delta):
    for _ in range(abs(delta)):
        state["enc"] = (state["enc"] + (1 if delta > 0 else -1)) & 15
        hid_buttons([])
def pad_report(pressures, now):
    rep = [0x20]
    for hw in range(16):
        v = pressures.get(hw, 0)
        rep += [v & 255, (hw << 4) | ((v >> 8) & 15)]
    br.handle_report(rep + [0] * 32, now)

def snapshot(path):
    br._last_display_t = 0
    br.flush_output(time.time())
    from PIL import Image
    img = Image.new("L", (128, 64), 0)
    px = img.load()
    for y in range(64):
        for x in range(128):
            if br.dev.display[(y >> 3) * 128 + x] & (1 << (y & 7)): px[x, y] = 255
    big = Image.new("RGB", (128 * 4 + 16, 64 * 4 + 16), (20, 20, 20))
    big.paste(Image.merge("RGB", [img.point(lambda v: v * 0.55 + 20), img.point(lambda v: v * 0.85 + 25), img.point(lambda v: v + 30)]).resize((512, 256), Image.NEAREST), (8, 8))
    big.save(path)

os.makedirs(OUT, exist_ok=True)
# ---------------- scenario
snapshot(OUT + "/0_waiting.png")
pump_script()
snapshot(OUT + "/1_volume.png")

turn(-4); pump_script()
vol = SONG.visible_tracks[0].mixer_device.volume.value
print("volume after -4 detents: %.4f" % vol); assert vol < 0.85

press("f2"); turn(+6); pump_script()
print("pan:", SONG.visible_tracks[0].mixer_device.panning.value); assert SONG.visible_tracks[0].mixer_device.panning.value > 0
snapshot(OUT + "/2_pan.png")

press("control"); press("arrow_right"); turn(3); pump_script()
snapshot(OUT + "/3_device.png")
assert SONG.visible_tracks[0].devices[0].parameters[2].value > 0.2

press("grid"); pump_script(); assert SONG.metronome
snapshot(OUT + "/4_flash.png")

press("erase"); assert SONG.undos == 1
press("play"); assert not SONG.is_playing
press("play"); assert SONG.is_playing
press("arrow_right"); press("f1"); press("arrow_right"); pump_script()
print("selected track:", SONG.view.selected_track.name); assert SONG.view.selected_track.name == "Bass"
press("arrow_left")

# pads in NOTE mode: bottom-left pad is hardware index 12
t = time.time()
pad_report({12: 500}, t); pad_report({12: 2200}, t + 0.001); pad_report({12: 2600}, t + 0.004)
pad_report({}, t + 0.1)
notes = [m for m in router.track_input if m.type in ("note_on", "note_off")]
print("note-mode pad ->", notes)
assert notes[0].type == "note_on" and notes[0].note == 36 and notes[0].channel == 0 and notes[0].velocity > 90
assert notes[1].type == "note_off"
# top-right pad (hw 3) = pad 16 = note 51
pad_report({3: 1000}, t + 0.2); pad_report({3: 1000}, t + 0.21); pad_report({}, t + 0.3)
assert router.track_input[-2].note == 51

# switch to CLIP mode, press top-left pad (hw 0 -> pad 13 -> track 0 / scene 0)
press("pad_mode"); pump_script()
assert br.pad_mode == 1
slot = SONG.visible_tracks[0].clip_slots[0]; before = slot.fired
pad_report({0: 1500}, t + 0.5); pad_report({0: 1500}, t + 0.51); pad_report({}, t + 0.6)
assert slot.fired == before + 1, "clip slot not fired"
print("clip pad fired slot:", slot.fired)
snapshot(OUT + "/5_clips.png")
leds = br.dev.leds
print("pad hw0 (track0 scene0, playing red clip) RGB:", leds[mb.LED_PADS_RGB:mb.LED_PADS_RGB + 3])
print("pad hw1 (track1 scene0, stopped blue clip) RGB:", leds[mb.LED_PADS_RGB + 3:mb.LED_PADS_RGB + 6])
print("play LED:", leds[mb.BUTTON_LED["play"]], " f1 LED:", leds[mb.BUTTON_LED["f1"]], " group RGB:", leds[8:11])

press("nav"); turn(2); pump_script()
print("tempo:", SONG.tempo); assert SONG.tempo == 126.0
snapshot(OUT + "/6_tempo.png")

# ---------------- playback lighting in NOTE mode
press("pad_mode"); pump_script(); assert br.pad_mode == 0
press("arrow_left"); pump_script()            # make sure "808 Drums" (with the playing clip) is selected
assert SONG.view.selected_track.name == "808 Drums", SONG.view.selected_track.name
clip = SONG.visible_tracks[0].clip_slots[0].clip
def pad_rgb(note):
    hw = mb.padnum_to_hw(note - br.base_note)
    br.flush_output(time.time())
    return br.dev.leds[mb.LED_PADS_RGB + 3 * hw: mb.LED_PADS_RGB + 3 * hw + 3]
br.playing_until.clear()
clip.playing_position = 0.3; pump_script()     # first refresh: no previous position
clip.playing_position = 0.6; pump_script()     # crossed the hat at 0.5 (only 0.05 beats long)
hat, kick, pad48 = pad_rgb(42), pad_rgb(36), pad_rgb(48)
print("hat (short hit crossed):", hat, " long note 48:", pad48, " kick (not playing):", kick)
assert max(hat) == 127 and max(pad48) == 127, "playing notes should light at full brightness"
assert max(kick) < 40, "kick isn't sounding at 0.6 so it should stay dim"
br.playing_until.clear()
clip.playing_position = 3.9; pump_script()
clip.playing_position = 0.02; pump_script()    # looped around: kick at 0.0 must flash
assert max(pad_rgb(36)) == 127, "kick at loop start should flash after wrap-around"
# notes routed from a MIDI track to the Feedback port also light pads
br.playing_until.clear(); br.on_midi(mido.Message("note_on", note=40, velocity=100))
assert max(pad_rgb(40)) == 127
br.on_midi(mido.Message("note_off", note=40)); br.playing_until.clear()
assert max(pad_rgb(40)) < 40
# stopped transport -> no playback lights
SONG.is_playing = False; br.playing_until.clear(); pump_script()
assert max(pad_rgb(48)) < 40
SONG.is_playing = True
print("playback lighting OK")

# display report framing check
class CapDev(mb.MikroDevice):
    def __init__(self): self.writes = []
    def write(self, data): self.writes.append(list(data))
cd = CapDev(); cd.write_display(bytes(range(256)) * 4); cd.write_leds([1] * 78)
assert [w[:9] for w in cd.writes[:4]] == [[0xE0, 0, 0, p, 0, 0x80, 0, 2, 0] for p in (0, 2, 4, 6)]
assert all(len(w) == 265 for w in cd.writes[:4]) and len(cd.writes[4]) == 79 and cd.writes[4][0] == 0x80
print("\nALL CHECKS PASSED  (screen snapshots in %s)" % OUT)
