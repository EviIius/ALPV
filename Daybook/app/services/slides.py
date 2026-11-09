"""PowerPoint (.pptx) to HTML, so decks can be read inside Daybook.

Every slide becomes positioned HTML: text keeps the deck's fonts, sizes, colours,
bullets and spacing (inherited from the slide layout, master and theme the way
PowerPoint does it), shapes and lines are drawn as SVG, and pictures, tables and
simple charts (column, bar, line, area, pie, doughnut, drawn from the numbers
cached in the file) come along. Speaker notes and each slide's plain text are
returned too, for search and highlights.

Nothing leaves the machine and nothing else is needed: no PowerPoint, no
LibreOffice, no network. The file is treated as untrusted input: XML entities
and network access are off, part sizes are capped, every piece of text is
escaped and every style value is built from validated numbers and colours.
"""
from __future__ import annotations

import colorsys
import html
import logging
import math
import posixpath
import re
import zipfile
from dataclasses import dataclass, field
from pathlib import Path

from lxml import etree

log = logging.getLogger("daybook")

RENDERER_VERSION = 2
EMU_PER_PX = 9525                      # 914400 EMU per inch, 96 px per inch
MAX_XML_BYTES = 25 * 1024 * 1024
MAX_MEDIA_BYTES = 40 * 1024 * 1024
MAX_SLIDES = 400

NS = {
    "a": "http://schemas.openxmlformats.org/drawingml/2006/main",
    "p": "http://schemas.openxmlformats.org/presentationml/2006/main",
    "r": "http://schemas.openxmlformats.org/officeDocument/2006/relationships",
    "c": "http://schemas.openxmlformats.org/drawingml/2006/chart",
    "mc": "http://schemas.openxmlformats.org/markup-compatibility/2006",
    "pr": "http://schemas.openxmlformats.org/package/2006/relationships",
    "dsp": "http://schemas.microsoft.com/office/drawing/2008/diagram",
}
A, P, R, C, MC, DSP = (f"{{{NS[k]}}}" for k in ("a", "p", "r", "c", "mc", "dsp"))
REL_BASE = "http://schemas.openxmlformats.org/officeDocument/2006/relationships/"
_PARSER = etree.XMLParser(resolve_entities=False, no_network=True, huge_tree=False, remove_comments=True)

WEB_IMAGES = {"png": "image/png", "jpg": "image/jpeg", "jpeg": "image/jpeg", "gif": "image/gif", "webp": "image/webp",
              "bmp": "image/bmp"}

PRESET_COLORS = {
    "black": "000000", "white": "FFFFFF", "red": "FF0000", "green": "008000", "blue": "0000FF", "yellow": "FFFF00",
    "gray": "808080", "grey": "808080", "darkGray": "A9A9A9", "lightGray": "D3D3D3", "orange": "FFA500",
    "purple": "800080", "navy": "000080", "teal": "008080", "silver": "C0C0C0", "maroon": "800000",
    "olive": "808000", "lime": "00FF00", "aqua": "00FFFF", "fuchsia": "FF00FF", "darkBlue": "00008B",
    "darkRed": "8B0000", "darkGreen": "006400", "lightBlue": "ADD8E6",
}
SYMBOL_BULLETS = {"§": "■", "Ø": "➢", "ü": "✓", "q": "❑", "v": "❖", "n": "■", "l": "●", "o": "□", "è": "➔",
                  "Ü": "➨", "w": "◆", "u": "◆", "·": "•", "": "■", "": "•", "": "➢", "": "✓"}

FONT_STACKS = {
    "calibri": '"Calibri", "Carlito", "Segoe UI", Arial, sans-serif',
    "calibri light": '"Calibri Light", "Calibri", "Carlito", "Segoe UI", Arial, sans-serif',
    "aptos": '"Aptos", "Segoe UI", "Helvetica Neue", Arial, sans-serif',
    "aptos display": '"Aptos Display", "Aptos", "Segoe UI", "Helvetica Neue", Arial, sans-serif',
    "arial": 'Arial, "Liberation Sans", Helvetica, sans-serif',
    "helvetica": 'Helvetica, Arial, sans-serif',
    "segoe ui": '"Segoe UI", system-ui, -apple-system, Arial, sans-serif',
    "cambria": 'Cambria, Caladea, Georgia, serif',
    "georgia": 'Georgia, serif',
    "times new roman": '"Times New Roman", Times, serif',
    "consolas": 'Consolas, Menlo, monospace',
    "courier new": '"Courier New", Courier, monospace',
    "century gothic": '"Century Gothic", "Avenir Next", Avenir, Arial, sans-serif',
    "verdana": 'Verdana, Arial, sans-serif',
    "tahoma": 'Tahoma, Verdana, Arial, sans-serif',
    "garamond": 'Garamond, "EB Garamond", Georgia, serif',
}


class DeckError(Exception):
    pass


# ---------------------------------------------------------------------------
# Small helpers
# ---------------------------------------------------------------------------
def _px(emu) -> float:
    try:
        return float(emu) / EMU_PER_PX
    except (TypeError, ValueError):
        return 0.0


def _num(v: float) -> str:
    """A compact number for CSS/SVG."""
    if abs(v - round(v)) < 0.005:
        return str(int(round(v)))
    return f"{v:.2f}".rstrip("0").rstrip(".")


def _int(el, name: str, default=None):
    if el is None:
        return default
    v = el.get(name)
    if v is None:
        return default
    try:
        return int(float(v))
    except ValueError:
        return default


def _bool(el, name: str, default=None):
    if el is None:
        return default
    v = el.get(name)
    if v is None:
        return default
    return v in ("1", "true", "on")


def _esc(s: str) -> str:
    return html.escape(s or "", quote=True)


def _attr(style: list[str]) -> str:
    return _esc(";".join(s for s in style if s))


_FONT_OK = re.compile(r"[^A-Za-z0-9 _\-.&]")


def font_stack(name: str | None) -> str | None:
    if not name:
        return None
    clean = _FONT_OK.sub("", name).strip()[:60]
    if not clean:
        return None
    stack = FONT_STACKS.get(clean.lower())
    if stack:
        return stack
    return f'"{clean}", "Carlito", Arial, sans-serif'


def _child(el, *path):
    for tag in path:
        if el is None:
            return None
        el = el.find(tag)
    return el


# ---------------------------------------------------------------------------
# Package (the zip) and relationships
# ---------------------------------------------------------------------------
class Package:
    def __init__(self, path: Path) -> None:
        try:
            self.z = zipfile.ZipFile(path)
        except (zipfile.BadZipFile, OSError) as exc:
            raise DeckError("This presentation couldn't be opened. It may be damaged or password-protected.") from exc
        self.names = {i.filename: i for i in self.z.infolist()}
        self._xml: dict[str, object] = {}
        self._rels: dict[str, dict] = {}

    def has(self, part: str) -> bool:
        return part in self.names

    def read(self, part: str, limit: int = MAX_XML_BYTES) -> bytes:
        info = self.names.get(part)
        if info is None:
            raise KeyError(part)
        if info.file_size > limit:
            raise DeckError(f"Part {part} is too large to read.")
        return self.z.read(info)

    def xml(self, part: str):
        if part not in self._xml:
            try:
                self._xml[part] = etree.fromstring(self.read(part), _PARSER)
            except KeyError:
                self._xml[part] = None
            except etree.XMLSyntaxError as exc:
                raise DeckError(f"Part {part} isn't valid XML.") from exc
        return self._xml[part]

    def rels(self, part: str) -> dict:
        if part in self._rels:
            return self._rels[part]
        folder, base = posixpath.split(part)
        rel_part = posixpath.join(folder, "_rels", base + ".rels")
        out: dict[str, dict] = {}
        root = self.xml(rel_part) if self.has(rel_part) else None
        if root is not None:
            for rel in root:
                rid, target, typ = rel.get("Id"), rel.get("Target") or "", rel.get("Type") or ""
                external = rel.get("TargetMode") == "External"
                resolved = target if external else posixpath.normpath(posixpath.join(folder, target)).lstrip("/")
                if target.startswith("/"):
                    resolved = target.lstrip("/")
                out[rid] = {"type": typ.rsplit("/", 1)[-1], "target": resolved, "external": external}
        self._rels[part] = out
        return out

    def rel_of_type(self, part: str, typ: str) -> str | None:
        for r in self.rels(part).values():
            if r["type"] == typ and not r["external"]:
                return r["target"]
        return None


# ---------------------------------------------------------------------------
# Colours
# ---------------------------------------------------------------------------
def _hex_rgb(h: str) -> tuple[float, float, float]:
    h = (h or "000000")[:6].rjust(6, "0")
    try:
        return int(h[0:2], 16) / 255, int(h[2:4], 16) / 255, int(h[4:6], 16) / 255
    except ValueError:
        return 0.0, 0.0, 0.0


def _rgb_hex(r: float, g: float, b: float) -> str:
    c = lambda v: max(0, min(255, int(round(v * 255))))  # noqa: E731
    return f"{c(r):02X}{c(g):02X}{c(b):02X}"


@dataclass
class Color:
    hex: str
    alpha: float = 1.0

    def css(self) -> str:
        if self.alpha >= 0.999:
            return f"#{self.hex}"
        r, g, b = (int(self.hex[i:i + 2], 16) for i in (0, 2, 4))
        return f"rgba({r},{g},{b},{self.alpha:.3f})"


class ColorCtx:
    def __init__(self, scheme: dict[str, str], clr_map: dict[str, str]) -> None:
        self.scheme = scheme
        self.map = clr_map

    def scheme_hex(self, val: str) -> str:
        key = self.map.get(val, val)
        return self.scheme.get(key) or self.scheme.get(val) or "000000"

    def resolve(self, el, ph: Color | None = None) -> Color | None:
        """A colour from any DrawingML colour element (or a fill holding one)."""
        if el is None:
            return None
        tag = etree.QName(el).localname
        if tag in ("solidFill", "fgClr", "bgClr", "buClr", "highlight") or tag.endswith("Ref"):
            for ch in el:
                c = self.resolve(ch, ph)
                if c:
                    return c
            return None
        if tag == "srgbClr":
            base = (el.get("val") or "000000").upper()
        elif tag == "schemeClr":
            val = el.get("val") or "tx1"
            if val == "phClr":
                if ph is None:
                    return None
                base = ph.hex
            else:
                base = self.scheme_hex(val)
        elif tag == "sysClr":
            base = (el.get("lastClr") or ("FFFFFF" if el.get("val") == "window" else "000000")).upper()
        elif tag == "prstClr":
            base = PRESET_COLORS.get(el.get("val") or "", "000000")
        elif tag == "scrgbClr":
            def lin(v):  # linear percentage -> sRGB
                x = max(0.0, min(1.0, (_int(el, v, 0) or 0) / 100000))
                return 12.92 * x if x <= 0.0031308 else 1.055 * x ** (1 / 2.4) - 0.055
            base = _rgb_hex(lin("r"), lin("g"), lin("b"))
        elif tag == "hslClr":
            h = (_int(el, "hue", 0) or 0) / 60000 / 360
            s = (_int(el, "sat", 0) or 0) / 100000
            lum = (_int(el, "lum", 0) or 0) / 100000
            r, g, b = colorsys.hls_to_rgb(h, lum, s)
            base = _rgb_hex(r, g, b)
        else:
            return None
        if not re.fullmatch(r"[0-9A-Fa-f]{6}", base or ""):
            base = "000000"
        return _apply_mods(Color(base.upper()), el)


def _apply_mods(col: Color, el) -> Color:
    r, g, b = _hex_rgb(col.hex)
    alpha = col.alpha
    for m in el:
        name = etree.QName(m).localname
        v = (_int(m, "val", 100000) or 0) / 100000
        if name in ("lumMod", "lumOff", "satMod", "satOff", "hueMod", "hueOff"):
            h, lum, s = colorsys.rgb_to_hls(r, g, b)
            if name == "lumMod":
                lum *= v
            elif name == "lumOff":
                lum += v
            elif name == "satMod":
                s *= v
            elif name == "satOff":
                s += v
            elif name == "hueMod":
                h = (h * v) % 1
            elif name == "hueOff":
                h = (h + (_int(m, "val", 0) or 0) / 60000 / 360) % 1
            r, g, b = colorsys.hls_to_rgb(h, max(0, min(1, lum)), max(0, min(1, s)))
        elif name == "tint":
            r, g, b = (1 - (1 - c) * v for c in (r, g, b))
        elif name == "shade":
            r, g, b = (c * v for c in (r, g, b))
        elif name == "alpha":
            alpha = v
        elif name == "alphaMod":
            alpha *= v
        elif name == "alphaOff":
            alpha += v
        elif name == "inv":
            r, g, b = 1 - r, 1 - g, 1 - b
        elif name in ("gray", "grayscl"):
            y = 0.299 * r + 0.587 * g + 0.114 * b
            r = g = b = y
        elif name == "comp":
            h, lum, s = colorsys.rgb_to_hls(r, g, b)
            r, g, b = colorsys.hls_to_rgb((h + 0.5) % 1, lum, s)
    return Color(_rgb_hex(r, g, b), max(0.0, min(1.0, alpha)))


# ---------------------------------------------------------------------------
# Theme, masters and layouts
# ---------------------------------------------------------------------------
@dataclass
class Theme:
    colors: dict = field(default_factory=dict)
    major: str = "Calibri"
    minor: str = "Calibri"
    fills: list = field(default_factory=list)
    lines: list = field(default_factory=list)
    bg_fills: list = field(default_factory=list)


