"""Convert documents into Markdown notes — entirely on this machine.

Supported: Word (.docx/.docm/.dotx), legacy Word/RTF/ODT/WebArchive (via macOS
`textutil` or LibreOffice when present), PDF (text layer, OCR fallback for
scans), PowerPoint (.pptx), Excel (.xlsx/.xlsm), CSV/TSV, HTML, email (.eml),
Outlook (.msg, optional), plain text/Markdown, JSON/XML/YAML/code, images
(OCR) and audio (local transcription, optional).
"""
from __future__ import annotations

import csv
import email
import email.policy
import io
import json
import re
import shutil
import subprocess
import tempfile
import zipfile
from dataclasses import dataclass, field
from pathlib import Path

from ..util import first_heading, title_from_filename


@dataclass
class Asset:
    name: str
    data: bytes
    mime: str


@dataclass
class Converted:
    title: str
    markdown: str
    assets: list[Asset] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    meta: dict = field(default_factory=dict)


class ConversionError(Exception):
    pass


WORD = {".docx", ".docm", ".dotx"}
LEGACY_DOC = {".doc", ".rtf", ".rtfd", ".odt", ".wordml", ".webarchive"}
PDF = {".pdf"}
SLIDES = {".pptx", ".pptm"}
SHEETS = {".xlsx", ".xlsm"}
DELIMITED = {".csv", ".tsv"}
HTML = {".html", ".htm", ".xhtml"}
EMAIL = {".eml"}
OUTLOOK = {".msg"}
TEXT = {".txt", ".text", ".log"}
MARKDOWN = {".md", ".markdown", ".mdown"}
CODE = {".json": "json", ".xml": "xml", ".yaml": "yaml", ".yml": "yaml", ".sql": "sql", ".py": "python",
        ".js": "javascript", ".ts": "typescript", ".sh": "bash", ".toml": "toml", ".ini": "ini", ".r": "r",
        ".scala": "scala", ".java": "java", ".go": "go", ".rs": "rust", ".css": "css", ".ipynb": "json"}
IMAGES = {".png", ".jpg", ".jpeg", ".gif", ".webp", ".heic", ".heif", ".tif", ".tiff", ".bmp"}
AUDIO = {".m4a", ".mp3", ".wav", ".aiff", ".aif", ".flac", ".ogg", ".opus", ".webm", ".mp4", ".mov"}
CALENDAR = {".ics"}


def supported_extensions() -> dict[str, list[str]]:
    return {
        "Word": sorted(WORD | LEGACY_DOC),
        "PDF": sorted(PDF),
        "PowerPoint": sorted(SLIDES),
        "Excel & CSV": sorted(SHEETS | DELIMITED),
        "Web & email": sorted(HTML | EMAIL | OUTLOOK),
        "Text & code": sorted(TEXT | MARKDOWN | set(CODE)),
        "Images (text recognition)": sorted(IMAGES),
        "Audio (transcription)": sorted(AUDIO),
        "Calendar": sorted(CALENDAR),
        "Archives": [".zip"],
    }


def is_supported(filename: str) -> bool:
    ext = Path(filename).suffix.lower()
    every = set().union(*[set(v) for v in supported_extensions().values()])
    return ext in every


# ---------------------------------------------------------------------------
# HTML -> Markdown
# ---------------------------------------------------------------------------
def html_to_markdown(html: str) -> str:
    from bs4 import BeautifulSoup
    from markdownify import MarkdownConverter

    soup = BeautifulSoup(html or "", "html.parser")
    for bad in soup(["script", "style", "noscript", "iframe", "object", "embed", "svg", "head", "form", "button"]):
        bad.decompose()
    # Word tables rarely mark a header row; promote the first row so Markdown tables keep it.
    for table in soup.find_all("table"):
        if table.find("th"):
            continue
        first = table.find("tr")
        if first:
            for td in first.find_all("td", recursive=False):
                td.name = "th"
    # Legacy Daybook / old app checklists: <div class="task-item"><input type=checkbox checked>text</div>
    for item in soup.select(".task-item"):
        box = item.find("input")
        checked = bool(box and box.has_attr("checked"))
        text = item.get_text(" ", strip=True)
        p = soup.new_tag("p")
        p.string = f"[[TASK:{'x' if checked else ' '}]] {text}"
        item.replace_with(p)

    class _Conv(MarkdownConverter):
        def convert_input(self, el, text, *args, **kwargs):  # task list checkboxes
            if (el.get("type") or "").lower() == "checkbox":
                return "[x] " if el.has_attr("checked") else "[ ] "
            return ""

    md = _Conv(heading_style="ATX", bullets="-", strong_em_symbol="*", escape_underscores=False,
               escape_asterisks=False, newline_style="BACKSLASH").convert_soup(soup)
    md = re.sub(r"^\[\[TASK:(x| )\]\] ", lambda m: f"- [{m.group(1)}] ", md, flags=re.M)
    md = re.sub(r"^(\s*)[-*]\s+\[ ?(x| )\]\s*", lambda m: f"{m.group(1)}- [{m.group(2)}] ", md, flags=re.M)
    return tidy_markdown(md)


