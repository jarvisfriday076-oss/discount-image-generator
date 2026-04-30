import os
from datetime import datetime
from flask import Flask, render_template, request, jsonify, send_from_directory, redirect, url_for, Blueprint
from dotenv import load_dotenv

from config import config_map
from models import db, User, Store, Generation, ScheduledPost, ActivityLog
from database import init_db
from utils import (
    setup_logging, validate_image_file,
    validate_data_file, get_store_output_dir, get_store_upload_dir,
    log_activity, save_env_key,
)
from generator import (
    get_client, generate_single_image, generate_promo_image,
    generate_generic_image, generate_video, EVENT_STYLES, THEME_STYLES, FESTIVAL_LIST,
)

load_dotenv()


def create_app(config_name=None):
    """Application factory."""
    if config_name is None:
        config_name = os.environ.get("FLASK_ENV", "development")

    app = Flask(__name__)
    app.config.from_object(config_map.get(config_name, config_map["default"]))

    os.makedirs(app.config["UPLOAD_FOLDER"], exist_ok=True)
    os.makedirs(app.config["OUTPUT_FOLDER"], exist_ok=True)

    # Initialize extensions
    init_db(app)
    logger = setup_logging(app)

    # Main blueprint for all routes
    main_bp = Blueprint("main", __name__)

    def get_store():
        """Get the default store."""
        return Store.query.first()

    def get_api_key():
        """Get OpenAI API key for image generation: per-store override or global."""
        store = get_store()
        if store and store.custom_api_key:
            return store.custom_api_key
        return app.config["OPENAI_API_KEY"]

    def get_gemini_key():
        """Get Gemini API key for video generation."""
        return app.config.get("GEMINI_API_KEY", "")

    def check_generation_limit(store):
        """Limits removed — every store can generate."""
        if not store:
            return False, "No store configured."
        return True, ""

    # ── Landing / Workspace ────────────────────────────────────────
    @main_bp.route("/")
    def index():
        return redirect(url_for("main.workspace"))

    @main_bp.route("/workspace")
    def workspace():
        api_key = get_api_key()
        has_key = bool(api_key and api_key != "your_api_key_here")
        events = list(EVENT_STYLES.keys())
        themes = THEME_STYLES
        festivals = FESTIVAL_LIST
        store = get_store()
        return render_template(
            "index.html",
            has_key=has_key,
            events=events,
            themes=themes,
            festivals=festivals,
            store=store,
        )

    # ── Single Image Generation ────────────────────────────────────
    @main_bp.route("/generate-single", methods=["POST"])
    def generate_single():
        store = get_store()
        allowed, err = check_generation_limit(store)
        if not allowed:
            return jsonify({"error": err}), 429

        api_key = get_api_key()
        if not api_key or api_key == "your_api_key_here":
            return jsonify({"error": "API key not configured."}), 400

        product_name = request.form.get("product_name", "").strip()
        if not product_name:
            return jsonify({"error": "Product name is required"}), 400

        try:
            original_price = float(request.form.get("original_price", 0))
        except (ValueError, TypeError):
            return jsonify({"error": "Invalid original price"}), 400

        try:
            discount_pct = int(request.form.get("discount_pct", 0))
        except (ValueError, TypeError):
            return jsonify({"error": "Invalid discount percentage"}), 400

        if original_price <= 0:
            return jsonify({"error": "Original price must be greater than 0"}), 400
        if discount_pct < 1 or discount_pct > 99:
            return jsonify({"error": "Discount must be between 1% and 99%"}), 400

        event = request.form.get("event", "")
        theme = request.form.get("theme", "flash_sale_gold")
        product_image_url = request.form.get("product_image_url", "")
        fmt = request.form.get("format", "post")

        store_name = store.store_name if store else ""
        logo_path = store.logo_path if store and store.logo_path and os.path.exists(store.logo_path) else ""

        # Handle product image upload
        product_upload = request.files.get("product_image")
        prod_path = ""
        if product_upload and product_upload.filename:
            valid, _ = validate_image_file(product_upload)
            if valid:
                prod_dir = get_store_upload_dir(store.id, "products")
                prod_path = os.path.join(prod_dir, "temp_product.png")
                product_upload.save(prod_path)
                product_image_url = ""

        output_dir = get_store_output_dir(store.id) if store else app.config["OUTPUT_FOLDER"]

        try:
            client = get_client(api_key)

            # Auto-search for the official product image if user supplied neither upload nor URL
            if not prod_path and not product_image_url:
                from generator import find_best_product_image_url
                product_image_url = find_best_product_image_url(client, product_name)
                if not product_image_url:
                    return jsonify({
                        "error": f"Couldn't find an image for '{product_name}'. "
                                 f"Please upload a product image or check the spelling. "
                                 f"We don't generate fake bottles when no real reference is available."
                    }), 400

            path = generate_single_image(
                client=client,
                product_name=product_name,
                original_price=original_price,
                discount_pct=discount_pct,
                output_dir=output_dir,
                event=event,
                theme=theme,
                product_image_url=product_image_url,
                logo_path=logo_path,
                store_name=store_name,
                product_image_path=prod_path,
                fmt=fmt,
            )
            if path:
                img_file = os.path.basename(path)

                gen = Generation(
                    store_id=store.id,
                    product_name=product_name,
                    gen_type="festival" if event else "discount",
                    image_path=img_file,
                    original_price=original_price,
                    discounted_price=round(original_price * (1 - discount_pct / 100), 2),
                    discount_pct=discount_pct,
                    event=event,
                    theme=theme,
                    fmt=fmt,
                    status="completed",
                )
                db.session.add(gen)
                store.increment_generation()
                db.session.commit()

                log_activity(db.session, "image_generated", f"{product_name} ({discount_pct}% off)", store_id=store.id)

                return jsonify({"status": "success", "image_path": img_file})
            else:
                return jsonify({"error": "Image generation returned no image"}), 500
        except Exception as e:
            logger.error(f"Generation failed for {product_name}: {e}")
            return jsonify({"error": str(e)}), 500

    # ── Bulk Upload ────────────────────────────────────────────────
    @main_bp.route("/upload", methods=["POST"])
    def upload():
        store = get_store()
        if not store:
            return jsonify({"error": "No store configured."}), 400
        api_key = get_api_key()
        if not api_key or api_key == "your_api_key_here":
            return jsonify({"error": "API key not configured."}), 400

        if "file" not in request.files:
            return jsonify({"error": "No file uploaded"}), 400

        file = request.files["file"]
        if file.filename == "":
            return jsonify({"error": "No file selected"}), 400

        if not validate_data_file(file.filename):
            return jsonify({"error": "Only CSV and Excel files are allowed"}), 400

        import pandas as pd

        upload_dir = get_store_upload_dir(store.id) if store else app.config["UPLOAD_FOLDER"]
        filepath = os.path.join(upload_dir, file.filename)
        file.save(filepath)

        try:
            if filepath.endswith(".csv"):
                df = pd.read_csv(filepath)
            else:
                df = pd.read_excel(filepath)
        except Exception as e:
            return jsonify({"error": f"Failed to read file: {str(e)}"}), 400

        df.columns = df.columns.str.strip()

        column_mapping = {}
        for col in df.columns:
            col_lower = col.lower().replace(" ", "").replace("_", "")
            if col_lower in ("productname", "product", "name", "itemname", "item"):
                column_mapping[col] = "product_name"
            elif col_lower in ("originalprice", "price", "mrp", "regularprice", "actualprice", "originprice"):
                column_mapping[col] = "original_price"
            elif col_lower in ("discountedprice", "discountprice", "saleprice", "offerprice", "finalprice", "sellingprice"):
                column_mapping[col] = "discounted_price"
            elif col_lower in ("producturl", "url", "link", "bigimage", "image", "imageurl", "productimage", "productlink"):
                column_mapping[col] = "product_url"
            elif col_lower in ("productdescription", "description", "desc", "subtitle", "details"):
                column_mapping[col] = "product_description"

        df.rename(columns=column_mapping, inplace=True)
        df.columns = df.columns.str.strip().str.lower()

        required_cols = {"product_name", "original_price", "discounted_price"}
        missing = required_cols - set(df.columns)
        if missing:
            return jsonify({"error": f"Missing columns: {', '.join(missing)}. Found: {', '.join(df.columns)}"}), 400

        logo_path = store.logo_path if store and store.logo_path and os.path.exists(store.logo_path) else ""
        store_name = store.store_name if store else ""

        rows = []
        for idx, row in df.iterrows():
            rows.append({
                "product_name": str(row.get("product_name", "Product")),
                "original_price": str(row.get("original_price", "0")),
                "discounted_price": str(row.get("discounted_price", "0")),
                "product_url": str(row.get("product_url", "")) if "product_url" in df.columns else "",
                "product_description": str(row.get("product_description", "")) if "product_description" in df.columns else "",
            })

        return jsonify({"rows": rows, "logo_path": logo_path or "", "store_name": store_name})

    # ── Bulk Item Generation ───────────────────────────────────────
    @main_bp.route("/generate-bulk-item", methods=["POST"])
    def generate_bulk_item():
        store = get_store()
        allowed, err = check_generation_limit(store)
        if not allowed:
            return jsonify({"error": err}), 429

        api_key = get_api_key()
        if not api_key or api_key == "your_api_key_here":
            return jsonify({"error": "API key not configured."}), 400

        data = request.get_json()
        if not data:
            return jsonify({"error": "No data provided"}), 400

        product_name = data.get("product_name", "Product")
        original_price = data.get("original_price", "0")
        discounted_price = data.get("discounted_price", "0")
        product_url = data.get("product_url", "")
        product_description = data.get("product_description", "")

        logo_path = store.logo_path if store and store.logo_path and os.path.exists(store.logo_path) else ""
        store_name = store.store_name if store else ""
        output_dir = get_store_output_dir(store.id) if store else app.config["OUTPUT_FOLDER"]

        try:
            client = get_client(api_key)
            path = generate_promo_image(
                client=client,
                product_name=product_name,
                original_price=original_price,
                discounted_price=discounted_price,
                output_dir=output_dir,
                product_description=product_description,
                product_url=product_url,
                logo_path=logo_path,
                store_name=store_name,
            )
            if path:
                img_file = os.path.basename(path)
                gen = Generation(
                    store_id=store.id,
                    product_name=product_name,
                    gen_type="discount",
                    image_path=img_file,
                    status="completed",
                )
                db.session.add(gen)
                store.increment_generation()
                db.session.commit()
                return jsonify({"product_name": product_name, "status": "success", "image_path": img_file})
            else:
                return jsonify({"product_name": product_name, "status": "failed", "error": "No image returned"})
        except Exception as e:
            logger.error(f"Bulk generation failed for {product_name}: {e}")
            return jsonify({"product_name": product_name, "status": "error", "error": str(e)})

    # ── Generic Image ──────────────────────────────────────────────
    @main_bp.route("/generate-generic", methods=["POST"])
    def generate_generic():
        store = get_store()
        allowed, err = check_generation_limit(store)
        if not allowed:
            return jsonify({"error": err}), 429

        api_key = get_api_key()
        if not api_key or api_key == "your_api_key_here":
            return jsonify({"error": "API key not configured."}), 400

        product_name = request.form.get("product_name", "").strip()
        if not product_name:
            return jsonify({"error": "Product name is required"}), 400

        product_size = request.form.get("product_size", "").strip()
        event = request.form.get("event", "")
        theme = request.form.get("theme", "flash_sale_gold")
        product_image_url = request.form.get("product_image_url", "")
        fmt = request.form.get("format", "post")

        store_name = store.store_name if store else ""
        logo_path = store.logo_path if store and store.logo_path and os.path.exists(store.logo_path) else ""

        product_upload = request.files.get("product_image")
        prod_path = ""
        if product_upload and product_upload.filename:
            valid, _ = validate_image_file(product_upload)
            if valid:
                prod_dir = get_store_upload_dir(store.id, "products")
                prod_path = os.path.join(prod_dir, "temp_product.png")
                product_upload.save(prod_path)
                product_image_url = ""

        output_dir = get_store_output_dir(store.id) if store else app.config["OUTPUT_FOLDER"]

        try:
            client = get_client(api_key)
            path = generate_generic_image(
                client=client,
                product_name=product_name,
                product_size=product_size,
                output_dir=output_dir,
                event=event,
                theme=theme,
                product_image_url=product_image_url,
                logo_path=logo_path,
                store_name=store_name,
                product_image_path=prod_path,
                fmt=fmt,
            )
            if path:
                img_file = os.path.basename(path)
                gen = Generation(
                    store_id=store.id,
                    product_name=product_name,
                    gen_type="generic",
                    image_path=img_file,
                    event=event,
                    theme=theme,
                    fmt=fmt,
                    status="completed",
                )
                db.session.add(gen)
                store.increment_generation()
                db.session.commit()
                log_activity(db.session, "generic_generated", product_name, store_id=store.id)
                return jsonify({"status": "success", "image_path": img_file})
            else:
                return jsonify({"error": "Image generation returned no image"}), 500
        except Exception as e:
            logger.error(f"Generic generation failed for {product_name}: {e}")
            return jsonify({"error": str(e)}), 500

    # ── Video Generation ───────────────────────────────────────────
    @main_bp.route("/generate-video", methods=["POST"])
    def generate_video_route():
        store = get_store()
        allowed, err = check_generation_limit(store)
        if not allowed:
            return jsonify({"error": err}), 429

        api_key = get_api_key()
        if not api_key or api_key == "your_api_key_here":
            return jsonify({"error": "API key not configured."}), 400

        product_name = request.form.get("product_name", "").strip()
        if not product_name:
            return jsonify({"error": "Product name is required"}), 400

        event = request.form.get("event", "")
        theme = request.form.get("theme", "flash_sale_gold")
        product_description = request.form.get("product_description", "")
        store_name = store.store_name if store else ""

        module = request.form.get("module", "discount")
        is_discount = module in ("discount", "festival")

        original_price = 0
        discount_pct = 0
        if is_discount:
            try:
                original_price = float(request.form.get("original_price", 0))
            except (ValueError, TypeError):
                original_price = 0
            try:
                discount_pct = int(request.form.get("discount_pct", 0))
            except (ValueError, TypeError):
                discount_pct = 0

        output_dir = get_store_output_dir(store.id) if store else app.config["OUTPUT_FOLDER"]

        try:
            client = get_client(api_key)
            path = generate_video(
                client=client,
                product_name=product_name,
                original_price=original_price,
                discount_pct=discount_pct,
                output_dir=output_dir,
                event=event,
                theme=theme,
                store_name=store_name,
                product_description=product_description,
                is_discount=is_discount,
                gemini_api_key=get_gemini_key(),
            )
            if path:
                video_file = os.path.basename(path)
                gen = Generation(
                    store_id=store.id,
                    product_name=product_name,
                    gen_type="video",
                    image_path=video_file,
                    event=event,
                    theme=theme,
                    status="completed",
                )
                db.session.add(gen)
                store.increment_generation()
                db.session.commit()
                log_activity(db.session, "video_generated", product_name, store_id=store.id)
                return jsonify({"status": "success", "video_path": video_file})
            else:
                return jsonify({"error": "Video generation returned no video"}), 500
        except TimeoutError as e:
            return jsonify({"error": f"Video generation timed out: {e}"}), 504
        except Exception as e:
            logger.error(f"Video generation failed for {product_name}: {e}")
            return jsonify({"error": str(e)}), 500

    # ── Serve Output Files ─────────────────────────────────────────
    @main_bp.route("/output/<path:filename>")
    def serve_output(filename):
        store = get_store()
        if store:
            store_dir = get_store_output_dir(store.id)
            if os.path.exists(os.path.join(store_dir, filename)):
                return send_from_directory(store_dir, filename)
        return send_from_directory(app.config["OUTPUT_FOLDER"], filename)

    # ── Gallery ────────────────────────────────────────────────────
    @main_bp.route("/gallery")
    def gallery():
        store = get_store()
        if not store:
            return jsonify({"images": []})

        generations = Generation.query.filter_by(store_id=store.id).order_by(Generation.created_at.desc()).all()
        items = []
        for g in generations:
            is_video = g.image_path.lower().endswith(".mp4") if g.image_path else False
            items.append({
                "id": g.id,
                "filename": g.image_path,
                "product_name": g.product_name,
                "type": g.gen_type,
                "media": "video" if is_video else "image",
                "created": g.created_at.isoformat(),
                "is_favorite": g.is_favorite,
            })

        return jsonify({"images": items})

    # ── Favorites ──────────────────────────────────────────────────
    @main_bp.route("/generation/<int:gen_id>/favorite", methods=["POST"])
    def toggle_favorite(gen_id):
        gen = Generation.query.get_or_404(gen_id)
        store = get_store()
        if not store or gen.store_id != store.id:
            return jsonify({"error": "Not authorized"}), 403
        gen.is_favorite = not gen.is_favorite
        db.session.commit()
        return jsonify({"status": "success", "is_favorite": gen.is_favorite})

    # ── Delete Generation ──────────────────────────────────────────
    @main_bp.route("/generation/<int:gen_id>", methods=["DELETE"])
    def delete_generation(gen_id):
        gen = Generation.query.get_or_404(gen_id)
        store = get_store()
        if not store or gen.store_id != store.id:
            return jsonify({"error": "Not authorized"}), 403

        # Delete the file from disk
        if gen.image_path:
            output_dir = get_store_output_dir(store.id) if store else app.config["OUTPUT_FOLDER"]
            file_path = os.path.join(output_dir, gen.image_path)
            try:
                if os.path.exists(file_path):
                    os.remove(file_path)
            except Exception as e:
                logger.warning(f"Could not delete file {file_path}: {e}")

        product_name = gen.product_name
        db.session.delete(gen)
        db.session.commit()
        log_activity(db.session, "image_deleted", product_name, store_id=store.id)
        return jsonify({"status": "success"})

    # ── Store Profile ──────────────────────────────────────────────
    @main_bp.route("/settings/store-profile", methods=["GET"])
    def get_store_profile():
        store = get_store()
        if not store:
            return jsonify({"store_name": "", "has_logo": False})
        has_logo = bool(store.logo_path and os.path.exists(store.logo_path))
        return jsonify({
            "store_name": store.store_name,
            "has_logo": has_logo,
            "plan": store.plan,
            "weekly_limit": store.weekly_limit,
            "used": store.generations_this_week,
            "remaining": store.remaining_generations,
        })

    @main_bp.route("/settings/store-profile", methods=["POST"])
    def update_store_profile():
        store = get_store()
        if not store:
            return jsonify({"error": "No store found"}), 400

        store_name = request.form.get("store_name", "").strip()
        if store_name:
            store.store_name = store_name

        logo_file = request.files.get("logo")
        if logo_file and logo_file.filename:
            valid, _ = validate_image_file(logo_file)
            if valid:
                logo_dir = get_store_upload_dir(store.id, "logos")
                logo_path = os.path.join(logo_dir, "store_logo.png")
                logo_file.save(logo_path)
                store.logo_path = logo_path

        db.session.commit()
        return jsonify({"status": "success", "store_name": store.store_name, "has_logo": bool(store.logo_path)})

    @main_bp.route("/settings/store-logo")
    def serve_store_logo():
        store = get_store()
        if store and store.logo_path and os.path.exists(store.logo_path):
            directory = os.path.dirname(store.logo_path)
            filename = os.path.basename(store.logo_path)
            return send_from_directory(directory, filename)
        return "", 404

    # ── Product Image Search ───────────────────────────────────────
    @main_bp.route("/search-product-image", methods=["POST"])
    def search_product_image():
        from generator import search_product_image_urls

        data = request.get_json()
        product_name = (data.get("product_name") or "").strip()
        if not product_name:
            return jsonify({"error": "Product name required"}), 400

        try:
            urls = search_product_image_urls(product_name, max_results=5)
            return jsonify({"images": urls})
        except Exception as e:
            logger.error(f"Image search failed: {e}")
            return jsonify({"images": [], "error": str(e)})

    # ── API Key Settings ───────────────────────────────────────────
    @main_bp.route("/settings/api-key", methods=["GET"])
    def get_api_key_status():
        openai_key = get_api_key()
        gemini_key = get_gemini_key()
        has_openai = bool(openai_key and openai_key != "your_api_key_here")
        has_gemini = bool(gemini_key and gemini_key != "your_api_key_here")
        return jsonify({
            "has_key": has_openai,
            "has_openai": has_openai,
            "has_gemini": has_gemini,
            "masked_key": (openai_key[:5] + "..." + openai_key[-3:]) if has_openai else "",
        })

    @main_bp.route("/settings/api-key", methods=["POST"])
    def update_api_key():
        data = request.get_json()
        new_key = (data.get("api_key") or "").strip()
        provider = (data.get("provider") or "openai").strip()
        if not new_key:
            return jsonify({"error": "API key cannot be empty"}), 400

        if provider == "gemini":
            env_var = "GEMINI_API_KEY"
        else:
            env_var = "OPENAI_API_KEY"

        app.config[env_var] = new_key
        os.environ[env_var] = new_key
        save_env_key(env_var, new_key)

        masked = new_key[:5] + "..." + new_key[-3:]
        return jsonify({"status": "success", "masked_key": masked, "provider": provider})

    # ── Scheduled Posts ────────────────────────────────────────────
    @main_bp.route("/schedule-post", methods=["POST"])
    def schedule_post():
        store = get_store()
        if not store:
            return jsonify({"error": "No store found"}), 400

        data = request.get_json()
        if not data:
            return jsonify({"error": "No data provided"}), 400

        image = (data.get("image") or "").strip()
        platform = (data.get("platform") or "").strip()
        caption = (data.get("caption") or "").strip()
        scheduled_at_str = (data.get("scheduled_at") or "").strip()

        if not image:
            return jsonify({"error": "Image is required"}), 400
        if not platform:
            return jsonify({"error": "Platform is required"}), 400
        if not scheduled_at_str:
            return jsonify({"error": "Schedule date/time is required"}), 400

        try:
            scheduled_at = datetime.fromisoformat(scheduled_at_str)
        except ValueError:
            return jsonify({"error": "Invalid date/time format"}), 400

        post = ScheduledPost(
            store_id=store.id,
            image_path=image,
            platform=platform,
            caption=caption,
            scheduled_at=scheduled_at,
            status="pending",
        )
        db.session.add(post)
        db.session.commit()

        log_activity(db.session, "post_scheduled", f"{platform} - {image}", store_id=store.id)

        return jsonify({
            "status": "success",
            "post": {
                "id": post.id,
                "image": post.image_path,
                "platform": post.platform,
                "caption": post.caption,
                "scheduled_at": post.scheduled_at.isoformat(),
                "status": post.status,
                "created_at": post.created_at.isoformat(),
            }
        })

    @main_bp.route("/scheduled-posts")
    def get_scheduled_posts():
        store = get_store()
        if not store:
            return jsonify({"posts": []})

        posts = ScheduledPost.query.filter_by(store_id=store.id).order_by(ScheduledPost.scheduled_at).all()
        return jsonify({
            "posts": [{
                "id": p.id,
                "image": p.image_path,
                "platform": p.platform,
                "caption": p.caption,
                "scheduled_at": p.scheduled_at.isoformat(),
                "status": p.status,
                "posted_at": p.posted_at.isoformat() if p.posted_at else None,
                "created_at": p.created_at.isoformat(),
            } for p in posts]
        })

    @main_bp.route("/scheduled-posts/<int:post_id>", methods=["DELETE"])
    def delete_scheduled_post(post_id):
        post = ScheduledPost.query.get_or_404(post_id)
        store = get_store()
        if not store or post.store_id != store.id:
            return jsonify({"error": "Not authorized"}), 403
        db.session.delete(post)
        db.session.commit()
        return jsonify({"status": "success"})

    @main_bp.route("/scheduled-posts/<int:post_id>/post-now", methods=["POST"])
    def post_now(post_id):
        post = ScheduledPost.query.get_or_404(post_id)
        store = get_store()
        if not store or post.store_id != store.id:
            return jsonify({"error": "Not authorized"}), 403
        post.status = "posted"
        post.posted_at = datetime.utcnow()
        db.session.commit()
        return jsonify({"status": "success"})

    # ── Usage Stats ────────────────────────────────────────────────
    @main_bp.route("/api/usage")
    def get_usage():
        store = get_store()
        if not store:
            return jsonify({"error": "No store"}), 400
        store.check_and_reset_week()
        db.session.commit()
        return jsonify({
            "plan": store.plan,
            "weekly_limit": store.weekly_limit,
            "used": store.generations_this_week,
            "remaining": store.remaining_generations,
            "total_all_time": store.generations.count(),
        })

    # ── Health Check ───────────────────────────────────────────────
    @main_bp.route("/health")
    def health():
        return jsonify({"status": "ok", "timestamp": datetime.utcnow().isoformat()})

    app.register_blueprint(main_bp)

    # Custom error pages
    @app.errorhandler(404)
    def not_found(e):
        if request.path.startswith("/api/") or request.headers.get("Accept", "").startswith("application/json"):
            return jsonify({"error": "Not found"}), 404
        return redirect(url_for("main.workspace"))

    @app.errorhandler(500)
    def server_error(e):
        return jsonify({"error": "Internal server error"}), 500

    @app.errorhandler(429)
    def rate_limited(e):
        return jsonify({"error": "Weekly generation limit reached."}), 429

    return app


# Run with: python app.py
if __name__ == "__main__":
    app = create_app()
    app.run(debug=True, port=5000)
