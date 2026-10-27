"""Bildverarbeitung: Speichern, Vorschauen erzeugen, EXIF auslesen."""
import hashlib
import json
import logging
import shutil
import uuid
from fractions import Fraction
from pathlib import Path

from PIL import ExifTags, Image, ImageDraw, ImageFont, ImageOps

from . import config

log = logging.getLogger("gallery")

Image.MAX_IMAGE_PIXELS = 300_000_000  # grosse Mittelformat-Dateien erlauben

SIZES = {"thumb": config.THUMB_SIZE, "preview": config.PREVIEW_SIZE}

_TAGS = {v: k for k, v in ExifTags.TAGS.items()}
_EXIF_IFD = 0x8769


def original_path(album_id, stored_name):
    return config.ORIGINALS_DIR / str(album_id) / stored_name


def derived_path(album_id, stored_name, size):
    return config.DERIVED_DIR / str(album_id) / "{}_{}.jpg".format(Path(stored_name).stem, size)


def _fmt_exposure(v):
    v = float(v)
    if v <= 0:
        return None
    if v >= 1:
        return "{:g} s".format(round(v, 1))
    return "1/{} s".format(round(1 / v))


def _fmt_ev(v):
    """Belichtungskorrektur als Drittelstufen, z. B. -0.333 -> "-1/3 EV", 1.667 -> "+1 2/3 EV"."""
    f = Fraction(v).limit_denominator(3)
    sign = "+" if f > 0 else "-"
    whole, rest = divmod(abs(f), 1)
    parts = []
    if whole:
        parts.append(str(int(whole)))
    if rest:
        parts.append("{}/{}".format(rest.numerator, rest.denominator))
    return "{}{} EV".format(sign, " ".join(parts) or "0")


def _num(v):
    try:
        return float(v)
    except (TypeError, ValueError, ZeroDivisionError):
        return None


def extract_exif(img):
    """Liefert ein Dict mit lesbaren Aufnahmedaten, wie im DPreview-Infopanel."""
    out = {}
    try:
        exif = img.getexif()
    except Exception:
        return out, None
    data = dict(exif)
    try:
        data.update(exif.get_ifd(_EXIF_IFD))
    except Exception:
        pass

    def tag(name):
        return data.get(_TAGS.get(name))

    make = (tag("Make") or "").strip().strip("\x00")
    model = (tag("Model") or "").strip().strip("\x00")
    if model:
        out["camera"] = model if make and model.lower().startswith(make.split()[0].lower()) else "{} {}".format(make, model).strip()
    lens = tag("LensModel")
    if lens:
        out["lens"] = str(lens).strip().strip("\x00")
    fl = _num(tag("FocalLength"))
    if fl:
        out["focal_length"] = "{:g} mm".format(round(fl, 1))
    fl35 = tag("FocalLengthIn35mmFilm")
    if fl35:
        out["focal_length_35"] = "{} mm".format(fl35)
    fn = _num(tag("FNumber"))
    if fn:
        out["aperture"] = "f/{:g}".format(round(fn, 1))
    et = _num(tag("ExposureTime"))
    if et:
        out["exposure"] = _fmt_exposure(et)
    iso = tag("ISOSpeedRatings") or tag("PhotographicSensitivity")
    if isinstance(iso, (tuple, list)):
        iso = iso[0] if iso else None
    if iso:
        out["iso"] = "ISO {}".format(iso)
    ev = _num(tag("ExposureBiasValue"))
    if ev is not None and ev != 0:
        out["exposure_bias"] = _fmt_ev(ev)
    flash = tag("Flash")
    if flash is not None:
        out["flash"] = "Ja" if int(flash) & 1 else "Nein"
    artist = tag("Artist")
    if artist:
        out["artist"] = str(artist).strip().strip("\x00")
    copyright_ = tag("Copyright")
    if copyright_:
        out["copyright"] = str(copyright_).strip().strip("\x00")

    taken = tag("DateTimeOriginal") or tag("DateTime")
    taken_iso = None
    if taken:
        try:
            d, t = str(taken).strip().split(" ")
            taken_iso = "{} {}".format(d.replace(":", "-"), t)
            out["taken"] = "{}.{}.{} {}".format(d[8:10], d[5:7], d[0:4], t[:5])
        except ValueError:
            pass
    return out, taken_iso


