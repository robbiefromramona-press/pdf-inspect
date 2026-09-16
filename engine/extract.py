"""
PDF-Inspect extraction engine.

This one file runs unchanged in two places:
  * in the browser, inside Pyodide (Python compiled to WebAssembly) - see
    js/engine-worker.js. The PDF never leaves the user's computer.
  * in regular desktop Python, for the test suite - see tests/.

Entry point: inspect_pdf(data, progress=None) -> dict
             run_json(data, progress=None)    -> JSON string (what the browser calls)

Design rules:
  * Every report field is extracted independently. A malformed dictionary in
    one part of the file marks that one field "error" instead of blanking the
    whole report.
  * Only the PDF's own structure is read. Nothing is guessed from page text.
  * The only dependency is pypdf, which is pure Python (BSD license), so it
    runs in the browser without any compiled extensions.

Every field result has the same shape:
  status   "found" | "absent" | "error" | "locked"
  summary  one-line value for the report table (None when absent)
  detail   list of lines with the full itemized value
  notes    list of short explanatory lines
  flags    list of warnings worth highlighting (e.g. non-embedded fonts)
  data     structured version of the value, for the JSON export
"""

import io
import json
import re
from collections import Counter, OrderedDict

from pypdf import PasswordType, PdfReader
from pypdf import __version__ as PYPDF_VERSION
from pypdf.errors import DependencyError
from pypdf.generic import (
    ArrayObject,
    BooleanObject,
    ByteStringObject,
    DictionaryObject,
    FloatObject,
    IndirectObject,
    NameObject,
    NullObject,
)

# The report rows, in order. Must match engine/fields.json (the test suite checks).
FIELD_KEYS = [
    "title", "author", "subject", "keywords", "creator", "producer",
    "created", "modified", "page_count", "page_sizes", "page_units",
    "layers", "layer_flags", "tagged_objects",
    "bb_custom_properties", "bb_custom_columns", "bb_measurements",
    "embedded_scale", "geopdf",
    "bookmarks", "hyperlinks", "form_fields", "annotations",
    "attachments", "fonts", "images",
    "permissions", "encryption",
]

# Keep the report (and the browser tab's memory) bounded on enormous drawing sets.
MAX_DETAIL_LINES = 2000
MAX_DATA_ITEMS = 20000
MAX_STRUCT_ELEMENTS = 400000


# ---------------------------------------------------------------------------
# Small helpers for reading raw PDF objects
# ---------------------------------------------------------------------------

def R(obj):
    """Follow an indirect reference ("12 0 R") to the object it points at."""
    hops = 0
    while isinstance(obj, IndirectObject) and hops < 32:
        obj = obj.get_object()
        hops += 1
    return obj


def dget(d, key, default=None):
    """Read key from a PDF dictionary, resolving references. Safe on non-dicts."""
    d = R(d)
    if not isinstance(d, dict):
        return default
    value = dict.get(d, key)
    if value is None:
        return default
    value = R(value)
    return default if value is None or isinstance(value, NullObject) else value


def oid(obj):
    """A stable identity for a PDF object, used to avoid counting it twice."""
    if isinstance(obj, IndirectObject):
        return ("ref", obj.idnum, obj.generation)
    ref = getattr(obj, "indirect_reference", None)
    if ref is not None:
        return ("ref", ref.idnum, ref.generation)
    return ("direct", id(obj))


def as_list(obj):
    obj = R(obj)
    if isinstance(obj, list):
        return obj
    return [] if obj is None else [obj]


def num(obj, default=None):
    obj = R(obj)
    try:
        return float(obj)
    except (TypeError, ValueError):
        return default


def fmt_num(x, digits=6):
    if x is None:
        return ""
    return f"{x:.{digits}g}"


def txt(obj, depth=0):
    """Turn any PDF object into readable text."""
    obj = R(obj)
    if obj is None or isinstance(obj, NullObject):
        return ""
    if isinstance(obj, BooleanObject):
        return "true" if obj.value else "false"
    if isinstance(obj, NameObject):
        return str(obj)[1:] if str(obj).startswith("/") else str(obj)
    if isinstance(obj, (ByteStringObject, bytes, bytearray)):
        raw = bytes(obj)
        try:
            s = raw.decode("utf-8")
        except UnicodeDecodeError:
            s = raw.decode("latin-1")
        return s.replace("\x00", "").strip()
    if isinstance(obj, (float, FloatObject)):
        return fmt_num(float(obj))
    if isinstance(obj, list):
        if depth > 2:
            return "[…]"
        items = [txt(x, depth + 1) for x in obj[:20]]
        return "[" + ", ".join(items) + (", …" if len(obj) > 20 else "") + "]"
    if isinstance(obj, dict):
        if depth > 2:
            return "{…}"
        items = [f"{txt(NameObject(k))}: {txt(v, depth + 1)}" for k, v in list(obj.items())[:12]]
        return "{" + ", ".join(items) + (", …" if len(obj) > 12 else "") + "}"
    return str(obj).replace("\x00", "").strip()


def one_line(s, limit=200):
    s = re.sub(r"\s+", " ", s or "").strip()
    return s if len(s) <= limit else s[: limit - 1] + "…"


_PDF_DATE = re.compile(
    r"^(?:D:)?(\d{4})(\d{2})?(\d{2})?(\d{2})?(\d{2})?(\d{2})?\s*([Zz+\-])?\s*(\d{2})?'?(\d{2})?'?"
)


def pdf_date(value):
    """Format a PDF date string (D:20240305142210-08'00') as 2024-03-05 14:22:10 -08:00."""
    s = txt(value).strip()
    if not s:
        return None
    m = _PDF_DATE.match(s)
    if not m:
        return s
    y, mo, d, h, mi, se = (m.group(i) for i in range(1, 7))
    out = f"{y}-{mo or '01'}-{d or '01'} {h or '00'}:{mi or '00'}:{se or '00'}"
    tz = m.group(7)
    if tz in ("Z", "z"):
        out += " UTC"
    elif tz in ("+", "-"):
        out += f" {tz}{m.group(8) or '00'}:{m.group(9) or '00'}"
    return out


def xmp_date(value):
    if value is None:
        return None
    try:
        s = value.isoformat(sep=" ")
    except (AttributeError, TypeError):
        return txt(value) or None
    return s.replace("+00:00", " UTC")


def plural(n, word, plural_word=None):
    return f"{n:,} {word if n == 1 else (plural_word or word + 's')}"


def page_ranges(pages):
    """[1,2,3,5,7,8] -> '1–3, 5, 7–8'"""
    pages = sorted(set(pages))
    out, start, prev = [], None, None
    for p in pages:
        if start is None:
            start = prev = p
        elif p == prev + 1:
            prev = p
        else:
            out.append(f"{start}" if start == prev else f"{start}–{prev}")
            start = prev = p
    if start is not None:
        out.append(f"{start}" if start == prev else f"{start}–{prev}")
    return ", ".join(out)


def human_bytes(n):
    if n is None:
        return "unknown size"
    for unit in ("bytes", "KB", "MB", "GB"):
        if n < 1024 or unit == "GB":
            return f"{n:,} bytes" if unit == "bytes" else f"{n:.1f} {unit}"
        n /= 1024.0


def stream_bytes(stream):
    """Stored (compressed) size of a stream. pypdf drops /Length once a stream
    is read, so fall back to the length of the raw bytes it kept."""
    stream = R(stream)
    raw = getattr(stream, "_data", None)
    if raw is not None:
        return len(raw)
    return int(num(dget(stream, "/Length"), 0)) or None


def cap(lines):
    if len(lines) <= MAX_DETAIL_LINES:
        return lines
    return lines[:MAX_DETAIL_LINES] + [f"… and {len(lines) - MAX_DETAIL_LINES:,} more (see JSON export for the full list)"]


