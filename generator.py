import os
import io
import logging
import base64
import time
import requests as http_requests
from openai import OpenAI
from PIL import Image

# Gemini imports are lazy — only loaded when video generation is called
genai = None
types = None

logger = logging.getLogger("bottela.generator")

MAX_RETRIES = 3
RETRY_DELAY = 5  # seconds

# ── OpenAI Client ──────────────────────────────────────────────────

def get_openai_client(api_key: str) -> OpenAI:
    """Get OpenAI client for image generation."""
    return OpenAI(api_key=api_key)


# ── Gemini Client (video only) ─────────────────────────────────────

def get_gemini_client(api_key: str):
    """Get Gemini client for video generation (lazy import)."""
    global genai, types
    if genai is None:
        try:
            from google import genai as _genai
            from google.genai import types as _types
            genai = _genai
            types = _types
        except ImportError:
            raise ImportError("google-genai package is required for video generation. Install with: pip install google-genai")
    return genai.Client(
        api_key=api_key,
        http_options=types.HttpOptions(timeout=120_000),
    )


# Keep legacy name for backward compatibility in app.py
def get_client(api_key: str):
    """Legacy: returns OpenAI client (used for images)."""
    return get_openai_client(api_key)


# ── Event/Theme Styles ─────────────────────────────────────────────

EVENT_STYLES = {
    "christmas": "Christmas holiday theme — red & green color palette, snowflakes, Christmas ornaments, pine branches, snow effects, warm cozy lighting, festive ribbons",
    "new_year": "New Year celebration theme — midnight blue & gold palette, fireworks, confetti, champagne sparkle effects, countdown clock elements, glittery textures",
    "valentines": "Valentine's Day theme — red & pink palette, hearts, roses, soft romantic bokeh, satin/velvet textures, love-themed decorative elements",
    "easter": "Easter theme — pastel palette (lavender, mint, baby pink, soft yellow), Easter eggs, spring flowers, butterflies, soft natural light",
    "memorial_day": "Memorial Day theme — red, white & blue palette, patriotic stars, American flag elements, summer BBQ vibes, respectful and festive",
    "independence_day": "4th of July theme — red, white & blue palette, stars, fireworks, patriotic ribbons, bold American flag-inspired design elements",
    "labor_day": "Labor Day theme — end of summer vibes, warm golden tones, relaxed outdoor atmosphere, patriotic accents, casual celebration mood",
    "halloween": "Halloween theme — dark orange & black palette, spooky fog, bats, cobwebs, jack-o-lantern glow, eerie moonlight, subtle creepy elegance",
    "thanksgiving": "Thanksgiving theme — warm autumn palette (burnt orange, deep red, gold), fall leaves, harvest elements, warm candlelight, rustic wood textures",
    "black_friday": "Black Friday theme — pure black & neon gold/red palette, bold dramatic lighting, cracked/shattered glass effects, intense urgency, maximum contrast",
    "cyber_monday": "Cyber Monday theme — dark tech-inspired palette with neon blue and electric green, digital grid effects, futuristic glowing elements",
    "summer_sale": "Summer sale theme — bright tropical palette (turquoise, coral, yellow), palm leaves, sun rays, beach vibes, gradient sunset sky, fresh and vibrant energy",
    "super_bowl": "Super Bowl theme — football field green, team spirit colors, stadium lights, bold sports typography, championship energy",
    "st_patricks": "St. Patrick's Day theme — green and gold palette, shamrocks, Celtic patterns, Irish pub warmth, lucky charm elements",
    "cinco_de_mayo": "Cinco de Mayo theme — vibrant Mexican fiesta colors (red, green, white), papel picado, sombreros, festive energy, colorful patterns",
}