def _load_theme(root) -> Theme:
    t = Theme()
    if root is None:
        return t
    scheme = root.find(f".//{A}clrScheme")
    if scheme is not None:
        for ch in scheme:
            name = etree.QName(ch).localname
            for col in ch:
                tag = etree.QName(col).localname
                if tag == "srgbClr":
                    t.colors[name] = (col.get("val") or "000000").upper()
                elif tag == "sysClr":
                    t.colors[name] = (col.get("lastClr") or "000000").upper()
    major = root.find(f".//{A}majorFont/{A}latin")
    minor = root.find(f".//{A}minorFont/{A}latin")
    t.major = (major.get("typeface") if major is not None else None) or "Calibri"
    t.minor = (minor.get("typeface") if minor is not None else None) or "Calibri"
    fmt = root.find(f".//{A}fmtScheme")
    if fmt is not None:
        t.fills = list(_child(fmt, f"{A}fillStyleLst") if _child(fmt, f"{A}fillStyleLst") is not None else [])
        t.lines = list(_child(fmt, f"{A}lnStyleLst") if _child(fmt, f"{A}lnStyleLst") is not None else [])
        t.bg_fills = list(_child(fmt, f"{A}bgFillStyleLst") if _child(fmt, f"{A}bgFillStyleLst") is not None else [])
    return t


@dataclass
class Placeholders:
    by_type: dict = field(default_factory=dict)
    by_idx: dict = field(default_factory=dict)


def _ph_info(sp) -> tuple[str, int] | None:
    ph = sp.find(f"./{P}nvSpPr/{P}nvPr/{P}ph")
    if ph is None:
        ph = sp.find(f"./{P}nvPicPr/{P}nvPr/{P}ph")
    if ph is None:
        ph = sp.find(f"./{P}nvGraphicFramePr/{P}nvPr/{P}ph")
    if ph is None:
        return None
    return ph.get("type") or "obj", _int(ph, "idx", 0) or 0


def _collect_ph(root) -> Placeholders:
    out = Placeholders()
    tree = root.find(f"./{P}cSld/{P}spTree") if root is not None else None
    if tree is None:
        return out
    for sp in tree.iter(f"{P}sp", f"{P}pic"):
        info = _ph_info(sp)
        if not info:
            continue
        ptype, idx = info
        out.by_type.setdefault("title" if ptype == "ctrTitle" else ptype, sp)
        out.by_idx.setdefault(idx, sp)
    return out


@dataclass
class Master:
    part: str
    root: object
    theme: Theme
    clr_map: dict
    ph: Placeholders
    title_style: object = None
    body_style: object = None
    other_style: object = None


@dataclass
class Layout:
    part: str
    root: object
    master: Master
    ph: Placeholders


UNIQUE_PH = {"title", "ctrTitle", "subTitle", "dt", "ftr", "sldNum", "hdr"}


def _match_ph(ph: Placeholders, ptype: str, idx: int, *, master: bool = False):
    if master:
        t = {"ctrTitle": "title", "subTitle": "body", "obj": "body"}.get(ptype, ptype)
        if t in ph.by_type:
            return ph.by_type[t]
        return ph.by_type.get("body") if t not in ("title", "dt", "ftr", "sldNum", "hdr") else None
    if ptype in UNIQUE_PH:
        t = "title" if ptype == "ctrTitle" else ptype
        if t in ph.by_type:
            return ph.by_type[t]
    if idx in ph.by_idx:
        cand = ph.by_idx[idx]
        cinfo = _ph_info(cand)
        if not cinfo or cinfo[0] not in ("title", "ctrTitle") or ptype in ("title", "ctrTitle"):
            return cand
    return ph.by_type.get(ptype)


# ---------------------------------------------------------------------------
# Text properties (merged the way PowerPoint inherits them)
# ---------------------------------------------------------------------------
PPR_ATTRS = ("marL", "indent", "algn", "lvl", "rtl", "defTabSz", "fontAlgn")
RPR_ATTRS = ("sz", "b", "i", "u", "strike", "cap", "spc", "baseline")


def _rpr_props(el) -> dict:
    out: dict = {}
    if el is None:
        return out
    for a in RPR_ATTRS:
        if el.get(a) is not None:
            out[a] = el.get(a)
    for ch in el:
        name = etree.QName(ch).localname
        if name in ("solidFill", "noFill", "gradFill"):
            out["fill"] = ch
        elif name == "latin" and ch.get("typeface"):
            out["latin"] = ch.get("typeface")
        elif name == "highlight":
            out["highlight"] = ch
        elif name == "hlinkClick":
            out["hlink"] = ch
    return out


def _ppr_props(el) -> dict:
    out: dict = {}
    if el is None:
        return out
    for a in PPR_ATTRS:
        if el.get(a) is not None:
            out[a] = el.get(a)
    for ch in el:
        name = etree.QName(ch).localname
        if name in ("lnSpc", "spcBef", "spcAft"):
            out[name] = ch
        elif name in ("buNone", "buChar", "buAutoNum", "buBlip"):
            out["bu"] = ch
        elif name in ("buFont", "buFontTx"):
            out["buFont"] = ch
        elif name in ("buClr", "buClrTx"):
            out["buClr"] = ch
        elif name in ("buSzPct", "buSzPts", "buSzTx"):
            out["buSz"] = ch
        elif name == "defRPr":
            out["defRPr"] = _rpr_props(ch)
    return out


def _merge(base: dict, over: dict) -> dict:
    out = dict(base)
    for k, v in over.items():
        if k == "defRPr":
            out["defRPr"] = {**out.get("defRPr", {}), **v}
        else:
            out[k] = v
    return out


def _lvl_props(lst, lvl: int) -> dict:
    """Properties for paragraph level `lvl` (0-based) from a list-style element."""
    if lst is None:
        return {}
    props = _ppr_props(lst.find(f"{A}lvl{lvl + 1}pPr"))
    return props


# ---------------------------------------------------------------------------
# The renderer
# ---------------------------------------------------------------------------
@dataclass
class Rendered:
    width: int
    height: int
    slides: list
    media: dict
    warnings: list
    title: str | None = None


class _SlideCtx:
    def __init__(self, deck: "Deck", n: int, part: str, colors: ColorCtx, theme: Theme, layout: Layout | None) -> None:
        self.deck = deck
        self.n = n
        self.part = part
        self.colors = colors
        self.theme = theme
        self.layout = layout
        self.master = layout.master if layout else None
        self.ids = 0
        self.text: list[str] = []
        self.title = ""

    def uid(self, prefix: str) -> str:
        self.ids += 1
        return f"s{self.n}{prefix}{self.ids}"


