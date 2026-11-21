import glob
import hashlib
import html
import json
import os
import re
import shutil
from argparse import Namespace
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from core.assets import AssetContext, build_asset_ctx
from core.glyphs import refresh_glyphs
from core.hidden_text import first_desc
from core.page import CompileBuildRequest, HtmlBuildConfig, MobileCompileConfig, compile_html
from core.paths import (
    ASSET_HASH_LEN,
    DRIVER_DIR,
    EXTERNAL_LINK_ATTRS,
    TEMPLATES_DIR,
    WEB_DIR,
    is_external_href,
    page_url,
    reset_dir,
    resolve_base_url,
)
from core.svg import extract_friend_card_symbols
from core.typst import (
    hash_text_with_paths,
    make_inputs,
    plain_text,
    query_title_subtitle_from_source,
    query_typst,
    update_hash_with_typ_files,
)

PostEntry = Dict[str, Any]
PostsByDate = Dict[str, List[PostEntry]]

_REVISION_SUFFIX_RE = re.compile(r"-(\d+)$")
_ROOT_SHARED_FILENAMES = ("robots.txt", "favicon.ico")


def _append_revision_suffix(post_dir_name: str, title: str) -> str:
    rev_match = _REVISION_SUFFIX_RE.search(post_dir_name)
    return f"{title} Rev {rev_match.group(1)}" if rev_match else title


def _collect_day_posts(date_dir: str, date_str: str) -> List[PostEntry]:
    day_posts: List[PostEntry] = []
    for post_dir_name in os.listdir(date_dir):
        post_path = os.path.join(date_dir, post_dir_name)
        if not os.path.isdir(post_path):
            continue
        with open(os.path.join(post_path, "post.json"), "r", encoding="utf-8") as f:
            post_data = json.load(f)
        title = _append_revision_suffix(post_dir_name, post_data["title"])
        day_posts.append(
            {
                "name": title,
                "description": post_data.get("description", title),
                "subtitle": post_data.get("subtitle"),
                "link": f"./posts/{date_str}/{post_dir_name}/index.html",
                "time": os.path.getmtime(post_path),
                "date": date_str,
            }
        )
    day_posts.sort(key=lambda post: post["time"], reverse=True)
    return day_posts


def _collect_posts_by_date(posts_dir: str) -> PostsByDate:
    posts_by_date: PostsByDate = {}
    for date_str in sorted(os.listdir(posts_dir), reverse=True):
        date_dir = os.path.join(posts_dir, date_str)
        if not os.path.isdir(date_dir):
            continue
        day_posts = _collect_day_posts(date_dir, date_str)
        if day_posts:
            posts_by_date[date_str] = day_posts
    return posts_by_date


def _build_hidden_text(
    parsed_title: str,
    parsed_subtitle: str,
    posts_by_date: PostsByDate,
) -> str:
    sections: List[str] = [
        '<p class="post-nav">'
        '<a href="rss.xml" tabindex="-1">RSS</a> · '
        '<a href="friends/index.html" tabindex="-1">Friends</a>'
        "</p>",
        f"<h1>{html.escape(parsed_title)}</h1>",
    ]
    if parsed_subtitle:
        sections.append(f"<p>{html.escape(parsed_subtitle)}</p>")
    sections.append("<h1>Content</h1>")

    for date_str, day_posts in posts_by_date.items():
        sections.append(f"<h2>{html.escape(date_str)}</h2>")
        for post in day_posts:
            href = html.escape(post["link"])
            label = html.escape(post["name"])
            sections.append(f'<p><a href="{href}" tabindex="-1">{label}</a></p>')

    return "\n".join(sections)


def _build_sitemap_lines(base_url: str, posts_by_date: PostsByDate) -> List[str]:
    lines = [
        '<?xml version="1.0" encoding="UTF-8"?>',
        '<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">',
        f"  <url><loc>{html.escape(base_url + '/', quote=False)}</loc></url>",
    ]
    for day_posts in posts_by_date.values():
        for post in day_posts:
            rel_link = str(post["link"]).lstrip("./")
            lines.append(
                f"  <url><loc>{html.escape(base_url + '/' + rel_link, quote=False)}</loc></url>"
            )
    lines.append("</urlset>")
    return lines


