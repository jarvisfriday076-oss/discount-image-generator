from datetime import datetime, timedelta
from flask import Blueprint, render_template, request, jsonify, redirect, url_for
from flask_login import login_required, current_user
from models import db, User, Store, Generation, ScheduledPost, ActivityLog
from utils import admin_required, log_activity, save_env_key

admin_bp = Blueprint("admin", __name__, url_prefix="/admin")


@admin_bp.before_request
@login_required
def check_admin():
    if not current_user.is_admin:
        return redirect(url_for("main.workspace"))


@admin_bp.route("/")
def dashboard():
    stores = Store.query.all()
    total_stores = len(stores)
    active_stores = sum(1 for s in stores if s.is_active)
    total_generations = Generation.query.count()

    # This week's generations
    week_ago = datetime.utcnow() - timedelta(days=7)
    weekly_generations = Generation.query.filter(Generation.created_at >= week_ago).count()

    # Today's generations
    today_start = datetime.utcnow().replace(hour=0, minute=0, second=0, microsecond=0)
    today_generations = Generation.query.filter(Generation.created_at >= today_start).count()

    # Recent activity
    recent_activity = ActivityLog.query.order_by(ActivityLog.created_at.desc()).limit(20).all()

    # Per-store stats
    store_stats = []
    for s in stores:
        s.check_and_reset_week()
        gen_count = Generation.query.filter_by(store_id=s.id).count()
        store_stats.append({
            "id": s.id,
            "name": s.store_name,
            "owner": s.owner.username,
            "plan": s.plan,
            "weekly_limit": s.weekly_limit,
            "used_this_week": s.generations_this_week,
            "total_generations": gen_count,
            "is_active": s.is_active,
            "created_at": s.created_at,
        })
    db.session.commit()

    return render_template(
        "admin.html",
        total_stores=total_stores,
        active_stores=active_stores,
        total_generations=total_generations,
        weekly_generations=weekly_generations,
        today_generations=today_generations,
        store_stats=store_stats,
        recent_activity=recent_activity,
    )


@admin_bp.route("/api/stores", methods=["GET"])
def list_stores():
    stores = Store.query.all()
    result = []
    for s in stores:
        s.check_and_reset_week()
        result.append({
            "id": s.id,
            "store_name": s.store_name,
            "owner": s.owner.username,
            "email": s.owner.email,
            "plan": s.plan,
            "weekly_limit": s.weekly_limit,
            "used_this_week": s.generations_this_week,
            "total_generations": s.generations.count(),
            "is_active": s.is_active,
            "created_at": s.created_at.isoformat(),
        })
    db.session.commit()
    return jsonify({"stores": result})


@admin_bp.route("/api/stores", methods=["POST"])
def create_store():
    data = request.get_json()
    username = (data.get("username") or "").strip()
    email = (data.get("email") or "").strip()
    password = (data.get("password") or "").strip()
    store_name = (data.get("store_name") or "").strip()
    plan = "free"
    weekly_limit = 1

    if not all([username, email, password, store_name]):
        return jsonify({"error": "All fields are required"}), 400

    if len(password) < 6:
        return jsonify({"error": "Password must be at least 6 characters"}), 400

    if User.query.filter((User.username == username) | (User.email == email)).first():
        return jsonify({"error": "Username or email already exists"}), 400

    api_key = (data.get("api_key") or "").strip()

    try:
        user = User(username=username, email=email, role="store")
        user.set_password(password)
        db.session.add(user)
        db.session.flush()

        store = Store(
            user_id=user.id,
            store_name=store_name,
            weekly_limit=weekly_limit,
            plan=plan,
            custom_api_key=api_key,
        )
        db.session.add(store)
        db.session.commit()
    except Exception as e:
        db.session.rollback()
        return jsonify({"error": f"Failed to create store: {str(e)}"}), 500

    log_activity(db.session, "store_created", f"Store '{store_name}' created for {username}", user_id=current_user.id)

    return jsonify({"status": "success", "store_id": store.id})


@admin_bp.route("/api/stores/<int:store_id>", methods=["PUT"])
def update_store(store_id):
    store = Store.query.get_or_404(store_id)
    data = request.get_json()

    if "store_name" in data:
        store.store_name = data["store_name"].strip()
    if "weekly_limit" in data:
        store.weekly_limit = int(data["weekly_limit"])
    if "plan" in data:
        store.plan = data["plan"]
    if "is_active" in data:
        store.is_active = bool(data["is_active"])
        store.owner.is_active_user = bool(data["is_active"])
    if "api_key" in data:
        store.custom_api_key = data["api_key"].strip()
    if "reset_counter" in data and data["reset_counter"]:
        store.generations_this_week = 0
        store.week_start = datetime.utcnow()

    db.session.commit()
    log_activity(db.session, "store_updated", f"Store '{store.store_name}' updated", store_id=store.id, user_id=current_user.id)
    return jsonify({"status": "success"})


@admin_bp.route("/api/stores/<int:store_id>", methods=["DELETE"])
def delete_store(store_id):
    store = Store.query.get_or_404(store_id)
    user = store.owner
    store_name = store.store_name

    # Delete all related data
    Generation.query.filter_by(store_id=store.id).delete()
    ScheduledPost.query.filter_by(store_id=store.id).delete()
    ActivityLog.query.filter_by(store_id=store.id).delete()
    db.session.delete(store)
    db.session.delete(user)
    db.session.commit()

    log_activity(db.session, "store_deleted", f"Store '{store_name}' permanently deleted", user_id=current_user.id)
    return jsonify({"status": "success"})