class Deck:
    def __init__(self, path: Path, media_url) -> None:
        self.path = Path(path)
        self.pkg = Package(self.path)
        self.media_url = media_url           # callable: media name -> URL
        self.media: dict[str, bytes] = {}
        self._media_names: dict[str, str] = {}
        self.warnings: list[str] = []
        pres_part = "ppt/presentation.xml"
        self.pres = self.pkg.xml(pres_part)
        if self.pres is None:
            raise DeckError("This file doesn't look like a PowerPoint presentation.")
        sz = self.pres.find(f"{P}sldSz")
        self.cx = _int(sz, "cx", 12192000) or 12192000
        self.cy = _int(sz, "cy", 6858000) or 6858000
        self.default_text = self.pres.find(f"{P}defaultTextStyle")
        rels = self.pkg.rels(pres_part)
        self.slide_parts: list[str] = []
        lst = self.pres.find(f"{P}sldIdLst")
        if lst is not None:
            for sid in lst:
                rid = sid.get(f"{R}id")
                if rid in rels and not rels[rid]["external"]:
                    self.slide_parts.append(rels[rid]["target"])
        ts = self.pkg.rel_of_type(pres_part, "tableStyles")
        self.table_styles = self.pkg.xml(ts) if ts and self.pkg.has(ts) else None
        self._masters: dict[str, Master] = {}
        self._layouts: dict[str, Layout] = {}

    # -- structure -------------------------------------------------------------------
    def master(self, part: str) -> Master:
        if part not in self._masters:
            root = self.pkg.xml(part)
            theme_part = self.pkg.rel_of_type(part, "theme")
            theme = _load_theme(self.pkg.xml(theme_part) if theme_part else None)
            cm = root.find(f"{P}clrMap") if root is not None else None
            clr_map = dict(cm.attrib) if cm is not None else {"bg1": "lt1", "tx1": "dk1", "bg2": "lt2", "tx2": "dk2"}
            tx = root.find(f"{P}txStyles") if root is not None else None
            self._masters[part] = Master(part, root, theme, clr_map, _collect_ph(root),
                                         _child(tx, f"{P}titleStyle"), _child(tx, f"{P}bodyStyle"), _child(tx, f"{P}otherStyle"))
        return self._masters[part]

    def layout(self, part: str) -> Layout | None:
        if part not in self._layouts:
            root = self.pkg.xml(part)
            mpart = self.pkg.rel_of_type(part, "slideMaster")
            if root is None or not mpart:
                return None
            self._layouts[part] = Layout(part, root, self.master(mpart), _collect_ph(root))
        return self._layouts[part]

    def media_for(self, from_part: str, rid: str | None) -> str | None:
        """URL of an image part (copied out once), or None if it can't be shown."""
        if not rid:
            return None
        rel = self.pkg.rels(from_part).get(rid)
        if not rel or rel["external"]:
            return None
        target = rel["target"]
        if target in self._media_names:
            name = self._media_names[target]
            return self.media_url(name) if name else None
        ext = target.rsplit(".", 1)[-1].lower() if "." in target else ""
        name = None
        try:
            data = self.pkg.read(target, MAX_MEDIA_BYTES)
        except (KeyError, DeckError):
            data = None
        if data and ext in WEB_IMAGES:
            name = f"m{len(self._media_names) + 1}.{'jpg' if ext == 'jpeg' else ext}"
            self.media[name] = data
        elif data and ext in ("tif", "tiff"):
            try:
                from io import BytesIO

                from PIL import Image

                buf = BytesIO()
                Image.open(BytesIO(data)).save(buf, "PNG")
                name = f"m{len(self._media_names) + 1}.png"
                self.media[name] = buf.getvalue()
            except Exception:
                name = None
        self._media_names[target] = name or ""
        return self.media_url(name) if name else None

    # -- rendering ---------------------------------------------------------------------
    def render(self) -> Rendered:
        slides = []
        deck_title = None
        for i, part in enumerate(self.slide_parts[:MAX_SLIDES], 1):
            try:
                s = self.render_slide(i, part)
            except DeckError:
                raise
            except Exception as exc:  # one broken slide shouldn't hide the deck
                log.warning("Couldn't render slide %s of %s: %s", i, self.path.name, exc)
                s = {"n": i, "html": "", "title": f"Slide {i}", "notes": "", "text": "", "hidden": False, "error": True}
                self.warnings.append(f"Slide {i} couldn't be drawn.")
            slides.append(s)
            if i == 1 and s.get("title"):
                deck_title = s["title"]
        if len(self.slide_parts) > MAX_SLIDES:
            self.warnings.append(f"Only the first {MAX_SLIDES} slides are shown.")
        return Rendered(round(_px(self.cx)), round(_px(self.cy)), slides, self.media, self.warnings, deck_title)

    def render_slide(self, n: int, part: str) -> dict:
        root = self.pkg.xml(part)
        if root is None:
            raise ValueError("missing slide part")
        lpart = self.pkg.rel_of_type(part, "slideLayout")
        layout = self.layout(lpart) if lpart else None
        master = layout.master if layout else None
        theme = master.theme if master else Theme()
        clr_map = dict(master.clr_map) if master else {"bg1": "lt1", "tx1": "dk1", "bg2": "lt2", "tx2": "dk2"}
        for src in (layout.root if layout else None, root):
            ovr = _child(src, f"{P}clrMapOvr", f"{A}overrideClrMapping") if src is not None else None
            if ovr is not None:
                clr_map.update(ovr.attrib)
        ctx = _SlideCtx(self, n, part, ColorCtx(theme.colors, clr_map), theme, layout)

        bg = self._background(ctx, root) or (self._background(ctx, layout.root, layout.part) if layout else None) \
            or (self._background(ctx, master.root, master.part) if master else None) or "#FFFFFF"
        out: list[str] = []
        show_master = _bool(root, "showMasterSp", True)
        if show_master and layout is not None:
            if _bool(layout.root, "showMasterSp", True) and master is not None:
                out += self._tree(ctx, master.root, master.part, decor=True)
            out += self._tree(ctx, layout.root, layout.part, decor=True)
        out += self._tree(ctx, root, part, decor=False)
        w, h = _px(self.cx), _px(self.cy)
        body = (f'<div class="pptx-slide" data-slide="{n}" style="{_attr([f"width:{_num(w)}px", f"height:{_num(h)}px", bg])}">'
                + "".join(out) + "</div>")
        return {"n": n, "html": body, "title": ctx.title or f"Slide {n}", "notes": self._notes(part),
                "text": "\n".join(t for t in ctx.text if t.strip()), "hidden": not _bool(root, "show", True)}

    def _notes(self, part: str) -> str:
        npart = self.pkg.rel_of_type(part, "notesSlide")
        root = self.pkg.xml(npart) if npart and self.pkg.has(npart) else None
        if root is None:
            return ""
        for sp in root.iter(f"{P}sp"):
            info = _ph_info(sp)
            if info and info[0] == "body":
                paras = []
                for p in sp.iter(f"{A}p"):
                    paras.append("".join(t.text or "" for t in p.iter(f"{A}t")))
                return "\n".join(paras).strip()
        return ""

    def _background(self, ctx: _SlideCtx, root, part: str | None = None) -> str | None:
        bg = _child(root, f"{P}cSld", f"{P}bg") if root is not None else None
        if bg is None:
            return None
        part = part or ctx.part
        bg_pr = bg.find(f"{P}bgPr")
        if bg_pr is not None:
            paint = self._paint(ctx, bg_pr, part)
            return f"background:{paint['css']}" if paint and paint.get("css") else None
        ref = bg.find(f"{P}bgRef")
        if ref is not None:
            idx = _int(ref, "idx", 0) or 0
            ph = ctx.colors.resolve(ref)
            fills = ctx.theme.bg_fills if idx >= 1001 else ctx.theme.fills
            k = (idx - 1001) if idx >= 1001 else idx - 1
            if 0 <= k < len(fills):
                wrapper = etree.Element("x")
                wrapper.append(etree.fromstring(etree.tostring(fills[k]), _PARSER))
                paint = self._paint(ctx, wrapper, part, ph)
                if paint and paint.get("css"):
                    return f"background:{paint['css']}"
            if ph:
                return f"background:{ph.css()}"
        return None

    # -- paints and lines ----------------------------------------------------------------
    def _paint(self, ctx: _SlideCtx, holder, part: str, ph: Color | None = None) -> dict | None:
        """The fill inside `holder` (spPr, bgPr, tcPr…): {'kind', 'css', 'svg', 'defs'}."""
        if holder is None:
            return None
        for ch in holder:
            name = etree.QName(ch).localname
            if name == "noFill":
                return {"kind": "none", "css": "transparent", "svg": "none"}
            if name == "solidFill":
                c = ctx.colors.resolve(ch, ph)
                if c:
                    return {"kind": "solid", "css": c.css(), "svg": c.css()}
            if name == "gradFill":
                stops = []
                for gs in ch.iter(f"{A}gs"):
                    c = ctx.colors.resolve(gs[0] if len(gs) else None, ph) if len(gs) else None
                    if c:
                        stops.append(((_int(gs, "pos", 0) or 0) / 1000, c))
                if not stops:
                    continue
                stops.sort(key=lambda s: s[0])
                lin = ch.find(f"{A}lin")
                ang = ((_int(lin, "ang", 0) or 0) / 60000) if lin is not None else 90.0
                css = f"linear-gradient({_num(ang + 90)}deg, " + ", ".join(f"{c.css()} {_num(p)}%" for p, c in stops) + ")"
                gid = ctx.uid("g")
                rad = math.radians(ang)
                x1, y1 = 50 - 50 * math.cos(rad), 50 - 50 * math.sin(rad)
                x2, y2 = 50 + 50 * math.cos(rad), 50 + 50 * math.sin(rad)
                defs = (f'<linearGradient id="{gid}" x1="{_num(x1)}%" y1="{_num(y1)}%" x2="{_num(x2)}%" y2="{_num(y2)}%">'
                        + "".join(f'<stop offset="{_num(p)}%" stop-color="#{c.hex}" stop-opacity="{c.alpha:.3f}"/>' for p, c in stops)
                        + "</linearGradient>")
                return {"kind": "grad", "css": css, "svg": f"url(#{gid})", "defs": defs}
            if name == "blipFill":
                blip = ch.find(f"{A}blip")
                url = self.media_for(part, blip.get(f"{R}embed") if blip is not None else None)
                if url:
                    return {"kind": "img", "css": f'url("{url}") center / cover no-repeat', "svg": "none", "url": url}
                return {"kind": "none", "css": "transparent", "svg": "none"}
            if name == "pattFill":
                c = ctx.colors.resolve(ch.find(f"{A}fgClr"), ph) or ctx.colors.resolve(ch.find(f"{A}bgClr"), ph)
                if c:
                    return {"kind": "solid", "css": c.css(), "svg": c.css()}
            if name == "grpFill":
                return None
        return None

    def _style_paint(self, ctx: _SlideCtx, style, part: str) -> dict | None:
        ref = _child(style, f"{A}fillRef")
        idx = _int(ref, "idx", 0) or 0
        if ref is None or idx == 0:
            return None
        ph = ctx.colors.resolve(ref)
        fills = ctx.theme.bg_fills if idx >= 1001 else ctx.theme.fills
        k = (idx - 1001) if idx >= 1001 else idx - 1
        if 0 <= k < len(fills):
            wrapper = etree.Element("x")
            wrapper.append(etree.fromstring(etree.tostring(fills[k]), _PARSER))
            p = self._paint(ctx, wrapper, part, ph)
            if p:
                return p
        return {"kind": "solid", "css": ph.css(), "svg": ph.css()} if ph else None

    def _line(self, ctx: _SlideCtx, ln, style=None, part: str = "") -> dict | None:
        """{'w', 'color', 'dash', 'head', 'tail'} for a line, or None for no line."""
        ph = None
        theme_ln = None
        ref = _child(style, f"{A}lnRef")
        if ref is not None and (_int(ref, "idx", 0) or 0) > 0:
            ph = ctx.colors.resolve(ref)
            k = (_int(ref, "idx", 0) or 0) - 1
            if 0 <= k < len(ctx.theme.lines):
                theme_ln = ctx.theme.lines[k]
        if ln is None and theme_ln is None:
            return None
        width = None
        color = None
        dash = None
        for src in (theme_ln, ln):
            if src is None:
                continue
            if src.get("w") is not None:
                width = max(0.5, _px(src.get("w")))
            for ch in src:
                name = etree.QName(ch).localname
                if name == "noFill":
                    color = "none"
                elif name == "solidFill":
                    c = ctx.colors.resolve(ch, ph)
                    color = c.css() if c else color
                elif name == "gradFill":
                    gs = ch.find(f".//{A}gs")
                    c = ctx.colors.resolve(gs[0], ph) if gs is not None and len(gs) else None
                    color = c.css() if c else color
                elif name == "prstDash":
                    dash = ch.get("val")
        if color in (None, "none"):
            return None
        width = width if width is not None else 0.75
        head = ln.find(f"{A}headEnd") if ln is not None else None
        tail = ln.find(f"{A}tailEnd") if ln is not None else None
        return {"w": width, "color": color, "dash": dash,
                "head": head.get("type") if head is not None and head.get("type") not in (None, "none") else None,
                "tail": tail.get("type") if tail is not None and tail.get("type") not in (None, "none") else None}

    @staticmethod
    def _dash(dash: str | None, w: float) -> str | None:
        pat = {"dash": (4, 3), "sysDash": (3, 1), "dot": (1, 2), "sysDot": (1, 1), "lgDash": (8, 3),
               "dashDot": (4, 3, 1, 3), "lgDashDot": (8, 3, 1, 3), "sysDashDot": (3, 1, 1, 1)}.get(dash or "")
        if not pat:
            return None
        return ",".join(_num(max(1.0, w) * v) for v in pat)

    # -- shape tree ------------------------------------------------------------------------
    def _tree(self, ctx: _SlideCtx, root, part: str, *, decor: bool) -> list[str]:
        tree = _child(root, f"{P}cSld", f"{P}spTree")
        if tree is None:
            return []
        return self._children(ctx, tree, part, decor=decor, xf=None)

    def _children(self, ctx: _SlideCtx, tree, part: str, *, decor: bool, xf) -> list[str]:
        out: list[str] = []
        for el in tree:
            if el.tag == f"{MC}AlternateContent":
                pick = el.find(f"{MC}Fallback")
                if pick is None:
                    pick = el.find(f"{MC}Choice")
                if pick is not None:
                    out += self._children(ctx, pick, part, decor=decor, xf=xf)
                continue
            try:
                html_ = self._element(ctx, el, part, decor=decor, xf=xf)
            except Exception as exc:
                log.debug("Skipped a shape on slide %s: %s", ctx.n, exc)
                html_ = ""
            if html_:
                out.append(html_)
        return out

    def _element(self, ctx: _SlideCtx, el, part: str, *, decor: bool, xf) -> str:
        tag = etree.QName(el).localname
        if tag not in ("sp", "pic", "cxnSp", "grpSp", "graphicFrame"):
            return ""
        nv = el.find(f"./{P}nvSpPr/{P}cNvPr") if tag == "sp" else el.find(f".//{P}cNvPr")
        if nv is not None and _bool(nv, "hidden", False):
            return ""
        info = _ph_info(el) if tag in ("sp", "pic", "graphicFrame") else None
        if decor and info:
            return ""  # placeholders on layouts/masters are templates, not content
        if tag == "grpSp":
            return self._group(ctx, el, part, decor=decor, xf=xf)
        if tag == "graphicFrame":
            return self._frame(ctx, el, part, xf=xf)
        if tag == "pic":
            return self._picture(ctx, el, part, info, xf=xf)
        return self._shape(ctx, el, part, info, xf=xf, connector=tag == "cxnSp")

    def _xfrm(self, el, info, *, sp_pr_tag=f"{P}spPr"):
        xfrm = _child(el, sp_pr_tag, f"{A}xfrm")
        if xfrm is None and info is not None:
            for src in self._ph_chain(info):
                xfrm = _child(src, f"{P}spPr", f"{A}xfrm")
                if xfrm is not None:
                    break
        return xfrm

    def _ph_chain(self, info, ctx: _SlideCtx | None = None) -> list:
        """The layout and master placeholders a slide placeholder inherits from (nearest first)."""
        layout = getattr(self, "_cur_layout", None)
        out = []
        if layout is None or info is None:
            return out
        ptype, idx = info
        lph = _match_ph(layout.ph, ptype, idx)
        if lph is not None:
            out.append(lph)
            linfo = _ph_info(lph)
            ptype = linfo[0] if linfo else ptype
        mph = _match_ph(layout.master.ph, ptype, idx, master=True)
        if mph is not None:
            out.append(mph)
        return out

    @staticmethod
    def _box(xfrm, xf) -> tuple[float, float, float, float, float, bool, bool]:
        off, ext = _child(xfrm, f"{A}off"), _child(xfrm, f"{A}ext")
        x, y = (_int(off, "x", 0) or 0), (_int(off, "y", 0) or 0)
        cx, cy = (_int(ext, "cx", 0) or 0), (_int(ext, "cy", 0) or 0)
        if xf:
            x, y, cx, cy = xf(x, y, cx, cy)
        rot = (_int(xfrm, "rot", 0) or 0) / 60000 if xfrm is not None else 0
        return _px(x), _px(y), _px(cx), _px(cy), rot, bool(_bool(xfrm, "flipH", False)), bool(_bool(xfrm, "flipV", False))

    def _group(self, ctx: _SlideCtx, el, part: str, *, decor: bool, xf) -> str:
        xfrm = _child(el, f"{P}grpSpPr", f"{A}xfrm")
        if xfrm is None:
            return "".join(self._children(ctx, el, part, decor=decor, xf=xf))
        off, ext = _child(xfrm, f"{A}off"), _child(xfrm, f"{A}ext")
        choff, chext = _child(xfrm, f"{A}chOff"), _child(xfrm, f"{A}chExt")
        gx, gy = _int(off, "x", 0) or 0, _int(off, "y", 0) or 0
        gcx, gcy = _int(ext, "cx", 0) or 0, _int(ext, "cy", 0) or 0
        cx0, cy0 = _int(choff, "x", gx) or 0, _int(choff, "y", gy) or 0
        ccx, ccy = _int(chext, "cx", gcx) or gcx or 1, _int(chext, "cy", gcy) or gcy or 1
        sx, sy = (gcx / ccx if ccx else 1), (gcy / ccy if ccy else 1)

        def child_xf(x, y, cx, cy):
            nx, ny, ncx, ncy = gx + (x - cx0) * sx, gy + (y - cy0) * sy, cx * sx, cy * sy
            return xf(nx, ny, ncx, ncy) if xf else (nx, ny, ncx, ncy)
        return "".join(self._children(ctx, el, part, decor=decor, xf=child_xf))

    def _wrap(self, x, y, w, h, rot, inner: str, cls: str = "ps-sp", extra: list[str] | None = None) -> str:
        style = ["position:absolute", f"left:{_num(x)}px", f"top:{_num(y)}px", f"width:{_num(w)}px", f"height:{_num(h)}px"]
        if rot:
            style.append(f"transform:rotate({_num(rot)}deg)")
        style += extra or []
        return f'<div class="{cls}" style="{_attr(style)}">{inner}</div>'

    # -- shapes ---------------------------------------------------------------------------
    def _shape(self, ctx: _SlideCtx, el, part: str, info, *, xf, connector: bool) -> str:
        self._cur_layout = ctx.layout
        sp_pr = el.find(f"{P}spPr")
        style = el.find(f"{P}style")
        xfrm = self._xfrm(el, info)
        if xfrm is None:
            return ""
        x, y, w, h, rot, fh, fv = self._box(xfrm, xf)
        geom = _child(sp_pr, f"{A}prstGeom")
        prst = geom.get("prst") if geom is not None else ("rect" if _child(sp_pr, f"{A}custGeom") is None else "cust")
        chain = self._ph_chain(info) if info else []
        if geom is None and info:
            for src in chain:
                g = _child(src, f"{P}spPr", f"{A}prstGeom")
                if g is not None:
                    geom, prst = g, g.get("prst") or "rect"
                    break
        # Fill and line: own, then the placeholder chain, then the style references.
        paint = self._paint(ctx, sp_pr, part)
        line_el = _child(sp_pr, f"{A}ln")
        if paint is None and info:
            for src in chain:
                paint = self._paint(ctx, src.find(f"{P}spPr"), part)
                if paint:
                    break
        if paint is None and style is not None and not connector:
            paint = self._style_paint(ctx, style, part)
        if line_el is None and info:
            for src in chain:
                line_el = _child(src, f"{P}spPr", f"{A}ln")
                if line_el is not None:
                    break
        line = self._line(ctx, line_el, style, part)
        is_line = connector or prst in ("line", "straightConnector1", "bentConnector2", "bentConnector3",
                                       "curvedConnector3")
        parts: list[str] = []
        if is_line:
            if line:
                parts.append(self._svg_line(ctx, w, h, fh, fv, line, prst))
        else:
            svg = self._svg_geom(ctx, el, prst, geom, w, h, fh, fv, paint, line)
            if svg:
                parts.append(svg)
        tx = el.find(f"{P}txBody")
        if tx is not None:
            parts.append(self._text_body(ctx, tx, part, info, chain, style, w, h))
        if not parts:
            return ""
        return self._wrap(x, y, w, h, rot, "".join(parts))

    def _svg_line(self, ctx, w, h, fh, fv, line, prst) -> str:
        x1, y1, x2, y2 = 0.0, 0.0, w, h
        if fh:
            x1, x2 = x2, x1
        if fv:
            y1, y2 = y2, y1
        mid = ctx.uid("m")
        defs, attrs = [], ""
        size = max(3.0, line["w"] * 3)
        for end, key in (("start", "head"), ("end", "tail")):
            t = line.get(key)
            if not t:
                continue
            mk = f"{mid}{end[0]}"
            shape = '<circle cx="5" cy="5" r="4"/>' if t == "oval" else \
                ('<path d="M0,5 L5,0 L10,5 L5,10 Z"/>' if t == "diamond" else '<path d="M0,0 L10,5 L0,10 Z"/>')
            orient = "auto-start-reverse" if end == "start" else "auto"
            defs.append(f'<marker id="{mk}" viewBox="0 0 10 10" refX="{5 if t in ("oval", "diamond") else 9}" refY="5" '
                        f'markerWidth="{_num(size)}" markerHeight="{_num(size)}" markerUnits="userSpaceOnUse" orient="{orient}">'
                        f'<g fill="{_esc(line["color"])}">{shape}</g></marker>')
            attrs += f' marker-{end}="url(#{mk})"'
        dash = self._dash(line.get("dash"), line["w"])
        if prst in ("bentConnector3", "bentConnector2"):
            path = f"M{_num(x1)},{_num(y1)} L{_num((x1 + x2) / 2)},{_num(y1)} L{_num((x1 + x2) / 2)},{_num(y2)} L{_num(x2)},{_num(y2)}" \
                if prst == "bentConnector3" else f"M{_num(x1)},{_num(y1)} L{_num(x2)},{_num(y1)} L{_num(x2)},{_num(y2)}"
        else:
            path = f"M{_num(x1)},{_num(y1)} L{_num(x2)},{_num(y2)}"
        return (f'<svg class="ps-geom" width="{_num(max(w, 1))}" height="{_num(max(h, 1))}" overflow="visible" aria-hidden="true">'
                + (f"<defs>{''.join(defs)}</defs>" if defs else "")
                + f'<path d="{path}" fill="none" stroke="{_esc(line["color"])}" stroke-width="{_num(line["w"])}"'
                + (f' stroke-dasharray="{dash}"' if dash else "") + f'{attrs}/></svg>')

    def _svg_geom(self, ctx, el, prst, geom, w, h, fh, fv, paint, line) -> str:
        fill = paint["svg"] if paint else "none"
        if fill == "none" and not line:
            return ""
        defs = paint.get("defs", "") if paint else ""
        adj = {}
        av = _child(geom, f"{A}avLst")
        if av is not None:
            for gd in av:
                m = re.match(r"val\s+(-?\d+)", gd.get("fmla") or "")
                if m:
                    adj[gd.get("name")] = int(m.group(1))
        if prst == "cust":
            d = self._cust_path(_child(el.find(f"{P}spPr"), f"{A}custGeom"), w, h)
        else:
            d = preset_path(prst, w, h, adj)
        if not d:
            return ""
        stroke = ""
        if line:
            dash = self._dash(line.get("dash"), line["w"])
            stroke = f' stroke="{_esc(line["color"])}" stroke-width="{_num(line["w"])}"' + (f' stroke-dasharray="{dash}"' if dash else "")
        tf = ""
        if fh or fv:
            tf = f' transform="translate({_num(w if fh else 0)},{_num(h if fv else 0)}) scale({-1 if fh else 1},{-1 if fv else 1})"'
        img = ""
        if paint and paint.get("kind") == "img":
            cid = ctx.uid("c")
            defs += f'<clipPath id="{cid}"><path d="{d}"/></clipPath>'
            img = (f'<image href="{_esc(paint["url"])}" x="0" y="0" width="{_num(w)}" height="{_num(h)}" '
                   f'preserveAspectRatio="xMidYMid slice" clip-path="url(#{cid})"/>')
            fill = "none"
        return (f'<svg class="ps-geom" width="{_num(max(w, 1))}" height="{_num(max(h, 1))}" overflow="visible" aria-hidden="true">'
                + (f"<defs>{defs}</defs>" if defs else "") + f"<g{tf}>{img}"
                + f'<path d="{d}" fill="{_esc(fill)}"{stroke}/></g></svg>')

    @staticmethod
    def _cust_path(cust, w, h) -> str | None:
        if cust is None:
            return None
        out = []
        for path in cust.iter(f"{A}path"):
            pw = _int(path, "w", 0) or 0
            ph = _int(path, "h", 0) or 0
            sx = w / pw if pw else 1 / EMU_PER_PX
            sy = h / ph if ph else 1 / EMU_PER_PX
            cur = (0.0, 0.0)
            for cmd in path:
                name = etree.QName(cmd).localname
                pts = [(_int(pt, "x", 0) or 0, _int(pt, "y", 0) or 0) for pt in cmd.iter(f"{A}pt")]
                pts = [(px_ * sx, py_ * sy) for px_, py_ in pts]
                if name == "moveTo" and pts:
                    out.append(f"M{_num(pts[0][0])},{_num(pts[0][1])}")
                    cur = pts[0]
                elif name == "lnTo" and pts:
                    out.append(f"L{_num(pts[0][0])},{_num(pts[0][1])}")
                    cur = pts[0]
                elif name == "cubicBezTo" and len(pts) == 3:
                    out.append("C" + " ".join(f"{_num(a)},{_num(b)}" for a, b in pts))
                    cur = pts[-1]
                elif name == "quadBezTo" and len(pts) == 2:
                    out.append("Q" + " ".join(f"{_num(a)},{_num(b)}" for a, b in pts))
                    cur = pts[-1]
                elif name == "arcTo":
                    wr = (_int(cmd, "wR", 0) or 0) * sx
                    hr = (_int(cmd, "hR", 0) or 0) * sy
                    st = math.radians((_int(cmd, "stAng", 0) or 0) / 60000)
                    sw = math.radians((_int(cmd, "swAng", 0) or 0) / 60000)
                    cxp = cur[0] - wr * math.cos(st)
                    cyp = cur[1] - hr * math.sin(st)
                    ex = cxp + wr * math.cos(st + sw)
                    ey = cyp + hr * math.sin(st + sw)
                    large = 1 if abs(sw) > math.pi else 0
                    sweep = 1 if sw > 0 else 0
                    out.append(f"A{_num(wr)},{_num(hr)} 0 {large},{sweep} {_num(ex)},{_num(ey)}")
                    cur = (ex, ey)
                elif name == "close":
                    out.append("Z")
        return " ".join(out) or None

    # -- pictures --------------------------------------------------------------------------
    def _picture(self, ctx: _SlideCtx, el, part: str, info, *, xf) -> str:
        self._cur_layout = ctx.layout
        xfrm = self._xfrm(el, info)
        if xfrm is None:
            return ""
        x, y, w, h, rot, fh, fv = self._box(xfrm, xf)
        blip_fill = el.find(f"{P}blipFill")
        blip = _child(blip_fill, f"{A}blip")
        url = self.media_for(part, blip.get(f"{R}embed") if blip is not None else None)
        sp_pr = el.find(f"{P}spPr")
        geom = _child(sp_pr, f"{A}prstGeom")
        prst = geom.get("prst") if geom is not None else "rect"
        extra = []
        if prst == "ellipse":
            extra.append("border-radius:50%")
        elif prst == "roundRect":
            extra.append(f"border-radius:{_num(min(w, h) * 0.1667)}px")
        line = self._line(ctx, _child(sp_pr, f"{A}ln"), el.find(f"{P}style"), part)
        if line:
            extra.append(f"box-shadow:0 0 0 {_num(line['w'])}px {line['color']}")
        if not url:
            return self._wrap(x, y, w, h, rot, '<div class="ps-missing" title="This picture can\'t be shown here"></div>', "ps-sp ps-pic", extra)
        opacity = ""
        amf = blip.find(f"{A}alphaModFix") if blip is not None else None
        if amf is not None:
            opacity = f";opacity:{(_int(amf, 'amt', 100000) or 0) / 100000:.3f}"
        src = _child(blip_fill, f"{A}srcRect")
        flip = ""
        if fh or fv:
            flip = f";transform:scale({-1 if fh else 1},{-1 if fv else 1})"
        if src is not None and any(src.get(k) for k in ("l", "t", "r", "b")):
            l_, t_, r_, b_ = ((_int(src, k, 0) or 0) / 100000 for k in ("l", "t", "r", "b"))
            iw = w / max(0.01, 1 - l_ - r_)
            ih = h / max(0.01, 1 - t_ - b_)
            img = (f'<img src="{_esc(url)}" alt="" draggable="false" style="position:absolute;max-width:none;left:{_num(-l_ * iw)}px;'
                   f'top:{_num(-t_ * ih)}px;width:{_num(iw)}px;height:{_num(ih)}px{opacity}">')
            inner = f'<div style="position:absolute;inset:0;overflow:hidden{flip}">{img}</div>'
        else:
            inner = f'<img src="{_esc(url)}" alt="" draggable="false" style="position:absolute;inset:0;width:100%;height:100%;max-width:none{opacity}{flip}">'
        extra.append("overflow:hidden")
        return self._wrap(x, y, w, h, rot, inner, "ps-sp ps-pic", extra)

    # -- graphic frames: tables, charts, diagrams --------------------------------------------
    def _frame(self, ctx: _SlideCtx, el, part: str, *, xf) -> str:
        xfrm = el.find(f"{P}xfrm")
        if xfrm is None:
            return ""
        x, y, w, h, rot, _fh, _fv = self._box(xfrm, xf)
        data = el.find(f"./{A}graphic/{A}graphicData")
        if data is None:
            return ""
        uri = data.get("uri") or ""
        if uri.endswith("/table"):
            tbl = data.find(f"{A}tbl")
            return self._wrap(x, y, w, h, rot, self._table(ctx, tbl, part, w), "ps-sp ps-tbl", ["overflow:visible"]) if tbl is not None else ""
        if uri.endswith("/chart"):
            ch = data.find(f"{C}chart")
            rid = ch.get(f"{R}id") if ch is not None else None
            rel = self.pkg.rels(part).get(rid or "")
            if rel and not rel["external"]:
                try:
                    svg = render_chart(self, ctx, rel["target"], w, h)
                except Exception as exc:
                    log.debug("Chart on slide %s skipped: %s", ctx.n, exc)
                    svg = ""
                if svg:
                    return self._wrap(x, y, w, h, rot, svg, "ps-sp ps-chart")
            return self._wrap(x, y, w, h, rot, '<div class="ps-chart-missing">Chart</div>', "ps-sp ps-chart")
        if uri.endswith("/diagram"):
            drawing = self.pkg.rel_of_type(part, "diagramDrawing")
            droot = self.pkg.xml(drawing) if drawing and self.pkg.has(drawing) else None
            tree = droot.find(f".//{DSP}spTree") if droot is not None else None
            if tree is not None:
                ox, oy = (_int(_child(xfrm, f"{A}off"), "x", 0) or 0), (_int(_child(xfrm, f"{A}off"), "y", 0) or 0)

                def dxf(x_, y_, cx_, cy_):
                    nx, ny = x_ + ox, y_ + oy
                    return xf(nx, ny, cx_, cy_) if xf else (nx, ny, cx_, cy_)
                out = []
                for sp in tree.iter(f"{DSP}sp"):
                    try:
                        out.append(self._dsp_shape(ctx, sp, drawing, dxf))
                    except Exception:
                        continue
                return "".join(out)
            return ""
        # OLE objects and anything else: their preview picture, if there is one.
        pic = el.find(f".//{P}pic")
        if pic is not None:
            return self._picture(ctx, pic, part, None, xf=xf)
        return ""

    def _dsp_shape(self, ctx, sp, part, xf) -> str:
        sp_pr = sp.find(f"{DSP}spPr")
        xfrm = _child(sp_pr, f"{A}xfrm")
        if xfrm is None:
            return ""
        x, y, w, h, rot, fh, fv = self._box(xfrm, xf)
        style = sp.find(f"{DSP}style")
        geom = _child(sp_pr, f"{A}prstGeom")
        prst = geom.get("prst") if geom is not None else "rect"
        paint = self._paint(ctx, sp_pr, part) or (self._style_paint(ctx, style, part) if style is not None else None)
        line = self._line(ctx, _child(sp_pr, f"{A}ln"), style, part)
        parts = [self._svg_geom(ctx, sp, prst, geom, w, h, fh, fv, paint, line)]
        tx = sp.find(f"{DSP}txBody")
        if tx is not None:
            parts.append(self._text_body(ctx, tx, part, None, [], style, w, h))
        return self._wrap(x, y, w, h, rot, "".join(p for p in parts if p))

    # -- tables ---------------------------------------------------------------------------
    def _table_style(self, tbl):
        style_id = _child(tbl, f"{A}tblPr", f"{A}tableStyleId")
        sid = style_id.text.strip() if style_id is not None and style_id.text else None
        root = self.table_styles
        if root is None or not sid:
            return None  # a table without a style id has no style (only its own cell formatting)
        for st in root.iter(f"{A}tblStyle"):
            if st.get("styleId") == sid:
                return st
        return None

    def _table(self, ctx: _SlideCtx, tbl, part: str, frame_w: float) -> str:
        cols = [_px(g.get("w")) for g in tbl.iter(f"{A}gridCol")]
        tbl_pr = tbl.find(f"{A}tblPr")
        flags = {k: bool(_bool(tbl_pr, k, False)) for k in ("firstRow", "lastRow", "firstCol", "lastCol", "bandRow", "bandCol")}
        style = self._table_style(tbl)
        rows = list(tbl.iter(f"{A}tr"))
        nrows = len(rows)
        out = [f'<table class="ps-pt" style="width:{_num(sum(cols) or frame_w)}px"><colgroup>'
               + "".join(f'<col style="width:{_num(c)}px">' for c in cols) + "</colgroup>"]
        for ri, tr in enumerate(rows):
            out.append(f'<tr style="height:{_num(_px(tr.get("h")))}px">')
            ci = 0
            for tc in tr.iter(f"{A}tc"):
                if _bool(tc, "hMerge", False) or _bool(tc, "vMerge", False):
                    ci += 1
                    continue
                span = _int(tc, "gridSpan", 1) or 1
                rspan = _int(tc, "rowSpan", 1) or 1
                parts = self._cell_parts(style, flags, ri, ci, nrows, len(cols))
                out.append(self._cell(ctx, tc, part, parts, span, rspan, ci, ri, len(cols), nrows))
                ci += span
            out.append("</tr>")
        out.append("</table>")
        return "".join(out)

    @staticmethod
    def _cell_parts(style, flags, ri, ci, nrows, ncols) -> list:
        """Table-style sections that apply to a cell, lowest priority first."""
        if style is None:
            return []
        names = ["wholeTbl"]
        data_r = ri - (1 if flags["firstRow"] else 0)
        if flags["bandRow"] and data_r >= 0 and not (flags["lastRow"] and ri == nrows - 1):
            names.append("band1H" if data_r % 2 == 0 else "band2H")
        data_c = ci - (1 if flags["firstCol"] else 0)
        if flags["bandCol"] and data_c >= 0:
            names.append("band1V" if data_c % 2 == 0 else "band2V")
        if flags["firstCol"] and ci == 0:
            names.append("firstCol")
        if flags["lastCol"] and ci == ncols - 1:
            names.append("lastCol")
        if flags["lastRow"] and ri == nrows - 1:
            names.append("lastRow")
        if flags["firstRow"] and ri == 0:
            names.append("firstRow")
        return [style.find(f"{A}{n}") for n in names if style.find(f"{A}{n}") is not None]

    def _cell(self, ctx, tc, part, sparts, span, rspan, ci, ri, ncols, nrows) -> str:
        tc_pr = tc.find(f"{A}tcPr")
        fill = None
        borders = {}
        txt = {}
        for sp in sparts:
            tcs = sp.find(f"{A}tcStyle")
            if tcs is not None:
                f = tcs.find(f"{A}fill")
                if f is not None:
                    fill = self._paint(ctx, f, part) or fill
                fref = tcs.find(f"{A}fillRef")
                if fref is not None:
                    c = ctx.colors.resolve(fref)
                    if c:
                        fill = {"kind": "solid", "css": c.css()}
                bdr = tcs.find(f"{A}tcBdr")
                if bdr is not None:
                    for side in bdr:
                        nm = etree.QName(side).localname
                        ln = side.find(f"{A}ln")
                        if ln is not None:
                            borders[nm] = self._line(ctx, ln)
            tts = sp.find(f"{A}tcTxStyle")
            if tts is not None:
                if tts.get("b") == "on":
                    txt["b"] = "1"
                if tts.get("i") == "on":
                    txt["i"] = "1"
                c = None
                for ch in tts:
                    if etree.QName(ch).localname in ("srgbClr", "schemeClr", "sysClr", "prstClr"):
                        c = ctx.colors.resolve(ch)
                    elif etree.QName(ch).localname == "fontRef":
                        c = ctx.colors.resolve(ch) or c
                if c:
                    txt["color"] = c
        own = self._paint(ctx, tc_pr, part) if tc_pr is not None else None
        if own is not None:
            fill = own
        style = []
        if fill and fill.get("css") and fill.get("kind") != "none":
            style.append(f"background:{fill['css']}")
        # Borders: style borders on the outside/inside, then the cell's own.
        side_map = {"lnL": "left", "lnR": "right", "lnT": "top", "lnB": "bottom"}
        resolved = {
            "left": borders.get("left") if ci == 0 else borders.get("insideV"),
            "right": borders.get("right") if ci + span >= ncols else borders.get("insideV"),
            "top": borders.get("top") if ri == 0 else borders.get("insideH"),
            "bottom": borders.get("bottom") if ri + rspan >= nrows else borders.get("insideH"),
        }
        if tc_pr is not None:
            for tag, side in side_map.items():
                ln = tc_pr.find(f"{A}{tag}")
                if ln is not None:
                    resolved[side] = self._line(ctx, ln)
        for side, ln in resolved.items():
            if ln:
                style.append(f"border-{side}:{_num(ln['w'])}px solid {ln['color']}")
        mar = {k: _px(_int(tc_pr, k, d)) for k, d in (("marL", 91440), ("marR", 91440), ("marT", 45720), ("marB", 45720))}
        style.append(f"padding:{_num(mar['marT'])}px {_num(mar['marR'])}px {_num(mar['marB'])}px {_num(mar['marL'])}px")
        anchor = (tc_pr.get("anchor") if tc_pr is not None else None) or "t"
        style.append("vertical-align:" + {"t": "top", "ctr": "middle", "b": "bottom"}.get(anchor, "top"))
        tx = tc.find(f"{A}txBody")
        inner = self._paragraphs(ctx, tx, part, base_sources=[self.default_text], cell_txt=txt) if tx is not None else ""
        attrs = (f' colspan="{span}"' if span > 1 else "") + (f' rowspan="{rspan}"' if rspan > 1 else "")
        return f'<td{attrs} style="{_attr(style)}">{inner}</td>'

    # -- text --------------------------------------------------------------------------------
    def _text_body(self, ctx: _SlideCtx, tx, part: str, info, chain: list, style, w: float, h: float) -> str:
        body = tx.find(f"{A}bodyPr")
        merged: dict = {}
        autofit = None
        for src in list(reversed(chain)) + [None]:
            bp = _child(src, f"{P}txBody", f"{A}bodyPr") if src is not None else body
            if bp is None:
                continue
            merged.update(bp.attrib)
            for ch in bp:
                n = etree.QName(ch).localname
                if n in ("normAutofit", "spAutoFit", "noAutofit"):
                    autofit = ch
        ins = {k: _px(int(merged.get(k, d))) for k, d in (("lIns", 91440), ("tIns", 45720), ("rIns", 91440), ("bIns", 45720))}
        anchor = merged.get("anchor", "t")
        scale = 1.0
        spacing_cut = 0.0
        if autofit is not None and etree.QName(autofit).localname == "normAutofit":
            scale = (_int(autofit, "fontScale", 100000) or 100000) / 100000
            spacing_cut = (_int(autofit, "lnSpcReduction", 0) or 0) / 100000
        # Text style sources, lowest priority first.
        sources = []
        fontref = {}
        if info:
            ptype = info[0]
            master = ctx.master
            if master is not None:
                if ptype in ("title", "ctrTitle"):
                    sources.append(master.title_style)
                elif ptype in ("dt", "ftr", "sldNum", "hdr"):
                    sources.append(master.other_style)
                else:
                    sources.append(master.body_style)
            for src in reversed(chain):
                sources.append(_child(src, f"{P}txBody", f"{A}lstStyle"))
        else:
            sources.append(self.default_text)
            fr = _child(style, f"{A}fontRef")
            if fr is not None:
                c = ctx.colors.resolve(fr)
                if c:
                    fontref["color"] = c
                if fr.get("idx") in ("major", "minor"):
                    fontref["latin"] = "+mj-lt" if fr.get("idx") == "major" else "+mn-lt"
        sources.append(tx.find(f"{A}lstStyle"))
        inner = self._paragraphs(ctx, tx, part, base_sources=sources, scale=scale, spacing_cut=spacing_cut,
                                 fontref=fontref, is_title=bool(info and info[0] in ("title", "ctrTitle")))
        just = {"t": "flex-start", "ctr": "center", "b": "flex-end", "just": "center", "dist": "center"}.get(anchor, "flex-start")
        st = ["position:absolute", "inset:0", "display:flex", "flex-direction:column", f"justify-content:{just}",
              f"padding:{_num(ins['tIns'])}px {_num(ins['rIns'])}px {_num(ins['bIns'])}px {_num(ins['lIns'])}px"]
        if merged.get("wrap") == "none":
            st.append("white-space:pre")
        cols = int(merged.get("numCol", "1") or 1)
        inner_style = [f"column-count:{cols}"] if cols > 1 else []
        return (f'<div class="ps-tx" style="{_attr(st)}"><div class="ps-tb"'
                + (f' style="{_attr(inner_style)}"' if inner_style else "") + f">{inner}</div></div>")

    def _font_family(self, ctx: _SlideCtx, name: str | None, is_title: bool) -> str | None:
        if not name:
            name = "+mj-lt" if is_title else "+mn-lt"
        if name.startswith("+mj"):
            name = ctx.theme.major
        elif name.startswith("+mn"):
            name = ctx.theme.minor
        return font_stack(name)

    def _paragraphs(self, ctx: _SlideCtx, tx, part: str, *, base_sources: list, scale: float = 1.0, spacing_cut: float = 0.0,
                    fontref: dict | None = None, is_title: bool = False, cell_txt: dict | None = None) -> str:
        out = []
        counters: dict[int, int] = {}
        for p in tx.findall(f"{A}p"):
            ppr = p.find(f"{A}pPr")
            lvl = _int(ppr, "lvl", 0) or 0
            props: dict = {}
            for src in base_sources:
                props = _merge(props, _lvl_props(src, lvl))
            props = _merge(props, _ppr_props(ppr))
            def_r = dict(props.get("defRPr", {}))
            if fontref:
                if "color" in fontref and "fill" not in def_r:
                    def_r["_color"] = fontref["color"]
                if "latin" in fontref and "latin" not in def_r:
                    def_r["latin"] = fontref["latin"]
            if cell_txt:
                if cell_txt.get("b") and "b" not in def_r:
                    def_r["b"] = "1"
                if cell_txt.get("i") and "i" not in def_r:
                    def_r["i"] = "1"
                if cell_txt.get("color") and "fill" not in def_r:
                    def_r["_color"] = cell_txt["color"]
            runs_html, first_size, first_color, plain = self._runs(ctx, p, part, def_r, scale, is_title)
            ctx.text.append(plain)
            if is_title and plain.strip() and not ctx.title:
                ctx.title = plain.strip()[:200]
            size = first_size
            style = ["margin:0", f"font-size:{_num(size)}px"]
            algn = props.get("algn", "l")
            style.append("text-align:" + {"l": "left", "ctr": "center", "r": "right", "just": "justify", "dist": "justify"}.get(algn, "left"))
            mar_l = _px(props.get("marL", 0))
            indent = _px(props.get("indent", 0))
            if mar_l:
                style.append(f"padding-left:{_num(mar_l)}px")
            if indent:
                style.append(f"text-indent:{_num(indent)}px")
            lh = 1.2
            ln_spc = props.get("lnSpc")
            if ln_spc is not None:
                pct = _child(ln_spc, f"{A}spcPct")
                pts = _child(ln_spc, f"{A}spcPts")
                if pct is not None:
                    lh = 1.2 * (_int(pct, "val", 100000) or 100000) / 100000
                elif pts is not None:
                    lh = None
                    style.append(f"line-height:{_num((_int(pts, 'val', 0) or 0) / 100 * 4 / 3 * scale)}px")
            if lh is not None:
                style.append(f"line-height:{_num(lh * (1 - spacing_cut))}")
            for key, css in (("spcBef", "margin-top"), ("spcAft", "margin-bottom")):
                el = props.get(key)
                if el is None:
                    continue
                pct = _child(el, f"{A}spcPct")
                pts = _child(el, f"{A}spcPts")
                if pts is not None:
                    style.append(f"{css}:{_num((_int(pts, 'val', 0) or 0) / 100 * 4 / 3 * scale)}px")
                elif pct is not None:
                    style.append(f"{css}:{_num(1.2 * (_int(pct, 'val', 0) or 0) / 100000 * (1 - spacing_cut))}em")
            bullet = self._bullet(ctx, props, lvl, counters, size, first_color, indent, bool(plain.strip()))
            out.append(f'<p style="{_attr(style)}">{bullet}{runs_html}</p>')
        return "".join(out)

    def _bullet(self, ctx, props, lvl, counters, size, color, indent, has_text) -> str:
        bu = props.get("bu")
        kind = etree.QName(bu).localname if bu is not None else "buNone"
        if kind not in ("buChar", "buAutoNum") or not has_text:
            if kind != "buAutoNum":
                counters.pop(lvl, None)
            return ""
        for k in list(counters):
            if k > lvl:
                counters.pop(k)
        if kind == "buChar":
            ch = bu.get("char") or "•"
            font = props.get("buFont")
            face = (font.get("typeface") if font is not None else "") or ""
            if face.lower() in ("wingdings", "wingdings 2", "wingdings 3", "symbol") or ch in SYMBOL_BULLETS:
                ch = SYMBOL_BULLETS.get(ch, "•")
            text = ch
        else:
            scheme = bu.get("type") or "arabicPeriod"
            start = _int(bu, "startAt", 1) or 1
            counters[lvl] = counters.get(lvl, start - 1) + 1
            text = autonum(scheme, counters[lvl])
        st = []
        bclr = props.get("buClr")
        c = ctx.colors.resolve(bclr) if bclr is not None else None
        if c:
            st.append(f"color:{c.css()}")
        elif color:
            st.append(f"color:{color}")
        bsz = props.get("buSz")
        if bsz is not None and etree.QName(bsz).localname == "buSzPct":
            st.append(f"font-size:{_num(size * (_int(bsz, 'val', 100000) or 100000) / 100000)}px")
        elif bsz is not None and etree.QName(bsz).localname == "buSzPts":
            st.append(f"font-size:{_num((_int(bsz, 'val', 0) or 0) / 100 * 4 / 3)}px")
        hang = -indent if indent < 0 else 0
        if hang:
            st += ["display:inline-block", f"width:{_num(hang)}px", "text-indent:0"]
        else:
            st.append("margin-right:0.35em")
        return f'<span class="ps-bu" aria-hidden="true" style="{_attr(st)}">{_esc(text)}</span>'

    def _runs(self, ctx: _SlideCtx, p, part: str, def_r: dict, scale: float, is_title: bool):
        out = []
        plain = []
        first_size = None
        first_color = None
        for el in p:
            name = etree.QName(el).localname
            if name not in ("r", "fld", "br"):
                continue
            rp = dict(def_r)
            rp.update(_rpr_props(el.find(f"{A}rPr")))
            if "fill" in _rpr_props(el.find(f"{A}rPr")):
                rp.pop("_color", None)
            sz = (_int(rp, "sz", 1800) if isinstance(rp, etree._Element) else int(float(rp.get("sz", 1800)))) / 100 * 4 / 3 * scale
            color = None
            fill = rp.get("fill")
            if fill is not None and etree.QName(fill).localname == "solidFill":
                c = ctx.colors.resolve(fill)
                color = c.css() if c else None
            elif fill is not None and etree.QName(fill).localname == "noFill":
                color = "transparent"
            elif rp.get("_color") is not None:
                color = rp["_color"].css()
            elif rp.get("hlink") is not None:
                color = f"#{ctx.colors.scheme_hex('hlink')}"
            else:
                color = f"#{ctx.colors.scheme_hex('tx1')}"
            if name == "br":
                out.append("<br>")
                plain.append("\n")
                if first_size is None:
                    first_size = sz
                continue
            text = "".join(t.text or "" for t in el.iter(f"{A}t"))
            if name == "fld" and (el.get("type") or "").lower() == "slidenum":
                text = str(ctx.n)
            if first_size is None:
                first_size, first_color = sz, color
            st = [f"font-size:{_num(sz)}px", f"color:{color}"]
            fam = self._font_family(ctx, rp.get("latin"), is_title)
            if fam:
                st.append(f"font-family:{fam}")
            if rp.get("b") in ("1", "true"):
                st.append("font-weight:700")
            if rp.get("i") in ("1", "true"):
                st.append("font-style:italic")
            deco = []
            if rp.get("u") and rp.get("u") != "none":
                deco.append("underline")
            if rp.get("strike") and rp.get("strike") != "noStrike":
                deco.append("line-through")
            if deco:
                st.append("text-decoration:" + " ".join(deco))
            if rp.get("cap") == "all":
                st.append("text-transform:uppercase")
            elif rp.get("cap") == "small":
                st.append("font-variant:small-caps")
            spc = rp.get("spc")
            if spc:
                try:
                    st.append(f"letter-spacing:{_num(int(spc) / 100 * 4 / 3 * scale)}px")
                except ValueError:
                    pass
            base = rp.get("baseline")
            if base and base not in ("0",):
                try:
                    st.append(f"vertical-align:{'super' if int(base) > 0 else 'sub'}")
                    st.append(f"font-size:{_num(sz * 0.7)}px")
                except ValueError:
                    pass
            hl = rp.get("highlight")
            if hl is not None:
                c = ctx.colors.resolve(hl)
                if c:
                    st.append(f"background:{c.css()}")
            span = f'<span style="{_attr(st)}">{_esc(text)}</span>'
            link = rp.get("hlink")
            if link is not None:
                rel = self.pkg.rels(part).get(link.get(f"{R}id") or "")
                url = rel["target"] if rel and rel["external"] else ""
                if re.match(r"^(https?://|mailto:)", url or "", re.I):
                    span = f'<a href="{_esc(url)}" target="_blank" rel="noopener noreferrer">{span}</a>'
            out.append(span)
            plain.append(text)
        if first_size is None:
            end = p.find(f"{A}endParaRPr")
            props = dict(def_r)
            props.update(_rpr_props(end))
            first_size = float(props.get("sz", 1800)) / 100 * 4 / 3 * scale
            out.append(f'<span style="font-size:{_num(first_size)}px">​</span>')
        return "".join(out), first_size, first_color, "".join(plain)