def _format_rss_pub_date(date_str: str) -> str:
    parsed = datetime.strptime(date_str, "%Y-%m-%d").replace(tzinfo=timezone.utc)
    return parsed.strftime("%a, %d %b %Y %H:%M:%S +0000")


def _build_rss_lines(
    base_url: str,
    site_title: str,
    site_description: str,
    posts_by_date: PostsByDate,
) -> List[str]:
    feed_url = f"{base_url}/rss.xml"
    lines = [
        '<?xml version="1.0" encoding="UTF-8"?>',
        '<rss version="2.0" xmlns:atom="http://www.w3.org/2005/Atom">',
        "  <channel>",
        f"    <title>{html.escape(site_title, quote=False)}</title>",
        f"    <link>{html.escape(base_url + '/', quote=False)}</link>",
        f"    <description>{html.escape(site_description, quote=False)}</description>",
        "    <language>en-us</language>",
        f'    <atom:link href="{html.escape(feed_url)}" rel="self" type="application/rss+xml" />',
    ]

    all_posts: List[PostEntry] = [
        post for day_posts in posts_by_date.values() for post in day_posts
    ]
    last_build = (
        _format_rss_pub_date(str(all_posts[0]["date"]))
        if all_posts
        else datetime.now(timezone.utc).strftime("%a, %d %b %Y %H:%M:%S +0000")
    )
    lines.append(f"    <lastBuildDate>{last_build}</lastBuildDate>")

    for post in all_posts:
        rel_link = str(post["link"]).lstrip("./")
        post_url = f"{base_url}/{rel_link}"
        lines.extend(
            [
                "    <item>",
                f"      <title>{html.escape(str(post['name']), quote=False)}</title>",
                f"      <link>{html.escape(post_url, quote=False)}</link>",
                f'      <guid isPermaLink="true">{html.escape(post_url, quote=False)}</guid>',
                f"      <pubDate>{_format_rss_pub_date(str(post['date']))}</pubDate>",
                f"      <description>{html.escape(str(post['description']), quote=False)}</description>",
                "    </item>",
            ]
        )

    lines.extend(["  </channel>", "</rss>"])
    return lines


def _is_remote_avatar(avatar: str) -> bool:
    return avatar.startswith(("http://", "https://"))


def _build_friends_hidden_text(
    title: str,
    subtitle: Optional[str],
    friends: List[PostEntry],
    site_base_url: Optional[str] = None,
) -> str:
    parts: List[str] = [
        '<p class="post-nav"><a href="../index.html" tabindex="-1">Back</a></p>',
        f"<h1>{html.escape(title)}</h1>",
    ]
    if subtitle:
        parts.append(f"<p>{html.escape(subtitle)}</p>")
    for friend in friends:
        name = html.escape(str(friend["name"]))
        raw_url = str(friend["url"])
        url = html.escape(raw_url, quote=True)
        target_attrs = (
            EXTERNAL_LINK_ATTRS if is_external_href(raw_url, site_base_url) else ""
        )
        description = html.escape(str(friend.get("description", "")))
        parts.append(
            f'<p class="friend"><a href="{url}"{target_attrs} tabindex="-1">{name}</a>'
            f" — {description}</p>"
        )
    return "\n".join(parts)


def _friends_asset_hash(
    source: str,
    template_typ_path: str,
    friends_data_path: str,
    avatar_paths: List[str],
) -> str:
    hasher = hashlib.sha256()
    hasher.update(source.encode("utf-8"))
    update_hash_with_typ_files(hasher, [template_typ_path])
    for path in [friends_data_path, *avatar_paths]:
        hasher.update(path.encode("utf-8"))
        with open(path, "rb") as f:
            hasher.update(f.read())
    return hasher.hexdigest()[:ASSET_HASH_LEN]


