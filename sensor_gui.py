"""
GUI de lecture brute des capteurs PAROL6 (firmware wiring test).

Usage:
  1. Flashe le firmware de ce dossier.
  2. Ferme le moniteur serie PlatformIO (le port COM ne peut etre ouvert qu'une fois).
  3. py -3.11 sensor_gui.py
  4. Choisis le COM (STM32 Virtual COM), Connecter.

Pas besoin de pip / pygame : manette via l'API Windows (comme joy.cpl).
pyserial est utilise s'il est deja la, sinon COM via Win32.
"""

from __future__ import annotations
import ctypes
import json
import os
import queue
import shutil
import threading
import time
import tkinter as tk
from ctypes import wintypes
from tkinter import ttk, messagebox, filedialog, simpledialog

try:
    import serial
    from serial.tools import list_ports
except ImportError:
    serial = None
    list_ports = None


# --- COM Windows si pyserial absent (Device Guard bloque pip.exe) ---

class _PortInfo:
    def __init__(self, device, description=""):
        self.device = device
        self.description = description or device


class _DCB(ctypes.Structure):
    _fields_ = [
        ("DCBlength", wintypes.DWORD),
        ("BaudRate", wintypes.DWORD),
        ("flags", wintypes.DWORD),
        ("wReserved", wintypes.WORD),
        ("XonLim", wintypes.WORD),
        ("XoffLim", wintypes.WORD),
        ("ByteSize", ctypes.c_ubyte),
        ("Parity", ctypes.c_ubyte),
        ("StopBits", ctypes.c_ubyte),
        ("XonChar", ctypes.c_char),
        ("XoffChar", ctypes.c_char),
        ("ErrorChar", ctypes.c_char),
        ("EofChar", ctypes.c_char),
        ("EvtChar", ctypes.c_char),
        ("wReserved1", wintypes.WORD),
    ]


class _COMMTIMEOUTS(ctypes.Structure):
    _fields_ = [
        ("ReadIntervalTimeout", wintypes.DWORD),
        ("ReadTotalTimeoutMultiplier", wintypes.DWORD),
        ("ReadTotalTimeoutConstant", wintypes.DWORD),
        ("WriteTotalTimeoutMultiplier", wintypes.DWORD),
        ("WriteTotalTimeoutConstant", wintypes.DWORD),
    ]


class WinCom:
    def __init__(self, port, baudrate, timeout=0.05):
        k32 = ctypes.WinDLL("kernel32", use_last_error=True)
        k32.CreateFileW.restype = wintypes.HANDLE
        path = port if port.startswith("\\\\") else f"\\\\.\\{port}"
        handle = k32.CreateFileW(path, 0xC0000000, 0, None, 3, 0, None)
        if handle == wintypes.HANDLE(-1).value:
            raise OSError(f"Impossible d'ouvrir {port}")
        self._k32 = k32
        self._h = handle
        self.is_open = True
        dcb = _DCB()
        dcb.DCBlength = ctypes.sizeof(_DCB)
        if not k32.GetCommState(handle, ctypes.byref(dcb)):
            self.close()
            raise OSError("GetCommState")
        dcb.BaudRate = int(baudrate)
        dcb.ByteSize = 8
        dcb.Parity = 0
        dcb.StopBits = 0
        if not k32.SetCommState(handle, ctypes.byref(dcb)):
            self.close()
            raise OSError("SetCommState")
        tmo = _COMMTIMEOUTS()
        ms = max(1, int((timeout or 0) * 1000))
        tmo.ReadIntervalTimeout = 1
        tmo.ReadTotalTimeoutMultiplier = 0
        tmo.ReadTotalTimeoutConstant = ms
        tmo.WriteTotalTimeoutMultiplier = 0
        tmo.WriteTotalTimeoutConstant = 200
        k32.SetCommTimeouts(handle, ctypes.byref(tmo))

    def read(self, n):
        buf = ctypes.create_string_buffer(n)
        nread = wintypes.DWORD()
        ok = self._k32.ReadFile(self._h, buf, n, ctypes.byref(nread), None)
        if not ok:
            return b""
        return buf.raw[: nread.value]

    def write(self, data):
        if isinstance(data, str):
            data = data.encode("ascii")
        nwr = wintypes.DWORD()
        self._k32.WriteFile(self._h, data, len(data), ctypes.byref(nwr), None)
        return nwr.value

    def close(self):
        if getattr(self, "is_open", False):
            self._k32.CloseHandle(self._h)
            self.is_open = False


def list_serial_ports():
    if list_ports is not None:
        return list(list_ports.comports())
    ports = []
    try:
        import winreg

        key = winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, r"HARDWARE\DEVICEMAP\SERIALCOMM")
        i = 0
        while True:
            try:
                name, value, _ = winreg.EnumValue(key, i)
                ports.append(_PortInfo(str(value), str(name)))
                i += 1
            except OSError:
                break
        winreg.CloseKey(key)
    except OSError:
        pass
    return ports


def open_serial(dev, baud):
    if serial is not None:
        return serial.Serial(dev, baud, timeout=0.05)
    return WinCom(dev, baud, timeout=0.05)


# --- Manette via winmm (joy.cpl), sans pygame ---

JOY_RETURNALL = 0xFF
JOYERR_NOERROR = 0
JOYCAPS_HASZ = 0x0001
JOYCAPS_HASR = 0x0002
JOYCAPS_HASU = 0x0004
JOYCAPS_HASV = 0x0008
JOYCAPS_HASPOV = 0x0010
MAXPNAMELEN = 32
MAX_JOYSTICKOEMVXDNAME = 260


class JOYCAPSW(ctypes.Structure):
    _fields_ = [
        ("wMid", wintypes.WORD),
        ("wPid", wintypes.WORD),
        ("szPname", wintypes.WCHAR * MAXPNAMELEN),
        ("wXmin", wintypes.UINT),
        ("wXmax", wintypes.UINT),
        ("wYmin", wintypes.UINT),
        ("wYmax", wintypes.UINT),
        ("wZmin", wintypes.UINT),
        ("wZmax", wintypes.UINT),
        ("wNumButtons", wintypes.UINT),
        ("wPeriodMin", wintypes.UINT),
        ("wPeriodMax", wintypes.UINT),
        ("wRmin", wintypes.UINT),
        ("wRmax", wintypes.UINT),
        ("wUmin", wintypes.UINT),
        ("wUmax", wintypes.UINT),
        ("wVmin", wintypes.UINT),
        ("wVmax", wintypes.UINT),
        ("wCaps", wintypes.UINT),
        ("wMaxAxes", wintypes.UINT),
        ("wNumAxes", wintypes.UINT),
        ("wMaxButtons", wintypes.UINT),
        ("szRegKey", wintypes.WCHAR * MAXPNAMELEN),
        ("szOEMVxD", wintypes.WCHAR * MAX_JOYSTICKOEMVXDNAME),
    ]


class JOYINFOEX(ctypes.Structure):
    _fields_ = [
        ("dwSize", wintypes.DWORD),
        ("dwFlags", wintypes.DWORD),
        ("dwXpos", wintypes.DWORD),
        ("dwYpos", wintypes.DWORD),
        ("dwZpos", wintypes.DWORD),
        ("dwRpos", wintypes.DWORD),
        ("dwUpos", wintypes.DWORD),
        ("dwVpos", wintypes.DWORD),
        ("dwButtons", wintypes.DWORD),
        ("dwButtonNumber", wintypes.DWORD),
        ("dwPOV", wintypes.DWORD),
        ("dwReserved1", wintypes.DWORD),
        ("dwReserved2", wintypes.DWORD),
    ]


_winmm = ctypes.WinDLL("winmm")
_winmm.joyGetNumDevs.restype = wintypes.UINT
_winmm.joyGetDevCapsW.argtypes = [wintypes.UINT, ctypes.POINTER(JOYCAPSW), wintypes.UINT]
_winmm.joyGetDevCapsW.restype = wintypes.UINT
_winmm.joyGetPosEx.argtypes = [wintypes.UINT, ctypes.POINTER(JOYINFOEX)]
_winmm.joyGetPosEx.restype = wintypes.UINT


def _axis_norm(pos, amin, amax):
    span = (float(amax) - float(amin)) / 2.0
    if span <= 0:
        return 0.0
    mid = (float(amin) + float(amax)) / 2.0
    v = (float(pos) - mid) / span
    if v < -1.0:
        return -1.0
    if v > 1.0:
        return 1.0
    return v


def _pov_hat(pov):
    if pov > 35900:
        return (0, 0)
    octant = int((int(pov) / 100.0 + 22.5) // 45) % 8
    table = (
        (0, 1),
        (1, 1),
        (1, 0),
        (1, -1),
        (0, -1),
        (-1, -1),
        (-1, 0),
        (-1, 1),
    )
    return table[octant]


def read_winmm_pad():
    empty = {"name": "", "axes": [], "buttons": [], "hats": []}
    nmax = int(_winmm.joyGetNumDevs() or 0)
    for jid in range(nmax):
        caps = JOYCAPSW()
        if _winmm.joyGetDevCapsW(jid, ctypes.byref(caps), ctypes.sizeof(JOYCAPSW)) != JOYERR_NOERROR:
            continue
        info = JOYINFOEX()
        info.dwSize = ctypes.sizeof(JOYINFOEX)
        info.dwFlags = JOY_RETURNALL
        if _winmm.joyGetPosEx(jid, ctypes.byref(info)) != JOYERR_NOERROR:
            continue
        axes = [
            _axis_norm(info.dwXpos, caps.wXmin, caps.wXmax),
            _axis_norm(info.dwYpos, caps.wYmin, caps.wYmax),
            _axis_norm(info.dwZpos, caps.wZmin, caps.wZmax),
            _axis_norm(info.dwRpos, caps.wRmin, caps.wRmax),
        ]
        if caps.wCaps & JOYCAPS_HASU:
            axes.append(_axis_norm(info.dwUpos, caps.wUmin, caps.wUmax))
        if caps.wCaps & JOYCAPS_HASV:
            axes.append(_axis_norm(info.dwVpos, caps.wVmin, caps.wVmax))
        nbtn = int(caps.wNumButtons or 0)
        if nbtn < 12:
            nbtn = 12
        if nbtn > 32:
            nbtn = 32
        buttons = [bool(info.dwButtons & (1 << i)) for i in range(nbtn)]
        hats = [_pov_hat(info.dwPOV)] if (caps.wCaps & JOYCAPS_HASPOV) else []
        return {
            "name": caps.szPname or f"Joystick {jid}",
            "axes": axes,
            "buttons": buttons,
            "hats": hats,
        }
    return empty

BAUD = 115200
HIGHLIGHT_MS = 3000
POLL_MS = 30
PAD_MS = 40


def _appdata_dir():
    base = os.environ.get("APPDATA") or os.path.expanduser("~")
    folder = os.path.join(base, "PAROL6")
    try:
        os.makedirs(folder, exist_ok=True)
    except OSError:
        return None
    return folder


def _user_file(name):
    """Suites et réglages dans %APPDATA%\\PAROL6, pour qu'une mise à jour du code ne les écrase pas."""
    src = os.path.join(os.path.dirname(os.path.abspath(__file__)), name)
    folder = _appdata_dir()
    if not folder:
        return src
    dest = os.path.join(folder, name)
    if not os.path.isfile(dest) and os.path.isfile(src):
        try:
            shutil.copy2(src, dest)
        except OSError:
            return src
    return dest


def app_version():
    path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "VERSION")
    try:
        with open(path, encoding="utf-8") as f:
            text = f.read().strip()
    except OSError:
        text = ""
    return text or "dev"


MAP_FILE = _user_file("pad_map.json")
CLIP_FILE = _user_file("last_motion.json")
LIBRARY_FILE = _user_file("motion_library.json")
PLAY_SPEED_MAX = 8000
PLAY_RATE_MAX = 8.0
START_RETURN_ID = "__start_return__"
PROG_MODE_LABELS = ("1x", "Inverse", "Boomerang")
PROG_MODE_FROM_LABEL = {"1x": "1x", "Inverse": "inverse", "Boomerang": "boomerang"}
PROG_MODE_TO_LABEL = {"1x": "1x", "inverse": "Inverse", "boomerang": "Boomerang"}


def prog_step_mode(step):
    m = str((step or {}).get("mode") or "").strip().lower()
    if m in ("inverse", "reverse", "retour"):
        return "inverse"
    if m in ("boomerang", "boom"):
        return "boomerang"
    if m in ("1x", "once", "forward", "aller"):
        return "1x"
    if (step or {}).get("boomerang"):
        return "boomerang"
    return "1x"

SILK_TO_JOINT = {
    "L1": "axe J6",
    "L2": "axe J2",
    "L3": "axe J3",
    "L4": "axe J4",
    "L5": "axe J5",
    "L6": "axe J1",
}

JOINT_LIMIT = {
    "J1": "LIMIT6  NPN",
    "J2": "LIMIT2  switch",
    "J3": "LIMIT3  switch",
    "J4": "LIMIT4  NPN",
    "J5": "LIMIT5  switch",
    "J6": "LIMIT1  NPN",
}

JOINT_NAMES = ["J1 base", "J2 epaule", "J3 coude", "J4 roll", "J5 pitch", "J6 yaw"]

BG = "#f4f4f4"
CARD = "#ffffff"
UI_SCALE = 1.0
ZOOM_LEVELS = (1.0, 1.5, 2.0)
ZOOM_LABELS = ("100%", "150%", "200%")


def z(n):
    """Pixels d'interface, multipliés par le zoom choisi."""
    return max(1, int(round(float(n) * UI_SCALE)))


def load_ui_scale():
    folder = _appdata_dir()
    if not folder:
        return 1.0
    path = os.path.join(folder, "ui_scale.json")
    try:
        with open(path, encoding="utf-8") as f:
            raw = json.load(f)
        value = float(raw.get("scale", 1))
    except (OSError, ValueError, TypeError, json.JSONDecodeError):
        return 1.0
    for scale in ZOOM_LEVELS:
        if abs(value - scale) < 0.05:
            return scale
    return 1.0


def save_ui_scale(scale):
    folder = _appdata_dir()
    if not folder:
        return
    path = os.path.join(folder, "ui_scale.json")
    try:
        with open(path, "w", encoding="utf-8") as f:
            json.dump({"scale": float(scale)}, f)
    except OSError:
        pass
YELLOW = "#ffe566"
GREEN = "#d4edda"
RED = "#f8d7da"
PAD_BG = "#1c1c1c"
ON = "#7cfc00"
OFF = "#3d3d3d"
PAD_VIZ_W = 250
PAD_VIZ_H = 155
PAD_XY_W = 250
PAD_XY_H = 88
PAD_AXIS_W = 250
PAD_AXIS_H = 72


def default_maps():
    return [
        {"type": "axis", "index": 0, "invert": False, "pos": None, "neg": None, "hat": 0, "hat_axis": "x"},
        {"type": "axis", "index": 1, "invert": True, "pos": None, "neg": None, "hat": 0, "hat_axis": "y"},
        {"type": "axis", "index": 2, "invert": False, "pos": None, "neg": None, "hat": 0, "hat_axis": "x"},
        {"type": "axis", "index": 3, "invert": True, "pos": None, "neg": None, "hat": 0, "hat_axis": "y"},
        {"type": "buttons", "index": 0, "invert": False, "pos": 4, "neg": 6, "hat": 0, "hat_axis": "x"},
        {"type": "buttons", "index": 0, "invert": False, "pos": 5, "neg": 7, "hat": 0, "hat_axis": "x"},
    ]


def norm_map(m):
    if not isinstance(m, dict):
        m = {}
    t = m.get("type", "none")
    if t not in ("axis", "buttons", "hat", "none"):
        t = "none"
    pos, neg = m.get("pos"), m.get("neg")
    return {
        "type": t,
        "index": int(m.get("index") or 0),
        "invert": bool(m.get("invert")),
        "pos": None if pos is None else int(pos),
        "neg": None if neg is None else int(neg),
        "hat": int(m.get("hat") or 0),
        "hat_axis": "y" if m.get("hat_axis") == "y" else "x",
    }


