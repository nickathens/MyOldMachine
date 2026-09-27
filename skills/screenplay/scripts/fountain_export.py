"""Fountain parsing and export through screenplain, in process.

screenplain lays every PDF page out in the PDF standard Courier, which only
has Western European letters. A Greek script came out in a different face
with every accented vowel as a black box, and afterwriting's fonts print Greek
as blank space (both seen 2026-09-27). When a script needs letters outside
that set, a Courier style face that has them is registered under reportlab's
four Courier names, so screenplain's layout and its bold and italic markup
stay exactly as they are. Nimbus Mono PS (the URW Courier clone that comes
with Ghostscript) is first choice. It ships as CFF outlines, which reportlab
cannot read, so it is converted to TrueType in memory (about 0.4 s a face).
"""

import io
import os
import re
import shutil
import subprocess
import unicodedata
from collections import Counter
from pathlib import Path

STYLES = ("Regular", "Bold", "Italic", "Bold Italic")
REPORTLAB_NAMES = {
    "Regular": "Courier",
    "Bold": "Courier-Bold",
    "Italic": "Courier-Oblique",
    "Bold Italic": "Courier-BoldOblique",
}
# All four are 0.6 em wide like Courier, so the page count does not move.
FAMILIES = ("Nimbus Mono PS", "Courier New", "Liberation Mono", "FreeMono")

TIME_OF_DAY = {
    "DAY", "NIGHT", "DAWN", "DUSK", "MORNING", "AFTERNOON", "EVENING", "SUNRISE",
    "SUNSET", "NOON", "MIDNIGHT", "CONTINUOUS", "LATER", "MOMENTS LATER", "SAME",
    "SAME TIME",
    "ΜΕΡΑ", "ΝΥΧΤΑ", "ΞΗΜΕΡΩΜΑ", "ΣΟΥΡΟΥΠΟ", "ΠΡΩΙ", "ΜΕΣΗΜΕΡΙ", "ΑΠΟΓΕΥΜΑ",
    "ΒΡΑΔΥ", "ΣΥΝΕΧΕΙΑ", "ΑΡΓΟΤΕΡΑ",
}


def read_text(path):
    try:
        return Path(path).read_text(encoding="utf-8-sig")
    except UnicodeDecodeError as exc:
        # a Greek script saved on Windows is often cp1253
        raise RuntimeError(f"{path} is not UTF-8 text (byte {exc.start}); save it as UTF-8 and export again") from None


# Fountain lyrics are lines starting with ~, printed in italics; screenplain
# does not know them and printed the tilde
LYRIC = re.compile(r"^~[ \t]*(.*\S)[ \t]*$", re.MULTILINE)


def parse_text(text):
    """Parse Fountain with screenplain's parser (boneyard and notes removed)."""
    from screenplain.parsers import fountain

    return fountain.parse(io.StringIO(LYRIC.sub(r"*\1*", text)))


def parse(path):
    return parse_text(read_text(path))


def needs_unicode_font(text):
    """True when the PDF standard Courier cannot draw every character."""
    try:
        text.encode("cp1252")
        return False
    except UnicodeEncodeError:
        return True


# macOS keeps Courier New outside fontconfig when fontconfig is not installed
MAC_FONTS = {
    ("Courier New", "Regular"): "/System/Library/Fonts/Supplemental/Courier New.ttf",
    ("Courier New", "Bold"): "/System/Library/Fonts/Supplemental/Courier New Bold.ttf",
    ("Courier New", "Italic"): "/System/Library/Fonts/Supplemental/Courier New Italic.ttf",
    ("Courier New", "Bold Italic"): "/System/Library/Fonts/Supplemental/Courier New Bold Italic.ttf",
}


def _font_file(family, style):
    """The file fontconfig holds for family and style, or None."""
    if not shutil.which("fc-match"):
        path = MAC_FONTS.get((family, style))
        return path if path and os.path.isfile(path) else None
    try:
        out = subprocess.run(["fc-match", "-f", "%{family}\n%{file}", f"{family}:style={style}"],
                             capture_output=True, text=True, timeout=20).stdout
    except (OSError, subprocess.TimeoutExpired):
        return None
    names, _, path = out.partition("\n")
    # fc-match answers with its best substitute when the family is missing
    if family not in [n.strip() for n in names.split(",")] or not path:
        return None
    return path


