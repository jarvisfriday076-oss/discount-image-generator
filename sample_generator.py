from PIL import Image, ImageDraw, ImageFont
import os

def generate_sample():
    """Generate a sample Instagram promo image (1080x1080)"""
    W, H = 1080, 1080

    img = Image.new("RGB", (W, H), "#1a1a2e")
    draw = ImageDraw.Draw(img)

    # --- Background gradient effect (top band) ---
    for y in range(0, 280):
        r = int(230 - (y / 280) * 200)
        g = int(57 - (y / 280) * 30)
        b = int(70 - (y / 280) * 40)
        draw.line([(0, y), (W, y)], fill=(r, g, b))

    # --- "SALE" banner at top ---
    try:
        font_big = ImageFont.truetype("arialbd.ttf", 72)
        font_medium = ImageFont.truetype("arialbd.ttf", 42)
        font_small = ImageFont.truetype("arial.ttf", 32)
        font_price = ImageFont.truetype("arialbd.ttf", 80)
        font_original = ImageFont.truetype("arial.ttf", 40)
        font_name = ImageFont.truetype("arialbd.ttf", 38)
        font_percent = ImageFont.truetype("arialbd.ttf", 56)
        font_off = ImageFont.truetype("arialbd.ttf", 28)
    except:
        font_big = ImageFont.load_default()
        font_medium = font_small = font_price = font_original = font_name = font_percent = font_off = font_big

    # Top banner text
    draw.text((W // 2, 60), "MEGA SALE", fill="white", font=font_big, anchor="mt")
    draw.text((W // 2, 145), "LIMITED TIME OFFER", fill="#ffcc00", font=font_small, anchor="mt")

    # --- Divider line ---
    draw.line([(100, 200), (W - 100, 200)], fill="#e63946", width=3)

    # --- Product image placeholder (simulated) ---
    product_box = (190, 240, 890, 700)
    # White rounded rect background for product
    draw.rounded_rectangle(product_box, radius=20, fill="#ffffff")

    # Simulated product placeholder
    cx = (product_box[0] + product_box[2]) // 2
    cy = (product_box[1] + product_box[3]) // 2
    # Draw a shoe-like placeholder icon
    draw.rounded_rectangle((cx - 160, cy - 100, cx + 160, cy + 80), radius=30, fill="#f0f0f0", outline="#cccccc", width=2)
    draw.text((cx, cy - 20), "PRODUCT", fill="#999999", font=font_medium, anchor="mm")
    draw.text((cx, cy + 30), "IMAGE", fill="#999999", font=font_small, anchor="mm")

    # --- Product name ---
    draw.text((W // 2, 740), "Premium Wireless Headphones", fill="white", font=font_name, anchor="mt")

    # --- Price section ---
    # Original price (strikethrough)
    orig_text = "₹4,999"
    orig_bbox = draw.textbbox((0, 0), orig_text, font=font_original)
    orig_w = orig_bbox[2] - orig_bbox[0]
    orig_x = W // 2 - 120
    orig_y = 810
    draw.text((orig_x, orig_y), orig_text, fill="#888888", font=font_original)
    # Strikethrough line
    orig_text_bbox = draw.textbbox((orig_x, orig_y), orig_text, font=font_original)
    line_y = (orig_text_bbox[1] + orig_text_bbox[3]) // 2
    draw.line([(orig_text_bbox[0], line_y), (orig_text_bbox[2], line_y)], fill="#e63946", width=3)

    # Discounted price
    draw.text((W // 2 + 80, 795), "₹2,499", fill="#00ff88", font=font_price, anchor="mt")

    # --- Discount badge (circle) ---
    badge_cx, badge_cy = 920, 320
    badge_r = 75
    draw.ellipse(
        (badge_cx - badge_r, badge_cy - badge_r, badge_cx + badge_r, badge_cy + badge_r),
        fill="#e63946"
    )
    draw.text((badge_cx, badge_cy - 15), "50%", fill="white", font=font_percent, anchor="mm")
    draw.text((badge_cx, badge_cy + 30), "OFF", fill="white", font=font_off, anchor="mm")

    # --- Bottom CTA bar ---
    draw.rounded_rectangle((0, 940, W, H), radius=0, fill="#e63946")
    draw.text((W // 2, 970), "SHOP NOW  →", fill="white", font=font_medium, anchor="mt")

    # --- Bottom accent line ---
    draw.line([(0, 938), (W, 938)], fill="#ffcc00", width=4)

    # --- Save ---
    os.makedirs("output", exist_ok=True)
    output_path = os.path.join("output", "sample_promo.png")
    img.save(output_path, quality=95)
    print(f"Sample image saved to: {output_path}")
    return output_path

if __name__ == "__main__":
    generate_sample()
