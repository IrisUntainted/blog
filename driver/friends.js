(() => {
  const srOnly = document.querySelector(".sr-only");
  const entries = srOnly ? Array.from(srOnly.querySelectorAll("p.friend")) : [];
  const n = entries.length;

  const perm = Array.from({ length: n }, (_, i) => i);
  for (let i = n - 1; i > 0; i--) {
    const j = Math.floor(Math.random() * (i + 1));
    const tmp = perm[i];
    perm[i] = perm[j];
    perm[j] = tmp;
  }

  if (srOnly) {
    perm
      .map((cardIdx) => entries[cardIdx])
      .forEach((node) => srOnly.appendChild(node));
  }

  const writeHref = (el, val) => {
    if (el.hasAttribute("xlink:href")) {
      el.setAttribute("xlink:href", val);
    } else {
      el.setAttribute("href", val);
    }
  };

  const applyToSvg = (svgDoc) => {
    const uses = Array.from(svgDoc.querySelectorAll("use.friend-card"));
    if (uses.length === n) {
      uses.forEach((use, slotIdx) => writeHref(use, "#card-" + perm[slotIdx]));
    }
    svgDoc.documentElement.style.setProperty("--friend-cards", "visible");
  };

  const bound = new WeakSet();
  const init = (pageObject, force = false) => {
    const svgDoc = pageObject.contentDocument;
    if (
      !svgDoc ||
      svgDoc.documentElement?.tagName.toLowerCase() !== "svg" ||
      bound.has(svgDoc)
    ) {
      return false;
    }
    const count = svgDoc.querySelectorAll("use.friend-card").length;
    if (count === 0 || (!force && n > 0 && count < n)) {
      return false;
    }
    bound.add(svgDoc);
    applyToSvg(svgDoc);
    return true;
  };

  // Reveal cards as soon as the SVG DOM is parsed rather than blocking on
  // slow or unreachable remote avatar <image> subresources before `load`.
  const watch = (pageObject) => {
    if (!pageObject.getAttribute("data") || init(pageObject, false)) {
      return;
    }
    let frames = 0;
    const poll = () => {
      if (init(pageObject, false) || ++frames > 180) return;
      requestAnimationFrame(poll);
    };
    requestAnimationFrame(poll);
  };

  document
    .querySelectorAll('object.page[type="image/svg+xml"]')
    .forEach((pageObject) => {
      pageObject.addEventListener("load", () => init(pageObject, true));
      new MutationObserver(() => watch(pageObject)).observe(pageObject, {
        attributes: true,
        attributeFilter: ["data"],
      });
      watch(pageObject);
    });
})();
