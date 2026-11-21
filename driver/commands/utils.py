import os
import re
import html
import hashlib
import shutil
import subprocess
import sys
import json
import tempfile
from urllib.parse import parse_qs, unquote, urlparse
from dataclasses import dataclass, replace
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Set, Tuple, Union

from lxml import etree

from .revisions import list_revisions
from . import xmlutil


_typst_path: Optional[str] = None
_svgo_path: Optional[str] = None
_svgo_path_checked = False
_svgo_missing_warned = False
_lightningcss_command: Optional[List[str]] = None
_lightningcss_command_checked = False
_lightningcss_missing_warned = False
_terser_missing_warned = False
_ACTION_HREF_PREFIX = "#action="
_COND_HREF_PREFIX = "#cond="
_COPY_PREFIX = "copy:"
_INPUT_PREFIX = "input:"
_FORM_ACTION_PREFIX = "form-action:"
_CHECKBOX_PREFIX = "checkbox:"
_RADIO_PREFIX = "radio:"

# Prepended to workspace form-hooks before minification so form.cond/action/on
# register against a guaranteed-initialized form object.
_FORM_HOOKS_STUB = (
    "window.form=window.form||{_conds:{},_actions:{},_on:{}};"
    "form.cond=(i,f)=>{form._conds[i]=f};"
    "form.action=(i,f)=>{form._actions[i]=f};"
    "form.on=(i,f)=>{(form._on[i]=form._on[i]||[]).push(f)};\n"
)

_WORKSPACE_NAME_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$")
_REMOTE_IMAGE_PREFIX = "driver-remote-image://"
_ASSET_HASH_LENGTH = 6
_RAW_COPY_ID_HASH_LENGTH = 10
WORKSPACE_PUBLIC_DIR_NAME = "public"
WEB_ASSETS_DIR_NAME = "assets"
TEMP_WORK_DIR_NAME = ".tmp"
_SVGO_CONFIG_PATH = os.path.join(
    os.path.dirname(os.path.dirname(__file__)),
    "svgo.config.mjs",
)
_SVGO_GLYPH_CONFIG_PATH = os.path.join(
    os.path.dirname(os.path.dirname(__file__)),
    "svgo.glyphs.config.mjs",
)
GLOBAL_GLYPH_ASSET_FILENAME = "glyphs.svg"
GLOBAL_GLYPH_MAP_FILENAME = "glyph-map.json"
_SENTENCE_PATTERN = re.compile(r".+?(?:[.!?](?=\s|$)|$)")
_SVG_THEME_FILL_CLASS_MAP: Dict[str, str] = {
    "#fff": "theme-paper-bg",
    "#ffffff": "theme-paper-bg",
    "#000": "t",
    "#000000": "t",
    "black": "t",
    "#141414": "t",
    "#373737": "theme-quote-text",
    "#5a5a5a": "theme-caption-text",
    "#646464": "theme-muted-text",
    "#aaa": "theme-footer-text",
    "#aaaaaa": "theme-footer-text",
    "#f5f5f5": "theme-code-bg",
    "#f8f8f8": "theme-surface-bg",
    "#74747c": "theme-code-comment",
    "#198810": "theme-code-green",
    "#1d6c76": "theme-code-dark-green",
    "#16718d": "theme-code-teal",
    "#301414": "theme-code-dark-red",
    "#d73948": "theme-code-red",
    "#4b69c6": "theme-code-blue",
    "#8b41b1": "theme-code-violet",
    "#b60157": "theme-code-magenta",
}
_SVG_THEME_STROKE_CLASS_MAP: Dict[str, str] = {
    "#000": "theme-muted-stroke",
    "#000000": "theme-muted-stroke",
    "black": "theme-muted-stroke",
    "#141414": "t-stroke",
    "#646464": "theme-muted-stroke",
    "#787878": "theme-code-gutter",
}
_SVG_THEME_STYLE = """
<style id="driver-theme-style">
.theme-paper-bg { fill: var(--svg-paper-bg, #ffffff) !important; }
.t { fill: var(--svg-text, #141414) !important; }
.theme-quote-text { fill: var(--svg-quote-text, #373737) !important; }
.theme-caption-text { fill: var(--svg-caption-text, #5a5a5a) !important; }
.theme-muted-text { fill: var(--svg-muted-text, #646464) !important; }
.theme-footer-text { fill: var(--svg-footer-text, #aaaaaa) !important; }
.theme-code-bg { fill: var(--svg-code-bg, #f5f5f5) !important; }
.theme-surface-bg { fill: var(--svg-surface-bg, #f8f8f8) !important; }
.theme-code-comment { fill: var(--svg-code-comment, #74747c) !important; }
.theme-code-green { fill: var(--svg-code-green, #198810) !important; }
.theme-code-dark-green { fill: var(--svg-code-dark-green, #1d6c76) !important; }
.theme-code-teal { fill: var(--svg-code-teal, #16718d) !important; }
.theme-code-dark-red { fill: var(--svg-code-dark-red, #301414) !important; }
.theme-code-red { fill: var(--svg-code-red, #d73948) !important; }
.theme-code-blue { fill: var(--svg-code-blue, #4b69c6) !important; }
.theme-code-violet { fill: var(--svg-code-violet, #8b41b1) !important; }
.theme-code-magenta { fill: var(--svg-code-magenta, #b60157) !important; }
.t-stroke { stroke: var(--svg-text, #141414) !important; }
.theme-muted-stroke { stroke: var(--svg-muted-stroke, #646464) !important; }
.theme-code-gutter { stroke: var(--svg-code-gutter, #787878) !important; }
a:focus,
a:focus-visible {
  outline: none;
}
a:focus-visible > path {
  fill: transparent !important;
  stroke: var(--svg-focus-ring, #4b69c6) !important;
  stroke-width: 1.2 !important;
  stroke-linejoin: round;
  vector-effect: non-scaling-stroke;
}
@media (forced-colors: active) {
  a:focus-visible > path {
    stroke: CanvasText !important;
  }
}
@media (prefers-color-scheme: dark) {
  :root {
    --svg-paper-bg: #161b22;
    --svg-text: #e8ecf2;
    --svg-quote-text: #cdd3df;
    --svg-caption-text: #b4bbc9;
    --svg-muted-text: #a7b0bf;
    --svg-footer-text: #7f8897;
    --svg-code-bg: #222a34;
    --svg-surface-bg: #1b232d;
    --svg-code-comment: #8e97a6;
    --svg-code-green: #86df80;
    --svg-code-dark-green: #74cad3;
    --svg-code-teal: #6ec7d6;
    --svg-code-dark-red: #e0b8b8;
    --svg-code-red: #ff8d99;
    --svg-code-blue: #8ca8ff;
    --svg-code-violet: #c792ea;
    --svg-code-magenta: #e58bff;
    --svg-muted-stroke: #a7b0bf;
    --svg-code-gutter: #6f7783;
    --svg-focus-ring: #8ca8ff;
  }
}
a[href^="#cond="] { pointer-events: none; }
[data-cond-branch="1"] { display: var(--svg-cond-b1, none); }
</style>
""".strip()


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


# Declares every driver web asset and how it is built and placed. Order is the
# deterministic render order within <head>/<body>. Each project declares the keys
# it requires via <driver-assets> metadata; only those are linked at build time.
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
class DriverAssetContext:
    web_assets: Dict[str, WebAsset]
    global_glyph_asset_path: str
    global_glyph_map_path: str


@dataclass(frozen=True)
class TypstInputs:
    svg: Dict[str, str]
    pdf: Dict[str, str]


@dataclass(frozen=True)
class HtmlBuildConfig:
    template_path: str
    dest_dir: str
    title_format: str
    default_title: str
    asset_context: DriverAssetContext
    description: Optional[str] = None
    hidden_text_override: Optional[str] = None
    top_bar_html: str = ""
    revision_html: str = ""
    extract_title_from_pdf: bool = False
    svg_href_rewrites: Optional[Dict[str, str]] = None
    svg_name_prefix: str = "page"
    html_filename: str = "index.html"
    asset_dest_dir: Optional[str] = None
    rss_feed_path: Optional[str] = None
    og_type: str = "website"
    og_url: Optional[str] = None
    shared_glyphs: bool = True
    image_source_dir: Optional[str] = None
    site_base_url: Optional[str] = None
    form_hooks: str = ""
    primary_pages_wrapper_class: str = ""
    extra_pages_html: str = ""


@dataclass(frozen=True)
class MobileCompileConfig:
    svg_name_prefix: str = "m-page"
    paper_width: str = "125mm"
    paper_height: Optional[str] = "200mm"
    primary_wrapper_class: str = "pages pages-desktop"
    wrapper_class: str = "pages pages-mobile"


@dataclass(frozen=True)
class CompileBuildRequest:
    source_bytes: bytes
    output_dir: str
    asset_hash: str
    file_prefix: str
    html: HtmlBuildConfig
    typst_inputs: Optional[TypstInputs] = None
    mobile: Optional[MobileCompileConfig] = None


def make_inputs(
    shared_inputs: Optional[Dict[str, str]] = None,
) -> TypstInputs:
    base_inputs = {"with_driver": "true"}
    if shared_inputs:
        base_inputs.update(shared_inputs)

    svg = dict(base_inputs)
    svg["export_format"] = "svg"

    pdf = dict(base_inputs)
    pdf["export_format"] = "pdf"

    return TypstInputs(svg=svg, pdf=pdf)


def _custom_theme_color_class(light_hex: str) -> str:
    return f"tc-{light_hex.lstrip('#')}"


def _build_custom_theme_maps(
    custom_colors: List[Tuple[str, str]],
) -> Tuple[Dict[str, str], Dict[str, str]]:
    fill_map: Dict[str, str] = {}
    stroke_map: Dict[str, str] = {}
    for light, _dark in custom_colors:
        cls = _custom_theme_color_class(light)
        fill_map[light] = cls
        stroke_map[light] = f"{cls}-s"
    return fill_map, stroke_map


def _inject_svg_theme_classes(
    svg_data: str,
    custom_colors: Optional[List[Tuple[str, str]]] = None,
) -> str:
    extra_fill: Dict[str, str] = {}
    extra_stroke: Dict[str, str] = {}
    if custom_colors:
        extra_fill, extra_stroke = _build_custom_theme_maps(custom_colors)

    root = xmlutil.parse_svg(svg_data)
    for el in root.iter():
        if not isinstance(el.tag, str):
            continue
        classes: List[str] = []
        for attr_name, static_map, extra_map in (
            ("fill", _SVG_THEME_FILL_CLASS_MAP, extra_fill),
            ("stroke", _SVG_THEME_STROKE_CLASS_MAP, extra_stroke),
        ):
            raw = el.get(attr_name)
            if raw is None:
                continue
            value = raw.strip().lower()
            cls = static_map.get(value) or extra_map.get(value)
            if cls:
                classes.append(cls)
                xmlutil.del_attr_local(el, attr_name)
        if classes:
            existing = el.get("class")
            tokens = existing.split() if existing else []
            for cls in classes:
                if cls not in tokens:
                    tokens.append(cls)
            el.set("class", " ".join(tokens))
    return xmlutil.serialize_svg(root)


