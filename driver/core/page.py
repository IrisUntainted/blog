import html
import os
import re
from dataclasses import dataclass, replace
from typing import Any, Callable, Dict, List, Optional, Set, Tuple

from .assets import AssetContext, minify_html_text, minify_js
from .glyphs import (
    build_glyph_preload,
    extract_and_rewrite_glyphs,
    extract_first_glyph_symbol_id,
    find_glyph_asset,
)
from .overlay import PageOverlay, build_svg_overlays, fmt_num
from .paths import WEB_ASSETS_DIR_NAME, asset_rel_path, relative_href, root_asset_href
from .svg import (
    is_generated_svg,
    optimize_svg,
    patch_svg_file,
    remove_stale_driver_images,
    svg_aspect_ratio,
)
from .typst import (
    TypstInputs,
    compile_typst,
    compile_typst_bytes,
    pdf_creation_timestamp,
    query_page_metadata,
)

# Prepended to workspace form-hooks before minification so form.cond/action/on
# register against a guaranteed-initialized form object.
_FORM_HOOKS_STUB = (
    "window.form=window.form||{_conds:{},_actions:{},_on:{}};"
    "form.cond=(i,f)=>{form._conds[i]=f};"
    "form.action=(i,f)=>{form._actions[i]=f};"
    "form.on=(i,f)=>{(form._on[i]=form._on[i]||[]).push(f)};\n"
)


@dataclass(frozen=True)
class HtmlBuildConfig:
    template_path: str
    dest_dir: str
    title_format: str
    default_title: str
    asset_context: AssetContext
    description: Optional[str] = None
    hidden_text: str = ""
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
    typst_inputs: Optional[TypstInputs]
    html: HtmlBuildConfig
    pdf_prefix: Optional[str] = None
    mobile: Optional[MobileCompileConfig] = None
    resolve_doc: Optional[Callable[[List[Dict[str, Any]]], Tuple[str, str, str]]] = None


@dataclass(frozen=True)
class CompiledPage:
    html_path: str
    title: str
    description: str


def _build_og_metadata(
    title: str,
    description: str,
    og_type: str,
    og_url: Optional[str],
) -> str:
    lines = [
        f'<meta property="og:title" content="{html.escape(title, quote=True)}">',
        f'<meta property="og:description" content="{html.escape(description, quote=True)}">',
        f'<meta property="og:type" content="{html.escape(og_type, quote=True)}">',
    ]
    if og_url:
        lines.append(
            f'<meta property="og:url" content="{html.escape(og_url, quote=True)}">'
        )
    return "\n    ".join(lines)


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
    asset_context: AssetContext,
    pdf_bytes: bytes,
    page_count_limit: Optional[int] = None,
    custom_theme_colors: Optional[List[Tuple[str, str]]] = None,
    friend_cards: bool = False,
) -> Tuple[List[str], Optional[str], Dict[str, str]]:
    svg_files = sorted(
        f for f in os.listdir(source_dir) if is_generated_svg(f, svg_name_prefix)
    )
    if page_count_limit:
        svg_files = svg_files[:page_count_limit]

    current_set = set(svg_files)
    cleanup_dirs = [asset_dir]
    if os.path.abspath(asset_dir) != os.path.abspath(html_dir):
        cleanup_dirs.append(html_dir)
    for cleanup_dir in cleanup_dirs:
        if not os.path.isdir(cleanup_dir):
            continue
        for filename in os.listdir(cleanup_dir):
            if is_generated_svg(filename, svg_name_prefix) and filename not in current_set:
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

    overlays: List[PageOverlay] = build_svg_overlays(
        pdf_bytes,
        patched_svg_paths,
        svg_href_rewrites=svg_href_rewrites,
        site_base_url=site_base_url,
        friend_cards=friend_cards,
    )

    glyph_asset_path: Optional[str] = None
    if shared_glyphs:
        glyph_asset_path, _ = extract_and_rewrite_glyphs(
            patched_svg_paths,
            assets_dir=asset_context.assets_dir,
            glyph_map_path=asset_context.glyph_map_path,
        )
        for svg_path in patched_svg_paths:
            optimize_svg(
                svg_path,
                site_base_url=site_base_url,
                action_metadata=action_metadata,
            )

    page_links: List[str] = []
    for idx, (svg_path, page_title) in enumerate(zip(patched_svg_paths, page_titles)):
        page_href = f"./{WEB_ASSETS_DIR_NAME}/{asset_rel_path(svg_path)}"
        aspect_ratio = svg_aspect_ratio(svg_path)
        style_attr = f' style="aspect-ratio: {aspect_ratio}"' if aspect_ratio else ""
        obj_html = (
            f'<object class="page" type="image/svg+xml" '
            f'data-src="{html.escape(page_href, quote=True)}"{style_attr} '
            f'title="{page_title}"></object>'
        )
        if idx < len(overlays) and overlays[idx].width > 0:
            ov = overlays[idx]
            page_links.append(
                f'<div class="page-wrap" style="--page-w:{fmt_num(ov.width)}">\n'
                f"{obj_html}\n"
                f'<div class="page-overlay" aria-hidden="true">{ov.html}</div>\n'
                f"</div>"
            )
        else:
            page_links.append(obj_html)

    return page_links, glyph_asset_path, image_asset_names


