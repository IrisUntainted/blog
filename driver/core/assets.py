import hashlib
import os
import re
import shutil
import subprocess
import sys
import threading
from dataclasses import dataclass
from typing import Dict, List, Optional, Set, Tuple

from .paths import (
    ASSET_HASH_LEN,
    GLOBAL_GLYPH_MAP_FILENAME,
    WEB_ASSETS_DIR_NAME,
    WEB_DIR,
    root_asset_href,
    safe_join_child,
)


@dataclass(frozen=True)
class WebAsset:
    kind: str
    placement: str
    href: Optional[str]
    inline: Optional[str]


@dataclass(frozen=True)
class AssetSpec:
    key: str
    source: str
    kind: str
    placement: str
    linkable: bool


# Order determines render order inside <head>/<body>.
_ASSET_SPECS: Tuple[AssetSpec, ...] = (
    AssetSpec("inline-style", "inline-style.css", "css", "head", False),
    AssetSpec("inline-script", "inline-script.js", "js", "head", False),
    AssetSpec("stylesheet", "site.css", "css", "head", True),
    AssetSpec("visual", "visual.js", "js", "body", True),
    AssetSpec("clipboard", "clipboard.js", "js", "body", True),
    AssetSpec("form", "form.js", "js", "body", True),
    AssetSpec("friends", "friends.js", "js", "body", True),
)


@dataclass(frozen=True)
class AssetContext:
    web_assets: Dict[str, WebAsset]
    assets_dir: str
    glyph_map_path: str


_TOOL_LOCK = threading.Lock()
_terser_cmd: Optional[List[str]] = None
_terser_checked = False
_lightningcss_cmd: Optional[List[str]] = None
_lightningcss_checked = False
_warned_missing: Set[str] = set()

_ASSET_CTX_LOCK = threading.Lock()
_ASSET_CTX_CACHE: Dict[str, AssetContext] = {}

_HTML_MINIFY_PRESERVE_RE = re.compile(
    r"(<(?:script\b[^>]*>.*?</script>|style\b[^>]*>.*?</style>|pre\b[^>]*>.*?</pre>"
    r"|span\b[^>]*\bov-s\b[^>]*>.*?</span>|a\b[^>]*\bov-s\b[^>]*>.*?</a>))",
    re.DOTALL | re.IGNORECASE,
)


def _resolve_terser() -> Optional[List[str]]:
    global _terser_cmd, _terser_checked
    with _TOOL_LOCK:
        if not _terser_checked:
            path = shutil.which("terser") or shutil.which("terser.cmd")
            _terser_cmd = [path] if path else None
            _terser_checked = True
        return _terser_cmd


def _resolve_lightningcss() -> Optional[List[str]]:
    global _lightningcss_cmd, _lightningcss_checked
    with _TOOL_LOCK:
        if not _lightningcss_checked:
            path = shutil.which("lightningcss") or shutil.which("lightningcss.cmd")
            if path:
                _lightningcss_cmd = [path]
            else:
                npx = shutil.which("npx") or shutil.which("npx.cmd")
                if npx:
                    _lightningcss_cmd = [npx, "--yes", "lightningcss-cli"]
            _lightningcss_checked = True
        return _lightningcss_cmd


def _run_minifier(
    cmd: Optional[List[str]],
    args: List[str],
    source: str,
    label: str,
    install_hint: str,
) -> str:
    if not cmd:
        with _TOOL_LOCK:
            if label not in _warned_missing:
                _warned_missing.add(label)
                print(
                    f"{label} not found, skipping minification. Install it with `{install_hint}`.",
                    file=sys.stderr,
                )
        return source
    try:
        result = subprocess.run(
            [*cmd, *args],
            check=True,
            input=source,
            text=True,
            capture_output=True,
        )
    except subprocess.CalledProcessError as e:
        stderr = e.stderr.strip() if e.stderr else ""
        detail = f": {stderr}" if stderr else "."
        print(f"{label} minification failed, using original{detail}", file=sys.stderr)
        return source

    minified = result.stdout.strip()
    if not minified:
        print(
            f"{label} minification produced empty output, using original.",
            file=sys.stderr,
        )
        return source
    return minified


