"""Génère les icônes de l'appli (web/icons/) à partir du logo : python tools/make_icons.py [design/logo.webp]

Le logo est dessiné sur fond blanc avec beaucoup de marge : on repère la zone du dessin,
puis on la recentre avec les marges attendues par iOS (icône carrée, coins arrondis par le
système) et par Android (icône "maskable" : le dessin doit tenir dans le cercle central de 80 %).
Utilise le Chromium de Playwright, déjà installé pour la recherche.
"""
import base64
import json
import sys
from pathlib import Path

from playwright.sync_api import sync_playwright

ROOT = Path(__file__).resolve().parent.parent
SRC = Path(sys.argv[1]) if len(sys.argv) > 1 else ROOT / "design" / "logo.webp"
OUT = ROOT / "web" / "icons"

# nom -> (taille en px, largeur du dessin en fraction de l'icône)
ICONS = {
    "apple-touch-icon.png": (180, 0.84),
    "icon-192.png": (192, 0.84),
    "icon-512.png": (512, 0.84),
    "icon-maskable-512.png": (512, 0.62),  # dans le cercle de sécurité d'Android
}

PAGE = """<html><body style="margin:0">
<canvas id="c"></canvas>
<script>
async function bbox(src) {
  const img = new Image(); img.src = src; await img.decode();
  const c = document.getElementById("c"); c.width = img.width; c.height = img.height;
  const g = c.getContext("2d"); g.drawImage(img, 0, 0);
  const d = g.getImageData(0, 0, img.width, img.height).data;
  let x0 = img.width, y0 = img.height, x1 = 0, y1 = 0;
  for (let y = 0; y < img.height; y++) for (let x = 0; x < img.width; x++) {
    const i = (y * img.width + x) * 4;
    if (d[i + 3] > 10 && Math.min(d[i], d[i + 1], d[i + 2]) < 240) {
      if (x < x0) x0 = x; if (x > x1) x1 = x; if (y < y0) y0 = y; if (y > y1) y1 = y;
    }
  }
  return { w: img.width, h: img.height, x0, y0, x1, y1 };
}
function sample(points) {
  const g = document.getElementById("c").getContext("2d");
  return points.map(([x, y]) => { const p = g.getImageData(x, y, 1, 1).data;
    return "#" + [p[0], p[1], p[2]].map(v => v.toString(16).padStart(2, "0")).join(""); });
}
</script></body></html>"""


def main() -> None:
    src = f"data:image/{SRC.suffix.lstrip('.')};base64,{base64.b64encode(SRC.read_bytes()).decode()}"
    with sync_playwright() as p:
        browser = p.chromium.launch()
        page = browser.new_page()
        page.set_content(PAGE)
        box = page.evaluate("bbox", src)
        cw, ch = box["x1"] - box["x0"] + 1, box["y1"] - box["y0"] + 1
        print(f"logo {box['w']}x{box['h']}, dessin {cw}x{ch} en ({box['x0']}, {box['y0']})")
        if len(sys.argv) > 2 and sys.argv[2] == "--sample":
            pts = json.loads(sys.argv[3])
            print(dict(zip(map(str, pts), page.evaluate("sample", pts), strict=True)))
        for name, (size, frac) in ICONS.items():
            scale = size * frac / max(cw, ch * 1.0)
            w, h = cw * scale, ch * scale
            icon = browser.new_page(viewport={"width": size, "height": size}, device_scale_factor=1)
            icon.set_content(
                f'<html><body style="margin:0;width:{size}px;height:{size}px;background:#fff;'
                f'overflow:hidden;position:relative">'
                f'<div style="position:absolute;left:{(size - w) / 2}px;top:{(size - h) / 2}px;'
                f'width:{w}px;height:{h}px;background:url({src}) no-repeat;'
                f'background-size:{box["w"] * scale}px {box["h"] * scale}px;'
                f'background-position:{-box["x0"] * scale}px {-box["y0"] * scale}px"></div></body></html>')
            icon.wait_for_timeout(100)
            icon.screenshot(path=str(OUT / name))
            icon.close()
            print(f"  {name} ({size} px)")
        browser.close()


if __name__ == "__main__":
    main()
