import base64
import hashlib
import os
import sys
from argparse import Namespace
from concurrent.futures import ThreadPoolExecutor
from typing import Any, Dict, List, Set, Tuple

from core.paths import load_config


def _md5_base64(file_path: str) -> str:
    md5 = hashlib.md5()
    with open(file_path, "rb") as f:
        while chunk := f.read(1024 * 1024):
            md5.update(chunk)
    return base64.b64encode(md5.digest()).decode("ascii")


def _scan_local_files(
    root_dir: str,
) -> Tuple[Dict[str, str], Dict[str, str], Dict[str, Set[str]]]:
    local_paths: Dict[str, str] = {}
    local_md5: Dict[str, str] = {}
    local_by_folder: Dict[str, Set[str]] = {}

    for current_dir, _, files in os.walk(root_dir):
        for filename in files:
            local_path = os.path.join(current_dir, filename)
            rel_path = os.path.relpath(local_path, start=root_dir).replace("\\", "/")
            local_paths[rel_path] = local_path
            local_md5[rel_path] = _md5_base64(local_path)
            folder = os.path.dirname(rel_path).replace("\\", "/")
            local_by_folder.setdefault(folder, set()).add(rel_path)

    return local_paths, local_md5, local_by_folder


def _scan_remote(
    client: Any, bucket_name: str, prefix: str
) -> Tuple[Dict[str, Any], Dict[str, Set[str]]]:
    remote_by_rel: Dict[str, Any] = {}
    remote_by_folder: Dict[str, Set[str]] = {}

    for blob in client.list_blobs(bucket_name, prefix=prefix):
        blob_name = blob.name
        if prefix and not blob_name.startswith(prefix):
            continue
        rel_path = blob_name[len(prefix) :] if prefix else blob_name
        if not rel_path or rel_path.endswith("/"):
            continue
        remote_by_rel[rel_path] = blob
        folder = os.path.dirname(rel_path).replace("\\", "/")
        remote_by_folder.setdefault(folder, set()).add(rel_path)

    return remote_by_rel, remote_by_folder


def _worker_count(task_count: int) -> int:
    if task_count <= 1:
        return 1
    return min(task_count, max(4, min(32, (os.cpu_count() or 1) * 4)))


def _upload_changes(
    bucket: Any,
    prefix: str,
    local_paths: Dict[str, str],
    local_md5: Dict[str, str],
    remote_by_rel: Dict[str, Any],
) -> Tuple[int, int]:
    tasks: List[Tuple[str, str]] = []
    skip_count = 0

    for rel_path in sorted(local_paths):
        remote_blob = remote_by_rel.get(rel_path)
        if remote_blob and remote_blob.md5_hash == local_md5[rel_path]:
            skip_count += 1
            continue
        tasks.append((f"{prefix}{rel_path}", local_paths[rel_path]))

    if not tasks:
        return 0, skip_count

    def _upload(task: Tuple[str, str]) -> str:
        blob_name, local_path = task
        bucket.blob(blob_name).upload_from_filename(local_path)
        return blob_name

    with ThreadPoolExecutor(max_workers=_worker_count(len(tasks))) as executor:
        for blob_name in executor.map(_upload, tasks):
            print(f"Uploaded: {blob_name}")

    return len(tasks), skip_count


def _delete_stale(
    bucket: Any,
    prefix: str,
    local_by_folder: Dict[str, Set[str]],
    remote_by_folder: Dict[str, Set[str]],
    remote_by_rel: Dict[str, Any],
) -> int:
    stale_blobs: List[Tuple[Any, str]] = []
    dirty_folders = sorted(
        folder
        for folder, local_files in local_by_folder.items()
        if local_files != remote_by_folder.get(folder, set())
    )
    for folder in dirty_folders:
        stale_files = sorted(
            remote_by_folder.get(folder, set()) - local_by_folder[folder]
        )
        for rel_path in stale_files:
            blob_name = f"{prefix}{rel_path}"
            blob = remote_by_rel.get(rel_path) or bucket.blob(blob_name)
            stale_blobs.append((blob, blob_name))

    if not stale_blobs:
        return 0

    def _delete(task: Tuple[Any, str]) -> str:
        blob, blob_name = task
        blob.delete()
        return blob_name

    with ThreadPoolExecutor(max_workers=_worker_count(len(stale_blobs))) as executor:
        for blob_name in executor.map(_delete, stale_blobs):
            print(f"Deleted stale asset: {blob_name}")

    return len(stale_blobs)


def run_upload(args: Namespace) -> None:
    try:
        from google.cloud import storage  # type: ignore
    except ImportError:
        print("'google-cloud-storage' is not installed.", file=sys.stderr)
        sys.exit(1)

    config_data = load_config(getattr(args, "config", ""))

    def _resolve(arg_name: str, config_key: str, default: Any) -> Any:
        value = getattr(args, arg_name, None)
        return config_data.get(config_key, default) if value is None else value

    bucket_raw = _resolve("bucket", "bucket", None)
    bucket_name = str(bucket_raw) if bucket_raw else ""
    if not bucket_name:
        print(
            "Please provide a bucket name using the --bucket argument or in config JSON.",
            file=sys.stderr,
        )
        sys.exit(1)

    prefix_raw = _resolve("prefix", "prefix", "blog/")
    prefix = str(prefix_raw) if prefix_raw else ""
    if prefix and not prefix.endswith("/"):
        prefix += "/"

    root_dir: str = args.root_dir
    print(
        f"Uploading '{root_dir}' directory to GCS bucket '{bucket_name}' under prefix '{prefix}'..."
    )

    project_raw = _resolve("project", "project", None)
    project_id = str(project_raw) if project_raw else ""
    client = storage.Client(project=project_id) if project_id else storage.Client()
    bucket = client.bucket(bucket_name)

    if not os.path.exists(root_dir):
        print(f"Directory '{root_dir}' does not exist.", file=sys.stderr)
        sys.exit(1)

    local_paths, local_md5, local_by_folder = _scan_local_files(root_dir)
    remote_by_rel, remote_by_folder = _scan_remote(client, bucket_name, prefix)

    upload_count, skip_count = _upload_changes(
        bucket, prefix, local_paths, local_md5, remote_by_rel
    )
    delete_count = _delete_stale(
        bucket, prefix, local_by_folder, remote_by_folder, remote_by_rel
    )

    print(
        f"Upload complete. {upload_count} uploaded, "
        f"{skip_count} skipped (unchanged), "
        f"{delete_count} stale assets deleted."
    )