FESTIVAL_LIST = {
    "christmas": {"name": "Christmas", "emoji": "\U0001f384", "colors": ["#c62828", "#2e7d32"]},
    "new_year": {"name": "New Year", "emoji": "\U0001f386", "colors": ["#1a237e", "#ffd600"]},
    "valentines": {"name": "Valentine's", "emoji": "\u2764\ufe0f", "colors": ["#c62828", "#f48fb1"]},
    "easter": {"name": "Easter", "emoji": "\U0001f430", "colors": ["#7b1fa2", "#81c784"]},
    "memorial_day": {"name": "Memorial Day", "emoji": "\U0001f1fa\U0001f1f8", "colors": ["#b71c1c", "#1565c0"]},
    "independence_day": {"name": "4th of July", "emoji": "\U0001f387", "colors": ["#b71c1c", "#1565c0"]},
    "labor_day": {"name": "Labor Day", "emoji": "\u2692\ufe0f", "colors": ["#1565c0", "#c62828"]},
    "halloween": {"name": "Halloween", "emoji": "\U0001f383", "colors": ["#e65100", "#212121"]},
    "thanksgiving": {"name": "Thanksgiving", "emoji": "\U0001f983", "colors": ["#bf360c", "#ff8f00"]},
    "black_friday": {"name": "Black Friday", "emoji": "\U0001f4b0", "colors": ["#212121", "#ffd600"]},
    "cyber_monday": {"name": "Cyber Monday", "emoji": "\U0001f4bb", "colors": ["#0d47a1", "#00e676"]},
    "summer_sale": {"name": "Summer Sale", "emoji": "\u2600\ufe0f", "colors": ["#00838f", "#ff6f00"]},
    "super_bowl": {"name": "Super Bowl", "emoji": "\U0001f3c8", "colors": ["#1b5e20", "#ff6f00"]},
    "st_patricks": {"name": "St. Patrick's", "emoji": "\u2618\ufe0f", "colors": ["#1b5e20", "#ffd600"]},
    "cinco_de_mayo": {"name": "Cinco de Mayo", "emoji": "\U0001f389", "colors": ["#c62828", "#1b5e20"]},
}

THEME_STYLES = {
    "flash_sale_gold": {
        "name": "Flash Sale Gold",
        "desc": "Dark moody slate/stone background. Gold metallic wax-seal 'SPECIAL OFFER' badge top-left. 'FLASH SALE!' headline in bold white and gold metallic text with light rays. Product centered with dramatic lighting. Gold square '50% OFF LIMITED TIME OFFER' badge on right. Strikethrough original price 'WAS $XX' in white, bold yellow 'NOW $XX' sale price. Dark footer bar with Instagram handle and 'SHOP NOW! LINK IN BIO.' White and gold color scheme on dark background.",
    },
    "flash_sale_yellow": {
        "name": "Flash Sale Yellow",
        "desc": "Warm wood/library background with table lamp and books. Yellow banner 'SPECIAL OFFER' badge top-left. 'FLASH SALE!' headline in bold white text on dark diagonal banner with yellow accents. Product centered on wooden surface. Yellow circle '50% OFF LIMITED TIME OFFER' badge on right. Strikethrough 'WAS $XX' in white, bold yellow 'NOW $XX' sale price. Dark footer bar with Instagram handle and 'SHOP NOW! LINK IN BIO.' Yellow and black color scheme on warm background.",
    },
}

# ── Layout variations — randomized for visual variety per generation ───
LAYOUT_STYLES = [
    "Centered hero composition: product anchored in the center, large display headline arching above, prices flanking the product symmetrically, prominent discount badge floating upper-right. Clean balance.",
    "Asymmetric editorial split: product offset to right two-thirds of the canvas, oversized angled typography on the left, dynamic diagonal energy. High-fashion magazine layout feel.",
    "Layered overlapping composition: oversized product as focal hero, translucent display typography overlapping its silhouette, prices set in geometric color blocks, strong depth via blur and shadow layers.",
    "Minimal luxury layout: extreme negative space, refined serif display headline, product offset to lower-third, single accent color against a muted backdrop, gallery-quality restraint.",
    "Bold maximalist style: vibrant high-saturation palette with one clashing accent, extreme typography scale jumps, decorative shapes overlapping the product edges, energetic kinetic feel — visually rich but balanced.",
    "Vintage poster aesthetic: textured paper or grain background, retro display type with subtle distressing, two-tone palette with a metallic accent, framed by ornamental borders.",
    "Modern gradient style (Spotify Wrapped energy): smooth radial gradient background in 2-3 saturated colors, sans-serif geometric display type, soft inner glow on product, minimal decorative elements.",
]


