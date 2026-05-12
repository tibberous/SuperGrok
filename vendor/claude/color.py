"""
color.py -- Forgiving Color class. Parses anything reasonable.

Color.fromString("ghost white")          # -> Color(248, 248, 255)
Color.fromString("GHOSTWHITE")           # -> Color(248, 248, 255)
Color.fromString("ghost_white")          # -> Color(248, 248, 255)
Color.fromString("#f8f8ff")              # -> Color(248, 248, 255)
Color.fromString("rgb(248, 248, 255)")   # -> Color(248, 248, 255)
Color.fromString("r=248 g=248 b=255")    # -> Color(248, 248, 255)
Color.fromString("0xf8f8ff")             # -> Color(248, 248, 255)

Color.mix("#333", COLOR_BLANCHED_ALMOND, midpoint=50)
Color.mix("red", "blue", midpoint=25)    # 25% red, 75% blue
"""
from __future__ import annotations
import re
from dataclasses import dataclass
from typing import Union


# ---------------------------------------------------------------------------
# Full CSS named color table (147 colors, CSS Color Level 4)
# ---------------------------------------------------------------------------
NAMED_COLORS: dict[str, str] = {
    "aliceblue": "#f0f8ff", "antiquewhite": "#faebd7", "aqua": "#00ffff",
    "aquamarine": "#7fffd4", "azure": "#f0ffff", "beige": "#f5f5dc",
    "bisque": "#ffe4c4", "black": "#000000", "blanchedalmond": "#ffebcd",
    "blue": "#0000ff", "blueviolet": "#8a2be2", "brown": "#a52a2a",
    "burlywood": "#deb887", "cadetblue": "#5f9ea0", "chartreuse": "#7fff00",
    "chocolate": "#d2691e", "coral": "#ff7f50", "cornflowerblue": "#6495ed",
    "cornsilk": "#fff8dc", "crimson": "#dc143c", "cyan": "#00ffff",
    "darkblue": "#00008b", "darkcyan": "#008b8b", "darkgoldenrod": "#b8860b",
    "darkgray": "#a9a9a9", "darkgrey": "#a9a9a9", "darkgreen": "#006400",
    "darkkhaki": "#bdb76b", "darkmagenta": "#8b008b", "darkolivegreen": "#556b2f",
    "darkorange": "#ff8c00", "darkorchid": "#9932cc", "darkred": "#8b0000",
    "darksalmon": "#e9967a", "darkseagreen": "#8fbc8f", "darkslateblue": "#483d8b",
    "darkslategray": "#2f4f4f", "darkslategrey": "#2f4f4f",
    "darkturquoise": "#00ced1", "darkviolet": "#9400d3", "deeppink": "#ff1493",
    "deepskyblue": "#00bfff", "dimgray": "#696969", "dimgrey": "#696969",
    "dodgerblue": "#1e90ff", "firebrick": "#b22222", "floralwhite": "#fffaf0",
    "forestgreen": "#228b22", "fuchsia": "#ff00ff", "gainsboro": "#dcdcdc",
    "ghostwhite": "#f8f8ff", "gold": "#ffd700", "goldenrod": "#daa520",
    "gray": "#808080", "grey": "#808080", "green": "#008000",
    "greenyellow": "#adff2f", "honeydew": "#f0fff0", "hotpink": "#ff69b4",
    "indianred": "#cd5c5c", "indigo": "#4b0082", "ivory": "#fffff0",
    "khaki": "#f0e68c", "lavender": "#e6e6fa", "lavenderblush": "#fff0f5",
    "lawngreen": "#7cfc00", "lemonchiffon": "#fffacd", "lightblue": "#add8e6",
    "lightcoral": "#f08080", "lightcyan": "#e0ffff",
    "lightgoldenrodyellow": "#fafad2", "lightgray": "#d3d3d3",
    "lightgrey": "#d3d3d3", "lightgreen": "#90ee90", "lightpink": "#ffb6c1",
    "lightsalmon": "#ffa07a", "lightseagreen": "#20b2aa",
    "lightskyblue": "#87cefa", "lightslategray": "#778899",
    "lightslategrey": "#778899", "lightsteelblue": "#b0c4de",
    "lightyellow": "#ffffe0", "lime": "#00ff00", "limegreen": "#32cd32",
    "linen": "#faf0e6", "magenta": "#ff00ff", "maroon": "#800000",
    "mediumaquamarine": "#66cdaa", "mediumblue": "#0000cd",
    "mediumorchid": "#ba55d3", "mediumpurple": "#9370db",
    "mediumseagreen": "#3cb371", "mediumslateblue": "#7b68ee",
    "mediumspringgreen": "#00fa9a", "mediumturquoise": "#48d1cc",
    "mediumvioletred": "#c71585", "midnightblue": "#191970",
    "mintcream": "#f5fffa", "mistyrose": "#ffe4e1", "moccasin": "#ffe4b5",
    "navajowhite": "#ffdead", "navy": "#000080", "oldlace": "#fdf5e6",
    "olive": "#808000", "olivedrab": "#6b8e23", "orange": "#ffa500",
    "orangered": "#ff4500", "orchid": "#da70d6", "palegoldenrod": "#eee8aa",
    "palegreen": "#98fb98", "paleturquoise": "#afeeee",
    "palevioletred": "#db7093", "papayawhip": "#ffefd5",
    "peachpuff": "#ffdab9", "peru": "#cd853f", "pink": "#ffc0cb",
    "plum": "#dda0dd", "powderblue": "#b0e0e6", "purple": "#800080",
    "rebeccapurple": "#663399", "red": "#ff0000", "rosybrown": "#bc8f8f",
    "royalblue": "#4169e1", "saddlebrown": "#8b4513", "salmon": "#fa8072",
    "sandybrown": "#f4a460", "seagreen": "#2e8b57", "seashell": "#fff5ee",
    "sienna": "#a0522d", "silver": "#c0c0c0", "skyblue": "#87ceeb",
    "slateblue": "#6a5acd", "slategray": "#708090", "slategrey": "#708090",
    "snow": "#fffafa", "springgreen": "#00ff7f", "steelblue": "#4682b4",
    "tan": "#d2b48c", "teal": "#008080", "thistle": "#d8bfd8",
    "tomato": "#ff6347", "turquoise": "#40e0d0", "violet": "#ee82ee",
    "wheat": "#f5deb3", "white": "#ffffff", "whitesmoke": "#f5f5f5",
    "yellow": "#ffff00", "yellowgreen": "#9acd32",
}

