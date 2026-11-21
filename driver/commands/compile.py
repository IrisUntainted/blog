import os
import shutil
import sys
from argparse import Namespace
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Dict, List, Optional, Tuple

from core.assets import build_asset_ctx
from core.hidden_text import build_hidden_text, first_desc, page_head_title
from core.page import (
    CompileBuildRequest,
    HtmlBuildConfig,
    MobileCompileConfig,
    compile_html,
)
from core.paths import (
    DRIVER_DIR,
    TEMPLATES_DIR,
    WORKSPACE_PUBLIC_DIR_NAME,
    make_temp_dir,
    page_url,
    reset_dir,
    resolve_base_url,
    safe_join_child,
)
from core.typst import make_inputs, query_title_subtitle, sources_hash
from core.workspace import prev_rev

_SITE_TITLE = "Fragmenta Iridis"
_FORM_HOOKS_FILENAME = "form-hooks.js"


@dataclass(frozen=True)
class CompileOutputDirs:
    asset_output_dir: str
    html_output_dir: str
    public_output_dir: str


@dataclass(frozen=True)
class CompiledPost:
    title: str
    subtitle: Optional[str]
    description: str
    asset_hash: str


def _read_form_hooks(workspace_path: str) -> str:
    hooks_path = os.path.join(workspace_path, _FORM_HOOKS_FILENAME)
    if not os.path.isfile(hooks_path):
        return ""
    with open(hooks_path, "r", encoding="utf-8") as f:
        return f.read()


def _copy_workspace_public_files(workspace_path: str, output_dir: str) -> int:
    public_dir = os.path.join(workspace_path, WORKSPACE_PUBLIC_DIR_NAME)
    if not os.path.isdir(public_dir):
        return 0

    copied = 0
    for current_dir, _, files in os.walk(public_dir):
        rel_dir = os.path.relpath(current_dir, start=public_dir).replace("\\", "/")
        for filename in files:
            src_path = os.path.join(current_dir, filename)
            rel_file = filename if rel_dir == "." else f"{rel_dir}/{filename}"
            dst_path = safe_join_child(output_dir, rel_file)
            if os.path.exists(dst_path):
                print(
                    f"Skipping '{WORKSPACE_PUBLIC_DIR_NAME}/{rel_file}' because "
                    "it would overwrite a generated output file.",
                    file=sys.stderr,
                )
                continue
            os.makedirs(os.path.dirname(dst_path), exist_ok=True)
            shutil.copy2(src_path, dst_path)
            copied += 1
    return copied


def _workspace_latest_modified_date(workspace_path: str) -> str:
    latest_mtime: Optional[float] = None
    for root, _, files in os.walk(workspace_path):
        for filename in files:
            mtime = os.path.getmtime(os.path.join(root, filename))
            if latest_mtime is None or mtime > latest_mtime:
                latest_mtime = mtime
    if latest_mtime is None:
        raise RuntimeError(f"Workspace '{workspace_path}' does not contain any files.")
    return datetime.fromtimestamp(latest_mtime).strftime("%Y-%m-%d")


def _stage_external_workspace(
    workspace_path: str, repo_root: str, build_base: str
) -> Tuple[str, Optional[str]]:
    workspace_abs = os.path.abspath(workspace_path)
    repo_abs = os.path.abspath(repo_root)
    try:
        if os.path.commonpath([repo_abs, workspace_abs]) == repo_abs:
            return workspace_abs, None
    except ValueError:
        pass
    staged = make_temp_dir(build_base, prefix=".compile-")
    shutil.copytree(workspace_abs, staged, dirs_exist_ok=True)
    return staged, staged


def _prepare_compile_sources(workspace_path: str) -> Tuple[bytes, str, str]:
    driver_typ_path = os.path.join(TEMPLATES_DIR, "driver.typ")
    template_typ_path = os.path.join(DRIVER_DIR, "template.typ")
    form_typ_path = os.path.join(DRIVER_DIR, "form.typ")

    with open(driver_typ_path, "r", encoding="utf-8") as f:
        driver_source = f.read()

    main_typ_abs = os.path.abspath(os.path.join(workspace_path, "main.typ"))
    main_typ_rel = os.path.relpath(main_typ_abs, start=os.getcwd()).replace("\\", "/")
    hash_paths = [workspace_path, driver_typ_path, template_typ_path]
    if os.path.isfile(os.path.join(workspace_path, _FORM_HOOKS_FILENAME)):
        hash_paths.append(form_typ_path)
    asset_hash = sources_hash(hash_paths)
    driver_source = driver_source.replace(
        "// IMPORT_MAIN", f'#include "{main_typ_rel}"'
    )
    return driver_source.encode("utf-8"), main_typ_abs, asset_hash