# ── Helpers ─────────────────────────────────────────────────────────

def download_product_image(url: str) -> Image.Image | None:
    """Download a product image from a URL and return as PIL Image."""
    if not url or url.lower() in ("nan", "none", ""):
        return None
    try:
        resp = http_requests.get(url, timeout=15, headers={
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"
        })
        resp.raise_for_status()
        img = Image.open(io.BytesIO(resp.content)).convert("RGBA")
        img.thumbnail((512, 512), Image.LANCZOS)
        return img
    except Exception as e:
        logger.warning(f"Could not download product image from {url}: {e}")
        return None


def load_logo(logo_path: str) -> Image.Image | None:
    if not logo_path or not os.path.exists(logo_path):
        return None
    try:
        img = Image.open(logo_path).convert("RGBA")
        img.thumbnail((256, 256), Image.LANCZOS)
        return img
    except Exception as e:
        logger.warning(f"Could not load logo from {logo_path}: {e}")
        return None


def load_product_image(path: str) -> Image.Image | None:
    if not path or not os.path.exists(path):
        return None
    try:
        img = Image.open(path).convert("RGBA")
        img.thumbnail((512, 512), Image.LANCZOS)
        return img
    except Exception as e:
        logger.warning(f"Could not load product image from {path}: {e}")
        return None


def pil_to_base64(img: Image.Image) -> str:
    """Convert PIL Image to base64 data URI for OpenAI input."""
    buf = io.BytesIO()
    img.convert("RGB").save(buf, format="PNG")
    return base64.b64encode(buf.getvalue()).decode("utf-8")


def search_product_image_urls(product_name: str, max_results: int = 5) -> list[str]:
    """Scrape Google Images for candidate product photos."""
    import re
    query = f"{product_name} official product photo"
    try:
        resp = http_requests.get(
            "https://www.google.com/search",
            params={"q": query, "tbm": "isch", "safe": "active"},
            headers={"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"},
            timeout=10,
        )
        img_urls = re.findall(r'"(https?://[^"]+\.(?:jpg|jpeg|png|webp)(?:\?[^"]*)?)"', resp.text)
        valid: list[str] = []
        for url in img_urls:
            if any(skip in url.lower() for skip in ["google.com", "gstatic.com", "favicon", "logo", "icon", "1x1"]):
                continue
            valid.append(url)
            if len(valid) >= max_results:
                break
        return valid
    except Exception as e:
        logger.warning(f"Image search failed for {product_name}: {e}")
        return []


def verify_product_image_match(client: OpenAI, image_url: str, product_name: str) -> bool:
    """GPT-4o-mini vision check: does this image actually show the named product?"""
    try:
        resp = client.chat.completions.create(
            model="gpt-4o-mini",
            messages=[{
                "role": "user",
                "content": [
                    {"type": "text", "text": (
                        f"Does this image show '{product_name}' as the main subject? "
                        f"Be strict — the brand and product type must match. "
                        f"Reply with only YES or NO."
                    )},
                    {"type": "image_url", "image_url": {"url": image_url, "detail": "low"}},
                ],
            }],
            max_tokens=5,
        )
        answer = (resp.choices[0].message.content or "").strip().upper()
        return answer.startswith("YES")
    except Exception as e:
        logger.warning(f"Vision verify failed for {product_name} ({image_url}): {e}")
        return False


def find_best_product_image_url(client: OpenAI, product_name: str, max_candidates: int = 3) -> str:
    """Search and vision-verify candidates; return first verified URL, or empty string."""
    for url in search_product_image_urls(product_name, max_results=max_candidates):
        if verify_product_image_match(client, url, product_name):
            logger.info(f"Verified product image for {product_name}: {url}")
            return url
    logger.info(f"No verified product image found for {product_name}")
    return ""


