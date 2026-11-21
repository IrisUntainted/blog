import json
import os
import shutil
import sys
from argparse import Namespace
from concurrent.futures import Future, ThreadPoolExecutor, as_completed
from datetime import datetime
from typing import Any, Dict, Optional, Tuple

from core.assets import build_asset_ctx
from core.meta import MetaFieldsRequest, MetaPageRequest, build_meta_fields, compile_meta_page
from core.paths import make_temp_dir, page_url, resolve_base_url, safe_join_child
from core.workspace import (
    collect_files,
    latest_rev,
    load_manifest,
    published_workspaces,
    resolve_post_meta,
    resolve_submit_dest,
    stage_workspace,
    sync_snapshot,
    write_manifest,
)
from .compile import CompileOutputDirs, run_compile
from .content import update_content


def _backup_existing_destination(
    build_base: str, dest_dir: str, dest_dir_name: str
) -> Tuple[Optional[str], Optional[str]]:
    if not os.path.isdir(dest_dir):
        return None, None
    backup_root = make_temp_dir(build_base, prefix=".submit-backup-")
    backup_dir = os.path.join(backup_root, dest_dir_name)
    os.replace(dest_dir, backup_dir)
    return backup_root, backup_dir


def _restore_destination_backup(dest_dir: str, backup_dir: Optional[str]) -> None:
    if backup_dir is None or not os.path.isdir(backup_dir):
        return
    if os.path.isdir(dest_dir):
        shutil.rmtree(dest_dir, ignore_errors=True)
    os.replace(backup_dir, dest_dir)


def _submit_to_destination(
    args: Namespace,
    workspace_name: str,
    workspace_path: str,
    date_str: str,
    dest_dir_name: str,
    target_rev: int,
    amend_mode: bool,
) -> None:
    posts_dir = os.path.join(args.root_dir, "posts")
    dest_base_dir = os.path.join(posts_dir, date_str)
    os.makedirs(dest_base_dir, exist_ok=True)
    dest_dir = os.path.join(dest_base_dir, dest_dir_name)
    dest_assets_dir = os.path.join(dest_dir, "assets")

    post_json: Dict[str, Any] = {}
    manifest: Dict[str, Any] = {}
    if amend_mode and os.path.isdir(dest_dir):
        post_json_path = os.path.join(dest_dir, "post.json")
        if os.path.isfile(post_json_path):
            with open(post_json_path, "r", encoding="utf-8") as f:
                post_json = json.load(f)
        manifest = load_manifest(os.path.join(dest_dir, "source"))

    stage_path, temp_root = stage_workspace(workspace_path, dest_dir, args.build_base)
    backup_root: Optional[str] = None
    backup_dir: Optional[str] = None
    try:
        workspace_files = collect_files(stage_path)
        backup_root, backup_dir = _backup_existing_destination(
            args.build_base, dest_dir, dest_dir_name
        )

        compile_args = Namespace(**vars(args))
        compile_args.name = workspace_name
        compile_args.amend = amend_mode
        compile_args.shared_glyphs = False
        compiled = run_compile(
            compile_args,
            workspace_path=stage_path,
            publish_date=date_str,
            output_dirs=CompileOutputDirs(
                asset_output_dir=dest_assets_dir,
                html_output_dir=dest_dir,
                public_output_dir=dest_dir,
            ),
        )

        asset_context = build_asset_ctx(args.root_dir)
        source_dest_dir = os.path.join(dest_dir, "source")
        sync_snapshot(stage_path, source_dest_dir)

        pdf_name = f"assets/post.{compiled.asset_hash}.pdf"
        meta_generated_at = (
            post_json.get("generated_at")
            if amend_mode and post_json.get("source_hash") == compiled.asset_hash
            else None
        )
        meta_fields = build_meta_fields(
            MetaFieldsRequest(
                workspace_name=workspace_name,
                date_str=date_str,
                target_rev=target_rev,
                dest_dir_name=dest_dir_name,
                post_title=compiled.title,
                post_subtitle=compiled.subtitle,
                pdf_name=pdf_name,
                post_source_hash=compiled.asset_hash,
                workspace_files=workspace_files,
                generated_at=meta_generated_at,
            )
        )

        base_url = resolve_base_url(args)
        meta_og_url = page_url(
            base_url=base_url,
            root_dir=args.root_dir,
            dest_dir=dest_dir,
            html_filename="meta.html",
        )
        compile_meta_page(
            MetaPageRequest(
                build_base=args.build_base,
                dest_dir=dest_dir,
                asset_dir=dest_assets_dir,
                post_title=compiled.title,
                meta_fields=meta_fields,
                workspace_files=workspace_files,
                asset_context=asset_context,
                og_url=meta_og_url,
                site_base_url=base_url,
            )
        )

        with open(os.path.join(dest_dir, "post.json"), "w", encoding="utf-8") as f:
            json.dump(
                {
                    "title": compiled.title,
                    "subtitle": compiled.subtitle,
                    "description": compiled.description,
                    "source_hash": compiled.asset_hash,
                    "generated_at": meta_fields["Generated At"],
                },
                f,
                ensure_ascii=False,
                indent=2,
            )

        write_manifest(
            manifest_path=os.path.join(source_dest_dir, ".workspace-manifest.json"),
            workspace_name=workspace_name,
            publish_date=date_str,
            revision_name=dest_dir_name,
            files=workspace_files,
            generated_at=manifest.get("generated_at") if amend_mode else None,
        )
    except Exception:
        _restore_destination_backup(dest_dir, backup_dir)
        raise
    finally:
        if backup_root is not None:
            shutil.rmtree(backup_root, ignore_errors=True)
        if temp_root is not None:
            shutil.rmtree(temp_root, ignore_errors=True)


