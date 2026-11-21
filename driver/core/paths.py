import hashlib
import json
import os
import re
import shutil
import tempfile
from typing import Any, Dict, Optional
from urllib.parse import urlparse

DRIVER_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
TEMPLATES_DIR = os.path.join(DRIVER_DIR, "templates")
WEB_DIR = os.path.join(DRIVER_DIR, "web")

WORKSPACE_PUBLIC_DIR_NAME = "public"
WEB_ASSETS_DIR_NAME = "assets"
GLOBAL_GLYPH_MAP_FILENAME = "glyph-map.json"
ASSET_HASH_LEN = 6
RAW_COPY_HASH_LEN = 10
EXTERNAL_LINK_ATTRS = ' target="_blank" rel="noopener noreferrer"'

_WORKSPACE_NAME_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$")


def raw_copy_id(text: str) -> str:
    return hashlib.sha1(text.encode("utf-8")).hexdigest()[:RAW_COPY_HASH_LEN]


def validate_workspace(name: str) -> str:
    normalized = name.strip()
    if not normalized:
        raise ValueError("Workspace name must not be empty.")
    if normalized in {".", ".."}:
        raise ValueError("Workspace name cannot be '.' or '..'.")
    if "/" in normalized or "\\" in normalized:
        raise ValueError("Workspace name must not contain path separators.")
    if not _WORKSPACE_NAME_PATTERN.fullmatch(normalized):
        raise ValueError(
            "Workspace name may only contain letters, numbers, dots, underscores, and hyphens."
        )
    return normalized


def safe_join_child(base_dir: str, child_name: str) -> str:
    base_abs = os.path.abspath(base_dir)
    child_abs = os.path.abspath(os.path.join(base_abs, child_name))
    if os.path.commonpath([base_abs, child_abs]) != base_abs:
        raise ValueError(f"Resolved path escapes base directory: {base_dir}")
    return child_abs


def reset_dir(path: str) -> None:
    os.makedirs(path, exist_ok=True)
    if os.listdir(path):
        shutil.rmtree(path)
        os.makedirs(path, exist_ok=True)


def make_temp_dir(build_base: str, prefix: str) -> str:
    root = os.path.abspath(build_base)
    os.makedirs(root, exist_ok=True)
    return tempfile.mkdtemp(prefix=prefix, dir=root)


def relative_href(from_dir: str, target_path: str) -> str:
    rel_path = os.path.relpath(target_path, start=from_dir).replace("\\", "/")
    if not rel_path.startswith("."):
        return f"./{rel_path}"
    return rel_path


def asset_rel_path(asset_path: str) -> str:
    parts = os.path.abspath(asset_path).split(os.sep)
    if WEB_ASSETS_DIR_NAME not in parts:
        raise ValueError(
            f"Expected asset path inside '{WEB_ASSETS_DIR_NAME}/': {asset_path}"
        )
    idx = len(parts) - 1 - parts[::-1].index(WEB_ASSETS_DIR_NAME)
    rel_path = "/".join(part for part in parts[idx + 1 :] if part)
    if not rel_path or rel_path == "." or rel_path.startswith("../"):
        raise ValueError(f"Invalid asset path: {asset_path}")
    return rel_path


def root_asset_href(asset_path: str) -> str:
    return f"/{WEB_ASSETS_DIR_NAME}/{asset_rel_path(asset_path)}"


def rebase_relative_href(href: str, source_dir: str, dest_dir: str) -> str:
    if not href:
        return href

    parsed = urlparse(href)
    if parsed.scheme or href.startswith(("//", "/", "#")):
        return href

    src_abs = os.path.abspath(source_dir)
    dst_abs = os.path.abspath(dest_dir)
    if src_abs == dst_abs:
        return href

    joined_target = os.path.normpath(
        os.path.join(src_abs, parsed.path.replace("/", os.sep))
    )
    rebased = relative_href(dst_abs, joined_target)
    if parsed.query:
        rebased = f"{rebased}?{parsed.query}"
    if parsed.fragment:
        rebased = f"{rebased}#{parsed.fragment}"
    return rebased


def is_external_href(href: str, site_base_url: Optional[str]) -> bool:
    s = (href or "").strip()
    if not s or s.startswith("#"):
        return False
    parsed = urlparse(s)
    scheme = parsed.scheme.lower()
    if not scheme:
        return s.startswith("//")
    if scheme not in {"http", "https"}:
        return True
    if not site_base_url:
        return True
    site = urlparse(site_base_url)
    if not parsed.hostname or not site.hostname:
        return True
    return parsed.hostname.lower() != site.hostname.lower()


def load_config(config_path: str) -> Dict[str, Any]:
    if not config_path or not os.path.isfile(config_path):
        return {}
    with open(config_path, "r", encoding="utf-8") as f:
        loaded = json.load(f)
    if not isinstance(loaded, dict):
        raise RuntimeError(
            f"Invalid config file '{config_path}': expected JSON object."
        )
    return loaded


def resolve_base_url(args: Any, required: bool = True) -> Optional[str]:
    config_data = load_config(getattr(args, "config", ""))
    base_url_arg = getattr(args, "base_url", None)
    if base_url_arg:
        base_url_raw = base_url_arg
    elif "base_url" in config_data:
        base_url_raw = config_data["base_url"]
    else:
        if required:
            raise RuntimeError(
                "Missing base_url. Provide --base-url or set base_url in config JSON."
            )
        return None
    return str(base_url_raw).rstrip("/")


def page_url(
    base_url: str,
    root_dir: str,
    dest_dir: str,
    html_filename: str = "index.html",
) -> Optional[str]:
    root_abs = os.path.abspath(root_dir)
    dest_abs = os.path.abspath(dest_dir)
    try:
        if os.path.commonpath([root_abs, dest_abs]) != root_abs:
            return None
    except ValueError:
        return None

    rel_dir = os.path.relpath(dest_abs, start=root_abs).replace("\\", "/")
    if rel_dir == ".":
        return f"{base_url}/{html_filename}"
    return f"{base_url}/{rel_dir}/{html_filename}"
