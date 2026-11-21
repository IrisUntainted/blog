import html
import re
import threading
from dataclasses import dataclass
from typing import Dict, List, Optional, Tuple

import fitz

from . import xmlutil
from .paths import EXTERNAL_LINK_ATTRS, is_external_href

_FITZ_LOCK = threading.Lock()

_ACTION_PREFIX = "#action="
_SKIP_PREFIXES = ("#cond=", "driver-image://", "driver-remote-image://")
_TRANSLATE_RE = re.compile(
    r"translate\(\s*([-0-9.eE+]+)(?:[\s,]+([-0-9.eE+]+))?\s*\)"
)
_MATRIX_RE = re.compile(
    r"matrix\(\s*1\s+0\s+0\s+-1\s+([-0-9.eE+]+)\s+([-0-9.eE+]+)\s*\)"
)
_CARD_CLIP_RE = re.compile(r"url\(#.+\)")

_CondChain = Tuple[Tuple[str, str], ...]
_SvgCtx = Tuple[_CondChain, int]
_BBox = Tuple[float, float, float, float]


@dataclass(frozen=True)
class PageOverlay:
    html: str
    width: float


@dataclass
class _GlyphRun:
    x: float
    y: float
    cond_chain: _CondChain
    card_idx: int
    used: bool = False


@dataclass
class _AnchorCtx:
    href: str
    x0: float
    y0: float
    x1: float
    y1: float
    cond_chain: _CondChain
    card_idx: int
    used: bool = False


@dataclass(frozen=True)
class _HitRect:
    kind: str  # "link" or "action"
    target: str
    x0: float
    y0: float
    x1: float
    y1: float
    cond_chain: _CondChain
    card_idx: int


@dataclass
class _Segment:
    text: str
    x0: float
    y0: float
    x1: float
    y1: float
    size: float
    is_mono: bool
    is_bold: bool
    is_italic: bool
    hit_kind: str
    hit_target: str
    cond_chain: _CondChain
    card_idx: int
    sep: str = ""


@dataclass
class _ExtractedLine:
    block_idx: int
    x0: float
    y0: float
    x1: float
    y1: float
    size: float
    is_mono: bool
    cond_chain: _CondChain
    card_idx: int
    segments: List[_Segment]


def _collect_svg_contexts(
    svg_path: str, friend_cards: bool
) -> Tuple[List[_GlyphRun], List[_AnchorCtx], List[float]]:
    """Collect glyph runs, interactive anchor boxes, and card Y coordinates from a patched pre-SVGO SVG."""
    with open(svg_path, "r", encoding="utf-8") as f:
        svg_text = f.read()
    if not friend_cards and "data-cond-id=" not in svg_text:
        return [], [], []
    root = xmlutil.parse_svg(svg_text)

    glyph_runs: List[_GlyphRun] = []
    anchor_contexts: List[_AnchorCtx] = []
    card_ys: List[float] = []

    def walk(
        el,
        dx: float,
        dy: float,
        cond_chain: _CondChain,
        card_idx: int,
    ) -> None:
        if not isinstance(el.tag, str):
            return
        tag = xmlutil.local(el.tag)
        if tag in ("defs", "style"):
            return

        tf = el.get("transform") or ""
        m_tr = _TRANSLATE_RE.fullmatch(tf)
        if m_tr:
            dx += float(m_tr.group(1))
            dy += float(m_tr.group(2) or 0.0)

        if (
            friend_cards
            and card_idx == -1
            and tag == "g"
            and m_tr is not None
            and _CARD_CLIP_RE.fullmatch(el.get("clip-path") or "")
        ):
            card_idx = len(card_ys)
            card_ys.append(dy)

        cond_id = el.get("data-cond-id")
        if cond_id is not None:
            cond_chain = cond_chain + ((cond_id, el.get("data-cond-branch") or "0"),)

        if tag == "g":
            m_mx = _MATRIX_RE.fullmatch(tf)
            if m_mx and any(
                isinstance(c.tag, str) and xmlutil.local(c.tag) == "use" for c in el
            ):
                glyph_runs.append(
                    _GlyphRun(
                        x=dx + float(m_mx.group(1)),
                        y=dy + float(m_mx.group(2)),
                        cond_chain=cond_chain,
                        card_idx=card_idx,
                    )
                )
                return

        if tag == "a":
            href = (xmlutil.attr_local(el, "href") or "").strip()
            if href and not href.startswith(_SKIP_PREFIXES):
                for c in el.iter():
                    if isinstance(c.tag, str) and xmlutil.local(c.tag) == "rect":
                        w = float(c.get("width") or 0.0)
                        h = float(c.get("height") or 0.0)
                        if w > 0.0 and h > 0.0:
                            rx = dx + float(c.get("x") or 0.0)
                            ry = dy + float(c.get("y") or 0.0)
                            anchor_contexts.append(
                                _AnchorCtx(
                                    href=href,
                                    x0=rx,
                                    y0=ry,
                                    x1=rx + w,
                                    y1=ry + h,
                                    cond_chain=cond_chain,
                                    card_idx=card_idx,
                                )
                            )
                        break

        for child in el:
            walk(child, dx, dy, cond_chain, card_idx)

    walk(root, 0.0, 0.0, (), -1)
    return glyph_runs, anchor_contexts, card_ys