def calculate_discount_percent(original: float, discounted: float) -> int:
    if original <= 0:
        return 0
    return round(((original - discounted) / original) * 100)


def calculate_discounted_price(original: float, discount_pct: int) -> float:
    return round(original * (1 - discount_pct / 100), 2)


FORMAT_SIZES = {
    "post": {"width": 1080, "height": 1080, "label": "1080x1080 square", "aspect": "square"},
    "story": {"width": 1080, "height": 1920, "label": "1080x1920 vertical", "aspect": "9:16 vertical"},
}

OPENAI_SIZE_MAP = {
    "post": "1024x1024",
    "story": "1024x1536",
}


# ── Prompt Builders ─────────────────────────────────────────────────

def build_prompt(
    product_name: str,
    original_price: str,
    discounted_price: str,
    discount_pct: int,
    product_description: str = "",
    event: str = "",
    theme: str = "",
    store_name: str = "",
    fmt: str = "post",
    has_product_image: bool = False,
) -> str:
    import random
    display_store_name = store_name.strip().upper() if store_name and store_name.strip() else ""
    fmt_info = FORMAT_SIZES.get(fmt, FORMAT_SIZES["post"])

    event_desc = EVENT_STYLES.get(event, "")
    theme_data = THEME_STYLES.get(theme, THEME_STYLES["flash_sale_gold"])
    theme_desc = theme_data["desc"]
    style_line = f"Festival theme: {event_desc}" if event_desc else f"Design theme: {theme_desc}"
    store_line = f'\nStore branding: Show "{display_store_name}" with Instagram icon in the footer bar.' if display_store_name else ""
    layout_line = f"\nLayout direction: {random.choice(LAYOUT_STYLES)}"

    if has_product_image:
        opener = "Design a professional product promotional poster for Instagram/TikTok."
        product_req = (
            "- The product MUST exactly match the attached reference image. "
            "Container shape, proportions, label artwork, colors, logo, and packaging details "
            "must be IDENTICAL to the reference. "
            "If the reference shows a CAN, render a CAN — do NOT turn it into a bottle. "
            "If the reference shows a BOTTLE, render a BOTTLE — do NOT turn it into a can. "
            "Never substitute the container type or invent a different package shape."
        )
        prop_line = (
            "- Do NOT add any prop drinkware: no whisky glass, no champagne flute, "
            "no wine glass, no tumbler, no ice cubes, no garnishes, no pouring liquid. "
            "The product alone is the hero — show ONLY the product itself.\n"
        )
        style_footer = (
            "- Style: Clean professional product photography. "
            "The festival/seasonal theme should appear ONLY in the background and decorative borders — "
            "do NOT apply festival styling to the product itself, and do NOT add themed props next to it."
        )
    else:
        opener = "Design a professional liquor promotional poster for Instagram/TikTok."
        product_req = "- The bottle must be photorealistic, centered, with dramatic professional lighting"
        prop_line = "- Include a crystal whisky glass as a prop\n"
        style_footer = "- Style: Premium spirits advertisement with professional photography lighting"

    return f"""{opener}

Product: "{product_name}"
Original price: {original_price} (show with strikethrough, e.g. "WAS {original_price}")
Sale price: {discounted_price} (large, bold, e.g. "NOW {discounted_price}")
Discount: {discount_pct}% OFF (prominent badge)
Format: {fmt_info['label']} — {fmt_info['aspect']}

{style_line}{store_line}{layout_line}

CRITICAL REQUIREMENTS:
- The product name "{product_name}" MUST be spelled exactly as provided, in large bold text
{product_req}
{prop_line}- All prices must be clearly readable — original price with red strikethrough line
- The discount badge must be prominent and eye-catching
- Footer must include "SHOP NOW! LINK IN BIO." with a shopping bag icon
- All text must be sharp, correctly spelled, and high contrast
{style_footer}

DESIGN POLISH (treat this like a senior graphic designer would):
- Typography: pair a strong display font for the headline with a complementary secondary face for prices/CTA. Mix weights for hierarchy. Avoid generic system fonts — go for editorial/branded character.
- Focal hierarchy: the eye should travel product → discount badge → sale price → CTA, in that order. Use scale, contrast, and color weight to enforce that path.
- Color: confident, intentional palette of 3-4 colors max. One bold accent that pops against the dominant background. No muddy mid-tones.
- Texture & depth: subtle gradients, soft directional shadows, atmospheric haze, depth-of-field blur on background elements. Avoid the flat "AI-generated template" look.
- Negative space: let the composition breathe. Don't pack every corner.
- The result should look like a premium brand campaign, not stock-template clip art.
"""