def load_maps():
    try:
        with open(MAP_FILE, "r", encoding="utf-8") as f:
            raw = json.load(f)
        joints = raw.get("joints", raw)
        out = [norm_map(x) for x in joints]
        while len(out) < 6:
            out.append(norm_map({"type": "none"}))
        return out[:6]
    except Exception:
        return default_maps()


def describe_map(m):
    t = m.get("type")
    inv = "  inv" if m.get("invert") else ""
    if t == "axis":
        return f"axe {m.get('index', 0)}{inv}"
    if t == "hat":
        return f"chapeau {m.get('hat', 0)} {str(m.get('hat_axis', 'x')).upper()}{inv}"
    if t == "buttons":
        p, n = m.get("pos"), m.get("neg")
        bits = []
        if p is not None:
            bits.append(f"+btn{p}")
        if n is not None:
            bits.append(f"-btn{n}")
        return ("  ".join(bits) if bits else "boutons ?") + inv
    return "off"


def joint_axis(m, pad):
    t = m.get("type")
    if t == "axis":
        axes = pad["axes"]
        i = int(m.get("index") or 0)
        if i < 0 or i >= len(axes):
            return 0.0
        v = float(axes[i])
        return -v if m.get("invert") else v
    if t == "hat":
        hats = pad["hats"]
        hi = int(m.get("hat") or 0)
        if hi < 0 or hi >= len(hats):
            return 0.0
        hx, hy = hats[hi]
        v = float(hx if m.get("hat_axis") == "x" else hy)
        return -v if m.get("invert") else v
    if t == "buttons":
        btns = pad["buttons"]
        v = 0.0
        p, n = m.get("pos"), m.get("neg")
        if p is not None and 0 <= int(p) < len(btns) and btns[int(p)]:
            v += 1.0
        if n is not None and 0 <= int(n) < len(btns) and btns[int(n)]:
            v -= 1.0
        return -v if m.get("invert") else v
    return 0.0


def _six_speeds(s):
    out = [int(x) for x in (s or [])]
    if len(out) < 6:
        out = out + [0] * (6 - len(out))
    return out[:6]


def clip_segments(clip):
    """Echantillons (t, vitesses) -> segments (duree, vitesses tenues)."""
    segs = []
    if not clip or len(clip) < 2:
        return segs
    for i in range(len(clip) - 1):
        t0, s0 = clip[i]
        t1, _ = clip[i + 1]
        dt = float(t1) - float(t0)
        if dt <= 1e-4:
            continue
        segs.append((dt, _six_speeds(s0)))
    return segs


def reverse_segments(segs):
    """Miroir temporel : on rejoue a l'envers en inversant chaque vitesse."""
    return [(dt, [-x for x in s]) for dt, s in reversed(segs)]


def slice_segments_until(segs, t_end):
    """Prefixe des segments jusqu'a t_end secondes (temps 1x)."""
    out = []
    left = max(0.0, float(t_end or 0.0))
    for dt, s in segs or []:
        if left <= 1e-6:
            break
        take = min(float(dt), left)
        if take > 1e-6:
            out.append((take, list(s)))
        left -= take
    return out


def slice_segments_from(segs, t_start):
    """Suffixe des segments a partir de t_start secondes (temps 1x)."""
    out = []
    skipped = 0.0
    t_start = max(0.0, float(t_start or 0.0))
    for dt, s in segs or []:
        dt = float(dt)
        end = skipped + dt
        if end <= t_start + 1e-6:
            skipped = end
            continue
        take_start = max(0.0, t_start - skipped)
        take = dt - take_start
        if take > 1e-6:
            out.append((take, list(s)))
        skipped = end
    return out


def clip_has_motion(clip):
    for _, s in clip_segments(clip):
        if any(v != 0 for v in s):
            return True
    return False


def timeline_from_segments(segs):
    """Timeline (t, vitesses) dont le dernier tampon marque la fin du dernier segment."""
    steps = []
    t = 0.0
    for dt, s in segs:
        steps.append((round(t, 3), list(s)))
        t += dt
    if steps:
        steps.append((round(t, 3), list(segs[-1][1])))
    return steps, t


def steps_from_raw(raw_steps):
    clip = []
    for x in raw_steps or []:
        if isinstance(x, dict):
            clip.append((float(x["t"]), _six_speeds(x["s"])))
        else:
            clip.append((float(x[0]), _six_speeds(x[1])))
    return clip


def scale_speeds(speeds, rate):
    out = []
    for v in _six_speeds(speeds):
        if v == 0:
            out.append(0)
            continue
        s = int(round(v * rate))
        if s == 0:
            s = 50 if v > 0 else -50
        if s > PLAY_SPEED_MAX:
            s = PLAY_SPEED_MAX
        elif s < -PLAY_SPEED_MAX:
            s = -PLAY_SPEED_MAX
        out.append(s)
    return out


class ValueCell:
    def __init__(self, parent, title: str, subtitle: str = ""):
        self.frame = tk.Frame(parent, bg=CARD, bd=1, relief="solid", padx=z(10), pady=z(8))
        self.title = tk.Label(self.frame, text=title, bg=CARD, font=("Segoe UI", 10, "bold"))
        self.title.pack(anchor="w")
        if subtitle:
            self.sub = tk.Label(self.frame, text=subtitle, bg=CARD, fg="#666", font=("Segoe UI", 8))
            self.sub.pack(anchor="w")
        else:
            self.sub = None
        self.value = tk.Label(self.frame, text="—", bg=CARD, font=("Consolas", 28, "bold"))
        self.value.pack()
        self.prev = tk.Label(self.frame, text="", bg=CARD, fg="#444", font=("Segoe UI", 10))
        self.prev.pack()
        self.current = None
        self._after = None
        self.widgets = [self.frame, self.title, self.value, self.prev]
        if self.sub:
            self.widgets.append(self.sub)

    def _paint(self, color: str):
        for w in self.widgets:
            w.configure(bg=color)

    def set(self, v, on_change=None):
        text = str(v)
        if self.current is None:
            self.current = text
            self.value.configure(text=text)
            return False
        if text == self.current:
            return False
        old = self.current
        self.current = text
        self.value.configure(text=text)
        self.prev.configure(text=f"avant : {old}")
        self._paint(YELLOW)
        if self._after is not None:
            self.frame.after_cancel(self._after)
        self._after = self.frame.after(HIGHLIGHT_MS, self._clear)
        if on_change:
            on_change(old, text)
        return True

    def _clear(self):
        self._after = None
        self.prev.configure(text="")
        self._paint(CARD)


