from models import db, User, Store, Template
from generator import THEME_STYLES


def init_db(app):
    """Initialize database and create tables."""
    db.init_app(app)
    with app.app_context():
        db.create_all()
        _seed_defaults()


def _seed_defaults():
    """Seed default store and templates if they don't exist."""
    # Create default user + store if none exists
    if not Store.query.first():
        user = User.query.filter_by(username="default").first()
        if not user:
            user = User(
                username="default",
                email="store@bottela.ai",
                role="store",
            )
            user.set_password("unused")
            db.session.add(user)
            db.session.flush()

        store = Store(
            user_id=user.id,
            store_name="My Store",
            plan="free",
            weekly_limit=10,
        )
        db.session.add(store)
        db.session.commit()

    # Seed default templates from THEME_STYLES
    for key, data in THEME_STYLES.items():
        if not Template.query.filter_by(theme_key=key).first():
            t = Template(
                name=data["name"],
                theme_key=key,
                description=data["desc"][:200],
                category="general",
                is_default=True,
            )
            db.session.add(t)
    db.session.commit()
