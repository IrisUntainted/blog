#import "driver/template.typ": action, driver_image_show, driver_page, nav_link, right_top_bar
#let title = "Friends"
#let subtitle = "Iris finds them interesting."

#let friends = json("driver/friends.json")

#let top_bar = right_top_bar([
  #nav_link("./index.html", [Back]) | #action("theme", [Theme], label: "Theme")
])

#let friend_card(f) = {
  let avatar = f.at("avatar", default: none)
  let avatar_block = if avatar == none {
    rect(width: 1.3cm, height: 1.3cm, fill: luma(170))
  } else {
    image("driver/friends-assets/" + avatar, width: 1.3cm, height: 1.3cm, alt: f.name)
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

#show image: driver_image_show
#show: driver_page.with(title: title, subtitle: subtitle, top_bar: top_bar)

#for f in friends [
  #friend_card(f)
]
