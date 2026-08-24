from typing import Iterator, Optional

from lxml import etree
from lxml import html as _html

XLINK_NS = "http://www.w3.org/1999/xlink"
_NAMESPACED = (XLINK_NS,)

_SVG_PARSER = etree.XMLParser(
    remove_blank_text=False, remove_comments=False, strip_cdata=False
)


def parse_svg(text: str):
    return etree.fromstring(text.encode("utf-8"), _SVG_PARSER)


def serialize_svg(root) -> str:
    return etree.tostring(root, encoding="unicode")


def inner_xml(el) -> str:
    serialized = etree.tostring(el, encoding="unicode", with_tail=False)
    close = f"</{local(el.tag)}>"
    if not serialized.endswith(close):
        return el.text or ""
    return serialized[serialized.index(">") + 1 : -len(close)]


def local(tag) -> str:
    if isinstance(tag, str) and tag.startswith("{"):
        return tag.split("}", 1)[1]
    return tag


def iter_local(root, name: str):
    for el in root.iter():
        if isinstance(el.tag, str) and local(el.tag) == name:
            yield el


def _attr_keys(name: str) -> Iterator[str]:
    yield name
    for ns in _NAMESPACED:
        yield f"{{{ns}}}{name}"


def attr_local(el, name: str) -> Optional[str]:
    for key in _attr_keys(name):
        if key in el.attrib:
            return el.attrib[key]
    return None


def del_attr_local(el, name: str) -> None:
    for key in list(_attr_keys(name)):
        el.attrib.pop(key, None)


def namespaced(parent, name: str) -> str:
    ns = etree.QName(parent.tag).namespace
    return f"{{{ns}}}{name}" if ns else name


def declare_namespace(root, prefix: str, uri: str):
    if prefix in root.nsmap:
        return root
    nsmap = dict(root.nsmap)
    nsmap[prefix] = uri
    new_root = etree.Element(root.tag, nsmap=nsmap)
    new_root.attrib.update(root.attrib)
    new_root.text = root.text
    new_root.tail = root.tail
    new_root.extend(list(root))
    return new_root


def parse_html(text: str):
    return _html.fromstring(text)


def serialize_html(el) -> str:
    body = _html.tostring(el, encoding="unicode", include_meta_content_type=False)
    return "<!DOCTYPE html>" + body + "\n"