def tidy_markdown(md: str) -> str:
    md = md.replace("\r\n", "\n").replace("\r", "\n").replace("\u00a0", " ")
    md = re.sub(r"[ \t]+\n", "\n", md)
    md = re.sub(r"\n{3,}", "\n\n", md)
    # Empty headings / bold markers left behind by Word styles.
    md = re.sub(r"^#{1,6}\s*$", "", md, flags=re.M)
    md = re.sub(r"\*\*\s*\*\*", "", md)
    return md.strip() + "\n"


# ---------------------------------------------------------------------------
# Individual formats
# ---------------------------------------------------------------------------
def _docx_core_title(path: Path) -> str | None:
    try:
        with zipfile.ZipFile(path) as z:
            xml = z.read("docProps/core.xml").decode("utf-8", "ignore")
        m = re.search(r"<dc:title>([^<]+)</dc:title>", xml)
        return m.group(1).strip() if m else None
    except (KeyError, zipfile.BadZipFile, OSError):
        return None


def convert_docx(path: Path, filename: str) -> Converted:
    import mammoth

    assets: list[Asset] = []

    def handle_image(image):
        with image.open() as f:
            data = f.read()
        ext = (image.content_type or "image/png").split("/")[-1].replace("jpeg", "jpg").replace("x-emf", "emf")
        idx = len(assets)
        assets.append(Asset(f"image-{idx + 1}.{ext}", data, image.content_type or "image/png"))
        return {"src": f"asset://{idx}", "alt": f"Image {idx + 1}"}

    style_map = "\n".join([
        "p[style-name='Title'] => h1:fresh",
        "p[style-name='Subtitle'] => h2:fresh",
        "p[style-name='Quote'] => blockquote:fresh",
        "p[style-name='Intense Quote'] => blockquote:fresh",
        "r[style-name='Strong'] => strong",
    ])
    with open(path, "rb") as f:
        result = mammoth.convert_to_html(f, convert_image=mammoth.images.img_element(handle_image), style_map=style_map)
    md = html_to_markdown(result.value)
    warnings = [m.message for m in result.messages if getattr(m, "type", "") == "error"][:10]
    title = _docx_core_title(path) or first_heading(md) or title_from_filename(filename)
    return Converted(title=title, markdown=md, assets=assets, warnings=warnings)


def convert_legacy_doc(path: Path, filename: str) -> Converted:
    html = None
    if shutil.which("textutil"):  # macOS built-in
        try:
            res = subprocess.run(["textutil", "-convert", "html", "-stdout", "--", str(path)],
                                 capture_output=True, timeout=120)
            if res.returncode == 0 and res.stdout:
                html = res.stdout.decode("utf-8", "ignore")
        except (OSError, subprocess.TimeoutExpired):
            html = None
    if html is None:
        soffice = shutil.which("soffice") or shutil.which("libreoffice")
        if soffice:
            with tempfile.TemporaryDirectory() as out:
                try:
                    subprocess.run([soffice, "--headless", "--convert-to", "docx", "--outdir", out, str(path)],
                                   capture_output=True, timeout=180)
                    produced = list(Path(out).glob("*.docx"))
                    if produced:
                        conv = convert_docx(produced[0], filename)
                        return conv
                except (OSError, subprocess.TimeoutExpired):
                    pass
    if html is None and path.suffix.lower() == ".odt":
        try:
            with zipfile.ZipFile(path) as z:
                xml = z.read("content.xml").decode("utf-8", "ignore")
            text = re.sub(r"<text:(p|h)[^>]*>", "\n\n", xml)
            text = re.sub(r"<[^>]+>", "", text)
            return Converted(title=title_from_filename(filename), markdown=tidy_markdown(text))
        except (KeyError, zipfile.BadZipFile):
            pass
    if html is None and path.suffix.lower() == ".rtf":
        raw = path.read_text("latin-1", errors="ignore")
        text = re.sub(r"\\par[d]?", "\n", raw)
        text = re.sub(r"\{\\\*[^{}]*\}|\\[a-z]+-?\d* ?|[{}]", "", text)
        return Converted(title=title_from_filename(filename), markdown=tidy_markdown(text),
                         warnings=["Basic RTF conversion (formatting dropped)."])
    if html is None:
        raise ConversionError("This format needs macOS (textutil) or LibreOffice to convert. Save it as .docx or PDF instead.")
    md = html_to_markdown(html)
    return Converted(title=first_heading(md) or title_from_filename(filename), markdown=md)