class SensorGui(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title(f"PAROL6 — capteurs + manette  ·  {app_version()}")
        self.configure(bg=BG)
        self.ui_scale = load_ui_scale()
        self._tk_base = float(self.tk.call("tk", "scaling"))
        self._scroll_after = None
        self._vsb_on = False
        self._hsb_on = False
        self._building = False
        self._apply_tk_scale()
        self._fit_window()

        self.ser = None
        self.reader_stop = threading.Event()
        self.reader = None
        self.q = queue.Queue()
        self.cells = {}
        self.k_vars = [tk.IntVar(value=0) for _ in range(6)]
        self.inv_vars = [tk.IntVar(value=0) for _ in range(6)]
        self._syncing_k = False
        self.h_state = [0] * 6
        self.h_prev = [0] * 6
        self.lock_dir = [0] * 6
        self.maps = load_maps()
        for i, m in enumerate(self.maps):
            self.inv_vars[i].set(1 if m.get("invert") else 0)
        self.pad_enable = tk.IntVar(value=0)
        self.motors_soft = False
        self.dead_var = tk.DoubleVar(value=0.18)
        self.maxspd_var = tk.IntVar(value=1600)
        self._capture = None
        self._capture_until = 0.0
        self._pad_last = [0] * 6
        self.map_src = []
        self.btn_lamps = []
        self.rec_on = False
        self.play_on = False
        self.play_boomerang = False
        self.play_mode = "1x"
        self.play_reverse = False
        self.play_cycle = 0
        self.play_steps = []
        self.play_dur = 0.0
        self.rec_t0 = 0.0
        self.play_t0 = 0.0
        self.play_i = 0
        self.play_head_1x = 0.0
        self._play_last_wall = 0.0
        self.clip = []
        self.clip_id = None
        self.library = []
        self._suite_ids = []
        self._selecting_suite = False
        self.play_rate = tk.DoubleVar(value=1.0)
        self._suite_btn_key = None
        self.estop_pressed = False
        self._estop_halted = False
        self.start_pressed = False
        self.start_suite_id = None
        self.start_boom = tk.IntVar(value=0)
        self.start_invert = tk.IntVar(value=0)
        self._start_seen = False
        self._start_ignore_until = 0.0
        self._start_combo_ids = [None]
        self.start_seq_i = 0
        self.programs = []
        self.program_id = None
        self._prog_ids = []
        self._selecting_program = False
        self._selecting_prog_step = False
        self._syncing_prog_step = False
        self.play_program_id = None
        self.play_prog_steps = []
        self.play_prog_i = 0
        self.play_step_repeat = 1
        self.play_step_left = 1
        self.play_one_step = False
        self.run_executed = []
        self.run_pass_segs = []
        self.start_program_id = None
        self.prog_mode_var = tk.StringVar(value="1x")
        self.prog_repeat = tk.IntVar(value=1)
        self.prog_chain = tk.IntVar(value=0)

        self._build()
        self._library_autoload()
        self.bind_all("<MouseWheel>", self._on_wheel)
        self.after(POLL_MS, self._drain)
        self.after(PAD_MS, self._pad_tick)
        self.protocol("WM_DELETE_WINDOW", self._on_close)

    def _apply_tk_scale(self):
        global UI_SCALE
        UI_SCALE = float(self.ui_scale)
        self.tk.call("tk", "scaling", self._tk_base * UI_SCALE)
        style = ttk.Style(self)
        if UI_SCALE <= 1.01:
            style.configure("TButton", padding=(1, 1))
            style.configure("TNotebook.Tab", padding=(0, 0))
        else:
            extra = max(1, int(round(4 * (UI_SCALE - 1.0))))
            style.configure("TButton", padding=(extra, extra))
            style.configure("TNotebook.Tab", padding=(extra, max(1, extra // 2)))

    def _fit_window(self):
        sw = max(800, int(self.winfo_screenwidth()))
        sh = max(600, int(self.winfo_screenheight()))
        w = z(1280)
        h = z(900)
        if self.ui_scale > 1.01:
            w = min(w, max(1280, sw - 16))
            h = min(h, max(900, sh - 72))
        self.geometry(f"{w}x{h}")
        self.minsize(min(z(1100), max(800, sw - 40)), min(z(720), max(500, sh - 80)))

    def _schedule_scroll(self, _event=None):
        if self._scroll_after is not None:
            try:
                self.after_cancel(self._scroll_after)
            except tk.TclError:
                pass
        self._scroll_after = self.after(40, self._sync_scroll)

    def _sync_scroll(self):
        self._scroll_after = None
        view = getattr(self, "_view", None)
        host = getattr(self, "_host", None)
        if view is None or host is None:
            return
        try:
            if not view.winfo_exists() or not host.winfo_exists():
                return
            view.update_idletasks()
            req_w = host.winfo_reqwidth()
            req_h = host.winfo_reqheight()
            cw = view.winfo_width()
            ch = view.winfo_height()
        except tk.TclError:
            return
        if cw < 2 or ch < 2:
            return
        view.itemconfigure(self._view_win, width=max(cw, req_w))
        try:
            view.update_idletasks()
            req_h = host.winfo_reqheight()
        except tk.TclError:
            return
        view.itemconfigure(self._view_win, height=max(ch, req_h))
        view.configure(scrollregion=(0, 0, max(cw, req_w), max(ch, req_h)))
        need_y = req_h > (ch - 24 if self._vsb_on else ch + 4)
        need_x = req_w > (cw - 24 if self._hsb_on else cw + 4)
        if need_y != self._vsb_on:
            if need_y:
                self._vsb.grid(row=0, column=1, sticky="ns")
            else:
                self._vsb.grid_remove()
                view.yview_moveto(0)
            self._vsb_on = need_y
        if need_x != self._hsb_on:
            if need_x:
                self._hsb.grid(row=1, column=0, sticky="ew")
            else:
                self._hsb.grid_remove()
                view.xview_moveto(0)
            self._hsb_on = need_x

    def _on_wheel(self, event):
        view = getattr(self, "_view", None)
        if view is None:
            return
        try:
            if not view.winfo_exists():
                return
        except tk.TclError:
            return
        widget = event.widget
        while widget is not None:
            if isinstance(widget, (tk.Listbox, tk.Text, tk.Spinbox, tk.Scale)):
                return
            widget = getattr(widget, "master", None)
        step = int(-event.delta / 120) or (-1 if event.delta > 0 else 1)
        if event.state & 0x0001:
            view.xview_scroll(step, "units")
        else:
            view.yview_scroll(step, "units")

    def _on_zoom_selected(self, _event=None):
        if self._building:
            return
        label = self.zoom_var.get()
        try:
            scale = ZOOM_LEVELS[ZOOM_LABELS.index(label)]
        except ValueError:
            return
        if abs(scale - self.ui_scale) < 0.01:
            return
        self.ui_scale = scale
        save_ui_scale(scale)
        self._rebuild_ui()

    def _rebuild_ui(self):
        log_text = ""
        port = ""
        try:
            log_text = self.log.get("1.0", "end-1c")
        except (tk.TclError, AttributeError):
            pass
        try:
            port = self.port_var.get()
        except (tk.TclError, AttributeError):
            pass
        connected = bool(self.ser and getattr(self.ser, "is_open", False))
        dev = self._port_device() if connected else ""
        self._building = True
        try:
            if self._scroll_after is not None:
                try:
                    self.after_cancel(self._scroll_after)
                except tk.TclError:
                    pass
                self._scroll_after = None
            for child in list(self.winfo_children()):
                child.destroy()
            self.cells = {}
            self.map_src = []
            self.btn_lamps = []
            self._vsb_on = False
            self._hsb_on = False
            self._suite_btn_key = None
            self._apply_tk_scale()
            self._fit_window()
            self._build()
            if port:
                values = list(self.port_box["values"])
                if port not in values:
                    name = port.split()[0]
                    match = next((v for v in values if str(v).split()[0] == name), None)
                    port = match or port
                self.port_var.set(port)
            if connected:
                shown = self._port_device() or dev
                self.conn_btn.configure(text="Deconnecter")
                self.status.configure(text=f"Connecte  {shown}", fg="#080")
            if self.motors_soft:
                self._set_soft_ui(True)
            self._start_lamp(self.start_pressed)
            self._refresh_map_labels()
            self._refresh_suite_list()
            self._suite_refresh()
            if self._capture:
                joint, _mode = self._capture
                self.cap_lbl.configure(text=f"Capture {JOINT_NAMES[joint]} en cours")
            if log_text.strip():
                self.log.configure(state="normal")
                self.log.insert("1.0", log_text if log_text.endswith("\n") else log_text + "\n")
                self.log.configure(state="disabled")
                self.log.see("end")
        finally:
            self._building = False

    def _build(self):
        top = tk.Frame(self, bg=BG)
        top.pack(fill="x", padx=12, pady=(10, 4 if self.ui_scale > 1.01 else 10))
        tools = top
        if self.ui_scale > 1.01:
            tools = tk.Frame(self, bg=BG)
            tools.pack(fill="x", padx=12, pady=(0, 8))
        shell = tk.Frame(self, bg=BG)
        shell.pack(fill="both", expand=True)
        self._shell = shell
        self._view = tk.Canvas(shell, bg=BG, highlightthickness=0, borderwidth=0)
        self._vsb = ttk.Scrollbar(shell, orient="vertical", command=self._view.yview)
        self._hsb = ttk.Scrollbar(shell, orient="horizontal", command=self._view.xview)
        self._view.configure(yscrollcommand=self._vsb.set, xscrollcommand=self._hsb.set)
        shell.grid_rowconfigure(0, weight=1)
        shell.grid_columnconfigure(0, weight=1)
        self._view.grid(row=0, column=0, sticky="nsew")
        host = tk.Frame(self._view, bg=BG)
        self._host = host
        self._view_win = self._view.create_window((0, 0), window=host, anchor="nw")
        host.bind("<Configure>", self._schedule_scroll)
        self._view.bind("<Configure>", self._schedule_scroll)

        tk.Label(top, text="Port COM", bg=BG).pack(side="left")
        self.port_var = tk.StringVar()
        self.port_box = ttk.Combobox(top, textvariable=self.port_var, width=42, state="readonly")
        self.port_box.pack(side="left", padx=8)
        ttk.Button(top, text="Rafraichir", command=self._refresh_ports).pack(side="left")
        self.conn_btn = ttk.Button(top, text="Connecter", command=self._toggle_conn)
        self.conn_btn.pack(side="left", padx=8)
        self.status = tk.Label(top, text="Deconnecte", bg=BG, fg="#a00")
        self.status.pack(side="left", padx=12)
        self.btn_soft = tk.Button(
            tools,
            text="RENDRE MOU",
            command=self._toggle_soft,
            bg="#455a64",
            fg="white",
            activebackground="#455a64",
            activeforeground="white",
            font=("Segoe UI", 11, "bold"),
            padx=z(14),
            pady=z(4),
        )
        self.btn_soft.pack(side="right", padx=4)
        tk.Label(
            tools,
            text=app_version(),
            bg=BG,
            fg="#777",
            font=("Segoe UI", 9),
        ).pack(side="right", padx=8)
        self.zoom_var = tk.StringVar(value=ZOOM_LABELS[ZOOM_LEVELS.index(self.ui_scale)])
        self.zoom_box = ttk.Combobox(
            tools,
            textvariable=self.zoom_var,
            values=ZOOM_LABELS,
            width=5,
            state="readonly",
        )
        self.zoom_box.pack(side="right", padx=(0, 4))
        self.zoom_box.bind("<<ComboboxSelected>>", self._on_zoom_selected)
        tk.Label(tools, text="Zoom", bg=BG).pack(side="right", padx=(12, 4))

        self._refresh_ports()

        nb = ttk.Notebook(host)
        nb.pack(fill="both", expand=True, padx=10, pady=(0, 10))
        tab_s = tk.Frame(nb, bg=BG)
        tab_p = tk.Frame(nb, bg=BG)
        nb.add(tab_s, text="  Capteurs  ")
        nb.add(tab_p, text="  Manette  ")
        self._build_sensors(tab_s)
        self._build_pad(tab_p)
        self._schedule_scroll()

    def _build_sensors(self, parent):
        hint = tk.Label(
            parent,
            text="Jaune 3 s = la valeur vient de changer.  "
            "Butee : bloque seulement le sens d'arrivee (tu peux repartir). "
            "Active seulement les axes dont le capteur marche.",
            bg=BG,
            fg="#333",
            font=("Segoe UI", 9),
        )
        hint.pack(fill="x", padx=12, pady=(8, 0))

        silk_fr = tk.LabelFrame(parent, text="Broches carte (LIMIT silk)  —  0/1 brut", bg=BG)
        silk_fr.pack(fill="x", padx=12, pady=8)
        silk_row = tk.Frame(silk_fr, bg=BG)
        silk_row.pack(fill="x", padx=6, pady=6)
        for i in range(1, 7):
            key = f"L{i}"
            cell = ValueCell(silk_row, f"LIMIT{i}", SILK_TO_JOINT[key])
            cell.frame.pack(side="left", expand=True, fill="both", padx=4)
            self.cells[key] = cell

        joint_fr = tk.LabelFrame(parent, text="Axes (mapping firmware)  —  brut / butee FW", bg=BG)
        joint_fr.pack(fill="x", padx=12, pady=8)
        jrow = tk.Frame(joint_fr, bg=BG)
        jrow.pack(fill="x", padx=6, pady=6)
        hrow = tk.Frame(joint_fr, bg=BG)
        hrow.pack(fill="x", padx=6, pady=(0, 4))
        for i in range(1, 7):
            key = f"J{i}"
            cell = ValueCell(jrow, JOINT_NAMES[i - 1], f"raw  {JOINT_LIMIT[key]}")
            cell.frame.pack(side="left", expand=True, fill="both", padx=4)
            self.cells[key] = cell
            hkey = f"H{i}"
            hcell = ValueCell(hrow, f"J{i} butee FW", "1 = Home verrait une butee")
            hcell.frame.pack(side="left", expand=True, fill="both", padx=4)
            self.cells[hkey] = hcell

        krow = tk.Frame(joint_fr, bg=BG)
        krow.pack(fill="x", padx=6, pady=(0, 8))
        tk.Label(krow, text="Butee : bloque le sens d'arrivee seulement :", bg=BG).pack(side="left", padx=(4, 8))
        for i in range(6):
            ttk.Checkbutton(
                krow,
                text=f"J{i + 1}",
                variable=self.k_vars[i],
                command=lambda n=i: self._on_k(n),
            ).pack(side="left", padx=6)

        io_fr = tk.LabelFrame(parent, text="Autres entrees", bg=BG)
        io_fr.pack(fill="x", padx=12, pady=8)
        iorow = tk.Frame(io_fr, bg=BG)
        iorow.pack(fill="x", padx=6, pady=6)
        for key, title, sub in (
            ("ESTOP", "ESTOP", "1 = OK, 0 = presse"),
            ("IN1", "START (IN1)", "NO : 0 repos, 1 presse"),
            ("IN2", "INPUT2", "brut"),
            ("BTN", "Bouton power", "brut"),
            ("JSEL", "Axe choisi", ""),
            ("SPD", "Vitesse", "steps/s"),
        ):
            cell = ValueCell(iorow, title, sub)
            cell.frame.pack(side="left", expand=True, fill="both", padx=4)
            self.cells[key] = cell

        mot = tk.Frame(parent, bg=BG)
        mot.pack(fill="x", padx=12, pady=6)
        tk.Label(mot, text="Moteur :", bg=BG).pack(side="left")
        for n in range(1, 7):
            ttk.Button(mot, text=f"J{n}", command=lambda k=str(n): self._cmd(k)).pack(side="left", padx=3)
        ttk.Button(mot, text="p  +400", command=lambda: self._cmd("p")).pack(side="left", padx=8)
        ttk.Button(mot, text="m  -400", command=lambda: self._cmd("m")).pack(side="left", padx=3)
        ttk.Button(mot, text="STOP", command=lambda: self._cmd("s")).pack(side="left", padx=8)

        log_fr = tk.LabelFrame(parent, text="Historique des changements", bg=BG)
        log_fr.pack(fill="both", expand=True, padx=12, pady=(4, 12))
        self.log = tk.Text(log_fr, height=8, font=("Consolas", 10), bg="#111", fg="#eee", state="disabled")
        self.log.pack(fill="both", expand=True, padx=6, pady=6)

    def _build_pad(self, parent):
        bar = tk.Frame(parent, bg=BG)
        bar.pack(fill="x", padx=12, pady=(6, 2))
        ttk.Checkbutton(
            bar,
            text="Piloter le bras avec la manette",
            variable=self.pad_enable,
            command=self._on_pad_enable,
        ).pack(side="left")
        tk.Label(bar, text="  zone morte", bg=BG).pack(side="left", padx=(16, 4))
        tk.Scale(
            bar,
            from_=0.05,
            to=0.45,
            resolution=0.01,
            orient="horizontal",
            variable=self.dead_var,
            length=z(120),
            showvalue=True,
            bg=BG,
        ).pack(side="left")
        tk.Label(bar, text="  vitesse max", bg=BG).pack(side="left", padx=(12, 4))
        tk.Scale(
            bar,
            from_=200,
            to=8000,
            resolution=100,
            orient="horizontal",
            variable=self.maxspd_var,
            length=z(180),
            showvalue=True,
            bg=BG,
        ).pack(side="left")
        self.pad_name = tk.Label(bar, text="Pad : —", bg=BG, fg="#333")
        self.pad_name.pack(side="left", padx=16)
        self.drive_lbl = tk.Label(bar, text="idle", bg=BG, fg="#080", font=("Consolas", 10, "bold"))
        self.drive_lbl.pack(side="right")

        self.cap_lbl = tk.Label(parent, text="", bg=BG, fg="#a60", font=("Segoe UI", 10, "bold"))
        self.cap_lbl.pack(fill="x", padx=12)

        rec_fr = tk.LabelFrame(parent, text="Suite de mouvements", bg=BG)
        self.rec_fr = rec_fr
        self.suite_banner = tk.Label(
            rec_fr,
            text="Pret. Clique Nouvelle rec, bouge le bras, puis Stop rec.",
            font=("Segoe UI", 11, "bold"),
            bg="#eeeeee",
            fg="#333",
            pady=4,
        )
        self.suite_banner.pack(fill="x", padx=8, pady=(4, 2))

        body = tk.Frame(rec_fr, bg=BG)
        body.pack(fill="both", expand=True, padx=8, pady=4)

        rec_btns = tk.Frame(body, bg=BG)
        rec_btns.pack(fill="x")

        def suite_btn(parent, text, cmd, bg, fg="white"):
            return tk.Button(
                parent,
                text=text,
                command=cmd,
                bg=bg,
                fg=fg,
                activebackground=bg,
                activeforeground=fg,
                disabledforeground="#888",
                font=("Segoe UI", 9, "bold"),
                padx=z(10),
                pady=z(5),
                relief="raised",
                bd=2,
            )

        self.btn_rec = suite_btn(rec_btns, "●  Nouvelle rec", self._rec_start, "#c62828")
        self.btn_rec.pack(side="left", padx=3)
        self.btn_rec_stop = suite_btn(rec_btns, "Stop rec", self._rec_stop, "#f9a825", "#111")
        self.btn_rec_stop.pack(side="left", padx=3)
        self.btn_play_once = suite_btn(rec_btns, "Rejouer 1x", lambda: self._play_start(False), "#6a1b9a")
        self.btn_play_once.pack(side="left", padx=(14, 3))
        self.btn_play_rev = suite_btn(rec_btns, "Inverse", lambda: self._play_start(reverse=True), "#00695c")
        self.btn_play_rev.pack(side="left", padx=3)
        self.btn_boom = suite_btn(rec_btns, "Boomerang", lambda: self._play_start(True), "#1565c0")
        self.btn_boom.pack(side="left", padx=3)
        self.btn_play_stop = suite_btn(rec_btns, "Stop play", lambda: self._play_stop(keep_run=True), "#424242")
        self.btn_play_stop.pack(side="left", padx=3)
        self.btn_home = suite_btn(rec_btns, "Retour depart", self._play_return_home, "#e65100")
        self.btn_home.pack(side="left", padx=3)
        self.btn_save = suite_btn(rec_btns, "Exporter", self._clip_save, "#eceff1", "#111")
        self.btn_save.pack(side="left", padx=(14, 3))
        self.btn_load = suite_btn(rec_btns, "Importer", self._clip_load, "#eceff1", "#111")
        self.btn_load.pack(side="left", padx=3)

        spd = tk.Frame(body, bg=BG)
        spd.pack(fill="x", pady=(6, 0))
        tk.Label(spd, text="Vitesse lecture (suites et programmes)", bg=BG, font=("Segoe UI", 9, "bold")).pack(side="left")
        self.rate_scale = tk.Scale(
            spd,
            from_=0.5,
            to=PLAY_RATE_MAX,
            resolution=0.1,
            orient="horizontal",
            variable=self.play_rate,
            length=z(240),
            showvalue=False,
            bg=BG,
            highlightthickness=0,
            command=lambda _v: self._rate_label(),
        )
        self.rate_scale.pack(side="left", padx=8)
        self.rate_scale.bind("<ButtonRelease-1>", lambda _e: self._on_play_rate_release())
        self.rate_lbl = tk.Label(spd, text="1.0x", bg=BG, font=("Consolas", 11, "bold"))
        self.rate_lbl.pack(side="left")
        tk.Label(spd, text="  enregistre lent, joue plus vite", bg=BG, fg="#666").pack(side="left", padx=8)

        self.rec_lbl = tk.Label(body, text="", bg=BG, fg="#555", font=("Consolas", 10))
        self.rec_lbl.pack(anchor="w", pady=(2, 2))

        lists = tk.Frame(body, bg=BG)
        lists.pack(fill="both", expand=True, pady=(2, 0))

        suites_fr = tk.LabelFrame(lists, text="Mes suites  (double-clic = ajouter au programme)", bg=BG)
        suites_fr.pack(side="left", fill="both", expand=True)
        self.suite_list = tk.Listbox(
            suites_fr, height=8, width=32, font=("Segoe UI", 9), exportselection=False, bg="#fff"
        )
        self.suite_list.pack(fill="both", expand=True, padx=6, pady=4)
        self.suite_list.bind("<<ListboxSelect>>", self._on_suite_select)
        self.suite_list.bind("<Double-Button-1>", lambda _e: self._program_add_suite())
        lib_btns = tk.Frame(suites_fr, bg=BG)
        lib_btns.pack(fill="x", padx=6, pady=(0, 6))
        self.btn_rename = suite_btn(lib_btns, "Renommer", self._clip_rename, "#eceff1", "#111")
        self.btn_rename.pack(side="left", padx=2)
        self.btn_clear = suite_btn(lib_btns, "Supprimer", self._clip_clear, "#eceff1", "#111")
        self.btn_clear.pack(side="left", padx=2)

        mid_add = tk.Frame(lists, bg=BG)
        mid_add.pack(side="left", fill="y", padx=8, pady=20)
        self.btn_prog_add = suite_btn(mid_add, "Ajouter  →", self._program_add_suite, "#2e7d32")
        self.btn_prog_add.pack(pady=(18, 6))
        tk.Label(mid_add, text="ou\ndouble-clic", bg=BG, fg="#666", font=("Segoe UI", 8), justify="center").pack()

        prog_fr = tk.LabelFrame(lists, text="Programme", bg=BG)
        prog_fr.pack(side="left", fill="both", expand=True)
        prog_top = tk.Frame(prog_fr, bg=BG)
        prog_top.pack(fill="x", padx=6, pady=(4, 0))
        self.prog_combo_var = tk.StringVar(value="(aucun)")
        self.prog_combo = ttk.Combobox(
            prog_top, textvariable=self.prog_combo_var, width=18, state="readonly"
        )
        self.prog_combo.pack(side="left", fill="x", expand=True)
        self.prog_combo.bind("<<ComboboxSelected>>", self._on_program_combo)
        self.btn_prog_new = suite_btn(prog_top, "Nouveau", self._program_new, "#eceff1", "#111")
        self.btn_prog_new.pack(side="left", padx=(4, 2))
        self.btn_prog_rename = suite_btn(prog_top, "Renommer", self._program_rename, "#eceff1", "#111")
        self.btn_prog_rename.pack(side="left", padx=2)
        self.btn_prog_del = suite_btn(prog_top, "Suppr", self._program_delete, "#eceff1", "#111")
        self.btn_prog_del.pack(side="left", padx=2)
        self.prog_steps_list = tk.Listbox(
            prog_fr, height=8, width=38, font=("Segoe UI", 9), exportselection=False, bg="#fff"
        )
        self.prog_steps_list.pack(fill="both", expand=True, padx=6, pady=4)
        self.prog_steps_list.bind("<<ListboxSelect>>", self._on_prog_step_select)
        self.prog_steps_list.bind("<Double-Button-1>", lambda _e: self._program_step_remove())
        step_btns = tk.Frame(prog_fr, bg=BG)
        step_btns.pack(fill="x", padx=6, pady=(0, 4))
        self.btn_prog_up = suite_btn(step_btns, "Monter", self._program_step_up, "#eceff1", "#111")
        self.btn_prog_up.pack(side="left", padx=2)
        self.btn_prog_down = suite_btn(step_btns, "Descendre", self._program_step_down, "#eceff1", "#111")
        self.btn_prog_down.pack(side="left", padx=2)
        self.btn_prog_remove = suite_btn(step_btns, "Retirer", self._program_step_remove, "#eceff1", "#111")
        self.btn_prog_remove.pack(side="left", padx=2)
        tk.Label(step_btns, text="Mode", bg=BG).pack(side="left", padx=(8, 0))
        self.prog_mode_combo = ttk.Combobox(
            step_btns,
            textvariable=self.prog_mode_var,
            values=PROG_MODE_LABELS,
            width=10,
            state="readonly",
        )
        self.prog_mode_combo.pack(side="left", padx=(4, 4))
        self.prog_mode_combo.bind("<<ComboboxSelected>>", self._on_prog_step_edit)
        self.prog_repeat_lbl = tk.Label(step_btns, text="fois", bg=BG)
        self.prog_repeat_lbl.pack(side="left")
        self.prog_repeat_spin = tk.Spinbox(
            step_btns,
            from_=1,
            to=20,
            textvariable=self.prog_repeat,
            width=4,
            command=self._on_prog_step_edit,
        )
        self.prog_repeat_spin.pack(side="left", padx=2)
        self.prog_repeat_spin.bind("<FocusOut>", lambda _e: self._on_prog_step_edit())
        self.prog_repeat_spin.bind("<Return>", lambda _e: self._on_prog_step_edit())
        self.btn_prog_play = suite_btn(step_btns, "Jouer", self._program_play_click, "#1565c0")
        self.btn_prog_play.pack(side="left", padx=2)
        self.btn_prog_start = suite_btn(
            step_btns, "→ START", self._start_assign_program, "#eceff1", "#111"
        )
        self.btn_prog_start.pack(side="left", padx=2)
        self.chk_prog_chain = ttk.Checkbutton(
            step_btns,
            text="meme IN1",
            variable=self.prog_chain,
            command=self._on_prog_step_edit,
        )
        self.chk_prog_chain.pack(side="left", padx=(10, 0))

        start = tk.Frame(rec_fr, bg=BG)
        start.pack(fill="x", padx=8, pady=(4, 0))
        self.start_lamp = tk.Label(
            start, text=" START ", bg=OFF, fg="#ccc", font=("Segoe UI", 9, "bold"), padx=8, pady=2
        )
        self.start_lamp.pack(side="left")
        tk.Label(start, text="IN1", bg=BG).pack(side="left", padx=(8, 4))
        ttk.Checkbutton(
            start, text="inverser", variable=self.start_invert, command=self._on_start_invert
        ).pack(side="left")
        tk.Label(start, text="Programme", bg=BG).pack(side="left", padx=(12, 4))
        self.start_combo_var = tk.StringVar(value="(aucun)")
        self.start_combo = ttk.Combobox(
            start, textvariable=self.start_combo_var, width=22, state="readonly"
        )
        self.start_combo.pack(side="left")
        self.start_combo.bind("<<ComboboxSelected>>", self._on_start_combo)
        self.start_next_lbl = tk.Label(
            start, text="Prochaine pression : —", bg=BG, font=("Segoe UI", 9, "bold")
        )
        self.start_next_lbl.pack(side="left", padx=12)
        ttk.Button(start, text="Remettre a 1", command=self._start_seq_reset).pack(side="left")
        tk.Label(
            rec_fr,
            text="Assigne un programme a START. Jouer = tout d'un coup. "
            "IN1 = une pression par groupe. Coche « meme IN1 » sur une etape pour la coller a la precedente "
            "(ex. 1, 2, 3 puis les 3 inverse d'un coup). Apres la derniere pression, ca recommence a la 1re. "
            "E-stop coupe les moteurs. Retour depart inverse le chemin deja joue.",
            bg=BG,
            fg="#666",
            font=("Segoe UI", 8),
            wraplength=z(1200),
            justify="left",
        ).pack(anchor="w", padx=8, pady=(0, 4))

        mid = tk.Frame(parent, bg=BG)
        mid.pack(fill="x", padx=8, pady=(0, 2))

        viz = tk.Frame(mid, bg=BG)
        viz.pack(side="left", padx=(0, 6))
        self.pad_cv = tk.Canvas(
            viz, width=z(PAD_VIZ_W), height=z(PAD_VIZ_H), bg=PAD_BG, highlightthickness=0
        )
        self.pad_cv.pack(side="left", padx=(4, 4), pady=2)
        gauges = tk.Frame(viz, bg="#111")
        gauges.pack(side="left", pady=2)
        self.xy_cv = tk.Canvas(
            gauges, width=z(PAD_XY_W), height=z(PAD_XY_H), bg="#111", highlightthickness=0
        )
        self.xy_cv.pack()
        self.axis_cv = tk.Canvas(
            gauges, width=z(PAD_AXIS_W), height=z(PAD_AXIS_H), bg="#111", highlightthickness=0
        )
        self.axis_cv.pack()
        btn_fr = tk.Frame(gauges, bg="#111")
        btn_fr.pack(fill="x", pady=(2, 2))
        for i in range(16):
            lab = tk.Label(
                btn_fr, text=str(i), width=2, bg=OFF, fg="#ccc", font=("Consolas", 8, "bold")
            )
            lab.grid(row=i // 8, column=i % 8, padx=1, pady=1)
            self.btn_lamps.append(lab)

        map_fr = tk.LabelFrame(mid, text="Quelle touche bouge quel moteur", bg=BG)
        map_fr.pack(side="left", fill="both", expand=True, padx=(4, 4), pady=2)
        tk.Label(
            map_fr,
            text="Capturer : stick / chapeau / touche.   + et − = paire de boutons.",
            bg=BG,
            justify="left",
            fg="#444",
            font=("Segoe UI", 8),
        ).pack(anchor="w", padx=6, pady=(2, 0))

        for i in range(6):
            row = tk.Frame(map_fr, bg=BG)
            row.pack(fill="x", padx=4, pady=1)
            tk.Label(
                row, text=JOINT_NAMES[i], width=11, anchor="w", bg=BG, font=("Segoe UI", 8, "bold")
            ).pack(side="left")
            src = tk.Label(
                row,
                text=describe_map(self.maps[i]),
                width=16,
                anchor="w",
                bg="#eee",
                relief="solid",
                bd=1,
                font=("Segoe UI", 8),
            )
            src.pack(side="left", padx=3)
            self.map_src.append(src)
            ttk.Button(row, text="Capturer", width=8, command=lambda n=i: self._start_capture(n, "auto")).pack(
                side="left", padx=1
            )
            ttk.Button(row, text="+", width=2, command=lambda n=i: self._start_capture(n, "pos")).pack(side="left")
            ttk.Button(row, text="−", width=2, command=lambda n=i: self._start_capture(n, "neg")).pack(side="left")
            ttk.Button(row, text="Off", width=3, command=lambda n=i: self._clear_map(n)).pack(side="left", padx=1)
            ttk.Checkbutton(
                row,
                text="inv",
                variable=self.inv_vars[i],
                command=lambda n=i: self._on_inv(n),
            ).pack(side="left")
            ttk.Checkbutton(
                row,
                text="butee",
                variable=self.k_vars[i],
                command=lambda n=i: self._on_k(n),
            ).pack(side="left")

        rec_fr.pack(fill="both", expand=True, padx=12, pady=(2, 8))

    def _refresh_map_labels(self):
        for i, lab in enumerate(self.map_src):
            lab.configure(text=describe_map(self.maps[i]))

    def _save_maps(self):
        try:
            with open(MAP_FILE, "w", encoding="utf-8") as f:
                json.dump({"joints": self.maps}, f, indent=2)
        except Exception:
            pass
        self._refresh_map_labels()

    def _on_inv(self, i):
        self.maps[i]["invert"] = bool(self.inv_vars[i].get())
        self._save_maps()

    def _clear_map(self, i):
        self.maps[i] = norm_map({"type": "none", "invert": bool(self.inv_vars[i].get())})
        self._capture = None
        self.cap_lbl.configure(text="")
        self._save_maps()

    def _start_capture(self, i, mode):
        self._capture = (i, mode)
        self._capture_until = time.time() + 8
        names = {
            "auto": "bouge un stick, le chapeau, ou une touche",
            "pos": "appuie la touche SENS +",
            "neg": "appuie la touche SENS −",
        }
        self.cap_lbl.configure(text=f"Capture {JOINT_NAMES[i]} : {names.get(mode, mode)}  (8 s)")

    def _on_k(self, i):
        if self._syncing_k:
            return
        self._cmd(f"k{i + 1}={1 if self.k_vars[i].get() else 0}")

    def _on_pad_enable(self):
        if not self.pad_enable.get():
            self._send_speeds([0] * 6, force=True)
            self.drive_lbl.configure(text="idle")

    def _refresh_ports(self):
        ports = list_serial_ports()
        labels = [f"{p.device}  {p.description}" for p in ports]
        self.port_box["values"] = labels
        if labels:
            stm = next((x for x in labels if "STM" in x.upper() or "SERIAL" in x.upper()), labels[0])
            self.port_var.set(stm)
        else:
            self.port_var.set("")

    def _port_device(self) -> str:
        raw = self.port_var.get().strip()
        return raw.split()[0] if raw else ""

    def _toggle_conn(self):
        if self.ser and self.ser.is_open:
            self._disconnect()
        else:
            self._connect()

    def _connect(self):
        dev = self._port_device()
        if not dev:
            messagebox.showerror("Port", "Aucun port COM.")
            return
        try:
            self.ser = open_serial(dev, BAUD)
        except Exception as e:
            messagebox.showerror(
                "Port",
                f"Impossible d'ouvrir {dev}\n{e}\n\nFerme le moniteur PlatformIO.",
            )
            return
        self.reader_stop.clear()
        self.reader = threading.Thread(target=self._read_loop, daemon=True)
        self.reader.start()
        self.conn_btn.configure(text="Deconnecter")
        self.status.configure(text=f"Connecte  {dev}", fg="#080")
        self._log(f"--- connecte {dev} ---")
        self._start_seen = False
        self.start_pressed = False
        self._start_lamp(False)
        for i, var in enumerate(self.k_vars):
            self._cmd(f"k{i + 1}={1 if var.get() else 0}")

    def _disconnect(self):
        try:
            if self.ser and self.ser.is_open:
                self.ser.write(b"s\n")
        except Exception:
            pass
        self.reader_stop.set()
        if self.ser:
            try:
                self.ser.close()
            except Exception:
                pass
            self.ser = None
        self.conn_btn.configure(text="Connecter")
        self.status.configure(text="Deconnecte", fg="#a00")
        self._log("--- deconnecte ---")
        self.pad_enable.set(0)
        self._pad_last = [0] * 6

    def _blocked_while_soft(self, c: str) -> bool:
        s = c.strip().lower()
        if not s:
            return False
        if s in ("p", "m", "+", "-", "plus", "minus", "go", "g"):
            return True
        return s[0] in ("a", "v")

    def _set_soft_ui(self, soft: bool):
        self.motors_soft = soft
        if soft:
            self.btn_soft.configure(
                text="Tenir",
                bg="#e65100",
                activebackground="#e65100",
            )
            self.drive_lbl.configure(text="mou", fg="#e65100")
        else:
            self.btn_soft.configure(
                text="Rendre mou",
                bg="#455a64",
                activebackground="#455a64",
            )
            if not self.play_on:
                self.drive_lbl.configure(text="idle", fg="#080")

    def _toggle_soft(self):
        if not self.ser or not self.ser.is_open:
            self._log("Pas de COM")
            return
        if not self.motors_soft:
            if not messagebox.askokcancel(
                "Rendre mou",
                "Les moteurs ne tiendront plus le bras.\n\n"
                "Tiens-le : l'epaule et le coude vont tomber.\n"
                "La position sera perdue si tu le bouges a la main.",
            ):
                return
            self._play_stop()
            self._pad_last = [0] * 6
            self._cmd("off")
        else:
            self._pad_last = [0] * 6
            self._cmd("on")

    def _cmd(self, c: str):
        if not self.ser or not self.ser.is_open:
            return
        if self.motors_soft and self._blocked_while_soft(c):
            self._log("Ignore : bras mou. Clique Tenir d'abord.")
            return
        try:
            payload = c if c.endswith("\n") else c + "\n"
            self.ser.write(payload.encode("ascii"))
        except Exception:
            pass

    def _read_loop(self):
        buf = b""
        while not self.reader_stop.is_set():
            try:
                if not self.ser or not self.ser.is_open:
                    time.sleep(0.05)
                    continue
                chunk = self.ser.read(256)
                if not chunk:
                    continue
                buf += chunk
                while b"\n" in buf:
                    line, buf = buf.split(b"\n", 1)
                    text = line.decode("ascii", errors="ignore").strip()
                    if text.startswith("D ") or text.startswith("D"):
                        self.q.put(text)
            except Exception:
                time.sleep(0.05)

    def _drain(self):
        try:
            while True:
                line = self.q.get_nowait()
                self._apply_line(line)
        except queue.Empty:
            pass
        self.after(POLL_MS, self._drain)

    def _apply_line(self, line: str):
        data = {}
        for part in line.split():
            if part == "D" or "=" not in part:
                continue
            k, v = part.split("=", 1)
            data[k] = v

        def log_change(name):
            def _cb(old, new):
                self._log(f"{name}: {old}  ->  {new}")

            return _cb

        for i in range(1, 7):
            k = f"L{i}"
            if k in data:
                self.cells[k].set(data[k], log_change(f"LIMIT{i} (silk, {SILK_TO_JOINT[k]})"))
            k = f"J{i}"
            if k in data:
                self.cells[k].set(data[k], log_change(f"{k} raw"))
            k = f"H{i}"
            if k in data:
                self.cells[k].set(data[k], log_change(f"J{i} butee FW"))
                try:
                    self.h_state[i - 1] = int(data[k])
                except ValueError:
                    pass
        for k, label in (
            ("ESTOP", "ESTOP"),
            ("IN1", "INPUT1"),
            ("IN2", "INPUT2"),
            ("BTN", "Bouton"),
            ("JSEL", "Axe"),
            ("SPD", "Vitesse"),
        ):
            if k in data:
                self.cells[k].set(data[k], log_change(label))

        if "SOFT" in data:
            soft = data["SOFT"] == "1"
            if soft != self.motors_soft:
                if soft and self.play_on:
                    self._play_stop()
                self._pad_last = [0] * 6
                self._set_soft_ui(soft)
                self._log(
                    "Bras mou. Tiens-le."
                    if soft
                    else "Couple remis. Position fausse si le bras a ete bouge a la main."
                )

        if "ESTOP" in data:
            pressed = data["ESTOP"] == "0"
            was = self.estop_pressed
            self.estop_pressed = pressed
            if pressed and not was:
                self._on_estop()
            elif (not pressed) and was:
                self._clip_status()

        if "IN1" in data:
            raw = data["IN1"] == "1"
            pressed = (not raw) if self.start_invert.get() else raw
            was = self.start_pressed
            self.start_pressed = pressed
            self._start_lamp(pressed)
            if not self._start_seen:
                self._start_seen = True
            elif pressed and not was:
                self._on_start_button()

        for i in range(1, 7):
            h = data.get(f"H{i}")
            cell = self.cells[f"H{i}"]
            if cell._after:
                continue
            if h == "1":
                cell._paint(RED)
            elif h == "0":
                cell._paint(GREEN)

        if any(f"K{i}" in data for i in range(1, 7)):
            self._syncing_k = True
            try:
                for i in range(1, 7):
                    kv = data.get(f"K{i}")
                    if kv is None:
                        continue
                    want = 1 if kv == "1" else 0
                    if self.k_vars[i - 1].get() != want:
                        self.k_vars[i - 1].set(want)
            finally:
                self._syncing_k = False

    def _log(self, msg: str):
        ts = time.strftime("%H:%M:%S")
        self.log.configure(state="normal")
        self.log.insert("end", f"{ts}  {msg}\n")
        self.log.see("end")
        self.log.configure(state="disabled")

    def _read_pad(self):
        return read_winmm_pad()

    def _try_capture(self, pad):
        if self._capture is None:
            return
        if time.time() > self._capture_until:
            self._capture = None
            self.cap_lbl.configure(text="Capture annulee")
            return
        i, mode = self._capture
        m = self.maps[i]
        inv = bool(self.inv_vars[i].get())

        if mode in ("pos", "neg"):
            for b, on in enumerate(pad["buttons"]):
                if on:
                    m["type"] = "buttons"
                    m[mode] = b
                    m["invert"] = inv
                    self.maps[i] = norm_map(m)
                    self._capture = None
                    self.cap_lbl.configure(text=f"{JOINT_NAMES[i]} : {describe_map(self.maps[i])}")
                    self._save_maps()
                    return
            return

        for hi, (hx, hy) in enumerate(pad["hats"]):
            if hx or hy:
                axis = "x" if abs(hx) >= abs(hy) else "y"
                self.maps[i] = norm_map(
                    {"type": "hat", "hat": hi, "hat_axis": axis, "invert": inv}
                )
                self._capture = None
                self.cap_lbl.configure(text=f"{JOINT_NAMES[i]} : {describe_map(self.maps[i])}")
                self._save_maps()
                return
        best = -1
        best_v = 0.55
        for ai, av in enumerate(pad["axes"]):
            if abs(av) > best_v:
                best_v = abs(av)
                best = ai
        if best >= 0:
            self.maps[i] = norm_map({"type": "axis", "index": best, "invert": inv})
            self._capture = None
            self.cap_lbl.configure(text=f"{JOINT_NAMES[i]} : {describe_map(self.maps[i])}")
            self._save_maps()
            return
        for b, on in enumerate(pad["buttons"]):
            if on:
                m = {
                    "type": "buttons",
                    "pos": b,
                    "neg": self.maps[i].get("neg"),
                    "invert": inv,
                }
                self.maps[i] = norm_map(m)
                self._capture = None
                self.cap_lbl.configure(text=f"{JOINT_NAMES[i]} : {describe_map(self.maps[i])}  (touche − pour l'autre sens)")
                self._save_maps()
                return

    def _draw_xy(self, cv, x, y, size, ax, ay, title, hat=False):
        x, y, size = z(x), z(y), z(size)
        cv.create_rectangle(x, y, x + size, y + size, outline="#666", fill="#1a1a1a")
        cv.create_text(x + size / 2, y - z(7), text=title, fill="#bbb", font=("Segoe UI", 7))
        cx, cy = x + size / 2, y + size / 2
        cv.create_line(x + z(3), cy, x + size - z(3), cy, fill="#333")
        cv.create_line(cx, y + z(3), cx, y + size - z(3), fill="#333")
        px = cx + max(-1.0, min(1.0, ax)) * (size / 2 - z(6))
        py = cy + max(-1.0, min(1.0, ay)) * (size / 2 - z(6))
        r = z(4 if hat else 5)
        cv.create_oval(px - r, py - r, px + r, py + r, fill=ON, outline="")

    def _draw_pad(self, pad):
        cv = self.pad_cv
        cv.delete("all")
        axes = pad["axes"] + [0.0] * 8
        btns = pad["buttons"] + [False] * 16
        hats = pad["hats"]
        hx, hy = hats[0] if hats else (0, 0)
        if not hats:
            # certains clones PS1/PS2 envoient le D-pad en boutons 12-15
            if len(pad["buttons"]) > 15:
                hx = (1 if btns[15] else 0) - (1 if btns[13] else 0)
                hy = (1 if btns[12] else 0) - (1 if btns[14] else 0)

        def lit(i):
            return ON if i < len(pad["buttons"]) and pad["buttons"][i] else "#2a2a2a"

        sx = z(PAD_VIZ_W) / 470.0
        sy = z(PAD_VIZ_H) / 300.0

        def X(v):
            return v * sx

        def Y(v):
            return v * sy

        cv.create_rectangle(X(40), Y(70), X(430), Y(285), outline="#666", fill="#252530", width=2)
        name = pad["name"] or "Aucune manette"
        if len(name) > 28:
            name = name[:27] + "..."
        cv.create_text(X(235), Y(16), text=name, fill="#eee", font=("Segoe UI", 8, "bold"))

        def shoulder(x1, y1, x2, y2, i, label):
            cv.create_rectangle(X(x1), Y(y1), X(x2), Y(y2), fill=lit(i), outline="#888")
            cv.create_text((X(x1) + X(x2)) / 2, (Y(y1) + Y(y2)) / 2, text=label, fill="#fff", font=("Segoe UI", 7, "bold"))

        shoulder(50, 28, 130, 48, 6, "L2")
        shoulder(50, 50, 130, 68, 4, "L1")
        shoulder(340, 28, 420, 48, 7, "R2")
        shoulder(340, 50, 420, 68, 5, "R1")

        def dpad_arm(x1, y1, x2, y2, on):
            cv.create_rectangle(X(x1), Y(y1), X(x2), Y(y2), fill=ON if on else "#333", outline="#555")

        dx, dy = 120, 145
        dpad_arm(dx - 12, dy - 38, dx + 12, dy - 14, hy > 0)
        dpad_arm(dx - 12, dy + 14, dx + 12, dy + 38, hy < 0)
        dpad_arm(dx - 38, dy - 12, dx - 14, dy + 12, hx < 0)
        dpad_arm(dx + 14, dy - 12, dx + 38, dy + 12, hx > 0)
        cv.create_rectangle(X(dx - 12), Y(dy - 12), X(dx + 12), Y(dy + 12), fill="#333", outline="#555")

        fx, fy = 350, 145
        r_face = 12 * min(sx, sy)

        def face(px, py, i, lab):
            cv.create_oval(X(px) - r_face, Y(py) - r_face, X(px) + r_face, Y(py) + r_face, fill=lit(i), outline="#888")
            cv.create_text(X(px), Y(py), text=lab, fill="#fff", font=("Segoe UI", 7, "bold"))

        face(fx, fy - 32, 0, "△")
        face(fx + 32, fy, 1, "○")
        face(fx, fy + 32, 2, "×")
        face(fx - 32, fy, 3, "□")

        r_outer = 28 * min(sx, sy)
        r_knob = 10 * min(sx, sy)
        travel = 14 * min(sx, sy)

        def stick(cx, cy, ax, ay, click_i):
            cv.create_oval(X(cx) - r_outer, Y(cy) - r_outer, X(cx) + r_outer, Y(cy) + r_outer, outline="#555", fill="#1a1a1a")
            kx = X(cx) + ax * travel
            ky = Y(cy) + ay * travel
            col = ON if click_i < len(pad["buttons"]) and pad["buttons"][click_i] else "#888"
            cv.create_oval(kx - r_knob, ky - r_knob, kx + r_knob, ky + r_knob, fill=col, outline="#ccc")

        stick(155, 230, axes[0], axes[1], 10)
        rx = axes[2] if len(pad["axes"]) > 2 else 0.0
        ry = axes[3] if len(pad["axes"]) > 3 else 0.0
        stick(250, 230, rx, ry, 11)

        cv.create_rectangle(X(200), Y(118), X(228), Y(132), fill=lit(8), outline="#666")
        cv.create_text(X(214), Y(125), text="sel", fill="#fff", font=("Segoe UI", 6))
        cv.create_rectangle(X(242), Y(118), X(270), Y(132), fill=lit(9), outline="#666")
        cv.create_text(X(256), Y(125), text="st", fill="#fff", font=("Segoe UI", 6))

        g = self.xy_cv
        g.delete("all")
        self._draw_xy(g, 8, 14, 70, axes[0], axes[1], "Stick G")
        self._draw_xy(g, 90, 14, 70, rx, ry, "Stick D")
        self._draw_xy(g, 172, 14, 70, float(hx), float(-hy), "D-pad", hat=True)

        a = self.axis_cv
        a.delete("all")
        w = z(210)
        row_h = z(11)
        for i, val in enumerate(pad["axes"][:6]):
            yy = z(4) + i * row_h
            a.create_text(z(22), yy + z(5), text=f"A{i}", fill="#aaa", font=("Consolas", 7), anchor="e")
            a.create_rectangle(z(28), yy, z(28) + w, yy + z(9), outline="#444", fill="#222")
            mid = z(28) + w / 2
            a.create_line(mid, yy, mid, yy + z(9), fill="#555")
            px = mid + max(-1.0, min(1.0, val)) * (w / 2 - z(2))
            a.create_rectangle(min(mid, px), yy + z(1), max(mid, px), yy + z(8), fill="#5ad", outline="")

        shown = max(len(pad["buttons"]), 8)
        for i, lab in enumerate(self.btn_lamps):
            on = i < len(pad["buttons"]) and pad["buttons"][i]
            lab.configure(bg=ON if on else OFF, fg="#111" if on else "#ccc")
            lab.configure(text=str(i) if i < shown else "")

    def _drive_from_pad(self, pad):
        if self.play_on:
            return
        if self.motors_soft:
            self.drive_lbl.configure(text="mou", fg="#e65100")
            return
        if not self.pad_enable.get():
            self.h_prev = self.h_state[:]
            self.drive_lbl.configure(text="idle")
            return

        dead = float(self.dead_var.get())
        maxspd = int(self.maxspd_var.get())
        speeds = []
        for i in range(6):
            v = joint_axis(self.maps[i], pad)
            if abs(v) < dead:
                sp = 0
            else:
                mag = (abs(v) - dead) / max(1e-6, 1.0 - dead)
                mag = max(0.0, min(1.0, mag))
                sp = int(round(mag * maxspd))
                if v < 0:
                    sp = -sp
                if sp != 0 and abs(sp) < 50:
                    sp = 50 if sp > 0 else -50
            speeds.append(self._limit_speed(i, sp))
        self.h_prev = self.h_state[:]

        if not self.ser or not self.ser.is_open:
            self.drive_lbl.configure(text="pas de COM")
            return
        self._send_speeds(speeds)
        self.drive_lbl.configure(text=self._fmt_speeds(speeds))

    def _limit_speed(self, i, speed):
        if not self.k_vars[i].get():
            self.lock_dir[i] = 0
            return speed
        h = self.h_state[i]
        if h == 0:
            self.lock_dir[i] = 0
            return speed
        if self.h_prev[i] == 0 and speed != 0:
            self.lock_dir[i] = 1 if speed > 0 else -1
        if self.lock_dir[i] != 0 and (
            (speed > 0 and self.lock_dir[i] > 0) or (speed < 0 and self.lock_dir[i] < 0)
        ):
            return 0
        return speed

    def _fmt_speeds(self, speeds):
        bits = [f"J{i + 1}{s:+d}" for i, s in enumerate(speeds) if s]
        return "  ".join(bits) if bits else "idle"

    def _send_speeds(self, speeds, force=False):
        if self.motors_soft:
            return
        speeds = [int(x) for x in speeds]
        if len(speeds) < 6:
            speeds = speeds + [0] * (6 - len(speeds))
        speeds = speeds[:6]
        if not force:
            same = True
            for a, b in zip(speeds, self._pad_last):
                if abs(a - b) >= 80 or (a == 0) != (b == 0):
                    same = False
                    break
            if same:
                return
        if not self.ser or not self.ser.is_open:
            return
        if all(s == 0 for s in speeds):
            self._cmd("s")
        else:
            self._cmd("a" + ",".join(str(s) for s in speeds))
        if self.rec_on:
            self._stamp_clip(speeds)
        self._pad_last = speeds[:]

    def _stamp_clip(self, speeds=None):
        if not self.rec_on:
            return
        t = round(time.time() - self.rec_t0, 3)
        speeds = _six_speeds(self._pad_last if speeds is None else speeds)
        if self.clip:
            last_t, last_s = self.clip[-1]
            if last_s == speeds and abs(last_t - t) < 0.001:
                return
        self.clip.append((t, speeds[:]))
        self._clip_status()

    def _clip_duration(self, clip=None):
        use = self.clip if clip is None else clip
        dur = sum(dt for dt, _ in clip_segments(use))
        if dur > 1e-4:
            return dur
        if use:
            span = float(use[-1][0]) - float(use[0][0])
            if span > 1e-4:
                return span
        if clip is None and self.rec_on:
            return max(0.0, time.time() - self.rec_t0)
        return 0.0

    def _clip_status(self):
        self._suite_refresh()

    def _play_rate(self):
        try:
            r = float(self.play_rate.get())
        except Exception:
            r = 1.0
        if r < 0.5:
            r = 0.5
        if r > PLAY_RATE_MAX:
            r = PLAY_RATE_MAX
        return r

    def _rate_label(self, *_):
        if not hasattr(self, "rate_lbl"):
            return
        r = self._play_rate()
        if self.play_on:
            dur = self._play_remaining_1x()
            if dur > 0:
                self.rate_lbl.configure(text=f"{r:.1f}x  →  {dur / r:.1f} s restant")
            else:
                self.rate_lbl.configure(text=f"{r:.1f}x")
            return
        p = self._program_by_id(self.program_id)
        if p and p.get("id") != START_RETURN_ID:
            dur = self._program_duration(p)
            if dur > 0:
                self.rate_lbl.configure(text=f"{r:.1f}x  →  prog {dur / r:.1f} s")
                return
        dur = self._clip_duration()
        if dur > 0:
            self.rate_lbl.configure(text=f"{r:.1f}x  →  {dur / r:.1f} s")
        else:
            self.rate_lbl.configure(text=f"{r:.1f}x")

    def _on_play_rate_release(self, _e=None):
        p = self._program_by_id(self.program_id)
        if p and p.get("id") != START_RETURN_ID:
            p["play_rate"] = self._play_rate()
        self._library_save()
        self._rate_label()

    def _apply_program_rate(self, pid=None):
        p = self._program_by_id(pid or self.program_id)
        if not p or p.get("id") == START_RETURN_ID:
            return
        rate = p.get("play_rate")
        if rate is None:
            return
        try:
            self.play_rate.set(max(0.5, min(PLAY_RATE_MAX, float(rate))))
        except (TypeError, ValueError, tk.TclError):
            return
        self._rate_label()

    def _step_play_duration(self, step, clip_dur):
        try:
            rep = max(1, int(step.get("repeat") or 1))
        except (TypeError, ValueError):
            rep = 1
        if prog_step_mode(step) == "boomerang":
            return clip_dur * 2 * rep
        return clip_dur * rep

    def _program_duration(self, program=None):
        p = program if isinstance(program, dict) else self._program_by_id(program or self.program_id)
        if not p:
            return 0.0
        total = 0.0
        steps = p.get("steps") if p.get("id") == START_RETURN_ID else None
        if steps is None:
            steps = self._program_resolved_steps(p)
        for st in steps:
            s = self._suite_by_id(st.get("suite_id"))
            if not s:
                continue
            d = self._clip_duration(steps_from_raw(s.get("steps")))
            total += self._step_play_duration(st, d)
        return total

    def _play_remaining_1x(self):
        if not self.play_on:
            return 0.0
        left = max(0.0, self.play_dur - min(self.play_head_1x, self.play_dur or 0.0))
        if self.play_boomerang:
            if self.play_reverse:
                left += self.play_dur
            left += self.play_dur * 2 * max(0, self.play_step_repeat - self.play_cycle)
        elif self.play_step_left > 1:
            left += self.play_dur * (self.play_step_left - 1)
        if self.play_prog_steps and not self.play_one_step:
            for st in self.play_prog_steps[self.play_prog_i + 1 :]:
                s = self._suite_by_id(st.get("suite_id"))
                if not s:
                    continue
                d = self._clip_duration(steps_from_raw(s.get("steps")))
                left += self._step_play_duration(st, d)
        return left

    def _suite_name(self):
        s = self._suite_by_id(self.clip_id)
        return s.get("name", "Suite") if s else "Suite"

    def _suite_set_btns(self, rec, rec_stop, play_once, boom, play_stop, files):
        idle = not self.rec_on and not self.play_on
        has = bool(self._suite_by_id(self.clip_id))
        pairs = (
            (self.btn_rec, rec),
            (self.btn_rec_stop, rec_stop),
            (self.btn_play_once, play_once),
            (self.btn_play_rev, play_once),
            (self.btn_boom, boom),
            (self.btn_play_stop, play_stop),
            (self.btn_home, self._run_can_return() and not self.estop_pressed),
            (self.btn_clear, files and has),
            (self.btn_save, files),
            (self.btn_load, idle),
            (self.btn_rename, idle and has),
        )
        for btn, on in pairs:
            btn.configure(state="normal" if on else "disabled")
        self.suite_list.configure(state="normal" if idle else "disabled")
        if hasattr(self, "rate_scale"):
            self.rate_scale.configure(state="normal")
        self._program_set_enabled(idle)

    def _suite_refresh(self):
        if not hasattr(self, "suite_banner"):
            return
        n = len(self.clip)
        dur = self._clip_duration()
        motion = clip_has_motion(self.clip)
        blink = int(time.time() * 2) % 2 == 0
        rate = self._play_rate()
        name = self._suite_name()
        btn_key = (
            self.rec_on,
            self.play_on,
            self.play_boomerang,
            self.play_mode,
            motion,
            n > 0,
            self.clip_id,
            self._estop_halted,
            self.estop_pressed,
            self.play_program_id,
            self.play_prog_i,
            self._run_can_return(),
        )
        self._rate_label()

        if self.rec_on:
            live = time.time() - self.rec_t0
            changes = max(0, n - 1)
            waiting = not any(any(v != 0 for v in s) for _, s in self.clip)
            if waiting:
                msg = f"●  REC  {name}  {live:.1f} s  —  en attente de mouvement (bouge le bras)"
            else:
                msg = f"●  REC  {name}  {live:.1f} s  —  {changes} changements captures"
            bg = "#e53935" if blink else "#b71c1c"
            fg = "#ffffff"
            title = "ENREGISTREMENT EN COURS"
            detail = "Stop rec pour couper. Les autres suites sont conservees."
            btns = (False, True, False, False, False, False)
            shown = f"{live:.1f}"
            if getattr(self, "_suite_list_live", None) != shown:
                self._suite_list_live = shown
                self._suite_list_update_current(f"● {name}   {live:.1f} s")
        elif self.play_on:
            elapsed = min(self.play_head_1x, self.play_dur)
            tag = f"{rate:.1f}x"
            prog = self._program_by_id(self.play_program_id) if self.play_program_id else None
            prefix = ""
            if prog:
                prefix = (
                    f"{prog.get('name')}  {self.play_prog_i + 1}/{len(self.play_prog_steps)}  ·  "
                )
            if self.play_mode == "home":
                msg = (
                    f"◀  RETOUR DEPART   {elapsed:.1f} / {self.play_dur:.1f} s  ({tag})"
                )
                bg, title = "#e65100", "RETOUR POSITION DE DEPART"
            elif self.play_boomerang:
                cyc = (
                    f"{self.play_cycle}/{self.play_step_repeat}"
                    if prog
                    else str(self.play_cycle)
                )
                if self.play_reverse:
                    msg = (
                        f"◀  {prefix}{name}  RECUL  cycle {cyc}   "
                        f"{elapsed:.1f} / {self.play_dur:.1f} s  ({tag})"
                    )
                    bg, title = "#0d47a1", "PROGRAMME — RECUL" if prog else "BOOMERANG — RECUL"
                else:
                    msg = (
                        f"▶  {prefix}{name}  AVANCE  cycle {cyc}   "
                        f"{elapsed:.1f} / {self.play_dur:.1f} s  ({tag})"
                    )
                    bg, title = "#1b5e20", "PROGRAMME — AVANCE" if prog else "BOOMERANG — AVANCE"
            elif self.play_mode == "inverse":
                left = f"  ({self.play_step_left} restant)" if prog and self.play_step_left > 1 else ""
                msg = (
                    f"◀  {prefix}{name}  INVERSE{left}   "
                    f"{elapsed:.1f} / {self.play_dur:.1f} s  ({tag})"
                )
                bg, title = "#0d47a1", "PROGRAMME — INVERSE" if prog else "LECTURE INVERSE"
            else:
                left = f"  ({self.play_step_left} restant)" if prog and self.play_step_left > 1 else ""
                msg = f"▶  {prefix}{name}  1x{left}   {elapsed:.1f} / {self.play_dur:.1f} s  ({tag})"
                bg, title = "#6a1b9a", "PROGRAMME — 1x" if prog else "LECTURE 1x"
            fg = "#ffffff"
            detail = (
                "Retour depart inverse le chemin deja joue, meme pendant le mouvement. "
                "E-stop coupe les moteurs sans rentrer tout seul."
            )
            btns = (False, False, False, False, True, False)
        elif self._estop_halted:
            msg = "E-stop : moteurs coupes. Ca ne revient pas tout seul."
            if self._run_can_return() and not self.estop_pressed:
                msg += "  Retour depart inverse le chemin deja parcouru."
            elif self._run_can_return():
                msg += "  Relache l'E-stop puis Retour depart."
            bg, fg = "#ffebee", "#b71c1c"
            title = "Lecture arretee par E-stop"
            detail = "Relacher l'E-stop ne relance pas. Retour depart ramene a la pose du debut de lecture."
            btns = (True, False, True, True, False, True)
        elif self._run_can_return():
            msg = "Chemin en memoire. Retour depart inverse les actions jusqu'a la pose de debut de lecture."
            bg, fg = "#fff3e0", "#e65100"
            title = "Retour au depart disponible"
            detail = "E-stop coupe seulement les moteurs. Tu peux aussi lancer Retour depart pendant un play."
            btns = (True, False, True, True, False, True)
        elif motion:
            msg = (
                f"{name} prete : {dur:.1f} s aller  →  {dur / rate:.1f} s a {rate:.1f}x.  "
                "Inverse = reverse une fois.  Boomerang recule puis avance."
            )
            bg, fg = "#e8f5e9", "#1b5e20"
            title = "Suite de mouvements — prete"
            detail = f"{n} pas  ·  {len(self.library)} suite(s)  ·  Nouvelle rec n'ecrase pas les autres"
            btns = (True, False, True, True, False, True)
        elif n:
            msg = "Rien capture. Recommence : Nouvelle rec → bouger le bras → Stop rec."
            bg, fg = "#fff8e1", "#e65100"
            title = "Suite de mouvements"
            detail = "Le clip n'a aucun mouvement."
            btns = (True, False, False, False, False, True)
        else:
            msg = "Pret. Clique Nouvelle rec, bouge le bras, puis Stop rec."
            bg, fg = "#eeeeee", "#333333"
            title = "Suite de mouvements"
            detail = f"{len(self.library)} suite(s) en memoire" if self.library else "aucune suite"
            btns = (True, False, False, False, False, False)

        if btn_key != self._suite_btn_key:
            self._suite_btn_key = btn_key
            self._suite_set_btns(*btns)
        self.rec_fr.configure(text=title)
        self.suite_banner.configure(text=msg, bg=bg, fg=fg)
        self.rec_lbl.configure(text=detail)

    def _clip_write_file(self, path):
        payload = {
            "name": self._suite_name(),
            "steps": [{"t": t, "s": s} for t, s in self.clip],
            "duration": round(self._clip_duration(), 3),
        }
        with open(path, "w", encoding="utf-8") as f:
            json.dump(payload, f, indent=2)

    def _suite_by_id(self, sid):
        if not sid:
            return None
        for s in self.library:
            if s.get("id") == sid:
                return s
        return None

    def _next_suite_name(self):
        names = {str(s.get("name") or "") for s in self.library}
        n = 1
        while f"Suite {n}" in names:
            n += 1
        return f"Suite {n}"

    def _new_suite_id(self):
        base = time.strftime("%Y%m%d-%H%M%S")
        sid = base
        n = 1
        while self._suite_by_id(sid):
            n += 1
            sid = f"{base}-{n}"
        return sid

    def _sync_clip_to_library(self):
        s = self._suite_by_id(self.clip_id)
        if not s:
            return
        s["steps"] = [{"t": t, "s": list(sp)} for t, sp in self.clip]
        s["duration"] = round(self._clip_duration(), 3)

    def _library_save(self):
        self._sync_clip_to_library()
        try:
            payload = {
                "selected": self.clip_id,
                "play_rate": self._play_rate(),
                "start_suite_id": self.start_suite_id,
                "start_program_id": self.start_program_id,
                "start_boomerang": bool(self.start_boom.get()),
                "start_invert": bool(self.start_invert.get()),
                "suites": self.library,
                "programs": self.programs,
                "selected_program": self.program_id,
            }
            with open(LIBRARY_FILE, "w", encoding="utf-8") as f:
                json.dump(payload, f, indent=2)
        except Exception:
            pass

    def _apply_suite(self, sid):
        s = self._suite_by_id(sid)
        if not s:
            return
        self.clip_id = sid
        self.clip = steps_from_raw(s.get("steps"))

    def _suite_list_update_current(self, text):
        if not hasattr(self, "suite_list") or not self.clip_id:
            return
        if self.clip_id not in getattr(self, "_suite_ids", []):
            return
        i = self._suite_ids.index(self.clip_id)
        prev = str(self.suite_list.cget("state"))
        self._selecting_suite = True
        try:
            self.suite_list.configure(state="normal")
            self.suite_list.delete(i)
            self.suite_list.insert(i, text)
            self.suite_list.selection_clear(0, "end")
            self.suite_list.selection_set(i)
        finally:
            if prev == "disabled":
                self.suite_list.configure(state="disabled")
            self._selecting_suite = False

    def _refresh_suite_list(self):
        if not hasattr(self, "suite_list"):
            return
        self._selecting_suite = True
        prev = str(self.suite_list.cget("state"))
        try:
            self.suite_list.configure(state="normal")
            self.suite_list.delete(0, "end")
            self._suite_ids = []
            for s in self.library:
                if s.get("id") == self.clip_id:
                    clip = self.clip
                    dur = self._clip_duration(clip)
                    if self.rec_on:
                        dur = max(dur, time.time() - self.rec_t0)
                else:
                    clip = steps_from_raw(s.get("steps"))
                    dur = self._clip_duration(clip)
                    stored = s.get("duration")
                    if dur <= 0 and stored:
                        try:
                            dur = float(stored)
                        except (TypeError, ValueError):
                            pass
                mark = "● " if s.get("id") == self.clip_id else "    "
                self.suite_list.insert("end", f"{mark}{s.get('name', '?')}   {dur:.1f} s")
                self._suite_ids.append(s.get("id"))
            if self.clip_id in self._suite_ids:
                i = self._suite_ids.index(self.clip_id)
                self.suite_list.selection_clear(0, "end")
                self.suite_list.selection_set(i)
                self.suite_list.see(i)
        finally:
            if prev == "disabled":
                self.suite_list.configure(state="disabled")
            self._selecting_suite = False
        self._refresh_start_combo()
        self._refresh_program_combo()
        self._refresh_program_steps()

    def _on_suite_select(self, _evt=None):
        if self._selecting_suite or self.rec_on or self.play_on:
            return
        cur = self.suite_list.curselection()
        if not cur:
            return
        i = int(cur[0])
        if i < 0 or i >= len(self._suite_ids):
            return
        sid = self._suite_ids[i]
        if sid == self.clip_id:
            return
        self._apply_suite(sid)
        self._library_save()
        self._refresh_suite_list()
        self._clip_status()

    def _library_autoload(self):
        self.library = []
        self.clip = []
        self.clip_id = None
        self.programs = []
        self.program_id = None
        self.start_program_id = None
        if os.path.isfile(LIBRARY_FILE):
            try:
                with open(LIBRARY_FILE, "r", encoding="utf-8") as f:
                    raw = json.load(f)
                self.library = list(raw.get("suites") or [])
                rate = raw.get("play_rate")
                if rate is not None:
                    self.play_rate.set(max(0.5, min(PLAY_RATE_MAX, float(rate))))
                sel = raw.get("selected")
                if sel and self._suite_by_id(sel):
                    self._apply_suite(sel)
                elif self.library:
                    self._apply_suite(self.library[0]["id"])
                sid = raw.get("start_suite_id")
                self.start_suite_id = sid if self._suite_by_id(sid) else None
                self.programs = list(raw.get("programs") or [])
                pid = raw.get("selected_program")
                self.program_id = pid if self._program_by_id(pid) else (
                    self.programs[0]["id"] if self.programs else None
                )
                spid = raw.get("start_program_id")
                self.start_program_id = spid if self._program_by_id(spid) else None
                if self.start_program_id:
                    self.start_suite_id = None
                if "start_boomerang" in raw:
                    self.start_boom.set(1 if raw.get("start_boomerang") else 0)
                if "start_invert" in raw:
                    self.start_invert.set(1 if raw.get("start_invert") else 0)
            except Exception:
                self.library = []
                self.clip = []
                self.clip_id = None
                self.programs = []
                self.program_id = None
                self.start_program_id = None
        if not self.library and os.path.isfile(CLIP_FILE):
            try:
                with open(CLIP_FILE, "r", encoding="utf-8") as f:
                    raw = json.load(f)
                clip = steps_from_raw(raw.get("steps", raw))
                if clip_has_motion(clip):
                    sid = "imported-last"
                    self.library = [
                        {
                            "id": sid,
                            "name": raw.get("name") or "Suite 1",
                            "steps": [{"t": t, "s": s} for t, s in clip],
                            "duration": round(self._clip_duration(clip), 3),
                        }
                    ]
                    self.clip_id = sid
                    self.clip = clip
                    self._library_save()
            except Exception:
                pass
        self._refresh_suite_list()
        self._apply_program_rate(self.program_id)
        self._clip_status()

    def _rec_start(self):
        if not self.ser or not self.ser.is_open:
            messagebox.showerror("Enregistrer", "Connecte le COM d'abord.")
            return
        if self.estop_pressed:
            messagebox.showerror("Enregistrer", "E-stop presse. Relache-le d'abord.")
            return
        self._play_stop()
        self._estop_halted = False
        name = self._next_suite_name()
        sid = self._new_suite_id()
        self.clip_id = sid
        self.clip = []
        self.library.append({"id": sid, "name": name, "steps": [], "duration": 0.0})
        self.rec_on = True
        self.rec_t0 = time.time()
        self.pad_enable.set(1)
        self.clip.append((0.0, _six_speeds(self._pad_last)))
        self._refresh_suite_list()
        self._clip_status()

    def _rec_stop(self):
        if not self.rec_on:
            self._clip_status()
            return
        self._stamp_clip()
        self.rec_on = False
        self._suite_list_live = None
        self._send_speeds([0] * 6, force=True)
        if not clip_has_motion(self.clip):
            self.library = [x for x in self.library if x.get("id") != self.clip_id]
            if self.library:
                self._apply_suite(self.library[-1]["id"])
            else:
                self.clip_id = None
                self.clip = []
        else:
            self._sync_clip_to_library()
            try:
                self._clip_write_file(CLIP_FILE)
            except Exception:
                pass
        self._library_save()
        self._clip_status()
        self._refresh_suite_list()

    def _run_reset(self):
        self.run_executed = []
        self.run_pass_segs = []

    def _run_can_return(self):
        if self.rec_on:
            return False
        if self.play_on and self.play_mode == "home":
            return False
        if self.run_executed:
            return True
        if self.play_on and self.run_pass_segs and self.play_head_1x > 0.05:
            return True
        return False

    def _play_capture_run(self):
        if not self.run_pass_segs:
            return
        if self.play_mode == "home":
            remaining = slice_segments_from(self.run_pass_segs, self.play_head_1x)
            self.run_executed = reverse_segments(remaining)
        elif self.play_head_1x + 1e-4 >= (self.play_dur or 0.0):
            self.run_executed.extend(self.run_pass_segs)
        else:
            self.run_executed.extend(slice_segments_until(self.run_pass_segs, self.play_head_1x))
        self.run_pass_segs = []

    def _play_begin_pass(self, reverse):
        self._play_capture_run()
        segs = clip_segments(self.clip)
        if reverse:
            segs = reverse_segments(segs)
        self.run_pass_segs = [(dt, list(s)) for dt, s in segs]
        self.play_steps, self.play_dur = timeline_from_segments(segs)
        self.play_reverse = reverse
        self.play_i = 0
        self.play_t0 = time.time()
        self.play_head_1x = 0.0
        self._play_last_wall = time.time()
        if not self.play_steps:
            if self.play_program_id and not self.play_one_step:
                self._program_advance()
            else:
                self._play_stop(keep_run=True)
            return
        self._play_apply(self.play_steps[0][1], force=True)
        self._clip_status()

    def _start_lamp(self, on):
        if not hasattr(self, "start_lamp"):
            return
        if on:
            self.start_lamp.configure(bg=ON, fg="#111")
        else:
            self.start_lamp.configure(bg=OFF, fg="#ccc")

    def _start_seq_steps(self):
        p = self._program_by_id(self.start_program_id)
        if not p or p.get("id") == START_RETURN_ID:
            return []
        return self._program_resolved_steps(p)

    def _group_start_steps(self, steps):
        groups = []
        for st in steps or []:
            if groups and st.get("chain"):
                groups[-1].append(st)
            else:
                groups.append([st])
        return groups

    def _start_group_label(self, group):
        if not group:
            return "?"
        if len(group) == 1:
            st = group[0]
            mode = PROG_MODE_TO_LABEL.get(prog_step_mode(st), "1x")
            return f"{st.get('name')}  ({mode})"
        names = " + ".join(str(st.get("name") or "?") for st in group)
        return f"{len(group)} etapes d'un coup  ({names})"

    def _start_seq_reset(self):
        self.start_seq_i = 0
        self._refresh_start_seq_label()
        self._log("START : sequence remise a la 1re pression")

    def _refresh_start_combo(self):
        if not hasattr(self, "start_combo"):
            self._refresh_start_seq_label()
            return
        labels = ["(aucun)"]
        ids = [None]
        for p in self.programs:
            labels.append(str(p.get("name") or "?"))
            ids.append(p.get("id"))
        self._start_combo_ids = ids
        self.start_combo["values"] = labels
        if self.start_program_id and self.start_program_id in ids:
            i = ids.index(self.start_program_id)
            self.start_combo_var.set(labels[i])
        else:
            if self.start_program_id:
                self.start_seq_i = 0
            self.start_program_id = None
            self.start_combo_var.set("(aucun)")
        self._refresh_start_seq_label()

    def _refresh_start_seq_label(self):
        if not hasattr(self, "start_next_lbl"):
            return
        p = self._program_by_id(self.start_program_id)
        if not p or p.get("id") == START_RETURN_ID:
            self.start_seq_i = 0
            self.start_next_lbl.configure(text="Prochaine pression : assigne un programme")
            return
        pname = p.get("name") or "Programme"
        groups = self._group_start_steps(self._start_seq_steps())
        n = len(groups)
        if n == 0:
            self.start_seq_i = 0
            self.start_next_lbl.configure(text=f"{pname}  ·  (aucune etape jouable)")
            return
        if self.start_seq_i < 0 or self.start_seq_i > n:
            self.start_seq_i = 0
        i = self.start_seq_i
        if i >= n:
            self.start_next_lbl.configure(
                text=f"{pname}  ·  termine  ·  prochaine = 1/{n}  {self._start_group_label(groups[0])}"
            )
            return
        self.start_next_lbl.configure(
            text=f"{pname}  ·  {i + 1}/{n}  {self._start_group_label(groups[i])}"
        )

    def _on_start_combo(self, _evt=None):
        i = self.start_combo.current()
        if i < 0 or i >= len(self._start_combo_ids):
            return
        pid = self._start_combo_ids[i]
        if pid != self.start_program_id:
            self.start_seq_i = 0
        self.start_program_id = pid
        if pid:
            self._apply_program_rate(pid)
        self._refresh_start_seq_label()
        self._library_save()

    def _on_start_invert(self):
        self._start_seen = False
        self._library_save()

    def _start_assign_program(self):
        p = self._program_by_id(self.program_id)
        if not p or p.get("id") == START_RETURN_ID:
            messagebox.showinfo("START", "Cree ou selectionne un programme d'abord.")
            return
        if not self._program_resolved_steps(p):
            messagebox.showinfo("START", "Le programme n'a aucune etape jouable.")
            return
        if p["id"] != self.start_program_id:
            self.start_seq_i = 0
        self.start_program_id = p["id"]
        self._apply_program_rate(p["id"])
        self._refresh_start_combo()
        self._library_save()
        self._log(f"START assigne au programme « {p.get('name')} »")

    def _on_start_button(self):
        now = time.time()
        if now < self._start_ignore_until:
            return
        self._start_ignore_until = now + 0.35
        if self.rec_on:
            self._log("START ignore : enregistrement en cours")
            return
        if self.play_on:
            self._log("START ignore : deja en lecture")
            return
        if self.estop_pressed:
            self._log("START ignore : E-stop")
            return
        if not self.ser or not self.ser.is_open:
            self._log("START ignore : pas de COM")
            return
        p = self._program_by_id(self.start_program_id)
        if not p or p.get("id") == START_RETURN_ID:
            self._log("START : aucun programme assigne")
            return
        groups = self._group_start_steps(self._start_seq_steps())
        if not groups:
            self._log("START : programme vide")
            return
        n = len(groups)
        if self.start_seq_i < 0 or self.start_seq_i >= n:
            self.start_seq_i = 0
        if self.start_seq_i == 0:
            self._apply_program_rate(p["id"])
            self._run_reset()
        group = groups[self.start_seq_i]
        self._log(f"START : {self.start_seq_i + 1}/{n}  {self._start_group_label(group)}")
        self._estop_halted = False
        self.play_one_step = False
        self.play_program_id = p["id"]
        self.play_prog_steps = group
        self.play_prog_i = 0
        self._program_launch_current()
        if self.play_on:
            self.start_seq_i += 1
            self._refresh_start_seq_label()

    def _on_estop(self):
        stopped = False
        if self.play_on:
            self._estop_halted = True
            self._play_stop(keep_run=True)
            self.pad_enable.set(0)
            stopped = True
        if self.rec_on:
            self._rec_stop()
            stopped = True
        if stopped:
            self._send_speeds([0] * 6, force=True)
            self._log("E-stop : moteurs coupes. Retour depart pour inverser le chemin.")
            self._clip_status()

    def _play_start(self, boomerang=False, from_button=False, reverse=False):
        if self.rec_on:
            if from_button:
                self._log("START ignore : enregistrement en cours")
                return False
            self._rec_stop()
        if self.play_on and from_button:
            self._log("START ignore : deja en lecture")
            return False
        if self.estop_pressed:
            if from_button:
                self._log("START ignore : E-stop")
            else:
                messagebox.showerror("Clip", "E-stop presse. Relache-le avant de relancer.")
            return False
        if self.motors_soft:
            if from_button:
                self._log("START ignore : bras mou")
            else:
                messagebox.showinfo("Clip", "Bras mou. Clique Tenir avant de rejouer.")
            return False
        if not clip_has_motion(self.clip):
            if from_button:
                self._log("START ignore : suite vide")
            else:
                messagebox.showinfo(
                    "Clip",
                    "Aucun mouvement dans la suite. Enregistre, bouge le bras, puis Stop rec.",
                )
            return False
        if not self.ser or not self.ser.is_open:
            if from_button:
                self._log("START ignore : pas de COM")
            else:
                messagebox.showerror("Clip", "Connecte le COM d'abord.")
            return False
        self._estop_halted = False
        self._run_reset()
        self.play_one_step = False
        self.play_program_id = None
        self.play_prog_steps = []
        self.play_prog_i = 0
        self.play_step_repeat = 1
        self.play_step_left = 1
        self.play_on = True
        if boomerang:
            self.play_mode = "boomerang"
        elif reverse:
            self.play_mode = "inverse"
        else:
            self.play_mode = "1x"
        self.play_boomerang = bool(boomerang)
        self.play_cycle = 1
        # Apres l'enregistrement le bras est en bout de course :
        # boomerang et Inverse commencent par le miroir (reculer).
        self._play_begin_pass(reverse=bool(boomerang or reverse))
        return True

    def _play_stop(self, keep_run=False):
        if keep_run:
            self._play_capture_run()
        else:
            self._run_reset()
        was = self.play_on
        self.play_on = False
        self.play_reverse = False
        self.play_mode = "1x"
        self.play_steps = []
        self.play_dur = 0.0
        self.play_head_1x = 0.0
        self.play_program_id = None
        self.play_prog_steps = []
        self.play_prog_i = 0
        self.play_step_repeat = 1
        self.play_step_left = 1
        self.play_one_step = False
        if was:
            self._send_speeds([0] * 6, force=True)
            self.drive_lbl.configure(text="idle")
        self._clip_status()
        self._refresh_start_seq_label()

    def _play_return_home(self):
        if self.rec_on:
            self._log("Retour depart ignore : enregistrement en cours")
            return
        if self.estop_pressed:
            messagebox.showerror(
                "Retour depart",
                "Relache l'E-stop d'abord. L'E-stop coupe les moteurs, il ne rentre pas tout seul.",
            )
            return
        if self.motors_soft:
            messagebox.showinfo("Retour depart", "Bras mou. Clique Tenir avant de bouger.")
            return
        if not self.ser or not self.ser.is_open:
            messagebox.showerror("Retour depart", "Connecte le COM d'abord.")
            return
        if self.play_on and self.play_mode == "home":
            return
        self._play_capture_run()
        segs = reverse_segments(self.run_executed)
        if not any(any(v != 0 for v in s) for _, s in segs):
            messagebox.showinfo(
                "Retour depart",
                "Rien a inverser : le bras est deja a la pose de debut de cette lecture.",
            )
            self._run_reset()
            self._clip_status()
            return
        self._estop_halted = False
        self.play_one_step = True
        self.play_program_id = None
        self.play_prog_steps = []
        self.play_prog_i = 0
        self.play_step_repeat = 1
        self.play_step_left = 1
        self.play_boomerang = False
        self.play_mode = "home"
        self.play_on = True
        self.play_cycle = 1
        self.run_executed = []
        self.run_pass_segs = [(dt, list(s)) for dt, s in segs]
        self.play_steps, self.play_dur = timeline_from_segments(segs)
        self.play_reverse = True
        self.play_i = 0
        self.play_t0 = time.time()
        self.play_head_1x = 0.0
        self._play_last_wall = time.time()
        self._play_apply(self.play_steps[0][1], force=True)
        self._log(f"Retour depart : {self.play_dur:.1f} s a {self._play_rate():.1f}x")
        self._clip_status()

    def _play_apply(self, speeds, force=False):
        rate = self._play_rate()
        if getattr(self, "_play_rate_used", None) != rate:
            force = True
            self._play_rate_used = rate
        scaled = scale_speeds(speeds, rate)
        out = [self._limit_speed(i, int(sp)) for i, sp in enumerate(scaled)]
        self.h_prev = self.h_state[:]
        self._send_speeds(out, force=force)
        arrow = "← " if self.play_reverse else "→ "
        self.drive_lbl.configure(text=arrow + self._fmt_speeds(out))

    def _play_tick(self):
        if not self.play_on or not self.play_steps:
            return False
        if self.estop_pressed:
            self._on_estop()
            return False
        now = time.time()
        rate = self._play_rate()
        self.play_head_1x += (now - self._play_last_wall) * rate
        self._play_last_wall = now
        elapsed = self.play_head_1x
        while self.play_i + 1 < len(self.play_steps) and self.play_steps[self.play_i + 1][0] <= elapsed:
            self.play_i += 1
        self._play_apply(self.play_steps[self.play_i][1])
        if elapsed >= self.play_dur:
            if self.play_mode == "home":
                self._play_capture_run()
                self.play_on = False
                self.play_mode = "1x"
                self._send_speeds([0] * 6, force=True)
                self.drive_lbl.configure(text="idle")
                self.start_seq_i = 0
                self._refresh_start_seq_label()
                self._log("Retour depart termine")
                self._clip_status()
                return False
            if self.play_boomerang:
                if self.play_reverse:
                    self._play_begin_pass(reverse=False)
                else:
                    if self.play_program_id and self.play_cycle >= self.play_step_repeat:
                        return self._play_finish_step()
                    self.play_cycle += 1
                    self._play_begin_pass(reverse=True)
            elif self.play_program_id:
                self.play_step_left -= 1
                if self.play_step_left > 0:
                    self._play_begin_pass(reverse=(self.play_mode == "inverse"))
                else:
                    return self._play_finish_step()
            else:
                self._play_stop(keep_run=True)
                return False
        return True

    def _program_by_id(self, pid):
        if not pid:
            return None
        if pid == START_RETURN_ID:
            return {"id": pid, "name": "Retour repos"}
        for p in self.programs:
            if p.get("id") == pid:
                return p
        return None

    def _new_program_id(self):
        base = "prog-" + time.strftime("%Y%m%d-%H%M%S")
        pid = base
        n = 1
        while self._program_by_id(pid):
            n += 1
            pid = f"{base}-{n}"
        return pid

    def _next_program_name(self):
        names = {str(p.get("name") or "") for p in self.programs}
        n = 1
        while f"Programme {n}" in names:
            n += 1
        return f"Programme {n}"

    def _program_set_enabled(self, idle):
        if not hasattr(self, "prog_combo"):
            return
        has = bool(self._program_by_id(self.program_id))
        combo_state = "readonly" if idle else "disabled"
        self.prog_combo.configure(state=combo_state)
        self.prog_steps_list.configure(state="normal" if idle else "disabled")
        self.prog_repeat_spin.configure(state="normal" if idle and has else "disabled")
        self.prog_mode_combo.configure(state="readonly" if idle and has else "disabled")
        i = self._current_prog_step_index() if idle and has else None
        if hasattr(self, "chk_prog_chain"):
            self.chk_prog_chain.configure(
                state="normal" if idle and has and i is not None and i > 0 else "disabled"
            )
        pairs = (
            (self.btn_prog_new, idle),
            (self.btn_prog_rename, idle and has),
            (self.btn_prog_del, idle and has),
            (self.btn_prog_add, idle),
            (self.btn_prog_up, idle and has),
            (self.btn_prog_down, idle and has),
            (self.btn_prog_remove, idle and has),
            (self.btn_prog_play, idle and has),
            (self.btn_prog_start, idle and has),
        )
        for btn, on in pairs:
            btn.configure(state="normal" if on else "disabled")

    def _format_prog_step(self, step, index=0, press=1):
        s = self._suite_by_id(step.get("suite_id"))
        name = s.get("name") if s else "(suite absente)"
        try:
            rep = max(1, int(step.get("repeat") or 1))
        except (TypeError, ValueError):
            rep = 1
        mode = prog_step_mode(step)
        if mode == "boomerang":
            how = f"boom ×{rep}"
        elif mode == "inverse":
            how = "inverse" + (f" ×{rep}" if rep > 1 else "")
        elif rep > 1:
            how = f"1x ×{rep}"
        else:
            how = "1x"
        chain = bool(step.get("chain")) and index > 0
        lead = f"IN1 {press}" if not chain else "      "
        mark = "  ↳ meme IN1" if chain else ""
        return f"{lead}  {index + 1}. {name}   ·  {how}{mark}"

    def _refresh_program_combo(self):
        if not hasattr(self, "prog_combo"):
            return
        self._selecting_program = True
        try:
            labels = []
            ids = []
            for p in self.programs:
                labels.append(str(p.get("name") or "?"))
                ids.append(p.get("id"))
            self._prog_ids = ids
            self.prog_combo["values"] = labels or ["(aucun)"]
            if self.program_id in ids:
                i = ids.index(self.program_id)
                self.prog_combo_var.set(labels[i])
                self.prog_combo.current(i)
            elif ids:
                self.program_id = ids[0]
                self.prog_combo_var.set(labels[0])
                self.prog_combo.current(0)
            else:
                self.program_id = None
                self.prog_combo_var.set("(aucun)")
        finally:
            self._selecting_program = False

    def _refresh_program_steps(self, select=None):
        if not hasattr(self, "prog_steps_list"):
            return
        self._selecting_prog_step = True
        try:
            cur = self.prog_steps_list.curselection()
            keep = int(cur[0]) if cur else 0
            self.prog_steps_list.delete(0, "end")
            p = self._program_by_id(self.program_id)
            steps = list((p or {}).get("steps") or [])
            if steps:
                steps[0]["chain"] = False
            press = 0
            for i, st in enumerate(steps):
                if i == 0 or not st.get("chain"):
                    press += 1
                self.prog_steps_list.insert(
                    "end", self._format_prog_step(st, index=i, press=press)
                )
            if not steps:
                return
            if select == -1:
                idx = len(steps) - 1
            elif select is not None:
                idx = max(0, min(int(select), len(steps) - 1))
            else:
                idx = max(0, min(keep, len(steps) - 1))
            self.prog_steps_list.selection_clear(0, "end")
            self.prog_steps_list.selection_set(idx)
            self.prog_steps_list.see(idx)
            self._load_prog_step_editor(idx)
        finally:
            self._selecting_prog_step = False

    def _current_prog_step_index(self):
        cur = self.prog_steps_list.curselection()
        if not cur:
            return None
        i = int(cur[0])
        p = self._program_by_id(self.program_id)
        steps = list((p or {}).get("steps") or [])
        if i < 0 or i >= len(steps):
            return None
        return i

    def _load_prog_step_editor(self, idx):
        p = self._program_by_id(self.program_id)
        steps = list((p or {}).get("steps") or [])
        if idx is None or idx < 0 or idx >= len(steps):
            return
        st = steps[idx]
        self._syncing_prog_step = True
        try:
            mode = prog_step_mode(st)
            self.prog_mode_var.set(PROG_MODE_TO_LABEL.get(mode, "1x"))
            try:
                self.prog_repeat.set(max(1, min(20, int(st.get("repeat") or 1))))
            except (TypeError, ValueError, tk.TclError):
                self.prog_repeat.set(1)
            self.prog_repeat_lbl.configure(text="cycles boom" if mode == "boomerang" else "fois")
            self.prog_chain.set(1 if idx > 0 and st.get("chain") else 0)
            if hasattr(self, "chk_prog_chain"):
                self.chk_prog_chain.configure(state="normal" if idx > 0 else "disabled")
        finally:
            self._syncing_prog_step = False

    def _on_program_combo(self, _evt=None):
        if self._selecting_program or self.rec_on or self.play_on:
            return
        i = self.prog_combo.current()
        if i < 0 or i >= len(self._prog_ids):
            return
        pid = self._prog_ids[i]
        if pid == self.program_id:
            return
        self.program_id = pid
        self._apply_program_rate(pid)
        self._library_save()
        self._refresh_program_steps(select=0)

    def _on_prog_step_select(self, _evt=None):
        if self._selecting_prog_step or self.rec_on or self.play_on:
            return
        i = self._current_prog_step_index()
        if i is None:
            return
        self._load_prog_step_editor(i)

    def _on_prog_step_edit(self, _evt=None):
        if self._syncing_prog_step or self.rec_on or self.play_on:
            return
        p = self._program_by_id(self.program_id)
        i = self._current_prog_step_index()
        if not p or i is None:
            return
        try:
            rep = int(self.prog_repeat.get())
        except (TypeError, ValueError, tk.TclError):
            rep = 1
        rep = max(1, min(20, rep))
        mode = PROG_MODE_FROM_LABEL.get(self.prog_mode_var.get(), "1x")
        prev_mode = prog_step_mode(p["steps"][i])
        try:
            prev_rep = max(1, int(p["steps"][i].get("repeat") or 1))
        except (TypeError, ValueError):
            prev_rep = 1
        chain = bool(self.prog_chain.get()) and i > 0
        prev_chain = bool(p["steps"][i].get("chain"))
        if mode == prev_mode and rep == prev_rep and chain == prev_chain:
            self.prog_repeat_lbl.configure(text="cycles boom" if mode == "boomerang" else "fois")
            return
        p["steps"][i]["mode"] = mode
        p["steps"][i]["boomerang"] = mode == "boomerang"
        p["steps"][i]["repeat"] = rep
        p["steps"][i]["chain"] = chain
        if int(self.prog_repeat.get()) != rep:
            self._syncing_prog_step = True
            try:
                self.prog_repeat.set(rep)
            finally:
                self._syncing_prog_step = False
        self.prog_repeat_lbl.configure(text="cycles boom" if mode == "boomerang" else "fois")
        self._library_save()
        self._refresh_program_steps(select=i)

    def _program_new(self):
        pid = self._new_program_id()
        name = self._next_program_name()
        self.programs.append({"id": pid, "name": name, "steps": []})
        self.program_id = pid
        self._library_save()
        self._refresh_start_combo()
        self._refresh_program_combo()
        self._refresh_program_steps()
        self._clip_status()

    def _program_rename(self):
        p = self._program_by_id(self.program_id)
        if not p:
            return
        name = simpledialog.askstring(
            "Renommer",
            "Nom du programme :",
            initialvalue=p.get("name") or "",
            parent=self,
        )
        if name is None:
            return
        name = name.strip()
        if not name:
            return
        p["name"] = name
        self._library_save()
        self._refresh_start_combo()
        self._refresh_program_combo()

    def _program_delete(self):
        p = self._program_by_id(self.program_id)
        if not p:
            return
        if not messagebox.askyesno("Supprimer", f"Supprimer le programme « {p.get('name')} » ?"):
            return
        deleted = p["id"]
        self.programs = [x for x in self.programs if x.get("id") != deleted]
        if self.start_program_id == deleted:
            self.start_program_id = None
            self.start_seq_i = 0
        self.program_id = self.programs[0]["id"] if self.programs else None
        self._library_save()
        self._refresh_start_combo()
        self._refresh_program_combo()
        self._refresh_program_steps()
        self._clip_status()

    def _program_add_suite(self):
        if self.rec_on or self.play_on:
            return
        if not self._suite_by_id(self.clip_id):
            messagebox.showinfo("Programme", "Selectionne une suite dans « Mes suites ».")
            return
        if not self._program_by_id(self.program_id):
            self._program_new()
        p = self._program_by_id(self.program_id)
        if not p:
            return
        p.setdefault("steps", []).append(
            {
                "suite_id": self.clip_id,
                "mode": "1x",
                "boomerang": False,
                "repeat": 1,
                "chain": False,
            }
        )
        self._library_save()
        self._refresh_program_steps(select=-1)

    def _program_step_move(self, delta):
        p = self._program_by_id(self.program_id)
        i = self._current_prog_step_index()
        if not p or i is None:
            return
        j = i + delta
        steps = p.get("steps") or []
        if j < 0 or j >= len(steps):
            return
        steps[i], steps[j] = steps[j], steps[i]
        if steps:
            steps[0]["chain"] = False
        self._library_save()
        self._refresh_program_steps(select=j)

    def _program_step_up(self):
        self._program_step_move(-1)

    def _program_step_down(self):
        self._program_step_move(1)

    def _program_step_remove(self):
        if self.rec_on or self.play_on:
            return
        p = self._program_by_id(self.program_id)
        i = self._current_prog_step_index()
        if not p or i is None:
            return
        del p["steps"][i]
        if p["steps"]:
            p["steps"][0]["chain"] = False
        self._library_save()
        self._refresh_program_steps(select=min(i, max(0, len(p["steps"]) - 1)))

    def _program_resolved_steps(self, program):
        out = []
        for st in program.get("steps") or []:
            s = self._suite_by_id(st.get("suite_id"))
            if not s:
                continue
            clip = steps_from_raw(s.get("steps"))
            if not clip_has_motion(clip):
                continue
            try:
                rep = max(1, min(20, int(st.get("repeat") or 1)))
            except (TypeError, ValueError):
                rep = 1
            mode = prog_step_mode(st)
            out.append(
                {
                    "suite_id": s["id"],
                    "name": s.get("name") or "Suite",
                    "mode": mode,
                    "boomerang": mode == "boomerang",
                    "repeat": rep,
                    "chain": bool(st.get("chain")) and bool(out),
                }
            )
        return out

    def _program_play_click(self):
        self._program_play(pid=self.program_id, from_button=False)

    def _program_play(self, pid=None, from_button=False):
        if self.rec_on:
            if from_button:
                self._log("START ignore : enregistrement en cours")
                return
            self._rec_stop()
        if self.play_on and from_button:
            self._log("START ignore : deja en lecture")
            return
        if self.estop_pressed:
            if from_button:
                self._log("START ignore : E-stop")
            else:
                messagebox.showerror("Programme", "E-stop presse. Relache-le avant de relancer.")
            return
        if not self.ser or not self.ser.is_open:
            if from_button:
                self._log("START ignore : pas de COM")
            else:
                messagebox.showerror("Programme", "Connecte le COM d'abord.")
            return
        p = self._program_by_id(pid)
        if not p:
            if from_button:
                self._log("START ignore : programme introuvable")
            else:
                messagebox.showinfo("Programme", "Cree ou selectionne un programme.")
            return
        steps = self._program_resolved_steps(p)
        if not steps:
            if from_button:
                self._log("START ignore : programme vide")
            else:
                messagebox.showinfo(
                    "Programme",
                    "Aucune etape jouable. Ajoute des suites avec du mouvement.",
                )
            return
        if self.play_on:
            self._play_stop()
        self._estop_halted = False
        self._run_reset()
        self.play_one_step = False
        self.play_program_id = p["id"]
        self.play_prog_steps = steps
        self.play_prog_i = 0
        if p.get("id") == self.start_program_id:
            self.start_seq_i = 0
            self._refresh_start_seq_label()
        self._apply_program_rate(p.get("id"))
        self._log(f"Programme « {p.get('name')} » : {len(steps)} etape(s) a {self._play_rate():.1f}x")
        self._program_launch_current()

    def _program_launch_current(self):
        if self.play_prog_i >= len(self.play_prog_steps):
            self._play_stop(keep_run=True)
            return
        step = self.play_prog_steps[self.play_prog_i]
        self._apply_suite(step["suite_id"])
        self._refresh_suite_list()
        self.play_on = True
        mode = prog_step_mode(step)
        self.play_mode = mode
        self.play_boomerang = mode == "boomerang"
        self.play_step_repeat = max(1, int(step.get("repeat") or 1))
        self.play_step_left = self.play_step_repeat
        self.play_cycle = 1
        self._play_begin_pass(reverse=(mode != "1x"))

    def _play_finish_step(self):
        if self.play_one_step:
            self._play_stop(keep_run=True)
            return False
        return self._program_advance()

    def _program_advance(self):
        self._send_speeds([0] * 6, force=True)
        self.play_prog_i += 1
        if self.play_prog_i >= len(self.play_prog_steps):
            name = (self._program_by_id(self.play_program_id) or {}).get("name") or "Programme"
            self._log(f"Programme « {name} » termine")
            self._play_stop(keep_run=True)
            return False
        self._program_launch_current()
        return True

    def _clip_clear(self):
        if self.rec_on or self.play_on:
            return
        s = self._suite_by_id(self.clip_id)
        if not s:
            self.clip = []
            self.clip_id = None
            self._clip_status()
            return
        if not messagebox.askyesno("Supprimer", f"Supprimer « {s.get('name')} » ?"):
            return
        deleted = self.clip_id
        self.library = [x for x in self.library if x.get("id") != deleted]
        if self.start_suite_id == deleted:
            self.start_suite_id = None
        for p in self.programs:
            p["steps"] = [st for st in (p.get("steps") or []) if st.get("suite_id") != deleted]
        if self.library:
            self._apply_suite(self.library[-1]["id"])
        else:
            self.clip_id = None
            self.clip = []
        self._library_save()
        self._refresh_suite_list()
        self._clip_status()

    def _clip_rename(self):
        s = self._suite_by_id(self.clip_id)
        if not s:
            return
        name = simpledialog.askstring(
            "Renommer",
            "Nom de la suite :",
            initialvalue=s.get("name") or "",
            parent=self,
        )
        if name is None:
            return
        name = name.strip()
        if not name:
            return
        s["name"] = name
        self._library_save()
        self._refresh_suite_list()
        self._clip_status()

    def _clip_save(self):
        if not self.clip:
            messagebox.showinfo("Exporter", "Clip vide.")
            return
        path = filedialog.asksaveasfilename(
            defaultextension=".json",
            filetypes=[("JSON", "*.json")],
            initialfile=f"{self._suite_name()}.json",
        )
        if not path:
            return
        try:
            self._clip_write_file(path)
        except Exception as e:
            messagebox.showerror("Exporter", str(e))

    def _clip_load(self):
        path = filedialog.askopenfilename(filetypes=[("JSON", "*.json")])
        if not path:
            return
        self._play_stop()
        self.rec_on = False
        try:
            with open(path, "r", encoding="utf-8") as f:
                raw = json.load(f)
            if isinstance(raw, dict) and raw.get("suites"):
                added = 0
                for item in raw["suites"]:
                    clip = steps_from_raw(item.get("steps"))
                    if not clip_has_motion(clip):
                        continue
                    self.library.append(
                        {
                            "id": self._new_suite_id(),
                            "name": item.get("name") or self._next_suite_name(),
                            "steps": [{"t": t, "s": s} for t, s in clip],
                            "duration": round(self._clip_duration(clip), 3),
                        }
                    )
                    added += 1
                if not added:
                    messagebox.showinfo("Importer", "Aucune suite avec du mouvement dans ce fichier.")
                    return
                self._apply_suite(self.library[-1]["id"])
            else:
                clip = steps_from_raw(raw.get("steps", raw) if isinstance(raw, dict) else raw)
                if not clip_has_motion(clip):
                    messagebox.showinfo("Importer", "Aucun mouvement dans ce fichier.")
                    return
                sid = self._new_suite_id()
                name = raw.get("name") if isinstance(raw, dict) else None
                if not name:
                    name = os.path.splitext(os.path.basename(path))[0] or self._next_suite_name()
                self.library.append(
                    {
                        "id": sid,
                        "name": name,
                        "steps": [{"t": t, "s": s} for t, s in clip],
                        "duration": round(self._clip_duration(clip), 3),
                    }
                )
                self.clip_id = sid
                self.clip = clip
            self._library_save()
            self._refresh_suite_list()
            self._clip_status()
        except Exception as e:
            messagebox.showerror("Importer", str(e))

    def _pad_tick(self):
        pad = self._read_pad()
        name = pad["name"] or "aucun pad"
        extra = ""
        if pad["name"]:
            extra = f"  —  {len(pad['axes'])} axes, {len(pad['buttons'])} boutons, {len(pad['hats'])} chapeau"
        self.pad_name.configure(text=f"Pad : {name}{extra}")
        self._draw_pad(pad)
        self._try_capture(pad)
        if self._play_tick():
            pass
        elif self._capture is None:
            self._drive_from_pad(pad)
        if self.rec_on or self.play_on:
            self._suite_refresh()
        self.after(PAD_MS, self._pad_tick)

    def _on_close(self):
        if self.rec_on:
            self._rec_stop()
        self._play_stop()
        self._library_save()
        self.pad_enable.set(0)
        self._disconnect()
        self.destroy()


if __name__ == "__main__":
    SensorGui().mainloop()
