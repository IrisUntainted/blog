#import "@preview/cetz:0.3.4": canvas, draw
#import "../../driver/template.typ": theme_color

#let ink = rgb("#222222")
#let _ring-names = ( 
  "dls-01", "ams-01", "par-01", "tyo-01", "hkg-01", "lax-02", "lax-01",
)
#let _ring-radius = 3.6
#let nodes = {
  let n = _ring-names.len()
  let out = ()
  for i in range(n) {
    let theta = 90deg - i * (360deg / n)
    let x = _ring-radius * calc.cos(theta)
    let y = _ring-radius * calc.sin(theta)
    out.push((_ring-names.at(i), x, y))
  }
  out
}

#let node-pos(name) = {
  for n in nodes {
    if n.at(0) == name { return (n.at(1), n.at(2)) }
  }
  panic("unknown node: " + name)
}

#let diagram = block(width: 100%, breakable: false)[
  #theme_color(ink, rgb("#e8ecf2"))
  #set text(
    font: ("Helvetica Neue", "Helvetica", "Arial"),
    size: 10pt,
    fill: ink,
  )

  #align(center, canvas(length: 1cm, {
    import draw: *

    for i in range(nodes.len()) {
      for j in range(i + 1, nodes.len()) {
        let a = (nodes.at(i).at(1), nodes.at(i).at(2))
        let b = (nodes.at(j).at(1), nodes.at(j).at(2))
        line(a, b, stroke: (paint: ink, thickness: 0.5pt))
      }
    }
    for n in nodes {
      let (name, x, y) = n
      circle((x, y), radius: 0.45,
             fill: white,
             stroke: (paint: ink, thickness: 0.8pt))
      content((x, y), text(size: 7pt, weight: "bold", name))
    }
  }))
]

#diagram