def _render(img, size, dest):
    im = img.copy()
    im.thumbnail((size, size), Image.LANCZOS)
    if im.mode not in ("RGB", "L"):
        bg = Image.new("RGB", im.size, (255, 255, 255))
        if im.mode in ("RGBA", "LA", "P"):
            im = im.convert("RGBA")
            bg.paste(im, mask=im.split()[-1])
            im = bg
        else:
            im = im.convert("RGB")
    dest.parent.mkdir(parents=True, exist_ok=True)
    im.save(dest, "JPEG", quality=config.JPEG_QUALITY, optimize=True, progressive=True)


def ingest(album_id, upload_file, original_name):
    """Speichert eine hochgeladene Datei und erzeugt Vorschaubilder.

    Gibt ein Dict mit den Datenbankfeldern zurück oder wirft ValueError.
    """
    ext = Path(original_name).suffix.lower()
    if ext not in config.ALLOWED_EXT:
        raise ValueError("Dateityp {} wird nicht unterstützt".format(ext or "?"))
    stored = uuid.uuid4().hex + (".jpg" if ext == ".jpeg" else ext)
    dest = original_path(album_id, stored)
    dest.parent.mkdir(parents=True, exist_ok=True)
    with open(dest, "wb") as fh:
        shutil.copyfileobj(upload_file, fh, length=1024 * 1024)
    try:
        with Image.open(dest) as img:
            img.load()
            try:
                exif, taken = extract_exif(img)
            except Exception as exc:  # fehlerhafte Metadaten dürfen den Upload nicht verhindern
                log.warning("EXIF von %s nicht lesbar: %s", original_name, exc)
                exif, taken = {}, None
            img = ImageOps.exif_transpose(img)
            width, height = img.size
            for name, size in SIZES.items():
                _render(img, size, derived_path(album_id, stored, name))
    except Exception as exc:
        dest.unlink(missing_ok=True)
        delete_files(album_id, stored)
        raise ValueError("Bild konnte nicht gelesen werden: {}".format(exc))
    return {
        "original_name": Path(original_name).name[:200],
        "stored_name": stored,
        "width": width,
        "height": height,
        "size_bytes": dest.stat().st_size,
        "exif_json": json.dumps(exif, ensure_ascii=False),
        "taken_at": taken,
    }


def store_raw(album_id, upload_file, original_name):
    """Speichert eine RAW-Datei unverändert. Gibt (gespeicherter Name, Grösse) zurück."""
    ext = Path(original_name).suffix.lower()
    if ext not in config.RAW_EXT:
        raise ValueError("Kein unterstütztes RAW-Format: {}".format(ext or "?"))
    stored = uuid.uuid4().hex + ext
    dest = original_path(album_id, stored)
    dest.parent.mkdir(parents=True, exist_ok=True)
    with open(dest, "wb") as fh:
        shutil.copyfileobj(upload_file, fh, length=1024 * 1024)
    size = dest.stat().st_size
    if not size:
        dest.unlink(missing_ok=True)
        raise ValueError("Die RAW-Datei ist leer.")
    return stored, size


def delete_raw(album_id, raw_stored):
    if raw_stored:
        original_path(album_id, raw_stored).unlink(missing_ok=True)


def delete_files(album_id, stored_name):
    original_path(album_id, stored_name).unlink(missing_ok=True)
    folder = config.DERIVED_DIR / str(album_id)
    for p in folder.glob(Path(stored_name).stem + "_*.jpg"):  # Vorschauen inkl. Wasserzeichen-Varianten
        p.unlink(missing_ok=True)


# --------------------------------------------------------------------------- #
# Wasserzeichen
# --------------------------------------------------------------------------- #