# ---------------------------------------------------------------------------
# Preset geometry
# ---------------------------------------------------------------------------
def preset_path(prst: str, w: float, h: float, adj: dict) -> str:
    ss = min(w, h)
    hc, vc = w / 2, h / 2
    n = _num

    def a(name, default, lo=0, hi=100000):
        return max(lo, min(hi, adj.get(name, default)))

    if prst in ("rect", "flowChartProcess", "textBox", "frame", "bracketPair", "plaque") or not prst:
        return f"M0,0 H{n(w)} V{n(h)} H0 Z"
    if prst in ("roundRect", "flowChartAlternateProcess"):
        r = ss * a("adj", 16667, 0, 50000) / 100000
        return (f"M{n(r)},0 H{n(w - r)} A{n(r)},{n(r)} 0 0 1 {n(w)},{n(r)} V{n(h - r)} A{n(r)},{n(r)} 0 0 1 {n(w - r)},{n(h)} "
                f"H{n(r)} A{n(r)},{n(r)} 0 0 1 0,{n(h - r)} V{n(r)} A{n(r)},{n(r)} 0 0 1 {n(r)},0 Z")
    if prst == "flowChartTerminator":
        r = min(h / 2, w / 2)
        return f"M{n(r)},0 H{n(w - r)} A{n(r)},{n(r)} 0 0 1 {n(w - r)},{n(h)} H{n(r)} A{n(r)},{n(r)} 0 0 1 {n(r)},0 Z"
    if prst in ("ellipse", "flowChartConnector", "circle"):
        return (f"M0,{n(vc)} A{n(hc)},{n(vc)} 0 1 1 {n(w)},{n(vc)} A{n(hc)},{n(vc)} 0 1 1 0,{n(vc)} Z")
    if prst in ("round2SameRect",):
        r1 = ss * a("adj1", 16667, 0, 50000) / 100000
        r2 = ss * a("adj2", 0, 0, 50000) / 100000
        return (f"M{n(r1)},0 H{n(w - r1)} A{n(r1)},{n(r1)} 0 0 1 {n(w)},{n(r1)} V{n(h - r2)} "
                f"A{n(r2)},{n(r2)} 0 0 1 {n(w - r2)},{n(h)} H{n(r2)} A{n(r2)},{n(r2)} 0 0 1 0,{n(h - r2)} V{n(r1)} "
                f"A{n(r1)},{n(r1)} 0 0 1 {n(r1)},0 Z")
    if prst == "snip1Rect":
        d = ss * a("adj", 16667, 0, 50000) / 100000
        return f"M0,0 H{n(w - d)} L{n(w)},{n(d)} V{n(h)} H0 Z"
    if prst == "snip2SameRect":
        d = ss * a("adj1", 16667, 0, 50000) / 100000
        return f"M{n(d)},0 H{n(w - d)} L{n(w)},{n(d)} V{n(h)} H0 V{n(d)} Z"
    if prst in ("triangle", "flowChartExtract"):
        x = w * a("adj", 50000) / 100000
        return f"M{n(x)},0 L{n(w)},{n(h)} H0 Z"
    if prst == "rtTriangle":
        return f"M0,0 L{n(w)},{n(h)} H0 Z"
    if prst in ("diamond", "flowChartDecision"):
        return f"M{n(hc)},0 L{n(w)},{n(vc)} L{n(hc)},{n(h)} L0,{n(vc)} Z"
    if prst in ("parallelogram", "flowChartInputOutput"):
        x = ss * a("adj", 25000, 0, 100000 * w / ss if ss else 0) / 100000
        return f"M0,{n(h)} L{n(x)},0 H{n(w)} L{n(w - x)},{n(h)} Z"
    if prst == "trapezoid":
        x = ss * a("adj", 25000, 0, 50000 * w / ss if ss else 0) / 100000
        return f"M0,{n(h)} L{n(x)},0 H{n(w - x)} L{n(w)},{n(h)} Z"
    if prst == "hexagon":
        x = ss * a("adj", 25000, 0, 50000 * w / ss if ss else 0) / 100000
        return f"M0,{n(vc)} L{n(x)},0 H{n(w - x)} L{n(w)},{n(vc)} L{n(w - x)},{n(h)} H{n(x)} Z"
    if prst == "octagon":
        x = ss * a("adj", 29289, 0, 50000) / 100000
        return (f"M{n(x)},0 H{n(w - x)} L{n(w)},{n(x)} V{n(h - x)} L{n(w - x)},{n(h)} H{n(x)} L0,{n(h - x)} V{n(x)} Z")
    if prst == "pentagon":
        return (f"M{n(hc)},0 L{n(w)},{n(h * 0.38)} L{n(w * 0.81)},{n(h)} H{n(w * 0.19)} L0,{n(h * 0.38)} Z")
    if prst in ("homePlate",):
        x = w - ss * a("adj", 50000, 0, 100000 * w / ss if ss else 0) / 100000
        return f"M0,0 H{n(x)} L{n(w)},{n(vc)} L{n(x)},{n(h)} H0 Z"
    if prst == "chevron":
        d = ss * a("adj", 50000, 0, 100000 * w / ss if ss else 0) / 100000
        return f"M0,0 H{n(w - d)} L{n(w)},{n(vc)} L{n(w - d)},{n(h)} H0 L{n(d)},{n(vc)} Z"
    if prst in ("rightArrow", "leftArrow"):
        a1 = a("adj1", 50000)
        dx = ss * a("adj2", 50000, 0, 100000 * w / ss if ss else 0) / 100000
        dy = h * a1 / 200000
        if prst == "rightArrow":
            return (f"M0,{n(vc - dy)} H{n(w - dx)} V0 L{n(w)},{n(vc)} L{n(w - dx)},{n(h)} V{n(vc + dy)} H0 Z")
        return (f"M{n(w)},{n(vc - dy)} H{n(dx)} V0 L0,{n(vc)} L{n(dx)},{n(h)} V{n(vc + dy)} H{n(w)} Z")
    if prst in ("downArrow", "upArrow"):
        a1 = a("adj1", 50000)
        dy = ss * a("adj2", 50000, 0, 100000 * h / ss if ss else 0) / 100000
        dx = w * a1 / 200000
        if prst == "downArrow":
            return (f"M{n(hc - dx)},0 H{n(hc + dx)} V{n(h - dy)} H{n(w)} L{n(hc)},{n(h)} L0,{n(h - dy)} H{n(hc - dx)} Z")
        return (f"M{n(hc - dx)},{n(h)} H{n(hc + dx)} V{n(dy)} H{n(w)} L{n(hc)},0 L0,{n(dy)} H{n(hc - dx)} Z")
    if prst == "leftRightArrow":
        a1 = a("adj1", 50000)
        dx = ss * a("adj2", 50000, 0, 50000 * w / ss if ss else 0) / 100000
        dy = h * a1 / 200000
        return (f"M0,{n(vc)} L{n(dx)},0 V{n(vc - dy)} H{n(w - dx)} V0 L{n(w)},{n(vc)} L{n(w - dx)},{n(h)} "
                f"V{n(vc + dy)} H{n(dx)} V{n(h)} Z")
    if prst in ("plus", "mathPlus"):
        d = ss * a("adj", 25000, 0, 50000) / 100000
        return (f"M{n(d)},0 H{n(w - d)} V{n(d)} H{n(w)} V{n(h - d)} H{n(w - d)} V{n(h)} H{n(d)} V{n(h - d)} H0 V{n(d)} H{n(d)} Z"
                if prst == "plus" else
                f"M{n(hc - d / 2)},{n(vc - h / 4)} H{n(hc + d / 2)} V{n(vc - d / 2)} H{n(hc + w / 4)} V{n(vc + d / 2)} "
                f"H{n(hc + d / 2)} V{n(vc + h / 4)} H{n(hc - d / 2)} V{n(vc + d / 2)} H{n(hc - w / 4)} V{n(vc - d / 2)} H{n(hc - d / 2)} Z")
    if prst in ("star5", "star4", "star6"):
        pts = {"star4": 4, "star5": 5, "star6": 6}[prst]
        inner = 0.4 if pts != 4 else 0.3
        coords = []
        for i in range(pts * 2):
            ang = -math.pi / 2 + i * math.pi / pts
            rr = 1 if i % 2 == 0 else inner
            coords.append(f"{n(hc + hc * rr * math.cos(ang))},{n(vc + vc * rr * math.sin(ang))}")
        return "M" + " L".join(coords) + " Z"
    if prst == "can":
        e = ss * a("adj", 25000, 0, 50000) / 100000 / 2
        return (f"M0,{n(e)} A{n(hc)},{n(e)} 0 0 1 {n(w)},{n(e)} V{n(h - e)} A{n(hc)},{n(e)} 0 0 1 0,{n(h - e)} Z "
                f"M0,{n(e)} A{n(hc)},{n(e)} 0 0 0 {n(w)},{n(e)}")
    if prst in ("wedgeRectCallout", "wedgeRoundRectCallout"):
        return f"M0,0 H{n(w)} V{n(h)} H{n(w * 0.4)} L{n(w * 0.2)},{n(h * 1.25)} L{n(w * 0.25)},{n(h)} H0 Z"
    if prst == "donut":
        d = ss * a("adj", 25000, 0, 50000) / 100000
        return (f"M0,{n(vc)} A{n(hc)},{n(vc)} 0 1 1 {n(w)},{n(vc)} A{n(hc)},{n(vc)} 0 1 1 0,{n(vc)} Z "
                f"M{n(d)},{n(vc)} A{n(hc - d)},{n(vc - d)} 0 1 0 {n(w - d)},{n(vc)} A{n(hc - d)},{n(vc - d)} 0 1 0 {n(d)},{n(vc)} Z")
    return f"M0,0 H{n(w)} V{n(h)} H0 Z"