def build_friends_page(
    *,
    workspace_base: str,
    root_dir: str,
    build_base: str,
    asset_context: AssetContext,
    base_url: str,
) -> None:
    with open(os.path.join(TEMPLATES_DIR, "friends.typ"), "r", encoding="utf-8") as f:
        friends_template = f.read()

    # Authored source lives in workspace_base (gitignored); update refreshes
    # the tracked snapshot under root/friends/source/ so fresh checkouts build.
    repo_root = os.path.dirname(DRIVER_DIR)
    workspace_data = os.path.join(workspace_base, "friends.typ")
    workspace_avatars = os.path.join(workspace_base, "friends-assets")
    friends_dir = os.path.join(root_dir, "friends")
    snapshot_dir = os.path.join(friends_dir, "source")
    snapshot_data = os.path.join(snapshot_dir, "friends.typ")
    snapshot_avatars = os.path.join(snapshot_dir, "friends-assets")
    if os.path.isfile(workspace_data):
        os.makedirs(snapshot_dir, exist_ok=True)
        shutil.copy2(workspace_data, snapshot_data)
        if os.path.isdir(snapshot_avatars):
            shutil.rmtree(snapshot_avatars)
        if os.path.isdir(workspace_avatars):
            shutil.copytree(workspace_avatars, snapshot_avatars)

    query_src = (
        friends_template.rstrip()
        + "\n#metadata((title: title, subtitle: subtitle, friends: friends)) <friends-meta>"
    )
    nodes = query_typst(query_src, query_root=repo_root, selector="<friends-meta>")
    meta_val = nodes[0]["value"] if nodes else {}
    title = plain_text(meta_val.get("title") or "") or "Friends"
    subtitle_raw = meta_val.get("subtitle")
    subtitle = (plain_text(subtitle_raw) or None) if subtitle_raw is not None else None
    friends = meta_val.get("friends") or []

    hidden_text = _build_friends_hidden_text(
        title, subtitle, friends, site_base_url=base_url
    )

    output_dir = os.path.join(build_base, "friends")
    reset_dir(output_dir)

    avatar_paths = [
        os.path.join(snapshot_avatars, friend["avatar"])
        for friend in friends
        if friend.get("avatar") and not _is_remote_avatar(friend["avatar"])
    ]
    asset_hash = _friends_asset_hash(
        friends_template,
        os.path.join(DRIVER_DIR, "template.typ"),
        snapshot_data,
        avatar_paths,
    )

    compile_html(
        CompileBuildRequest(
            source_bytes=friends_template.encode("utf-8"),
            output_dir=output_dir,
            asset_hash=asset_hash,
            typst_inputs=make_inputs(shared_inputs={"back_href": "../index.html"}),
            mobile=MobileCompileConfig(svg_name_prefix="m-friends"),
            html=HtmlBuildConfig(
                template_path=os.path.join(TEMPLATES_DIR, "index.html"),
                dest_dir=friends_dir,
                title_format="Friends Page {i}",
                svg_name_prefix="friends",
                default_title=title,
                asset_context=asset_context,
                description=subtitle or title,
                hidden_text=hidden_text,
                asset_dest_dir=os.path.join(friends_dir, "assets"),
                image_source_dir=repo_root,
                og_type="website",
                og_url=page_url(base_url, root_dir, friends_dir),
                shared_glyphs=False,
                site_base_url=base_url,
            ),
        )
    )


