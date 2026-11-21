import json
import os
import shutil
import sys
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Dict, List, Optional, Set, Tuple

from .paths import make_temp_dir, safe_join_child, validate_workspace


@dataclass(frozen=True)
class WorkspaceRevision:
    date: str
    entry_name: str
    revision: int
    path: str


def parse_revision(entry_name: str, workspace_name: str) -> int:
    if entry_name == workspace_name:
        return 0
    if not entry_name.startswith(workspace_name + "-"):
        raise ValueError("not a matching workspace revision")
    return int(entry_name[len(workspace_name) + 1 :])


def list_revisions(posts_dir: str, workspace_name: str) -> List[WorkspaceRevision]:
    if not os.path.isdir(posts_dir):
        return []
    revisions: List[WorkspaceRevision] = []
    for date_str in sorted(os.listdir(posts_dir), reverse=True):
        date_dir = os.path.join(posts_dir, date_str)
        if not os.path.isdir(date_dir):
            continue
        day_revisions: List[WorkspaceRevision] = []
        for entry_name in os.listdir(date_dir):
            entry_path = os.path.join(date_dir, entry_name)
            if not os.path.isdir(entry_path):
                continue
            try:
                rev = parse_revision(entry_name, workspace_name)
            except ValueError:
                continue
            day_revisions.append(
                WorkspaceRevision(
                    date=date_str, entry_name=entry_name, revision=rev, path=entry_path
                )
            )
        day_revisions.sort(key=lambda item: item.revision, reverse=True)
        revisions.extend(day_revisions)
    return revisions


def latest_rev(posts_dir: str, workspace_name: str) -> Optional[WorkspaceRevision]:
    revisions = list_revisions(posts_dir, workspace_name)
    return revisions[0] if revisions else None


def prev_rev(
    posts_dir: str, workspace_name: str, skip_latest: bool = False
) -> Tuple[Optional[str], Optional[str]]:
    revisions = list_revisions(posts_dir, workspace_name)
    idx = 1 if skip_latest else 0
    if idx >= len(revisions):
        return None, None
    selected = revisions[idx]
    return selected.date, f"../../{selected.date}/{selected.entry_name}/index.html"


def next_revision_name(date_dir: str, workspace_name: str) -> Tuple[int, str]:
    os.makedirs(date_dir, exist_ok=True)
    existing: List[int] = []
    for entry_name in os.listdir(date_dir):
        try:
            existing.append(parse_revision(entry_name, workspace_name))
        except ValueError:
            continue
    target_rev = (max(existing) if existing else -1) + 1
    if target_rev == 0:
        return target_rev, workspace_name
    return target_rev, f"{workspace_name}-{target_rev}"


def resolve_submit_dest(
    posts_dir: str,
    workspace_name: str,
    amend_mode: bool,
    today_str: str,
) -> Tuple[str, str, int, bool]:
    if amend_mode:
        latest = latest_rev(posts_dir, workspace_name)
        if latest is None:
            print(
                f"No existing revision found for '{workspace_name}'. "
                "Creating a new revision instead of amending."
            )
            return today_str, workspace_name, 0, False
        return latest.date, latest.entry_name, latest.revision, True

    date_dir = os.path.join(posts_dir, today_str)
    target_rev, dest_dir_name = next_revision_name(date_dir, workspace_name)
    return today_str, dest_dir_name, target_rev, False


def normalize_rel_paths(paths: List[str]) -> List[str]:
    normalized_paths: List[str] = []
    seen: Set[str] = set()
    for raw_path in paths:
        norm = str(raw_path).replace("\\", "/").strip()
        if (
            not norm
            or norm == "."
            or norm.startswith(("/", "../"))
            or "/../" in norm
        ):
            continue
        if norm.startswith("./"):
            norm = norm[2:]
        if not norm or norm in seen:
            continue
        seen.add(norm)
        normalized_paths.append(norm)
    normalized_paths.sort()
    return normalized_paths


def load_manifest(source_dir: str) -> Dict[str, Any]:
    manifest_path = os.path.join(source_dir, ".workspace-manifest.json")
    if not os.path.isfile(manifest_path):
        return {}
    with open(manifest_path, "r", encoding="utf-8") as f:
        return json.load(f)


def write_manifest(
    manifest_path: str,
    workspace_name: str,
    publish_date: str,
    revision_name: str,
    files: List[str],
    generated_at: Optional[str] = None,
) -> None:
    payload: Dict[str, Any] = {
        "workspace": workspace_name,
        "publish_date": publish_date,
        "revision": revision_name,
        "files": files,
        "generated_at": generated_at or datetime.now().isoformat(timespec="seconds"),
    }
    with open(manifest_path, "w", encoding="utf-8") as f:
        json.dump(payload, f, indent=2, ensure_ascii=False)
        f.write("\n")


def collect_files(base_dir: str) -> List[str]:
    file_paths = [
        os.path.relpath(os.path.join(root, filename), start=base_dir).replace("\\", "/")
        for root, _, files in os.walk(base_dir)
        for filename in files
    ]
    return normalize_rel_paths(file_paths)


