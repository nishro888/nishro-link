"""Windows VK  <->  Linux evdev KEY_ code mapping.

The wire protocol speaks evdev codes (see protocol.py). Windows capture turns
its VK codes into evdev codes before sending; Windows injection turns received
evdev codes back into VKs. The Linux side already speaks evdev.

Covers the standard PC keyboard. Unmapped keys are dropped (logged) rather than
mis-fired; extend the table as needed.
"""
from __future__ import annotations

# --- evdev KEY_ codes we care about (from linux/input-event-codes.h) ---
E = {
    "ESC": 1, "1": 2, "2": 3, "3": 4, "4": 5, "5": 6, "6": 7, "7": 8, "8": 9,
    "9": 10, "0": 11, "MINUS": 12, "EQUAL": 13, "BACKSPACE": 14, "TAB": 15,
    "Q": 16, "W": 17, "E": 18, "R": 19, "T": 20, "Y": 21, "U": 22, "I": 23,
    "O": 24, "P": 25, "LEFTBRACE": 26, "RIGHTBRACE": 27, "ENTER": 28,
    "LEFTCTRL": 29, "A": 30, "S": 31, "D": 32, "F": 33, "G": 34, "H": 35,
    "J": 36, "K": 37, "L": 38, "SEMICOLON": 39, "APOSTROPHE": 40, "GRAVE": 41,
    "LEFTSHIFT": 42, "BACKSLASH": 43, "Z": 44, "X": 45, "C": 46, "V": 47,
    "B": 48, "N": 49, "M": 50, "COMMA": 51, "DOT": 52, "SLASH": 53,
    "RIGHTSHIFT": 54, "KPASTERISK": 55, "LEFTALT": 56, "SPACE": 57,
    "CAPSLOCK": 58, "F1": 59, "F2": 60, "F3": 61, "F4": 62, "F5": 63, "F6": 64,
    "F7": 65, "F8": 66, "F9": 67, "F10": 68, "NUMLOCK": 69, "SCROLLLOCK": 70,
    "F11": 87, "F12": 88, "RIGHTCTRL": 97, "RIGHTALT": 100, "HOME": 102,
    "UP": 103, "PAGEUP": 104, "LEFT": 105, "RIGHT": 106, "END": 107,
    "DOWN": 108, "PAGEDOWN": 109, "INSERT": 110, "DELETE": 111,
    "LEFTMETA": 125, "RIGHTMETA": 126,
}

# --- Windows virtual-key -> evdev name ---
_VK_NAME = {
    0x1B: "ESC", 0x08: "BACKSPACE", 0x09: "TAB", 0x0D: "ENTER", 0x20: "SPACE",
    0x14: "CAPSLOCK", 0x90: "NUMLOCK", 0x91: "SCROLLLOCK",
    # modifiers (LL hook reports L/R specific; keep generic fallbacks too)
    0xA0: "LEFTSHIFT", 0xA1: "RIGHTSHIFT", 0x10: "LEFTSHIFT",
    0xA2: "LEFTCTRL", 0xA3: "RIGHTCTRL", 0x11: "LEFTCTRL",
    0xA4: "LEFTALT", 0xA5: "RIGHTALT", 0x12: "LEFTALT",
    0x5B: "LEFTMETA", 0x5C: "RIGHTMETA",
    # navigation
    0x25: "LEFT", 0x26: "UP", 0x27: "RIGHT", 0x28: "DOWN",
    0x24: "HOME", 0x23: "END", 0x21: "PAGEUP", 0x22: "PAGEDOWN",
    0x2D: "INSERT", 0x2E: "DELETE",
    # OEM punctuation (US layout)
    0xBD: "MINUS", 0xBB: "EQUAL", 0xDB: "LEFTBRACE", 0xDD: "RIGHTBRACE",
    0xBA: "SEMICOLON", 0xDE: "APOSTROPHE", 0xC0: "GRAVE", 0xDC: "BACKSLASH",
    0xBC: "COMMA", 0xBE: "DOT", 0xBF: "SLASH",
}
# letters A-Z  (VK 0x41-0x5A) and digits 0-9 (VK 0x30-0x39)
for _c in range(0x41, 0x5B):
    _VK_NAME[_c] = chr(_c)
for _d in range(0x30, 0x3A):
    _VK_NAME[_d] = chr(_d)
# function keys F1-F12 (VK 0x70-0x7B)
for _i in range(12):
    _VK_NAME[0x70 + _i] = f"F{_i + 1}"

VK_TO_EVDEV = {vk: E[name] for vk, name in _VK_NAME.items() if name in E}
EVDEV_TO_VK = {}
for _vk, _code in VK_TO_EVDEV.items():
    EVDEV_TO_VK.setdefault(_code, _vk)   # first VK wins (L variants precede generic)


def vk_to_evdev(vk: int):
    return VK_TO_EVDEV.get(vk)


def evdev_to_vk(code: int):
    return EVDEV_TO_VK.get(code)
