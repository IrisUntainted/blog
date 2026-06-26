#import "../../driver/template.typ": *

#let title = "Inline Link Test"

#show: article.with(title: title)

This is a test post to ensure links are correctly parsed and rendered in screen readers.
You may ignore this post if you are visually inspecting the rendered SVG image.

Here is a link to #link("https://blog.owo.li", "Homepage").

Here is another link to #link("../../../index.html", "Homepage"), but this one is relative.