def update_content(args: Namespace) -> None:
    root_dir: str = args.root_dir
    os.makedirs(root_dir, exist_ok=True)

    copied_root_files: Dict[str, str] = {}
    for filename in _ROOT_SHARED_FILENAMES:
        dest_path = os.path.join(root_dir, filename)
        shutil.copy2(os.path.join(WEB_DIR, filename), dest_path)
        copied_root_files[filename] = dest_path

    dest_base_dir = os.path.join(root_dir, "posts")
    if not os.path.exists(dest_base_dir):
        return

    posts_by_date = _collect_posts_by_date(dest_base_dir)
    with open(os.path.join(TEMPLATES_DIR, "content.typ"), "r", encoding="utf-8") as f:
        content_template = f.read()

    repo_root = os.path.dirname(DRIVER_DIR)
    parsed_title, parsed_subtitle = query_title_subtitle_from_source(
        content_template.replace("{{POSTS}}", ""), repo_root
    )
    if parsed_subtitle is None:
        raise RuntimeError("Missing required subtitle declaration in Typst source.")

    posts_typst_lines: List[str] = []
    for date_str, day_posts in posts_by_date.items():
        posts_typst_lines.append(f"== {date_str}")
        for post in day_posts:
            posts_typst_lines.append(f'- #link("{post["link"]}")[{post["name"]}]')
        posts_typst_lines.append("")

    content_source = content_template.replace("{{POSTS}}", "\n".join(posts_typst_lines))
    hidden_text = _build_hidden_text(parsed_title, parsed_subtitle, posts_by_date)
    page_description = first_desc(hidden_text, parsed_subtitle)

    output_dir = os.path.join(args.build_base, "content")
    reset_dir(output_dir)

    asset_hash = hash_text_with_paths(
        content_source, [os.path.join(DRIVER_DIR, "template.typ")]
    )
    html_template = os.path.join(TEMPLATES_DIR, "index.html")
    asset_context = build_asset_ctx(root_dir)
    base_url = resolve_base_url(args)

    compile_html(
        CompileBuildRequest(
            source_bytes=content_source.encode("utf-8"),
            output_dir=output_dir,
            asset_hash=asset_hash,
            typst_inputs=make_inputs(shared_inputs={"back_href": "./index.html"}),
            mobile=MobileCompileConfig(),
            html=HtmlBuildConfig(
                template_path=html_template,
                dest_dir=root_dir,
                title_format="Content Page {i}",
                default_title=parsed_title,
                asset_context=asset_context,
                description=page_description,
                hidden_text=hidden_text,
                asset_dest_dir=os.path.join(root_dir, "assets"),
                rss_feed_path=os.path.join(root_dir, "rss.xml"),
                og_type="website",
                og_url=page_url(base_url, root_dir, root_dir, "index.html"),
                shared_glyphs=False,
                site_base_url=base_url,
            ),
        )
    )

    build_friends_page(
        workspace_base=args.workspace_base,
        root_dir=root_dir,
        build_base=args.build_base,
        asset_context=asset_context,
        base_url=base_url,
    )

    friends_dir = os.path.join(root_dir, "friends")
    glyph_page_dirs: List[str] = [root_dir, friends_dir]
    for date_str in sorted(os.listdir(dest_base_dir), reverse=True):
        date_dir = os.path.join(dest_base_dir, date_str)
        if not os.path.isdir(date_dir):
            continue
        for post_dir_name in os.listdir(date_dir):
            post_path = os.path.join(date_dir, post_dir_name)
            if os.path.isdir(post_path):
                glyph_page_dirs.append(post_path)

    refresh_glyphs(
        root_dir=root_dir,
        page_dirs=glyph_page_dirs,
        site_base_url=base_url,
    )

    # Lift friend cards into <symbol>s after glyph hoisting so hidden card
    # <use>s (revealed by friends.js) are never stripped by svgo.
    for pattern in ("friends[0-9]*.svg", "m-friends[0-9]*.svg"):
        for svg_path in glob.glob(os.path.join(friends_dir, "assets", pattern)):
            extract_friend_card_symbols(svg_path)

    sitemap_path = os.path.join(root_dir, "sitemap.xml")
    with open(sitemap_path, "w", encoding="utf-8") as f:
        f.write("\n".join(_build_sitemap_lines(base_url, posts_by_date)))

    rss_path = os.path.join(root_dir, "rss.xml")
    with open(rss_path, "w", encoding="utf-8") as f:
        f.write(
            "\n".join(
                _build_rss_lines(
                    base_url=base_url,
                    site_title=parsed_title,
                    site_description=parsed_subtitle,
                    posts_by_date=posts_by_date,
                )
            )
        )

    print(f"Content page updated in {root_dir}.")
    for filename in _ROOT_SHARED_FILENAMES:
        print(f"{filename} generated at {copied_root_files[filename]}.")
    print(f"Sitemap generated at {sitemap_path}.")
    print(f"RSS feed generated at {rss_path}.")
