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

from .shared import (
    page_url,
    build_asset_ctx,
    driver_dir,
    refresh_glyphs,
    resolve_base_url,
)
from .utils import (
    _ASSET_HASH_LENGTH,
    _query_nodes_from_source,
    _update_hash_with_typ_files,
    CompileBuildRequest,
    HtmlBuildConfig,
    MobileCompileConfig,
    compile_html,
    extract_friend_card_symbols,
    first_desc,
    hash_text_with_paths,
    make_inputs,
    minify_html,
    query_title_subtitle_from_source,
    reset_dir,
)


PostEntry = Dict[str, Any]
PostsByDate = Dict[str, List[PostEntry]]

_REVISION_SUFFIX_PATTERN = re.compile(r"-(\d+)$")
_ROOT_SHARED_FILENAMES = ("robots.txt", "favicon.ico")


def _append_revision_suffix(post_dir_name: str, title: str) -> str:
    rev_match = _REVISION_SUFFIX_PATTERN.search(post_dir_name)
    if not rev_match:
        return title
    return f"{title} Rev {rev_match.group(1)}"

def _collect_day_posts(date_dir: str, date_str: str) -> List[PostEntry]:
    day_posts: List[PostEntry] = []
    for post_dir_name in os.listdir(date_dir):
        post_path = os.path.join(date_dir, post_dir_name)
        if not os.path.isdir(post_path):
            continue

        post_json_path = os.path.join(post_path, "post.json")
        with open(post_json_path, "r", encoding="utf-8") as _f:
            post_data = json.load(_f)
        title = _append_revision_suffix(post_dir_name, post_data["title"])
        subtitle = post_data.get("subtitle")
        description = post_data.get("description", title)

        day_posts.append(
            {
                "name": title,
                "description": description,
                "subtitle": subtitle,
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
    sections: List[str] = [f"<h1>{html.escape(parsed_title)}</h1>"]
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
    sitemap_lines = [
        '<?xml version="1.0" encoding="UTF-8"?>',
        '<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">',
        f"  <url><loc>{html.escape(base_url + '/', quote=False)}</loc></url>",
    ]

    for day_posts in posts_by_date.values():
        for post in day_posts:
            rel_link_clean = str(post["link"]).lstrip("./")
            sitemap_lines.append(
                f"  <url><loc>{html.escape(base_url + '/' + rel_link_clean, quote=False)}</loc></url>"
            )

    sitemap_lines.append("</urlset>")
    return sitemap_lines


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
    rss_lines = [
        '<?xml version="1.0" encoding="UTF-8"?>',
        '<rss version="2.0" xmlns:atom="http://www.w3.org/2005/Atom">',
        "  <channel>",
        f"    <title>{html.escape(site_title, quote=False)}</title>",
        f"    <link>{html.escape(base_url + '/', quote=False)}</link>",
        f"    <description>{html.escape(site_description, quote=False)}</description>",
        "    <language>en-us</language>",
        f'    <atom:link href="{html.escape(feed_url)}" rel="self" type="application/rss+xml" />',
    ]

    all_posts: List[PostEntry] = []
    for day_posts in posts_by_date.values():
        all_posts.extend(day_posts)

    if all_posts:
        rss_lines.append(
            f"    <lastBuildDate>{_format_rss_pub_date(str(all_posts[0]['date']))}</lastBuildDate>"
        )
    else:
        rss_lines.append(
            "    <lastBuildDate>"
            f"{datetime.now(timezone.utc).strftime('%a, %d %b %Y %H:%M:%S +0000')}"
            "</lastBuildDate>"
        )

    for post in all_posts:
        rel_link_clean = str(post["link"]).lstrip("./")
        post_url = f"{base_url}/{rel_link_clean}"
        item_description = str(post["description"])
        rss_lines.extend(
            [
                "    <item>",
                f"      <title>{html.escape(str(post['name']), quote=False)}</title>",
                f"      <link>{html.escape(post_url, quote=False)}</link>",
                f'      <guid isPermaLink="true">{html.escape(post_url, quote=False)}</guid>',
                f"      <pubDate>{_format_rss_pub_date(str(post['date']))}</pubDate>",
                "      <description>"
                f"{html.escape(item_description, quote=False)}</description>",
                "    </item>",
            ]
        )

    rss_lines.extend(["  </channel>", "</rss>"])
    return rss_lines


def _is_remote_avatar(avatar: str) -> bool:
    return avatar.startswith("http://") or avatar.startswith("https://")


def _build_friends_hidden_text(
    title: str, subtitle: Optional[str], friends: List[PostEntry]
) -> str:
    parts: List[str] = [f"<h1>{html.escape(title)}</h1>"]
    if subtitle:
        parts.append(f"<p>{html.escape(subtitle)}</p>")
    parts.append("<h1>Friends</h1>")
    for friend in friends:
        name = html.escape(str(friend["name"]))
        url = html.escape(str(friend["url"]))
        description = html.escape(str(friend.get("description", "")))
        parts.append(
            f'<p class="friend"><a href="{url}" tabindex="-1">{name}</a>'
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
    _update_hash_with_typ_files(hasher, [template_typ_path])
    for path in [friends_data_path, *avatar_paths]:
        hasher.update(path.encode("utf-8"))
        with open(path, "rb") as f:
            hasher.update(f.read())
    return hasher.hexdigest()[:_ASSET_HASH_LENGTH]


def build_friends_page(
    base_dir: str,
    root_dir: str,
    build_base: str,
    asset_context: Any,
    base_url: str,
) -> None:
    template_path = os.path.join(base_dir, "friends.template.typ")
    with open(template_path, "r", encoding="utf-8") as f:
        friends_template = f.read()

    # The authored source lives in workspace/ (gitignored, like drafts). A
    # tracked snapshot under root/ is what the build reads, so a fresh checkout
    # has the data; `update` refreshes the snapshot from workspace when present.
    repo_root = os.path.dirname(base_dir)
    workspace_data = os.path.join(repo_root, "workspace", "friends.typ")
    workspace_avatars = os.path.join(repo_root, "workspace", "friends-assets")
    snapshot_dir = os.path.join(root_dir, "friends-source")
    snapshot_data = os.path.join(snapshot_dir, "friends.typ")
    snapshot_avatars = os.path.join(snapshot_dir, "friends-assets")
    if os.path.isfile(workspace_data):
        os.makedirs(snapshot_dir, exist_ok=True)
        shutil.copy2(workspace_data, snapshot_data)
        if os.path.isdir(snapshot_avatars):
            shutil.rmtree(snapshot_avatars)
        if os.path.isdir(workspace_avatars):
            shutil.copytree(workspace_avatars, snapshot_avatars)

    rel_data = os.path.relpath(snapshot_data, repo_root).replace("\\", "/")
    query_src = (
        f'#import "{rel_data}": friends\n'
      f"[#metadata(friends) <friends-data>]"
    )
    nodes = _query_nodes_from_source(
        query_src, query_root=repo_root, selector="<friends-data>"
    )
    friends = nodes[0]["value"] if nodes else []

    title, subtitle = query_title_subtitle_from_source(friends_template, repo_root)
    hidden_text = _build_friends_hidden_text(title, subtitle, friends)

    output_dir = os.path.join(build_base, "friends")
    reset_dir(output_dir)

    template_typ_path = os.path.join(base_dir, "template.typ")
    avatar_paths = [
        os.path.join(snapshot_avatars, friend["avatar"])
        for friend in friends
        if friend.get("avatar") and not _is_remote_avatar(friend["avatar"])
    ]
    asset_hash = _friends_asset_hash(
        friends_template,
        template_typ_path,
        snapshot_data,
        avatar_paths,
    )

    og_url = page_url(
        base_url=base_url,
        root_dir=root_dir,
        dest_dir=root_dir,
        html_filename="friends.html",
    )

    compile_html(
        CompileBuildRequest(
            source_bytes=friends_template.encode(),
            output_dir=output_dir,
            asset_hash=asset_hash,
            file_prefix="friends",
            typst_inputs=make_inputs(shared_inputs={"back_href": "./index.html"}),
            mobile=MobileCompileConfig(svg_name_prefix="m-friends"),
            html=HtmlBuildConfig(
                template_path=os.path.join(base_dir, "index.template.html"),
                dest_dir=root_dir,
                html_filename="friends.html",
                title_format="Friends Page {i}",
                svg_name_prefix="friends",
                default_title=title,
                asset_context=asset_context,
                description=subtitle or title,
                hidden_text_override=hidden_text,
                asset_dest_dir=os.path.join(root_dir, "assets"),
                image_source_dir=os.path.dirname(base_dir),
                og_type="website",
                og_url=og_url,
                site_base_url=base_url,
            ),
        )
    )


def update_content(args: Namespace) -> None:
    base_dir = driver_dir()
    root_dir: str = args.root_dir
    os.makedirs(root_dir, exist_ok=True)

    copied_root_files: Dict[str, str] = {}
    for filename in _ROOT_SHARED_FILENAMES:
        dest_path = os.path.join(root_dir, filename)
        shutil.copy2(os.path.join(base_dir, filename), dest_path)
        copied_root_files[filename] = dest_path

    dest_base_dir = os.path.join(args.root_dir, "posts")
    if not os.path.exists(dest_base_dir):
        return

    posts_by_date = _collect_posts_by_date(dest_base_dir)
    template_path = os.path.join(base_dir, "content.template.typ")
    with open(template_path, "r", encoding="utf-8") as f:
        content_template = f.read()

    parsed_title, parsed_subtitle = query_title_subtitle_from_source(
        content_template.replace("{{POSTS}}", ""), os.path.dirname(base_dir)
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
    page_title = parsed_title
    page_description = first_desc(hidden_text, parsed_subtitle)

    output_dir = os.path.join(args.build_base, "content")
    reset_dir(output_dir)

    template_typ_path = os.path.join(base_dir, "template.typ")
    asset_hash = hash_text_with_paths(
        content_source,
        [template_typ_path],
    )

    source_bytes = content_source.encode()
    html_template = os.path.join(base_dir, "index.template.html")
    typst_inputs = make_inputs(
        shared_inputs={"back_href": "./index.html"},
    )

    content_asset_dir = os.path.join(root_dir, "assets")
    asset_context = build_asset_ctx(base_dir, root_dir)
    base_url = resolve_base_url(args)
    og_url = page_url(
        base_url=base_url,
        root_dir=root_dir,
        dest_dir=root_dir,
        html_filename="index.html",
    )

    compile_html(
        CompileBuildRequest(
            source_bytes=source_bytes,
            output_dir=output_dir,
            asset_hash=asset_hash,
            file_prefix="content",
            typst_inputs=typst_inputs,
            mobile=MobileCompileConfig(),
            html=HtmlBuildConfig(
                template_path=html_template,
                dest_dir=root_dir,
                title_format="Content Page {i}",
                default_title=page_title,
                asset_context=asset_context,
                description=page_description,
                hidden_text_override=hidden_text,
                asset_dest_dir=content_asset_dir,
                rss_feed_path=os.path.join(root_dir, "rss.xml"),
                og_type="website",
                og_url=og_url,
                site_base_url=base_url,
            ),
        )
    )

    build_friends_page(
        base_dir=base_dir,
        root_dir=root_dir,
        build_base=args.build_base,
        asset_context=asset_context,
        base_url=base_url,
    )

    glyph_target_dirs: List[str] = [root_dir, content_asset_dir]
    for date_str in sorted(os.listdir(dest_base_dir), reverse=True):
        date_dir = os.path.join(dest_base_dir, date_str)
        if not os.path.isdir(date_dir):
            continue
        for post_dir_name in os.listdir(date_dir):
            post_path = os.path.join(date_dir, post_dir_name)
            if not os.path.isdir(post_path):
                continue
            glyph_target_dirs.append(post_path)
            post_assets_dir = os.path.join(post_path, "assets")
            if os.path.isdir(post_assets_dir):
                glyph_target_dirs.append(post_assets_dir)
            source_dir = os.path.join(post_path, "source")
            if os.path.isdir(source_dir):
                glyph_target_dirs.append(source_dir)

    refresh_glyphs(
        root_dir=root_dir,
        target_dirs=glyph_target_dirs
    )
    # Lift friend cards into <symbol>s after glyph hoisting, so the hidden card
    # <use>s (revealed by friends.js) are never run through svgo's
    # removeHiddenElems during refresh.
    for pattern in ("friends[0-9]*.svg", "m-friends[0-9]*.svg"):
        for svg_path in glob.glob(os.path.join(root_dir, "assets", pattern)):
            extract_friend_card_symbols(svg_path)
    minify_html(os.path.join(root_dir, "index.html"))

    sitemap_lines = _build_sitemap_lines(base_url, posts_by_date)
    rss_lines = _build_rss_lines(
        base_url=base_url,
        site_title=parsed_title,
        site_description=parsed_subtitle,
        posts_by_date=posts_by_date,
    )

    sitemap_path = os.path.join(root_dir, "sitemap.xml")
    with open(sitemap_path, "w", encoding="utf-8") as f:
        f.write("\n".join(sitemap_lines))
    rss_path = os.path.join(root_dir, "rss.xml")
    with open(rss_path, "w", encoding="utf-8") as f:
        f.write("\n".join(rss_lines))

    print(f"Content page updated in {root_dir}.")
    for filename in _ROOT_SHARED_FILENAMES:
        print(f"{filename} generated at {copied_root_files[filename]}.")
    print(f"Sitemap generated at {sitemap_path}.")
    print(f"RSS feed generated at {rss_path}.")
