import hashlib
import os
import re
import shutil
import subprocess
import sys
import threading
from typing import Any, Dict, List, Optional, Set, Tuple
from urllib.parse import parse_qs, unquote, urlparse

from lxml import etree

from . import xmlutil
from .paths import ASSET_HASH_LEN, WEB_DIR, is_external_href, rebase_relative_href

_ACTION_HREF_PREFIX = "#action="
_COND_HREF_PREFIX = "#cond="
_REMOTE_IMAGE_PREFIX = "driver-remote-image://"

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

_SVG_THEME_CSS = """
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
"""

_SVGO_CONFIG_PATH = os.path.join(WEB_DIR, "svgo.config.mjs")
_SVGO_GLYPH_CONFIG_PATH = os.path.join(WEB_DIR, "svgo.glyphs.config.mjs")
_SVGO_LOCK = threading.Lock()
_svgo_path: Optional[str] = None
_svgo_checked = False
_svgo_missing_warned = False

_INDEX_SEGMENT_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*$")
_DATE_SEGMENT_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
_LOWERCASE_TITLE_WORDS = {
    "a", "an", "and", "as", "at", "by", "for", "in", "of", "on", "or", "the", "to", "with",
}
_HTML_TITLE_CACHE: Dict[str, Optional[str]] = {}
_HTML_TITLE_LOCK = threading.Lock()


def is_generated_svg(filename: str, prefix: str = "page") -> bool:
    return bool(re.match(rf"^{re.escape(prefix)}\d+(?:\.[^.]+)?\.svg$", filename))


def _fmt_svg_num(val: str) -> str:
    return f"{round(float(val), 2):.2f}".rstrip("0").rstrip(".") or "0"


def svg_aspect_ratio(svg_path: str) -> Optional[str]:
    with open(svg_path, "r", encoding="utf-8") as f:
        root = xmlutil.parse_svg(f.read())
    view_box = root.get("viewBox")
    if not view_box:
        return None
    _, _, width, height = view_box.split()
    return f"{_fmt_svg_num(width)} / {_fmt_svg_num(height)}"


def _resolve_svgo_path() -> Optional[str]:
    global _svgo_path, _svgo_checked
    with _SVGO_LOCK:
        if not _svgo_checked:
            _svgo_path = shutil.which("svgo") or shutil.which("svgo.cmd")
            _svgo_checked = True
        return _svgo_path


def _run_svgo(svg_path: str, preserve_ids: bool = False) -> None:
    global _svgo_missing_warned
    svgo_path = _resolve_svgo_path()
    if not svgo_path:
        with _SVGO_LOCK:
            if not _svgo_missing_warned:
                print("SVGO not found, skipping SVG optimization.", file=sys.stderr)
                _svgo_missing_warned = True
        return

    config_path = _SVGO_GLYPH_CONFIG_PATH if preserve_ids else _SVGO_CONFIG_PATH
    command = [svgo_path, svg_path, "-o", svg_path, "--multipass", "--precision", "2"]
    if os.path.isfile(config_path):
        command.extend(["--config", config_path])

    try:
        subprocess.run(command, check=True, capture_output=True)
    except subprocess.CalledProcessError as e:
        print(f"Aggressive SVGO failed for {svg_path}; retrying with defaults.", file=sys.stderr)
        if e.stderr:
            print(e.stderr.decode("utf-8"), file=sys.stderr)
        fallback = [svgo_path, svg_path, "-o", svg_path]
        if preserve_ids and os.path.isfile(_SVGO_GLYPH_CONFIG_PATH):
            fallback.extend(["--config", _SVGO_GLYPH_CONFIG_PATH])
        try:
            subprocess.run(fallback, check=True, capture_output=True)
        except subprocess.CalledProcessError as fallback_err:
            print(f"SVGO failed for {svg_path}", file=sys.stderr)
            if fallback_err.stderr:
                print(fallback_err.stderr.decode("utf-8"), file=sys.stderr)


def _extract_anchor_href(el) -> Optional[str]:
    href = xmlutil.attr_local(el, "href")
    if href is None:
        return None
    stripped = href.strip()
    return stripped or None


def _parse_svg_action_href(href: Optional[str]) -> Optional[str]:
    if not href:
        return None
    stripped = href.strip()
    if not stripped.startswith(_ACTION_HREF_PREFIX):
        return None
    return unquote(stripped[len(_ACTION_HREF_PREFIX) :]).strip() or None