def _join_pdf_lines(text: str) -> str:
    text = text.replace("\r", "")
    text = re.sub(r"(\w)-\n(\w)", r"\1\2", text)  # hyphenated line breaks
    lines = [ln.rstrip() for ln in text.split("\n")]
    out: list[str] = []
    bullet = re.compile(r"^\s*([•◦▪●○■□‣·\-–*\uf0b7\uf0a7\uf076\uf0d8\u2022\u25aa\u25cf\x7f]|\(?\d{1,3}[.)]|[a-z][.)])\s+")
    for ln in lines:
        s = ln.strip()
        if not s:
            if out and out[-1] != "":
                out.append("")
            continue
        m = bullet.match(s)
        if m:
            marker = m.group(1)
            rest = s[m.end():]
            if re.match(r"\(?\d", marker):
                out.append(f"{re.sub(r'[()]', '', marker).rstrip('.)')}. {rest}")
            else:
                out.append(f"- {rest}")
            continue
        prev = out[-1] if out else ""
        if prev and not re.search(r"[.!?:;]\s*$", prev) and not re.match(r"^(- |\d+\. |#)", prev) \
                and (s[:1].islower() or len(prev) > 60):
            out[-1] = prev + " " + s
        else:
            if prev and not re.match(r"^(- |\d+\. )", prev):
                out.append("")  # separate paragraphs (a single newline would merge them)
            elif prev and re.match(r"^(- |\d+\. )", prev):
                out.append("")
            out.append(s)
    text = "\n".join(out)
    return re.sub(r"\n\n(?=(- |\d+\. ))", lambda m: "\n" if True else m.group(0), text)


def convert_pdf(path: Path, filename: str, ocr=None) -> Converted:
    from pypdf import PdfReader

    try:
        reader = PdfReader(str(path))
    except Exception as exc:
        raise ConversionError("This PDF couldn't be opened. It may be damaged.") from exc
    if reader.is_encrypted:
        try:
            if not reader.decrypt(""):
                raise ConversionError("This PDF is password-protected. Remove the password and import it again.")
        except Exception as exc:
            raise ConversionError("This PDF is password-protected. Remove the password and import it again.") from exc
    pages: list[str] = []
    for page in reader.pages:
        try:
            pages.append(page.extract_text() or "")
        except Exception:
            pages.append("")
    warnings: list[str] = []
    chars = sum(len(p.strip()) for p in pages)
    if chars < 40 * max(1, len(pages)) and ocr is not None:
        # Probably a scan: recognise text in the page images.
        recognised: list[str] = []
        for i, page in enumerate(reader.pages):
            texts = []
            try:
                for img in page.images:
                    t = ocr(img.data, img.name)
                    if t:
                        texts.append(t)
            except Exception:
                continue
            recognised.append("\n".join(texts))
        if sum(len(t) for t in recognised) > chars:
            pages = recognised
            warnings.append("Text was recognised from scanned pages. Check it against the original.")
    body = "\n\n".join(_join_pdf_lines(p) for p in pages if p.strip())
    if not body.strip():
        warnings.append("No text found in this PDF. The original file is attached.")
    meta_title = None
    try:
        meta_title = (reader.metadata.title or "").strip() if reader.metadata else None
    except Exception:
        meta_title = None
    if meta_title and (len(meta_title) < 4 or meta_title.lower().startswith(("microsoft word", "untitled"))):
        meta_title = None
    return Converted(title=meta_title or title_from_filename(filename), markdown=tidy_markdown(body),
                     warnings=warnings, meta={"pages": len(pages)})