def _build_html_from_svgs(
    config: HtmlBuildConfig,
    output_dir: str,
    page_count: int,
    pdf_bytes: bytes,
    asset_hash: Optional[str] = None,
    action_metadata: Optional[Dict[str, Dict[str, str]]] = None,
    custom_theme_colors: Optional[List[Tuple[str, str]]] = None,
    required_assets: Optional[Set[str]] = None,
    friend_cards: bool = False,
) -> CompiledPage:
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
        page_count_limit=page_count or None,
        custom_theme_colors=custom_theme_colors,
        pdf_bytes=pdf_bytes,
        friend_cards=friend_cards,
    )

    if config.image_source_dir is not None and asset_hash is not None:
        remove_stale_driver_images(
            page_asset_dir, keep_filenames=set(image_asset_names.values())
        )

    primary_pages = "\n".join(page_links)
    if config.primary_pages_wrapper_class:
        primary_pages = (
            f'<div class="{config.primary_pages_wrapper_class}">\n'
            f"{primary_pages}\n"
            f"</div>"
        )
    full_pages = (
        f"{primary_pages}\n{config.extra_pages_html}"
        if config.extra_pages_html
        else primary_pages
    )

    title = config.default_title
    meta_description = config.description or title
    hidden_html = f'<div class="sr-only">\n{config.hidden_text}\n</div>'

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
                minified_hooks = minify_js(_FORM_HOOKS_STUB + config.form_hooks)
                if minified_hooks:
                    body_parts.append(f"<script>{minified_hooks}</script>")
            body_parts.append(
                f'<script defer src="{html.escape(asset.href)}"></script>'
            )

    rss_link = ""
    if config.rss_feed_path:
        rss_href = relative_href(html_dir, config.rss_feed_path)
        rss_link = (
            '<link rel="alternate" type="application/rss+xml" '
            f'title="RSS" href="{html.escape(rss_href)}">'
        )

    glyph_html = ""
    resolved_glyph = glyph_asset_path or find_glyph_asset(
        config.asset_context.assets_dir
    )
    if resolved_glyph:
        symbol_id = extract_first_glyph_symbol_id(resolved_glyph)
        if symbol_id is not None:
            glyph_html = build_glyph_preload(
                root_asset_href(resolved_glyph), symbol_id
            )

    replacements = {
        "{{PAGES}}": full_pages,
        "{{DESCRIPTION}}": html.escape(meta_description),
        "{{OGP_METADATA}}": _build_og_metadata(
            title=title,
            description=meta_description,
            og_type=config.og_type,
            og_url=config.og_url,
        ),
        "{{TITLE}}": html.escape(title),
        "{{TEXT}}": hidden_html,
        "{{HEAD_ASSETS}}": "\n".join(head_parts),
        "{{BODY_ASSETS}}": "\n".join(body_parts),
        "{{RSS_FEED_LINK}}": rss_link,
        "{{GLYPH_PRELOAD}}": glyph_html,
    }
    _TOKEN_RE = re.compile(r"\{\{[A-Z0-9_]+\}\}")
    rendered = _TOKEN_RE.sub(
        lambda m: replacements.get(m.group(0), m.group(0)), template
    )

    with open(index_path, "w", encoding="utf-8") as f:
        f.write(minify_html_text(rendered))

    return CompiledPage(
        html_path=index_path, title=title, description=meta_description
    )


