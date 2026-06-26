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

  const readHref = (el) =>
    el.getAttribute("href") || el.getAttribute("xlink:href") || "";
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
    // Reveal the (now shuffled) cards — they start hidden to avoid a flash.
    svgDoc.documentElement.style.setProperty("--friend-cards", "visible");
  };

  const bound = new WeakSet();
  const init = (pageObject) => {
    const svgDoc = pageObject.contentDocument;
    if (!svgDoc || bound.has(svgDoc)) {
      return;
    }
    if (!svgDoc.querySelector("use.friend-card")) {
      return;
    }
    bound.add(svgDoc);
    applyToSvg(svgDoc);
  };

  document
    .querySelectorAll('object.page[type="image/svg+xml"]')
    .forEach((pageObject) => {
      pageObject.addEventListener("load", () => init(pageObject));
      init(pageObject);
    });
})();