def _truetype_bytes(font):
    """The font as TrueType bytes; CFF outlines are converted to quadratic."""
    from fontTools.pens.cu2quPen import Cu2QuPen
    from fontTools.pens.ttGlyphPen import TTGlyphPen
    from fontTools.ttLib import newTable

    if "CFF " in font:
        order = font.getGlyphOrder()
        glyph_set = font.getGlyphSet()
        quadratic = {}
        for name in order:
            pen = TTGlyphPen(glyph_set)
            glyph_set[name].draw(Cu2QuPen(pen, 1.0, reverse_direction=True))
            quadratic[name] = pen.glyph()
        font["loca"] = newTable("loca")
        font["glyf"] = glyf = newTable("glyf")
        glyf.glyphOrder = order
        glyf.glyphs = quadratic
        del font["CFF "]
        if "VORG" in font:
            del font["VORG"]
        glyf.compile(font)
        hmtx = font["hmtx"]
        for name, glyph in glyf.glyphs.items():
            if hasattr(glyph, "xMin"):
                hmtx[name] = (hmtx[name][0], glyph.xMin)
        font["maxp"] = maxp = newTable("maxp")
        maxp.tableVersion = 0x00010000
        maxp.maxZones = 1
        for field in ("maxTwilightPoints", "maxStorage", "maxFunctionDefs", "maxInstructionDefs",
                      "maxStackElements", "maxSizeOfInstructions"):
            setattr(maxp, field, 0)
        maxp.maxComponentElements = max(len(getattr(g, "components", None) or [])
                                        for g in glyf.glyphs.values())
        maxp.compile(font)
        post = font["post"]
        post.formatType = 2.0
        post.extraNames = []
        post.mapping = {}
        post.glyphOrder = order
        font.sfntVersion = "\x00\x01\x00\x00"
    buffer = io.BytesIO()
    font.save(buffer)
    return buffer.getvalue()


def _fallback_fonts(missing):
    """Map each letter in missing to an installed TrueType file that has it."""
    from fontTools.ttLib import TTFont

    found, tried = {}, set()
    remaining = set(missing)
    while remaining and shutil.which("fc-list"):
        target = min(remaining)
        try:
            listing = subprocess.run(["fc-list", f":charset={ord(target):x}", "file"],
                                     capture_output=True, text=True, timeout=20).stdout
        except (OSError, subprocess.TimeoutExpired):
            break
        paths = sorted({line.rstrip(": ").strip() for line in listing.splitlines()} - tried)
        # reportlab reads TrueType outlines only, so .otf and collections are out
        paths = [path for path in paths if path.lower().endswith(".ttf")]
        chosen = None
        for path in paths:
            tried.add(path)
            try:
                cmap = TTFont(path, lazy=True).getBestCmap()
            except Exception:
                continue
            if ord(target) in cmap:
                chosen = (path, cmap)
                break
        if not chosen:
            remaining.discard(target)
            continue
        path, cmap = chosen
        for ch in [ch for ch in remaining if ord(ch) in cmap]:
            found[ch] = path
            remaining.discard(ch)
    return found


def use_unicode_courier(text):
    """Register a Courier style face for the letters in text.

    The first family in FAMILIES that has every letter wins; failing that,
    the one missing the fewest, and each missing letter is set in an installed
    TrueType font that has it (a Chinese word in an English script). Returns
    (family, {letter: reportlab font name}, letters no font has). Raises
    RuntimeError when none of FAMILIES is installed.
    """
    from fontTools.ttLib import TTFont
    from reportlab.pdfbase import pdfmetrics
    from reportlab.pdfbase.ttfonts import TTFont as ReportlabFont

    wanted = {ch for ch in text if ch.isprintable() and not ch.isspace()}
    best = None
    for family in FAMILIES:
        files = {style: _font_file(family, style) for style in STYLES}
        if not files["Regular"]:
            continue
        regular = TTFont(files["Regular"])
        missing = {ch for ch in wanted if ord(ch) not in regular.getBestCmap()}
        if best is None or len(missing) < len(best[3]):
            best = (family, files, regular, missing)
        if not missing:
            break
    if best is None:
        raise RuntimeError(f"none of {', '.join(FAMILIES)} is installed; "
                           "on Debian or Ubuntu: apt install fonts-urw-base35 "
                           "(macOS has Courier New; brew install fontconfig finds it)")
    family, files, regular, missing = best
    for style in STYLES:
        path = files[style] or files["Regular"]
        data = _truetype_bytes(regular if path == files["Regular"] else TTFont(path))
        pdfmetrics.registerFont(ReportlabFont(REPORTLAB_NAMES[style], io.BytesIO(data)))
    if not isinstance(pdfmetrics.getFont("Courier"), ReportlabFont):
        raise RuntimeError("Courier was already in use in this process; cannot swap the face")

    fallback = {}
    names = {}
    for ch, path in sorted(_fallback_fonts(missing).items()):
        if path not in names:
            names[path] = f"Fallback{len(names) + 1}"
            pdfmetrics.registerFont(ReportlabFont(names[path], path))
        fallback[ch] = names[path]
    return family, fallback, sorted(missing - set(fallback))