def result(status="found", summary=None, detail=None, notes=None, flags=None, data=None):
    return {
        "status": status,
        "summary": summary,
        "detail": cap(detail or []),
        "notes": notes or [],
        "flags": flags or [],
        "data": data,
    }


def absent(*notes, data=None):
    return result("absent", notes=[n for n in notes if n], data=data)


# ---------------------------------------------------------------------------
# Reference tables
# ---------------------------------------------------------------------------

# Named sheet sizes in inches (short side, long side).
SHEET_SIZES = [
    ("Letter", 8.5, 11), ("Legal", 8.5, 14), ("Tabloid / ANSI B", 11, 17),
    ("ANSI C", 17, 22), ("ANSI D", 22, 34), ("ANSI E", 34, 44),
    ("ARCH A", 9, 12), ("ARCH B", 12, 18), ("ARCH C", 18, 24),
    ("ARCH D", 24, 36), ("ARCH E1", 30, 42), ("ARCH E", 36, 48),
    ("ISO A4", 8.27, 11.69), ("ISO A3", 11.69, 16.54), ("ISO A2", 16.54, 23.39),
    ("ISO A1", 23.39, 33.11), ("ISO A0", 33.11, 46.81),
]

STANDARD_14_FONTS = {
    "Courier", "Courier-Bold", "Courier-Oblique", "Courier-BoldOblique",
    "Helvetica", "Helvetica-Bold", "Helvetica-Oblique", "Helvetica-BoldOblique",
    "Times-Roman", "Times-Bold", "Times-Italic", "Times-BoldItalic",
    "Symbol", "ZapfDingbats",
}

STANDARD_INFO_KEYS = {
    "/Title", "/Author", "/Subject", "/Keywords", "/Creator", "/Producer",
    "/CreationDate", "/ModDate", "/Trapped",
}

IMAGE_FILTERS = {
    "DCTDecode": "JPEG", "JPXDecode": "JPEG 2000", "FlateDecode": "Flate (lossless)",
    "CCITTFaxDecode": "CCITT fax", "JBIG2Decode": "JBIG2", "RunLengthDecode": "Run-length",
    "LZWDecode": "LZW",
}

MEASUREMENT_INTENTS = {"/LineDimension", "/PolyLineDimension", "/PolygonDimension"}
MEASUREMENT_SUBJECT = re.compile(
    r"\b(measurements?|length|area|perimeter|count|volume|diameter|radius|angle|spaces?|dimension)\b",
    re.IGNORECASE,
)
NON_MARKUP_ANNOTS = {"/Link", "/Widget", "/Popup"}

# Object-data field names shown first, in this order, when present.
PREFERRED_PROPERTY_ORDER = ["Category", "Family", "Type", "Family and Type", "Level", "Mark", "Comments"]


# ---------------------------------------------------------------------------
# The inspector
# ---------------------------------------------------------------------------