def _md_table(rows: list[list], max_rows: int) -> tuple[str, int]:
    rows = [["" if c is None else str(c) for c in r] for r in rows]
    rows = [r for r in rows if any(c.strip() for c in r)]
    if not rows:
        return "", 0
    width = max(len(r) for r in rows)
    # Drop columns that are empty everywhere.
    keep = [i for i in range(width) if any(i < len(r) and r[i].strip() for r in rows)]
    rows = [[(r[i] if i < len(r) else "") for i in keep] for r in rows]

    def cell(v: str) -> str:
        return v.replace("|", "\\|").replace("\n", " ").strip()[:200]

    header, body = rows[0], rows[1:]
    total = len(body)
    body = body[:max_rows]
    lines = ["| " + " | ".join(cell(c) or " " for c in header) + " |", "| " + " | ".join("---" for _ in header) + " |"]
    lines += ["| " + " | ".join(cell(c) for c in r) + " |" for r in body]
    return "\n".join(lines), total


def convert_xlsx(path: Path, filename: str, max_rows: int = 300) -> Converted:
    import openpyxl

    try:
        wb = openpyxl.load_workbook(str(path), read_only=True, data_only=True)
    except Exception as exc:
        raise ConversionError("This workbook couldn't be opened.") from exc
    parts: list[str] = []
    warnings: list[str] = []
    for ws in wb.worksheets:
        rows = []
        for i, row in enumerate(ws.iter_rows(values_only=True)):
            rows.append(list(row))
            if i > max_rows + 1:
                break
        table, total = _md_table(rows, max_rows)
        if not table:
            continue
        parts.append(f"## {ws.title}\n\n{table}")
        try:
            full_rows = (ws.max_row or 0) - 1
        except Exception:
            full_rows = total
        if full_rows > max_rows:
            parts.append(f"*Showing the first {max_rows} of {full_rows} rows. The full workbook is attached.*")
            warnings.append(f"Sheet “{ws.title}” was trimmed to {max_rows} rows.")
    wb.close()
    return Converted(title=title_from_filename(filename), markdown="\n\n".join(parts) + "\n", warnings=warnings)


def _decode(data: bytes) -> str:
    for enc in ("utf-8-sig", "utf-16") if data[:2] in (b"\xff\xfe", b"\xfe\xff") else ("utf-8-sig", "cp1252", "latin-1"):
        try:
            return data.decode(enc)
        except UnicodeDecodeError:
            continue
    return data.decode("utf-8", "replace")


def convert_delimited(path: Path, filename: str, max_rows: int = 300) -> Converted:
    text = _decode(path.read_bytes())
    delim = "\t" if path.suffix.lower() == ".tsv" else ","
    try:
        delim = csv.Sniffer().sniff(text[:5000], delimiters=",;\t|").delimiter
    except csv.Error:
        pass
    rows = list(csv.reader(io.StringIO(text), delimiter=delim))
    table, total = _md_table(rows, max_rows)
    md = table
    if total > max_rows:
        md += f"\n\n*Showing the first {max_rows} of {total} rows. The full file is attached.*"
    return Converted(title=title_from_filename(filename), markdown=md + "\n")


def _shape_text(shape, out: list[str]) -> None:
    from pptx.enum.shapes import MSO_SHAPE_TYPE

    if shape.shape_type == MSO_SHAPE_TYPE.GROUP:
        for s in shape.shapes:
            _shape_text(s, out)
        return
    if getattr(shape, "has_table", False) and shape.has_table:
        rows = [[c.text for c in r.cells] for r in shape.table.rows]
        table, _ = _md_table(rows, 200)
        if table:
            out.append(table)
        return
    if getattr(shape, "has_text_frame", False) and shape.has_text_frame:
        for p in shape.text_frame.paragraphs:
            text = "".join(r.text for r in p.runs).strip()
            if text:
                out.append("  " * min(p.level, 4) + "- " + text)