def run_submit(args: Namespace) -> None:
    workspace_name: str = args.name
    workspace_path = safe_join_child(args.workspace_base, workspace_name)
    posts_dir = os.path.join(args.root_dir, "posts")
    today_str = datetime.now().strftime("%Y-%m-%d")
    amend_mode = getattr(args, "amend", False)

    date_str, dest_dir_name, target_rev, did_amend = resolve_submit_dest(
        posts_dir=posts_dir,
        workspace_name=workspace_name,
        amend_mode=amend_mode,
        today_str=today_str,
    )

    _submit_to_destination(
        args=args,
        workspace_name=workspace_name,
        workspace_path=workspace_path,
        date_str=date_str,
        dest_dir_name=dest_dir_name,
        target_rev=target_rev,
        amend_mode=did_amend,
    )

    verb = "Amended" if did_amend else "Submitted"
    dest_path = os.path.join(posts_dir, date_str, dest_dir_name)
    print(f"{verb} '{workspace_name}' {'in' if did_amend else 'to'} '{dest_path}'")
    update_content(args)


def _amend_latest_workspace(
    args: Namespace, posts_dir: str, workspace_name: str
) -> Optional[Tuple[str, str]]:
    latest = latest_rev(posts_dir, workspace_name)
    if latest is None:
        raise FileNotFoundError(f"No revisions found for workspace '{workspace_name}'.")
    post_dir = latest.path
    source_dir = os.path.join(post_dir, "source")
    if not os.path.isdir(source_dir):
        print(
            f"Skipping '{workspace_name}': '{source_dir}' does not exist.",
            file=sys.stderr,
        )
        return None

    _, publish_date, _ = resolve_post_meta(post_dir, latest.entry_name)
    workspace_path = safe_join_child(args.workspace_base, workspace_name)
    if not os.path.isdir(workspace_path):
        workspace_path = source_dir

    _submit_to_destination(
        args=args,
        workspace_name=workspace_name,
        workspace_path=workspace_path,
        date_str=publish_date,
        dest_dir_name=latest.entry_name,
        target_rev=latest.revision,
        amend_mode=True,
    )
    return workspace_name, post_dir


def run_amend_all(args: Namespace) -> int:
    posts_dir = os.path.join(args.root_dir, "posts")
    workspaces = published_workspaces(posts_dir)
    if not workspaces:
        print(f"No published workspaces found in '{posts_dir}'.")
        return 0

    amended_count = 0
    max_workers = min(len(workspaces), max(1, os.cpu_count() or 1))
    future_map: Dict[Future[Optional[Tuple[str, str]]], str] = {}
    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        for workspace_name in workspaces:
            future_map[
                executor.submit(_amend_latest_workspace, args, posts_dir, workspace_name)
            ] = workspace_name

        try:
            for future in as_completed(future_map):
                result = future.result()
                if result is None:
                    continue
                workspace_name, post_dir = result
                amended_count += 1
                print(f"Amended '{workspace_name}' in '{post_dir}'")
        except Exception:
            for pending in future_map:
                pending.cancel()
            raise

    update_content(args)
    print(f"Amended {amended_count} published workspace(s).")
    return amended_count