class Inspector:
    def __init__(self, data, progress=None):
        self.data = bytes(data)
        self._progress = progress
        self.reader = PdfReader(io.BytesIO(self.data), strict=False)
        self.locked = False

        # Filled by the single page pass (_scan_pages).
        self.page_info = []          # one dict per page: size, units, rotation
        self.annots = []             # (page_no, annotation dict)
        self.viewports = []          # (page_no, viewport dict)
        self.lgi_dicts = []          # (page_no, LGIDict) - older GeoPDF encoding
        self.fonts = OrderedDict()   # oid -> font record
        self.images = OrderedDict()  # oid -> image record
        self.soft_masks = set()      # image oids that are only transparency masks
        self.mc_properties = OrderedDict()  # oid -> marked-content property list
        self._resource_memo = {}

    # -- progress ---------------------------------------------------------
    def progress(self, label, fraction):
        if self._progress is None:
            return
        try:
            self._progress(label, float(fraction))
        except Exception:  # a broken callback must never break extraction
            pass

    # -- run ----------------------------------------------------------------
    def run(self):
        reader = self.reader
        fields = {}

        # Security first: it decides whether anything else can be read.
        if reader.is_encrypted:
            # An empty password opens files that only have an owner password.
            # Raises DependencyError if AES support isn't loaded (handled by run_json).
            self.locked = reader.decrypt("") == PasswordType.NOT_DECRYPTED

        fields["encryption"] = self._safe(self.f_encryption)
        fields["permissions"] = self._safe(self.f_permissions)

        if self.locked:
            note = "The file requires a password to open, so its contents can't be read."
            for key in FIELD_KEYS:
                fields.setdefault(key, result("locked", notes=[note]))
            return self._package(fields, calibration=None)

        self.progress("Reading document properties", 0.02)
        self.root = R(reader.trailer.get("/Root"))
        self.info = R(reader.trailer.get("/Info"))
        try:
            self.xmp = reader.xmp_metadata
        except Exception:
            self.xmp = None

        self._safe(self._scan_pages, fallback=None)

        simple = [
            ("title", self.f_title), ("author", self.f_author), ("subject", self.f_subject),
            ("keywords", self.f_keywords), ("creator", self.f_creator), ("producer", self.f_producer),
            ("created", self.f_created), ("modified", self.f_modified),
            ("page_count", self.f_page_count), ("page_sizes", self.f_page_sizes),
            ("page_units", self.f_page_units),
            ("layers", self.f_layers), ("layer_flags", self.f_layer_flags),
        ]
        for key, fn in simple:
            fields[key] = self._safe(fn)

        self.progress("Reading tagged object data", 0.85)
        fields["tagged_objects"] = self._safe(self.f_tagged_objects)

        self.progress("Reading markups, links and forms", 0.92)
        rest = [
            ("bb_custom_properties", self.f_custom_properties),
            ("bb_custom_columns", self.f_custom_columns),
            ("bb_measurements", self.f_measurements),
            ("embedded_scale", self.f_embedded_scale),
            ("geopdf", self.f_geopdf),
            ("bookmarks", self.f_bookmarks),
            ("hyperlinks", self.f_hyperlinks),
            ("form_fields", self.f_form_fields),
            ("annotations", self.f_annotations),
            ("attachments", self.f_attachments),
            ("fonts", self.f_fonts),
            ("images", self.f_images),
        ]
        for key, fn in rest:
            fields[key] = self._safe(fn)

        self.progress("Building report", 0.98)
        return self._package(fields, calibration=self._calibration(fields))

    def _safe(self, fn, fallback="error"):
        try:
            return fn()
        except DependencyError:
            raise
        except Exception as exc:  # one bad field must not sink the report
            if fallback is None:
                return None
            return result("error", notes=[f"This part of the file could not be read: {one_line(str(exc), 160)}"])

    def _package(self, fields, calibration):
        return {
            "engine": {"library": "pypdf", "version": PYPDF_VERSION},
            "pageCount": len(self.page_info) if self.page_info else None,
            "encrypted": bool(self.reader.is_encrypted),
            "locked": self.locked,
            "fields": {k: fields[k] for k in FIELD_KEYS},
            "calibration": calibration,
        }

    # -----------------------------------------------------------------------
    # One pass over every page, collecting everything page-level at once so
    # large drawing sets are only walked a single time.
    # -----------------------------------------------------------------------
    def _scan_pages(self):
        pages = self.reader.pages
        total = len(pages)
        self.page_refs = {}
        for i, page in enumerate(pages):
            page_no = i + 1
            if page.indirect_reference is not None:
                self.page_refs[oid(page.indirect_reference)] = page_no

            box = page.cropbox
            w, h = float(box.width), float(box.height)
            rotation = (page.rotation or 0) % 360
            if rotation in (90, 270):
                w, h = h, w
            user_unit = float(page.user_unit or 1)
            self.page_info.append({
                "page": page_no, "widthPt": w, "heightPt": h, "userUnit": user_unit,
                "widthIn": w * user_unit / 72.0, "heightIn": h * user_unit / 72.0,
                "rotation": rotation,
            })

            for annot in as_list(dget(page, "/Annots")):
                annot = R(annot)
                if isinstance(annot, dict):
                    self.annots.append((page_no, annot))
                    ap_normal = dget(dget(annot, "/AP"), "/N")
                    if isinstance(ap_normal, dict) and dget(ap_normal, "/Subtype") == "/Form":
                        self._scan_resources(dget(ap_normal, "/Resources"), page_no)

            for vp in as_list(dget(page, "/VP")):
                vp = R(vp)
                if isinstance(vp, dict):
                    self.viewports.append((page_no, vp))

            for lgi in as_list(dget(page, "/LGIDict")):
                lgi = R(lgi)
                if isinstance(lgi, dict):
                    self.lgi_dicts.append((page_no, lgi))

            self._scan_resources(dget(page, "/Resources"), page_no)

            if total and (page_no == total or page_no % 5 == 0):
                self.progress(f"Reading page {page_no:,} of {total:,}", 0.05 + 0.78 * page_no / total)

        acroform = dget(self.root, "/AcroForm")
        self._scan_resources(dget(acroform, "/DR"), None)

    def _scan_resources(self, resources, page_no, depth=0):
        """Record fonts, images and marked-content properties under a /Resources
        dictionary, following nested form XObjects and tiling patterns.
        Returns the image ids found, so shared resources can still be credited
        to every page that uses them."""
        resources = R(resources)
        if not isinstance(resources, dict) or depth > 16:
            return frozenset()
        key = oid(resources)
        if key in self._resource_memo:
            found = self._resource_memo[key]
            self._credit_pages(found, page_no)
            return found
        self._resource_memo[key] = frozenset()  # guards against reference loops

        images = set()
        for _, font in (dget(resources, "/Font") or {}).items():
            self._record_font(font)

        for _, xobj in (dget(resources, "/XObject") or {}).items():
            x = R(xobj)
            if not isinstance(x, dict):
                continue
            subtype = dget(x, "/Subtype")
            if subtype == "/Image":
                images.add(self._record_image(x))
            elif subtype == "/Form":
                images |= self._scan_resources(dget(x, "/Resources"), page_no, depth + 1)

        for _, pattern in (dget(resources, "/Pattern") or {}).items():
            p = R(pattern)
            if isinstance(p, dict) and num(dget(p, "/PatternType")) == 1:
                images |= self._scan_resources(dget(p, "/Resources"), page_no, depth + 1)

        for name, prop in (dget(resources, "/Properties") or {}).items():
            p = R(prop)
            if not isinstance(p, dict) or dget(p, "/Type") in ("/OCG", "/OCMD"):
                continue
            keys = [k for k in p.keys() if k not in ("/Type", "/MCID", "/Metadata")]
            if keys and oid(p) not in self.mc_properties:
                self.mc_properties[oid(p)] = {
                    "resourceName": txt(NameObject(name)),
                    "properties": {txt(NameObject(k)): one_line(txt(p.get(k)), 300) for k in keys},
                }

        found = frozenset(images)
        self._resource_memo[key] = found
        self._credit_pages(found, page_no)
        return found

    def _credit_pages(self, image_ids, page_no):
        if page_no is None:
            return
        for image_id in image_ids:
            self.images[image_id]["pages"].add(page_no)

    def _record_font(self, font_ref):
        font = R(font_ref)
        if not isinstance(font, dict):
            return
        key = oid(font)
        if key in self.fonts:
            return
        subtype = txt(dget(font, "/Subtype"))
        base = txt(dget(font, "/BaseFont")) or txt(dget(font, "/Name"))
        descriptor = dget(font, "/FontDescriptor")
        type_label = subtype or "unknown type"
        if subtype == "Type0":
            descendant = R((dget(font, "/DescendantFonts") or [None])[0])
            descriptor = dget(descendant, "/FontDescriptor")
            if isinstance(descendant, dict):
                type_label = f"Type0 / {txt(dget(descendant, '/Subtype'))}"
        if subtype == "Type3":
            embedded = True  # Type 3 glyphs are always drawn from inside the file
        else:
            embedded = isinstance(descriptor, dict) and any(
                k in descriptor for k in ("/FontFile", "/FontFile2", "/FontFile3"))
        subset = bool(re.match(r"^[A-Z]{6}\+", base))
        name = base[7:] if subset else base
        self.fonts[key] = {
            "name": name or "(unnamed font)",
            "type": type_label,
            "embedded": embedded,
            "subset": subset,
            "standard14": (not embedded) and name in STANDARD_14_FONTS,
        }

    def _record_image(self, image):
        key = oid(image)
        if key not in self.images:
            smask = dict.get(image, "/SMask")
            if smask is not None:
                self.soft_masks.add(oid(smask))
            filters = [txt(f) for f in as_list(dget(image, "/Filter"))]
            colorspace = dget(image, "/ColorSpace")
            if isinstance(colorspace, list) and colorspace:
                cs_name = txt(colorspace[0])
                if cs_name == "ICCBased":
                    comps = num(dget(R(colorspace[1]), "/N")) if len(colorspace) > 1 else None
                    cs_name = f"ICC ({int(comps)} channels)" if comps else "ICC"
            else:
                cs_name = txt(colorspace)
            if dget(image, "/ImageMask"):
                cs_name = "1-bit mask"
            self.images[key] = {
                "width": int(num(dget(image, "/Width"), 0)),
                "height": int(num(dget(image, "/Height"), 0)),
                "bitsPerComponent": int(num(dget(image, "/BitsPerComponent"), 0)) or None,
                "colorSpace": cs_name or "unspecified",
                "encoding": ", ".join(IMAGE_FILTERS.get(f, f) for f in filters) or "uncompressed",
                "bytes": stream_bytes(image),
                "pages": set(),
            }
        return key

    # -----------------------------------------------------------------------
    # Document metadata
    # -----------------------------------------------------------------------
    def _info_or_xmp(self, info_key, xmp_value, xmp_label="XMP metadata"):
        info_value = one_line(txt(dget(self.info, info_key)), 1000)
        xmp_value = one_line(xmp_value or "", 1000)
        if info_value:
            notes = []
            if xmp_value and xmp_value != info_value:
                notes.append(f"XMP metadata has a different value: {xmp_value}")
            return result(summary=info_value, notes=notes, data={"info": info_value, "xmp": xmp_value or None})
        if xmp_value:
            return result(summary=xmp_value, notes=[f"Source: {xmp_label}."], data={"info": None, "xmp": xmp_value})
        return absent()

    def _xmp(self, attr):
        if self.xmp is None:
            return None
        try:
            value = getattr(self.xmp, attr)
        except Exception:
            return None
        if isinstance(value, dict):
            return next((v for v in value.values() if v), None)
        if isinstance(value, list):
            return "; ".join(str(v) for v in value if v) or None
        return value

    def f_title(self):
        return self._info_or_xmp("/Title", self._xmp("dc_title"))

    def f_author(self):
        return self._info_or_xmp("/Author", self._xmp("dc_creator"))

    def f_subject(self):
        return self._info_or_xmp("/Subject", self._xmp("dc_description"))

    def f_keywords(self):
        return self._info_or_xmp("/Keywords", self._xmp("pdf_keywords") or self._xmp("dc_subject"))

    def f_creator(self):
        return self._info_or_xmp("/Creator", self._xmp("xmp_creator_tool"))

    def f_producer(self):
        return self._info_or_xmp("/Producer", self._xmp("pdf_producer"))

    def _date_field(self, info_key, xmp_attr):
        raw = txt(dget(self.info, info_key))
        info_value = pdf_date(raw)
        xmp_value = xmp_date(self._xmp(xmp_attr))
        if info_value:
            return result(summary=info_value,
                          data={"formatted": info_value, "raw": raw, "xmp": xmp_value})
        if xmp_value:
            return result(summary=xmp_value, notes=["Source: XMP metadata."],
                          data={"formatted": xmp_value, "raw": None, "xmp": xmp_value})
        return absent()

    def f_created(self):
        return self._date_field("/CreationDate", "xmp_create_date")

    def f_modified(self):
        return self._date_field("/ModDate", "xmp_modify_date")

    def f_page_count(self):
        n = len(self.reader.pages)
        return result(summary=plural(n, "page"), data={"count": n})

    @staticmethod
    def _sheet_name(w_in, h_in):
        short, long_ = sorted((w_in, h_in))
        for name, a, b in SHEET_SIZES:
            if abs(short - a) <= 0.08 and abs(long_ - b) <= 0.08:
                return name
        return None

    def f_page_sizes(self):
        if not self.page_info:
            return absent("No pages could be read.")
        groups = OrderedDict()
        for p in self.page_info:
            w, h = round(p["widthIn"], 2), round(p["heightIn"], 2)
            orientation = "landscape" if w > h else "portrait" if h > w else "square"
            groups.setdefault((w, h, orientation), []).append(p["page"])

        # "Mixed" means different sheet sizes; the same sheet turned sideways isn't mixed.
        distinct_sheets = {tuple(sorted((w, h))) for (w, h, _) in groups}
        lines, data = [], []
        for (w, h, orientation), pages in groups.items():
            name = self._sheet_name(w, h)
            label = f"{fmt_num(w, 4)} × {fmt_num(h, 4)} in" + (f" ({name})" if name else "") + f", {orientation}"
            lines.append(f"{label} — {'page' if len(pages) == 1 else 'pages'} {page_ranges(pages)}")
            data.append({"widthIn": w, "heightIn": h, "sheet": name, "orientation": orientation, "pages": pages})

        flags = []
        if len(distinct_sheets) > 1:
            flags.append(f"Mixed sheet sizes: this file uses {len(distinct_sheets)} different sheet sizes.")
            summary = f"Mixed — {len(distinct_sheets)} sheet sizes"
        else:
            summary = lines[0].split(" — ")[0]
        return result(summary=summary, detail=lines, flags=flags, data=data)

    def f_page_units(self):
        if not self.page_info:
            return absent()
        units = Counter(p["userUnit"] for p in self.page_info)
        lines, data = [], []
        for unit, count in units.items():
            if unit == 1:
                label = "1 unit = 1/72 inch (PDF points, the default)"
            else:
                label = f"1 unit = {fmt_num(unit)}/72 inch ({fmt_num(unit / 72.0)} in) — custom UserUnit"
            pages = [p["page"] for p in self.page_info if p["userUnit"] == unit]
            lines.append(f"{label} — {'page' if len(pages) == 1 else 'pages'} {page_ranges(pages)}")
            data.append({"userUnit": unit, "inchesPerUnit": unit / 72.0, "pages": pages})
        summary = lines[0].split(" — ")[0] if len(units) == 1 else f"Mixed — {len(units)} different page units"
        return result(summary=summary, detail=lines if len(units) > 1 else [], data=data)

    # -----------------------------------------------------------------------
    # Layers (optional content groups)
    # -----------------------------------------------------------------------
    def _layer_records(self):
        if hasattr(self, "_layers_cache"):
            return self._layers_cache
        ocprops = dget(self.root, "/OCProperties")
        ocgs = [R(o) for o in as_list(dget(ocprops, "/OCGs"))]
        ocgs = [o for o in ocgs if isinstance(o, dict)]
        if not ocgs:
            self._layers_cache = None
            return None

        config = dget(ocprops, "/D")
        base_state = txt(dget(config, "/BaseState")) or "ON"
        on_ids = {oid(x) for x in as_list(dget(config, "/ON"))}
        off_ids = {oid(x) for x in as_list(dget(config, "/OFF"))}
        locked_ids = {oid(x) for x in as_list(dget(config, "/Locked"))}

        # /Order describes the tree shown in a viewer's layer panel. AutoCAD and
        # Revit usually nest the CAD layers under a parent named after the sheet.
        paths = {}

        def walk(order, path, depth=0):
            if depth > 32:
                return
            items = as_list(order)
            last_layer_name = None
            start = 0
            if items and isinstance(R(items[0]), str) and not isinstance(R(items[0]), NameObject):
                path = path + [txt(items[0])]  # a text label heading a group
                start = 1
            for item in items[start:]:
                obj = R(item)
                if isinstance(obj, dict):
                    paths.setdefault(oid(obj), path)
                    last_layer_name = txt(dget(obj, "/Name"))
                elif isinstance(obj, list):
                    walk(obj, path + ([last_layer_name] if last_layer_name else []), depth + 1)

        walk(dget(config, "/Order"), [])

        records = []
        for ocg in ocgs:
            key = oid(ocg)
            if base_state == "OFF":
                visible = key in on_ids
            else:
                visible = key not in off_ids
            print_state = txt(dget(dget(dget(ocg, "/Usage"), "/Print"), "/PrintState")).lower() or None
            records.append({
                "name": txt(dget(ocg, "/Name")) or "(unnamed layer)",
                "group": paths.get(key, []),
                "visible": visible,
                "print": print_state,  # "on", "off", or None = prints whatever is visible
                "locked": key in locked_ids,
                "inOrder": key in paths,
            })
        self._layers_cache = records
        return records

    def f_layers(self):
        records = self._layer_records()
        if not records:
            return absent("The file has no layers (optional content groups).")
        lines = [" › ".join(r["group"] + [r["name"]]) for r in records]
        groups = {r["group"][0] for r in records if r["group"]}
        summary = plural(len(records), "layer")
        if groups:
            summary += f" in {plural(len(groups), 'group')}"
        return result(summary=summary, detail=lines,
                      data=[{"name": r["name"], "group": r["group"]} for r in records])

    def f_layer_flags(self):
        records = self._layer_records()
        if not records:
            return absent("The file has no layers.")
        lines = []
        for r in records:
            parts = ["shown" if r["visible"] else "HIDDEN",
                     {"on": "prints", "off": "DOES NOT PRINT", None: "prints if shown"}[r["print"] if r["print"] in ("on", "off") else None]]
            if r["locked"]:
                parts.append("locked")
            lines.append(f"{r['name']} — " + " · ".join(parts))
        hidden = sum(1 for r in records if not r["visible"])
        no_print = sum(1 for r in records if r["print"] == "off")
        locked = sum(1 for r in records if r["locked"])
        bits = [f"{len(records) - hidden:,} shown", f"{hidden:,} hidden"]
        if no_print:
            bits.append(f"{no_print:,} set not to print")
        if locked:
            bits.append(f"{locked:,} locked")
        flags = []
        if hidden:
            flags.append(f"{plural(hidden, 'layer')} hidden by default — content on {'it' if hidden == 1 else 'them'} won't show until turned on.")
        if no_print:
            flags.append(f"{plural(no_print, 'layer')} set not to print.")
        return result(summary=", ".join(bits), detail=lines, flags=flags, data=records)

    # -----------------------------------------------------------------------
    # Tagged object properties
    # -----------------------------------------------------------------------
    def _user_properties(self, element, class_map):
        attributes = []
        for a in as_list(dget(element, "/A")):
            a = R(a)
            if isinstance(a, dict):
                attributes.append(a)
        classes = dget(element, "/C")
        class_names = [classes] if isinstance(classes, str) else as_list(classes)
        for name in class_names:
            for a in as_list(dget(class_map, R(name))):
                a = R(a)
                if isinstance(a, dict):
                    attributes.append(a)

        props = OrderedDict()
        for a in attributes:
            if dget(a, "/O") != "/UserProperties":
                continue
            for p in as_list(dget(a, "/P")):
                p = R(p)
                if not isinstance(p, dict):
                    continue
                name = txt(dget(p, "/N"))
                if not name:
                    continue
                formatted = dget(p, "/F")
                props[name] = one_line(txt(formatted if formatted is not None else dget(p, "/V")), 300)
        return props

    def f_tagged_objects(self):
        struct_root = dget(self.root, "/StructTreeRoot")
        objects = []
        truncated = False
        if isinstance(struct_root, dict):
            class_map = dget(struct_root, "/ClassMap")
            stack = [dget(struct_root, "/K")]
            visited = set()
            elements = 0
            while stack:
                node = R(stack.pop())
                if isinstance(node, list):
                    stack.extend(reversed(node))
                    continue
                if not isinstance(node, dict):
                    continue  # marked-content ids (plain integers)
                key = oid(node)
                if key in visited or dget(node, "/Type") in ("/MCR", "/OBJR"):
                    continue
                visited.add(key)
                elements += 1
                if elements > MAX_STRUCT_ELEMENTS:
                    truncated = True
                    break
                props = self._user_properties(node, class_map)
                if props:
                    objects.append({"tag": txt(dget(node, "/S")), "title": txt(dget(node, "/T")), "properties": props})
                kids = dget(node, "/K")
                if kids is not None:
                    stack.append(kids)

        mc = list(self.mc_properties.values())
        if not objects and not mc:
            if isinstance(struct_root, dict):
                return absent("The file is tagged (it has a structure tree), but no object properties are attached to its elements.")
            return absent("The file has no tagged object data.")

        # Order property names: well-known BIM fields first, then by how often they appear.
        frequency = Counter(name for o in objects for name in o["properties"])
        ordered_names = [n for n in PREFERRED_PROPERTY_ORDER if n in frequency]
        ordered_names += [n for n, _ in frequency.most_common() if n not in ordered_names]

        lines = []
        for o in objects:
            parts = [f"{n}: {o['properties'][n]}" for n in ordered_names if n in o["properties"]]
            lines.append(" | ".join(parts))
        for m in mc:
            parts = [f"{k}: {v}" for k, v in m["properties"].items()]
            lines.append(f"[marked content {m['resourceName']}] " + " | ".join(parts))

        summary_bits = []
        if objects:
            summary_bits.append(f"{plural(len(objects), 'tagged object')} with properties")
            category_key = next((n for n in ordered_names if n.lower() == "category"), None)
            if category_key:
                counts = Counter(o["properties"].get(category_key) for o in objects if o["properties"].get(category_key))
                top = ", ".join(f"{c} ({n:,})" for c, n in counts.most_common(6))
                if len(counts) > 6:
                    top += f", +{len(counts) - 6} more"
                summary_bits.append(top)
            else:
                summary_bits.append("fields: " + ", ".join(ordered_names[:8]))
        if mc:
            summary_bits.append(f"{plural(len(mc), 'marked-content property list')}")

        notes = ["Source: structure element attributes (UserProperties) and marked-content property lists."]
        if objects:
            notes.append("Fields found: " + ", ".join(ordered_names))
        if truncated:
            notes.append(f"Stopped after {MAX_STRUCT_ELEMENTS:,} structure elements; the list may be incomplete.")
        return result(summary=" — ".join(summary_bits), detail=lines, notes=notes,
                      data={"objects": objects[:MAX_DATA_ITEMS], "objectCount": len(objects),
                            "markedContent": mc[:MAX_DATA_ITEMS], "fieldNames": ordered_names})

    # -----------------------------------------------------------------------
    # Bluebeam
    # -----------------------------------------------------------------------
    def f_custom_properties(self):
        if not isinstance(self.info, dict):
            return absent("The file has no document information dictionary.")
        custom = OrderedDict()
        for key in self.info.keys():
            if key not in STANDARD_INFO_KEYS:
                custom[txt(NameObject(key))] = one_line(txt(self.info.get(key)), 500)
        if not custom:
            return absent("No custom document properties are set.")
        lines = [f"{k}: {v}" for k, v in custom.items()]
        return result(summary=plural(len(custom), "custom property", "custom properties"), detail=lines,
                      notes=["Source: custom entries in the document information dictionary."],
                      data=custom)

    def _bluebeam_catalog_keys(self):
        return [k for k in (self.root or {}).keys() if str(k).startswith("/BSI")]

    def f_custom_columns(self):
        catalog_keys = self._bluebeam_catalog_keys()

        # Column definitions: a catalog entry holding a list of dictionaries that
        # each carry a name. Used only to label the values stored on markups.
        column_names = None
        for k in catalog_keys:
            value = dget(self.root, k)
            if isinstance(value, list) and value and all(isinstance(R(v), dict) for v in value):
                names = []
                for v in value:
                    v = R(v)
                    label = next((txt(dget(v, n)) for n in ("/Name", "/DisplayName", "/Title", "/Label") if dget(v, n) is not None), "")
                    names.append(label)
                if any(names):
                    column_names = names
                    break

        rows = []
        for page_no, annot in self.annots:
            values = dget(annot, "/BSIColumnData")
            if values is None:
                continue
            values = [one_line(txt(v), 200) for v in as_list(values)]
            kind = txt(dget(annot, "/Subj")) or txt(dget(annot, "/Subtype"))
            if column_names and len(column_names) == len(values):
                pairs = [f"{n}: {v}" for n, v in zip(column_names, values) if v]
            else:
                pairs = [v for v in values if v]
            rows.append({"page": page_no, "markup": kind, "values": values,
                         "line": f"p.{page_no} · {kind} · " + (", ".join(pairs) if pairs else "(all columns empty)")})

        if not rows and not column_names:
            if catalog_keys:
                return absent("Bluebeam data is present (" + ", ".join(txt(NameObject(k)) for k in catalog_keys) +
                              "), but no custom column values were found.")
            return absent("No Bluebeam custom column data was found.")

        summary = []
        if column_names:
            summary.append(plural(len(column_names), "column") + ": " + ", ".join(n for n in column_names if n))
        summary.append(f"{plural(len(rows), 'markup')} with column values")
        return result(summary=" — ".join(summary), detail=[r.pop("line") for r in rows],
                      notes=["Read from Bluebeam's private data keys; layout can vary between Revu versions."],
                      data={"columns": column_names, "markups": rows[:MAX_DATA_ITEMS]})

    def f_measurements(self):
        rows = []
        for page_no, annot in self.annots:
            subtype = dget(annot, "/Subtype")
            if subtype in NON_MARKUP_ANNOTS:
                continue
            intent = dget(annot, "/IT")
            subject = txt(dget(annot, "/Subj"))
            measure = dget(annot, "/Measure")
            if not (intent in MEASUREMENT_INTENTS or isinstance(measure, dict)
                    or (subject and MEASUREMENT_SUBJECT.search(subject))):
                continue
            kind = subject or txt(intent) or txt(subtype)
            value = one_line(txt(dget(annot, "/Contents")), 200)
            scale = txt(dget(measure, "/R")) if isinstance(measure, dict) else ""
            label = one_line(txt(dget(annot, "/T")), 80)
            rows.append({"page": page_no, "type": kind, "value": value or None, "scale": scale or None,
                         "author": label or None, "annotationType": txt(subtype)})

        if not rows:
            return absent("No measurement or space markups were found.")
        counts = Counter(r["type"] for r in rows)
        lines = []
        for r in rows:
            parts = [f"p.{r['page']}", r["type"], r["value"] or "(no value stored)"]
            if r["scale"]:
                parts.append(f"scale {r['scale']}")
            lines.append(" · ".join(parts))
        summary = f"{plural(len(rows), 'measurement')}: " + ", ".join(f"{t} ({n:,})" for t, n in counts.most_common(6))
        return result(summary=summary, detail=lines, data=rows[:MAX_DATA_ITEMS])

    # -----------------------------------------------------------------------
    # Scale and georeferencing
    # -----------------------------------------------------------------------
    @staticmethod
    def _number_format_chain(formats):
        chain = []
        for f in as_list(formats):
            f = R(f)
            if isinstance(f, dict):
                chain.append({"unit": txt(dget(f, "/U")), "factor": num(dget(f, "/C"))})
        return chain

    def _scale_records(self):
        records = []
        sources = [(p, vp, dget(vp, "/Measure"), "viewport") for p, vp in self.viewports]
        for page_no, vp, measure, source in sources:
            if not isinstance(measure, dict) or dget(measure, "/Subtype", "/RL") != "/RL":
                continue
            x_chain = self._number_format_chain(dget(measure, "/X"))
            y_chain = self._number_format_chain(dget(measure, "/Y")) or x_chain
            bbox = [num(v) for v in as_list(dget(vp, "/BBox"))]
            user_unit = self.page_info[page_no - 1]["userUnit"] if page_no - 1 < len(self.page_info) else 1.0
            records.append({
                "page": page_no,
                "viewport": txt(dget(vp, "/Name")) or None,
                "bbox": bbox if len(bbox) == 4 else None,
                "ratio": txt(dget(measure, "/R")) or None,
                "unit": x_chain[0]["unit"] if x_chain else None,
                "unitsPerPoint": x_chain[0]["factor"] if x_chain else None,
                "unitsPerPointY": y_chain[0]["factor"] if y_chain else None,
                "userUnit": user_unit,
                "numberFormats": x_chain,
                "source": source,
            })
        return records

    def f_embedded_scale(self):
        records = self._scale_records()
        if not records:
            return absent("No calibrated scale (measure dictionary) is stored in the file.")
        lines = []
        for r in records:
            where = f"p.{r['page']}" + (f" viewport “{r['viewport']}”" if r["viewport"] else "")
            scale = r["ratio"] or "(no ratio label)"
            conversion = ""
            if r["unitsPerPoint"] is not None and r["unit"]:
                conversion = f" — 1 pt = {fmt_num(r['unitsPerPoint'])} {r['unit']}"
                if r["unitsPerPointY"] not in (None, r["unitsPerPoint"]):
                    conversion += f" (vertical: {fmt_num(r['unitsPerPointY'])} {r['unit']})"
            lines.append(f"{where}: {scale}{conversion}")
        ratios = Counter(r["ratio"] or "(unlabeled)" for r in records)
        pages = {r["page"] for r in records}
        if len(ratios) == 1:
            summary = f"{next(iter(ratios))} — {plural(len(records), 'calibrated area')} on {'page' if len(pages) == 1 else 'pages'} {page_ranges(pages)}"
        else:
            summary = f"{len(ratios)} different scales across {plural(len(records), 'calibrated area')}"
        return result(summary=summary, detail=lines, data=records)

    @staticmethod
    def _wkt_name(wkt):
        m = re.match(r'^\s*(?:PROJCS|GEOGCS|PROJCRS|GEOGCRS|GEODCRS|COMPD_CS)\s*\[\s*"([^"]+)"', wkt or "")
        return m.group(1) if m else None

    @staticmethod
    def _pairs(values):
        nums = [num(v) for v in as_list(values)]
        return [[nums[i], nums[i + 1]] for i in range(0, len(nums) - 1, 2)]

    def _geo_records(self):
        records = []
        for page_no, vp in self.viewports:
            measure = dget(vp, "/Measure")
            if not isinstance(measure, dict) or dget(measure, "/Subtype") != "/GEO":
                continue
            gcs = dget(measure, "/GCS")
            wkt = txt(dget(gcs, "/WKT")) or None
            epsg = dget(gcs, "/EPSG")
            records.append({
                "page": page_no,
                "source": "GEO measure dictionary",
                "viewport": txt(dget(vp, "/Name")) or None,
                "bbox": [num(v) for v in as_list(dget(vp, "/BBox"))] or None,
                "coordinateSystem": self._wkt_name(wkt) or txt(dget(gcs, "/Type")) or None,
                "epsg": int(epsg) if epsg is not None else None,
                "wkt": wkt,
                "geoPoints": self._pairs(dget(measure, "/GPTS")),   # latitude, longitude pairs
                "localPoints": self._pairs(dget(measure, "/LPTS")),  # positions within the viewport
                "bounds": self._pairs(dget(measure, "/Bounds")),
                "displayUnits": [txt(u) for u in as_list(dget(measure, "/PDU"))],
            })
        for page_no, lgi in self.lgi_dicts:
            projection = dget(lgi, "/Projection")
            datum = dget(projection, "/Datum")
            records.append({
                "page": page_no,
                "source": "LGIDict (older GeoPDF encoding)",
                "coordinateSystem": " ".join(filter(None, [
                    txt(dget(projection, "/ProjectionType")),
                    ("zone " + txt(dget(projection, "/Zone"))) if dget(projection, "/Zone") is not None else "",
                ])) or None,
                "datum": txt(datum) if datum is not None else None,
                "epsg": None,
                "wkt": None,
                "registration": [[num(v) for v in as_list(pair)] for pair in as_list(dget(lgi, "/Registration"))],
                "ctm": [num(v) for v in as_list(dget(lgi, "/CTM"))],
                "neatline": [num(v) for v in as_list(dget(lgi, "/Neatline"))],
                "description": txt(dget(lgi, "/Description")) or None,
            })
        return records

    def f_geopdf(self):
        records = self._geo_records()
        if not records:
            return absent("No geographic registration is stored in the file.")
        lines = []
        for r in records:
            name = r["coordinateSystem"] or "unnamed coordinate system"
            if r.get("epsg"):
                name += f" (EPSG:{r['epsg']})"
            points = len(r.get("geoPoints") or r.get("registration") or [])
            lines.append(f"p.{r['page']}: {name} — {plural(points, 'control point')} — {r['source']}")
        systems = {(r["coordinateSystem"], r.get("epsg")) for r in records}
        pages = {r["page"] for r in records}
        first = records[0]
        label = first["coordinateSystem"] or "Georeferenced"
        if first.get("epsg"):
            label += f" (EPSG:{first['epsg']})"
        summary = (label if len(systems) == 1 else f"{len(systems)} coordinate systems") + \
            f" — {'page' if len(pages) == 1 else 'pages'} {page_ranges(pages)}"
        return result(summary=summary, detail=lines, data=records)

    def _calibration(self, fields):
        """The subset CoordXY needs to skip manual calibration. None when the
        file carries no usable scale or georeference."""
        scale = [r for r in (fields["embedded_scale"].get("data") or []) if r.get("unitsPerPoint")]
        geo = fields["geopdf"].get("data") or []
        if not scale and not geo:
            return None
        return {
            "version": 1,
            "scale": [{k: r[k] for k in ("page", "viewport", "bbox", "ratio", "unit", "unitsPerPoint", "unitsPerPointY", "userUnit")}
                      for r in scale],
            "geo": geo,
            "pages": [{k: p[k] for k in ("page", "widthPt", "heightPt", "userUnit", "rotation")} for p in self.page_info],
        }

    # -----------------------------------------------------------------------
    # Navigation and interactive elements
    # -----------------------------------------------------------------------
    def f_bookmarks(self):
        try:
            outline = self.reader.outline
        except Exception as exc:
            return result("error", notes=[f"The bookmark tree is damaged: {one_line(str(exc), 160)}"])
        lines, data = [], []

        def walk(items, depth):
            for item in items:
                if len(lines) > MAX_DATA_ITEMS:
                    return
                if isinstance(item, list):
                    walk(item, depth + 1)
                    continue
                title = one_line(getattr(item, "title", None) or txt(item.get("/Title")), 200)
                try:
                    page = self.reader.get_destination_page_number(item) + 1
                except Exception:
                    page = None
                lines.append("    " * depth + (title or "(untitled)") + (f"  → p.{page}" if page else ""))
                data.append({"title": title, "page": page, "level": depth})

        walk(outline or [], 0)
        if not data:
            return absent("The file has no bookmarks.")
        top = sum(1 for d in data if d["level"] == 0)
        return result(summary=f"{plural(len(data), 'bookmark')} ({top:,} top-level)", detail=lines, data=data)

    def _dest_page(self, dest):
        dest = R(dest)
        if isinstance(dest, dict):
            dest = dget(dest, "/D")
        if isinstance(dest, (str, bytes)):
            if not hasattr(self, "_named"):
                try:
                    self._named = self.reader.named_destinations
                except Exception:
                    self._named = {}
            name = txt(dest)
            target = self._named.get(name) or self._named.get("/" + name)
            if target is None:
                return None
            try:
                return self.reader.get_destination_page_number(target) + 1
            except Exception:
                return None
        if isinstance(dest, list) and dest:
            first = dest[0]
            if isinstance(first, int) and not isinstance(first, bool):
                # Spec says local links point at a page object, but some writers
                # store a zero-based page number instead. Accept it.
                return first + 1 if 0 <= first < len(self.page_info) else None
            return self.page_refs.get(oid(first))
        return None

    @staticmethod
    def _filespec_name(spec):
        spec = R(spec)
        if isinstance(spec, dict):
            return txt(dget(spec, "/UF")) or txt(dget(spec, "/F"))
        return txt(spec)

    def f_hyperlinks(self):
        internal, external, other = Counter(), Counter(), Counter()
        for page_no, annot in self.annots:
            if dget(annot, "/Subtype") != "/Link":
                continue
            action = dget(annot, "/A")
            kind = dget(action, "/S")
            if kind == "/URI":
                external[(page_no, one_line(txt(dget(action, "/URI")), 300))] += 1
            elif kind == "/GoTo" or (action is None and dget(annot, "/Dest") is not None):
                target = self._dest_page(dict.get(action, "/D") if isinstance(action, dict) else dict.get(annot, "/Dest"))
                internal[(page_no, f"p.{target}" if target else "(destination not found)")] += 1
            elif kind == "/GoToR":
                other[(page_no, "other PDF: " + self._filespec_name(dget(action, "/F")))] += 1
            elif kind == "/Launch":
                other[(page_no, "opens file: " + self._filespec_name(dget(action, "/F")))] += 1
            elif kind is not None:
                other[(page_no, f"{txt(kind)} action")] += 1

        total_internal, total_external, total_other = sum(internal.values()), sum(external.values()), sum(other.values())
        if not (total_internal or total_external or total_other):
            return absent("The file has no hyperlinks.")

        lines, data = [], []
        for label, counter, category in (("internal", internal, "internal"), ("external", external, "external"), ("other", other, "other")):
            for (page_no, target), n in counter.items():
                lines.append(f"[{label}] p.{page_no} → {target}" + (f"  ×{n}" if n > 1 else ""))
                data.append({"type": category, "page": page_no, "target": target, "count": n})
        bits = [f"{total_internal:,} internal (sheet to sheet)", f"{total_external:,} external"]
        if total_other:
            bits.append(f"{total_other:,} other")
        return result(summary=", ".join(bits), detail=lines, data=data)

    def f_form_fields(self):
        acroform = dget(self.root, "/AcroForm")
        try:
            fields = self.reader.get_fields() or {}
        except Exception as exc:
            return result("error", notes=[f"The form data is damaged: {one_line(str(exc), 160)}"])
        type_names = {"/Tx": "text", "/Btn": "button/checkbox", "/Ch": "choice", "/Sig": "signature"}
        lines, data, filled = [], [], 0
        for name, f in fields.items():
            ftype = type_names.get(f.get("/FT"), txt(f.get("/FT")) or "group")
            if ftype == "group" and "/Kids" in f:
                continue  # a container for other fields, not a field itself
            value = one_line(txt(f.get("/V")), 300)
            if value and value != "Off":
                filled += 1
            lines.append(f"{name} ({ftype}): {value or '(empty)'}")
            data.append({"name": name, "type": ftype, "value": value or None})
        notes = []
        if dget(acroform, "/XFA") is not None:
            notes.append("The file also contains an XFA form, which is not listed here.")
        if not data:
            return absent(*notes, "The file has no form fields.")
        return result(summary=f"{plural(len(data), 'field')}, {filled:,} filled in", detail=lines, notes=notes, data=data)

    def f_annotations(self):
        rows = []
        for page_no, annot in self.annots:
            subtype = dget(annot, "/Subtype")
            if subtype in NON_MARKUP_ANNOTS:
                continue
            rows.append({
                "page": page_no,
                "type": txt(subtype) or "unknown",
                "subject": one_line(txt(dget(annot, "/Subj")), 80) or None,
                "author": one_line(txt(dget(annot, "/T")), 80) or None,
                "modified": pdf_date(dget(annot, "/M")),
                "contents": one_line(txt(dget(annot, "/Contents")), 200) or None,
            })
        if not rows:
            return absent("The pages have no comments or markups.")
        counts = Counter(r["type"] for r in rows)
        lines = []
        for r in rows:
            parts = [f"p.{r['page']}", r["subject"] or r["type"]]
            if r["author"]:
                parts.append(f"by {r['author']}")
            if r["modified"]:
                parts.append(r["modified"])
            if r["contents"]:
                parts.append(f"“{r['contents']}”")
            lines.append(" · ".join(parts))
        authors = {r["author"] for r in rows if r["author"]}
        summary = f"{plural(len(rows), 'markup')}: " + ", ".join(f"{t} ({n:,})" for t, n in counts.most_common(6))
        if authors:
            summary += f" — {plural(len(authors), 'author')}"
        return result(summary=summary, detail=lines, data=rows[:MAX_DATA_ITEMS])

    # -----------------------------------------------------------------------
    # Embedded assets
    # -----------------------------------------------------------------------
    def _embedded_file(self, spec, where):
        spec = R(spec)
        stream = dget(dget(spec, "/EF"), "/UF") or dget(dget(spec, "/EF"), "/F")
        size = num(dget(dget(stream, "/Params"), "/Size"))
        if size is None and stream is not None:
            stored = stream_bytes(stream)
            if stored is not None and stored < 25 * 1024 * 1024:
                try:
                    size = len(stream.get_data())  # decompress to get the real file size
                except Exception:
                    size = stored
            else:
                size = stored
        return {
            "name": self._filespec_name(spec) or "(unnamed)",
            "description": one_line(txt(dget(spec, "/Desc")), 200) or None,
            "mimeType": txt(dget(stream, "/Subtype")) or None,
            "bytes": int(size) if size is not None else None,
            "location": where,
        }

    def f_attachments(self):
        files = []

        def walk_names(node, depth=0):
            node = R(node)
            if not isinstance(node, dict) or depth > 32:
                return
            names = as_list(dget(node, "/Names"))
            for i in range(1, len(names), 2):
                files.append(self._embedded_file(names[i], "document"))
            for kid in as_list(dget(node, "/Kids")):
                walk_names(kid, depth + 1)

        walk_names(dget(dget(self.root, "/Names"), "/EmbeddedFiles"))
        for page_no, annot in self.annots:
            if dget(annot, "/Subtype") == "/FileAttachment":
                files.append(self._embedded_file(dget(annot, "/FS"), f"page {page_no}"))

        if not files:
            return absent("The file has no attachments.")
        lines = []
        for f in files:
            line = f"{f['name']} — {human_bytes(f['bytes'])}"
            if f["location"] != "document":
                line += f" — attached on {f['location']}"
            if f["description"]:
                line += f" — {f['description']}"
            lines.append(line)
        total = sum(f["bytes"] or 0 for f in files)
        return result(summary=f"{plural(len(files), 'attachment')}, {human_bytes(total)} total", detail=lines, data=files)

    def f_fonts(self):
        if not self.fonts:
            return absent("The pages don't reference any fonts.")
        unique = OrderedDict()
        for f in self.fonts.values():
            unique.setdefault((f["name"], f["type"], f["embedded"]), f)
        fonts = sorted(unique.values(), key=lambda f: (f["embedded"], f["name"].lower()))
        missing = [f for f in fonts if not f["embedded"]]
        lines = []
        for f in fonts:
            state = "embedded" + (" (subset)" if f["subset"] else "") if f["embedded"] else \
                ("NOT EMBEDDED (standard PDF font)" if f["standard14"] else "NOT EMBEDDED")
            lines.append(f"{f['name']} ({f['type']}) — {state}")
        flags = []
        risky = [f for f in missing if not f["standard14"]]
        if risky:
            flags.append("Not embedded, may display differently elsewhere: " + ", ".join(f["name"] for f in risky[:12]) +
                         (f", +{len(risky) - 12} more" if len(risky) > 12 else ""))
        summary = f"{plural(len(fonts), 'font')} — " + (f"{len(missing):,} not embedded" if missing else "all embedded")
        notes = []
        if missing and not risky:
            notes.append("The fonts that aren't embedded are standard PDF fonts, which every PDF viewer supplies.")
        return result(summary=summary, detail=lines, flags=flags, notes=notes, data=fonts)

    def f_images(self):
        images = [dict(img, pages=sorted(img["pages"])) for key, img in self.images.items() if key not in self.soft_masks]
        if not images:
            return absent("The pages contain no embedded raster images.")
        lines = []
        for img in images:
            where = f"p.{page_ranges(img['pages'])}" if img["pages"] else "not placed on a page"
            bits = f"{img['bitsPerComponent']}-bit " if img["bitsPerComponent"] else ""
            lines.append(f"{where} · {img['width']:,}×{img['height']:,} px · {bits}{img['colorSpace']} · {img['encoding']} · {human_bytes(img['bytes'])}")
        total = sum(img["bytes"] or 0 for img in images)
        largest = max(images, key=lambda i: i["width"] * i["height"])
        summary = (f"{plural(len(images), 'image')}, {human_bytes(total)} total — "
                   f"largest {largest['width']:,}×{largest['height']:,} px")
        notes = ["Small images placed directly in page content (inline images) are not counted."]
        if self.soft_masks:
            notes.append(f"{plural(len(self.soft_masks), 'transparency mask')} not counted as separate images.")
        return result(summary=summary, detail=lines, notes=notes, data=images[:MAX_DATA_ITEMS])

    # -----------------------------------------------------------------------
    # Security
    # -----------------------------------------------------------------------
    def _encrypt_dict(self):
        return dget(self.reader.trailer, "/Encrypt")

    def f_encryption(self):
        enc = self._encrypt_dict()
        if not isinstance(enc, dict):
            return absent("The file is not encrypted.")
        version = int(num(dget(enc, "/V"), 0))
        revision = int(num(dget(enc, "/R"), 0))
        length = int(num(dget(enc, "/Length"), 40))
        if version in (1, 2):
            method = f"RC4 {length}-bit"
        elif version == 4:
            cfm = txt(dget(dget(dget(enc, "/CF"), "/StdCF"), "/CFM"))
            method = {"AESV2": "AES 128-bit", "V2": "RC4 128-bit", "None": "no cipher"}.get(cfm, f"crypt filter {cfm or 'unknown'}")
        elif version == 5:
            method = "AES 256-bit"
        else:
            method = f"unknown method (V{version})"
        handler = txt(dget(enc, "/Filter")) or "unknown"
        if self.locked:
            access = "Password required to open"
        else:
            access = "Opens without a password; an owner password sets the restrictions"
        detail = [f"Method: {method}", f"Security handler: {handler}, V{version} R{revision}", access]
        flags = ["A password is required to open this file."] if self.locked else []
        if dget(enc, "/EncryptMetadata") is not None and not dget(enc, "/EncryptMetadata").value:
            detail.append("Document metadata is left unencrypted")
        return result(summary=f"Encrypted — {method} — {access.split(';')[0].lower()}", detail=detail, flags=flags,
                      data={"method": method, "handler": handler, "version": version, "revision": revision,
                            "openPasswordRequired": self.locked})

    def f_permissions(self):
        enc = self._encrypt_dict()
        if not isinstance(enc, dict):
            return absent("No restrictions: the file is not encrypted.")
        p = int(num(dget(enc, "/P"), -1)) & 0xFFFFFFFF
        revision = int(num(dget(enc, "/R"), 2))

        def bit(n):
            return bool(p & (1 << (n - 1)))

        if not bit(3):
            printing = "not allowed"
        elif revision >= 3 and not bit(12):
            printing = "low resolution only"
        else:
            printing = "allowed"
        perms = OrderedDict([
            ("Printing", printing),
            ("Changing the document", "allowed" if bit(4) else "not allowed"),
            ("Copying text and graphics", "allowed" if bit(5) else "not allowed"),
            ("Commenting", "allowed" if bit(6) else "not allowed"),
            ("Filling in form fields", "allowed" if (bit(9) if revision >= 3 else bit(6)) else "not allowed"),
            ("Extraction for accessibility", "allowed" if (bit(10) if revision >= 3 else bit(5)) else "not allowed"),
            ("Page assembly (insert, rotate, delete)", "allowed" if (bit(11) if revision >= 3 else bit(4)) else "not allowed"),
        ])
        restricted = [k for k, v in perms.items() if v != "allowed"]
        lines = [f"{k}: {v}" for k, v in perms.items()]
        if restricted:
            summary = "Restricted: " + ", ".join(k.lower() for k in restricted)
            flags = [f"Restricted: {', '.join(k.lower() for k in restricted)}."]
        else:
            summary = "Encrypted, but no actions are restricted"
            flags = []
        return result(summary=summary, detail=lines, flags=flags,
                      notes=["Restrictions are enforced by the PDF viewer and can be lifted with the owner password."],
                      data={"flags": p, "permissions": perms})


def inspect_pdf(data, progress=None):
    return Inspector(data, progress).run()


def run_json(data, progress=None):
    """What the browser worker calls. Always returns JSON; never raises."""
    try:
        if hasattr(data, "to_py"):  # a JavaScript Uint8Array handed over by Pyodide
            data = data.to_py()
        return json.dumps({"ok": True, "report": inspect_pdf(bytes(data), progress)})
    except DependencyError as exc:
        return json.dumps({"ok": False, "needs": "cryptography", "message": str(exc)})
    except Exception as exc:
        return json.dumps({"ok": False, "message": f"This file couldn't be read as a PDF: {one_line(str(exc), 300)}"})