def _action_token_role(action_token: str) -> Optional[str]:
    if action_token in ("theme", "layout") or action_token.startswith(
        ("copy:", "form-action:")
    ):
        return "button"
    if action_token.startswith("input:"):
        return "textbox"
    if action_token.startswith("checkbox:"):
        return "checkbox"
    if action_token.startswith("radio:"):
        return "radio"
    return None


def _action_token_label(action_token: str) -> str:
    if action_token == "theme":
        return "Theme"
    if action_token == "layout":
        return "Layout"
    if action_token.startswith("copy:"):
        return "Copy"
    if action_token.startswith("input:"):
        return "Input"
    return "Action"


def _humanize_index_segment(segment: str) -> Optional[str]:
    normalized = segment.strip()
    if (
        not normalized
        or normalized in {".", ".."}
        or _DATE_SEGMENT_RE.fullmatch(normalized)
        or not _INDEX_SEGMENT_RE.fullmatch(normalized)
    ):
        return None

    words = [part for part in re.split(r"[._-]+", normalized) if part]
    if not words:
        return None

    parts: List[str] = []
    for index, word in enumerate(words):
        if word.isupper() or word.isdigit():
            parts.append(word)
        elif any(ch.isdigit() for ch in word):
            parts.append(word[0].upper() + word[1:] if word[0].isalpha() else word)
        elif index > 0 and word.lower() in _LOWERCASE_TITLE_WORDS:
            parts.append(word.lower())
        else:
            parts.append(word.lower().capitalize())
    return " ".join(parts).strip() or None


def _infer_index_href_label(href: str) -> Optional[str]:
    parsed = urlparse(href)
    path = unquote(parsed.path or href).replace("\\", "/").strip()
    if not path:
        return None
    segments = [s for s in path.split("/") if s and s != "."]
    if not segments or segments[-1].lower() != "index.html":
        return None
    if len(segments) >= 2:
        label = _humanize_index_segment(segments[-2])
        if label is not None:
            return label
    return "Contents"


def _read_html_title(html_path: str) -> Optional[str]:
    with _HTML_TITLE_LOCK:
        if html_path in _HTML_TITLE_CACHE:
            return _HTML_TITLE_CACHE[html_path]

    if not os.path.isfile(html_path):
        with _HTML_TITLE_LOCK:
            _HTML_TITLE_CACHE[html_path] = None
        return None

    with open(html_path, "r", encoding="utf-8") as f:
        doc = xmlutil.parse_html(f.read())

    target = next(
        (e for e in doc.iter() if isinstance(e.tag, str) and e.tag == "h1"), None
    )
    if target is None:
        target = next(
            (e for e in doc.iter() if isinstance(e.tag, str) and e.tag == "title"),
            None,
        )
    title = " ".join("".join(target.itertext()).split()).strip() if target is not None else ""
    result = title or None
    with _HTML_TITLE_LOCK:
        _HTML_TITLE_CACHE[html_path] = result
    return result


def _resolve_post_index_title(href: str, svg_path: Optional[str]) -> Optional[str]:
    if svg_path is None:
        return None
    parsed = urlparse(href)
    if parsed.scheme or parsed.netloc:
        return None
    decoded_path = unquote(parsed.path or href)
    if not decoded_path.lower().endswith("index.html"):
        return None
    target_path = os.path.normpath(
        os.path.join(os.path.dirname(os.path.abspath(svg_path)), decoded_path)
    )
    if "/posts/" not in target_path.replace(os.sep, "/"):
        return None
    return _read_html_title(target_path)


def _normalize_site_href_for_label(
    href: str, site_base_url: Optional[str]
) -> Optional[str]:
    if not site_base_url:
        return None
    parsed_href = urlparse(href)
    if parsed_href.scheme not in {"http", "https"} or not parsed_href.netloc:
        return None
    parsed_site = urlparse(site_base_url)
    if (
        not parsed_site.hostname
        or not parsed_href.hostname
        or parsed_site.hostname.lower() != parsed_href.hostname.lower()
    ):
        return None

    norm_site = (parsed_site.path or "/").rstrip("/")
    norm_href = (parsed_href.path or "/").rstrip("/")
    if norm_site and norm_site != norm_href:
        site_prefix = f"{norm_site}/"
        href_with_slash = f"{norm_href}/" if norm_href else "/"
        if not href_with_slash.startswith(site_prefix):
            return None
        rel = href_with_slash[len(site_prefix) :].rstrip("/")
    else:
        rel = ""

    if not rel:
        norm_path = "index.html"
    elif rel.endswith(".html") or "." in os.path.basename(rel):
        norm_path = rel
    else:
        norm_path = f"{rel}/index.html"

    return f"{norm_path}?{parsed_href.query}" if parsed_href.query else norm_path