# Convenience constants  e.g.  COLOR_BLANCHED_ALMOND
for _name, _hex in NAMED_COLORS.items():
    globals()[f"COLOR_{_name.upper()}"] = _hex


def _normalize_name(s: str) -> str:
    """'Ghost White' / 'ghost_white' / 'GHOST-WHITE' -> 'ghostwhite'"""
    return re.sub(r"[^a-z0-9]", "", s.lower())


@dataclass
class Color:
    r: int
    g: int
    b: int
    a: int = 255

    # ------------------------------------------------------------------
    # Construction
    # ------------------------------------------------------------------

    @classmethod
    def fromString(cls, s: str) -> "Color":
        """
        Parse any reasonable color string.

        Accepts:
          Named:   "ghost white", "ghost_white", "GHOSTWHITE", "GhostWhite"
          Hex:     "#f8f8ff", "#fff", "f8f8ff"
          0x:      "0xf8f8ff"
          rgb:     "rgb(248, 248, 255)"  or  "rgba(248,248,255,0.5)"
          r=g=b:   "r=248 g=248 b=255"  or  "r=f8 g=f8 b=ff"
          Single:  "r=255"  (g and b default to 0, warns)
        """
        s = s.strip()

        # Named color (forgiving: spaces, underscores, dashes, case all ok)
        key = _normalize_name(s)
        if key in NAMED_COLORS:
            return cls._from_hex(NAMED_COLORS[key])

        # Prefix strip: remove leading # or 0x
        clean = s.lstrip("#")

        # #RGB shorthand
        if re.match(r"^[0-9a-fA-F]{3}$", clean):
            r, g, b = clean
            return cls(int(r*2, 16), int(g*2, 16), int(b*2, 16))

        # #RRGGBB
        if re.match(r"^[0-9a-fA-F]{6}$", clean):
            return cls._from_hex("#" + clean)

        # #RRGGBBAA
        if re.match(r"^[0-9a-fA-F]{8}$", clean):
            return cls(int(clean[0:2],16), int(clean[2:4],16),
                       int(clean[4:6],16), int(clean[6:8],16))

        # 0xRRGGBB
        m = re.match(r"^0x([0-9a-fA-F]{6})$", s, re.I)
        if m:
            return cls._from_hex("#" + m[1])

        # rgb(r, g, b) / rgba(r, g, b, a)
        m = re.match(r"rgba?\s*\(\s*(\d+)\s*,\s*(\d+)\s*,\s*(\d+)"
                     r"(?:\s*,\s*([\d.]+))?\s*\)", s, re.I)
        if m:
            a = int(float(m[4]) * 255) if m[4] else 255
            return cls(int(m[1]), int(m[2]), int(m[3]), a)

        # r=248 g=248 b=255  (any order, spaces or commas between)
        parts = {}
        for ch in ("r", "g", "b", "a"):
            m = re.search(rf"\b{ch}\s*=\s*([0-9a-fA-Fx]+)", s, re.I)
            if m:
                val = m[1]
                if val.startswith("0x") or val.startswith("0X"):
                    parts[ch] = int(val, 16)
                elif len(val) <= 2 and all(c in "0123456789abcdefABCDEF" for c in val):
                    parts[ch] = int(val, 16)
                else:
                    parts[ch] = int(val)
        if parts:
            if len(parts) == 1:
                ch = list(parts.keys())[0]
                print(f"[WARNING:color] Partial color '{s}', only {ch} specified. Filling others with 0.")
            return cls(parts.get("r", 0), parts.get("g", 0),
                       parts.get("b", 0), parts.get("a", 255))

        print(f"[WARNING:color] Could not parse '{s}', returning black.")
        return cls(0, 0, 0)

    @classmethod
    def _from_hex(cls, h: str) -> "Color":
        h = h.lstrip("#")
        return cls(int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16))

    @classmethod
    def fromRGB(cls, r: int, g: int, b: int, a: int = 255) -> "Color":
        return cls(r, g, b, a)

    # ------------------------------------------------------------------
    # Mixing
    # ------------------------------------------------------------------

    @classmethod
    def mix(cls,
            a: "Color | str",
            b: "Color | str",
            midpoint: int = 50) -> "Color":
        """
        Blend two colors.

        midpoint=50  -> equal mix
        midpoint=25  -> 25% of `a`, 75% of `b`
        midpoint=100 -> 100% of `a`

        Color.mix("#333", COLOR_BLANCHED_ALMOND, midpoint=50)
        Color.mix("ghost white", "navy", midpoint=30)
        """
        ca = cls.fromString(a) if isinstance(a, str) else a
        cb = cls.fromString(b) if isinstance(b, str) else b
        t = midpoint / 100.0
        return cls(
            round(ca.r * t + cb.r * (1 - t)),
            round(ca.g * t + cb.g * (1 - t)),
            round(ca.b * t + cb.b * (1 - t)),
            round(ca.a * t + cb.a * (1 - t)),
        )

    # ------------------------------------------------------------------
    # Output
    # ------------------------------------------------------------------

    def toHex(self, alpha: bool = False) -> str:
        if alpha:
            return f"#{self.r:02x}{self.g:02x}{self.b:02x}{self.a:02x}"
        return f"#{self.r:02x}{self.g:02x}{self.b:02x}"

    def toRGB(self) -> str:
        return f"rgb({self.r}, {self.g}, {self.b})"

    def toRGBA(self) -> str:
        return f"rgba({self.r}, {self.g}, {self.b}, {self.a/255:.3f})"

    def toQtARGB(self) -> int:
        """Qt uses 0xAARRGGBB."""
        return (self.a << 24) | (self.r << 16) | (self.g << 8) | self.b

    def toTuple(self) -> tuple[int, int, int, int]:
        return (self.r, self.g, self.b, self.a)

    def withAlpha(self, a: int) -> "Color":
        return Color(self.r, self.g, self.b, a)

    def lighter(self, pct: int = 20) -> "Color":
        t = pct / 100.0
        return Color(
            min(255, round(self.r + (255 - self.r) * t)),
            min(255, round(self.g + (255 - self.g) * t)),
            min(255, round(self.b + (255 - self.b) * t)),
            self.a,
        )

    def darker(self, pct: int = 20) -> "Color":
        t = 1 - pct / 100.0
        return Color(round(self.r * t), round(self.g * t),
                     round(self.b * t), self.a)

    def __repr__(self) -> str:
        return f"Color({self.r}, {self.g}, {self.b}, {self.a}) {self.toHex()}"


