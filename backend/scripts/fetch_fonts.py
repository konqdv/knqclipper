"""Download OFL-licensed caption fonts from Fontsource (npm registry) and convert them to TTF.

libass needs TTF/OTF files; Fontsource ships WOFF2. Each weight gets a unique family name
(e.g. "Montserrat ExtraBold") so caption styles can select it unambiguously.

Usage: python scripts/fetch_fonts.py [output_dir]
"""

from __future__ import annotations

import io
import json
import sys
import tarfile
import urllib.request
from pathlib import Path

from fontTools.ttLib import TTFont

FONTS = [
    # (npm package, file stem, weight, family name exposed to captions)
    ("@fontsource/montserrat", "montserrat", 900, "Montserrat Black"),
    ("@fontsource/montserrat", "montserrat", 800, "Montserrat ExtraBold"),
    ("@fontsource/anton", "anton", 400, "Anton"),
    ("@fontsource/bebas-neue", "bebas-neue", 400, "Bebas Neue"),
    ("@fontsource/poppins", "poppins", 800, "Poppins ExtraBold"),
    ("@fontsource/inter", "inter", 600, "Inter SemiBold"),
    ("@fontsource/inter", "inter", 800, "Inter ExtraBold"),
    ("@fontsource/bangers", "bangers", 400, "Bangers"),
]
REGISTRY = "https://registry.npmjs.org"


def tarball(pkg: str) -> tarfile.TarFile:
    meta = json.load(urllib.request.urlopen(f"{REGISTRY}/{pkg.replace('/', '%2F')}"))
    url = meta["versions"][meta["dist-tags"]["latest"]]["dist"]["tarball"]
    return tarfile.open(fileobj=io.BytesIO(urllib.request.urlopen(url).read()), mode="r:gz")


def rename(font: TTFont, family: str) -> None:
    ps = family.replace(" ", "")
    for rec in list(font["name"].names):
        if rec.nameID in (16, 17, 21, 22):
            font["name"].removeNames(nameID=rec.nameID)
    for nid, val in ((1, family), (2, "Regular"), (4, family), (6, ps)):
        font["name"].setName(val, nid, 3, 1, 0x409)
        font["name"].setName(val, nid, 1, 0, 0)
    font["OS/2"].fsSelection = (font["OS/2"].fsSelection & ~0b1100001) | 0b1000000  # REGULAR
    font["head"].macStyle = 0


def main(out: Path) -> None:
    out.mkdir(parents=True, exist_ok=True)
    cache: dict[str, tarfile.TarFile] = {}
    for pkg, stem, weight, family in FONTS:
        tf = cache.setdefault(pkg, tarball(pkg))
        member = f"package/files/{stem}-latin-{weight}-normal.woff2"
        data = tf.extractfile(member).read()  # type: ignore[union-attr]
        font = TTFont(io.BytesIO(data))
        font.flavor = None
        rename(font, family)
        dest = out / f"{family.replace(' ', '')}.ttf"
        font.save(dest)
        print(f"{family:24} -> {dest.name} ({dest.stat().st_size // 1024} KB)")
        try:
            lic = tf.extractfile("package/LICENSE")
            if lic:
                (out / f"LICENSE-{stem}.txt").write_bytes(lic.read())
        except KeyError:
            pass


if __name__ == "__main__":
    main(Path(sys.argv[1]) if len(sys.argv) > 1 else Path(__file__).resolve().parents[1] / "clipper" / "assets" / "fonts")