def build_generic_prompt(
    product_name: str,
    product_size: str = "",
    event: str = "",
    theme: str = "",
    store_name: str = "",
    fmt: str = "post",
) -> str:
    display_store_name = store_name.strip().upper() if store_name and store_name.strip() else ""
    fmt_info = FORMAT_SIZES.get(fmt, FORMAT_SIZES["post"])
    size_line = f"\nSize/volume: {product_size}" if product_size else ""

    event_desc = EVENT_STYLES.get(event, "")
    theme_data = THEME_STYLES.get(theme, THEME_STYLES["flash_sale_gold"])
    theme_desc = theme_data["desc"]
    style_line = f"Festival theme: {event_desc}" if event_desc else f"Design theme: {theme_desc}"
    store_line = f'\nStore branding: Show "{display_store_name}" with Instagram icon in the footer bar.' if display_store_name else ""

    return f"""Design a professional liquor brand showcase poster for Instagram/TikTok.

Product: "{product_name}"{size_line}
Format: {fmt_info['label']} — {fmt_info['aspect']}

{style_line}{store_line}

CRITICAL REQUIREMENTS:
- Product name "{product_name}" in large bold text at top
- Bottle centered, photorealistic, with dramatic lighting and props (crystal glass, etc.)
- Dark footer with store branding and "SHOP NOW! LINK IN BIO."
- NO prices, NO discounts — brand awareness only
- All text must be sharp, correctly spelled, and high contrast
- Style: Premium spirits advertisement with professional photography lighting
"""


# ── OpenAI Image Generation ────────────────────────────────────────

def _openai_generate_image(
    client: OpenAI,
    prompt: str,
    output_path: str,
    fmt: str = "post",
    product_image: Image.Image | None = None,
    logo_image: Image.Image | None = None,
) -> str | None:
    """Core OpenAI image generation with retry logic."""
    size = OPENAI_SIZE_MAP.get(fmt, "1024x1024")
    has_reference_images = product_image is not None or logo_image is not None

    # Add reference image instructions to prompt
    if product_image:
        prompt += (
            "\n\nIMPORTANT: The first attached image is the OFFICIAL PRODUCT PHOTO. "
            "Place this product prominently in the center of the promotional banner. "
            "Reproduce its exact container shape, label, colors, logo, and proportions — "
            "do NOT replace, restyle, or invent a different package shape. "
            "The product must look identical to the reference."
        )
    if logo_image:
        prompt += "\nThe second attached image is the store LOGO. Place this logo in the bottom bar, clearly visible."

    last_error = None
    for attempt in range(MAX_RETRIES):
        try:
            logger.info(f"[Attempt {attempt+1}/{MAX_RETRIES}] OpenAI image generation ({size})")

            if has_reference_images:
                # Use Responses API with image_generation tool for multimodal input
                content_parts = []
                if product_image:
                    b64 = pil_to_base64(product_image)
                    content_parts.append({
                        "type": "input_image",
                        "image_url": f"data:image/png;base64,{b64}",
                    })
                if logo_image:
                    b64 = pil_to_base64(logo_image)
                    content_parts.append({
                        "type": "input_image",
                        "image_url": f"data:image/png;base64,{b64}",
                    })
                content_parts.append({"type": "input_text", "text": prompt})

                response = client.responses.create(
                    model="gpt-4.1",
                    input=[{"role": "user", "content": content_parts}],
                    tools=[{
                        "type": "image_generation",
                        "quality": "high",
                        "size": size,
                    }],
                    tool_choice={"type": "image_generation"},
                )

                # Extract image from response
                for output_block in response.output:
                    if output_block.type == "image_generation_call":
                        image_bytes = base64.b64decode(output_block.result)
                        image = Image.open(io.BytesIO(image_bytes))
                        image.save(output_path, quality=95)
                        logger.info(f"Image saved to: {output_path}")
                        return output_path
            else:
                # Use Images API for text-only prompt (simpler, cheaper)
                response = client.images.generate(
                    model="gpt-image-1.5",
                    prompt=prompt,
                    size=size,
                    quality="high",
                )

                image_bytes = base64.b64decode(response.data[0].b64_json)
                image = Image.open(io.BytesIO(image_bytes))
                image.save(output_path, quality=95)
                logger.info(f"Image saved to: {output_path}")
                return output_path

            logger.warning("No image in OpenAI response")
            return None

        except Exception as e:
            last_error = e
            logger.error(f"Attempt {attempt+1} failed: {type(e).__name__}: {e}")
            if attempt < MAX_RETRIES - 1:
                wait = RETRY_DELAY * (attempt + 1)
                logger.info(f"Retrying in {wait}s...")
                time.sleep(wait)
            else:
                raise last_error