def _roman(n: int) -> str:
    vals = [(1000, "M"), (900, "CM"), (500, "D"), (400, "CD"), (100, "C"), (90, "XC"), (50, "L"), (40, "XL"),
            (10, "X"), (9, "IX"), (5, "V"), (4, "IV"), (1, "I")]
    out = ""
    for v, s in vals:
        while n >= v:
            out += s
            n -= v
    return out


def autonum(scheme: str, n: int) -> str:
    if scheme.startswith("roman"):
        core = _roman(n)
        core = core if scheme.startswith("romanUc") else core.lower()
    elif scheme.startswith("alpha"):
        core = ""
        k = n
        while k > 0:
            k, rem = divmod(k - 1, 26)
            core = chr(65 + rem) + core
        core = core if scheme.startswith("alphaUc") else core.lower()
    else:
        core = str(n)
    if scheme.endswith("ParenBoth"):
        return f"({core})"
    if scheme.endswith("ParenR"):
        return f"{core})"
    if scheme.endswith("Period"):
        return f"{core}."
    if scheme.endswith("Minus"):
        return f"- {core} -"
    return core


# ---------------------------------------------------------------------------
# Number formats (for chart labels)
# ---------------------------------------------------------------------------
def format_number(v: float | None, code: str | None) -> str:
    if v is None:
        return ""
    code = (code or "General").strip()
    if not code or code.lower() == "general":
        if abs(v) >= 1e15 or (v and abs(v) < 1e-4):
            return f"{v:.3g}"
        s = f"{v:,.10f}".rstrip("0").rstrip(".") if abs(v - round(v)) > 1e-9 else f"{int(round(v))}"
        return s if "," not in s or abs(v) >= 1000 else s.replace(",", "")
    sections = _split_sections(code)
    sec = sections[0]
    if v < 0 and len(sections) > 1:
        sec, v = sections[1], -v
    elif v == 0 and len(sections) > 2:
        sec = sections[2]
    out, num_done = [], False
    pct = "%" in _strip_literals(sec)
    if pct:
        v *= 100
    core = _strip_literals(sec)
    m = re.search(r"[#0?][#0?,]*(\.[#0?]*)?,*", core)
    whole = m.group(0) if m else ""
    int_part, _, frac_part = whole.partition(".")
    decimals = len(frac_part.rstrip(",")) if m and "." in whole else 0
    # Commas at the end of the number scale it down by 1,000 each ("#,##0,," shows millions).
    scale_commas = len(whole) - len(whole.rstrip(","))
    thousands = "," in int_part.rstrip(",")
    v = v / (1000 ** scale_commas)
    number = f"{abs(v):,.{decimals}f}" if thousands else f"{abs(v):.{decimals}f}"
    if v < 0 and len(sections) == 1:
        number = "-" + number
    i = 0
    while i < len(sec):
        ch = sec[i]
        if ch == '"':
            j = sec.find('"', i + 1)
            j = len(sec) if j < 0 else j
            out.append(sec[i + 1:j])
            i = j + 1
            continue
        if ch == "\\" and i + 1 < len(sec):
            out.append(sec[i + 1])
            i += 2
            continue
        if ch in "#0.,?":
            if not num_done:
                out.append(number)
                num_done = True
            i += 1
            continue
        if ch == "[":
            j = sec.find("]", i)
            i = len(sec) if j < 0 else j + 1
            continue
        if ch in "_*":
            i += 2
            continue
        out.append(ch)
        i += 1
    if not num_done:
        out.append(number)
    return "".join(out).strip()


