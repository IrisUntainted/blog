#import "template.typ": action, overlap, requires_asset, _driver_has_driver, _driver_is_svg_export

#let _plain_text(it) = {
  if type(it) == str {
    it
  } else if it.has("text") {
    it.text
  } else if it.has("children") {
    it.children.map(_plain_text).join()
  } else if it.has("body") {
    _plain_text(it.body)
  } else if it.has("child") {
    _plain_text(it.child)
  } else if it == [ ] {
    " "
  } else {
    ""
  }
}

#let input_field(id, placeholder: "Type here", width: auto) = {
  requires_asset("form")
  show underline: it => it.body
  let resolved_label = if placeholder != "" { placeholder } else { "Input: " + id }
  action(
    "input:" + id,
    box(
      width: width,
      baseline: 0.1em,
      stroke: 0.5pt + luma(120),
      radius: 0.2em,
      inset: (x: 0.5em, y: 0.4em),
    )[
      #set text(fill: luma(160))
      #placeholder
    ],
    label: resolved_label,
    role: "textbox",
  )
}

#let form_button(id, body) = {
  requires_asset("form")
  show underline: it => it.body
  let text_label = _plain_text(body).trim()
  action(
    "form-action:" + id,
    box(
      baseline: 0.3em,
      stroke: 0.5pt + luma(120),
      radius: 0.2em,
      inset: (x: 0.5em, y: 0.3em),
    )[#body],
    label: if text_label != "" { text_label } else { id },
    role: "button",
  )
}

#let cond(id, if_false, if_true) = {
  if _driver_has_driver() and _driver_is_svg_export() {
    show underline: it => it.body
    overlap(
      link("#cond=" + id + ":0", if_false),
      link("#cond=" + id + ":1", if_true),
    )
  } else {
    if_false
  }
}

#let _check_indicator(checked) = {
  let mark = if checked {
    align(center + horizon, text(size: 0.65em)[✓])
  }
  box(
    width: 0.85em, height: 0.85em,
    stroke: 0.5pt + luma(120),
    radius: 0.1em,
    inset: 0pt,
  )[#mark]
}

#let checkbox(id, body) = {
  requires_asset("form")
  show underline: it => it.body
  let text_label = _plain_text(body).trim()
  action(
    "checkbox:" + id,
    [#box(baseline: 0.1em)[#cond(
      "ui:checkbox:" + id,
      _check_indicator(false),
      _check_indicator(true),
    )] #body],
    label: if text_label != "" { text_label } else { id },
    role: "checkbox",
  )
}

#let _radio_indicator(selected) = {
  let dot = if selected {
    align(center + horizon, circle(radius: 0.18em, fill: luma(20)))
  }
  box(
    width: 0.85em, height: 0.85em,
    stroke: 0.5pt + luma(120),
    radius: 50%,
    inset: 0pt,
  )[#dot]
}

#let radio(group, value, body) = {
  requires_asset("form")
  show underline: it => it.body
  let text_label = _plain_text(body).trim()
  action(
    "radio:" + group + ":" + value,
    [#box(baseline: 0.1em)[#cond(
      "ui:radio:" + group + ":" + value,
      _radio_indicator(false),
      _radio_indicator(true),
    )] #body],
    label: if text_label != "" { text_label } else { group + ": " + value },
    role: "radio",
  )
}

#let radio_switch(group, cases, default: none) = {
  if _driver_has_driver() and _driver_is_svg_export() {
    show underline: it => it.body
    let branches = ()
    for (value, body) in cases {
      branches.push(link("#cond=radio:" + group + ":" + value + ":1", body))
    }
    if default != none {
      branches.push(link("#cond=radio:" + group + ":0", default))
    }
    overlap(..branches)
  } else {
    default
  }
}
