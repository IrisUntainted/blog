import hashlib
import html
import json
import os
import re
from typing import Any, Dict, List, Optional, Set, Tuple

from lxml import etree

from . import xmlutil
from .assets import minify_html
from .paths import ASSET_HASH_LEN, GLOBAL_GLYPH_MAP_FILENAME, WEB_ASSETS_DIR_NAME, root_asset_href, safe_join_child
from .svg import is_generated_svg, optimize_svg

_HASHED_GLYPH_RE = re.compile(r"glyphs\.[0-9a-f]{6}\.svg")
_GLYPH_REF_RE = re.compile(r"assets/(glyphs\.[0-9a-f]{6}\.svg)")
_SVG_PREFIXES = ("page", "m-page", "meta-page", "m-meta-page", "friends", "m-friends")


def _load_glyph_registry(map_path: str) -> Dict[str, Any]:
    if not os.path.isfile(map_path):
        return {"next_short_index": 1, "symbols": {}, "fingerprint_to_short": {}}
    with open(map_path, "r", encoding="utf-8") as f:
        data = json.load(f)
    return {
        "next_short_index": data.get("next_short_index", 1),
        "symbols": data.get("symbols", {}),
        "fingerprint_to_short": data.get("fingerprint_to_short", {}),
    }


def _allocate_short_id(registry: Dict[str, Any]) -> str:
    index = registry["next_short_index"]
    registry["next_short_index"] = index + 1
    digits = "0123456789abcdefghijklmnopqrstuvwxyz"
    parts: List[str] = []
    while index:
        index, rem = divmod(index, 36)
        parts.append(digits[rem])
    return f"g{''.join(reversed(parts))}"


def _resolve_short_id(
    registry: Dict[str, Any],
    fingerprint: str,
    attrs_without_id: str,
    body: str,
) -> str:
    symbols: Dict[str, Dict[str, str]] = registry["symbols"]
    fingerprint_to_short: Dict[str, str] = registry["fingerprint_to_short"]

    short_id = fingerprint_to_short.get(fingerprint)
    if (
        short_id
        and short_id in symbols
        and symbols[short_id]["fingerprint"] == fingerprint
    ):
        return short_id

    short_id = _allocate_short_id(registry)
    symbols[short_id] = {
        "fingerprint": fingerprint,
        "attrs": attrs_without_id,
        "body": body,
    }
    fingerprint_to_short[fingerprint] = short_id
    return short_id


def _render_glyph_svg(symbols: Dict[str, Dict[str, str]]) -> str:
    ordered_ids = sorted(symbols.keys(), key=lambda sid: int(sid[1:], 36))
    parts = [
        f'<symbol id="{sid}"{symbols[sid]["attrs"]}>{symbols[sid]["body"]}</symbol>'
        for sid in ordered_ids
    ]
    return (
        '<svg xmlns="http://www.w3.org/2000/svg" '
        'xmlns:xlink="http://www.w3.org/1999/xlink">'
        f"<defs>{''.join(parts)}</defs>"
        "</svg>"
    )


def _wrapped_short_id(sym) -> Optional[str]:
    if len(sym) != 1 or (sym.text and sym.text.strip()):
        return None
    use = sym[0]
    if not (isinstance(use.tag, str) and xmlutil.local(use.tag) == "use"):
        return None
    href = xmlutil.attr_local(use, "href")
    if not href or "#" not in href:
        return None
    href_path, short_id = href.rsplit("#", 1)
    if not short_id or not href_path:
        return None
    href_basename = os.path.basename(href_path.split("?", 1)[0])
    if href_basename != "glyphs.svg" and not _HASHED_GLYPH_RE.fullmatch(href_basename):
        return None
    return short_id