def _match_anchor_context(
    anchor_contexts: List[_AnchorCtx],
    raw_href: str,
    rect: _BBox,
) -> _SvgCtx:
    is_action = raw_href.startswith(_ACTION_PREFIX)
    x0, y0, x1, y1 = rect
    best: Optional[_AnchorCtx] = None
    best_dist = 1.5
    for ctx in anchor_contexts:
        if ctx.used:
            continue
        if is_action and ctx.href != raw_href:
            continue
        if not is_action and ctx.href.startswith(_ACTION_PREFIX):
            continue
        dist = max(
            abs(ctx.x0 - x0),
            abs(ctx.y0 - y0),
            abs(ctx.x1 - x1),
            abs(ctx.y1 - y1),
        )
        if dist <= best_dist:
            best = ctx
            best_dist = dist
            if dist < 0.05:
                break
    if best is None:
        return (), -1
    best.used = True
    return best.cond_chain, best.card_idx


def _extract_page_hits(
    pdf_page,
    page_idx: int,
    anchor_contexts: List[_AnchorCtx],
    svg_href_rewrites: Optional[Dict[str, str]],
) -> Tuple[List[_HitRect], Optional[Tuple[float, float]]]:
    hits: List[_HitRect] = []
    top_bar_band: Optional[Tuple[float, float]] = None
    rewrites = svg_href_rewrites or {}

    for lnk in pdf_page.get_links():
        raw_href = (lnk.get("uri") or lnk.get("file") or "").strip()
        if not raw_href or raw_href.startswith(_SKIP_PREFIXES):
            continue
        r = lnk["from"]
        x0, y0, x1, y1 = float(r.x0), float(r.y0), float(r.x1), float(r.y1)
        if page_idx == 0 and raw_href == "#action=theme" and y1 < 45.0:
            top_bar_band = (y0 - 2.0, y1 + 2.0)

        cond_chain, card_idx = _match_anchor_context(
            anchor_contexts, raw_href, (x0, y0, x1, y1)
        )
        if raw_href.startswith(_ACTION_PREFIX):
            kind = "action"
            target = raw_href[len(_ACTION_PREFIX) :]
        else:
            kind = "link"
            target = rewrites.get(raw_href, raw_href)

        hits.append(
            _HitRect(
                kind=kind,
                target=target,
                x0=x0,
                y0=y0,
                x1=x1,
                y1=y1,
                cond_chain=cond_chain,
                card_idx=card_idx,
            )
        )

    return hits, top_bar_band


def _match_glyph_run(
    glyph_runs: List[_GlyphRun], origin_x: float, origin_y: float
) -> Optional[_SvgCtx]:
    for run in glyph_runs:
        if run.used:
            continue
        if abs(run.x - origin_x) <= 1.2 and abs(run.y - origin_y) <= 1.2:
            run.used = True
            return run.cond_chain, run.card_idx
    return None


def _cond_prefix_matches(
    hit_chain: _CondChain,
    span_chain: _CondChain,
) -> bool:
    if not hit_chain:
        return True
    return len(span_chain) >= len(hit_chain) and span_chain[: len(hit_chain)] == hit_chain


