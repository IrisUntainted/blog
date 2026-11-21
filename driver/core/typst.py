import hashlib
import json
import os
import shutil
import subprocess
import sys
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Set, Tuple, Union

from .paths import ASSET_HASH_LEN

_typst_path: Optional[str] = None
_TITLE_SUBTITLE_SELECTOR = "<title-subtitle>"


@dataclass(frozen=True)
class TypstInputs:
    svg: Dict[str, str]
    pdf: Dict[str, str]


@dataclass(frozen=True)
class PageMetadata:
    actions: Dict[str, Dict[str, str]]
    theme_colors: List[Tuple[str, str]]
    required_assets: Set[str]
    doc_elements: List[Dict[str, Any]]


def _resolve_typst_path() -> str:
    global _typst_path
    if _typst_path is None:
        _typst_path = shutil.which("typst") or shutil.which("typst.exe")
    if not _typst_path:
        print("Typst executable not found in PATH.", file=sys.stderr)
        sys.exit(1)
    return _typst_path


def make_inputs(shared_inputs: Optional[Dict[str, str]] = None) -> TypstInputs:
    base_inputs = {"with_driver": "true"}
    if shared_inputs:
        base_inputs.update(shared_inputs)

    svg = dict(base_inputs)
    svg["export_format"] = "svg"

    pdf = dict(base_inputs)
    pdf["export_format"] = "pdf"

    return TypstInputs(svg=svg, pdf=pdf)


def pdf_creation_timestamp(inputs: Optional[Dict[str, str]]) -> Optional[str]:
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


