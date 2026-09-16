"""
Builds the synthetic test PDFs in tests/fixtures/.

These are hand-assembled so every report field has something to find, using
the structures described in the PDF specification. They prove the engine reads
those structures correctly - they are NOT a substitute for real exports from
Revit, AutoCAD and Bluebeam, which may lay things out differently.

Run:  python tests/make_fixtures.py
"""

from pathlib import Path

from pypdf import PdfWriter
from pypdf.annotations import FreeText, Link, Polygon, Line
from pypdf.constants import UserAccessPermissions
from pypdf.generic import (
    ArrayObject, DecodedStreamObject, DictionaryObject, FloatObject,
    NameObject, NumberObject, TextStringObject,
)

OUT = Path(__file__).parent / "fixtures"


def N(name):
    return NameObject(name)


def T(text):
    return TextStringObject(text)


def A(*items):
    return ArrayObject(list(items))


def D(**entries):
    """D(Type=N('/OCG')) -> << /Type /OCG >>"""
    return DictionaryObject({NameObject("/" + k): v for k, v in entries.items()})


def F(x):
    return FloatObject(x)


def I(x):
    return NumberObject(x)


def stream(data, **entries):
    s = DecodedStreamObject()
    s.set_data(data)
    s.update({NameObject("/" + k): v for k, v in entries.items()})
    return s