def _find_char_hit(
    hits: List[_HitRect],
    bbox: _BBox,
    cond_chain: _CondChain,
    card_idx: int,
) -> Tuple[str, str]:
    cx0, cy0, cx1, cy1 = bbox
    char_h = max(cy1 - cy0, 0.1)
    mid_x = 0.5 * (cx0 + cx1)

    best_hit: Optional[_HitRect] = None
    best_key: Optional[Tuple[int, float]] = None
    for hit in hits:
        if hit.card_idx != -1 and hit.card_idx != card_idx:
            continue
        if not _cond_prefix_matches(hit.cond_chain, cond_chain):
            continue
        v_overlap = min(cy1, hit.y1) - max(cy0, hit.y0)
        if v_overlap < 0.5 * char_h:
            continue
        if not (hit.x0 - 0.5 <= mid_x <= hit.x1 + 0.5):
            continue
        area = (hit.x1 - hit.x0) * (hit.y1 - hit.y0)
        key = (0 if hit.cond_chain else 1, area)
        if best_key is None or key < best_key:
            best_key = key
            best_hit = hit
    if best_hit is None:
        return "", ""
    return best_hit.kind, best_hit.target


def _conds_compatible(
    chain_a: _CondChain,
    chain_b: _CondChain,
) -> bool:
    if not chain_a or not chain_b:
        return True
    map_a = dict(chain_a)
    for cid, branch in chain_b:
        if cid in map_a and map_a[cid] != branch:
            return False
    return True


def _font_flags(font_name: str) -> Tuple[bool, bool, bool]:
    is_mono = any(k in font_name for k in ("Mono", "Courier", "Consolas", "Code"))
    is_bold = "Bold" in font_name or "Semibold" in font_name
    is_italic = "Italic" in font_name or "Oblique" in font_name
    return is_mono, is_bold, is_italic


def _clean_char(raw: str) -> str:
    return raw.replace("\u200b", "").replace("\xad", "")


def _is_footer_block(block: Dict, page_h: float) -> bool:
    lines = block.get("lines") or []
    if not lines:
        return False
    min_y0 = min(float(line["bbox"][1]) for line in lines)
    if min_y0 < page_h - 26.0:
        return False
    text = "".join(
        _clean_char(c["c"])
        for line in lines
        for span in line.get("spans", ())
        for c in span.get("chars", ())
    )
    stripped = text.strip()
    return "Compiled on " in text or not stripped or stripped.isdigit()


def _resolve_span_contexts(
    line_spans: List[Dict], glyph_runs: List[_GlyphRun]
) -> List[_SvgCtx]:
    if not glyph_runs:
        return [((), -1)] * len(line_spans)

    contexts: List[Optional[_SvgCtx]] = [None] * len(line_spans)
    last_matched: Optional[_SvgCtx] = None
    last_oy: Optional[float] = None
    for si, span in enumerate(line_spans):
        raw_chars = span.get("chars") or ()
        if not any(_clean_char(c["c"]).strip() for c in raw_chars):
            continue
        ox, oy = (float(v) for v in span["origin"])
        matched = _match_glyph_run(glyph_runs, ox, oy)
        if (
            matched is None
            and last_matched is not None
            and last_oy is not None
            and abs(oy - last_oy) <= 1.2
        ):
            matched = last_matched
        ctx = matched if matched is not None else ((), -1)
        contexts[si] = ctx
        last_matched = ctx
        last_oy = oy

    # Backfill whitespace-only spans from adjacent matched spans on the same line.
    first_ctx = next((c for c in contexts if c is not None), ((), -1))
    cur_ctx = first_ctx
    resolved: List[_SvgCtx] = []
    for ctx in contexts:
        if ctx is not None:
            cur_ctx = ctx
        resolved.append(cur_ctx)
    return resolved