def extract_and_rewrite_glyphs(
    svg_paths: List[str],
    assets_dir: str,
    glyph_map_path: str,
) -> Tuple[Optional[str], List[str]]:
    if not svg_paths:
        return None, []

    registry = _load_glyph_registry(glyph_map_path)
    parsed_pages: List[Tuple[str, Any, Optional[Any], Dict[str, str], bool]] = []

    for svg_path in svg_paths:
        with open(svg_path, "r", encoding="utf-8") as f:
            svg_data = f.read()

        root = xmlutil.parse_svg(svg_data)
        unoptimized = 'id="driver-theme-style"' in svg_data
        defs = next(xmlutil.iter_local(root, "defs"), None)

        local_to_short: Dict[str, str] = {}
        has_raw = False
        if defs is not None:
            for sym in xmlutil.iter_local(defs, "symbol"):
                local_id = sym.get("id")
                if local_id is None:
                    continue
                wrapped_id = _wrapped_short_id(sym)
                if wrapped_id and wrapped_id in registry["symbols"]:
                    local_to_short[local_id] = wrapped_id
                    continue
                has_raw = True
                attrs_without_id = "".join(
                    f' {k}="{v}"' for k, v in sym.attrib.items() if k != "id"
                )
                body = xmlutil.inner_xml(sym)
                fingerprint = hashlib.sha1(
                    f"{' '.join(attrs_without_id.split())}\n{body.strip()}".encode(
                        "utf-8"
                    )
                ).hexdigest()
                local_to_short[local_id] = _resolve_short_id(
                    registry, fingerprint, attrs_without_id, body
                )

        if local_to_short or unoptimized:
            parsed_pages.append(
                (svg_path, root, defs, local_to_short, has_raw or unoptimized)
            )

    raw_hoisted_paths = [path for path, _, _, _, need_opt in parsed_pages if need_opt]

    if not registry["symbols"]:
        return None, raw_hoisted_paths

    glyph_svg = _render_glyph_svg(registry["symbols"])
    digest = hashlib.sha256(glyph_svg.encode("utf-8")).hexdigest()[:ASSET_HASH_LEN]
    assets_abs = os.path.abspath(assets_dir)
    os.makedirs(assets_abs, exist_ok=True)
    glyph_asset_path = safe_join_child(assets_abs, f"glyphs.{digest}.svg")

    if not os.path.isfile(glyph_asset_path):
        with open(glyph_asset_path, "w", encoding="utf-8") as f:
            f.write(glyph_svg)
        optimize_svg(glyph_asset_path, preserve_ids=True)

    os.makedirs(os.path.dirname(glyph_map_path), exist_ok=True)
    new_map_json = (
        json.dumps(registry, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        + "\n"
    )
    existing_map_json = None
    if os.path.isfile(glyph_map_path):
        with open(glyph_map_path, "r", encoding="utf-8") as f:
            existing_map_json = f.read()
    if new_map_json != existing_map_json:
        with open(glyph_map_path, "w", encoding="utf-8") as f:
            f.write(new_map_json)

    glyph_href = root_asset_href(glyph_asset_path)
    xlink_href_key = f"{{{xmlutil.XLINK_NS}}}href"

    for svg_path, root, defs, local_to_short, _ in parsed_pages:
        if defs is None or not local_to_short:
            continue
        changed = False
        for sym in list(xmlutil.iter_local(defs, "symbol")):
            short_id = local_to_short.get(sym.get("id") or "")
            if short_id is None:
                continue
            target_href = f"{glyph_href}#{short_id}"
            if (
                len(sym) == 1
                and isinstance(sym[0].tag, str)
                and xmlutil.local(sym[0].tag) == "use"
                and not (sym.text and sym.text.strip())
                and sym[0].get("href") == target_href
                and xlink_href_key not in sym[0].attrib
            ):
                continue
            for child in list(sym):
                sym.remove(child)
            sym.text = None
            use = etree.SubElement(sym, xmlutil.namespaced(sym, "use"))
            use.set("href", target_href)
            changed = True

        if changed:
            with open(svg_path, "w", encoding="utf-8") as f:
                f.write(xmlutil.serialize_svg(root))

    return glyph_asset_path, raw_hoisted_paths


def find_glyph_asset(assets_dir: str) -> Optional[str]:
    if not os.path.isdir(assets_dir):
        return None
    matches = sorted(
        f for f in os.listdir(assets_dir) if _HASHED_GLYPH_RE.fullmatch(f)
    )
    return os.path.join(assets_dir, matches[-1]) if matches else None


def extract_first_glyph_symbol_id(glyph_asset_path: str) -> Optional[str]:
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
    return (symbol.get("id") or "").strip() or None


def build_glyph_preload(glyph_src: str, glyph_symbol_id: str) -> str:
    glyph_ref = f"{glyph_src}#{glyph_symbol_id}"
    return (
        '<svg data-glyph-preload="true" aria-hidden="true" width="0" height="0" '
        'style="position:absolute;opacity:0;pointer-events:none">'
        f'<use href="{html.escape(glyph_ref, quote=True)}"></use>'
        "</svg>"
    )


def rewrite_glyph_preload(html_path: str, glyph_asset_path: str) -> bool:
    if not os.path.isfile(html_path) or not os.path.basename(glyph_asset_path):
        return False

    glyph_symbol_id = extract_first_glyph_symbol_id(glyph_asset_path)
    if glyph_symbol_id is None:
        return False

    glyph_src = root_asset_href(glyph_asset_path)
    glyph_ref = f"{glyph_src}#{glyph_symbol_id}"

    with open(html_path, "r", encoding="utf-8") as f:
        doc = xmlutil.parse_html(f.read())

    preload = next(
        (
            e
            for e in doc.iter()
            if isinstance(e.tag, str) and e.get("data-glyph-preload") == "true"
        ),
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
        preload_el = xmlutil.parse_html(build_glyph_preload(glyph_src, glyph_symbol_id))
        preload_el.tail = "\n" + (body.text or "")
        body.text = "\n"
        body.insert(0, preload_el)

    with open(html_path, "w", encoding="utf-8") as f:
        f.write(xmlutil.serialize_html(doc))
    return True


def clean_glyph_assets(root_dir: str) -> int:
    root_assets_dir = safe_join_child(root_dir, WEB_ASSETS_DIR_NAME)
    if not os.path.isdir(root_assets_dir):
        return 0

    assets_abs = os.path.abspath(root_assets_dir)
    referenced: Set[str] = set()
    for current_dir, _, files in os.walk(root_dir):
        for filename in files:
            if not filename.endswith((".html", ".svg")):
                continue
            file_path = os.path.join(current_dir, filename)
            if os.path.abspath(file_path).startswith(assets_abs + os.sep):
                continue
            with open(file_path, "r", encoding="utf-8") as f:
                referenced.update(_GLYPH_REF_RE.findall(f.read()))

    cleaned = 0
    for filename in os.listdir(root_assets_dir):
        if _HASHED_GLYPH_RE.fullmatch(filename) and filename not in referenced:
            target = os.path.join(root_assets_dir, filename)
            if os.path.isfile(target):
                os.remove(target)
                cleaned += 1
    return cleaned


def refresh_glyphs(
    root_dir: str,
    page_dirs: List[str],
    site_base_url: Optional[str] = None,
) -> None:
    assets_dir = safe_join_child(root_dir, WEB_ASSETS_DIR_NAME)
    glyph_map_path = os.path.join(assets_dir, GLOBAL_GLYPH_MAP_FILENAME)

    svg_dirs: List[str] = []
    seen_dirs: Set[str] = set()
    for page_dir in page_dirs:
        for candidate in (page_dir, os.path.join(page_dir, WEB_ASSETS_DIR_NAME)):
            abs_dir = os.path.abspath(candidate)
            if abs_dir not in seen_dirs and os.path.isdir(abs_dir):
                seen_dirs.add(abs_dir)
                svg_dirs.append(candidate)

    svg_paths: List[str] = []
    for directory in svg_dirs:
        for filename in sorted(os.listdir(directory)):
            if any(is_generated_svg(filename, p) for p in _SVG_PREFIXES):
                svg_paths.append(os.path.join(directory, filename))

    if not svg_paths:
        clean_glyph_assets(root_dir)
        return

    glyph_asset_path, raw_hoisted_paths = extract_and_rewrite_glyphs(
        svg_paths, assets_dir=assets_dir, glyph_map_path=glyph_map_path
    )

    for svg_path in raw_hoisted_paths:
        optimize_svg(svg_path, site_base_url=site_base_url)

    if glyph_asset_path:
        html_paths: Set[str] = {os.path.join(root_dir, "index.html")}
        for directory in page_dirs:
            if not os.path.isdir(directory):
                continue
            for filename in os.listdir(directory):
                if filename.endswith(".html"):
                    html_paths.add(os.path.join(directory, filename))

        for html_path in sorted(html_paths):
            if rewrite_glyph_preload(html_path, glyph_asset_path):
                minify_html(html_path)

    clean_glyph_assets(root_dir)