def convert_pptx(path: Path, filename: str) -> Converted:
    from pptx import Presentation

    try:
        prs = Presentation(str(path))
    except Exception as exc:
        raise ConversionError("This presentation couldn't be opened.") from exc
    parts: list[str] = []
    deck_title = None
    for i, slide in enumerate(prs.slides, 1):
        title_shape = slide.shapes.title
        title = (title_shape.text.strip() if title_shape is not None and title_shape.has_text_frame else "") or f"Slide {i}"
        if i == 1:
            deck_title = title if not title.startswith("Slide ") else None
        lines: list[str] = []
        shapes = sorted(slide.shapes, key=lambda s: ((s.top or 0), (s.left or 0)))
        for shape in shapes:
            if title_shape is not None and shape.shape_id == title_shape.shape_id:
                continue
            _shape_text(shape, lines)
        block = f"## {i}. {title}"
        if lines:
            block += "\n\n" + "\n".join(lines)
        if slide.has_notes_slide:
            notes = slide.notes_slide.notes_text_frame.text.strip() if slide.notes_slide.notes_text_frame else ""
            if notes:
                block += "\n\n> **Speaker notes:** " + notes.replace("\n", "\n> ")
        parts.append(block)
    return Converted(title=deck_title or title_from_filename(filename), markdown="\n\n".join(parts) + "\n",
                     meta={"slides": len(prs.slides)})


def convert_html(path: Path, filename: str) -> Converted:
    raw = _decode(path.read_bytes())
    m = re.search(r"<title[^>]*>(.*?)</title>", raw, re.I | re.S)
    md = html_to_markdown(raw)
    title = (m.group(1).strip() if m else None) or first_heading(md) or title_from_filename(filename)
    return Converted(title=re.sub(r"\s+", " ", title)[:200], markdown=md)


def convert_eml(path: Path, filename: str) -> Converted:
    msg = email.message_from_bytes(path.read_bytes(), policy=email.policy.default)
    subject = str(msg.get("subject") or "").strip() or title_from_filename(filename)
    header = []
    for key in ("From", "To", "Cc", "Date"):
        if msg.get(key):
            header.append(f"- **{key}:** {str(msg.get(key)).strip()}")
    body_part = msg.get_body(preferencelist=("plain", "html"))
    body = ""
    if body_part is not None:
        content = body_part.get_content()
        body = html_to_markdown(content) if body_part.get_content_type() == "text/html" else tidy_markdown(content)
    assets: list[Asset] = []
    for part in msg.iter_attachments():
        name = part.get_filename() or "attachment"
        data = part.get_payload(decode=True) or b""
        if data:
            assets.append(Asset(name, data, part.get_content_type() or "application/octet-stream"))
    md = "\n".join(header) + "\n\n---\n\n" + body
    if assets:
        md += "\n\n## Attachments\n" + "\n".join(f"- [{a.name}](asset://{i})" for i, a in enumerate(assets))
    return Converted(title=subject[:200], markdown=tidy_markdown(md), assets=assets)


def convert_msg(path: Path, filename: str) -> Converted:
    try:
        import extract_msg  # optional dependency
    except ImportError as exc:
        raise ConversionError("Outlook .msg files need the optional 'extract-msg' package (pip install extract-msg), or save the email as .eml.") from exc
    m = extract_msg.Message(str(path))
    try:
        header = [f"- **From:** {m.sender}", f"- **To:** {m.to}", f"- **Date:** {m.date}"]
        body = m.body or ""
        if not body and getattr(m, "htmlBody", None):
            body = html_to_markdown(m.htmlBody.decode("utf-8", "ignore"))
        md = "\n".join(header) + "\n\n---\n\n" + body
        return Converted(title=(m.subject or title_from_filename(filename))[:200], markdown=tidy_markdown(md))
    finally:
        m.close()


def convert_text(path: Path, filename: str, markdown: bool) -> Converted:
    text = _decode(path.read_bytes())
    if markdown:
        title = None
        fm = re.match(r"^---\n(.*?)\n---\n", text, re.S)
        meta = {}
        if fm:
            for line in fm.group(1).splitlines():
                if ":" in line:
                    k, v = line.split(":", 1)
                    meta[k.strip().lower()] = v.strip().strip("'\"")
            text = text[fm.end():]
            title = meta.get("title")
        return Converted(title=title or first_heading(text) or title_from_filename(filename), markdown=tidy_markdown(text), meta=meta)
    return Converted(title=title_from_filename(filename), markdown=tidy_markdown(text))


