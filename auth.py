from datetime import datetime
from flask import Blueprint, render_template, request, redirect, url_for, flash, jsonify
from flask_login import login_user, logout_user, login_required, current_user
from models import db, User, Store
from utils import log_activity

auth_bp = Blueprint("auth", __name__)


@auth_bp.route("/login", methods=["GET", "POST"])
def login():
    if current_user.is_authenticated:
        if current_user.is_admin:
            return redirect(url_for("admin.dashboard"))
        return redirect(url_for("main.workspace"))

    if request.method == "POST":
        username = request.form.get("username", "").strip()
        password = request.form.get("password", "")

        user = User.query.filter(
            (User.username == username) | (User.email == username)
        ).first()

        if user and user.check_password(password):
            if not user.is_active:
                flash("Your account has been deactivated. Contact admin.", "error")
                return render_template("login.html")

            login_user(user, remember=True)
            user.last_login = datetime.utcnow()
            db.session.commit()

            log_activity(db.session, "login", f"User {user.username} logged in", user_id=user.id)

            if user.is_admin:
                return redirect(url_for("admin.dashboard"))
            return redirect(url_for("main.workspace"))
        else:
            flash("Invalid username or password.", "error")

    return render_template("login.html")


@auth_bp.route("/logout")
@login_required
def logout():
    log_activity(db.session, "logout", f"User {current_user.username} logged out", user_id=current_user.id)
    logout_user()
    return redirect(url_for("auth.login"))


@auth_bp.route("/api/auth/status")
def auth_status():
    """Return current auth status for frontend."""
    if current_user.is_authenticated:
        data = {
            "authenticated": True,
            "username": current_user.username,
            "role": current_user.role,
        }
        if current_user.store:
            s = current_user.store
            data["store"] = {
                "id": s.id,
                "name": s.store_name,
                "plan": s.plan,
                "weekly_limit": s.weekly_limit,
                "used": s.generations_this_week,
                "remaining": s.remaining_generations,
            }
        return jsonify(data)
    return jsonify({"authenticated": False})