def run_compile(
    args: Namespace,
    *,
    workspace_path: Optional[str] = None,
    publish_date: Optional[str] = None,
    output_dirs: Optional[CompileOutputDirs] = None,
) -> CompiledPost:
    build_base: str = args.build_base
    workspace_name: str = args.name
    if workspace_path is None:
        workspace_path = safe_join_child(args.workspace_base, workspace_name)
        if not os.path.exists(workspace_path):
            print(f"Workspace '{workspace_name}' does not exist.", file=sys.stderr)
            sys.exit(1)

    workspace_path, staged_temp_root = _stage_external_workspace(
        workspace_path, repo_root=os.getcwd(), build_base=build_base
    )

    try:
        if output_dirs is None:
            os.makedirs(build_base, exist_ok=True)
            html_output_dir = safe_join_child(build_base, workspace_name)
            asset_output_dir = safe_join_child(html_output_dir, "assets")
            reset_dir(html_output_dir)
            os.makedirs(asset_output_dir, exist_ok=True)
            output_dirs = CompileOutputDirs(
                asset_output_dir=asset_output_dir,
                html_output_dir=html_output_dir,
                public_output_dir=html_output_dir,
            )
        else:
            for d in (
                output_dirs.asset_output_dir,
                output_dirs.html_output_dir,
                output_dirs.public_output_dir,
            ):
                os.makedirs(d, exist_ok=True)

        asset_context = build_asset_ctx(args.root_dir)
        source_bytes, main_typ_path, asset_hash = _prepare_compile_sources(
            workspace_path
        )
        post_title, post_subtitle = query_title_subtitle(
            main_typ_path, query_root=os.getcwd()
        )

        posts_dir = os.path.join(args.root_dir, "posts")
        skip_latest = getattr(args, "amend", False)
        edited_date = (
            _workspace_latest_modified_date(workspace_path) if skip_latest else None
        )
        last_rev_date, last_rev_url = prev_rev(
            posts_dir, workspace_name, skip_latest=skip_latest
        )
        shared_inputs: Dict[str, str] = {}
        if publish_date:
            shared_inputs["publish_date"] = publish_date
        if edited_date:
            shared_inputs["edited_date"] = edited_date
        if last_rev_date and last_rev_url:
            shared_inputs["last_revision_date"] = last_rev_date
            shared_inputs["last_revision_url"] = last_rev_url
        typst_inputs = make_inputs(shared_inputs=shared_inputs or None)

        print("Compiling Typst project...", file=sys.stderr)
        shared_glyphs = bool(getattr(args, "shared_glyphs", True))
        base_url = resolve_base_url(args, required=False)
        og_url = (
            page_url(
                base_url=base_url,
                root_dir=args.root_dir,
                dest_dir=output_dirs.html_output_dir,
            )
            if base_url
            else None
        )
        pdf_href = f"./assets/post.{asset_hash}.pdf"
        resolved_description: List[str] = [post_subtitle or post_title]

        def resolve_doc(doc_elements: List[Dict[str, Any]]) -> Tuple[str, str, str]:
            hidden_text = build_hidden_text(
                doc_elements,
                post_title,
                post_subtitle,
                asset_hash,
                nav_links=[
                    ("../../../index.html", "Contents"),
                    ("meta.html", "Meta"),
                    (pdf_href, "PDF"),
                ],
                site_base_url=base_url,
            )
            head_title = page_head_title(post_title, _SITE_TITLE, post_subtitle)
            description = first_desc(
                hidden_text,
                post_subtitle or post_title,
                skip_texts=[post_subtitle] if post_subtitle else None,
            )
            resolved_description[0] = description
            return head_title, description, hidden_text

        compile_html(
            CompileBuildRequest(
                source_bytes=source_bytes,
                output_dir=output_dirs.asset_output_dir,
                asset_hash=asset_hash,
                typst_inputs=typst_inputs,
                pdf_prefix="post",
                mobile=MobileCompileConfig(),
                resolve_doc=resolve_doc,
                html=HtmlBuildConfig(
                    template_path=os.path.join(TEMPLATES_DIR, "index.html"),
                    dest_dir=output_dirs.html_output_dir,
                    title_format="Page {i}",
                    default_title=post_title,
                    asset_context=asset_context,
                    description=post_subtitle,
                    svg_href_rewrites={"post.pdf": pdf_href},
                    asset_dest_dir=output_dirs.asset_output_dir,
                    og_type="article",
                    og_url=og_url,
                    shared_glyphs=shared_glyphs,
                    image_source_dir=workspace_path,
                    site_base_url=base_url,
                    form_hooks=_read_form_hooks(workspace_path),
                ),
            )
        )

        copied_files = _copy_workspace_public_files(
            workspace_path, output_dirs.public_output_dir
        )
        if copied_files:
            print(
                f"Copied {copied_files} file(s) from '{WORKSPACE_PUBLIC_DIR_NAME}/'.",
                file=sys.stderr,
            )

        return CompiledPost(
            title=post_title,
            subtitle=post_subtitle,
            description=resolved_description[0],
            asset_hash=asset_hash,
        )
    finally:
        if staged_temp_root:
            shutil.rmtree(staged_temp_root, ignore_errors=True)