def _collect_relative_directories(base_dir: str) -> List[str]:
    directories: List[str] = []
    for root, dirs, _ in os.walk(base_dir):
        rel_root = os.path.relpath(root, start=base_dir).replace("\\", "/")
        if rel_root == ".":
            rel_root = ""
        directories.append(rel_root)
        for directory in dirs:
            directories.append(os.path.join(rel_root, directory).replace("\\", "/"))
    return [""] + normalize_rel_paths(directories)


def sync_snapshot(workspace_path: str, source_dest_dir: str) -> None:
    workspace_files = collect_files(workspace_path)
    os.makedirs(source_dest_dir, exist_ok=True)

    file_set = set(workspace_files)
    for rel_path in collect_files(source_dest_dir):
        if rel_path not in file_set:
            os.remove(safe_join_child(source_dest_dir, rel_path))

    for rel_path in workspace_files:
        src_path = safe_join_child(workspace_path, rel_path)
        dst_path = safe_join_child(source_dest_dir, rel_path)
        os.makedirs(os.path.dirname(dst_path), exist_ok=True)
        shutil.copy2(src_path, dst_path)

    for root, _, _ in os.walk(source_dest_dir, topdown=False):
        if root != source_dest_dir:
            try:
                os.rmdir(root)
            except OSError:
                pass

    for rel_dir in _collect_relative_directories(workspace_path):
        src_dir = (
            workspace_path if rel_dir == "" else safe_join_child(workspace_path, rel_dir)
        )
        dst_dir = (
            source_dest_dir
            if rel_dir == ""
            else safe_join_child(source_dest_dir, rel_dir)
        )
        os.makedirs(dst_dir, exist_ok=True)
        shutil.copystat(src_dir, dst_dir)


def stage_workspace(
    workspace_path: str, dest_dir: str, build_base: str
) -> Tuple[str, Optional[str]]:
    manifest = load_manifest(workspace_path)
    tracked_files = (
        [
            p
            for p in normalize_rel_paths(manifest["files"])
            if p != ".workspace-manifest.json"
        ]
        if manifest
        else []
    )

    if tracked_files:
        # Stage directly inside build_base (2 levels under repo root) so
        # relative `#import "../../driver/template.typ"` resolves identically.
        staged_path = make_temp_dir(build_base, prefix=".stage-")
        copied_any = False
        for rel_path in tracked_files:
            src_path = safe_join_child(workspace_path, rel_path)
            if not os.path.isfile(src_path):
                continue
            dst_path = safe_join_child(staged_path, rel_path)
            os.makedirs(os.path.dirname(dst_path), exist_ok=True)
            shutil.copy2(src_path, dst_path)
            copied_any = True
        if copied_any:
            return staged_path, staged_path
        shutil.rmtree(staged_path, ignore_errors=True)

    workspace_abs = os.path.abspath(workspace_path)
    dest_abs = os.path.abspath(dest_dir)
    try:
        if os.path.commonpath([workspace_abs, dest_abs]) != dest_abs:
            return workspace_path, None
    except ValueError:
        return workspace_path, None

    staged_path = make_temp_dir(build_base, prefix=".stage-")
    shutil.copytree(workspace_abs, staged_path, dirs_exist_ok=True)
    return staged_path, staged_path


def resolve_post_meta(post_dir: str, entry_name: str) -> Tuple[str, str, int]:
    source_dir = os.path.join(post_dir, "source")
    manifest_data = load_manifest(source_dir)
    if not manifest_data:
        raise FileNotFoundError(f"Missing source manifest in '{source_dir}'.")
    try:
        ws_name_raw = manifest_data["workspace"]
        publish_date_raw = manifest_data["publish_date"]
    except KeyError as exc:
        raise KeyError(
            f"Manifest in '{source_dir}' is missing required key: {exc.args[0]}"
        ) from exc

    workspace_name = validate_workspace(str(ws_name_raw))
    return workspace_name, str(publish_date_raw), parse_revision(entry_name, workspace_name)


def published_workspaces(posts_dir: str) -> List[str]:
    workspaces: Set[str] = set()
    if not os.path.isdir(posts_dir):
        return []

    for date_str in os.listdir(posts_dir):
        date_dir = os.path.join(posts_dir, date_str)
        if not os.path.isdir(date_dir):
            continue
        for entry_name in os.listdir(date_dir):
            post_dir = os.path.join(date_dir, entry_name)
            if not os.path.isdir(post_dir):
                continue
            source_dir = os.path.join(post_dir, "source")
            if not os.path.isdir(source_dir):
                print(f"Skipping '{post_dir}': missing source snapshot.", file=sys.stderr)
                continue
            if not os.path.isfile(os.path.join(source_dir, ".workspace-manifest.json")):
                print(f"Skipping '{post_dir}': missing workspace manifest.", file=sys.stderr)
                continue
            try:
                workspace_name, _, _ = resolve_post_meta(post_dir, entry_name)
            except (FileNotFoundError, KeyError, ValueError) as exc:
                print(f"Skipping '{post_dir}': {exc}", file=sys.stderr)
                continue
            workspaces.add(workspace_name)

    return sorted(workspaces)
