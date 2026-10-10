import os
import math
import subprocess
from pathlib import Path
from PIL import Image, ImageDraw, ImageFilter

def draw_twitch_bubble(draw, x, y, w, h, fill, radius=16):
    """
    Draws the iconic Twitch chat bubble silhouette:
    Box with angled bottom-left cutout and speech beak.
    """
    beak_w = w * 0.16
    beak_h = h * 0.18
    beak_left = x + w * 0.14
    
    # Points of the Twitch chat bubble
    # Top-left, top-right, bottom-right, bottom beak right, beak tip, beak left, bottom-left notch
    notch = w * 0.10
    
    poly = [
        (x + radius, y),
        (x + w - radius, y),
        (x + w, y + radius),
        (x + w, y + h - radius),
        (x + w - radius, y + h),
        (beak_left + beak_w, y + h),
        (beak_left, y + h + beak_h),
        (beak_left, y + h),
        (x + notch, y + h),
        (x, y + h - notch),
        (x, y + radius)
    ]
    draw.polygon(poly, fill=fill)
    
    # Two vertical slit eyes
    eye_w = w * 0.08
    eye_h = h * 0.28
    eye_y = y + h * 0.32
    
    eye1_x = x + w * 0.38
    eye2_x = x + w * 0.62
    
    return poly, (eye1_x, eye_y, eye_w, eye_h), (eye2_x, eye_y, eye_w, eye_h)