def _split_sections(code: str) -> list[str]:
    out, cur, q = [], [], False
    for ch in code:
        if ch == '"':
            q = not q
        if ch == ";" and not q:
            out.append("".join(cur))
            cur = []
        else:
            cur.append(ch)
    out.append("".join(cur))
    return out


def _strip_literals(sec: str) -> str:
    return re.sub(r'"[^"]*"|\\.', "", sec)


# ---------------------------------------------------------------------------
# Charts (drawn from the values cached in the chart part)
# ---------------------------------------------------------------------------
def _cache_values(ref) -> list:
    if ref is None:
        return []
    pts = {}
    count = 0
    for cache in ref.iter(f"{C}numCache", f"{C}strCache", f"{C}numLit", f"{C}strLit"):
        pc = cache.find(f"{C}ptCount")
        count = max(count, _int(pc, "val", 0) or 0)
        for pt in cache.iter(f"{C}pt"):
            v = pt.find(f"{C}v")
            pts[_int(pt, "idx", 0) or 0] = v.text if v is not None else None
        break
    n = max(count, (max(pts) + 1) if pts else 0)
    return [pts.get(i) for i in range(n)]


def _fmt_code(ref) -> str | None:
    fc = ref.find(f".//{C}formatCode") if ref is not None else None
    return fc.text if fc is not None else None