def build_full():
    w = PdfWriter()
    add = w._add_object
    p1 = w.add_blank_page(36 * 72, 24 * 72)   # ARCH D landscape
    p2 = w.add_blank_page(36 * 72, 24 * 72)   # ARCH D landscape
    p3 = w.add_blank_page(8.5 * 72, 11 * 72)  # Letter portrait -> mixed sizes

    w.add_metadata({
        "/Title": "Riverside Clinic - Level 1 Floor Plan",
        "/Author": "R. Clarke",
        "/Subject": "Permit set",
        "/Keywords": "clinic, floor plan, permit",
        "/Creator": "Autodesk Revit 2025",
        "/Producer": "Bluebeam PDF Library 21",
        "/CreationDate": "D:20260301093000-08'00'",
        "/ModDate": "D:20260310170500-08'00'",
        "/Project Number": "2026-118",
        "/Phase": "Construction Documents",
    })

    # --- layers -------------------------------------------------------------
    walls = add(D(Type=N("/OCG"), Name=T("A-WALL")))
    doors = add(D(Type=N("/OCG"), Name=T("A-DOOR"), Usage=D(Print=D(PrintState=N("/OFF")))))
    notes = add(D(Type=N("/OCG"), Name=T("G-ANNO-TEXT")))
    w._root_object[N("/OCProperties")] = D(
        OCGs=A(walls, doors, notes),
        D=D(BaseState=N("/ON"), OFF=A(notes), Locked=A(walls),
            Order=A(A(T("Level 1 Floor Plan"), walls, doors, notes))),
    )

    # --- fonts, images, marked content (shared by pages 1 and 2) -------------
    font_file = add(stream(b"\x00fake-truetype-program"))
    arial = add(D(Type=N("/Font"), Subtype=N("/TrueType"), BaseFont=N("/ABCDEF+ArialMT"),
                  FontDescriptor=add(D(Type=N("/FontDescriptor"), FontName=N("/ABCDEF+ArialMT"), FontFile2=font_file))))
    romans = add(D(Type=N("/Font"), Subtype=N("/TrueType"), BaseFont=N("/RomanS"),
                   FontDescriptor=add(D(Type=N("/FontDescriptor"), FontName=N("/RomanS")))))
    helvetica = add(D(Type=N("/Font"), Subtype=N("/Type1"), BaseFont=N("/Helvetica")))

    mask = add(stream(b"\xff" * 4, Type=N("/XObject"), Subtype=N("/Image"), Width=I(2), Height=I(2),
                      ColorSpace=N("/DeviceGray"), BitsPerComponent=I(8)))
    logo = add(stream(b"\x10" * 12, Type=N("/XObject"), Subtype=N("/Image"), Width=I(2), Height=I(2),
                      ColorSpace=N("/DeviceRGB"), BitsPerComponent=I(8), SMask=mask))
    photo = add(stream(b"\x20" * 64, Type=N("/XObject"), Subtype=N("/Image"), Width=I(1600), Height=I(1200),
                       ColorSpace=N("/DeviceRGB"), BitsPerComponent=I(8), Filter=N("/DCTDecode")))
    title_block = add(stream(b"/Im2 Do", Type=N("/XObject"), Subtype=N("/Form"), BBox=A(I(0), I(0), I(100), I(100)),
                             Resources=D(XObject=D(Im2=photo))))
    element_data = add(D(ElementId=I(331245), Category=T("Doors")))
    shared = add(D(
        Font=D(F1=helvetica, F2=arial, F3=romans),
        XObject=D(Im1=logo, Fm1=title_block),
        Properties=D(MC0=element_data, OC1=walls),
    ))
    content = b"BT /F1 12 Tf 72 72 Td (Level 1) Tj ET q /Im1 Do Q q /Fm1 Do Q"
    for page in (p1, p2):
        page[N("/Resources")] = shared
        page[N("/Contents")] = add(stream(content))

    # --- scale (rectilinear measure) and georeference ------------------------
    # 1/8" = 1'-0": one inch of paper is 8 feet, so one point is 8/72 ft.
    p1[N("/VP")] = A(D(
        Type=N("/Viewport"), Name=T("Floor Plan"), BBox=A(F(72), F(72), F(2520), F(1656)),
        Measure=D(Type=N("/Measure"), Subtype=N("/RL"), R=T("1/8 in = 1 ft"),
                  X=A(D(Type=N("/NumberFormat"), U=T("ft"), C=F(8 / 72)),
                      D(Type=N("/NumberFormat"), U=T("in"), C=F(12))),
                  D=A(D(Type=N("/NumberFormat"), U=T("ft"), C=F(1))),
                  A=A(D(Type=N("/NumberFormat"), U=T("sq ft"), C=F(1)))),
    ))
    p2[N("/VP")] = A(D(
        Type=N("/Viewport"), Name=T("Site Plan"), BBox=A(F(0), F(0), F(2592), F(1728)),
        Measure=D(Type=N("/Measure"), Subtype=N("/GEO"),
                  Bounds=A(*[F(v) for v in (0, 0, 0, 1, 1, 1, 1, 0)]),
                  GCS=D(Type=N("/PROJCS"), EPSG=I(2230),
                        WKT=T('PROJCS["NAD83 / California zone 6 (ftUS)",GEOGCS["NAD83"]]')),
                  GPTS=A(*[F(v) for v in (32.71, -117.17, 32.72, -117.17, 32.72, -117.15, 32.71, -117.15)]),
                  LPTS=A(*[F(v) for v in (0, 0, 0, 1, 1, 1, 1, 0)]),
                  PDU=A(N("/FT"), N("/SQFT"), N("/DEG"))),
    ))
    p3[N("/LGIDict")] = D(
        Type=N("/LGIDict"), Version=T("2.1"), Description=T("USGS quad"),
        Projection=D(Type=N("/Projection"), ProjectionType=T("UT"), Datum=T("NAS"), Zone=I(11)),
        Registration=A(A(F(100), F(100), F(500000), F(3600000)), A(F(500), F(700), F(501000), F(3601500))),
        CTM=A(*[F(v) for v in (2.5, 0, 0, 2.5, 499750, 3599750)]),
    )

    # --- bookmarks -----------------------------------------------------------
    sheets = w.add_outline_item("Sheets", 0)
    w.add_outline_item("A101 Floor Plan", 0, parent=sheets)
    w.add_outline_item("C101 Site Plan", 1, parent=sheets)
    w.add_outline_item("Specifications", 2)

    # --- links -----------------------------------------------------------------
    w.add_annotation(0, Link(rect=(100, 100, 200, 120), target_page_index=1))
    w.add_annotation(0, Link(rect=(100, 130, 200, 150), target_page_index=1))
    w.add_annotation(1, Link(rect=(100, 100, 200, 120), url="https://bim-press.com"))

    # --- markups and measurements --------------------------------------------
    note = w.add_annotation(0, FreeText(text="Verify door swing", rect=(300, 300, 500, 340)))
    note.update({N("/T"): T("R. Clarke"), N("/M"): T("D:20260305101500-08'00'")})

    area = w.add_annotation(0, Polygon(vertices=[(600, 600), (900, 600), (900, 900), (600, 900)]))
    area.update({
        N("/IT"): N("/PolygonDimension"), N("/Subj"): T("Area Measurement"),
        N("/Contents"): T("1,245.50 sf"), N("/T"): T("R. Clarke"),
        N("/Measure"): D(Type=N("/Measure"), Subtype=N("/RL"), R=T("1/8 in = 1 ft")),
        N("/BSIColumnData"): A(T("Open"), T("Architectural")),
    })
    length = w.add_annotation(0, Line(p1=(100, 1000), p2=(600, 1000), rect=(100, 990, 600, 1010)))
    length.update({N("/Subj"): T("Length Measurement"), N("/Contents"): T("42'-6\"")})
    w._root_object[N("/BSIColumns")] = A(D(Name=T("Status")), D(Name=T("Discipline")))

    # --- form fields -----------------------------------------------------------
    project_name = add(D(Type=N("/Annot"), Subtype=N("/Widget"), FT=N("/Tx"), T=T("Project Name"),
                         V=T("Riverside Clinic"), Rect=A(F(50), F(50), F(250), F(70))))
    approved = add(D(Type=N("/Annot"), Subtype=N("/Widget"), FT=N("/Btn"), T=T("Approved"),
                     V=N("/Off"), Rect=A(F(50), F(80), F(70), F(100))))
    p3[N("/Annots")] = A(project_name, approved)
    w._root_object[N("/AcroForm")] = D(Fields=A(project_name, approved))

    # --- attachment --------------------------------------------------------------
    w.add_attachment("door-schedule.csv", b"MARK,TYPE,LEVEL\n101,A,Level 1\n102,B,Level 1\n")

    # --- tagged object data (structure tree with UserProperties) -----------------
    def props(**values):
        return A(*[D(N=T(k.replace("_", " ")), V=T(v)) for k, v in values.items()])

    door = add(D(Type=N("/StructElem"), S=N("/Figure"), K=I(0),
                 A=D(O=N("/UserProperties"), P=props(Category="Doors", Family="Single-Flush",
                                                     Type='36" x 84"', Level="Level 1", Mark="101"))))
    wall = add(D(Type=N("/StructElem"), S=N("/Figure"), K=I(1), C=N("/WallData")))
    plain = add(D(Type=N("/StructElem"), S=N("/P"), K=I(2)))
    document = add(D(Type=N("/StructElem"), S=N("/Document"), K=A(door, wall, plain)))
    w._root_object[N("/StructTreeRoot")] = D(
        Type=N("/StructTreeRoot"), K=document,
        ClassMap=D(WallData=D(O=N("/UserProperties"), P=props(Category="Walls", Family="Basic Wall",
                                                               Type="Interior - 4 7/8\" Partition", Level="Level 1"))),
    )
    return w


