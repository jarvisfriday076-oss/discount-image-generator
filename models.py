from datetime import datetime, timedelta
from flask_sqlalchemy import SQLAlchemy
from flask_login import UserMixin
from werkzeug.security import generate_password_hash, check_password_hash

db = SQLAlchemy()


class User(UserMixin, db.Model):
    __tablename__ = "users"

    id = db.Column(db.Integer, primary_key=True)
    username = db.Column(db.String(80), unique=True, nullable=False, index=True)
    email = db.Column(db.String(120), unique=True, nullable=False, index=True)
    password_hash = db.Column(db.String(256), nullable=False)
    role = db.Column(db.String(20), nullable=False, default="store")  # admin | store
    is_active_user = db.Column(db.Boolean, default=True)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    last_login = db.Column(db.DateTime)

    store = db.relationship("Store", backref="owner", uselist=False, lazy=True)

    def set_password(self, password):
        self.password_hash = generate_password_hash(password)

    def check_password(self, password):
        return check_password_hash(self.password_hash, password)

    @property
    def is_admin(self):
        return self.role == "admin"

    @property
    def is_active(self):
        return self.is_active_user


class Store(db.Model):
    __tablename__ = "stores"

    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=False, unique=True)
    store_name = db.Column(db.String(200), nullable=False)
    logo_path = db.Column(db.String(500), default="")
    phone = db.Column(db.String(20), default="")
    address = db.Column(db.Text, default="")

    # API config — uses global key by default, can override per-store
    custom_api_key = db.Column(db.String(500), default="")

    # Plan & limits
    plan = db.Column(db.String(20), default="free")  # free (1/week for all)
    weekly_limit = db.Column(db.Integer, default=1)
    generations_this_week = db.Column(db.Integer, default=0)
    week_start = db.Column(db.DateTime, default=datetime.utcnow)

    is_active = db.Column(db.Boolean, default=True)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    generations = db.relationship("Generation", backref="store", lazy="dynamic")
    scheduled_posts = db.relationship("ScheduledPost", backref="store", lazy="dynamic")
    activity_logs = db.relationship("ActivityLog", backref="store", lazy="dynamic")

    def check_and_reset_week(self):
        """Reset weekly counter if a new week has started."""
        now = datetime.utcnow()
        if self.week_start is None or (now - self.week_start) >= timedelta(days=7):
            self.week_start = now
            self.generations_this_week = 0

    def can_generate(self):
        """Check if store has remaining generations this week."""
        self.check_and_reset_week()
        return self.generations_this_week < self.weekly_limit

    def increment_generation(self):
        """Increment the weekly generation counter."""
        self.check_and_reset_week()
        self.generations_this_week += 1

    @property
    def remaining_generations(self):
        self.check_and_reset_week()
        return max(0, self.weekly_limit - self.generations_this_week)


class Generation(db.Model):
    __tablename__ = "generations"

    id = db.Column(db.Integer, primary_key=True)
    store_id = db.Column(db.Integer, db.ForeignKey("stores.id"), nullable=False, index=True)
    product_name = db.Column(db.String(300), nullable=False)
    gen_type = db.Column(db.String(30), nullable=False)  # discount | generic | festival | video
    image_path = db.Column(db.String(500))
    original_price = db.Column(db.Float, default=0)
    discounted_price = db.Column(db.Float, default=0)
    discount_pct = db.Column(db.Integer, default=0)
    event = db.Column(db.String(50), default="")
    theme = db.Column(db.String(50), default="")
    fmt = db.Column(db.String(20), default="post")
    prompt_used = db.Column(db.Text, default="")
    status = db.Column(db.String(20), default="completed")  # pending | processing | completed | failed
    error_message = db.Column(db.Text, default="")
    is_favorite = db.Column(db.Boolean, default=False)
    created_at = db.Column(db.DateTime, default=datetime.utcnow, index=True)


class ScheduledPost(db.Model):
    __tablename__ = "scheduled_posts"

    id = db.Column(db.Integer, primary_key=True)
    store_id = db.Column(db.Integer, db.ForeignKey("stores.id"), nullable=False, index=True)
    image_path = db.Column(db.String(500), nullable=False)
    platform = db.Column(db.String(30), nullable=False)
    caption = db.Column(db.Text, default="")
    scheduled_at = db.Column(db.DateTime, nullable=False, index=True)
    status = db.Column(db.String(20), default="pending")  # pending | posted | failed
    posted_at = db.Column(db.DateTime)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)


class ActivityLog(db.Model):
    __tablename__ = "activity_log"

    id = db.Column(db.Integer, primary_key=True)
    store_id = db.Column(db.Integer, db.ForeignKey("stores.id"), nullable=True, index=True)
    user_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=True)
    action = db.Column(db.String(100), nullable=False)
    details = db.Column(db.Text, default="")
    created_at = db.Column(db.DateTime, default=datetime.utcnow, index=True)


class Template(db.Model):
    __tablename__ = "templates"

    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(100), nullable=False)
    theme_key = db.Column(db.String(50), nullable=False, unique=True)
    description = db.Column(db.Text, default="")
    category = db.Column(db.String(50), default="general")
    preview_path = db.Column(db.String(500), default="")
    is_default = db.Column(db.Boolean, default=False)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