def _infer_svg_anchor_label(
    href: Optional[str],
    svg_path: Optional[str] = None,
    site_base_url: Optional[str] = None,
    action_metadata: Optional[Dict[str, Dict[str, str]]] = None,
) -> Optional[str]:
    if href is None:
        return None
    stripped = href.strip()
    if not stripped or stripped.startswith(_COND_HREF_PREFIX):
        return None

    action_token = _parse_svg_action_href(stripped)
    if action_token is not None:
        meta = (action_metadata or {}).get(action_token, {})
        return meta.get("label") or _action_token_label(action_token)

    internal_href = _normalize_site_href_for_label(stripped, site_base_url)
    href_for_inference = internal_href or stripped

    if stripped.startswith(("http://", "https://")) and internal_href is None:
        parsed = urlparse(stripped)
        return f"External: {parsed.netloc}" if parsed.netloc else "External"

    post_title = _resolve_post_index_title(href_for_inference, svg_path)
    if post_title is not None:
        return post_title

    index_label = _infer_index_href_label(href_for_inference)
    if index_label is not None:
        return index_label

    lower = href_for_inference.lower()
    if lower.endswith("rss.xml"):
        return "RSS"
    if lower.endswith("meta.html"):
        return "Meta"
    if lower.endswith(".pdf"):
        return "PDF"
    if lower.endswith(".typ"):
        return "Source"
    return "Link"


def _set_default(el, attr: str, value: str) -> None:
    if attr not in el.attrib:
        el.set(attr, value)


def _inject_anchor_accessibility(
    root,
    svg_path: Optional[str] = None,
    site_base_url: Optional[str] = None,
    action_metadata: Optional[Dict[str, Dict[str, str]]] = None,
) -> None:
    seen_action_hrefs: Set[str] = set()
    for el in list(xmlutil.iter_local(root, "a")):
        href = _extract_anchor_href(el)
        if href and href.startswith(_COND_HREF_PREFIX):
            _set_default(el, "tabindex", "-1")
            _set_default(el, "aria-hidden", "true")
            continue

        label = _infer_svg_anchor_label(
            href,
            svg_path=svg_path,
            site_base_url=site_base_url,
            action_metadata=action_metadata,
        )
        if label is None:
            continue

        action_token = _parse_svg_action_href(href)
        meta = (action_metadata or {}).get(action_token, {}) if action_token else {}
        role = meta.get("role") or (_action_token_role(action_token) if action_token else None)
        tabindex = meta.get("tabindex") or None

        if role is not None:
            _set_default(el, "role", role)
        if role in ("checkbox", "radio"):
            _set_default(el, "aria-checked", "false")

        has_tabindex = "tabindex" in el.attrib
        action_href = href or ""
        if action_href.startswith(_ACTION_HREF_PREFIX) and not has_tabindex:
            if action_href in seen_action_hrefs:
                tabindex = "-1"
            seen_action_hrefs.add(action_href)

        if tabindex is not None and not has_tabindex:
            el.set("tabindex", tabindex)

        if "aria-label" not in el.attrib and "aria-labelledby" not in el.attrib:
            el.set("aria-label", label)

        if is_external_href(action_href, site_base_url):
            _set_default(el, "rel", "noopener noreferrer")

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
        _set_default(img, "role", "img" if img.get("aria-label") else "presentation")


def _convert_to_webp(src_bytes: bytes, dst_path: str) -> None:
    import io

    from PIL import Image  # type: ignore[import]

    with Image.open(io.BytesIO(src_bytes)) as img:
        img.save(dst_path, "webp", quality=85)


def remove_stale_driver_images(asset_dir: str, keep_filenames: Set[str]) -> None:
    if not os.path.isdir(asset_dir):
        return
    for filename in os.listdir(asset_dir):
        if filename in keep_filenames:
            continue
        parts = filename.split(".")
        if (
            len(parts) < 3
            or parts[-1] != "webp"
            or len(parts[-2]) != ASSET_HASH_LEN
            or not all(ch in "0123456789abcdef" for ch in parts[-2])
        ):
            continue
        target = os.path.join(asset_dir, filename)
        if os.path.isfile(target):
            try:
                os.remove(target)
            except FileNotFoundError:
                continue


