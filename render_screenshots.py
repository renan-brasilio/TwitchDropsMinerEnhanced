import os
from pathlib import Path
from PIL import Image, ImageDraw, ImageFont

def render_macos_window(content_img_path, output_path):
    content = Image.open(content_img_path)
    cw, ch = content.size
    
    # Titlebar height
    tb_h = 42
    border = 1
    
    total_w = cw + border * 2
    total_h = ch + tb_h + border * 2
    
    # Create window canvas
    window = Image.new("RGBA", (total_w, total_h), (14, 14, 16, 255))
    draw = ImageDraw.Draw(window)
    
    # Draw border
    draw.rectangle([0, 0, total_w - 1, total_h - 1], outline=(45, 45, 53, 255), width=1)
    
    # Draw Titlebar
    draw.rectangle([border, border, total_w - border - 1, border + tb_h - 1], fill=(24, 24, 27, 255))
    draw.line([(border, border + tb_h - 1), (total_w - border - 1, border + tb_h - 1)], fill=(45, 45, 53, 255), width=1)
    
    # Traffic light buttons (Close, Minimize, Zoom)
    colors = [
        (255, 95, 86),   # Red
        (255, 189, 46),  # Yellow
        (39, 201, 63)    # Green
    ]
    dot_r = 6
    start_x = 18
    dot_y = border + tb_h // 2
    for i, col in enumerate(colors):
        dx = start_x + i * 20
        draw.ellipse([dx - dot_r, dot_y - dot_r, dx + dot_r, dot_y + dot_r], fill=col)
        # Subtle rim
        draw.ellipse([dx - dot_r, dot_y - dot_r, dx + dot_r, dot_y + dot_r], outline=(0, 0, 0, 40), width=1)
        
    # Window Title
    title = "Twitch Drops Miner Enhanced — Watching: Emongg (Local)"
    # Center title
    draw.text((total_w // 2, dot_y), title, fill=(173, 173, 184, 255), anchor="mm")
    
    # Paste content
    window.paste(content, (border, border + tb_h))
    
    # Save output
    output_path.parent.mkdir(parents=True, exist_ok=True)
    window.save(output_path, "PNG")
    print(f"Saved {output_path} ({window.size})")

def render_menubar_preview(output_path):
    w, h = 420, 280
    img = Image.new("RGBA", (w, h), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)
    
    # 1. Menu Bar Strip (32px high, dark frosted look)
    bar_h = 32
    draw.rectangle([0, 0, w, bar_h], fill=(20, 20, 24, 250))
    draw.line([(0, bar_h), (w, bar_h)], fill=(45, 45, 53, 255), width=1)
    
    # Menu Bar Item: Twitch+Pickaxe icon
    icon_path = Path("icons/MenuBarIcon@2x.png")
    if icon_path.exists():
        icon = Image.open(icon_path).convert("RGBA")
        icon = icon.resize((18, 18), Image.Resampling.LANCZOS)
        # Tint icon to light grey for dark menubar
        r, g, b, a = icon.split()
        white_icon = Image.merge("RGBA", (a, a, a, a))
        img.paste(white_icon, (24, (bar_h - 18) // 2), white_icon)
    
    # Adjacent system indicators (Wi-Fi, Battery, Control Center)
    draw.text((w - 110, bar_h // 2), "100%   Sat 16:35", fill=(200, 200, 210, 255), anchor="lm")
    
    # 2. Native macOS Dropdown Menu
    menu_w = 340
    menu_x = 16
    menu_y = bar_h + 4
    menu_h = h - menu_y - 8
    
    # Menu shadow & background
    draw.rounded_rectangle([menu_x, menu_y, menu_x + menu_w, menu_y + menu_h], radius=8, fill=(28, 28, 32, 250), outline=(50, 50, 58, 255), width=1)
    
    # Menu items
    items = [
        ("Open Dashboard Window", True),
        ("---", False),
        ("Status: ● Active [Local]", False),
        ("Task: Watching: Emongg", False),
        ("---", False),
        ("Game: Overwatch", True),
        ("Reward: Hollow Hearts Spray", False),
        ("Progress: [████████░░] 85% (153/180m • 27m left)", False),
        ("Channel: Emongg (5,809 viewers)", False),
        ("---", False),
        ("Pause Mining", False),
        ("Claim Eligible Drops", False),
        ("Quit", False),
    ]
    
    cur_y = menu_y + 10
    for text, is_bold in items:
        if text == "---":
            draw.line([(menu_x + 10, cur_y + 3), (menu_x + menu_w - 10, cur_y + 3)], fill=(45, 45, 53, 255), width=1)
            cur_y += 8
        else:
            col = (255, 255, 255, 255) if is_bold else (210, 210, 220, 255)
            if "● Active" in text:
                draw.text((menu_x + 14, cur_y), text, fill=(34, 197, 94, 255))
            elif "Progress:" in text:
                draw.text((menu_x + 14, cur_y), text, fill=(191, 148, 255, 255))
            else:
                draw.text((menu_x + 14, cur_y), text, fill=col)
            cur_y += 18
            
    output_path.parent.mkdir(parents=True, exist_ok=True)
    img.save(output_path, "PNG")
    print(f"Saved {output_path} ({img.size})")

if __name__ == "__main__":
    render_macos_window("/tmp/chrome_dashboard.png", Path("docs/images/app_window.png"))
    render_menubar_preview(Path("docs/images/menubar_status.png"))