def convert_code(path: Path, filename: str, lang: str) -> Converted:
    text = _decode(path.read_bytes())
    if lang == "json":
        try:
            text = json.dumps(json.loads(text), indent=2, ensure_ascii=False)
        except ValueError:
            pass
    if len(text) > 200_000:
        text = text[:200_000] + "\n… (truncated — the full file is attached)"
    fence = "````" if "```" in text else "```"
    return Converted(title=filename, markdown=f"{fence}{lang}\n{text.rstrip()}\n{fence}\n")


def _to_displayable_image(data: bytes, ext: str) -> tuple[bytes, str, str]:
    """HEIC/TIFF/BMP aren't viewable in most browsers; convert with macOS `sips` when possible."""
    mime = {".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".gif": "image/gif",
            ".webp": "image/webp"}.get(ext)
    if mime:
        return data, mime, ext
    if shutil.which("sips"):
        with tempfile.TemporaryDirectory() as d:
            src = Path(d) / f"in{ext}"
            dst = Path(d) / "out.jpg"
            src.write_bytes(data)
            try:
                subprocess.run(["sips", "-s", "format", "jpeg", str(src), "--out", str(dst)], capture_output=True, timeout=60)
                if dst.exists():
                    return dst.read_bytes(), "image/jpeg", ".jpg"
            except (OSError, subprocess.TimeoutExpired):
                pass
    return data, "application/octet-stream", ext


def convert_image(path: Path, filename: str, ocr=None) -> Converted:
    data = path.read_bytes()
    ext = path.suffix.lower()
    shown, mime, out_ext = _to_displayable_image(data, ext)
    name = Path(filename).stem + out_ext
    md = f"![{Path(filename).stem}](asset://0)\n"
    warnings = []
    if ocr is not None:
        text = ocr(data, filename)
        if text and text.strip():
            md += "\n## Text in this image\n\n" + tidy_markdown(text)
        else:
            warnings.append("No text was recognised in this image.")
    else:
        warnings.append("Text recognition isn't available. Enable it in Settings → Import.")
    return Converted(title=title_from_filename(filename), markdown=md, assets=[Asset(name, shown, mime)], warnings=warnings)


def convert_audio(path: Path, filename: str, transcribe=None) -> Converted:
    if transcribe is None:
        return Converted(
            title=title_from_filename(filename),
            markdown="*Audio attached. Local transcription isn't set up yet — see Settings → Import.*\n",
            warnings=["Transcription unavailable; the recording was attached."],
        )
    text, info = transcribe(path)
    md = tidy_markdown(text) if text.strip() else "*No speech was recognised.*\n"
    return Converted(title=title_from_filename(filename), markdown=md, meta=info or {})


# ---------------------------------------------------------------------------
# Dispatcher
# ---------------------------------------------------------------------------
def convert(path: Path, filename: str, *, ocr=None, transcribe=None, max_rows: int = 300) -> Converted:
    ext = Path(filename).suffix.lower()
    try:
        if ext in WORD:
            return convert_docx(path, filename)
        if ext in LEGACY_DOC:
            return convert_legacy_doc(path, filename)
        if ext in PDF:
            return convert_pdf(path, filename, ocr=ocr)
        if ext in SLIDES:
            return convert_pptx(path, filename)
        if ext in SHEETS:
            return convert_xlsx(path, filename, max_rows=max_rows)
        if ext in DELIMITED:
            return convert_delimited(path, filename, max_rows=max_rows)
        if ext in HTML:
            return convert_html(path, filename)
        if ext in EMAIL:
            return convert_eml(path, filename)
        if ext in OUTLOOK:
            return convert_msg(path, filename)
        if ext in MARKDOWN:
            return convert_text(path, filename, markdown=True)
        if ext in TEXT:
            return convert_text(path, filename, markdown=False)
        if ext in CODE:
            return convert_code(path, filename, CODE[ext])
        if ext in IMAGES:
            return convert_image(path, filename, ocr=ocr)
        if ext in AUDIO:
            return convert_audio(path, filename, transcribe=transcribe)
    except ConversionError:
        raise
    except zipfile.BadZipFile as exc:
        raise ConversionError("The file looks damaged or isn't really a " + ext + " file.") from exc
    except Exception as exc:  # keep the import queue alive whatever happens
        raise ConversionError(f"Couldn't read this {ext or 'file'}: {exc.__class__.__name__}: {exc}") from exc
    raise ConversionError(f"{ext or 'This file type'} isn't supported yet.")