def _extract_page_lines(
    pdf_page,
    page_h: float,
    glyph_runs: List[_GlyphRun],
    hits: List[_HitRect],
    top_bar_band: Optional[Tuple[float, float]],
) -> List[_ExtractedLine]:
    raw = pdf_page.get_text(
        "rawdict", flags=fitz.TEXTFLAGS_RAWDICT | fitz.TEXT_INHIBIT_SPACES
    )
    extracted: List[_ExtractedLine] = []

    for block_idx, block in enumerate(raw.get("blocks", ())):
        if block.get("type") != 0:
            continue
        if _is_footer_block(block, page_h):
            continue

        for line in block.get("lines", ()):
            lx0, ly0, lx1, ly1 = (float(v) for v in line["bbox"])
            if top_bar_band is not None and ly1 >= top_bar_band[0] and ly0 <= top_bar_band[1]:
                continue

            line_spans = list(line.get("spans", ()))
            span_contexts = _resolve_span_contexts(line_spans, glyph_runs)

            segments: List[_Segment] = []
            prev_key: Optional[Tuple] = None
            prev_ch = ""
            prev_cx1 = 0.0

            for si, span in enumerate(line_spans):
                raw_chars = span.get("chars") or ()
                if not raw_chars:
                    continue
                cond_chain, card_idx = span_contexts[si]

                if any(cid.startswith("ui:checkbox:") for cid, _ in cond_chain):
                    continue

                _, oy = (float(v) for v in span["origin"])
                size = float(span["size"])
                is_mono, is_bold, is_italic = _font_flags(str(span.get("font") or ""))

                for char_info in raw_chars:
                    ch = _clean_char(char_info["c"])
                    if not ch:
                        continue
                    cx0, cy0, cx1, cy1 = (float(v) for v in char_info["bbox"])
                    hit_kind, hit_target = _find_char_hit(
                        hits, (cx0, cy0, cx1, cy1), cond_chain, card_idx
                    )
                    key = (
                        cond_chain,
                        card_idx,
                        hit_kind,
                        hit_target,
                        is_mono,
                        is_bold,
                        is_italic,
                        round(size, 2),
                        round(oy, 1),
                    )
                    gap = cx0 - prev_cx1
                    intra_jump = (
                        prev_key == key
                        and not is_mono
                        and prev_ch != " "
                        and ch != " "
                        and gap > 0.45 * size
                    )
                    if prev_key != key or intra_jump:
                        if intra_jump and segments:
                            segments[-1].sep = "t" if gap >= 3.0 * size else "s"
                        segments.append(
                            _Segment(
                                text=ch,
                                x0=cx0,
                                y0=cy0,
                                x1=cx1,
                                y1=cy1,
                                size=size,
                                is_mono=is_mono,
                                is_bold=is_bold,
                                is_italic=is_italic,
                                hit_kind=hit_kind,
                                hit_target=hit_target,
                                cond_chain=cond_chain,
                                card_idx=card_idx,
                            )
                        )
                        prev_key = key
                    else:
                        seg = segments[-1]
                        seg.text += ch
                        seg.x0 = min(seg.x0, cx0)
                        seg.y0 = min(seg.y0, cy0)
                        seg.x1 = max(seg.x1, cx1)
                        seg.y1 = max(seg.y1, cy1)
                    prev_ch = ch
                    prev_cx1 = cx1

            # Drop non-mono whitespace-only segments while preserving inter-word space.
            filtered: List[_Segment] = []
            for seg in segments:
                if not seg.is_mono and not seg.text.strip():
                    if filtered and not filtered[-1].text.endswith(" "):
                        filtered[-1].sep = filtered[-1].sep or "s"
                    continue
                filtered.append(seg)

            if not filtered:
                continue

            # Compute intra-line separators between compatible consecutive segments.
            for idx in range(len(filtered) - 1):
                cur = filtered[idx]
                nxt = _next_compatible(filtered, idx)
                if nxt is None:
                    continue
                if cur.text.endswith(" ") or nxt.text.startswith(" "):
                    if cur.sep == "s":
                        cur.sep = ""
                    continue
                if cur.sep:
                    continue
                gap = nxt.x0 - cur.x1
                em = max(cur.size, nxt.size)
                if gap >= 3.0 * em:
                    cur.sep = "t"
                elif gap > 0.40 * em:
                    cur.sep = "s"

            extracted.append(
                _ExtractedLine(
                    block_idx=block_idx,
                    x0=min(s.x0 for s in filtered),
                    y0=min(s.y0 for s in filtered),
                    x1=max(s.x1 for s in filtered),
                    y1=max(s.y1 for s in filtered),
                    size=max(s.size for s in filtered),
                    is_mono=all(s.is_mono for s in filtered),
                    cond_chain=filtered[-1].cond_chain,
                    card_idx=filtered[-1].card_idx,
                    segments=filtered,
                )
            )

    return extracted


