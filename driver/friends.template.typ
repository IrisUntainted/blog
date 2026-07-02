#import "driver/template.typ": action, driver_page, nav_link, requires_asset, right_top_bar
#import "root/friends-source/friends.typ": friends
#let title = "Friends"
#let subtitle = "Iris finds them interesting."

#let top_bar = right_top_bar([
  #nav_link("./index.html", [Back]) | #action("theme", [Theme], label: "Theme")
])

#let _avatar_block(avatar, name) = {
  let alt_part = if name != none { "?alt=" + name } else { "" }
  if avatar.starts-with("http://") or avatar.starts-with("https://") {
    link(
      "driver-remote-image://" + avatar + alt_part,
      rect(width: 1.3cm, height: 1.3cm, fill: luma(220), stroke: 0.5pt + luma(200)),
    )
  } else {
    image(
      "root/friends-source/friends-assets/" + avatar,
      width: 1.3cm,
      height: 1.3cm,
      alt: name,
    )
  }
}

#let friend_card(f) = {
  let avatar = f.at("avatar", default: none)
  let avatar_block = if avatar == none {
    rect(width: 1.3cm, height: 1.3cm, fill: luma(170))
  } else {
    _avatar_block(avatar, f.name)
  }
  block(width: 100%, height: 1.6cm, clip: true)[
    #grid(
      columns: (1.3cm, 1fr),
      column-gutter: 0.7em,
      align: (center + horizon, left + horizon),
      [#avatar_block],
      [
        #text(weight: "bold")[#link(f.url)[#f.name]] \
        #text(size: 0.85em, fill: luma(100))[#f.description]
      ],
    )
  ]
}

#show: driver_page.with(title: title, subtitle: subtitle, top_bar: top_bar)

#requires_asset("friends")

#for f in friends [
  #friend_card(f)
]
