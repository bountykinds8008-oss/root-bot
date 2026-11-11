"""アプリのアイコンを作る（オリジナルのドット絵ボット「ビット」）。 python tools/make_icons.py"""
from pathlib import Path
from PIL import Image, ImageDraw, ImageFilter

OUT = Path(__file__).resolve().parent.parent / "dashboard" / "icons"
S = 1024

def base(pad):
    im = Image.new("RGB", (S, S), "#0a0612")
    # 背景：中心が明るい紫のグラデーション
    bg = Image.new("RGB", (S, S)); d = ImageDraw.Draw(bg)
    for r in range(S, 0, -8):
        t = r / S
        c = (int(10 + 48 * (1 - t)), int(6 + 14 * (1 - t)), int(18 + 70 * (1 - t)))
        d.ellipse([S/2 - r, S*0.42 - r, S/2 + r, S*0.42 + r], fill=c)
    im.paste(bg)
    d = ImageDraw.Draw(im)
    # 床のネオングリッド
    hz = int(S * 0.74)
    for i in range(-9, 10):
        d.line([(S/2 + i*40, hz), (S/2 + i*170, S)], fill=(120, 40, 110), width=3)
    for k in range(5):
        y = hz + int((S - hz) * ((k + 1) / 5) ** 1.7)
        d.line([(0, y), (S, y)], fill=(120, 40, 110), width=3)
    # ロボット（ドット単位）
    u = int((S - 2 * pad) / 15)
    cx, top = S // 2, int(S * 0.2 + pad * 0.3)
    glow = Image.new("RGBA", (S, S), (0, 0, 0, 0)); g = ImageDraw.Draw(glow)
    orange = (255, 159, 46)
    # アンテナ
    d.rectangle([cx - u*0.4, top, cx + u*0.4, top + u*2.2], fill=(143, 99, 55))
    g.rectangle([cx - u*1.1, top - u*1.6, cx + u*1.1, top + u*0.1], fill=orange + (255,))
    # 頭（外枠＋画面）
    hx0, hy0, hx1, hy1 = cx - u*6.2, top + u*2.2, cx + u*6.2, top + u*11.2
    d.rounded_rectangle([hx0, hy0, hx1, hy1], radius=u*0.8, fill=(143, 99, 55))
    d.rounded_rectangle([hx0 + u*0.7, hy0 + u*0.7, hx1 - u*0.7, hy1 - u*0.7], radius=u*0.4, fill=(7, 4, 13))
    # 顔のドット（目と笑顔）
    face = ["00000000", "01100110", "01100110", "00000000", "01000010", "00111100"]
    fx, fy = cx - u*4, hy0 + u*1.6
    for r, row in enumerate(face):
        for c, v in enumerate(row):
            if v == "1":
                g.rectangle([fx + c*u + u*.06, fy + r*u + u*.06, fx + (c+1)*u - u*.06, fy + (r+1)*u - u*.06], fill=orange + (255,))
    # 肩
    d.rectangle([cx - u*4.5, hy1, cx + u*4.5, hy1 + u*2.4], fill=(107, 74, 42))
    d.rectangle([cx - u*4.5, hy1 + u*0.8, cx + u*4.5, hy1 + u*1.4], fill=orange)
    # 光のにじみ
    blur = glow.filter(ImageFilter.GaussianBlur(u * 0.9))
    im = Image.alpha_composite(im.convert("RGBA"), blur)
    im = Image.alpha_composite(im, blur)
    im = Image.alpha_composite(im, glow)
    return im.convert("RGB")

OUT.mkdir(parents=True, exist_ok=True)
std, mask = base(120), base(230)   # maskable は安全域を広めに
for size, img, name in [(512, std, "icon-512.png"), (192, std, "icon-192.png"), (180, std, "apple-touch-icon.png"),
                        (512, mask, "icon-maskable-512.png"), (32, std, "favicon-32.png")]:
    img.resize((size, size), Image.LANCZOS).save(OUT / name, optimize=True)
std.save(OUT / "icon-1024.png", optimize=True)
print("ok", sorted(p.name for p in OUT.iterdir()))