def create_master_app_icon(size=1024):
    scale = 2
    canvas = size * scale
    img = Image.new("RGBA", (canvas, canvas), (0, 0, 0, 0))
    
    # 1. Base Squircle Dimensions
    pad = int(105 * scale)
    box_size = canvas - 2 * pad
    radius = int(185 * scale)
    
    # 2. Ambient Shadow
    shadow_offset = int(24 * scale)
    shadow_mask = Image.new("RGBA", (canvas, canvas), (0, 0, 0, 0))
    s_draw = ImageDraw.Draw(shadow_mask)
    s_draw.rounded_rectangle((pad, pad + shadow_offset, pad + box_size, pad + box_size + shadow_offset), radius=radius, fill=(0, 0, 0, 120))
    img.alpha_composite(shadow_mask.filter(ImageFilter.GaussianBlur(int(36 * scale))))
    
    # 3. Squircle Background: Deep Slate Dark Canvas with subtle purple tint
    base_sq = Image.new("RGBA", (box_size, box_size), (0, 0, 0, 0))
    for y in range(box_size):
        ratio = y / float(box_size)
        r = int(24 - ratio * 10)
        g = int(24 - ratio * 12)
        b = int(34 - ratio * 14)
        for x in range(box_size):
            base_sq.putpixel((x, y), (r, g, b, 255))
            
    mask = Image.new("L", (box_size, box_size), 0)
    m_draw = ImageDraw.Draw(mask)
    m_draw.rounded_rectangle((0, 0, box_size, box_size), radius=radius, fill=255)
    
    squircle_layer = Image.new("RGBA", (canvas, canvas), (0, 0, 0, 0))
    squircle_layer.paste(base_sq, (pad, pad), mask)
    
    # Border rim
    rim = Image.new("RGBA", (box_size, box_size), (0, 0, 0, 0))
    r_draw = ImageDraw.Draw(rim)
    r_draw.rounded_rectangle((0, 0, box_size, box_size), radius=radius, outline=(255, 255, 255, 30), width=int(3 * scale))
    squircle_layer.paste(rim, (pad, pad), rim)
    img.alpha_composite(squircle_layer)
    
    # 4. Twitch Chat Bubble in Center
    fg = Image.new("RGBA", (canvas, canvas), (0, 0, 0, 0))
    f_draw = ImageDraw.Draw(fg)
    
    tw_w = int(480 * scale)
    tw_h = int(420 * scale)
    tw_x = int((canvas - tw_w) / 2)
    tw_y = int((canvas - tw_h) / 2 - 25 * scale)
    
    # Bubble drop shadow
    b_shadow = Image.new("RGBA", (canvas, canvas), (0, 0, 0, 0))
    bs_draw = ImageDraw.Draw(b_shadow)
    draw_twitch_bubble(bs_draw, tw_x, tw_y + int(14 * scale), tw_w, tw_h, (0, 0, 0, 100), radius=int(30 * scale))
    img.alpha_composite(b_shadow.filter(ImageFilter.GaussianBlur(int(18 * scale))))
    
    # Twitch Bubble (Pure Vibrant Twitch Purple #9146FF)
    _, eye1, eye2 = draw_twitch_bubble(f_draw, tw_x, tw_y, tw_w, tw_h, (145, 70, 255, 255), radius=int(30 * scale))
    
    # Cut out the two eye slits in the Twitch bubble (dark background color)
    f_draw.rectangle([eye1[0], eye1[1], eye1[0] + eye1[2], eye1[1] + eye1[3]], fill=(22, 22, 28, 255))
    f_draw.rectangle([eye2[0], eye2[1], eye2[0] + eye2[2], eye2[1] + eye2[3]], fill=(22, 22, 28, 255))
    
    # 5. Mining Pickaxe Tool in FRONT of the Twitch Bubble
    cx = canvas / 2 + int(15 * scale)
    cy = canvas / 2 + int(15 * scale)
    
    shaft_len = int(500 * scale)
    shaft_w = int(32 * scale)
    angle = math.radians(45)
    
    # Pickaxe shaft coordinates (angled 45 degrees, cutting across)
    x0 = cx - int(shaft_len * 0.48 * math.cos(angle))
    y0 = cy + int(shaft_len * 0.48 * math.sin(angle))
    x1 = cx + int(shaft_len * 0.52 * math.cos(angle))
    y1 = cy - int(shaft_len * 0.52 * math.sin(angle))
    
    # Shadow under pickaxe
    f_draw.line([(x0, y0 + int(14 * scale)), (x1, y1 + int(14 * scale))], fill=(0, 0, 0, 120), width=shaft_w + int(10 * scale))
    
    # Shaft (Warm Wood Handle)
    f_draw.line([(x0, y0), (x1, y1)], fill=(210, 145, 85), width=shaft_w)
    f_draw.line([(x0 - 2, y0 - 2), (x1 - 2, y1 - 2)], fill=(245, 185, 125), width=int(shaft_w * 0.35))
    
    # Handle Grip Wrap / Golden Pommel
    pommel_r = int(20 * scale)
    f_draw.ellipse([(x0 - pommel_r, y0 - pommel_r), (x0 + pommel_r, y0 + pommel_r)], fill=(255, 215, 0))
    
    # Pickaxe Head (Steel/Titanium Blade curved across the top)
    head_cx = x1
    head_cy = y1
    reach = int(260 * scale)
    
    left_tip = (head_cx - int(reach * 0.85), head_cy - int(reach * 0.38))
    right_tip = (head_cx + int(reach * 0.38), head_cy + int(reach * 0.85))
    
    blade_poly = [
        left_tip,
        (head_cx - int(reach * 0.32), head_cy - int(reach * 0.22)),
        (head_cx, head_cy),
        (head_cx + int(reach * 0.22), head_cy + int(reach * 0.32)),
        right_tip,
        (head_cx + int(reach * 0.16), head_cy + int(reach * 0.16)),
        (head_cx - int(12 * scale), head_cy - int(12 * scale)),
        (head_cx - int(reach * 0.38), head_cy - int(reach * 0.12)),
    ]
    
    # Blade shadow
    f_draw.polygon([(p[0] + int(8 * scale), p[1] + int(16 * scale)) for p in blade_poly], fill=(0, 0, 0, 120))
    # Steel blade body
    f_draw.polygon(blade_poly, fill=(230, 238, 248))
    
    # Top blade chrome highlight
    blade_top = [
        left_tip,
        (head_cx - int(reach * 0.32), head_cy - int(reach * 0.22)),
        (head_cx, head_cy),
        (head_cx + int(reach * 0.22), head_cy + int(reach * 0.32)),
        right_tip,
        (head_cx + int(reach * 0.30), head_cy + int(reach * 0.48)),
        (head_cx + int(8 * scale), head_cy + int(8 * scale)),
        (head_cx - int(reach * 0.45), head_cy - int(reach * 0.26)),
    ]
    f_draw.polygon(blade_top, fill=(255, 255, 255))
    
    # Gold Center Ring
    c_ring = int(34 * scale)
    f_draw.ellipse([(head_cx - c_ring, head_cy - c_ring), (head_cx + c_ring, head_cy + c_ring)], fill=(255, 215, 0))
    f_draw.ellipse([(head_cx - int(24 * scale), head_cy - int(24 * scale)), (head_cx + int(24 * scale), head_cy + int(24 * scale))], fill=(255, 240, 140))
    
    # Radiant Sparkle at the pickaxe tip
    def draw_sparkle(sx, sy, r):
        f_draw.polygon([(sx, sy - r), (sx + r * 0.25, sy - r * 0.25), (sx + r, sy), (sx + r * 0.25, sy + r * 0.25),
                        (sx, sy + r), (sx - r * 0.25, sy + r * 0.25), (sx - r, sy), (sx - r * 0.25, sy - r * 0.25)],
                       fill=(255, 255, 255, 255))
    draw_sparkle(left_tip[0] - int(10 * scale), left_tip[1] - int(10 * scale), int(28 * scale))
    draw_sparkle(head_cx + int(60 * scale), head_cy - int(90 * scale), int(16 * scale))
    
    img.alpha_composite(fg)
    
    # Resample down from 2048 to 1024 with Lanczos for anti-aliasing
    return img.resize((size, size), Image.Resampling.LANCZOS)