def _with_fallback(paragraph, fallback):
    """Paragraph factory that sets the letters in fallback in their own fonts.

    screenplain writes every non-ASCII letter as a numeric entity, which is
    where the wrapping happens.
    """
    codes = {ord(ch): name for ch, name in fallback.items()}

    def wrap(match):
        name = codes.get(int(match.group(1)))
        return f'<font face="{name}">{match.group(0)}</font>' if name else match.group(0)

    def make(markup, *args, **kwargs):
        return paragraph(re.sub(r"&#(\d+);", wrap, markup), *args, **kwargs)

    return make


def write_pdf(play, out, a4=False, fallback=None):
    """Lay the script out as screenplain does. Returns the page count, title page excluded."""
    from reportlab.lib import pagesizes
    from screenplain.export import pdf

    settings = pdf.Settings(page_size=pagesizes.A4 if a4 else pagesizes.letter)
    docs = []

    def template(*args, **kwargs):
        docs.append(pdf.DocTemplate(*args, **kwargs))
        return docs[-1]

    original = pdf.Paragraph
    if fallback:
        pdf.Paragraph = _with_fallback(original, fallback)
    try:
        pdf.to_pdf(play, out, template_constructor=template, settings=settings)
    finally:
        pdf.Paragraph = original
    return docs[0].page - (1 if docs[0].has_title_page else 0)


def export(source, output, fmt, a4=False):
    """Write source (a .fountain file) to output as pdf, html or fdx.

    Returns a dict: pages (PDF only, title page not counted), family (the
    face standing in for Courier, when one was needed), fallback (letters set
    in another font) and missing (letters no installed font has; they print
    as boxes). The file appears whole or not at all.
    """
    text = read_text(source)
    play = parse_text(text)
    output = Path(output)
    tmp = output.with_name(f".{output.name}.part")
    result = {"pages": None, "family": None, "fallback": {}, "missing": []}
    try:
        if fmt == "pdf":
            if needs_unicode_font(text):
                result["family"], result["fallback"], result["missing"] = use_unicode_courier(text)
            with open(tmp, "wb") as out:
                result["pages"] = write_pdf(play, out, a4, result["fallback"])
        elif fmt == "html":
            from screenplain.export.html import convert

            with open(tmp, "w", encoding="utf-8") as out:
                convert(play, out)
        elif fmt == "fdx":
            from screenplain.export.fdx import to_fdx

            with open(tmp, "w", encoding="utf-8") as out:
                to_fdx(play, out)
        else:
            raise ValueError(f"unknown format {fmt!r}")
        os.replace(tmp, output)
    finally:
        tmp.unlink(missing_ok=True)
    return result


def _plain(rich):
    return str(rich).strip()


def speaker(dialog):
    """The character cue without extensions such as (V.O.) or (CONT'D)."""
    return re.sub(r"\s*\([^)]*\)", "", _plain(dialog.character)).strip()


def _unaccented(text):
    return "".join(ch for ch in unicodedata.normalize("NFD", text) if not unicodedata.combining(ch))


def location(slug):
    """The scene heading without its time of day: INT. KITCHEN - DAY gives INT. KITCHEN."""
    heading = _plain(slug.line).upper()
    parts = [p.strip() for p in heading.split(" - ")]
    if len(parts) > 1 and _unaccented(parts[-1].split("(")[0].strip()) in TIME_OF_DAY:
        parts = parts[:-1]
    return " - ".join(parts)


def stats(play):
    """Scene, speech and word counts from a parsed screenplay."""
    from screenplain.types import Dialog, DualDialog, Slug

    scenes = 0
    locations = set()
    speeches = Counter()
    words = Counter()
    for para in play:
        if isinstance(para, Slug):
            scenes += 1
            locations.add(location(para))
            continue
        if isinstance(para, DualDialog):
            dialogs = (para.left, para.right)
        elif isinstance(para, Dialog):
            dialogs = (para,)
        else:
            continue
        for dialog in dialogs:
            name = speaker(dialog)
            speeches[name] += 1
            words[name] += sum(len(_plain(line).split()) for is_paren, line in dialog.blocks if not is_paren)
    return {"scenes": scenes, "locations": locations, "speeches": speeches, "words": words}