def _inject_svg_theme_style(
    svg_data: str,
    custom_colors: Optional[List[Tuple[str, str]]] = None,
) -> str:
    if 'id="driver-theme-style"' in svg_data:
        return svg_data
    style = _SVG_THEME_STYLE
    if custom_colors:
        class_rules: List[str] = []
        dark_vars: List[str] = []
        for light, dark in custom_colors:
            cls = _custom_theme_color_class(light)
            var = f"--svg-{cls}"
            class_rules.append(
                f".{cls} {{ fill: var({var}, {light}) !important; }}"
            )
            class_rules.append(
                f".{cls}-s {{ stroke: var({var}, {light}) !important; }}"
            )
            dark_vars.append(f"    {var}: {dark};")
        style = style.replace(
            "\n  }\n}\na[href^",
            "\n" + "\n".join(dark_vars) + "\n  }\n}\na[href^",
        )
        style = style.replace(
            "a[href^=\"#cond=\"]",
            "\n".join(class_rules) + "\na[href^=\"#cond=\"]",
        )
    return re.sub(
        r"(<svg\b[^>]*>)",
        rf"\1{style}",
        svg_data,
        count=1,
    )


def make_raw_copy_id(text: str) -> str:
    digest = hashlib.sha1(text.encode("utf-8")).hexdigest()[:_RAW_COPY_ID_HASH_LENGTH]
    return f"{digest}"


def build_page_head_title(
    page_title: str,
    site_title: str,
    page_subtitle: Optional[str] = None,
) -> str:
    title_parts = [page_title]
    if page_subtitle:
        title_parts.append(page_subtitle)
    if page_title != site_title:
        title_parts.append(site_title)
    return " - ".join(title_parts)

def _normalize_description_comparison_text(text: str) -> str:
    normalized = html.unescape(text)
    normalized = normalized.translate(
        str.maketrans(
            {
                "\u2018": "'",
                "\u2019": "'",
                "\u201c": '"',
                "\u201d": '"',
                "\u2026": "...",
            }
        )
    )
    return re.sub(r"\s+", " ", normalized).strip()


def first_desc(
    html_fragment: str,
    fallback_desc: str,
    skip_texts: Optional[List[str]] = None,
    max_length: int = 220,
) -> str:
    skip_set = {
        _normalize_description_comparison_text(text)
        for text in (skip_texts or [])
        if text and text.strip()
    }
    block_tags = ("blockquote", "p", "pre", "figcaption", "li")
    doc = xmlutil.parse_html(html_fragment)
    for el in doc.iter():
        if not (isinstance(el.tag, str) and el.tag in block_tags):
            continue
        # itertext() on the outer block already emits nested text, so
        # re-visiting an inner block would double-count it.
        ancestor = el.getparent()
        while ancestor is not None and not (
            isinstance(ancestor.tag, str) and ancestor.tag in block_tags
        ):
            ancestor = ancestor.getparent()
        if ancestor is not None:
            continue

        if el.tag == "p" and re.search(
            r"\b(?:subtitle|post-nav)\b", el.get("class") or ""
        ):
            continue

        block_text = " ".join(" ".join(el.itertext()).split())
        if not block_text:
            continue
        if re.fullmatch(r"\d{4}-\d{2}-\d{2}", block_text):
            continue
        if (
            _normalize_description_comparison_text(block_text)
            in skip_set
        ):
            continue

        sentence_match = _SENTENCE_PATTERN.search(block_text)
        if not sentence_match:
            continue

        first_sentence = sentence_match.group(0).strip()
        if not first_sentence:
            continue
        if len(first_sentence) <= max_length:
            return first_sentence
        return first_sentence[: max_length - 1].rstrip() + "…"

    return fallback_desc


def _resolve_typst_path() -> str:
    global _typst_path
    if _typst_path is None:
        _typst_path = shutil.which("typst") or shutil.which("typst.exe")
    if not _typst_path:
        print("Typst executable not found in PATH.")
        sys.exit(1)
    return _typst_path


def _resolve_svgo_path() -> Optional[str]:
    global _svgo_path, _svgo_path_checked
    if not _svgo_path_checked:
        _svgo_path = shutil.which("svgo") or shutil.which("svgo.cmd")
        _svgo_path_checked = True
    return _svgo_path


def _resolve_lightningcss_command() -> Optional[List[str]]:
    global _lightningcss_command, _lightningcss_command_checked
    if _lightningcss_command_checked:
        return _lightningcss_command

    lightningcss_path = shutil.which("lightningcss") or shutil.which("lightningcss.cmd")
    if lightningcss_path:
        _lightningcss_command = [lightningcss_path]
    else:
        npx_path = shutil.which("npx") or shutil.which("npx.cmd")
        if npx_path:
            _lightningcss_command = [npx_path, "--yes", "lightningcss-cli"]

    _lightningcss_command_checked = True
    return _lightningcss_command


def _run_svgo(svg_path: str, preserve_ids: bool = False) -> None:
    global _svgo_missing_warned

    svgo_path = _resolve_svgo_path()
    if not svgo_path:
        if not _svgo_missing_warned:
            print("SVGO not found, skipping SVG optimization.", file=sys.stderr)
            _svgo_missing_warned = True
        return

    config_path = _SVGO_GLYPH_CONFIG_PATH if preserve_ids else _SVGO_CONFIG_PATH
    command = [
        svgo_path,
        svg_path,
        "-o",
        svg_path,
        "--multipass",
        "--precision",
        "2",
    ]
    if os.path.isfile(config_path):
        command.extend(["--config", config_path])

    try:
        subprocess.run(command, check=True, capture_output=True)
    except subprocess.CalledProcessError as e:
        print(
            f"Aggressive SVGO failed for {svg_path}; retrying with defaults.",
            file=sys.stderr,
        )
        if e.stderr:
            print(e.stderr.decode("utf-8"), file=sys.stderr)

        fallback_command = [svgo_path, svg_path, "-o", svg_path]
        if preserve_ids and os.path.isfile(_SVGO_GLYPH_CONFIG_PATH):
            fallback_command.extend(["--config", _SVGO_GLYPH_CONFIG_PATH])

        try:
            subprocess.run(
                fallback_command,
                check=True,
                capture_output=True,
            )
        except subprocess.CalledProcessError as fallback_error:
            print(f"SVGO failed for {svg_path}", file=sys.stderr)
            if fallback_error.stderr:
                print(fallback_error.stderr.decode("utf-8"), file=sys.stderr)


def reset_dir(path: str) -> None:
    os.makedirs(path, exist_ok=True)
    if os.listdir(path):
        shutil.rmtree(path)
        os.makedirs(path, exist_ok=True)


def make_temp_dir(build_base: str, prefix: str) -> str:
    temp_root = os.path.join(os.path.abspath(build_base), TEMP_WORK_DIR_NAME)
    os.makedirs(temp_root, exist_ok=True)
    return tempfile.mkdtemp(prefix=prefix, dir=temp_root)


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


def build_relative_href(from_dir: str, target_path: str) -> str:
    rel_path = os.path.relpath(target_path, start=from_dir).replace("\\", "/")
    if not rel_path.startswith("."):
        return f"./{rel_path}"
    return rel_path


def _build_asset_rel_path(asset_path: str) -> str:
    asset_parts = os.path.abspath(asset_path).split(os.sep)
    if WEB_ASSETS_DIR_NAME not in asset_parts:
        raise ValueError(
            f"Expected asset path inside '{WEB_ASSETS_DIR_NAME}/': {asset_path}"
        )
    asset_index = len(asset_parts) - 1 - asset_parts[::-1].index(WEB_ASSETS_DIR_NAME)
    rel_path = "/".join(part for part in asset_parts[asset_index + 1 :] if part)
    if not rel_path or rel_path == ".":
        raise ValueError(f"Invalid asset path: {asset_path}")
    if rel_path.startswith("../"):
        raise ValueError(f"Invalid asset path: {asset_path}")
    return rel_path


def build_root_asset_href(asset_path: str) -> str:
    rel_path = _build_asset_rel_path(asset_path)
    return f"/{WEB_ASSETS_DIR_NAME}/{rel_path}"


def _rebase_relative_href_for_destination(
    href: str,
    source_dir: str,
    dest_dir: str,
) -> str:
    if not href:
        return href

    parsed = urlparse(href)
    if parsed.scheme or href.startswith(("//", "/", "#")):
        return href

    normalized_source_dir = os.path.abspath(source_dir)
    normalized_dest_dir = os.path.abspath(dest_dir)
    if normalized_source_dir == normalized_dest_dir:
        return href

    joined_target = os.path.normpath(
        os.path.join(normalized_source_dir, parsed.path.replace("/", os.sep))
    )
    rebased_href = build_relative_href(normalized_dest_dir, joined_target)
    if parsed.query:
        rebased_href = f"{rebased_href}?{parsed.query}"
    if parsed.fragment:
        rebased_href = f"{rebased_href}#{parsed.fragment}"
    return rebased_href


def _remove_stale_hashed_assets(
    asset_dir: str,
    prefix: str,
    suffix: str,
    keep_filename: str,
) -> None:
    if not os.path.isdir(asset_dir):
        return

    expected_prefix = f"{prefix}."
    for filename in os.listdir(asset_dir):
        if filename == keep_filename:
            continue
        if not filename.startswith(expected_prefix) or not filename.endswith(suffix):
            continue

        stale_path = os.path.join(asset_dir, filename)
        if os.path.isfile(stale_path):
            try:
                os.remove(stale_path)
            except FileNotFoundError:
                continue


def _write_hashed_asset(
    asset_dir: str,
    prefix: str,
    suffix: str,
    content: str,
    remove_stale: bool = True,
) -> str:
    payload = content.encode("utf-8")
    digest = hashlib.sha256(payload).hexdigest()[:_ASSET_HASH_LENGTH]
    filename = f"{prefix}.{digest}{suffix}"
    asset_path = safe_join_child(asset_dir, filename)

    with open(asset_path, "w", encoding="utf-8") as f:
        f.write(content)

    if remove_stale:
        _remove_stale_hashed_assets(
            asset_dir=asset_dir,
            prefix=prefix,
            suffix=suffix,
            keep_filename=filename,
        )
    return asset_path


def _minify_js_source(source: str) -> str:
    global _terser_missing_warned

    terser_path = shutil.which("terser") or shutil.which("terser.cmd")
    if not terser_path:
        if not _terser_missing_warned:
            print(
                "Terser not found, skipping JavaScript minification. "
                "Install it with `npm install --global terser`.",
                file=sys.stderr,
            )
            _terser_missing_warned = True
        return source

    try:
        result = subprocess.run(
            [terser_path, "--compress", "passes=2", "--mangle", "--ecma", "2020"],
            check=True,
            input=source,
            text=True,
            capture_output=True,
        )
    except subprocess.CalledProcessError as e:
        stderr = e.stderr.strip() if e.stderr else ""
        if stderr:
            print(
                f"Terser minification failed, using original JavaScript: {stderr}",
                file=sys.stderr,
            )
        else:
            print(
                "Terser minification failed, using original JavaScript.",
                file=sys.stderr,
            )
        return source

    minified = result.stdout.strip()
    if not minified:
        print(
            "Terser minification produced empty output, using original JavaScript.",
            file=sys.stderr,
        )
        return source

    return minified


def _minify_css_source(source: str) -> str:
    global _lightningcss_missing_warned

    css_cmd = _resolve_lightningcss_command()
    if not css_cmd:
        if not _lightningcss_missing_warned:
            print(
                "Lightning CSS not found, skipping CSS minification. "
                "Install it with `npm install --global lightningcss-cli`.",
                file=sys.stderr,
            )
            _lightningcss_missing_warned = True
        return source

    try:
        result = subprocess.run(
            [*css_cmd, "--minify"],
            check=True,
            input=source,
            text=True,
            capture_output=True,
        )
    except subprocess.CalledProcessError as e:
        stderr = e.stderr.strip() if e.stderr else ""
        if stderr:
            print(
                f"Lightning CSS minification failed, using original CSS: {stderr}",
                file=sys.stderr,
            )
        else:
            print(
                "Lightning CSS minification failed, using original CSS.",
                file=sys.stderr,
            )
        return source

    minified = result.stdout.strip()
    if not minified:
        print(
            "Lightning CSS minification produced empty output, using original CSS.",
            file=sys.stderr,
        )
        return source

    return minified