def create_menubar_template_icon(size=18):
    """
    Creates a black-and-transparent template icon for the macOS menu bar.
    macOS automatically tints template images to match the dark/light menu bar.
    """
    scale = 4
    s = size * scale
    img = Image.new("RGBA", (s, s), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)
    
    # 1. Twitch Bubble Silhouette
    tw_w = s * 0.72
    tw_h = s * 0.65
    tw_x = s * 0.08
    tw_y = s * 0.12
    draw_twitch_bubble(draw, tw_x, tw_y, tw_w, tw_h, (0, 0, 0, 255), radius=int(3 * scale))
    
    # Cut out eye slits
    eye_w = tw_w * 0.09
    eye_h = tw_h * 0.28
    eye_y = tw_y + tw_h * 0.32
    draw.rectangle([tw_x + tw_w * 0.36, eye_y, tw_x + tw_w * 0.36 + eye_w, eye_y + eye_h], fill=(0, 0, 0, 0))
    draw.rectangle([tw_x + tw_w * 0.60, eye_y, tw_x + tw_w * 0.60 + eye_w, eye_y + eye_h], fill=(0, 0, 0, 0))
    
    # 2. Pickaxe angled over front
    cx = s * 0.58
    cy = s * 0.52
    length = s * 0.70
    shaft_w = int(2.5 * scale)
    angle = math.radians(45)
    
    x0 = cx - length * 0.42 * math.cos(angle)
    y0 = cy + length * 0.42 * math.sin(angle)
    x1 = cx + length * 0.58 * math.cos(angle)
    y1 = cy - length * 0.58 * math.sin(angle)
    
    # White outline to make pickaxe stand out over bubble
    draw.line([(x0, y0), (x1, y1)], fill=(0, 0, 0, 0), width=shaft_w + int(2 * scale))
    draw.line([(x0, y0), (x1, y1)], fill=(0, 0, 0, 255), width=shaft_w)
    
    # Pickaxe blade arc
    reach = s * 0.38
    left_tip = (x1 - reach * 0.8, y1 - reach * 0.3)
    right_tip = (x1 + reach * 0.3, y1 + reach * 0.8)
    
    blade = [left_tip, (x1, y1), right_tip, (x1 + 2 * scale, y1 + 2 * scale)]
    draw.line([left_tip, (x1, y1), right_tip], fill=(0, 0, 0, 0), width=int(4 * scale))
    draw.line([left_tip, (x1, y1), right_tip], fill=(0, 0, 0, 255), width=int(2.2 * scale))
    
    # Downsample
    final_1x = img.resize((size, size), Image.Resampling.LANCZOS)
    final_2x = img.resize((size * 2, size * 2), Image.Resampling.LANCZOS)
    return final_1x, final_2x

def generate_iconset(master, output_dir: Path):
    output_dir.mkdir(parents=True, exist_ok=True)
    sizes = [
        ("icon_16x16.png", 16),
        ("icon_16x16@2x.png", 32),
        ("icon_32x32.png", 32),
        ("icon_32x32@2x.png", 64),
        ("icon_128x128.png", 128),
        ("icon_128x128@2x.png", 256),
        ("icon_256x256.png", 256),
        ("icon_256x256@2x.png", 512),
        ("icon_512x512.png", 512),
        ("icon_512x512@2x.png", 1024),
    ]
    for filename, s in sizes:
        resized = master.resize((s, s), Image.Resampling.LANCZOS)
        resized.save(output_dir / filename, "PNG")

if __name__ == "__main__":
    print("==> Generating Twitch + Pickaxe 1024x1024 Master App Icon...")
    master = create_master_app_icon(1024)
    master.save("icons/app_icon_1024.png", "PNG")
    
    iconset_dir = Path("AppIcon.iconset")
    generate_iconset(master, iconset_dir)
    
    print("==> Compiling AppIcon.icns...")
    subprocess.run(["iconutil", "-c", "icns", str(iconset_dir), "-o", "AppIcon.icns"], check=True)
    print("AppIcon.icns generated successfully!")
    
    print("==> Generating macOS Menu Bar Template Icon...")
    mb1x, mb2x = create_menubar_template_icon(18)
    mb1x.save("icons/MenuBarIcon.png", "PNG")
    mb2x.save("icons/MenuBarIcon@2x.png", "PNG")
    print("MenuBarIcon.png and MenuBarIcon@2x.png generated successfully!")