def compile_html(request: CompileBuildRequest) -> CompiledPage:
    svg_in = dict(request.typst_inputs.svg) if request.typst_inputs else {}
    pdf_in = dict(request.typst_inputs.pdf) if request.typst_inputs else {}

    svg_prefix = f"{request.html.svg_name_prefix}{{0p}}.{request.asset_hash}.svg"
    compile_typst(
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
        compile_typst(
            request.source_bytes,
            os.path.join(request.output_dir, m_prefix),
            export_format="svg",
            inputs=m_inputs,
        )

    if request.pdf_prefix is not None:
        pdf_path = os.path.join(
            request.output_dir, f"{request.pdf_prefix}.{request.asset_hash}.pdf"
        )
        compile_typst(
            request.source_bytes,
            pdf_path,
            export_format="pdf",
            inputs=pdf_in,
            creation_timestamp=pdf_creation_timestamp(pdf_in),
        )

    page_count = sum(
        1
        for f in os.listdir(request.output_dir)
        if is_generated_svg(f, request.html.svg_name_prefix)
    )

    meta = query_page_metadata(
        request.source_bytes,
        query_root=os.getcwd(),
        inputs=svg_in,
        include_doc=request.resolve_doc is not None,
    )

    html_config = request.html
    if request.resolve_doc is not None:
        resolved_title, resolved_desc, resolved_hidden = request.resolve_doc(
            meta.doc_elements
        )
        html_config = replace(
            html_config,
            default_title=resolved_title,
            description=resolved_desc,
            hidden_text=resolved_hidden,
        )

    desktop_overlay_pdf = compile_typst_bytes(
        request.source_bytes, export_format="pdf", inputs=svg_in
    )
    friend_cards = "friends" in meta.required_assets

    if request.mobile is not None:
        mobile_overlay_pdf = compile_typst_bytes(
            request.source_bytes, export_format="pdf", inputs=m_inputs
        )
        html_dir = os.path.dirname(
            os.path.join(html_config.dest_dir, html_config.html_filename)
        )
        page_asset_dir = html_config.asset_dest_dir or html_config.dest_dir
        os.makedirs(page_asset_dir, exist_ok=True)
        m_page_links, _, _ = _process_svg_set(
            source_dir=request.output_dir,
            asset_dir=page_asset_dir,
            html_dir=html_dir,
            svg_name_prefix=request.mobile.svg_name_prefix,
            asset_hash=request.asset_hash,
            title_format=html_config.title_format,
            svg_href_rewrites=html_config.svg_href_rewrites,
            site_base_url=html_config.site_base_url,
            image_source_dir=html_config.image_source_dir,
            action_metadata=meta.actions,
            shared_glyphs=html_config.shared_glyphs,
            asset_context=html_config.asset_context,
            custom_theme_colors=meta.theme_colors or None,
            pdf_bytes=mobile_overlay_pdf,
            friend_cards=friend_cards,
        )
        mobile_html = (
            f'<div class="{request.mobile.wrapper_class}">\n'
            f'{chr(10).join(m_page_links)}\n'
            f"</div>"
        )
        html_config = replace(
            html_config,
            extra_pages_html=mobile_html,
            primary_pages_wrapper_class=request.mobile.primary_wrapper_class,
        )

    return _build_html_from_svgs(
        config=html_config,
        output_dir=request.output_dir,
        page_count=page_count,
        asset_hash=request.asset_hash,
        action_metadata=meta.actions,
        custom_theme_colors=meta.theme_colors or None,
        required_assets=meta.required_assets,
        pdf_bytes=desktop_overlay_pdf,
        friend_cards=friend_cards,
    )