_HTML_MINIFY_PRESERVE_PATTERN = re.compile(
    r"(<(?:script|style|pre)\b[^>]*>.*?</(?:script|style|pre)>)",
    re.DOTALL | re.IGNORECASE,
)


def _minify_html_text(content: str) -> str:
    segments = _HTML_MINIFY_PRESERVE_PATTERN.split(content)
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
    minified = _minify_html_text(content)
    if minified == content:
        return
    with open(path, "w", encoding="utf-8") as f:
        f.write(minified)


def build_web_assets(driver_dir: str, webroot_dir: str) -> Dict[str, WebAsset]:
    if not os.path.isdir(driver_dir):
        raise FileNotFoundError(driver_dir)

    os.makedirs(webroot_dir, exist_ok=True)
    assets_dir = safe_join_child(webroot_dir, WEB_ASSETS_DIR_NAME)
    os.makedirs(assets_dir, exist_ok=True)

    web_assets: Dict[str, WebAsset] = {}
    for spec in _ASSET_SPECS:
        with open(os.path.join(driver_dir, spec.source), "r", encoding="utf-8") as f:
            source = f.read()
        minified = (
            _minify_css_source(source)
            if spec.kind == "css"
            else _minify_js_source(source)
        )
        if not spec.linkable:
            web_assets[spec.key] = WebAsset(
                kind=spec.kind, placement=spec.placement, href=None, inline=minified
            )
            continue
        prefix = os.path.splitext(spec.source)[0]
        suffix = ".css" if spec.kind == "css" else ".min.js"
        path = _write_hashed_asset(
            asset_dir=assets_dir, prefix=prefix, suffix=suffix, content=minified
        )
        web_assets[spec.key] = WebAsset(
            kind=spec.kind,
            placement=spec.placement,
            href=build_root_asset_href(path),
            inline=None,
        )
    return web_assets


def run_typst_compile(
    source_bytes: bytes,
    output_path: str,
    export_format: str,
    inputs: Optional[Dict[str, str]] = None,
    creation_timestamp: Optional[str] = None,
) -> None:
    command = [
        _resolve_typst_path(),
        "compile",
        "-",
        output_path,
        "--format",
        export_format,
    ]
    if inputs:
        for key, value in inputs.items():
            command.extend(["--input", f"{key}={value}"])
    if creation_timestamp:
        command.extend(["--creation-timestamp", creation_timestamp])

    try:
        subprocess.run(
            command,
            check=True,
            input=source_bytes,
        )
    except subprocess.CalledProcessError as e:
        print(
            f"Typst compilation failed for {output_path} (exit code {e.returncode})",
            file=sys.stderr,
        )
        if e.stderr:
            print(e.stderr.decode("utf-8"), file=sys.stderr)
        elif e.output:
            print(e.output.decode("utf-8"), file=sys.stderr)
        raise RuntimeError(f"Typst compilation failed. Exit code {e.returncode}") from e


def _resolve_pdf_creation_timestamp(inputs: Optional[Dict[str, str]]) -> Optional[str]:
    if not inputs:
        return None

    raw_date = inputs.get("edited_date") or inputs.get("publish_date")
    if not raw_date:
        return None

    try:
        parsed_date = datetime.strptime(raw_date, "%Y-%m-%d")
    except ValueError:
        return None

    return str(int(parsed_date.replace(tzinfo=timezone.utc).timestamp()))


def _flatten_query_text(node: Any, skip_cond_links: bool = False) -> str:
    if isinstance(node, str):
        return node
    if isinstance(node, list):
        return "".join(_flatten_query_text(item, skip_cond_links) for item in node)
    if isinstance(node, dict):
        func = node.get("func")
        if func == "space":
            return " "
        if func == "linebreak":
            return "\n"
        if func == "smartquote":
            return '"' if node.get("double") else "'"
        if skip_cond_links and func == "link":
            dest = node.get("dest", "")
            if isinstance(dest, str) and dest.startswith(_COND_HREF_PREFIX):
                return ""
            return _flatten_query_text(node.get("body", ""), skip_cond_links)

        parts: List[str] = []
        text = node.get("text")
        if isinstance(text, str):
            parts.append(text)
        for key in ("body", "children", "child", "value", "values", "content"):
            if key in node:
                parts.append(_flatten_query_text(node[key], skip_cond_links))
        if not parts:
            for key, value in node.items():
                if key in ("func", "text"):
                    continue
                if isinstance(value, (dict, list)):
                    parts.append(_flatten_query_text(value, skip_cond_links))
        return "".join(parts)
    return ""

def _run_typst_query_json(
    command: List[str],
    source_bytes: Optional[bytes] = None,
    cwd: Optional[str] = None,
) -> List[Dict[str, Any]]:
    try:
        result = subprocess.run(
            command,
            check=True,
            capture_output=True,
            cwd=cwd,
            input=source_bytes,
        )
    except subprocess.CalledProcessError as e:
        stderr = e.stderr.decode("utf-8").strip() if e.stderr else ""
        if stderr:
            raise RuntimeError(f"Failed to query Typst source: {stderr}") from e
        raise RuntimeError(f"Failed to query Typst source: {e}") from e

    raw = result.stdout.decode("utf-8").strip()
    if not raw:
        raise RuntimeError("Typst query produced empty output.")

    try:
        data = json.loads(raw)
    except json.JSONDecodeError as e:
        raise RuntimeError(f"Failed to parse Typst query output: {e}") from e

    return data

def _extract_typst_table_rows(
    table_payload: Dict[str, Any],
) -> List[List[Dict[str, Any]]]:
    columns_raw = table_payload["columns"]
    column_count = len(columns_raw) if isinstance(columns_raw, list) else int(columns_raw)

    children = table_payload["children"]

    cell_nodes: List[Dict[str, Any]] = []
    for child in children:
        if child.get("func") != "cell":
            continue

        body = child["body"]
        text = re.sub(r"\s+", " ", _flatten_query_text(body)).strip()
        is_inline_code = bool(
            isinstance(body, dict)
            and body.get("func") == "raw"
            and not bool(body.get("block", False))
        )

        colspan = child.get("colspan", 1)
        rowspan = child.get("rowspan", 1)

        cell_nodes.append(
            {
                "text": text,
                "is_inline_code": is_inline_code,
                "colspan": colspan,
                "rowspan": rowspan,
            }
        )

    rows: List[List[Dict[str, Any]]] = [[]]
    occupied: Dict[Tuple[int, int], bool] = {}
    row_index = 0
    col_index = 0

    def advance_to_free_slot(start_row: int, start_col: int) -> Tuple[int, int]:
        current_row = start_row
        current_col = start_col
        while True:
            while current_col < column_count and occupied.get(
                (current_row, current_col), False
            ):
                current_col += 1
            if current_col < column_count:
                return current_row, current_col
            current_row += 1
            current_col = 0
            while current_row >= len(rows):
                rows.append([])

    for cell in cell_nodes:
        row_index, col_index = advance_to_free_slot(row_index, col_index)
        while row_index >= len(rows):
            rows.append([])

        rowspan = int(cell["rowspan"])
        colspan = int(cell["colspan"])

        rows[row_index].append(cell)
        for row_offset in range(rowspan):
            target_row = row_index + row_offset
            while target_row >= len(rows):
                rows.append([])
            for col_offset in range(colspan):
                target_col = col_index + col_offset
                slot_key = (target_row, target_col)
                occupied[slot_key] = True

        col_index += colspan

    if occupied:
        row_count = max(row for row, _ in occupied) + 1
        while len(rows) < row_count:
            rows.append([])
        if len(rows) > row_count:
            rows = rows[:row_count]

    return rows


def _query_nodes_from_source(
    source_content: Union[str, bytes],
    query_root: str,
    selector: str,
    inputs: Optional[Dict[str, str]] = None,
) -> List[Dict[str, Any]]:
    source_bytes = (
        source_content.encode("utf-8")
        if isinstance(source_content, str)
        else source_content
    )
    command = [
        _resolve_typst_path(),
        "query",
        "-",
        selector,
        "--root",
        os.path.abspath(query_root),
        "--format",
        "json",
    ]
    if inputs:
        for key, value in inputs.items():
            command.extend(["--input", f"{key}={value}"])

    return _run_typst_query_json(
        command=command,
        source_bytes=source_bytes,
        cwd=query_root,
    )


_TITLE_SUBTITLE_SELECTOR = "<title-subtitle>"


def _declared_text(value: Any) -> Optional[str]:
    if value is None:
        return None
    text = re.sub(r"\s+", " ", _flatten_query_text(value)).strip()
    return text or None


def _parse_title_subtitle(
    nodes: List[Dict[str, Any]],
) -> Tuple[Optional[str], Optional[str]]:
    value = nodes[0]["value"] if nodes else {}
    return _declared_text(value.get("title")), _declared_text(value.get("subtitle"))


def _fetch_title_subtitle(
    query_source: str, query_root: str, missing_context: str
) -> Tuple[str, Optional[str]]:
    title, subtitle = _parse_title_subtitle(
        _query_nodes_from_source(
            query_source, query_root=query_root, selector=_TITLE_SUBTITLE_SELECTOR
        )
    )
    if title is None:
        raise RuntimeError(f"Missing required title declaration in {missing_context}.")
    return title, subtitle


def query_title_subtitle(typ_path: str, query_root: str) -> Tuple[str, Optional[str]]:
    """Return ``(title, subtitle)`` declared in a self-contained Typst source
    file (one whose imports resolve relative to its own location, e.g. a
    workspace post). ``title`` is required; ``subtitle`` is ``None`` if absent.
    """
    rel = os.path.relpath(
        os.path.abspath(typ_path), os.path.abspath(query_root)
    ).replace("\\", "/")
    return _fetch_title_subtitle(
        query_source=(
            f'#import "{rel}" as _src\n'
            f"#let _exports = dictionary(_src)\n"
            f'#metadata((title: _exports.at("title", default: none), '
            f'subtitle: _exports.at("subtitle", default: none))) '
            f"{_TITLE_SUBTITLE_SELECTOR}"
        ),
        query_root=query_root,
        missing_context=typ_path,
    )


def query_title_subtitle_from_source(
    source: str, query_root: str
) -> Tuple[str, Optional[str]]:
    """Return ``(title, subtitle)`` from a Typst document source compiled from
    the project root (imports relative to the root), e.g. the friends/content
    templates. ``title`` is required; ``subtitle`` is ``None`` if absent.
    """
    return _fetch_title_subtitle(
        query_source=(
            source.rstrip()
            + "\n"
            + f"#metadata((title: title, subtitle: subtitle)) {_TITLE_SUBTITLE_SELECTOR}"
        ),
        query_root=query_root,
        missing_context="Typst source",
    )