def _next_compatible(items, idx: int):
    chain = items[idx].cond_chain
    for nxt in items[idx + 1 :]:
        if _conds_compatible(chain, nxt.cond_chain):
            return nxt
    return None


def _assign_line_end_seps(
    lines: List[_ExtractedLine], has_next_page: bool
) -> None:
    for idx, line in enumerate(lines):
        last_seg = line.segments[-1]
        nxt = _next_compatible(lines, idx)
        if last_seg.sep:
            if (
                last_seg.sep == "s"
                and nxt is not None
                and nxt.segments[0].text.startswith(" ")
            ):
                last_seg.sep = ""
            continue
        if nxt is None:
            if (has_next_page or line.card_idx >= 0) and not last_seg.text.endswith(" "):
                last_seg.sep = "nn"
            continue

        if line.card_idx >= 0 and nxt.card_idx != line.card_idx:
            last_seg.sep = "nn"
            continue

        if (
            abs(nxt.y0 - line.y0) < 0.4 * line.size
            and nxt.x0 > line.x1 + 0.5 * line.size
        ):
            last_seg.sep = "t"
        elif nxt.block_idx == line.block_idx or (nxt.y0 - line.y1) < 0.45 * line.size:
            last_seg.sep = (
                "" if (not line.is_mono and last_seg.text.endswith(" ")) else "n"
            )
        else:
            last_seg.sep = "nn"


def fmt_num(value: float, digits: int = 2) -> str:
    rounded = round(value, digits) + 0.0
    return f"{rounded:.{digits}f}".rstrip("0").rstrip(".") or "0"


def _fmt_pct(value: float, total: float) -> str:
    return fmt_num(value / total * 100.0, 3) if total > 0 else "0"


def _render_el(
    cls: str,
    style: str,
    kind: str,
    target: str,
    site_base_url: Optional[str],
    text: str = "",
    sep: str = "",
) -> str:
    sep_attr = f' data-sep="{sep}"' if sep else ""
    escaped_text = html.escape(text, quote=False) if text else ""
    if kind == "link":
        target_attrs = (
            EXTERNAL_LINK_ATTRS
            if is_external_href(target, site_base_url)
            else ' target="_top"'
        )
        return (
            f'<a class="{cls}" href="{html.escape(target, quote=True)}"'
            f'{target_attrs} tabindex="-1" draggable="false" style="{style}"{sep_attr}>{escaped_text}</a>'
        )
    action_attr = (
        f' data-action="{html.escape(target, quote=True)}"' if kind == "action" else ""
    )
    return f'<span class="{cls}"{action_attr} style="{style}"{sep_attr}>{escaped_text}</span>'


def _render_hit_html(
    hit: _HitRect,
    page_w: float,
    page_h: float,
    site_base_url: Optional[str],
) -> str:
    left = _fmt_pct(hit.x0, page_w)
    top = _fmt_pct(hit.y0, page_h)
    width = _fmt_pct(max(0.0, hit.x1 - hit.x0), page_w)
    height = _fmt_pct(max(0.0, hit.y1 - hit.y0), page_h)
    style = f"left:{left}%;top:{top}%;width:{width}%;height:{height}%"
    return _render_el("ov-hit", style, hit.kind, hit.target, site_base_url)


def _render_segment_html(
    seg: _Segment,
    page_w: float,
    page_h: float,
    site_base_url: Optional[str],
) -> str:
    classes = ["ov-s"]
    if seg.is_mono:
        classes.append("ov-m")
    if seg.is_bold:
        classes.append("ov-b")
    if seg.is_italic:
        classes.append("ov-i")

    left = _fmt_pct(seg.x0, page_w)
    top = _fmt_pct(seg.y0, page_h)
    w = fmt_num(max(0.0, seg.x1 - seg.x0))
    fs = fmt_num(seg.size)
    style = f"left:{left}%;top:{top}%;--w:{w};--fs:{fs}"
    return _render_el(
        " ".join(classes),
        style,
        seg.hit_kind,
        seg.hit_target,
        site_base_url,
        text=seg.text,
        sep=seg.sep,
    )