# ---------------------------------------------------------------------------
# Quick smoke test / demo
# ---------------------------------------------------------------------------
if __name__ == "__main__":
    tests = [
        "ghost white",
        "ghost_white",
        "GHOSTWHITE",
        "Ghost White",
        "blanchedalmond",
        "blanched almond",
        "BLANCHED_ALMOND",
        "#f8f8ff",
        "#fff",
        "0xf8f8ff",
        "rgb(248, 248, 255)",
        "rgba(248, 248, 255, 0.5)",
        "r=248 g=248 b=255",
        "r=f8 g=f8 b=ff",
        "r=255",
        "salmon",
        "burnt almond",   # not in CSS, should warn
    ]

    print("=== Color.fromString ===")
    for t in tests:
        c = Color.fromString(t)
        print(f"  {t!r:<30} -> {c}")

    print()
    print("=== Color.mix ===")
    print(f"  mix('#333', COLOR_BLANCHED_ALMOND, 50)  -> "
          f"{Color.mix('#333', COLOR_BLANCHEDALMOND, midpoint=50)}")
    print(f"  mix('red', 'blue', 25)                  -> "
          f"{Color.mix('red', 'blue', midpoint=25)}")
    print(f"  mix('ghost white', 'navy', 30)          -> "
          f"{Color.mix('ghost white', 'navy', midpoint=30)}")

    print()
    print("=== Output formats ===")
    c = Color.fromString("cornflower blue")
    print(f"  Hex:    {c.toHex()}")
    print(f"  RGB:    {c.toRGB()}")
    print(f"  RGBA:   {c.toRGBA()}")
    print(f"  QtARGB: {hex(c.toQtARGB())}")
    print(f"  Lighter 20%: {c.lighter(20)}")
    print(f"  Darker  20%: {c.darker(20)}")