LOGO_DIR = config.DATA_DIR / "branding"


def logo_path(settings):
    name = settings.get("logo", "")
    return LOGO_DIR / name if name else None


def watermark_key(settings):
    """Kurzer Hash der Wasserzeichen-Einstellungen; ändert sich etwas, entstehen neue Dateien."""
    lp = logo_path(settings)
    parts = [settings.get("watermark_text", ""), settings.get("watermark_opacity", ""),
             settings.get("watermark_logo", ""),
             str(lp.stat().st_mtime) if lp and lp.exists() else ""]
    return hashlib.sha1("|".join(parts).encode()).hexdigest()[:8]


def _font(size):
    try:
        return ImageFont.load_default(size=size)
    except TypeError:  # sehr alte Pillow-Versionen
        return ImageFont.load_default()


def apply_watermark(im, settings):
    im = im.convert("RGBA")
    w, h = im.size
    opacity = max(5, min(90, int(settings.get("watermark_opacity") or 35)))
    alpha = int(255 * opacity / 100)
    layer = Image.new("RGBA", im.size, (0, 0, 0, 0))
    lp = logo_path(settings)
    if settings.get("watermark_logo") == "1" and lp and lp.exists():
        with Image.open(lp) as logo:
            logo = logo.convert("RGBA")
            logo.thumbnail((int(w * 0.35), int(h * 0.35)), Image.LANCZOS)
            a = logo.split()[-1].point(lambda v: v * opacity // 100)
            logo.putalpha(a)
            layer.paste(logo, ((w - logo.width) // 2, (h - logo.height) // 2), logo)
    else:
        text = settings.get("watermark_text") or settings.get("site_name") or "Vorschau"
        size = max(14, int(min(w, h) / 14))
        font = _font(size)
        box = ImageDraw.Draw(layer).textbbox((0, 0), text, font=font)
        tw, th = box[2] - box[0] + size, box[3] - box[1] + size
        tile = Image.new("RGBA", (tw, th), (0, 0, 0, 0))
        d = ImageDraw.Draw(tile)
        d.text((size // 2 + 1, size // 2 + 1), text, font=font, fill=(0, 0, 0, alpha // 2))
        d.text((size // 2, size // 2), text, font=font, fill=(255, 255, 255, alpha))
        tile = tile.rotate(30, expand=True, resample=Image.BICUBIC)
        step_x, step_y = int(tile.width * 1.4), int(tile.height * 1.6)
        for row, y in enumerate(range(-tile.height, h + tile.height, step_y)):
            offset = (step_x // 2) * (row % 2)
            for x in range(-tile.width + offset, w + tile.width, step_x):
                layer.alpha_composite(tile, (max(x, 0), max(y, 0)), (max(-x, 0), max(-y, 0)))
    return Image.alpha_composite(im, layer).convert("RGB")


def watermarked_path(album_id, stored_name, size, settings):
    """Liefert (und erzeugt bei Bedarf) die Vorschau mit Wasserzeichen."""
    dest = config.DERIVED_DIR / str(album_id) / "{}_{}_wm{}.jpg".format(
        Path(stored_name).stem, size, watermark_key(settings))
    if not dest.exists():
        with Image.open(derived_path(album_id, stored_name, size)) as src:
            out = apply_watermark(src, settings)
        tmp = dest.with_name("{}.{}.tmp".format(dest.stem, uuid.uuid4().hex[:6]))
        out.save(tmp, "JPEG", quality=config.JPEG_QUALITY, optimize=True, progressive=True)
        tmp.replace(dest)
    return dest


def delete_album_files(album_id):
    shutil.rmtree(config.ORIGINALS_DIR / str(album_id), ignore_errors=True)
    shutil.rmtree(config.DERIVED_DIR / str(album_id), ignore_errors=True)


def human_size(n):
    for unit in ("B", "KB", "MB", "GB"):
        if n < 1024 or unit == "GB":
            return ("{:.0f} {}" if unit == "B" else "{:.1f} {}").format(n, unit)
        n /= 1024.0