# ── Public Image Generation Functions ──────────────────────────────

def generate_single_image(
    client: OpenAI,
    product_name: str,
    original_price: float,
    discount_pct: int,
    output_dir: str = "output",
    event: str = "",
    theme: str = "",
    product_image_url: str = "",
    logo_path: str = "",
    store_name: str = "",
    product_image_path: str = "",
    fmt: str = "post",
) -> str | None:
    """Generate a single promotional image using OpenAI."""

    discounted_price = calculate_discounted_price(original_price, discount_pct)
    orig_str = f"${original_price:,.2f}"
    disc_str = f"${discounted_price:,.2f}"

    product_image = load_product_image(product_image_path) if product_image_path else download_product_image(product_image_url)
    logo_image = load_logo(logo_path)

    prompt = build_prompt(
        product_name=product_name,
        original_price=orig_str,
        discounted_price=disc_str,
        discount_pct=discount_pct,
        event=event,
        theme=theme,
        store_name=store_name,
        fmt=fmt,
        has_product_image=product_image is not None,
    )

    os.makedirs(output_dir, exist_ok=True)
    safe_name = "".join(c if c.isalnum() or c in " -_" else "" for c in product_name).strip().replace(" ", "_")
    timestamp = int(time.time())
    output_path = os.path.join(output_dir, f"{safe_name}_{timestamp}_discount_promo.png")

    return _openai_generate_image(client, prompt, output_path, fmt, product_image, logo_image)


def generate_promo_image(
    client: OpenAI,
    product_name: str,
    original_price: str,
    discounted_price: str,
    output_dir: str = "output",
    product_description: str = "",
    product_url: str = "",
    logo_path: str = "",
    store_name: str = "",
) -> str | None:
    """Generate a promotional image using OpenAI (bulk mode)."""

    orig_val = float(original_price.replace(",", "").replace("\u20b9", "").replace("$", "").strip())
    disc_val = float(discounted_price.replace(",", "").replace("\u20b9", "").replace("$", "").strip())
    discount_pct = calculate_discount_percent(orig_val, disc_val)

    if not any(c in original_price for c in ["\u20b9", "$", "\u20ac", "\u00a3"]):
        original_price = f"${original_price}"
    if not any(c in discounted_price for c in ["\u20b9", "$", "\u20ac", "\u00a3"]):
        discounted_price = f"${discounted_price}"

    product_image = download_product_image(product_url)
    logo_image = load_logo(logo_path)

    prompt = build_prompt(
        product_name, original_price, discounted_price, discount_pct,
        product_description, store_name=store_name,
        has_product_image=product_image is not None,
    )

    os.makedirs(output_dir, exist_ok=True)
    safe_name = "".join(c if c.isalnum() or c in " -_" else "" for c in product_name).strip().replace(" ", "_")
    timestamp = int(time.time())
    output_path = os.path.join(output_dir, f"{safe_name}_{timestamp}_discount_promo.png")

    return _openai_generate_image(client, prompt, output_path, "post", product_image, logo_image)


