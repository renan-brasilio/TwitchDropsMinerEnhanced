import os
import math
import subprocess
from pathlib import Path
from PIL import Image, ImageDraw, ImageFilter

def create_master_icon(size=1024):
    # Render at 2x (2048x2048) for ultra-sharp supersampling
    scale = 2
    canvas_size = size * scale
    img = Image.new("RGBA", (canvas_size, canvas_size), (0, 0, 0, 0))
    
    # 1. Base Squircle Dimensions (Apple grid: ~824px at 1024 size)
    pad = int(100 * scale)
    box_size = canvas_size - 2 * pad
    radius = int(185 * scale)
    box = (pad, pad, pad + box_size, pad + box_size)
    
    # 2. Ambient Drop Shadow
    shadow_offset = int(24 * scale)
    shadow_blur = int(36 * scale)
    shadow_mask = Image.new("RGBA", (canvas_size, canvas_size), (0, 0, 0, 0))
    shadow_draw = ImageDraw.Draw(shadow_mask)
    shadow_box = (pad, pad + shadow_offset, pad + box_size, pad + box_size + shadow_offset)
    shadow_draw.rounded_rectangle(shadow_box, radius=radius, fill=(0, 0, 0, 110))
    shadow_blurred = shadow_mask.filter(ImageFilter.GaussianBlur(shadow_blur))
    img.alpha_composite(shadow_blurred)
    
    # 3. Squircle Background Gradient (Twitch Royal Purple to Deep Cosmic Indigo)
    gradient_img = Image.new("RGBA", (box_size, box_size), (0, 0, 0, 0))
    for y in range(box_size):
        ratio = y / float(box_size)
        # Gradient: Top-left bright vibrant violet (#9b51e0 / #9146ff) to bottom-right deep indigo (#34136b)
        r = int(155 * (1 - ratio * 0.65))
        g = int(75 * (1 - ratio * 0.70) + 15)
        b = int(255 * (1 - ratio * 0.50))
        for x in range(box_size):
            x_ratio = x / float(box_size)
            # Add subtle radial lighting from top-left
            dist = math.sqrt((x / box_size - 0.3)**2 + (y / box_size - 0.2)**2)
            light = max(0.0, 1.0 - dist * 1.1) * 45
            cr = min(255, int(r + light * 0.8))
            cg = min(255, int(g + light * 0.6))
            cb = min(255, int(b + light))
            gradient_img.putpixel((x, y), (cr, cg, cb, 255))
            
    # Apply rounded rectangle mask to gradient
    mask = Image.new("L", (box_size, box_size), 0)
    mask_draw = ImageDraw.Draw(mask)
    mask_draw.rounded_rectangle((0, 0, box_size, box_size), radius=radius, fill=255)
    
    squircle = Image.new("RGBA", (canvas_size, canvas_size), (0, 0, 0, 0))
    squircle.paste(gradient_img, (pad, pad), mask)
    
    # 4. Top Rim Highlight (Subtle Apple glass bevel)
    rim_mask = Image.new("RGBA", (box_size, box_size), (0, 0, 0, 0))
    rim_draw = ImageDraw.Draw(rim_mask)
    rim_draw.rounded_rectangle((0, 0, box_size, box_size), radius=radius, outline=(255, 255, 255, 70), width=int(3 * scale))
    squircle.paste(rim_mask, (pad, pad), rim_mask)
    
    img.alpha_composite(squircle)
    
    # 5. Foreground Graphic: Modern Stylized Pickaxe & Loot Diamond Gem
    fg = Image.new("RGBA", (canvas_size, canvas_size), (0, 0, 0, 0))
    draw = ImageDraw.Draw(fg)
    
    cx = canvas_size / 2
    cy = canvas_size / 2
    
    # Pickaxe shaft (angled at 45 degrees)
    shaft_len = int(380 * scale)
    shaft_w = int(28 * scale)
    angle = math.radians(45)
    
    # Shaft shadow
    s_x0 = cx - int(shaft_len * 0.45 * math.cos(angle))
    s_y0 = cy + int(shaft_len * 0.45 * math.sin(angle)) + int(12 * scale)
    s_x1 = cx + int(shaft_len * 0.55 * math.cos(angle))
    s_y1 = cy - int(shaft_len * 0.55 * math.sin(angle)) + int(12 * scale)
    draw.line([(s_x0, s_y0), (s_x1, s_y1)], fill=(0, 0, 0, 80), width=shaft_w + int(8 * scale))
    
    # Wooden Shaft
    x0 = cx - int(shaft_len * 0.45 * math.cos(angle))
    y0 = cy + int(shaft_len * 0.45 * math.sin(angle))
    x1 = cx + int(shaft_len * 0.55 * math.cos(angle))
    y1 = cy - int(shaft_len * 0.55 * math.sin(angle))
    draw.line([(x0, y0), (x1, y1)], fill=(215, 160, 100), width=shaft_w)
    # Shaft wood grain highlight
    draw.line([(x0 - 2, y0 - 2), (x1 - 2, y1 - 2)], fill=(245, 195, 140), width=int(shaft_w * 0.35))
    
    # Shaft pommel / cap (gold ring)
    cap_r = int(18 * scale)
    draw.ellipse([(x0 - cap_r, y0 - cap_r), (x0 + cap_r, y0 + cap_r)], fill=(255, 215, 0))
    
    # Pickaxe Head (Titanium Metallic Arc)
    head_cx = x1
    head_cy = y1
    
    # Curved pickaxe blade
    # Arc points
    blade_reach = int(240 * scale)
    left_tip = (head_cx - int(blade_reach * 0.8), head_cy - int(blade_reach * 0.35))
    right_tip = (head_cx + int(blade_reach * 0.35), head_cy + int(blade_reach * 0.8))
    
    blade_poly = [
        left_tip,
        (head_cx - int(blade_reach * 0.3), head_cy - int(blade_reach * 0.2)),
        (head_cx, head_cy),
        (head_cx + int(blade_reach * 0.2), head_cy + int(blade_reach * 0.3)),
        right_tip,
        (head_cx + int(blade_reach * 0.15), head_cy + int(blade_reach * 0.15)),
        (head_cx - int(10 * scale), head_cy - int(10 * scale)),
        (head_cx - int(blade_reach * 0.35), head_cy - int(blade_reach * 0.1)),
    ]
    
    # Blade shadow
    blade_shadow = [(p[0] + int(6 * scale), p[1] + int(14 * scale)) for p in blade_poly]
    draw.polygon(blade_shadow, fill=(0, 0, 0, 90))
    
    # Steel blade base
    draw.polygon(blade_poly, fill=(210, 225, 240))
    # Titanium blade edge
    blade_upper = [
        left_tip,
        (head_cx - int(blade_reach * 0.3), head_cy - int(blade_reach * 0.2)),
        (head_cx, head_cy),
        (head_cx + int(blade_reach * 0.2), head_cy + int(blade_reach * 0.3)),
        right_tip,
        (head_cx + int(blade_reach * 0.28), head_cy + int(blade_reach * 0.45)),
        (head_cx + int(8 * scale), head_cy + int(8 * scale)),
        (head_cx - int(blade_reach * 0.4), head_cy - int(blade_reach * 0.25)),
    ]
    draw.polygon(blade_upper, fill=(245, 250, 255))
    
    # Gold collar ring holding the pickaxe head
    collar_box = [
        (head_cx - int(32 * scale), head_cy - int(32 * scale)),
        (head_cx + int(32 * scale), head_cy + int(32 * scale))
    ]
    draw.ellipse(collar_box, fill=(255, 215, 0))
    draw.ellipse([
        (head_cx - int(24 * scale), head_cy - int(24 * scale)),
        (head_cx + int(24 * scale), head_cy + int(24 * scale))
    ], fill=(255, 240, 150))
    
    # 6. Glowing Twitch Drop Gem (Faceted Diamond/Cube Emblem)
    gem_cx = cx - int(95 * scale)
    gem_cy = cy - int(95 * scale)
    gem_size = int(120 * scale)
    
    # Ambient Gem Glow
    glow = Image.new("RGBA", (canvas_size, canvas_size), (0, 0, 0, 0))
    glow_draw = ImageDraw.Draw(glow)
    glow_box = [
        (gem_cx - gem_size, gem_cy - gem_size),
        (gem_cx + gem_size, gem_cy + gem_size)
    ]
    glow_draw.ellipse(glow_box, fill=(0, 255, 200, 120))
    glow_blurred = glow.filter(ImageFilter.GaussianBlur(int(45 * scale)))
    img.alpha_composite(glow_blurred)
    
    # Faceted Gem Polygons (Cyan/Emerald Twitch Drop Crystal)
    top_v = (gem_cx, gem_cy - int(gem_size * 0.65))
    bottom_v = (gem_cx, gem_cy + int(gem_size * 0.65))
    left_v = (gem_cx - int(gem_size * 0.65), gem_cy)
    right_v = (gem_cx + int(gem_size * 0.65), gem_cy)
    center_v = (gem_cx, gem_cy)
    
    # Shadow
    draw.polygon([(p[0], p[1] + int(10 * scale)) for p in [top_v, right_v, bottom_v, left_v]], fill=(0, 0, 0, 70))
    
    # Facets
    draw.polygon([top_v, left_v, center_v], fill=(0, 240, 255, 240))      # Top-Left Cyan Highlight
    draw.polygon([top_v, right_v, center_v], fill=(200, 255, 255, 255))   # Top-Right Pure Glow
    draw.polygon([bottom_v, left_v, center_v], fill=(0, 180, 220, 240))   # Bottom-Left Deep Aqua
    draw.polygon([bottom_v, right_v, center_v], fill=(0, 215, 230, 240))  # Bottom-Right Vibrant Teal
    
    # Sparkle Stars near the Gem
    def draw_star(sx, sy, r):
        draw.polygon([(sx, sy - r), (sx + r * 0.25, sy - r * 0.25), (sx + r, sy), (sx + r * 0.25, sy + r * 0.25),
                      (sx, sy + r), (sx - r * 0.25, sy + r * 0.25), (sx - r, sy), (sx - r * 0.25, sy - r * 0.25)],
                     fill=(255, 255, 255, 230))
    
    draw_star(gem_cx + int(85 * scale), gem_cy - int(65 * scale), int(28 * scale))
    draw_star(gem_cx - int(75 * scale), gem_cy + int(75 * scale), int(18 * scale))
    draw_star(left_tip[0] - int(15 * scale), left_tip[1] - int(15 * scale), int(22 * scale))
    
    img.alpha_composite(fg)
    
    # Resample down from 2048x2048 to 1024x1024 with Lanczos for razor-sharp anti-aliasing
    final_icon = img.resize((size, size), Image.Resampling.LANCZOS)
    return final_icon

def generate_all_sizes(master, output_dir: Path):
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
        print(f"Generated {filename} ({s}x{s})")

if __name__ == "__main__":
    print("==> Generating 1024x1024 Retina macOS master icon...")
    master = create_master_icon(1024)
    master.save("icons/app_icon_1024.png", "PNG")
    print("Saved icons/app_icon_1024.png")
    
    iconset_dir = Path("AppIcon.iconset")
    print("==> Generating Apple iconset dimensions...")
    generate_all_sizes(master, iconset_dir)
    
    print("==> Compiling AppIcon.icns via iconutil...")
    subprocess.run(["iconutil", "-c", "icns", str(iconset_dir), "-o", "AppIcon.icns"], check=True)
    print("SUCCESS: AppIcon.icns successfully created!")