def build_plain():
    w = PdfWriter()
    w.add_blank_page(8.5 * 72, 11 * 72)
    return w


def build_owner_locked():
    w = PdfWriter()
    w.add_blank_page(8.5 * 72, 11 * 72)
    w.add_metadata({"/Title": "Owner-protected sheet"})
    perms = UserAccessPermissions.all() & ~UserAccessPermissions.PRINT & ~UserAccessPermissions.EXTRACT
    w.encrypt(user_password="", owner_password="owner-secret", permissions_flag=perms, algorithm="AES-256")
    return w


def build_user_locked():
    w = PdfWriter()
    w.add_blank_page(8.5 * 72, 11 * 72)
    w.encrypt(user_password="open-secret", owner_password="owner-secret", algorithm="AES-128")
    return w


def build_revit_like():
    """Mirrors the structure of a real Revit 2027 export: lowercase custom info
    keys, per-page sheet keys, view transforms as marked-content properties,
    and /ElementNNN tags inside /ViewRegionNNN-N sections. All values invented."""
    w = PdfWriter()
    add = w._add_object
    page = w.add_blank_page(17 * 72, 11 * 72)
    w.add_metadata({"/creator": "Autodesk Revit", "/creator_version": "2027", "/document_id": "00000000-0000-0000-0000-000000000001"})
    page.update({
        N("/revit_metadata_view_name"): T("A101 - FLOOR PLAN "),
        N("/revit_metadata_view_sheet_number"): T("A101 "),
        N("/revit_metadata_view_type"): T("VT_Drafting "),
    })

    def view_props(scale):
        vp = [scale, 0, 0, 10, 0, scale, 0, -20, 0, 0, scale, 0, 0, 0, 0, 1]
        return add(D(PRECISION=I(5), UNITS=T("inches"), VP=A(*[F(v) for v in vp])))

    page[N("/Resources")] = D(Properties=D(MC0=view_props(0.25), MC1=view_props(0.5), MC2=view_props(24)))
    content = (
        b"/ViewRegion1001-1 /MC0 BDC /Element5001 BMC q -400 100 m -400 -250 l -160 -250 l -160 100 l h W* n "
        b"0 0 m 10 10 l S EMC /Element5002 BMC 1 1 m 2 2 l S EMC Q EMC "
        b"/ViewRegion1002-1 /MC1 BDC /Element5003 BMC q 0 100 m 0 -250 l 200 -250 l 200 100 l h W* n EMC Q EMC "
        b"/ViewRegion9000-0 /MC2 BDC /Element7001 BMC 5 5 m 6 6 l S EMC EMC "
        b"/Artifact << /Type /Pagination >> BDC EMC"
    )
    page[N("/Contents")] = add(stream(content))
    return w


