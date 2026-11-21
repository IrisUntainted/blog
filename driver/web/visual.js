(() => {
  const STORAGE_KEY = 'blog-color-theme';
  const LAYOUT_STORAGE_KEY = 'blog-layout';
  const LIGHT_THEME = 'light';
  const DARK_THEME = 'dark';
  const DESKTOP_LAYOUT = 'desktop';
  const MOBILE_LAYOUT = 'mobile';
  const ACTION_PREFIX = '#action=';
  const BOUND_SVG_DOCUMENTS = new WeakSet();
  const SVG_COLOR_TOKENS = [
    '--svg-paper-bg',
    '--svg-text',
    '--svg-quote-text',
    '--svg-caption-text',
    '--svg-muted-text',
    '--svg-footer-text',
    '--svg-code-bg',
    '--svg-surface-bg',
    '--svg-code-comment',
    '--svg-code-green',
    '--svg-code-dark-green',
    '--svg-code-teal',
    '--svg-code-dark-red',
    '--svg-code-red',
    '--svg-code-blue',
    '--svg-code-violet',
    '--svg-code-magenta',
    '--svg-muted-stroke',
    '--svg-code-gutter',
    '--svg-focus-ring',
  ];

  const readStorage = (key, validValues) => {
    try {
      const value = window.localStorage?.getItem(key);
      return validValues.includes(value) ? value : null;
    } catch {
      return null;
    }
  };

  const writeStorage = (key, value) => {
    try {
      window.localStorage?.setItem(key, value);
    } catch {
      // noop
    }
  };

  const resolveTheme = (theme) =>
      (theme === DARK_THEME ? DARK_THEME : LIGHT_THEME);

  const getSavedTheme = () => readStorage(STORAGE_KEY, [LIGHT_THEME, DARK_THEME]);

  const getPreferredTheme = () => {
    const media = window.matchMedia?.('(prefers-color-scheme: dark)');
    return media?.matches ? DARK_THEME : LIGHT_THEME;
  };

  const parseSvgCustomThemeVars = (svgRoot) => {
    const styles = svgRoot.querySelectorAll('style');
    if (!styles.length) return null;
    let text = '';
    for (const style of styles) {
      text += style.textContent + '\n';
    }
    const darkMatch = text.match(/@media\s*\(prefers-color-scheme:\s*dark\)\s*\{[^{}]*\{([^}]*)\}/);
    if (!darkMatch) return null;
    const lightDefaults = {};
    for (const m of text.matchAll(/var\(\s*(--svg-[\w-]+)\s*,\s*([^)]+)\)/g)) {
      lightDefaults[m[1]] = m[2].trim();
    }
    const vars = {};
    for (const m of darkMatch[1].matchAll(/(--svg-[\w-]+)\s*:\s*([^;]+)/g)) {
      const token = m[1];
      vars[token] = {dark: m[2].trim(), light: lightDefaults[token] || ''};
    }
    return vars;
  };

  const applyThemeToSvgRoot = (svgRoot, computedStyles) => {
    if (!svgRoot || svgRoot.tagName.toLowerCase() !== 'svg') {
      return;
    }
    for (const token of SVG_COLOR_TOKENS) {
      const value = computedStyles.getPropertyValue(token).trim();
      if (value) {
        svgRoot.style.setProperty(token, value);
      }
    }
    const customVars = parseSvgCustomThemeVars(svgRoot);
    if (!customVars) return;
    const isDark = document.documentElement.getAttribute('data-theme') === 'dark';
    for (const [token, pair] of Object.entries(customVars)) {
      if (SVG_COLOR_TOKENS.includes(token)) continue;
      if (isDark) {
        svgRoot.style.setProperty(token, pair.dark);
      } else if (pair.light) {
        svgRoot.style.setProperty(token, pair.light);
      } else {
        svgRoot.style.removeProperty(token);
      }
    }
  };

  const applyThemeToObject = (pageObject, computedStyles) => {
    const svgRoot = pageObject.contentDocument?.documentElement;
    if (!svgRoot) {
      return;
    }
    applyThemeToSvgRoot(svgRoot, computedStyles);
  };

  const readAnchorHref = (anchorNode) => anchorNode.getAttribute('href') || '';

  const actionHandlers = new Map();
  const registerSvgAction = (key, spec) => {
    actionHandlers.set(key, spec);
  };
  window.registerSvgAction = registerSvgAction;

  const parseActionToken = (href) => {
    if (!href.startsWith(ACTION_PREFIX)) {
      return null;
    }
    let payload = href.slice(ACTION_PREFIX.length);
    try {
      payload = decodeURIComponent(payload);
    } catch {
      // Keep the raw payload when decoding fails.
    }
    const token = payload.trim();
    if (!token) {
      return null;
    }
    const colonIdx = token.indexOf(':');
    return colonIdx < 0
        ? {key: token, arg: ''}
        : {key: token.slice(0, colonIdx).trim(), arg: token.slice(colonIdx + 1).trim()};
  };
  window.parseActionHref = parseActionToken;

  const parseSvgAction = (href) => {
    const token = parseActionToken(href);
    if (!token) {
      return null;
    }
    const spec = actionHandlers.get(token.key);
    if (!spec || (spec.validate && !spec.validate(token.arg))) {
      return null;
    }
    return {key: token.key, spec, arg: token.arg};
  };

  const bindSvgActionsToObject = (pageObject) => {
    const svgDocument = pageObject.contentDocument;
    if (!svgDocument ||
        svgDocument.documentElement?.tagName.toLowerCase() !== 'svg' ||
        BOUND_SVG_DOCUMENTS.has(svgDocument)) {
      return;
    }
    BOUND_SVG_DOCUMENTS.add(svgDocument);

    svgDocument.addEventListener('click', (event) => {
      const eventTarget = event.target;
      if (!eventTarget || typeof eventTarget.closest !== 'function') {
        return;
      }
      const anchorNode = eventTarget.closest('a');
      if (!anchorNode) {
        return;
      }
      const action = parseSvgAction(readAnchorHref(anchorNode));
      if (!action) {
        return;
      }
      event.preventDefault();
      action.spec.press(action.arg, {anchorNode, pageObject});
    });

    svgDocument.addEventListener('keydown', (event) => {
      const isActivation = event.key === ' ' || event.key === 'Spacebar' || event.key === 'Enter';
      const isPrintable = event.key.length === 1 && !event.ctrlKey && !event.altKey && !event.metaKey;
      const isArrowNav = event.key.startsWith('Arrow');
      if (!isActivation && !isPrintable && !isArrowNav) {
        return;
      }
      const eventTarget = event.target;
      if (!eventTarget || typeof eventTarget.closest !== 'function') {
        return;
      }
      const anchorNode = eventTarget.closest('a');
      if (!anchorNode) {
        return;
      }
      const action = parseSvgAction(readAnchorHref(anchorNode));
      if (!action) {
        return;
      }
      const {spec, arg} = action;
      const context = {
        anchorNode,
        pageObject,
        initialKey: isPrintable && !isActivation ? event.key : undefined,
      };
      if (isActivation) {
        event.preventDefault();
        spec.press(arg, context);
        return;
      }
      if (isPrintable && spec.keyboard === 'text') {
        event.preventDefault();
        spec.press(arg, context);
        return;
      }
      if (isArrowNav && spec.arrows) {
        event.preventDefault();
        spec.arrows(arg, context, event);
      }
    });
  };

  const applyTheme = (theme, persist = false) => {
    const resolvedTheme = resolveTheme(theme);
    const root = document.documentElement;
    root.setAttribute('data-theme', resolvedTheme);
    root.classList.toggle('theme-light', resolvedTheme === LIGHT_THEME);
    root.classList.toggle('theme-dark', resolvedTheme === DARK_THEME);
    root.style.colorScheme = resolvedTheme;

    const computedStyles = window.getComputedStyle(root);
    document.querySelectorAll('object.page[type="image/svg+xml"]')
        .forEach(
            (pageObject) => applyThemeToObject(pageObject, computedStyles));

    if (persist) {
      writeStorage(STORAGE_KEY, resolvedTheme);
    }
  };

  const getCurrentTheme = () =>
      resolveTheme(document.documentElement.getAttribute('data-theme'));

  const initializePageObject = (pageObject) => {
    const computedStyles = window.getComputedStyle(document.documentElement);
    applyThemeToObject(pageObject, computedStyles);
    bindSvgActionsToObject(pageObject);
  };

  document.querySelectorAll('object.page[type="image/svg+xml"]')
      .forEach((pageObject) => {
        pageObject.addEventListener('load', () => {
          initializePageObject(pageObject);
        });
        initializePageObject(pageObject);
      });

  const toggleColorTheme = () => {
    const nextTheme =
        getCurrentTheme() === DARK_THEME ? LIGHT_THEME : DARK_THEME;
    applyTheme(nextTheme, true);
  };
  window.toggleColorTheme = toggleColorTheme;

  const setColorTheme = (theme) => {
    if (theme !== LIGHT_THEME && theme !== DARK_THEME) {
      return;
    }
    applyTheme(theme, true);
  };
  window.setColorTheme = setColorTheme;

  const resolveLayout = (layout) =>
      (layout === MOBILE_LAYOUT ? MOBILE_LAYOUT : DESKTOP_LAYOUT);

  const activateLayoutPages = (layout) => {
    const wrapper = document.querySelector(`.pages-${layout}`);
    if (!wrapper) {
      return;
    }
    wrapper.querySelectorAll('object.page').forEach((pageObject) => {
      if (pageObject.getAttribute('data')) {
        return;
      }
      const src = pageObject.getAttribute('data-src');
      if (src) {
        pageObject.setAttribute('data', src);
      }
    });
  };

  const applyLayout = (layout, persist = false) => {
    const resolvedLayout = resolveLayout(layout);
    document.documentElement.setAttribute('data-layout', resolvedLayout);
    activateLayoutPages(resolvedLayout);
    if (persist) {
      writeStorage(LAYOUT_STORAGE_KEY, resolvedLayout);
    }
  };

  const getCurrentLayout = () =>
      resolveLayout(document.documentElement.getAttribute('data-layout'));

  const toggleLayout = () => {
    const nextLayout =
        getCurrentLayout() === MOBILE_LAYOUT ? DESKTOP_LAYOUT : MOBILE_LAYOUT;
    applyLayout(nextLayout, true);
  };
  window.toggleLayout = toggleLayout;

  const setLayout = (layout) => {
    if (layout !== DESKTOP_LAYOUT && layout !== MOBILE_LAYOUT) {
      return;
    }
    applyLayout(layout, true);
  };
  window.setLayout = setLayout;

  registerSvgAction('theme', {press: () => toggleColorTheme()});
  registerSvgAction('layout', {press: () => toggleLayout()});

  const initialTheme = getSavedTheme() || getPreferredTheme();
  applyTheme(initialTheme);

  const colorSchemeMedia = window.matchMedia?.('(prefers-color-scheme: dark)');
  colorSchemeMedia?.addEventListener?.('change', (event) => {
    if (!getSavedTheme()) {
      applyTheme(event.matches ? DARK_THEME : LIGHT_THEME, false);
    }
  });

  const SERIF_FONT = 'Georgia, "Times New Roman", "Songti SC", serif';
  const MONO_FONT = '"DejaVu Sans Mono", Menlo, Consolas, monospace';
  const COPY_SEPS = {s: ' ', t: '\t', n: '\n', nn: '\n\n'};

  const calibrateOverlays = () => {
    const spans = document.querySelectorAll('.page-overlay .ov-s');
    if (!spans.length) return;
    const measureCtx = document.createElement('canvas').getContext('2d');
    if (!measureCtx) return;

    let lastFont = '';
    for (const el of spans) {
      const text = el.textContent || '';
      const w = parseFloat(el.style.getPropertyValue('--w'));
      const fs = parseFloat(el.style.getPropertyValue('--fs'));
      if (!text || !(w > 0) || !(fs > 0)) continue;

      const style = el.classList.contains('ov-i') ? 'italic' : 'normal';
      const weight = el.classList.contains('ov-b') ? '700' : '400';
      const family = el.classList.contains('ov-m') ? MONO_FONT : SERIF_FONT;
      const fontSpec = `${style} ${weight} 100px ${family}`;
      if (fontSpec !== lastFont) {
        measureCtx.font = fontSpec;
        lastFont = fontSpec;
      }
      const naturalPt = (measureCtx.measureText(text).width / 100) * fs;
      if (naturalPt > 0) {
        const scale = Math.min(2.5, Math.max(0.5, w / naturalPt));
        el.style.transform = `scaleX(${scale.toFixed(4)})`;
      }
    }
  };

  calibrateOverlays();
  if (document.fonts?.size) {
    document.fonts.ready.then(calibrateOverlays);
  }

  const findSvgActionAnchor = (pageObject, key, arg, x, y) => {
    const svgDoc = pageObject?.contentDocument;
    if (!svgDoc) return null;
    const objRect = pageObject.getBoundingClientRect();
    let bestAnchor = null;
    let bestDist = Infinity;
    for (const a of svgDoc.querySelectorAll('a[href^="#action="]')) {
      const tok = parseActionToken(readAnchorHref(a));
      if (!tok || tok.key !== key || tok.arg !== arg) continue;
      const r = a.getBoundingClientRect();
      if (r.width <= 0 || r.height <= 0) continue;
      const dist = Math.hypot(
          objRect.left + r.left + r.width * 0.5 - x,
          objRect.top + r.top + r.height * 0.5 - y,
      );
      if (dist < bestDist) {
        bestDist = dist;
        bestAnchor = a;
      }
    }
    return bestAnchor;
  };

  document.addEventListener('click', (event) => {
    const target = event.target;
    if (!target || typeof target.closest !== 'function') return;
    const overlay = target.closest('.page-overlay');
    if (!overlay) return;

    const sel = window.getSelection();
    if (sel && !sel.isCollapsed && sel.toString().length > 0) {
      event.preventDefault();
      event.stopPropagation();
      return;
    }

    const actionEl = target.closest('[data-action]');
    if (!actionEl) return;
    const action = parseSvgAction(
        ACTION_PREFIX + (actionEl.getAttribute('data-action') || ''));
    if (!action) return;

    event.preventDefault();
    const pageObject = overlay.closest('.page-wrap')?.querySelector('object.page');
    const anchorNode = findSvgActionAnchor(
        pageObject, action.key, action.arg, event.clientX, event.clientY);
    action.spec.press(action.arg, {anchorNode, pageObject});
  });

  document.addEventListener('copy', (event) => {
    if (document.activeElement?.matches?.('input, textarea')) return;
    const sel = window.getSelection();
    if (!sel || sel.rangeCount === 0 || sel.isCollapsed) return;

    const visibleSpans = [...document.querySelectorAll('.page-overlay .ov-s')]
        .filter((el) => el.getClientRects().length > 0);
    if (!visibleSpans.length) return;

    const rangeTexts = [];
    for (let ri = 0; ri < sel.rangeCount; ri++) {
      const range = sel.getRangeAt(ri);
      if (range.collapsed) continue;

      const selected = visibleSpans.filter((el) => range.intersectsNode(el));
      if (!selected.length) continue;

      const pieces = [];
      for (const el of selected) {
        const full = el.textContent || '';
        const textNode =
            el.firstChild?.nodeType === Node.TEXT_NODE ? el.firstChild : null;
        let start = 0;
        let end = full.length;
        if (textNode && range.startContainer === textNode) {
          start = range.startOffset;
        } else if (range.startContainer === el && range.startOffset > 0) {
          start = full.length;
        }
        if (textNode && range.endContainer === textNode) {
          end = range.endOffset;
        } else if (range.endContainer === el && range.endOffset === 0) {
          end = 0;
        }
        const slice = full.slice(start, end);
        if (slice.length > 0) {
          pieces.push({el, slice, atEnd: end === full.length});
        }
      }

      let text = '';
      for (let i = 0; i < pieces.length; i++) {
        text += pieces[i].slice;
        if (i < pieces.length - 1 && pieces[i].atEnd) {
          text += COPY_SEPS[pieces[i].el.dataset.sep] || '';
        }
      }
      if (text) rangeTexts.push(text);
    }

    if (!rangeTexts.length) return;
    const out = rangeTexts.join('\n').replace(/[ \t]+\n/g, '\n');
    if (!out) return;
    event.clipboardData?.setData('text/plain', out);
    event.preventDefault();
  });
})();
