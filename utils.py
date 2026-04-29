import os
import logging

logger = logging.getLogger("bottela")


def setup_logging(app):
    """Configure structured logging."""
    handler = logging.StreamHandler()
    fmt = logging.Formatter("[%(asctime)s] %(levelname)s %(name)s: %(message)s", datefmt="%Y-%m-%d %H:%M:%S")
    handler.setFormatter(fmt)
    logger.addHandler(handler)
    logger.setLevel(logging.DEBUG if app.debug else logging.INFO)
    return logger


# Magic byte signatures for image validation
IMAGE_SIGNATURES = {
    b"\x89PNG": "png",
    b"\xff\xd8\xff": "jpg",
    b"RIFF": "webp",
    b"GIF8": "gif",
}


def validate_image_file(file_storage):
    """Validate uploaded image by checking magic bytes, not just extension."""
    header = file_storage.read(12)
    file_storage.seek(0)

    for sig, fmt in IMAGE_SIGNATURES.items():
        if header.startswith(sig):
            return True, fmt

    # Check WEBP specifically (RIFF....WEBP)
    if header[:4] == b"RIFF" and header[8:12] == b"WEBP":
        return True, "webp"

    return False, None


def validate_data_file(filename):
    """Check if a data file has an allowed extension."""
    if not filename or "." not in filename:
        return False
    ext = filename.rsplit(".", 1)[1].lower()
    return ext in {"csv", "xlsx", "xls"}


def sanitize_filename(name):
    """Create a safe filename from a product name."""
    return "".join(c if c.isalnum() or c in " -_" else "" for c in name).strip().replace(" ", "_")


def get_store_upload_dir(store_id, subfolder=""):
    """Get store-specific upload directory."""
    base = os.path.join("uploads", f"store_{store_id}")
    if subfolder:
        base = os.path.join(base, subfolder)
    os.makedirs(base, exist_ok=True)
    return base


def get_store_output_dir(store_id):
    """Get store-specific output directory."""
    path = os.path.join("output", f"store_{store_id}")
    os.makedirs(path, exist_ok=True)
    return path


def save_env_key(env_var: str, value: str):
    """Save a key to the .env file."""
    env_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), ".env")
    lines = []
    key_found = False
    if os.path.exists(env_path):
        with open(env_path, "r") as f:
            for line in f:
                if line.strip().startswith(env_var):
                    lines.append(f"{env_var}={value}\n")
                    key_found = True
                else:
                    lines.append(line)
    if not key_found:
        lines.append(f"{env_var}={value}\n")
    with open(env_path, "w") as f:
        f.writelines(lines)


def log_activity(db_session, action, details="", store_id=None, user_id=None):
    """Log an activity to the database."""
    from models import ActivityLog
    entry = ActivityLog(
        store_id=store_id,
        user_id=user_id,
        action=action,
        details=details[:500],
    )
    db_session.add(entry)
    db_session.commit()
