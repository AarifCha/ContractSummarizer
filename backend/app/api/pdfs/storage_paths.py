def slugify(value: str) -> str:
    safe = "".join(ch if ch.isalnum() or ch in "-_" else "_" for ch in value.strip())
    safe = safe.strip("._")
    return safe or "unknown"


def expected_user_folder(user: dict) -> str:
    user_name = user.get("email", "").split("@")[0] or str(user["id"])
    return slugify(user_name)


def is_supported_storage_path(stored_name: str, user: dict) -> bool:
    normalized = stored_name.replace("\\", "/")
    parts = [part for part in normalized.split("/") if part]
    if len(parts) != 3:
        return False
    return parts[0] == expected_user_folder(user)