def generate_generic_image(
    client: OpenAI,
    product_name: str,
    product_size: str = "",
    output_dir: str = "output",
    event: str = "",
    theme: str = "",
    product_image_url: str = "",
    logo_path: str = "",
    store_name: str = "",
    product_image_path: str = "",
    fmt: str = "post",
) -> str | None:
    """Generate a brand-style promotional image using OpenAI."""

    prompt = build_generic_prompt(
        product_name=product_name,
        product_size=product_size,
        event=event,
        theme=theme,
        store_name=store_name,
        fmt=fmt,
    )

    product_image = load_product_image(product_image_path) if product_image_path else download_product_image(product_image_url)
    logo_image = load_logo(logo_path)

    os.makedirs(output_dir, exist_ok=True)
    safe_name = "".join(c if c.isalnum() or c in " -_" else "" for c in product_name).strip().replace(" ", "_")
    timestamp = int(time.time())
    output_path = os.path.join(output_dir, f"{safe_name}_{timestamp}_generic_promo.png")

    return _openai_generate_image(client, prompt, output_path, fmt, product_image, logo_image)


def process_dataframe(client: OpenAI, df, output_dir: str = "output", logo_path: str = "") -> list[dict]:
    """Process all rows in a DataFrame and generate promo images."""
    results = []
    for idx, row in df.iterrows():
        product_name = str(row.get("product_name", "Product"))
        original_price = str(row.get("original_price", "0"))
        discounted_price = str(row.get("discounted_price", "0"))
        product_url = str(row.get("product_url", ""))
        product_description = str(row.get("product_description", "")) if "product_description" in df.columns else ""

        try:
            path = generate_promo_image(
                client=client,
                product_name=product_name,
                original_price=original_price,
                discounted_price=discounted_price,
                output_dir=output_dir,
                product_description=product_description,
                product_url=product_url,
                logo_path=logo_path,
            )
            results.append({"product_name": product_name, "status": "success" if path else "failed", "image_path": path})
        except Exception as e:
            results.append({"product_name": product_name, "status": "error", "error": str(e), "image_path": None})

    return results


# ── Video Generation (Gemini Veo — unchanged) ──────────────────────

VIDEO_POLL_INTERVAL = 10
VIDEO_MAX_POLL_TIME = 300