def _txpr_size(el, default: float) -> tuple[float, str | None, bool]:
    if el is None:
        return default, None, False
    d = el.find(f".//{A}defRPr")
    if d is None:
        return default, None, False
    sz = _int(d, "sz", None)
    return (sz / 100 * 4 / 3 if sz else default), None, d.get("b") in ("1", "true")


def _txpr_color(deck_ctx, el) -> str | None:
    if el is None:
        return None
    d = el.find(f".//{A}defRPr")
    fill = _child(d, f"{A}solidFill") if d is not None else None
    c = deck_ctx.colors.resolve(fill) if fill is not None else None
    return c.css() if c else None


def _nice_ticks(lo: float, hi: float, n: int = 5) -> list[float]:
    if hi <= lo:
        hi = lo + 1
    span = hi - lo
    raw = span / max(1, n)
    mag = 10 ** math.floor(math.log10(raw)) if raw > 0 else 1
    step = next((m * mag for m in (1, 2, 2.5, 5, 10) if m * mag >= raw), 10 * mag)
    start = math.floor(lo / step) * step
    ticks = []
    t = start
    while len(ticks) < 50:
        ticks.append(round(t, 10))
        if t >= hi - step * 1e-6:
            break
        t += step
    return ticks


def render_chart(deck: Deck, ctx: _SlideCtx, part: str, w: float, h: float) -> str:
    root = deck.pkg.xml(part)
    if root is None:
        return ""
    chart = root.find(f"{C}chart")
    plot = _child(chart, f"{C}plotArea")
    if plot is None:
        return ""
    kinds = ("barChart", "bar3DChart", "lineChart", "line3DChart", "areaChart", "area3DChart", "pieChart", "pie3DChart",
             "doughnutChart", "scatterChart")
    ct = next((ch for ch in plot if etree.QName(ch).localname in kinds), None)
    if ct is None:
        return ""
    kind = etree.QName(ct).localname.replace("3D", "")
    accents = [ctx.colors.scheme_hex(f"accent{i}") for i in range(1, 7)]
    series = []
    for i, ser in enumerate(ct.findall(f"{C}ser")):
        tx = ser.find(f"{C}tx")
        name_vals = _cache_values(tx) if tx is not None else []
        if tx is not None and not name_vals:
            v = tx.find(f"{C}v")
            name_vals = [v.text] if v is not None else []
        cats = _cache_values(ser.find(f"{C}cat")) or _cache_values(ser.find(f"{C}xVal"))
        vals_raw = _cache_values(ser.find(f"{C}val")) or _cache_values(ser.find(f"{C}yVal"))
        vals = []
        for v in vals_raw:
            try:
                vals.append(float(v) if v not in (None, "") else None)
            except ValueError:
                vals.append(None)
        sp = ser.find(f"{C}spPr")
        col = None
        if sp is not None:
            f = sp.find(f"{A}solidFill")
            if f is not None:
                c = ctx.colors.resolve(f)
                col = c.css() if c else None
            if col is None and kind in ("lineChart", "scatterChart"):
                ln = _child(sp, f"{A}ln", f"{A}solidFill")
                c = ctx.colors.resolve(ln) if ln is not None else None
                col = c.css() if c else None
        point_colors = {}
        for dpt in ser.findall(f"{C}dPt"):
            idx = _int(dpt.find(f"{C}idx"), "val", 0) or 0
            f = _child(dpt, f"{C}spPr", f"{A}solidFill")
            c = ctx.colors.resolve(f) if f is not None else None
            if c:
                point_colors[idx] = c.css()
        series.append({"name": name_vals[0] if name_vals else f"Series {i + 1}", "cats": [c or "" for c in cats], "vals": vals,
                       "color": col or f"#{accents[i % 6]}", "points": point_colors,
                       "fmt": _fmt_code(ser.find(f"{C}val")), "dlbls": ser.find(f"{C}dLbls")})
    if not series:
        return ""
    cats = max((s["cats"] for s in series), key=len) or [str(i + 1) for i in range(max(len(s["vals"]) for s in series))]
    n_cat = max(len(cats), max(len(s["vals"]) for s in series))
    base_px, _, _ = _txpr_size(root.find(f"{C}txPr"), 13.33)
    font = font_stack(ctx.theme.minor) or "sans-serif"
    ink = f"#{ctx.colors.scheme_hex('tx1')}"
    muted = f"#{ctx.colors.scheme_hex('tx2')}"
    out = [f'<svg class="ps-chart-svg" width="{_num(w)}" height="{_num(h)}" viewBox="0 0 {_num(w)} {_num(h)}" '
           f'font-family="{_esc(font)}" aria-hidden="true">']
    top, left, right, bottom = 8.0, 8.0, w - 8.0, h - 8.0
    # Title
    title_el = chart.find(f"{C}title")
    auto_del = _bool(chart.find(f"{C}autoTitleDeleted"), "val", False)
    title = None
    if title_el is not None:
        title = "".join(t.text or "" for t in title_el.iter(f"{A}t")).strip() or (series[0]["name"] if len(series) == 1 else None)
    elif not auto_del and len(series) == 1 and kind not in ("pieChart", "doughnutChart"):
        title = None
    if title:
        tsz, _, _ = _txpr_size(title_el.find(f"{C}txPr") if title_el is not None else None, base_px * 1.3)
        out.append(f'<text x="{_num(w / 2)}" y="{_num(top + tsz)}" text-anchor="middle" font-size="{_num(tsz)}" fill="{ink}">{_esc(title)}</text>')
        top += tsz * 1.6
    # Legend
    legend = chart.find(f"{C}legend")
    if legend is not None and (len(series) > 1 or kind in ("pieChart", "doughnutChart")):
        pos = _child(legend, f"{C}legendPos")
        pos = pos.get("val") if pos is not None else "r"
        items = [(s["name"], s["color"]) for s in series] if kind not in ("pieChart", "doughnutChart") else \
            [(cats[i] if i < len(cats) else str(i + 1), series[0]["points"].get(i, f"#{accents[i % 6]}")) for i in range(n_cat)]
        lsz = base_px * 0.9
        if pos in ("b", "t"):
            total = sum(len(str(t)) * lsz * 0.55 + lsz * 2 for t, _ in items)
            lx = max(left, w / 2 - total / 2)
            ly = bottom - lsz * 0.3 if pos == "b" else top + lsz
            for t, c in items:
                out.append(f'<rect x="{_num(lx)}" y="{_num(ly - lsz * 0.8)}" width="{_num(lsz * 0.8)}" height="{_num(lsz * 0.8)}" fill="{_esc(c)}"/>'
                           f'<text x="{_num(lx + lsz * 1.1)}" y="{_num(ly)}" font-size="{_num(lsz)}" fill="{muted}">{_esc(str(t))}</text>')
                lx += len(str(t)) * lsz * 0.55 + lsz * 2
            if pos == "b":
                bottom -= lsz * 1.8
            else:
                top += lsz * 1.8
        else:
            lw = max(len(str(t)) for t, _ in items) * lsz * 0.55 + lsz * 2
            ly = (top + bottom) / 2 - len(items) * lsz * 0.75
            lx = right - lw
            for t, c in items:
                out.append(f'<rect x="{_num(lx)}" y="{_num(ly - lsz * 0.8)}" width="{_num(lsz * 0.8)}" height="{_num(lsz * 0.8)}" fill="{_esc(c)}"/>'
                           f'<text x="{_num(lx + lsz * 1.1)}" y="{_num(ly)}" font-size="{_num(lsz)}" fill="{muted}">{_esc(str(t))}</text>')
                ly += lsz * 1.5
            right -= lw + 6

    def dl_info(s):
        dl = s["dlbls"] if s["dlbls"] is not None else ct.find(f"{C}dLbls")
        show = _bool(_child(dl, f"{C}showVal"), "val", False) if dl is not None else False
        nf = _child(dl, f"{C}numFmt") if dl is not None else None
        code = nf.get("formatCode") if nf is not None else s["fmt"]
        sz, _, bold = _txpr_size(_child(dl, f"{C}txPr") if dl is not None else None, base_px)
        color = _txpr_color(ctx, _child(dl, f"{C}txPr") if dl is not None else None) or ink
        pos = _child(dl, f"{C}dLblPos") if dl is not None else None
        return show, code, sz, bold, color, (pos.get("val") if pos is not None else None)

    if kind in ("pieChart", "doughnutChart"):
        s = series[0]
        vals = [max(0.0, v or 0.0) for v in s["vals"]]
        total = sum(vals) or 1
        cx, cy = (left + right) / 2, (top + bottom) / 2
        rad = max(4.0, min(right - left, bottom - top) / 2 - 4)
        hole = 0.0
        if kind == "doughnutChart":
            hs = _child(ct, f"{C}holeSize")
            hole = rad * (_int(hs, "val", 50) or 50) / 100
        ang = -math.pi / 2
        show, code, sz, bold, lcolor, _ = dl_info(s)
        for i, v in enumerate(vals):
            if v <= 0:
                continue
            sweep = 2 * math.pi * v / total
            col = s["points"].get(i, f"#{accents[i % 6]}")
            x1, y1 = cx + rad * math.cos(ang), cy + rad * math.sin(ang)
            x2, y2 = cx + rad * math.cos(ang + sweep), cy + rad * math.sin(ang + sweep)
            large = 1 if sweep > math.pi else 0
            if sweep >= 2 * math.pi - 1e-6:
                d = f"M{_num(cx - rad)},{_num(cy)} A{_num(rad)},{_num(rad)} 0 1 1 {_num(cx + rad)},{_num(cy)} A{_num(rad)},{_num(rad)} 0 1 1 {_num(cx - rad)},{_num(cy)} Z"
            elif hole:
                hx1, hy1 = cx + hole * math.cos(ang + sweep), cy + hole * math.sin(ang + sweep)
                hx2, hy2 = cx + hole * math.cos(ang), cy + hole * math.sin(ang)
                d = (f"M{_num(x1)},{_num(y1)} A{_num(rad)},{_num(rad)} 0 {large} 1 {_num(x2)},{_num(y2)} L{_num(hx1)},{_num(hy1)} "
                     f"A{_num(hole)},{_num(hole)} 0 {large} 0 {_num(hx2)},{_num(hy2)} Z")
            else:
                d = f"M{_num(cx)},{_num(cy)} L{_num(x1)},{_num(y1)} A{_num(rad)},{_num(rad)} 0 {large} 1 {_num(x2)},{_num(y2)} Z"
            out.append(f'<path d="{d}" fill="{_esc(col)}" stroke="#fff" stroke-width="1"/>')
            if show:
                mid = ang + sweep / 2
                lr = (rad + hole) / 2 if hole else rad * 0.62
                out.append(f'<text x="{_num(cx + lr * math.cos(mid))}" y="{_num(cy + lr * math.sin(mid) + sz / 3)}" text-anchor="middle" '
                           f'font-size="{_num(sz)}"{" font-weight=\"700\"" if bold else ""} fill="{_esc(lcolor)}">{_esc(format_number(v, code))}</text>')
            ang += sweep
        out.append("</svg>")
        return "".join(out)

    # Axes charts
    bar_dir = _child(ct, f"{C}barDir")
    horizontal = kind == "barChart" and bar_dir is not None and bar_dir.get("val") == "bar"
    grouping_el = _child(ct, f"{C}grouping")
    grouping = grouping_el.get("val") if grouping_el is not None else "clustered"
    stacked = grouping in ("stacked", "percentStacked")
    percent = grouping == "percentStacked"
    cat_ax = plot.find(f"{C}catAx") if plot.find(f"{C}catAx") is not None else plot.find(f"{C}dateAx")
    val_ax = plot.find(f"{C}valAx")
    val_deleted = _bool(_child(val_ax, f"{C}delete"), "val", True) if _child(val_ax, f"{C}delete") is not None else False
    cat_deleted = _bool(_child(cat_ax, f"{C}delete"), "val", True) if _child(cat_ax, f"{C}delete") is not None else False
    cat_sz, _, _ = _txpr_size(_child(cat_ax, f"{C}txPr"), base_px)
    val_sz, _, _ = _txpr_size(_child(val_ax, f"{C}txPr"), base_px)
    cat_color = _txpr_color(ctx, _child(cat_ax, f"{C}txPr")) or muted
    val_color = _txpr_color(ctx, _child(val_ax, f"{C}txPr")) or muted
    if stacked:
        pos_tot = [sum(max(0.0, s["vals"][i] or 0) for s in series if i < len(s["vals"])) for i in range(n_cat)]
        neg_tot = [sum(min(0.0, s["vals"][i] or 0) for s in series if i < len(s["vals"])) for i in range(n_cat)]
        vmax, vmin = (100.0, 0.0) if percent else (max(pos_tot or [0]), min(neg_tot or [0]))
    else:
        allv = [v for s in series for v in s["vals"] if v is not None]
        vmax, vmin = max(allv or [0]), min(allv or [0])
    vmin = min(0.0, vmin)
    scaling = _child(val_ax, f"{C}scaling")
    mx, mn = _child(scaling, f"{C}max"), _child(scaling, f"{C}min")
    ticks = _nice_ticks(vmin, vmax if vmax > vmin else vmin + 1)
    lo = float(mn.get("val")) if mn is not None else ticks[0]
    hi = float(mx.get("val")) if mx is not None else ticks[-1]
    if hi <= lo:
        hi = lo + 1
    ticks = [t for t in _nice_ticks(lo, hi) if lo - 1e-9 <= t <= hi + 1e-9]
    vfmt_el = _child(val_ax, f"{C}numFmt")
    vfmt = vfmt_el.get("formatCode") if vfmt_el is not None else series[0]["fmt"]
    if not val_deleted:
        label_w = max(len(format_number(t, vfmt)) for t in ticks) * val_sz * 0.55 + 6
        if horizontal:
            bottom -= val_sz * 1.6
        else:
            left += label_w
    if not cat_deleted:
        if horizontal:
            left += min((right - left) * 0.4, max(len(str(c)) for c in cats) * cat_sz * 0.55 + 8) if cats else 0
        else:
            bottom -= cat_sz * (2.6 if any(len(str(c)) * cat_sz * 0.55 > (right - left) / max(1, n_cat) for c in cats) else 1.6)
    # Room for value labels at the end of the bars.
    if is_bar_kind := (kind == "barChart"):
        shown = [(s, dl_info(s)) for s in series]
        shown = [(s, d) for s, d in shown if d[0] and d[5] in (None, "outEnd") and not stacked]
        if shown:
            lab_sz = max(d[2] for _, d in shown)
            if horizontal:
                widest = max(len(format_number(v, d[1])) for s, d in shown for v in s["vals"] if v is not None)
                right -= widest * lab_sz * 0.58 + 6
            else:
                top += lab_sz * 1.3
    pw, ph_ = max(1.0, right - left), max(1.0, bottom - top)

    def vpos(v):
        frac = (v - lo) / (hi - lo)
        return (left + frac * pw) if horizontal else (bottom - frac * ph_)

    grid = _child(val_ax, f"{C}majorGridlines")
    for t in ticks:
        if grid is not None:
            if horizontal:
                out.append(f'<line x1="{_num(vpos(t))}" y1="{_num(top)}" x2="{_num(vpos(t))}" y2="{_num(bottom)}" stroke="#D9D9D9" stroke-width="0.75"/>')
            else:
                out.append(f'<line x1="{_num(left)}" y1="{_num(vpos(t))}" x2="{_num(right)}" y2="{_num(vpos(t))}" stroke="#D9D9D9" stroke-width="0.75"/>')
        if not val_deleted:
            label = _esc(format_number(t, vfmt))
            if horizontal:
                out.append(f'<text x="{_num(vpos(t))}" y="{_num(bottom + val_sz * 1.2)}" text-anchor="middle" font-size="{_num(val_sz)}" fill="{val_color}">{label}</text>')
            else:
                out.append(f'<text x="{_num(left - 6)}" y="{_num(vpos(t) + val_sz / 3)}" text-anchor="end" font-size="{_num(val_sz)}" fill="{val_color}">{label}</text>')
    band = (pw if horizontal else pw) / max(1, n_cat) if not horizontal else ph_ / max(1, n_cat)
    gap_el = _child(ct, f"{C}gapWidth")
    gap = (_int(gap_el, "val", 150) or 150) / 100
    is_bar = kind == "barChart"
    nser = 1 if stacked else len(series)
    bar_w = band / (nser + gap) if is_bar else 0
    zero = vpos(max(lo, min(hi, 0.0)))
    # Category axis line and labels
    ax_line = _child(cat_ax, f"{C}spPr", f"{A}ln")
    ax_col = None
    if ax_line is not None:
        f = ax_line.find(f"{A}solidFill")
        c = ctx.colors.resolve(f) if f is not None else None
        ax_col = c.css() if c else None
        if ax_line.find(f"{A}noFill") is not None:
            ax_col = "none"
    ax_col = ax_col or "#BFBFBF"
    if ax_col != "none" and not cat_deleted:
        if horizontal:
            out.append(f'<line x1="{_num(zero)}" y1="{_num(top)}" x2="{_num(zero)}" y2="{_num(bottom)}" stroke="{_esc(ax_col)}" stroke-width="1"/>')
        else:
            out.append(f'<line x1="{_num(left)}" y1="{_num(zero)}" x2="{_num(right)}" y2="{_num(zero)}" stroke="{_esc(ax_col)}" stroke-width="1"/>')
    rev = _child(_child(cat_ax, f"{C}scaling"), f"{C}orientation")
    reverse = rev is not None and rev.get("val") == "maxMin"
    for i in range(n_cat):
        k = (n_cat - 1 - i) if (horizontal and not reverse) else i
        label = str(cats[i]) if i < len(cats) else ""
        if cat_deleted or not label:
            continue
        if horizontal:
            cy = top + band * (k + 0.5)
            out.append(f'<text x="{_num(left - 6)}" y="{_num(cy + cat_sz / 3)}" text-anchor="end" font-size="{_num(cat_sz)}" fill="{cat_color}">{_esc(label)}</text>')
        else:
            cx = left + band * (i + 0.5)
            words = label.split()
            lines = [label]
            if len(label) * cat_sz * 0.55 > band and len(words) > 1:
                half = (len(words) + 1) // 2
                lines = [" ".join(words[:half]), " ".join(words[half:])]
            for li, line in enumerate(lines):
                out.append(f'<text x="{_num(cx)}" y="{_num(bottom + cat_sz * (1.25 + li * 1.15))}" text-anchor="middle" font-size="{_num(cat_sz)}" fill="{cat_color}">{_esc(line)}</text>')
    stack_pos = [0.0] * n_cat
    stack_neg = [0.0] * n_cat
    for si, s in enumerate(series):
        show, code, sz, bold, lcolor, lpos = dl_info(s)
        if kind in ("lineChart", "scatterChart", "areaChart"):
            pts = []
            for i, v in enumerate(s["vals"]):
                if v is None:
                    continue
                vv = v
                if stacked:
                    vv = stack_pos[i] + v
                    stack_pos[i] = vv
                x = left + band * (i + 0.5) if not horizontal else vpos(vv)
                y = vpos(vv) if not horizontal else top + band * (i + 0.5)
                pts.append((x, y, v))
            if not pts:
                continue
            path = "M" + " L".join(f"{_num(x)},{_num(y)}" for x, y, _ in pts)
            if kind == "areaChart":
                out.append(f'<path d="{path} L{_num(pts[-1][0])},{_num(zero)} L{_num(pts[0][0])},{_num(zero)} Z" fill="{_esc(s["color"])}" opacity="0.85"/>')
            else:
                out.append(f'<path d="{path}" fill="none" stroke="{_esc(s["color"])}" stroke-width="2.25"/>')
                for x, y, _ in pts:
                    out.append(f'<circle cx="{_num(x)}" cy="{_num(y)}" r="3" fill="{_esc(s["color"])}"/>')
            if show:
                for x, y, v in pts:
                    out.append(f'<text x="{_num(x)}" y="{_num(y - 7)}" text-anchor="middle" font-size="{_num(sz)}"'
                               f'{" font-weight=\"700\"" if bold else ""} fill="{_esc(lcolor)}">{_esc(format_number(v, code))}</text>')
            continue
        for i, v in enumerate(s["vals"]):
            if v is None:
                continue
            k = (n_cat - 1 - i) if (horizontal and not reverse) else i
            vv = (v / ((sum(max(0.0, x.get("vals")[i] or 0) for x in series if i < len(x["vals"])) or 1) * 100)) if percent else v
            if stacked:
                if vv >= 0:
                    a0, a1 = stack_pos[i], stack_pos[i] + vv
                    stack_pos[i] = a1
                else:
                    a0, a1 = stack_neg[i], stack_neg[i] + vv
                    stack_neg[i] = a1
                off = band * (gap / (1 + gap)) / 2
                bw = band - 2 * off
            else:
                a0, a1 = 0.0, vv
                off = band * gap / (nser + gap) / 2 + bar_w * si
                bw = bar_w
            p0, p1 = vpos(max(lo, min(hi, a0))), vpos(max(lo, min(hi, a1)))
            col = s["points"].get(i, s["color"])
            if horizontal:
                y0 = top + band * k + off
                x0, x1 = min(p0, p1), max(p0, p1)
                out.append(f'<rect x="{_num(x0)}" y="{_num(y0)}" width="{_num(max(0.5, x1 - x0))}" height="{_num(bw)}" fill="{_esc(col)}"/>')
                if show:
                    tx = x1 + 4 if lpos in (None, "outEnd") and not stacked else (x0 + x1) / 2
                    anchor = "start" if lpos in (None, "outEnd") and not stacked else "middle"
                    out.append(f'<text x="{_num(tx)}" y="{_num(y0 + bw / 2 + sz / 3)}" text-anchor="{anchor}" font-size="{_num(sz)}"'
                               f'{" font-weight=\"700\"" if bold else ""} fill="{_esc(lcolor)}">{_esc(format_number(v, code))}</text>')
            else:
                x0 = left + band * i + off
                y0, y1 = min(p0, p1), max(p0, p1)
                out.append(f'<rect x="{_num(x0)}" y="{_num(y0)}" width="{_num(bw)}" height="{_num(max(0.5, y1 - y0))}" fill="{_esc(col)}"/>')
                if show:
                    ty = y0 - 5 if lpos in (None, "outEnd") and not stacked else (y0 + y1) / 2 + sz / 3
                    out.append(f'<text x="{_num(x0 + bw / 2)}" y="{_num(ty)}" text-anchor="middle" font-size="{_num(sz)}"'
                               f'{" font-weight=\"700\"" if bold else ""} fill="{_esc(lcolor)}">{_esc(format_number(v, code))}</text>')
    out.append("</svg>")
    return "".join(out)


# ---------------------------------------------------------------------------
# Entry points
# ---------------------------------------------------------------------------
def render(path: Path, media_url) -> Rendered:
    """Render a .pptx file. `media_url(name)` gives the URL a picture will be served at."""
    deck = Deck(Path(path), media_url)
    return deck.render()


def slide_texts(path: Path) -> list[dict]:
    """Each slide's title, text and speaker notes, without drawing anything (for search)."""
    r = render(path, lambda name: "")
    return [{"n": s["n"], "title": s["title"], "text": s["text"], "notes": s["notes"]} for s in r.slides]