def minify_js(source: str) -> str:
    return _run_minifier(
        _resolve_terser(),
        ["--compress", "passes=2", "--mangle", "--ecma", "2020"],
        source,
        "Terser",
        "npm install --global terser",
    )


def minify_css(source: str) -> str:
    return _run_minifier(
        _resolve_lightningcss(),
        ["--minify"],
        source,
        "Lightning CSS",
        "npm install --global lightningcss-cli",
    )


def minify_html_text(content: str) -> str:
    segments = _HTML_MINIFY_PRESERVE_RE.split(content)
    parts: List[str] = []
    for i, segment in enumerate(segments):
        if i % 2 == 1:
            parts.append(segment)
        else:
            segment = re.sub(r"<!--.*?-->", "", segment, flags=re.DOTALL)
            segment = re.sub(r">\s+<", "><", segment)
            segment = re.sub(r"[ \t]{2,}", " ", segment)
            segment = re.sub(r"[ \t]+\n", "\n", segment)
            segment = re.sub(r"\n{2,}", "\n", segment)
            parts.append(segment.strip())
    return "".join(parts).strip() + "\n"


def minify_html(path: str) -> None:
    if not os.path.isfile(path):
        return
    with open(path, "r", encoding="utf-8") as f:
        content = f.read()
    minified = minify_html_text(content)
    if minified == content:
        return
    with open(path, "w", encoding="utf-8") as f:
        f.write(minified)


def _remove_stale_hashed_assets(
    asset_dir: str, prefix: str, suffix: str, keep_filename: str
) -> None:
    if not os.path.isdir(asset_dir):
        return
    expected_prefix = f"{prefix}."
    for filename in os.listdir(asset_dir):
        if (
            filename == keep_filename
            or not filename.startswith(expected_prefix)
            or not filename.endswith(suffix)
        ):
            continue
        stale_path = os.path.join(asset_dir, filename)
        if os.path.isfile(stale_path):
            try:
                os.remove(stale_path)
            except FileNotFoundError:
                continue


def write_hashed_asset(
    asset_dir: str,
    prefix: str,
    suffix: str,
    content: str,
    remove_stale: bool = True,
) -> str:
    digest = hashlib.sha256(content.encode("utf-8")).hexdigest()[:ASSET_HASH_LEN]
    filename = f"{prefix}.{digest}{suffix}"
    asset_path = safe_join_child(asset_dir, filename)

    with open(asset_path, "w", encoding="utf-8") as f:
        f.write(content)

    if remove_stale:
        _remove_stale_hashed_assets(asset_dir, prefix, suffix, filename)
    return asset_path


def build_asset_ctx(root_dir: str) -> AssetContext:
    root_abs = os.path.abspath(root_dir)
    with _ASSET_CTX_LOCK:
        cached = _ASSET_CTX_CACHE.get(root_abs)
        if cached is not None and all(
            os.path.isfile(os.path.join(root_abs, asset.href.lstrip("/")))
            for asset in cached.web_assets.values()
            if asset.href
        ):
            return cached

        os.makedirs(root_abs, exist_ok=True)
        assets_dir = safe_join_child(root_abs, WEB_ASSETS_DIR_NAME)
        os.makedirs(assets_dir, exist_ok=True)

        web_assets: Dict[str, WebAsset] = {}
        for spec in _ASSET_SPECS:
            with open(os.path.join(WEB_DIR, spec.source), "r", encoding="utf-8") as f:
                source = f.read()
            minified = minify_css(source) if spec.kind == "css" else minify_js(source)
            if not spec.linkable:
                web_assets[spec.key] = WebAsset(
                    kind=spec.kind, placement=spec.placement, href=None, inline=minified
                )
                continue
            prefix = os.path.splitext(spec.source)[0]
            suffix = ".css" if spec.kind == "css" else ".min.js"
            path = write_hashed_asset(assets_dir, prefix, suffix, minified)
            web_assets[spec.key] = WebAsset(
                kind=spec.kind,
                placement=spec.placement,
                href=root_asset_href(path),
                inline=None,
            )

        ctx = AssetContext(
            web_assets=web_assets,
            assets_dir=assets_dir,
            glyph_map_path=os.path.join(assets_dir, GLOBAL_GLYPH_MAP_FILENAME),
        )
        _ASSET_CTX_CACHE[root_abs] = ctx
        return ctx