def build_bluebeam_like():
    """Mirrors a real Revu markup set: /BSIColumnData arrays on markups with no
    column definitions stored anywhere in the file."""
    w = PdfWriter()
    w.add_blank_page(36 * 72, 24 * 72)
    w.add_metadata({"/Creator": "Bluebeam Stapler 20.2.85.2", "/Producer": "Acrobat Distiller 20.0 (Windows)"})
    for i, flags in enumerate([("False", "False", "True", "False"), ("False", "True", "False", "False")]):
        box = w.add_annotation(0, FreeText(text="", rect=(100, 100 + 60 * i, 200, 140 + 60 * i)))
        box.update({N("/Subj"): T("Rectangle"), N("/T"): T("Reviewer"), N("/BSIColumnData"): A(*[T(v) for v in flags])})
    return w


def main():
    OUT.mkdir(parents=True, exist_ok=True)
    for name, build in [("full.pdf", build_full), ("plain.pdf", build_plain),
                        ("owner-locked.pdf", build_owner_locked), ("user-locked.pdf", build_user_locked),
                        ("revit-like.pdf", build_revit_like), ("bluebeam-like.pdf", build_bluebeam_like)]:
        path = OUT / name
        with open(path, "wb") as fh:
            build().write(fh)
        print(f"wrote {path} ({path.stat().st_size:,} bytes)")
    (OUT / "not-a-pdf.pdf").write_bytes(b"This is a text file wearing a .pdf extension.\n")


if __name__ == "__main__":
    main()