def _emit_with_cond_wrappers(
    items: List[Tuple[_CondChain, str]],
) -> str:
    parts: List[str] = []
    open_conds: List[Tuple[str, str]] = []

    def sync_conds(target: _CondChain) -> None:
        common = 0
        while (
            common < len(open_conds)
            and common < len(target)
            and open_conds[common] == target[common]
        ):
            common += 1
        while len(open_conds) > common:
            open_conds.pop()
            parts.append("</div>")
        for cid, branch in target[common:]:
            open_conds.append((cid, branch))
            parts.append(
                f'<div class="ov-cond" data-cond-id="{html.escape(cid, quote=True)}" '
                f'data-cond-branch="{html.escape(branch, quote=True)}">'
            )

    for cond_chain, item_html in items:
        sync_conds(cond_chain)
        parts.append(item_html)
    sync_conds(())
    return "".join(parts)


def build_svg_overlays(
    pdf_bytes: bytes,
    svg_paths: List[str],
    *,
    svg_href_rewrites: Optional[Dict[str, str]] = None,
    site_base_url: Optional[str] = None,
    friend_cards: bool = False,
) -> List[PageOverlay]:
    """Build PDF.js-style HTML text overlays aligned with each SVG page."""
    if not svg_paths:
        return []

    svg_contexts = [
        _collect_svg_contexts(svg_path, friend_cards=friend_cards)
        for svg_path in svg_paths
    ]

    extracted_pages: List[
        Tuple[float, float, List[_HitRect], List[_ExtractedLine], List[float]]
    ] = []
    with _FITZ_LOCK:
        doc = fitz.open(stream=pdf_bytes, filetype="pdf")
        try:
            total_pages = min(len(doc), len(svg_paths))
            for page_idx in range(total_pages):
                pdf_page = doc[page_idx]
                page_w = float(pdf_page.rect.width)
                page_h = float(pdf_page.rect.height)
                glyph_runs, anchor_contexts, card_ys = svg_contexts[page_idx]
                hits, top_bar_band = _extract_page_hits(
                    pdf_page, page_idx, anchor_contexts, svg_href_rewrites
                )
                lines = _extract_page_lines(
                    pdf_page, page_h, glyph_runs, hits, top_bar_band
                )
                extracted_pages.append((page_w, page_h, hits, lines, card_ys))
        finally:
            doc.close()

    overlays: List[PageOverlay] = []
    for page_idx, (page_w, page_h, hits, lines, card_ys) in enumerate(extracted_pages):
        _assign_line_end_seps(lines, has_next_page=(page_idx + 1 < total_pages))
        hits.sort(key=lambda h: -((h.x1 - h.x0) * (h.y1 - h.y0)))

        top_items: List[Tuple[_CondChain, str]] = []
        card_items: Dict[int, List[Tuple[_CondChain, str]]] = {
            idx: [] for idx in range(len(card_ys))
        }

        for hit in hits:
            hit_html = _render_hit_html(hit, page_w, page_h, site_base_url)
            card_items.get(hit.card_idx, top_items).append((hit.cond_chain, hit_html))

        for line in lines:
            for seg in line.segments:
                seg_html = _render_segment_html(seg, page_w, page_h, site_base_url)
                card_items.get(seg.card_idx, top_items).append(
                    (seg.cond_chain, seg_html)
                )

        page_parts = [_emit_with_cond_wrappers(top_items)]
        for card_idx, card_y in enumerate(card_ys):
            inner = _emit_with_cond_wrappers(card_items[card_idx])
            page_parts.append(
                f'<div class="ov-card" data-card-idx="{card_idx}" '
                f'data-card-y="{_fmt_pct(card_y, page_h)}">{inner}</div>'
            )

        overlays.append(PageOverlay(html="".join(page_parts), width=page_w))

    return overlays