def _replace_driver_image_anchors(
    root,
    image_source_dir: str,
    svg_dest_dir: str,
    asset_hash: str,
    image_asset_names: Dict[str, str],
) -> None:
    def _make_image(ref, href: str, width: str, height: str, alt: Optional[str]):
        img = etree.Element(xmlutil.namespaced(ref, "image"))
        img.set("href", href)
        img.set("width", width)
        img.set("height", height)
        img.set("role", "img" if alt else "presentation")
        if alt:
            img.set("aria-label", alt)
        return img

    for el in list(xmlutil.iter_local(root, "a")):
        href = xmlutil.attr_local(el, "href")
        if href is None:
            continue
        rect = next(
            (
                c
                for c in el.iter()
                if isinstance(c.tag, str)
                and xmlutil.local(c.tag) == "rect"
                and c.get("width") is not None
                and c.get("height") is not None
            ),
            None,
        )
        if rect is None:
            continue
        width, height = rect.get("width"), rect.get("height")

        if href.startswith(_REMOTE_IMAGE_PREFIX):
            rest = href[len(_REMOTE_IMAGE_PREFIX) :]
            alt = None
            alt_idx = rest.rfind("?alt=")
            if alt_idx != -1:
                alt = rest[alt_idx + len("?alt=") :]
                rest = rest[:alt_idx]
            img = _make_image(el, rest, width, height, alt)
            img.tail = el.tail
            el.getparent().replace(el, img)
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
            src_hash = hashlib.sha1(image_rel.encode()).hexdigest()[:ASSET_HASH_LEN]
            webp_name = f"image.{src_hash}.{asset_hash}.webp"
            image_asset_names[image_rel] = webp_name

        dst_path = os.path.join(svg_dest_dir, webp_name)
        if not os.path.exists(dst_path):
            with open(src_path, "rb") as f:
                _convert_to_webp(f.read(), dst_path)

        img = _make_image(el, webp_name, width, height, alt)
        img.tail = el.tail
        el.getparent().replace(el, img)


def _mark_cond_groups(root) -> None:
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


def _apply_theme_classes(
    root, custom_colors: Optional[List[Tuple[str, str]]] = None
) -> None:
    extra_fill: Dict[str, str] = {}
    extra_stroke: Dict[str, str] = {}
    if custom_colors:
        for light, _ in custom_colors:
            cls = f"tc-{light.lstrip('#')}"
            extra_fill[light] = cls
            extra_stroke[light] = f"{cls}-s"

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
            cls = static_map.get(raw.strip().lower()) or extra_map.get(raw.strip().lower())
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


def _build_theme_css(custom_colors: Optional[List[Tuple[str, str]]]) -> str:
    css = _SVG_THEME_CSS
    if not custom_colors:
        return css
    class_rules: List[str] = []
    dark_vars: List[str] = []
    for light, dark in custom_colors:
        cls = f"tc-{light.lstrip('#')}"
        var = f"--svg-{cls}"
        class_rules.append(f".{cls} {{ fill: var({var}, {light}) !important; }}")
        class_rules.append(f".{cls}-s {{ stroke: var({var}, {light}) !important; }}")
        dark_vars.append(f"    {var}: {dark};")
    css = css.replace(
        "\n  }\n}\na[href^",
        "\n" + "\n".join(dark_vars) + "\n  }\n}\na[href^",
    )
    return css.replace(
        'a[href^="#cond="]',
        "\n".join(class_rules) + '\na[href^="#cond="]',
    )


def _insert_theme_style(
    root, custom_colors: Optional[List[Tuple[str, str]]] = None
) -> None:
    if any(
        isinstance(el.tag, str)
        and xmlutil.local(el.tag) == "style"
        and el.get("id") == "driver-theme-style"
        for el in root
    ):
        return
    style_el = etree.Element(
        xmlutil.namespaced(root, "style"), id="driver-theme-style"
    )
    style_el.text = etree.CDATA(_build_theme_css(custom_colors))
    root.insert(0, style_el)