def _collect_typ_files(paths: List[str]) -> List[Tuple[str, str]]:
    typ_files: Dict[str, str] = {}

    for path_index, path in enumerate(paths):
        if not path:
            continue
        abs_path = os.path.abspath(path)
        logical_prefix = f"{path_index}:"

        if os.path.isfile(abs_path) and abs_path.endswith(".typ"):
            logical_path = f"{logical_prefix}{os.path.basename(abs_path)}"
            typ_files.setdefault(logical_path, abs_path)
            continue

        if os.path.isdir(abs_path):
            for root, _, files in os.walk(abs_path):
                for name in files:
                    if not name.endswith(".typ"):
                        continue
                    file_path = os.path.abspath(os.path.join(root, name))
                    rel_path = os.path.relpath(file_path, start=abs_path).replace(
                        "\\", "/"
                    )
                    logical_path = f"{logical_prefix}{rel_path}"
                    typ_files.setdefault(logical_path, file_path)

    return sorted(typ_files.items(), key=lambda item: item[0])


def _update_hash_with_typ_files(hasher: "hashlib._Hash", paths: List[str]) -> None:
    typ_files = _collect_typ_files(paths)
    for logical_path, file_path in typ_files:
        hasher.update(logical_path.encode("utf-8"))
        with open(file_path, "rb") as f:
            hasher.update(f.read())


def sources_hash(paths: List[str], length: int = _ASSET_HASH_LENGTH) -> str:
    hasher = hashlib.sha256()
    _update_hash_with_typ_files(hasher, paths)
    return hasher.hexdigest()[:length]


def hash_text_with_paths(
    text: str,
    paths: List[str],
    length: int = _ASSET_HASH_LENGTH,
) -> str:
    hasher = hashlib.sha256()
    hasher.update(text.encode("utf-8"))
    _update_hash_with_typ_files(hasher, paths)

    return hasher.hexdigest()[:length]


def extract_pdf_text(
    pdf_path: str,
    extract_title: bool = False,
    default_title: str = "",
) -> Tuple[str, str]:
    title: str = default_title
    hidden_text: str = ""
    import pymupdf
    from typing import cast, Any

    if os.path.exists(pdf_path):
        doc = pymupdf.open(pdf_path)
        doc_any = cast(Any, doc)
        raw_text = "".join(page.get_text() for page in doc_any)
        if extract_title:
            lines = raw_text.strip().split("\n")
            if lines:
                title = lines[0].strip()
        hidden_text = f'<div class="sr-only">\n{html.escape(raw_text)}\n</div>'
    return title, hidden_text

def _extract_first_glyph_symbol_id(glyph_asset_path: str) -> Optional[str]:
    if not os.path.isfile(glyph_asset_path):
        return None

    try:
        with open(glyph_asset_path, "r", encoding="utf-8") as f:
            root = xmlutil.parse_svg(f.read())
    except (OSError, UnicodeDecodeError):
        return None

    symbol = next(xmlutil.iter_local(root, "symbol"), None)
    if symbol is None:
        return None

    symbol_id = (symbol.get("id") or "").strip()
    return symbol_id or None


def _build_glyph_preload_svg(glyph_src: str, glyph_symbol_id: str) -> str:
    glyph_ref = f"{glyph_src}#{glyph_symbol_id}"
    return (
        '<svg data-glyph-preload="true" aria-hidden="true" width="0" height="0" '
        'style="position:absolute;opacity:0;pointer-events:none">'
        f'<use href="{html.escape(glyph_ref, quote=True)}"></use>'
        "</svg>"
    )


def rewrite_glyph_preload(html_path: str, glyph_asset_path: str) -> bool:
    if not os.path.isfile(html_path):
        return False

    glyph_name = os.path.basename(glyph_asset_path)
    if not glyph_name:
        return False

    with open(html_path, "r", encoding="utf-8") as f:
        html_content = f.read()

    glyph_src = build_root_asset_href(glyph_asset_path)
    glyph_symbol_id = _extract_first_glyph_symbol_id(glyph_asset_path)
    if glyph_symbol_id is None:
        return False
    glyph_ref = f"{glyph_src}#{glyph_symbol_id}"

    doc = xmlutil.parse_html(html_content)
    preload = next(
        (e for e in doc.iter()
         if isinstance(e.tag, str) and e.get("data-glyph-preload") == "true"),
        None,
    )
    if preload is not None:
        use = next(xmlutil.iter_local(preload, "use"), None)
        if use is None or use.get("href") == glyph_ref:
            return False
        use.set("href", glyph_ref)
    else:
        body = next(
            (e for e in doc.iter() if isinstance(e.tag, str) and e.tag == "body"),
            None,
        )
        if body is None:
            return False
        preload_el = xmlutil.parse_html(
            _build_glyph_preload_svg(glyph_src, glyph_symbol_id)
        )
        preload_el.tail = "\n" + (body.text or "")
        body.text = "\n"
        body.insert(0, preload_el)

    with open(html_path, "w", encoding="utf-8") as f:
        f.write(xmlutil.serialize_html(doc))
    return True


def _is_generated_svg(filename: str, svg_name_prefix: str = "page") -> bool:
    return bool(
        re.match(
            rf"^{re.escape(svg_name_prefix)}\d+(?:\.[^.]+)?\.svg$",
            filename,
        )
    )


def _load_global_glyph_registry(map_path: str) -> Dict[str, Any]:
    default_registry: Dict[str, Any] = {
        "next_short_index": 1,
        "symbols": {},
        "fingerprint_to_short": {},
        "typst_id_to_short": {},
    }

    if not os.path.isfile(map_path):
        return default_registry

    with open(map_path, "r", encoding="utf-8") as f:
        data = json.load(f)

    typst_id_to_short: Dict[str, str] = {}
    for raw_typst_id, short_id in data.get("typst_id_to_short", {}).items():
        if "::" in raw_typst_id:
            _, typst_id = raw_typst_id.split("::", 1)
        else:
            typst_id = raw_typst_id
        if typst_id in typst_id_to_short:
            continue
        typst_id_to_short[typst_id] = short_id

    return {
        "next_short_index": data.get("next_short_index", 1),
        "symbols": data.get("symbols", {}),
        "fingerprint_to_short": data.get("fingerprint_to_short", {}),
        "typst_id_to_short": typst_id_to_short,
    }


def _allocate_next_short_id(registry: Dict[str, Any]) -> str:
    index = registry["next_short_index"]
    registry["next_short_index"] = index + 1
    digits = "0123456789abcdefghijklmnopqrstuvwxyz"
    parts: List[str] = []
    while index:
        index, remainder = divmod(index, 36)
        parts.append(digits[remainder])
    return f"g{''.join(reversed(parts))}"


def _resolve_global_short_id(
    registry: Dict[str, Any],
    typst_id: str,
    fingerprint: str,
    attrs_without_id: str,
    body: str,
) -> str:
    symbols: Dict[str, Dict[str, str]] = registry["symbols"]
    typst_id_to_short: Dict[str, str] = registry["typst_id_to_short"]
    fingerprint_to_short: Dict[str, str] = registry["fingerprint_to_short"]

    chosen_short_id: Optional[str] = None

    mapped_short_id: Optional[str]
    if typst_id in typst_id_to_short:
        mapped_short_id = typst_id_to_short[typst_id]
    else:
        mapped_short_id = None
    if mapped_short_id:
        mapped_symbol = symbols[mapped_short_id] if mapped_short_id in symbols else None
        if mapped_symbol and mapped_symbol["fingerprint"] == fingerprint:
            chosen_short_id = mapped_short_id

    if chosen_short_id is None:
        mapped_short_id = (
            fingerprint_to_short[fingerprint]
            if fingerprint in fingerprint_to_short
            else None
        )
        if mapped_short_id and mapped_short_id in symbols:
            chosen_short_id = mapped_short_id

    if chosen_short_id is None:
        chosen_short_id = _allocate_next_short_id(registry)

    existing_symbol = symbols[chosen_short_id] if chosen_short_id in symbols else None
    if existing_symbol is None:
        symbols[chosen_short_id] = {
            "fingerprint": fingerprint,
            "attrs": attrs_without_id,
            "body": body,
        }
    elif existing_symbol["fingerprint"] != fingerprint:
        chosen_short_id = _allocate_next_short_id(registry)
        symbols[chosen_short_id] = {
            "fingerprint": fingerprint,
            "attrs": attrs_without_id,
            "body": body,
        }

    typst_id_to_short[typst_id] = chosen_short_id
    fingerprint_to_short[fingerprint] = chosen_short_id

    return chosen_short_id


def _render_glyph_svg(symbols: Dict[str, Dict[str, str]]) -> str:
    ordered_short_ids = sorted(symbols.keys(), key=lambda sid: int(sid[1:], 36))
    glyph_symbols: List[str] = []
    for short_id in ordered_short_ids:
        symbol = symbols[short_id]
        attrs = symbol["attrs"]
        body = symbol["body"]
        glyph_symbols.append(f'<symbol id="{short_id}"{attrs}>{body}</symbol>')

    return (
        '<svg xmlns="http://www.w3.org/2000/svg" '
        'xmlns:xlink="http://www.w3.org/1999/xlink">'
        f"<defs>{''.join(glyph_symbols)}</defs>"
        "</svg>"
    )


def _extract_svg_symbols(
    svg_data: str,
) -> Optional[List[Tuple[str, str, str]]]:
    root = xmlutil.parse_svg(svg_data)
    defs = next(xmlutil.iter_local(root, "defs"), None)
    if defs is None:
        return None

    symbols: List[Tuple[str, str, str]] = []
    for sym in xmlutil.iter_local(defs, "symbol"):
        local_id = sym.get("id")
        if local_id is None:
            continue
        attrs_without_id = "".join(
            f' {k}="{v}"' for k, v in sym.attrib.items() if k != "id"
        )
        body = xmlutil.inner_xml(sym)
        symbols.append((local_id, attrs_without_id, body))

    return symbols


def _extract_wrapped_global_short_id(symbol_body: str) -> Optional[str]:
    use = xmlutil.parse_svg(symbol_body)
    if not (isinstance(use.tag, str) and xmlutil.local(use.tag) == "use"):
        return None

    href_value = xmlutil.attr_local(use, "href")
    if not href_value or "#" not in href_value:
        return None

    href_path, short_id = href_value.rsplit("#", 1)
    if not short_id or not href_path:
        return None

    href_basename = os.path.basename(href_path.split("?", 1)[0])
    if href_basename != GLOBAL_GLYPH_ASSET_FILENAME and not re.fullmatch(
        r"glyphs\.[0-9a-f]{6}\.svg",
        href_basename,
    ):
        return None

    return short_id


