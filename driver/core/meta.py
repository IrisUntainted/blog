import html
import os
import shutil
from dataclasses import dataclass
from datetime import datetime
from typing import Dict, List, Optional

from .assets import AssetContext
from .page import CompileBuildRequest, HtmlBuildConfig, MobileCompileConfig, compile_html
from .paths import DRIVER_DIR, TEMPLATES_DIR, make_temp_dir, raw_copy_id
from .typst import hash_text_with_paths, make_inputs


@dataclass(frozen=True)
class MetaFieldsRequest:
    workspace_name: str
    date_str: str
    target_rev: int
    dest_dir_name: str
    post_title: str
    post_subtitle: Optional[str]
    pdf_name: str
    post_source_hash: str
    workspace_files: List[str]
    generated_at: Optional[str] = None


@dataclass(frozen=True)
class MetaPageRequest:
    build_base: str
    dest_dir: str
    asset_dir: Optional[str]
    post_title: str
    meta_fields: Dict[str, str]
    workspace_files: List[str]
    asset_context: AssetContext
    og_url: Optional[str] = None
    site_base_url: Optional[str] = None


def build_meta_fields(request: MetaFieldsRequest) -> Dict[str, str]:
    fields: Dict[str, str] = {"Post Title": request.post_title}
    if request.post_subtitle:
        fields["Post Subtitle"] = request.post_subtitle
    fields.update(
        {
            "Publish Date": request.date_str,
            "Revision": str(request.target_rev),
            "Workspace Name": request.workspace_name,
            "Post Path": f"{request.date_str}/{request.dest_dir_name}",
            "Source File Count": str(len(request.workspace_files)),
            "Source Hash": request.post_source_hash,
            "PDF Asset": request.pdf_name,
            "Generated At": request.generated_at
            or datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        }
    )
    return fields


def _escape_typst_string(value: str) -> str:
    return value.replace("\\", "\\\\").replace('"', '\\"')


def _build_meta_typst_source(
    template_source: str,
    meta_fields: Dict[str, str],
    source_files: List[str],
) -> str:
    placeholders = {
        "{{POST_TITLE}}": "Post Title",
        "{{PUBLISH_DATE}}": "Publish Date",
        "{{REVISION}}": "Revision",
        "{{WORKSPACE_NAME}}": "Workspace Name",
        "{{POST_PATH}}": "Post Path",
        "{{SOURCE_FILE_COUNT}}": "Source File Count",
        "{{SOURCE_HASH}}": "Source Hash",
        "{{PDF_ASSET}}": "PDF Asset",
        "{{GENERATED_AT}}": "Generated At",
    }
    replacements = {
        placeholder: f'#raw("{_escape_typst_string(meta_fields[field])}", block: false)'
        for placeholder, field in placeholders.items()
    }
    post_subtitle = meta_fields.get("Post Subtitle")
    replacements["{{POST_SUBTITLE}}"] = (
        f'raw("{_escape_typst_string(post_subtitle)}", block: false)'
        if post_subtitle is not None
        else "none"
    )

    source_lines = [
        f'- #link("source/{_escape_typst_string(rel)}")[source/{_escape_typst_string(rel)}]'
        for rel in (p.replace("\\", "/") for p in source_files)
    ] or ["- No files recorded."]

    source = template_source
    for placeholder, value in replacements.items():
        source = source.replace(placeholder, value)
    return source.replace("{{SOURCE_FILES}}", "\n".join(source_lines))


def _build_meta_hidden_text(
    meta_fields: Dict[str, str], source_files: List[str]
) -> str:
    seen_raw_ids: set = set()
    lines: List[str] = [
        '<p class="post-nav"><a href="index.html" tabindex="-1">Back</a></p>',
        "<h1>Meta</h1>",
        "<ul>",
    ]
    for key, value in meta_fields.items():
        copy_id = raw_copy_id(value)
        id_attr = "" if copy_id in seen_raw_ids else f' id="raw-{copy_id}"'
        seen_raw_ids.add(copy_id)
        lines.append(
            f"<li>{html.escape(key)}: <code{id_attr}>{html.escape(value)}</code></li>"
        )
    lines.extend(["</ul>", "<h2>Source Files</h2>", "<ul>"])
    for rel_path in source_files:
        safe_path = html.escape(rel_path.replace("\\", "/"))
        lines.append(
            f'<li><a href="source/{safe_path}" tabindex="-1">source/{safe_path}</a></li>'
        )
    if not source_files:
        lines.append("<li>No files recorded.</li>")
    lines.append("</ul>")
    return "\n".join(lines)


def compile_meta_page(request: MetaPageRequest) -> None:
    with open(os.path.join(TEMPLATES_DIR, "meta.typ"), "r", encoding="utf-8") as f:
        template_source = f.read()

    meta_source = _build_meta_typst_source(
        template_source, request.meta_fields, request.workspace_files
    )
    meta_hidden_text = _build_meta_hidden_text(
        request.meta_fields, request.workspace_files
    )
    meta_hash = hash_text_with_paths(
        meta_source, [os.path.join(DRIVER_DIR, "template.typ")]
    )

    meta_output_dir = make_temp_dir(request.build_base, prefix=".meta-build-")
    try:
        compile_html(
            CompileBuildRequest(
                source_bytes=meta_source.encode("utf-8"),
                output_dir=meta_output_dir,
                asset_hash=meta_hash,
                typst_inputs=make_inputs(shared_inputs={"back_href": "index.html"}),
                mobile=MobileCompileConfig(svg_name_prefix="m-meta-page"),
                html=HtmlBuildConfig(
                    template_path=os.path.join(TEMPLATES_DIR, "index.html"),
                    dest_dir=request.dest_dir,
                    title_format="Meta Page {i}",
                    default_title=f"{request.post_title} - Meta",
                    asset_context=request.asset_context,
                    description=f"Metadata for {request.post_title}",
                    hidden_text=meta_hidden_text,
                    svg_name_prefix="meta-page",
                    html_filename="meta.html",
                    asset_dest_dir=request.asset_dir,
                    og_type="article",
                    og_url=request.og_url,
                    shared_glyphs=False,
                    site_base_url=request.site_base_url,
                ),
            )
        )
    finally:
        shutil.rmtree(meta_output_dir, ignore_errors=True)