def _prepare_anchor_hrefs_for_svgo(root):
    xlink_key = f"{{{xmlutil.XLINK_NS}}}href"
    inserted = False
    for el in xmlutil.iter_local(root, "a"):
        if xlink_key in el.attrib or "href" not in el.attrib:
            continue
        el.attrib[xlink_key] = el.attrib.pop("href")
        inserted = True
    if inserted:
        root = xmlutil.declare_namespace(root, "xlink", xmlutil.XLINK_NS)
    return root


def _normalize_href_attrs(root) -> None:
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


def _strip_typst_classes(root) -> None:
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
    with open(src_path, "r", encoding="utf-8") as f:
        root = xmlutil.parse_svg(f.read())

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
            rewritten = safe_rewrites.get(el.attrib[key], el.attrib[key])
            if href_base_dir is not None:
                rewritten = rebase_relative_href(rewritten, href_base_dir, dest_dir)
            el.attrib[key] = rewritten

    if (
        image_source_dir is not None
        and asset_hash is not None
        and image_asset_names is not None
    ):
        _replace_driver_image_anchors(
            root, image_source_dir, dest_dir, asset_hash, image_asset_names
        )

    _mark_cond_groups(root)

    for el in list(xmlutil.iter_local(root, "a")):
        href = _extract_anchor_href(el) or ""
        if href.startswith((_ACTION_HREF_PREFIX, _COND_HREF_PREFIX)):
            xmlutil.del_attr_local(el, "target")
        elif "target" in el.attrib:
            continue
        elif is_external_href(href, site_base_url):
            el.set("target", "_blank")
            el.set("rel", "noopener noreferrer")
        else:
            el.set("target", "_top")

    _inject_anchor_accessibility(
        root,
        svg_path=dst_path,
        site_base_url=site_base_url,
        action_metadata=action_metadata,
    )
    _apply_theme_classes(root, custom_theme_colors)
    _insert_theme_style(root, custom_theme_colors)
    root = _prepare_anchor_hrefs_for_svgo(root)

    with open(dst_path, "w", encoding="utf-8") as f:
        f.write(xmlutil.serialize_svg(root))


def optimize_svg(
    svg_path: str,
    preserve_ids: bool = False,
    site_base_url: Optional[str] = None,
    action_metadata: Optional[Dict[str, Dict[str, str]]] = None,
) -> None:
    if not os.path.isfile(svg_path):
        return

    with open(svg_path, "r", encoding="utf-8") as f:
        before = f.read()
    prepared = xmlutil.serialize_svg(
        _prepare_anchor_hrefs_for_svgo(xmlutil.parse_svg(before))
    )
    if prepared != before:
        with open(svg_path, "w", encoding="utf-8") as f:
            f.write(prepared)

    _run_svgo(svg_path, preserve_ids=preserve_ids)

    with open(svg_path, "r", encoding="utf-8") as f:
        post_svgo = f.read()
    root = xmlutil.parse_svg(post_svgo)
    _normalize_href_attrs(root)
    _strip_typst_classes(root)
    _inject_anchor_accessibility(
        root,
        svg_path=svg_path,
        site_base_url=site_base_url,
        action_metadata=action_metadata,
    )
    out = xmlutil.serialize_svg(root)
    if out != post_svgo:
        with open(svg_path, "w", encoding="utf-8") as f:
            f.write(out)


def extract_friend_card_symbols(svg_path: str) -> int:
    with open(svg_path, "r", encoding="utf-8") as f:
        root = xmlutil.parse_svg(f.read())

    if any(el.get("class") == "friend-card" for el in xmlutil.iter_local(root, "use")):
        return 0

    cards: List[Tuple[Any, str, str, str]] = []
    for g in list(xmlutil.iter_local(root, "g")):
        clip_path = g.get("clip-path")
        transform = g.get("transform")
        if clip_path is None or transform is None:
            continue
        clip_m = re.fullmatch(r"url\(#(.+)\)", clip_path)
        tf = re.fullmatch(r"translate\(([-0-9.]+)\s+([-0-9.]+)\)", transform)
        if clip_m and tf:
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

    style_el = etree.Element(xmlutil.namespaced(root, "style"))
    style_el.text = "use.friend-card{visibility:var(--friend-cards,hidden)}"
    root.insert(0, style_el)

    with open(svg_path, "w", encoding="utf-8") as f:
        f.write(xmlutil.serialize_svg(root))
    return len(cards)
