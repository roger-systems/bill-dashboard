r"""
Generate billicon.ico — a glossy, Windows-7-style calendar-with-dollar-sign icon.

Pure Pillow (already a dependency of the tray app). Run:

    python make_icon.py

Produces, next to this script:
    billicon.ico          multi-resolution icon (16/32/64/128/256)
    billicon_preview.png  256px preview so you can eyeball it

Design: red glossy top bar, white body, rounded corners, spiral binding,
soft drop shadow, and a bold embossed dollar sign. (A pure-white "$" on a
white page is invisible, so the "$" is red with a white highlight edge — it
keeps the red+white theme while staying legible down to 16x16.)
"""
from pathlib import Path
from PIL import Image, ImageDraw, ImageFont, ImageFilter, ImageChops

S = 4                      # supersample factor for crisp anti-aliasing
SIZE = 256 * S             # working canvas (1024)
OUT = Path(__file__).resolve().with_name("billicon.ico")
PREVIEW = Path(__file__).resolve().with_name("billicon_preview.png")

# --- geometry (in the 1024 working space) ---------------------------------- #
# Wide, nearly-square body so the icon fills its taskbar tile edge-to-edge.
X0, Y0, X1, Y1 = 120, 200, 904, 900
RADIUS = 66
BAR_H = 172
BAR_BOTTOM = Y0 + BAR_H


def vgrad(top, bottom):
    """A full-canvas vertical gradient from `top` to `bottom` RGB."""
    base = Image.new("RGB", (1, SIZE))
    for y in range(SIZE):
        t = y / (SIZE - 1)
        base.putpixel((0, y), tuple(
            int(top[i] + (bottom[i] - top[i]) * t) for i in range(3)))
    return base.resize((SIZE, SIZE))


def load_font(size):
    for name in ("seguibl.ttf", "ariblk.ttf", "arialbd.ttf", "segoeuib.ttf"):
        try:
            return ImageFont.truetype(name, size)
        except OSError:
            continue
    return ImageFont.load_default()


def build():
    img = Image.new("RGBA", (SIZE, SIZE), (0, 0, 0, 0))

    # soft drop shadow
    shadow = Image.new("RGBA", (SIZE, SIZE), (0, 0, 0, 0))
    ImageDraw.Draw(shadow).rounded_rectangle(
        (X0, Y0 + 18, X1, Y1 + 18), radius=RADIUS, fill=(0, 0, 0, 115))
    img = Image.alpha_composite(img, shadow.filter(ImageFilter.GaussianBlur(22)))

    # white body (subtle top-to-bottom gradient so it reads as glossy paper)
    body_mask = Image.new("L", (SIZE, SIZE), 0)
    ImageDraw.Draw(body_mask).rounded_rectangle(
        (X0, Y0, X1, Y1), radius=RADIUS, fill=255)
    img.paste(vgrad((255, 255, 255), (226, 228, 234)), (0, 0), body_mask)

    # red top bar (rounded top corners, square bottom edge)
    bar_mask = Image.new("L", (SIZE, SIZE), 0)
    bd = ImageDraw.Draw(bar_mask)
    bd.rounded_rectangle((X0, Y0, X1, BAR_BOTTOM), radius=RADIUS, fill=255)
    bd.rectangle((X0, BAR_BOTTOM - RADIUS, X1, BAR_BOTTOM), fill=255)
    img.paste(vgrad((238, 74, 74), (188, 26, 32)), (0, 0), bar_mask)

    # glossy sheen across the upper half of the red bar
    gloss = Image.new("RGBA", (SIZE, SIZE), (0, 0, 0, 0))
    ImageDraw.Draw(gloss).ellipse(
        (X0 - 60, Y0 - BAR_H, X1 + 60, BAR_BOTTOM - 8), fill=(255, 255, 255, 95))
    gloss.putalpha(ImageChops.multiply(gloss.split()[3], bar_mask))
    img = Image.alpha_composite(img, gloss)

    # spiral binding: two metallic rings straddling the top of the bar
    for fx in (0.34, 0.66):
        cx = int(X0 + (X1 - X0) * fx)
        ring = (cx - 24, Y0 - 36, cx + 24, Y0 + 56)
        rsh = Image.new("RGBA", (SIZE, SIZE), (0, 0, 0, 0))
        ImageDraw.Draw(rsh).rounded_rectangle(
            (ring[0] + 4, ring[1] + 8, ring[2] + 4, ring[3] + 8),
            radius=24, fill=(0, 0, 0, 90))
        img = Image.alpha_composite(img, rsh.filter(ImageFilter.GaussianBlur(6)))
        ring_mask = Image.new("L", (SIZE, SIZE), 0)
        ImageDraw.Draw(ring_mask).rounded_rectangle(ring, radius=24, fill=255)
        img.paste(vgrad((232, 234, 238), (120, 124, 132)), (0, 0), ring_mask)

    # embossed dollar sign on the white body
    font = load_font(470)
    cx = (X0 + X1) // 2
    cy = (BAR_BOTTOM + Y1) // 2 + 8
    overlay = Image.new("RGBA", (SIZE, SIZE), (0, 0, 0, 0))
    od = ImageDraw.Draw(overlay)
    od.text((cx + 7, cy + 8), "$", font=font, anchor="mm", fill=(120, 12, 16, 130))   # shadow
    od.text((cx - 6, cy - 6), "$", font=font, anchor="mm", fill=(255, 220, 220, 230))  # highlight
    od.text((cx, cy), "$", font=font, anchor="mm", fill=(206, 32, 38, 255))            # face
    img = Image.alpha_composite(img, overlay)

    return img  # full-res (1024) RGBA; main() crops, scales, and downsizes