def build_video_prompt(
    product_name: str,
    original_price: str = "",
    discounted_price: str = "",
    discount_pct: int = 0,
    product_description: str = "",
    event: str = "",
    theme: str = "",
    store_name: str = "",
    is_discount: bool = True,
) -> str:
    display_store_name = store_name.strip().upper() if store_name and store_name.strip() else ""
    desc_line = f" ({product_description})" if product_description else ""

    event_desc = EVENT_STYLES.get(event, "")
    theme_data = THEME_STYLES.get(theme, THEME_STYLES["flash_sale_gold"])

    if event_desc:
        style_section = f"Visual theme: {event_desc}"
    else:
        style_section = f"Visual style: {theme_data['desc']}"

    if is_discount and original_price and discounted_price:
        price_section = f"""
4. DISCOUNT REVEAL (6-8s): Bold text "{discount_pct}% OFF" appears at the bottom center of the frame in large white bold font, with "LIMITED TIME SALE" subtitle underneath. The text fades/slides in smoothly over the hero shot.
"""
    else:
        price_section = ""

    store_section = f'\nShow store name "{display_store_name}" text at the bottom in the final seconds.' if display_store_name else ""

    return f"""Create a premium cinematic product advertisement video for a liquor/spirits bottle called "{product_name}".{desc_line}

CINEMATIC STYLE:
- Dark moody background with dynamic glowing light trails/streaks swirling and orbiting around the bottle
- Warm amber and cool blue lighting contrast
- Reflective dark surface beneath the bottle
- Ultra-smooth slow-motion camera movement
- Professional product photography lighting with rim lights and dramatic shadows

SHOT SEQUENCE:
1. OPENING (0-2s): The bottle appears floating at a dramatic tilted angle, rotating slowly in mid-air. Bright glowing light trails and energy streaks swirl dynamically around it against a dark background. Cinematic lens flares.

2. LANDING (2-4s): The bottle smoothly transitions to standing upright on a dark reflective surface. Ice cubes are scattered around the base. The swirling light trails continue in the background.

3. HERO SHOT (4-6s): Full product showcase — the bottle is centered and upright on the reflective surface, surrounded by ice cubes and fresh citrus fruits as props. The glowing light trails continue swirling in the background.
{price_section}
{style_section}{store_section}

CRITICAL:
- The bottle must be photorealistic and prominently labeled "{product_name}"
- Light trails must be dynamic, glowing, and continuously swirling throughout the video
- The overall feel should be like a high-end Super Bowl spirits commercial
- 9:16 vertical format for Instagram/TikTok
"""


def generate_video(
    client,
    product_name: str,
    original_price: float = 0,
    discount_pct: int = 0,
    output_dir: str = "output",
    event: str = "",
    theme: str = "",
    store_name: str = "",
    product_description: str = "",
    is_discount: bool = True,
    gemini_api_key: str = "",
) -> str | None:
    """Generate a promotional video using Gemini Veo model.

    Note: 'client' param is ignored — we create a Gemini client from gemini_api_key.
    This keeps the function signature compatible with app.py.
    """
    if not gemini_api_key:
        raise ValueError("Gemini API key is required for video generation. Set it in Admin → Settings.")

    gemini_client = get_gemini_client(gemini_api_key)

    if is_discount and original_price > 0 and discount_pct > 0:
        discounted_price = calculate_discounted_price(original_price, discount_pct)
        orig_str = f"${original_price:,.2f}"
        disc_str = f"${discounted_price:,.2f}"
    else:
        orig_str = ""
        disc_str = ""
        is_discount = False

    prompt = build_video_prompt(
        product_name=product_name,
        original_price=orig_str,
        discounted_price=disc_str,
        discount_pct=discount_pct,
        product_description=product_description,
        event=event,
        theme=theme,
        store_name=store_name,
        is_discount=is_discount,
    )

    os.makedirs(output_dir, exist_ok=True)
    safe_name = "".join(c if c.isalnum() or c in " -_" else "" for c in product_name).strip().replace(" ", "_")
    timestamp = int(time.time())
    suffix = "discount_promo" if is_discount else "generic_promo"
    output_path = os.path.join(output_dir, f"{safe_name}_{timestamp}_{suffix}.mp4")

    logger.info(f"Starting video generation for: {product_name}")

    operation = gemini_client.models.generate_videos(
        model="veo-3.1-generate-preview",
        prompt=prompt,
        config=types.GenerateVideosConfig(
            aspect_ratio="9:16",
            number_of_videos=1,
        ),
    )

    elapsed = 0
    while not operation.done:
        logger.info(f"Video generation in progress... ({elapsed}s elapsed)")
        time.sleep(VIDEO_POLL_INTERVAL)
        elapsed += VIDEO_POLL_INTERVAL
        operation = gemini_client.operations.get(operation)
        if elapsed >= VIDEO_MAX_POLL_TIME:
            raise TimeoutError(f"Video generation timed out after {VIDEO_MAX_POLL_TIME}s")

    generated_video = operation.response.generated_videos[0]
    gemini_client.files.download(file=generated_video.video)
    generated_video.video.save(output_path)
    logger.info(f"Video saved to: {output_path}")
    return output_path