def _extract_and_rewrite_shared_svg_glyphs(
    svg_paths: List[str],
    global_glyph_asset_path: str,
    global_glyph_map_path: str
) -> Optional[str]:
    if not svg_paths:
        return None

    registry = _load_global_glyph_registry(global_glyph_map_path)

    page_symbol_maps: List[Tuple[str, str, List[Tuple[str, str]]]] = []

    for svg_path in svg_paths:
        with open(svg_path, "r", encoding="utf-8") as svg_file:
            svg_data = svg_file.read()

        symbols = _extract_svg_symbols(svg_data)
        if not symbols:
            continue

        local_symbol_map: List[Tuple[str, str]] = []
        for local_id, attrs_without_id, body in symbols:
            wrapped_short_id = _extract_wrapped_global_short_id(body)
            if wrapped_short_id:
                existing_symbols: Dict[str, Dict[str, str]] = registry["symbols"]
                if wrapped_short_id in existing_symbols:
                    local_symbol_map.append((local_id, wrapped_short_id))
                    continue

            fingerprint_digest = hashlib.sha1(
                f"{' '.join(attrs_without_id.split())}\n{body.strip()}".encode("utf-8")
            ).hexdigest()

            short_id = _resolve_global_short_id(
                registry,
                typst_id=local_id,
                fingerprint=fingerprint_digest,
                attrs_without_id=attrs_without_id,
                body=body,
            )
            local_symbol_map.append((local_id, short_id))

        page_symbol_maps.append((svg_path, svg_data, local_symbol_map))

    if not page_symbol_maps:
        return None

    symbols = registry["symbols"]
    if not symbols:
        return None
    glyph_svg = _render_glyph_svg(symbols)
    glyph_asset_path = _write_hashed_asset(
        asset_dir=os.path.dirname(os.path.abspath(global_glyph_asset_path)),
        prefix="glyphs",
        suffix=".svg",
        content=glyph_svg,
        remove_stale=False,
    )
    os.makedirs(os.path.dirname(global_glyph_map_path), exist_ok=True)
    with open(global_glyph_map_path, "w", encoding="utf-8") as f:
        json.dump(registry, f, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        f.write("\n")

    for svg_path, svg_data, local_symbol_map in page_symbol_maps:
        glyph_href = build_root_asset_href(glyph_asset_path)
        short_by_id: Dict[str, str] = {}
        for local_id, short_id in local_symbol_map:
            short_by_id.setdefault(local_id, short_id)

        root = xmlutil.parse_svg(svg_data)
        defs = next(xmlutil.iter_local(root, "defs"), None)
        if defs is not None:
            for sym in list(xmlutil.iter_local(defs, "symbol")):
                short_id = short_by_id.get(sym.get("id") or "")
                if short_id is None:
                    continue
                for child in list(sym):
                    sym.remove(child)
                sym.text = None
                use = etree.SubElement(sym, xmlutil.namespaced(sym, "use"))
                use.set("href", f"{glyph_href}#{short_id}")

        with open(svg_path, "w", encoding="utf-8") as svg_file:
            svg_file.write(xmlutil.serialize_svg(root))

    return glyph_asset_path


def apply_glyph_map(
    root_dir: str,
    target_dirs: List[str]
) -> Optional[str]:
    assets_dir = safe_join_child(root_dir, WEB_ASSETS_DIR_NAME)
    glyph_asset_path = os.path.join(assets_dir, GLOBAL_GLYPH_ASSET_FILENAME)
    glyph_map_path = os.path.join(assets_dir, GLOBAL_GLYPH_MAP_FILENAME)

    svg_paths: List[str] = []
    for directory in target_dirs:
        if not os.path.isdir(directory):
            continue
        for filename in sorted(os.listdir(directory)):
            if (
                _is_generated_svg(filename, "page")
                or _is_generated_svg(filename, "m-page")
                or _is_generated_svg(filename, "meta-page")
                or _is_generated_svg(filename, "m-meta-page")
                or _is_generated_svg(filename, "friends")
                or _is_generated_svg(filename, "m-friends")
            ):
                svg_paths.append(os.path.join(directory, filename))

    if not svg_paths:
        return None

    glyph_result = _extract_and_rewrite_shared_svg_glyphs(
        svg_paths,
        global_glyph_asset_path=glyph_asset_path,
        global_glyph_map_path=glyph_map_path
    )

    for svg_path in svg_paths:
        _optimize_svg_with_normalized_href(svg_path)
    if glyph_result:
        _optimize_svg_with_normalized_href(glyph_result, preserve_ids=True)

    return glyph_result


def _is_root_asset_referenced(root_dir: str, asset_filename: str) -> bool:
    assets_dir = safe_join_child(root_dir, WEB_ASSETS_DIR_NAME)
    assets_dir_abs = os.path.abspath(assets_dir)
    needle = f"assets/{asset_filename}"

    for current_dir, _, files in os.walk(root_dir):
        for filename in files:
            if not filename.endswith((".html", ".svg")):
                continue

            file_path = os.path.join(current_dir, filename)
            file_abs = os.path.abspath(file_path)
            if file_abs.startswith(assets_dir_abs + os.sep):
                continue

            with open(file_path, "r", encoding="utf-8") as f:
                content = f.read()

            if needle in content:
                return True

    return False


def clean_glyph_assets(
    root_dir: str,
    target_dirs: List[str]
) -> int:
    cleaned_count = 0

    root_assets_dir = safe_join_child(root_dir, WEB_ASSETS_DIR_NAME)
    asset_dirs: Set[str] = set()
    asset_dirs.add(root_assets_dir)
    for directory in target_dirs:
        if not os.path.isdir(directory):
            continue
        asset_dirs.add(os.path.join(directory, WEB_ASSETS_DIR_NAME))

    for asset_dir in sorted(asset_dirs):
        if not os.path.isdir(asset_dir):
            continue

        normalized_asset_dir = os.path.abspath(asset_dir)
        is_root_assets_dir = normalized_asset_dir == os.path.abspath(root_assets_dir)

        for filename in os.listdir(asset_dir):
            is_stale_global_glyph = (
                is_root_assets_dir
                and bool(re.fullmatch(r"glyphs\.[0-9a-f]{6}\.svg", filename))
                and not _is_root_asset_referenced(root_dir, filename)
            )
            if not is_stale_global_glyph:
                continue

            target_path = os.path.join(asset_dir, filename)
            if not os.path.isfile(target_path):
                continue

            os.remove(target_path)
            cleaned_count += 1

        if normalized_asset_dir != os.path.abspath(root_assets_dir):
            try:
                if not os.listdir(asset_dir):
                    os.rmdir(asset_dir)
            except OSError:
                pass

    return cleaned_count


def _normalize_svg_href_attributes(svg_content: str) -> str:
    root = xmlutil.parse_svg(svg_content)
    xlink_key = f"{{{xmlutil.XLINK_NS}}}href"
    for el in root.iter():
        if not isinstance(el.tag, str) or xlink_key not in el.attrib:
            continue
        if "href" in el.attrib:
            del el.attrib[xlink_key]
        else:
            items = list(el.attrib.items())
            el.attrib.clear()
            for key, val in items:
                el.attrib["href" if key == xlink_key else key] = val
    return xmlutil.serialize_svg(root)


def _strip_typst_classes(svg_content: str) -> str:
    root = xmlutil.parse_svg(svg_content)
    for el in root.iter():
        if not isinstance(el.tag, str):
            continue
        class_value = el.get("class")
        if class_value is None:
            continue
        kept = [c for c in class_value.split() if not c.startswith("typst-")]
        if kept:
            el.set("class", " ".join(kept))
        else:
            del el.attrib["class"]
    return xmlutil.serialize_svg(root)


def _prepare_anchor_hrefs_for_svgo(svg_content: str) -> str:
    root = xmlutil.parse_svg(svg_content)
    xlink_key = f"{{{xmlutil.XLINK_NS}}}href"
    inserted = False
    for el in xmlutil.iter_local(root, "a"):
        if xlink_key in el.attrib or "href" not in el.attrib:
            continue
        el.attrib[xlink_key] = el.attrib.pop("href")
        inserted = True
    if inserted:
        root = xmlutil.declare_namespace(root, "xlink", xmlutil.XLINK_NS)
    return xmlutil.serialize_svg(root)


def _extract_anchor_href(el) -> Optional[str]:
    href = xmlutil.attr_local(el, "href")
    if href is None:
        return None
    href = href.strip()
    return href if href else None


def _parse_action_payload(action_payload: str) -> Tuple[str, Dict[str, str]]:
    segments = [
        stripped
        for segment in action_payload.split("|")
        if (stripped := segment.strip())
    ]
    if not segments:
        return "", {}

    action_token = segments[0]
    metadata: Dict[str, str] = {}
    for segment in segments[1:]:
        separator_index = segment.find(":")
        if separator_index <= 0:
            continue
        key = segment[:separator_index].strip().lower()
        value = segment[separator_index + 1 :].strip()
        if not key or not value:
            continue
        metadata[key] = value
    return action_token, metadata


def _parse_svg_action_href(href: Optional[str]) -> Tuple[Optional[str], Dict[str, str]]:
    if href is None:
        return None, {}

    stripped_href = href.strip()
    if not stripped_href.startswith(_ACTION_HREF_PREFIX):
        return None, {}

    action_payload = stripped_href[len(_ACTION_HREF_PREFIX) :]
    try:
        action_payload = unquote(action_payload)
    except Exception:
        pass
    return _parse_action_payload(action_payload)


def _action_token_role(action_token: str) -> Optional[str]:
    if action_token in ("theme", "layout") or action_token.startswith(_COPY_PREFIX):
        return "button"
    if action_token.startswith(_INPUT_PREFIX):
        return "textbox"
    if action_token.startswith(_FORM_ACTION_PREFIX):
        return "button"
    if action_token.startswith(_CHECKBOX_PREFIX):
        return "checkbox"
    if action_token.startswith(_RADIO_PREFIX):
        return "radio"
    return None


def _action_token_label(action_token: str) -> str:
    if action_token == "theme":
        return "Theme"
    if action_token.startswith(_COPY_PREFIX):
        return "Copy"
    if action_token.startswith(_INPUT_PREFIX):
        return "Input"
    return "Action"


def _infer_svg_anchor_attrs(
    action_token: Optional[str],
    action_metadata: Optional[Dict[str, Dict[str, str]]] = None,
) -> Tuple[Optional[str], Optional[str]]:
    if action_token is None:
        return None, None
    meta = (action_metadata or {}).get(action_token, {})
    role = meta.get("role") or _action_token_role(action_token)
    tabindex = meta.get("tabindex") or None
    return role, tabindex


_INDEX_HTML_SEGMENT_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*$")
_DATE_PATH_SEGMENT_PATTERN = re.compile(r"^\d{4}-\d{2}-\d{2}$")
_INDEX_LABEL_LOWERCASE_WORDS = {
    "a",
    "an",
    "and",
    "as",
    "at",
    "by",
    "for",
    "in",
    "of",
    "on",
    "or",
    "the",
    "to",
    "with",
}


def _humanize_index_target_segment(segment: str) -> Optional[str]:
    normalized = segment.strip()
    if not normalized or normalized in {".", ".."}:
        return None
    if _DATE_PATH_SEGMENT_PATTERN.fullmatch(normalized):
        return None
    if not _INDEX_HTML_SEGMENT_PATTERN.fullmatch(normalized):
        return None

    words = [part for part in re.split(r"[._-]+", normalized) if part]
    if not words:
        return None

    label_parts: List[str] = []
    for index, word in enumerate(words):
        if word.isupper() or word.isdigit():
            label_parts.append(word)
            continue
        if any(char.isdigit() for char in word):
            if word[0].isalpha():
                label_parts.append(word[0].upper() + word[1:])
            else:
                label_parts.append(word)
            continue

        lower_word = word.lower()
        if index > 0 and lower_word in _INDEX_LABEL_LOWERCASE_WORDS:
            label_parts.append(lower_word)
            continue

        label_parts.append(lower_word.capitalize())

    label = " ".join(label_parts).strip()
    return label if label else None


def _infer_index_href_label(href: str) -> Optional[str]:
    parsed_href = urlparse(href)
    path = parsed_href.path or href
    try:
        path = unquote(path)
    except Exception:
        pass

    normalized_path = path.replace("\\", "/").strip()
    if not normalized_path:
        return None

    segments = [
        segment for segment in normalized_path.split("/") if segment and segment != "."
    ]
    if not segments or segments[-1].lower() != "index.html":
        return None

    if len(segments) >= 2:
        target_label = _humanize_index_target_segment(segments[-2])
        if target_label is not None:
            return target_label
    return "Contents"


_HTML_TITLE_CACHE: Dict[str, Optional[str]] = {}


def _read_html_title(html_path: str) -> Optional[str]:
    if html_path in _HTML_TITLE_CACHE:
        return _HTML_TITLE_CACHE[html_path]

    if not os.path.isfile(html_path):
        _HTML_TITLE_CACHE[html_path] = None
        return None

    with open(html_path, "r", encoding="utf-8") as html_file:
        doc = xmlutil.parse_html(html_file.read())

    target = next(
        (e for e in doc.iter() if isinstance(e.tag, str) and e.tag == "h1"), None
    )
    if target is None:
        target = next(
            (e for e in doc.iter() if isinstance(e.tag, str) and e.tag == "title"),
            None,
        )
    if target is None:
        _HTML_TITLE_CACHE[html_path] = None
        return None

    title = " ".join("".join(target.itertext()).split()).strip()
    _HTML_TITLE_CACHE[html_path] = title or None
    return _HTML_TITLE_CACHE[html_path]


def _resolve_post_index_title_from_href(
    href: str,
    svg_path: Optional[str],
) -> Optional[str]:
    if svg_path is None:
        return None

    parsed = urlparse(href)
    if parsed.scheme or parsed.netloc:
        return None

    raw_path = parsed.path or href
    if not raw_path:
        return None

    try:
        decoded_path = unquote(raw_path)
    except Exception:
        decoded_path = raw_path

    if not decoded_path.lower().endswith("index.html"):
        return None

    svg_dir = os.path.dirname(os.path.abspath(svg_path))
    target_path = os.path.normpath(os.path.join(svg_dir, decoded_path))
    normalized_target = target_path.replace(os.sep, "/")
    if "/posts/" not in normalized_target:
        return None

    return _read_html_title(target_path)


EXTERNAL_LINK_ATTRS = ' target="_blank" rel="noopener noreferrer"'


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


def _normalize_site_href_for_label(
    href: str,
    site_base_url: Optional[str],
) -> Optional[str]:
    if not site_base_url:
        return None

    parsed_href = urlparse(href)
    if parsed_href.scheme not in {"http", "https"} or not parsed_href.netloc:
        return None

    parsed_site = urlparse(site_base_url)
    site_hostname = parsed_site.hostname
    href_hostname = parsed_href.hostname
    if not site_hostname or not href_hostname:
        return None
    if site_hostname.lower() != href_hostname.lower():
        return None

    site_path = parsed_site.path or "/"
    href_path = parsed_href.path or "/"
    normalized_site_path = site_path.rstrip("/")
    normalized_href_path = href_path.rstrip("/")

    if normalized_site_path and normalized_site_path != normalized_href_path:
        site_prefix = f"{normalized_site_path}/"
        href_with_slash = f"{normalized_href_path}/" if normalized_href_path else "/"
        if not href_with_slash.startswith(site_prefix):
            return None
        relative_path = href_with_slash[len(site_prefix) :].rstrip("/")
    else:
        relative_path = ""

    if not relative_path:
        normalized_path = "index.html"
    elif relative_path.endswith(".html"):
        normalized_path = relative_path
    elif "." not in os.path.basename(relative_path):
        normalized_path = f"{relative_path}/index.html"
    else:
        normalized_path = relative_path

    if parsed_href.query:
        return f"{normalized_path}?{parsed_href.query}"
    return normalized_path


def _infer_svg_anchor_label(
    href: Optional[str],
    svg_path: Optional[str] = None,
    site_base_url: Optional[str] = None,
    action_metadata: Optional[Dict[str, Dict[str, str]]] = None,
) -> Optional[str]:
    if href is None:
        return None

    stripped_href = href.strip()
    if not stripped_href:
        return None

    if stripped_href.startswith(_COND_HREF_PREFIX):
        return None

    action_token, _ = _parse_svg_action_href(href)
    if action_token is not None:
        meta = (action_metadata or {}).get(action_token, {})
        return meta.get("label") or _action_token_label(action_token)

    normalized_internal_href = _normalize_site_href_for_label(
        stripped_href,
        site_base_url=site_base_url,
    )
    href_for_inference = normalized_internal_href or stripped_href

    if stripped_href.startswith(("http://", "https://")):
        if normalized_internal_href is None:
            parsed = urlparse(stripped_href)
            if parsed.netloc:
                return f"External: {parsed.netloc}"
            return "External"

    post_title = _resolve_post_index_title_from_href(href_for_inference, svg_path)
    if post_title is not None:
        return post_title

    index_label = _infer_index_href_label(href_for_inference)
    if index_label is not None:
        return index_label

    href_lower = href_for_inference.lower()
    if href_lower.endswith("rss.xml"):
        return "RSS"
    if href_lower.endswith("meta.html"):
        return "Meta"
    if href_lower.endswith(".pdf"):
        return "PDF"
    if href_lower.endswith(".typ"):
        return "Source"

    return "Link"


def _inject_svg_anchor_accessibility(
    svg_data: str,
    svg_path: Optional[str] = None,
    site_base_url: Optional[str] = None,
    action_metadata: Optional[Dict[str, Dict[str, str]]] = None,
) -> str:
    seen_action_hrefs: set = set()
    root = xmlutil.parse_svg(svg_data)
    for el in list(xmlutil.iter_local(root, "a")):
        href = _extract_anchor_href(el)

        if href and href.strip().startswith(_COND_HREF_PREFIX):
            if "tabindex" not in el.attrib:
                el.set("tabindex", "-1")
            if "aria-hidden" not in el.attrib:
                el.set("aria-hidden", "true")
            continue

        label = _infer_svg_anchor_label(
            href,
            svg_path=svg_path,
            site_base_url=site_base_url,
            action_metadata=action_metadata,
        )
        if label is None:
            continue

        action_token, _ = _parse_svg_action_href(href)
        role, tabindex = _infer_svg_anchor_attrs(action_token, action_metadata)
        if role is not None and "role" not in el.attrib:
            el.set("role", role)
        if role in ("checkbox", "radio") and "aria-checked" not in el.attrib:
            el.set("aria-checked", "false")

        has_tabindex = "tabindex" in el.attrib
        action_href = href.strip() if href else ""
        if action_href.startswith(_ACTION_HREF_PREFIX) and not has_tabindex:
            if action_href in seen_action_hrefs:
                tabindex = "-1"
            seen_action_hrefs.add(action_href)

        if tabindex is not None and not has_tabindex:
            el.set("tabindex", tabindex)

        if "aria-label" not in el.attrib and "aria-labelledby" not in el.attrib:
            el.set("aria-label", label)

        if is_external_href(href or "", site_base_url) and "rel" not in el.attrib:
            el.set("rel", "noopener noreferrer")

        if not any(
            isinstance(c.tag, str) and xmlutil.local(c.tag) == "title" for c in el
        ):
            title_el = etree.Element(xmlutil.namespaced(el, "title"))
            title_el.text = label
            if el.text:
                title_el.tail = el.text
                el.text = None
            el.insert(0, title_el)

    for img in xmlutil.iter_local(root, "image"):
        if "role" not in img.attrib:
            img.set("role", "img" if img.get("aria-label") else "presentation")

    return xmlutil.serialize_svg(root)


def _normalize_svg_href_file(
    svg_path: str,
    site_base_url: Optional[str] = None,
    action_metadata: Optional[Dict[str, Dict[str, str]]] = None,
) -> None:
    if not os.path.isfile(svg_path):
        return

    with open(svg_path, "r", encoding="utf-8") as svg_file:
        svg_data = svg_file.read()

    svg_out = _normalize_svg_href_attributes(svg_data)
    svg_out = _strip_typst_classes(svg_out)
    svg_out = _inject_svg_anchor_accessibility(
        svg_out,
        svg_path=svg_path,
        site_base_url=site_base_url,
        action_metadata=action_metadata,
    )
    if svg_out == svg_data:
        return

    with open(svg_path, "w", encoding="utf-8") as svg_file:
        svg_file.write(svg_out)


def _prepare_anchor_hrefs_for_svgo_file(svg_path: str) -> None:
    if not os.path.isfile(svg_path):
        return

    with open(svg_path, "r", encoding="utf-8") as svg_file:
        svg_data = svg_file.read()

    prepared_svg_data = _prepare_anchor_hrefs_for_svgo(svg_data)
    if prepared_svg_data == svg_data:
        return

    with open(svg_path, "w", encoding="utf-8") as svg_file:
        svg_file.write(prepared_svg_data)


def _optimize_svg_with_normalized_href(
    svg_path: str,
    preserve_ids: bool = False,
    site_base_url: Optional[str] = None,
    action_metadata: Optional[Dict[str, Dict[str, str]]] = None,
) -> None:
    if not os.path.isfile(svg_path):
        return

    _prepare_anchor_hrefs_for_svgo_file(svg_path)
    _run_svgo(svg_path, preserve_ids=preserve_ids)
    _normalize_svg_href_file(svg_path, site_base_url=site_base_url, action_metadata=action_metadata)


def _convert_to_webp(src_bytes: bytes, dst_path: str) -> None:
    import io

    from PIL import Image  # type: ignore[import]

    with Image.open(io.BytesIO(src_bytes)) as img:
        img.save(dst_path, "webp", quality=85)

def _is_generated_driver_image_asset(filename: str) -> bool:
    parts = filename.split(".")
    if len(parts) < 3 or parts[-1] != "webp":
        return False
    digest = parts[-2]
    return (
        len(digest) == _ASSET_HASH_LENGTH
        and all(ch in "0123456789abcdef" for ch in digest)
    )


def _remove_stale_generated_driver_image_assets(
    asset_dir: str,
    keep_filenames: Set[str],
) -> None:
    if not os.path.isdir(asset_dir):
        return

    for filename in os.listdir(asset_dir):
        if filename in keep_filenames or not _is_generated_driver_image_asset(filename):
            continue

        target_path = os.path.join(asset_dir, filename)
        if os.path.isfile(target_path):
            try:
                os.remove(target_path)
            except FileNotFoundError:
                continue


def _replace_driver_image_anchors(
    svg_data: str,
    image_source_dir: str,
    svg_dest_dir: str,
    asset_hash: str,
    image_asset_names: Dict[str, str],
) -> str:
    def _make_image(ref, href, width, height, role, alt=None):
        img = etree.Element(xmlutil.namespaced(ref, "image"))
        img.set("href", href)
        img.set("width", width)
        img.set("height", height)
        img.set("role", role)
        if alt:
            img.set("aria-label", alt)
        return img

    def _replace(el, img):
        img.tail = el.tail
        el.getparent().replace(el, img)

    root = xmlutil.parse_svg(svg_data)
    for el in list(xmlutil.iter_local(root, "a")):
        href = xmlutil.attr_local(el, "href")
        if href is None:
            continue
        rect = next(
            (
                c for c in el.iter()
                if isinstance(c.tag, str)
                and xmlutil.local(c.tag) == "rect"
                and c.get("width") is not None
                and c.get("height") is not None
            ),
            None,
        )
        if rect is None:
            continue
        width = rect.get("width")
        height = rect.get("height")

        # Remote avatar: leave the URL intact so the browser loads it on visit.
        if href.startswith(_REMOTE_IMAGE_PREFIX):
            rest = href[len(_REMOTE_IMAGE_PREFIX):]
            alt = None
            alt_idx = rest.rfind("?alt=")
            if alt_idx != -1:
                alt = rest[alt_idx + len("?alt="):]
                rest = rest[:alt_idx]
            _replace(
                el,
                _make_image(el, rest, width, height, "img" if alt else "presentation", alt),
            )
            continue

        parsed = urlparse(href)
        if parsed.scheme != "driver-image":
            continue

        image_rel = unquote(parsed.netloc + parsed.path)
        alt_values = parse_qs(parsed.query).get("alt", [])
        alt = alt_values[0] if alt_values else None

        src_path = os.path.join(image_source_dir, image_rel)
        if not os.path.isfile(src_path):
            print(f"driver-image: source not found: {src_path!r}", file=sys.stderr)
            continue

        webp_name = image_asset_names.get(image_rel)
        if webp_name is None:
            src_hash = hashlib.sha1(image_rel.encode()).hexdigest()[
                :_ASSET_HASH_LENGTH
            ]
            webp_name = f"image.{src_hash}.{asset_hash}.webp"
            image_asset_names[image_rel] = webp_name
        dst_path = os.path.join(svg_dest_dir, webp_name)
        if not os.path.exists(dst_path):
            with open(src_path, "rb") as f:
                _convert_to_webp(f.read(), dst_path)

        _replace(
            el,
            _make_image(el, webp_name, width, height, "img" if alt else "presentation", alt),
        )

    return xmlutil.serialize_svg(root)


def _mark_svg_cond_groups(svg_content: str) -> str:
    root = xmlutil.parse_svg(svg_content)
    for el in list(xmlutil.iter_local(root, "a")):
        href = (xmlutil.attr_local(el, "href") or "").strip()
        cond = re.match(r"#cond=(.+):([01])$", href)
        if not cond:
            continue
        ancestor = el.getparent()
        while ancestor is not None and not (
            isinstance(ancestor.tag, str) and xmlutil.local(ancestor.tag) == "g"
        ):
            ancestor = ancestor.getparent()
        if ancestor is None or "data-cond-id" in ancestor.attrib:
            continue
        ancestor.set("data-cond-id", cond.group(1))
        ancestor.set("data-cond-branch", cond.group(2))
    return xmlutil.serialize_svg(root)


def extract_friend_card_symbols(svg_path: str) -> int:
    # Friend cards render as <g clip-path="url(#X)" transform="translate(X Y)">…</g>
    # (the clip-path comes from the card's `clip: true` block). Lift each into a
    # <symbol id="card-N"> in <defs> and replace the inline group with a lightweight
    # <use class="friend-card" href="#card-N" x y/>, so JS can shuffle cards by
    # reassigning hrefs without moving layout. Runs after compile_html (svgo already
    # done), so emitted ids/attributes are final.
    with open(svg_path, "r", encoding="utf-8") as f:
        root = xmlutil.parse_svg(f.read())

    cards: List[Tuple[Any, str, str, str]] = []
    for g in list(xmlutil.iter_local(root, "g")):
        clip_path = g.get("clip-path")
        transform = g.get("transform")
        if clip_path is None or transform is None:
            continue
        clip_m = re.fullmatch(r"url\(#(.+)\)", clip_path)
        tf = re.fullmatch(r"translate\(([-0-9.]+)\s+([-0-9.]+)\)", transform)
        if not clip_m or not tf:
            continue
        cards.append((g, clip_m.group(1), tf.group(1), tf.group(2)))

    if not cards:
        return 0

    defs = None
    for el in root.iter():
        if isinstance(el.tag, str) and xmlutil.local(el.tag) == "defs":
            defs = el

    for i, (g, clip, x, y) in enumerate(cards):
        symbol_id = f"card-{i}"

        use_el = etree.Element(xmlutil.namespaced(g, "use"))
        use_el.set("class", "friend-card")
        use_el.set("href", f"#{symbol_id}")
        use_el.set("x", x)
        use_el.set("y", y)
        use_el.tail = g.tail

        symbol_el = etree.Element(xmlutil.namespaced(g, "symbol"))
        symbol_el.set("id", symbol_id)
        inner_g = etree.SubElement(symbol_el, xmlutil.namespaced(g, "g"))
        inner_g.set("clip-path", f"url(#{clip})")
        inner_g.text = g.text
        for child in list(g):
            inner_g.append(child)

        g.getparent().replace(g, use_el)
        if defs is None:
            defs = etree.SubElement(root, xmlutil.namespaced(root, "defs"))
        defs.append(symbol_el)

    # Cards are hidden until friends.js reveals them post-shuffle, so the page
    # never flashes the default (file-order) cards on load.
    style_el = etree.Element(xmlutil.namespaced(root, "style"))
    style_el.text = "use.friend-card{visibility:var(--friend-cards,hidden)}"
    root.insert(0, style_el)

    with open(svg_path, "w", encoding="utf-8") as f:
        f.write(xmlutil.serialize_svg(root))
    return len(cards)


def patch_svg_file(
    src_path: str,
    dst_path: str,
    svg_href_rewrites: Optional[Dict[str, str]],
    href_base_dir: Optional[str] = None,
    site_base_url: Optional[str] = None,
    image_source_dir: Optional[str] = None,
    asset_hash: Optional[str] = None,
    image_asset_names: Optional[Dict[str, str]] = None,
    action_metadata: Optional[Dict[str, Dict[str, str]]] = None,
    custom_theme_colors: Optional[List[Tuple[str, str]]] = None,
) -> None:
    with open(src_path, "r", encoding="utf-8") as svg_file:
        svg_data = svg_file.read()

    root = xmlutil.parse_svg(svg_data)
    if root.get("width") is not None:
        root.set("width", "100%")
    if root.get("height") is not None:
        root.set("height", "100%")
    safe_rewrites = svg_href_rewrites or {}
    dest_dir = os.path.dirname(dst_path)
    for el in root.iter():
        if not isinstance(el.tag, str):
            continue
        for key in list(el.attrib):
            if xmlutil.local(key) != "href":
                continue
            value = el.attrib[key]
            rewritten = safe_rewrites.get(value, value)
            if href_base_dir is not None:
                rewritten = _rebase_relative_href_for_destination(
                    href=rewritten,
                    source_dir=href_base_dir,
                    dest_dir=dest_dir,
                )
            el.attrib[key] = rewritten
    svg_data = xmlutil.serialize_svg(root)

    svg_data = _prepare_anchor_hrefs_for_svgo(svg_data)

    # Per-anchor target policy: action/cond links drop target, external links
    # open in a new tab, the rest target the top frame.
    root = xmlutil.parse_svg(svg_data)
    for el in list(xmlutil.iter_local(root, "a")):
        href = _extract_anchor_href(el) or ""
        if href.startswith(_ACTION_HREF_PREFIX) or href.startswith(_COND_HREF_PREFIX):
            xmlutil.del_attr_local(el, "target")
        elif "target" in el.attrib:
            continue
        elif is_external_href(href, site_base_url):
            el.set("target", "_blank")
            el.set("rel", "noopener noreferrer")
        else:
            el.set("target", "_top")
    svg_data = xmlutil.serialize_svg(root)

    svg_data = _inject_svg_anchor_accessibility(
        svg_data,
        svg_path=dst_path,
        site_base_url=site_base_url,
        action_metadata=action_metadata,
    )
    svg_data = _inject_svg_theme_classes(svg_data, custom_theme_colors)
    svg_data = _inject_svg_theme_style(svg_data, custom_theme_colors)
    svg_data = _mark_svg_cond_groups(svg_data)

    if (
        image_source_dir is not None
        and asset_hash is not None
        and image_asset_names is not None
    ):
        svg_data = _replace_driver_image_anchors(
            svg_data,
            image_source_dir=image_source_dir,
            svg_dest_dir=os.path.dirname(dst_path),
            asset_hash=asset_hash,
            image_asset_names=image_asset_names,
        )

    with open(dst_path, "w", encoding="utf-8") as svg_file:
        svg_file.write(svg_data)


def _build_open_graph_metadata(
    title: str,
    description: str,
    og_type: str,
    og_url: Optional[str],
) -> str:
    metadata_lines = [
        f'<meta property="og:title" content="{html.escape(title, quote=True)}">',
        (
            '<meta property="og:description" content="'
            f'{html.escape(description, quote=True)}">'
        ),
        f'<meta property="og:type" content="{html.escape(og_type, quote=True)}">',
    ]
    if og_url:
        metadata_lines.append(
            f'<meta property="og:url" content="{html.escape(og_url, quote=True)}">'
        )
    return "\n    ".join(metadata_lines)


def _svg_aspect_ratio(svg_path: str) -> Optional[str]:
    """CSS aspect-ratio (e.g. "498.9 / 708.66") from an SVG's viewBox, so each
    page <object> reserves its true height before the SVG finishes loading."""
    with open(svg_path, "r", encoding="utf-8") as svg_file:
        root = xmlutil.parse_svg(svg_file.read())
    view_box = root.get("viewBox")
    if not view_box:
        return None
    _, _, width, height = view_box.split()
    return f"{width} / {height}"


def _process_svg_set(
    *,
    source_dir: str,
    asset_dir: str,
    html_dir: str,
    svg_name_prefix: str,
    asset_hash: Optional[str],
    title_format: str,
    svg_href_rewrites: Optional[Dict[str, str]],
    site_base_url: Optional[str],
    image_source_dir: Optional[str],
    action_metadata: Optional[Dict[str, Dict[str, str]]],
    shared_glyphs: bool,
    asset_context: DriverAssetContext,
    page_count_limit: Optional[int] = None,
    custom_theme_colors: Optional[List[Tuple[str, str]]] = None,
) -> Tuple[List[str], Optional[str], Dict[str, str]]:
    svg_files = sorted(
        f
        for f in os.listdir(source_dir)
        if _is_generated_svg(f, svg_name_prefix)
    )
    if page_count_limit:
        svg_files = svg_files[:page_count_limit]

    current_svg_set = set(svg_files)
    cleanup_dirs = [asset_dir]
    if os.path.abspath(asset_dir) != os.path.abspath(html_dir):
        cleanup_dirs.append(html_dir)
    for cleanup_dir in cleanup_dirs:
        if not os.path.isdir(cleanup_dir):
            continue
        for filename in os.listdir(cleanup_dir):
            if (
                _is_generated_svg(filename, svg_name_prefix)
                and filename not in current_svg_set
            ):
                os.remove(os.path.join(cleanup_dir, filename))

    image_asset_names: Dict[str, str] = {}
    patched_svg_paths: List[str] = []
    page_titles: List[str] = []
    for i, filename in enumerate(svg_files, start=1):
        src_path = os.path.join(source_dir, filename)
        dst_path = os.path.join(asset_dir, filename)
        patch_svg_file(
            src_path,
            dst_path,
            svg_href_rewrites,
            href_base_dir=html_dir,
            site_base_url=site_base_url,
            image_source_dir=image_source_dir,
            asset_hash=asset_hash,
            image_asset_names=image_asset_names,
            action_metadata=action_metadata,
            custom_theme_colors=custom_theme_colors,
        )
        patched_svg_paths.append(dst_path)
        page_titles.append(title_format.replace("{i}", str(i)))

    glyph_asset_path: Optional[str] = None
    if shared_glyphs:
        glyph_asset_path = _extract_and_rewrite_shared_svg_glyphs(
            patched_svg_paths,
            global_glyph_asset_path=asset_context.global_glyph_asset_path,
            global_glyph_map_path=asset_context.global_glyph_map_path,
        )

    for svg_path in patched_svg_paths:
        _optimize_svg_with_normalized_href(
            svg_path,
            site_base_url=site_base_url,
            action_metadata=action_metadata,
        )
    if shared_glyphs and glyph_asset_path:
        _optimize_svg_with_normalized_href(
            glyph_asset_path,
            preserve_ids=True,
            site_base_url=site_base_url,
        )

    page_links: List[str] = []
    for svg_path, page_title in zip(patched_svg_paths, page_titles):
        page_href = f"./{WEB_ASSETS_DIR_NAME}/{_build_asset_rel_path(svg_path)}"
        aspect_ratio = _svg_aspect_ratio(svg_path)
        style_attr = f' style="aspect-ratio: {aspect_ratio}"' if aspect_ratio else ""
        page_links.append(
            f'<object class="page" type="image/svg+xml" data-src="{html.escape(page_href, quote=True)}"{style_attr} '
            f'title="{page_title}"></object>'
        )

    return page_links, glyph_asset_path, image_asset_names


def build_html_from_svgs(
    config: HtmlBuildConfig,
    output_dir: str,
    page_count: int,
    pdf_path: Optional[str] = None,
    asset_hash: Optional[str] = None,
    action_metadata: Optional[Dict[str, Dict[str, str]]] = None,
    custom_theme_colors: Optional[List[Tuple[str, str]]] = None,
    required_assets: Optional[Set[str]] = None,
) -> str:
    with open(config.template_path, "r", encoding="utf-8") as f:
        template = f.read()

    index_path = os.path.join(config.dest_dir, config.html_filename)
    html_dir = os.path.dirname(index_path)
    page_asset_dir = config.asset_dest_dir or config.dest_dir
    os.makedirs(config.dest_dir, exist_ok=True)
    os.makedirs(page_asset_dir, exist_ok=True)

    page_links, glyph_asset_path, image_asset_names = _process_svg_set(
        source_dir=output_dir,
        asset_dir=page_asset_dir,
        html_dir=html_dir,
        svg_name_prefix=config.svg_name_prefix,
        asset_hash=asset_hash,
        title_format=config.title_format,
        svg_href_rewrites=config.svg_href_rewrites,
        site_base_url=config.site_base_url,
        image_source_dir=config.image_source_dir,
        action_metadata=action_metadata,
        shared_glyphs=config.shared_glyphs,
        asset_context=config.asset_context,
        page_count_limit=page_count if page_count else None,
        custom_theme_colors=custom_theme_colors,
    )

    if config.image_source_dir is not None and asset_hash is not None:
        _remove_stale_generated_driver_image_assets(
            page_asset_dir,
            keep_filenames=set(image_asset_names.values()),
        )

    primary_pages_html = "\n".join(page_links)
    if config.primary_pages_wrapper_class:
        primary_pages_html = (
            f'<div class="{config.primary_pages_wrapper_class}">\n'
            f"{primary_pages_html}\n"
            f"</div>"
        )
    full_pages_html = primary_pages_html
    if config.extra_pages_html:
        full_pages_html = full_pages_html + "\n" + config.extra_pages_html

    index_content = template.replace("{{PAGES}}", full_pages_html)

    title = config.default_title
    hidden_text = ""

    if pdf_path:
        title, hidden_text = extract_pdf_text(
            pdf_path,
            extract_title=config.extract_title_from_pdf,
            default_title=config.default_title,
        )

    if config.hidden_text_override is not None:
        hidden_text = f'<div class="sr-only">\n{config.hidden_text_override}\n</div>'

    meta_description = config.description if config.description else title
    index_content = index_content.replace(
        "{{DESCRIPTION}}", html.escape(meta_description)
    )
    index_content = index_content.replace(
        "{{OGP_METADATA}}",
        _build_open_graph_metadata(
            title=title,
            description=meta_description,
            og_type=config.og_type,
            og_url=config.og_url,
        ),
    )
    index_content = index_content.replace("{{TITLE}}", html.escape(title))
    index_content = index_content.replace("{{TEXT}}", hidden_text)
    index_content = index_content.replace("{{TOPBAR}}", config.top_bar_html)
    index_content = index_content.replace("{{REVISION}}", config.revision_html)

    required = required_assets or set()
    head_parts: List[str] = []
    body_parts: List[str] = []
    for key, asset in config.asset_context.web_assets.items():
        if key not in required:
            continue
        if asset.placement == "head":
            if asset.href:
                head_parts.append(
                    f'<link rel="stylesheet" href="{html.escape(asset.href)}">'
                )
            elif asset.kind == "css":
                head_parts.append(f"<style>{asset.inline}</style>")
            else:
                head_parts.append(f"<script>{asset.inline}</script>")
        else:
            if key == "form" and config.form_hooks:
                minified_hooks = _minify_js_source(_FORM_HOOKS_STUB + config.form_hooks)
                if minified_hooks:
                    body_parts.append(f"<script>{minified_hooks}</script>")
            body_parts.append(
                f'<script defer src="{html.escape(asset.href)}"></script>'
            )
    index_content = index_content.replace("{{HEAD_ASSETS}}", "\n".join(head_parts))
    index_content = index_content.replace("{{BODY_ASSETS}}", "\n".join(body_parts))

    rss_feed_link = ""
    if config.rss_feed_path:
        rss_feed_href = build_relative_href(
            html_dir,
            config.rss_feed_path,
        )
        rss_feed_link = (
            '<link rel="alternate" type="application/rss+xml" '
            f'title="RSS" href="{html.escape(rss_feed_href)}">'
        )
    index_content = index_content.replace("{{RSS_FEED_LINK}}", rss_feed_link)

    glyph_html = ""
    resolved_glyph_path = (
        glyph_asset_path or config.asset_context.global_glyph_asset_path
    )
    if resolved_glyph_path:
        glyph_src = build_root_asset_href(resolved_glyph_path)
        glyph_symbol_id = _extract_first_glyph_symbol_id(resolved_glyph_path)
        if glyph_symbol_id is not None:
            glyph_html = _build_glyph_preload_svg(glyph_src, glyph_symbol_id)
    index_content = index_content.replace("{{GLYPH_PRELOAD}}", glyph_html)

    with open(index_path, "w", encoding="utf-8") as f:
        f.write(index_content)

    return index_path


def prev_rev(
    posts_dir: str, workspace_name: str, skip_latest: bool = False
) -> Tuple[Optional[str], Optional[str]]:
    revisions = list_revisions(posts_dir, workspace_name)
    if not revisions:
        return None, None

    revision_index = 1 if skip_latest else 0
    if revision_index >= len(revisions):
        return None, None

    selected = revisions[revision_index]
    return selected.date, f"../../{selected.date}/{selected.entry_name}/index.html"


def compile_html(request: CompileBuildRequest) -> str:
    build_date = datetime.now().strftime("%Y-%m-%d")
    svg_in = (
        dict(request.typst_inputs.svg) if request.typst_inputs is not None else {}
    )
    pdf_in = (
        dict(request.typst_inputs.pdf) if request.typst_inputs is not None else {}
    )
    svg_in.setdefault("build_date", build_date)
    pdf_in.setdefault("build_date", build_date)

    svg_prefix = f"{request.html.svg_name_prefix}{{0p}}.{request.asset_hash}.svg"
    pdf_name = f"{request.file_prefix}.{request.asset_hash}.pdf"
    pdf_ts = _resolve_pdf_creation_timestamp(pdf_in)

    run_typst_compile(
        request.source_bytes,
        os.path.join(request.output_dir, svg_prefix),
        export_format="svg",
        inputs=svg_in,
    )

    if request.mobile is not None:
        m_inputs = dict(svg_in)
        m_inputs["paper_width"] = request.mobile.paper_width
        if request.mobile.paper_height:
            m_inputs["paper_height"] = request.mobile.paper_height
        m_inputs["mobile"] = "true"
        m_prefix = (
            f"{request.mobile.svg_name_prefix}{{0p}}.{request.asset_hash}.svg"
        )
        run_typst_compile(
            request.source_bytes,
            os.path.join(request.output_dir, m_prefix),
            export_format="svg",
            inputs=m_inputs,
        )

    pdf_path = os.path.join(request.output_dir, pdf_name)
    run_typst_compile(
        request.source_bytes,
        pdf_path,
        export_format="pdf",
        inputs=pdf_in,
        creation_timestamp=pdf_ts,
    )

    page_count = len(
        [
            f
            for f in os.listdir(request.output_dir)
            if _is_generated_svg(f, request.html.svg_name_prefix)
        ]
    )

    action_meta: Dict[str, Dict[str, str]] = {}
    for node in _query_nodes_from_source(
        source_content=request.source_bytes,
        query_root=os.getcwd(),
        selector="<driver-action>",
        inputs=svg_in,
    ):
        item = node.get("value")
        if not isinstance(item, dict):
            continue
        action_val = item.get("a")
        if not isinstance(action_val, str) or not action_val:
            continue
        action_meta[action_val] = {
            "label": str(item.get("label") or ""),
            "role": str(item.get("role") or ""),
            "tabindex": str(item.get("tabindex") or ""),
        }

    custom_colors: List[Tuple[str, str]] = []
    seen_colors: Set[str] = set()
    for node in _query_nodes_from_source(
        source_content=request.source_bytes,
        query_root=os.getcwd(),
        selector="<driver-theme-color>",
        inputs=svg_in,
    ):
        item = node.get("value")
        if not isinstance(item, dict):
            continue
        light = item.get("light")
        dark = item.get("dark")
        if not isinstance(light, str) or not isinstance(dark, str):
            continue
        key = light.lower()
        if key not in seen_colors:
            seen_colors.add(key)
            custom_colors.append((key, dark.lower()))

    required_assets: Set[str] = set()
    for node in _query_nodes_from_source(
        source_content=request.source_bytes,
        query_root=os.getcwd(),
        selector="<driver-assets>",
        inputs=svg_in,
    ):
        value = node.get("value")
        if isinstance(value, str):
            required_assets.add(value)
        elif isinstance(value, list):
            required_assets.update(v for v in value if isinstance(v, str))

    html_config = request.html
    if request.mobile is not None:
        html_dir = os.path.dirname(
            os.path.join(request.html.dest_dir, request.html.html_filename)
        )
        page_asset_dir = request.html.asset_dest_dir or request.html.dest_dir
        os.makedirs(page_asset_dir, exist_ok=True)
        m_page_links, _, _ = _process_svg_set(
            source_dir=request.output_dir,
            asset_dir=page_asset_dir,
            html_dir=html_dir,
            svg_name_prefix=request.mobile.svg_name_prefix,
            asset_hash=request.asset_hash,
            title_format=request.html.title_format,
            svg_href_rewrites=request.html.svg_href_rewrites,
            site_base_url=request.html.site_base_url,
            image_source_dir=request.html.image_source_dir,
            action_metadata=action_meta,
            shared_glyphs=request.html.shared_glyphs,
            asset_context=request.html.asset_context,
            custom_theme_colors=custom_colors or None,
        )
        mobile_html = (
            f'<div class="{request.mobile.wrapper_class}">\n'
            f'{chr(10).join(m_page_links)}\n'
            f"</div>"
        )
        html_config = replace(
            request.html,
            extra_pages_html=mobile_html,
            primary_pages_wrapper_class=request.mobile.primary_wrapper_class,
        )

    return build_html_from_svgs(
        config=html_config,
        output_dir=request.output_dir,
        page_count=page_count,
        pdf_path=pdf_path,
        asset_hash=request.asset_hash,
        action_metadata=action_meta,
        custom_theme_colors=custom_colors or None,
        required_assets=required_assets,
    )
