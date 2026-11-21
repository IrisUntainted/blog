import argparse
import json
import os
import sys
from argparse import ArgumentParser, BooleanOptionalAction, Namespace
from contextlib import contextmanager
from typing import Iterator

try:
    import fcntl  # type: ignore
except ImportError:
    fcntl = None

from commands.compile import run_compile
from commands.content import update_content
from commands.init import run_init
from commands.recover import run_recover
from commands.serve import run_serve
from commands.submit import run_amend_all, run_submit
from commands.upload import run_upload
from core.paths import validate_workspace

_DRIVER_LOCK_FILENAME = ".driver.lock"
_MUTATING_COMMANDS = {"init", "compile", "submit", "amend-all", "recover", "update", "upload"}


def _lock_path(args: Namespace) -> str:
    paths = [
        os.path.abspath(str(args.workspace_base)),
        os.path.abspath(str(args.build_base)),
        os.path.abspath(str(args.root_dir)),
    ]
    try:
        lock_base = os.path.commonpath(paths)
    except ValueError:
        lock_base = os.getcwd()
    if lock_base == os.path.sep:
        lock_base = os.getcwd()
    return os.path.join(lock_base, _DRIVER_LOCK_FILENAME)


def _read_lock_holder(lock_path: str) -> str:
    try:
        with open(lock_path, "r", encoding="utf-8") as f:
            payload = json.loads(f.read().strip())
        if isinstance(payload, dict):
            pid, cmd = payload.get("pid"), payload.get("command")
            if isinstance(pid, int) and isinstance(cmd, str) and cmd:
                return f"pid {pid} ({cmd})"
            if isinstance(pid, int):
                return f"pid {pid}"
    except (OSError, json.JSONDecodeError):
        pass
    return "another process"


@contextmanager
def _mutating_cmd_lock(args: Namespace, command: str) -> Iterator[None]:
    lock_path = _lock_path(args)
    os.makedirs(os.path.dirname(lock_path), exist_ok=True)
    lock_payload = {"pid": os.getpid(), "command": command}

    if fcntl is not None:
        with open(lock_path, "a+", encoding="utf-8") as f:
            try:
                fcntl.flock(f.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                print(
                    f"Cannot run '{command}': driver lock is held by {_read_lock_holder(lock_path)}.",
                    file=sys.stderr,
                )
                sys.exit(1)
            f.seek(0)
            f.truncate()
            json.dump(lock_payload, f, ensure_ascii=False)
            f.write("\n")
            f.flush()
            try:
                yield
            finally:
                f.seek(0)
                f.truncate()
                f.flush()
                fcntl.flock(f.fileno(), fcntl.LOCK_UN)
        return

    try:
        fd = os.open(lock_path, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
    except FileExistsError:
        print(
            f"Cannot run '{command}': driver lock is held by {_read_lock_holder(lock_path)}.",
            file=sys.stderr,
        )
        sys.exit(1)

    with os.fdopen(fd, "w", encoding="utf-8") as f:
        json.dump(lock_payload, f, ensure_ascii=False)
        f.write("\n")
    try:
        yield
    finally:
        try:
            os.remove(lock_path)
        except FileNotFoundError:
            pass


def _parse_name(value: str) -> str:
    try:
        return validate_workspace(value)
    except ValueError as e:
        raise argparse.ArgumentTypeError(str(e)) from e


def _build_parser() -> ArgumentParser:
    parser = ArgumentParser(
        prog="Blog Driver",
        description="Driver for building blog posts from Typst sources.",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    p_init = sub.add_parser("init", help="Create a new post draft with the specified workspace name.")
    p_init.add_argument("name", type=_parse_name)

    p_compile = sub.add_parser("compile", help="Name of the workspace to be compiled.")
    p_compile.add_argument("name", type=_parse_name)
    p_compile.add_argument("--amend", action=BooleanOptionalAction, default=False, dest="amend",
                           help="If set, compile with amend metadata for latest-revision links.")

    p_submit = sub.add_parser("submit", help="Name of the workspace to be submitted.")
    p_submit.add_argument("name", type=_parse_name)
    p_submit.add_argument("--amend", action=BooleanOptionalAction, default=False, dest="amend",
                          help="If set, replace the latest revision instead of creating a new one.")

    sub.add_parser("amend-all", help="Amend the latest published revision of every workspace from its source snapshot.")

    p_recover = sub.add_parser("recover", help="Name of the workspace to recover from the latest post source snapshot.")
    p_recover.add_argument("name", type=_parse_name)
    p_recover.add_argument("--force", action=BooleanOptionalAction, default=False, dest="force",
                           help="If set, remove an existing local workspace before recovery.")

    sub.add_parser("update", help="Update the content page, sitemap, and RSS feed.")

    p_upload = sub.add_parser("upload", help="Upload the generated root directory to Google Cloud Storage")
    p_upload.add_argument("--bucket", required=False, help="Name of the GCS bucket. Overrides config.")
    p_upload.add_argument("--prefix", default=None, help="Prefix (folder) in GCS (default: blog/). Overrides config.")
    p_upload.add_argument("--project", default=None, help="Google Cloud project ID. Overrides config.")

    p_serve = sub.add_parser("serve", help="Serve the generated root directory over HTTP.")
    p_serve.add_argument("--port", type=int, default=8000, help="Port for the local HTTP server (default: 8000).")
    p_serve.add_argument("--bind", default="127.0.0.1", help="Bind address (default: 127.0.0.1).")

    base_dir = os.path.dirname(os.path.abspath(__file__))
    cwd = os.getcwd()
    parser.add_argument("--template-dir", default=os.path.join(base_dir, "templates", "post"))
    parser.add_argument("--workspace-base", default=os.path.join(cwd, "workspace"))
    parser.add_argument("--build-base", default=os.path.join(cwd, "build"))
    parser.add_argument("--root-dir", default=os.path.join(cwd, "root"))
    parser.add_argument("--base-url", default=None, help="Base URL for the sitemap and RSS feed. Overrides config.")
    parser.add_argument("--config", default=os.path.join(cwd, "config.json"), help="Path to a JSON config file.")
    return parser


def main() -> None:
    args = _build_parser().parse_args()
    handlers = {
        "init": run_init,
        "compile": run_compile,
        "submit": run_submit,
        "amend-all": run_amend_all,
        "recover": run_recover,
        "update": update_content,
        "upload": run_upload,
        "serve": run_serve,
    }
    command = str(args.command)
    handler = handlers[command]
    if command in _MUTATING_COMMANDS:
        with _mutating_cmd_lock(args, command):
            handler(args)
    else:
        handler(args)


if __name__ == "__main__":
    main()