def compile_typst(
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
        subprocess.run(command, check=True, input=source_bytes)
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


def query_typst(
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

    try:
        result = subprocess.run(
            command,
            check=True,
            capture_output=True,
            cwd=query_root,
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
        return json.loads(raw)
    except json.JSONDecodeError as e:
        raise RuntimeError(f"Failed to parse Typst query output: {e}") from e


def query_page_metadata(
    source_bytes: bytes,
    query_root: str,
    inputs: Optional[Dict[str, str]] = None,
    include_doc: bool = False,
) -> PageMetadata:
    selector = (
        "selector(<driver-action>)"
        ".or(<driver-theme-color>)"
        ".or(<driver-assets>)"
    )
    if include_doc:
        selector += ".or(<driver-doc>)"

    actions: Dict[str, Dict[str, str]] = {}
    theme_colors: List[Tuple[str, str]] = []
    seen_colors: Set[str] = set()
    required_assets: Set[str] = set()
    doc_elements: List[Dict[str, Any]] = []

    for node in query_typst(source_bytes, query_root, selector, inputs=inputs):
        label = node.get("label")
        value = node.get("value")
        if label == "<driver-action>" and isinstance(value, dict):
            action_val = value.get("a")
            if isinstance(action_val, str) and action_val:
                actions[action_val] = {
                    "label": str(value.get("label") or ""),
                    "role": str(value.get("role") or ""),
                    "tabindex": str(value.get("tabindex") or ""),
                }
        elif label == "<driver-theme-color>" and isinstance(value, dict):
            light, dark = value.get("light"), value.get("dark")
            if isinstance(light, str) and isinstance(dark, str):
                key = light.lower()
                if key not in seen_colors:
                    seen_colors.add(key)
                    theme_colors.append((key, dark.lower()))
        elif label == "<driver-assets>":
            if isinstance(value, str):
                required_assets.add(value)
            elif isinstance(value, list):
                required_assets.update(v for v in value if isinstance(v, str))
        elif label == "<driver-doc>" and isinstance(value, dict):
            doc_elements.append(value)

    return PageMetadata(
        actions=actions,
        theme_colors=theme_colors,
        required_assets=required_assets,
        doc_elements=doc_elements,
    )


def flatten_text(node: Any, skip_cond_links: bool = False) -> str:
    if isinstance(node, str):
        return node
    if isinstance(node, list):
        return "".join(flatten_text(item, skip_cond_links) for item in node)
    if not isinstance(node, dict):
        return ""

    func = node.get("func")
    if func == "space":
        return " "
    if func == "linebreak":
        return "\n"
    if func == "smartquote":
        return '"' if node.get("double") else "'"
    if skip_cond_links and func == "link":
        dest = node.get("dest", "")
        if isinstance(dest, str) and dest.startswith("#cond="):
            return ""
        return flatten_text(node.get("body", ""), skip_cond_links)

    parts: List[str] = []
    text = node.get("text")
    if isinstance(text, str):
        parts.append(text)
    for key in ("body", "children", "child", "value", "values", "content"):
        if key in node:
            parts.append(flatten_text(node[key], skip_cond_links))
    if not parts:
        for key, value in node.items():
            if key not in ("func", "text") and isinstance(value, (dict, list)):
                parts.append(flatten_text(value, skip_cond_links))
    return "".join(parts)


def plain_text(node: Any, skip_cond_links: bool = False) -> str:
    return " ".join(flatten_text(node, skip_cond_links).split())


def extract_table_rows(table_payload: Dict[str, Any]) -> List[List[Dict[str, Any]]]:
    columns_raw = table_payload["columns"]
    column_count = (
        len(columns_raw) if isinstance(columns_raw, list) else int(columns_raw)
    )

    cell_nodes: List[Dict[str, Any]] = []
    for child in table_payload["children"]:
        if child.get("func") != "cell":
            continue
        body = child["body"]
        cell_nodes.append(
            {
                "text": plain_text(body),
                "is_inline_code": bool(
                    isinstance(body, dict)
                    and body.get("func") == "raw"
                    and not bool(body.get("block", False))
                ),
                "colspan": child.get("colspan", 1),
                "rowspan": child.get("rowspan", 1),
            }
        )

    rows: List[List[Dict[str, Any]]] = [[]]
    occupied: Dict[Tuple[int, int], bool] = {}
    row_index = 0
    col_index = 0

    def advance_to_free_slot(start_row: int, start_col: int) -> Tuple[int, int]:
        r, c = start_row, start_col
        while True:
            while c < column_count and occupied.get((r, c), False):
                c += 1
            if c < column_count:
                return r, c
            r += 1
            c = 0
            while r >= len(rows):
                rows.append([])

    for cell in cell_nodes:
        row_index, col_index = advance_to_free_slot(row_index, col_index)
        while row_index >= len(rows):
            rows.append([])

        rowspan = int(cell["rowspan"])
        colspan = int(cell["colspan"])
        rows[row_index].append(cell)
        for ro in range(rowspan):
            target_row = row_index + ro
            while target_row >= len(rows):
                rows.append([])
            for co in range(colspan):
                occupied[(target_row, col_index + co)] = True

        col_index += colspan

    if occupied:
        row_count = max(row for row, _ in occupied) + 1
        while len(rows) < row_count:
            rows.append([])
        if len(rows) > row_count:
            rows = rows[:row_count]

    return rows


def _declared_text(value: Any) -> Optional[str]:
    if value is None:
        return None
    return plain_text(value) or None


def _fetch_title_subtitle(
    query_source: str, query_root: str, missing_context: str
) -> Tuple[str, Optional[str]]:
    nodes = query_typst(query_source, query_root=query_root, selector=_TITLE_SUBTITLE_SELECTOR)
    value = nodes[0]["value"] if nodes else {}
    title = _declared_text(value.get("title"))
    subtitle = _declared_text(value.get("subtitle"))
    if title is None:
        raise RuntimeError(f"Missing required title declaration in {missing_context}.")
    return title, subtitle


def query_title_subtitle(typ_path: str, query_root: str) -> Tuple[str, Optional[str]]:
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
        prefix = f"{path_index}:"
        if os.path.isfile(abs_path) and abs_path.endswith(".typ"):
            typ_files.setdefault(f"{prefix}{os.path.basename(abs_path)}", abs_path)
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
                    typ_files.setdefault(f"{prefix}{rel_path}", file_path)
    return sorted(typ_files.items(), key=lambda item: item[0])


def update_hash_with_typ_files(hasher: "hashlib._Hash", paths: List[str]) -> None:
    for logical_path, file_path in _collect_typ_files(paths):
        hasher.update(logical_path.encode("utf-8"))
        with open(file_path, "rb") as f:
            hasher.update(f.read())


def sources_hash(paths: List[str], length: int = ASSET_HASH_LEN) -> str:
    hasher = hashlib.sha256()
    update_hash_with_typ_files(hasher, paths)
    return hasher.hexdigest()[:length]


def hash_text_with_paths(
    text: str, paths: List[str], length: int = ASSET_HASH_LEN
) -> str:
    hasher = hashlib.sha256()
    hasher.update(text.encode("utf-8"))
    update_hash_with_typ_files(hasher, paths)
    return hasher.hexdigest()[:length]