def grow_and_center(img, fill=0.97, alpha_threshold=45):
    """Scale the *solid* artwork to `fill` of the frame and re-center it.

    The bounding box is measured from pixels above `alpha_threshold` so the faint,
    blurred drop-shadow halo doesn't count — otherwise that halo inflates the box
    and the visible calendar stays small next to other taskbar icons."""
    frame = img.size[0]
    alpha = img.split()[3]
    solid = alpha.point(lambda a: 255 if a >= alpha_threshold else 0)
    bbox = solid.getbbox() or img.getbbox()
    if not bbox:
        return img
    content = img.crop(bbox)
    cw, ch = content.size
    scale = round(frame * fill) / max(cw, ch)
    new = (max(1, round(cw * scale)), max(1, round(ch * scale)))
    content = content.resize(new, Image.LANCZOS)
    out = Image.new("RGBA", (frame, frame), (0, 0, 0, 0))
    out.paste(content, ((frame - new[0]) // 2, (frame - new[1]) // 2), content)
    print(f"solid content {cw}x{ch} -> scaled x{scale:.2f}, fills {fill:.0%} of frame")
    return out


def main():
    art = grow_and_center(build(), fill=0.99)  # framed 1024px master
    # Include the sizes Windows actually requests (24 + 48 for taskbar at 100% /
    # 200% scaling), each downscaled straight from the master for crispness.
    sizes = [16, 24, 32, 48, 64, 128, 256]
    frames = []
    for s in sizes:
        f = art.resize((s, s), Image.LANCZOS)
        if s <= 48:  # gentle sharpen so small icons don't look soft
            f = f.filter(ImageFilter.UnsharpMask(radius=1.2, percent=90, threshold=0))
        frames.append(f)
    frames[-1].save(OUT, format="ICO", sizes=[(s, s) for s in sizes],
                    append_images=frames[:-1])
    frames[-1].save(PREVIEW, format="PNG")  # 256px preview
    frames[sizes.index(32)].resize((256, 256), Image.NEAREST).save(
        PREVIEW.with_name("billicon_32x.png"))  # magnified 32px to judge crispness
    print(f"Wrote {OUT}  (sizes: {sizes})")
    print(f"Wrote {PREVIEW} and billicon_32x.png")


if __name__ == "__main__":
    main()