@admin_bp.route("/api/stores/<int:store_id>/detail")
def store_detail(store_id):
    """Return full detail for a single store."""
    store = Store.query.get_or_404(store_id)
    store.check_and_reset_week()
    db.session.commit()

    user = store.owner
    total_gens = store.generations.count()
    total_posts = store.scheduled_posts.count()

    # Recent generations (last 20)
    recent_gens = Generation.query.filter_by(store_id=store.id).order_by(
        Generation.created_at.desc()
    ).limit(20).all()

    # Recent activity (last 15 for this store)
    recent_acts = ActivityLog.query.filter_by(store_id=store.id).order_by(
        ActivityLog.created_at.desc()
    ).limit(15).all()

    # Generation type breakdown
    type_counts = {}
    for g in store.generations:
        type_counts[g.gen_type] = type_counts.get(g.gen_type, 0) + 1

    return jsonify({
        "store": {
            "id": store.id,
            "store_name": store.store_name,
            "owner_username": user.username,
            "owner_email": user.email,
            "phone": store.phone,
            "address": store.address,
            "plan": store.plan,
            "weekly_limit": store.weekly_limit,
            "used_this_week": store.generations_this_week,
            "remaining": store.remaining_generations,
            "total_generations": total_gens,
            "total_posts": total_posts,
            "is_active": store.is_active,
            "has_custom_api_key": bool(store.custom_api_key),
            "created_at": store.created_at.isoformat(),
            "last_login": user.last_login.isoformat() if user.last_login else None,
        },
        "generations": [{
            "id": g.id,
            "product_name": g.product_name,
            "gen_type": g.gen_type,
            "image_path": g.image_path,
            "status": g.status,
            "created_at": g.created_at.isoformat(),
        } for g in recent_gens],
        "activity": [{
            "action": a.action,
            "details": a.details,
            "created_at": a.created_at.isoformat(),
        } for a in recent_acts],
        "type_breakdown": type_counts,
    })


@admin_bp.route("/api/stores/<int:store_id>/reset-password", methods=["POST"])
def reset_store_password(store_id):
    store = Store.query.get_or_404(store_id)
    data = request.get_json()
    new_password = (data.get("password") or "").strip()
    if not new_password or len(new_password) < 6:
        return jsonify({"error": "Password must be at least 6 characters"}), 400

    store.owner.set_password(new_password)
    db.session.commit()
    log_activity(db.session, "password_reset", f"Password reset for store '{store.store_name}'", store_id=store.id, user_id=current_user.id)
    return jsonify({"status": "success"})


@admin_bp.route("/api/analytics")
def analytics():
    """Return generation analytics for charts."""
    try:
        days = int(request.args.get("days", 30))
    except (ValueError, TypeError):
        days = 30
    start = datetime.utcnow() - timedelta(days=days)

    # Daily generation counts
    daily = db.session.query(
        db.func.date(Generation.created_at).label("date"),
        db.func.count(Generation.id).label("count"),
    ).filter(Generation.created_at >= start).group_by("date").all()

    # Type breakdown
    type_breakdown = db.session.query(
        Generation.gen_type,
        db.func.count(Generation.id),
    ).filter(Generation.created_at >= start).group_by(Generation.gen_type).all()

    # Top products
    top_products = db.session.query(
        Generation.product_name,
        db.func.count(Generation.id).label("count"),
    ).group_by(Generation.product_name).order_by(db.text("count DESC")).limit(10).all()

    return jsonify({
        "daily": [{"date": str(d.date), "count": d.count} for d in daily],
        "type_breakdown": {t: c for t, c in type_breakdown},
        "top_products": [{"name": p, "count": c} for p, c in top_products],
    })


@admin_bp.route("/api/activity")
def activity_log():
    try:
        page = int(request.args.get("page", 1))
        per_page = int(request.args.get("per_page", 50))
    except (ValueError, TypeError):
        page, per_page = 1, 50

    logs = ActivityLog.query.order_by(ActivityLog.created_at.desc()).paginate(page=page, per_page=per_page)

    return jsonify({
        "logs": [{
            "id": l.id,
            "action": l.action,
            "details": l.details,
            "store_id": l.store_id,
            "created_at": l.created_at.isoformat(),
        } for l in logs.items],
        "total": logs.total,
        "pages": logs.pages,
    })


@admin_bp.route("/api/save-key", methods=["POST"])
def save_global_key():
    """Save global API key (OpenAI or Gemini) from admin panel."""
    import os
    data = request.get_json()
    new_key = (data.get("api_key") or "").strip()
    provider = (data.get("provider") or "openai").strip()
    if not new_key:
        return jsonify({"error": "API key cannot be empty"}), 400

    from flask import current_app
    env_var = "GEMINI_API_KEY" if provider == "gemini" else "OPENAI_API_KEY"

    current_app.config[env_var] = new_key
    os.environ[env_var] = new_key
    save_env_key(env_var, new_key)

    masked = new_key[:5] + "..." + new_key[-3:]
    log_activity(db.session, "api_key_updated", f"{provider} key updated", user_id=current_user.id)
    return jsonify({"status": "success", "masked_key": masked, "provider": provider})


@admin_bp.route("/api/system-health")
def system_health():
    """Return system health info."""
    import os
    db_path = os.path.join(os.path.dirname(__file__), "bottela.db")
    db_size = os.path.getsize(db_path) if os.path.exists(db_path) else 0

    output_dir = os.path.join(os.path.dirname(__file__), "output")
    output_size = sum(
        os.path.getsize(os.path.join(output_dir, f))
        for f in os.listdir(output_dir) if os.path.isfile(os.path.join(output_dir, f))
    ) if os.path.exists(output_dir) else 0

    return jsonify({
        "db_size_mb": round(db_size / 1024 / 1024, 2),
        "output_size_mb": round(output_size / 1024 / 1024, 2),
        "total_users": User.query.count(),
        "total_stores": Store.query.count(),
        "total_generations": Generation.query.count(),
    })
