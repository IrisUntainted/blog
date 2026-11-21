(() => {
  const ACTIVE_INPUTS = new Map();
  const SR_INPUTS = new Map();
  const SR_CHECKBOXES = new Map();
  const SR_RADIOS = new Map();
  const BOUND_COND_DOCS = new WeakSet();

  const form = window.form = window.form || {_conds: {}, _actions: {}, _on: {}};

  const isSvgDoc = (doc) =>
      !!(doc && doc.documentElement &&
          doc.documentElement.tagName.toLowerCase() === 'svg');

  const eachSvgDoc = (fn) => {
    document.querySelectorAll('object.page[type="image/svg+xml"]')
        .forEach((obj) => {
          const doc = obj.contentDocument;
          if (isSvgDoc(doc)) fn(doc, obj);
        });
  };

  const initCondElements = (svgDoc) => {
    if (BOUND_COND_DOCS.has(svgDoc)) {
      return;
    }
    BOUND_COND_DOCS.add(svgDoc);

    svgDoc.querySelectorAll('[data-cond-id]').forEach((el) => {
      if (el.getAttribute('data-cond-branch') === '1') {
        el.style.display = 'none';
      }
    });
    svgDoc.documentElement.style.setProperty('--svg-cond-b1', 'inline');
  };

  const evaluateCond = (condId) => {
    const fn = form._conds[condId] ||
        (condId.startsWith('ui:') ? form._conds[condId.slice(3)] : undefined);
    return typeof fn === 'function' ? !!fn() : false;
  };

  const splitRadio = (arg) => {
    const idx = arg.indexOf(':');
    if (idx <= 0) return null;
    const group = arg.slice(0, idx).trim();
    const value = arg.slice(idx + 1).trim();
    return group && value ? [group, value] : null;
  };

  const syncAriaStates = (svgDoc) => {
    const parseHref = window.parseActionHref;
    if (typeof parseHref !== 'function') return;
    svgDoc.querySelectorAll('a[role="checkbox"], a[role="radio"]')
        .forEach((a) => {
          const token = parseHref(a.getAttribute('href') || '');
          if (!token) return;
          if (token.key === 'checkbox') {
            const cb = SR_CHECKBOXES.get(token.arg);
            a.setAttribute('aria-checked', cb && cb.checked ? 'true' : 'false');
          } else if (token.key === 'radio') {
            const parsed = splitRadio(token.arg);
            if (!parsed) return;
            const radios = SR_RADIOS.get(parsed[0]);
            const checked = !!(radios &&
                radios.some((r) => r.value === parsed[1] && r.checked));
            a.setAttribute('aria-checked', checked ? 'true' : 'false');
          }
        });
  };

  const refreshCond = (condId) => {
    const result = evaluateCond(condId);
    const selector = '[data-cond-id="' + CSS.escape(condId) + '"]';
    const toggle = (el) => {
      const branch = el.getAttribute('data-cond-branch');
      const show = branch === '1' ? result : !result;
      el.style.display = show ? '' : 'none';
    };
    eachSvgDoc((svgDoc) => {
      svgDoc.querySelectorAll(selector).forEach(toggle);
    });
    document.querySelectorAll(selector).forEach(toggle);
  };

  const refreshAllConds = () => {
    const ids = new Set();
    eachSvgDoc((svgDoc) => {
      svgDoc.querySelectorAll('[data-cond-id]').forEach((el) => {
        ids.add(el.getAttribute('data-cond-id'));
      });
      syncAriaStates(svgDoc);
    });
    document.querySelectorAll('[data-cond-id]').forEach((el) => {
      ids.add(el.getAttribute('data-cond-id'));
    });
    for (const condId of ids) {
      refreshCond(condId);
    }
  };

  const findVisibleInputAnchor = (id) => {
    const target = '#action=input:' + id;
    const layout = document.documentElement.getAttribute('data-layout') || 'desktop';
    const wrapper = document.querySelector('.pages-' + layout);
    const objects = wrapper
        ? wrapper.querySelectorAll('object.page[type="image/svg+xml"]')
        : document.querySelectorAll('object.page[type="image/svg+xml"]');
    for (const obj of objects) {
      const doc = obj.contentDocument;
      if (!isSvgDoc(doc)) continue;
      for (const a of doc.querySelectorAll('a[href^="#action=input:"]')) {
        if (a.getAttribute('href') === target) {
          const rect = a.getBoundingClientRect();
          if (rect.width > 0 && rect.height > 0) {
            return {anchorNode: a, pageObject: obj};
          }
        }
      }
    }
    return null;
  };

  const removeInputOverlay = (id) => {
    const entry = ACTIVE_INPUTS.get(id);
    if (entry) {
      entry.element.remove();
      ACTIVE_INPUTS.delete(id);
    }
  };

  const syncVisibleOverlays = () => {
    for (const [id, srInput] of SR_INPUTS) {
      if (!srInput.value) {
        const entry = ACTIVE_INPUTS.get(id);
        if (entry && document.activeElement !== entry.element) {
          removeInputOverlay(id);
        }
        continue;
      }
      if (!ACTIVE_INPUTS.has(id)) {
        const target = findVisibleInputAnchor(id);
        if (target) {
          spawnInputOverlay(id, target.anchorNode, target.pageObject, undefined, false);
        }
      }
    }
  };

  const syncValue = (id, value) => {
    const srInput = SR_INPUTS.get(id);
    if (srInput && srInput.value !== value) {
      srInput.value = value;
    }
    const overlay = ACTIVE_INPUTS.get(id);
    if (overlay) {
      if (overlay.element.value !== value) {
        overlay.element.value = value;
      }
      if (!value && document.activeElement !== overlay.element) {
        removeInputOverlay(id);
      }
    } else if (value) {
      const target = findVisibleInputAnchor(id);
      if (target) {
        spawnInputOverlay(id, target.anchorNode, target.pageObject, undefined, false);
      }
    }
  };

  const fireInput = (id, value) => {
    syncValue(id, value);
    const fns = form._on[id];
    if (fns) fns.forEach((fn) => fn(value));
    document.dispatchEvent(new CustomEvent('svg-input', {
      detail: {id, value},
    }));
  };

  form.get = (id) => {
    const srInput = SR_INPUTS.get(id);
    if (srInput) return srInput.value;
    const cb = SR_CHECKBOXES.get(id);
    if (cb) return cb.checked ? 'true' : '';
    const radios = SR_RADIOS.get(id);
    if (radios) {
      for (const r of radios) {
        if (r.checked) return r.value;
      }
      return '';
    }
    const entry = ACTIVE_INPUTS.get(id);
    return entry ? entry.element.value : '';
  };

  form.set = (id, value) => {
    const str = String(value);
    const cb = SR_CHECKBOXES.get(id);
    if (cb) {
      cb.checked = str === 'true';
      fireInput(id, cb.checked ? 'true' : '');
      return;
    }
    const radios = SR_RADIOS.get(id);
    if (radios) {
      let matched = '';
      for (const r of radios) {
        r.checked = r.value === str;
        if (r.checked) matched = r.value;
      }
      fireInput(id, matched);
      return;
    }
    fireInput(id, str);
  };

  form.refresh = refreshAllConds;

  const bindPageObject = (pageObject) => {
    const svgDoc = pageObject.contentDocument;
    if (!isSvgDoc(svgDoc)) {
      return;
    }
    initCondElements(svgDoc);
    refreshAllConds();
    syncVisibleOverlays();
  };

  const spawnInputOverlay = (
      id, anchorNode, pageObject, initialKey, shouldFocus = true) => {
    const existing = ACTIVE_INPUTS.get(id);
    if (existing) {
      if (shouldFocus) {
        existing.element.tabIndex = 0;
        existing.element.removeAttribute('aria-hidden');
        existing.element.focus();
      }
      if (initialKey) {
        existing.element.value += initialKey;
        fireInput(id, existing.element.value);
      }
      return;
    }

    const input = document.createElement('input');
    input.type = 'text';
    input.className = 'svg-input-overlay';
    input.dataset.inputId = id;

    const srInput = SR_INPUTS.get(id);
    const label = srInput?.getAttribute('aria-label') ||
        anchorNode.getAttribute('aria-label') || ('Input: ' + id);
    input.setAttribute('aria-label', label);
    if (srInput) {
      input.value = srInput.value;
    }
    if (!shouldFocus) {
      input.tabIndex = -1;
      input.setAttribute('aria-hidden', 'true');
    }

    const reposition = () => {
      const oRect = pageObject.getBoundingClientRect();
      const aRect = anchorNode.getBoundingClientRect();
      if (aRect.width <= 0 || aRect.height <= 0) return;
      input.style.left = (oRect.left + aRect.left + window.scrollX) + 'px';
      input.style.top = (oRect.top + aRect.top + window.scrollY) + 'px';
      input.style.width = aRect.width + 'px';
      input.style.height = aRect.height + 'px';
      input.style.fontSize = Math.round(Math.max(10, aRect.height * 0.5)) + 'px';
    };

    reposition();
    document.body.appendChild(input);
    ACTIVE_INPUTS.set(id, {element: input, reposition});

    const deactivate = () => {
      if (!input.value) {
        removeInputOverlay(id);
      } else {
        input.tabIndex = -1;
        input.setAttribute('aria-hidden', 'true');
      }
    };

    input.addEventListener('focus', () => {
      input.tabIndex = 0;
      input.removeAttribute('aria-hidden');
    });

    input.addEventListener('keydown', (e) => {
      if (e.key === 'Escape') {
        input.blur();
        deactivate();
        anchorNode.focus();
        return;
      }
      if (e.key === 'Tab') {
        e.preventDefault();
        input.blur();
        deactivate();
        const svgDoc = pageObject.contentDocument;
        if (svgDoc) {
          const focusable = [
            ...svgDoc.querySelectorAll('a[aria-label]:not([tabindex="-1"])'),
          ];
          const idx = focusable.indexOf(anchorNode);
          const next = focusable[e.shiftKey ? idx - 1 : idx + 1];
          if (next) next.focus();
          else anchorNode.focus();
        }
      }
    });

    input.addEventListener('blur', deactivate);

    input.addEventListener('input', () => {
      fireInput(id, input.value);
    });

    const trackPosition = () => {
      if (!input.isConnected) return;
      reposition();
      requestAnimationFrame(trackPosition);
    };
    requestAnimationFrame(trackPosition);

    if (shouldFocus) {
      input.focus();
    }

    if (initialKey) {
      input.value += initialKey;
      fireInput(id, input.value);
    }
  };

  const execFormAction = (id) => {
    const fn = form._actions[id];
    if (typeof fn === 'function') {
      fn();
    }
  };

  const toggleCheckbox = (id) => {
    const cb = SR_CHECKBOXES.get(id);
    if (cb) {
      cb.checked = !cb.checked;
      fireInput(id, cb.checked ? 'true' : '');
    }
  };

  const selectRadio = (group, value) => {
    const radios = SR_RADIOS.get(group);
    if (!radios) return;
    for (const r of radios) {
      r.checked = r.value === value;
    }
    fireInput(group, value);
  };

  const cycleRadio = (arg, context, event) => {
    const parsed = splitRadio(arg);
    if (!parsed) return;
    const [group, current] = parsed;
    const svgDocument = context.pageObject.contentDocument;
    if (!svgDocument) return;
    const values = [];
    const firstAnchor = new Map();
    for (const a of svgDocument.querySelectorAll('a[href^="#action="]')) {
      const token = window.parseActionHref(a.getAttribute('href') || '');
      if (!token || token.key !== 'radio') continue;
      const p = splitRadio(token.arg);
      if (!p || p[0] !== group || firstAnchor.has(p[1])) continue;
      firstAnchor.set(p[1], a);
      values.push(p[1]);
    }
    const idx = values.indexOf(current);
    if (idx < 0) return;
    const forward = event.key === 'ArrowDown' || event.key === 'ArrowRight';
    const next = values[forward
        ? (idx + 1) % values.length
        : (idx - 1 + values.length) % values.length];
    firstAnchor.get(next).focus();
    selectRadio(group, next);
  };

  document.querySelectorAll('.sr-only-input').forEach((input) => {
    const id = input.dataset.inputId;
    if (!id) return;
    SR_INPUTS.set(id, input);
    input.addEventListener('input', () => {
      fireInput(id, input.value);
    });
  });

  document.querySelectorAll('.sr-only-action').forEach((btn) => {
    const id = btn.dataset.actionId;
    if (!id) return;
    btn.addEventListener('click', () => {
      execFormAction(id);
    });
  });

  document.querySelectorAll('.sr-only-checkbox').forEach((cb) => {
    const id = cb.dataset.checkboxId;
    if (!id) return;
    SR_CHECKBOXES.set(id, cb);
    form._conds['checkbox:' + id] = () => cb.checked;
    cb.addEventListener('change', () => {
      fireInput(id, cb.checked ? 'true' : '');
    });
  });

  document.querySelectorAll('.sr-only-radio').forEach((radio) => {
    const group = radio.dataset.radioGroup;
    if (!group) return;
    if (!SR_RADIOS.has(group)) {
      SR_RADIOS.set(group, []);
      form._conds['radio:' + group] = () =>
          SR_RADIOS.get(group).some((r) => r.checked);
    }
    SR_RADIOS.get(group).push(radio);
    form._conds['radio:' + group + ':' + radio.value] = () => radio.checked;
    radio.addEventListener('change', () => {
      fireInput(group, radio.value);
    });
  });

  document.querySelectorAll('object.page[type="image/svg+xml"]')
      .forEach((pageObject) => {
        pageObject.addEventListener('load', () => bindPageObject(pageObject));
        bindPageObject(pageObject);
      });

  document.addEventListener('svg-input', () => {
    refreshAllConds();
  });

  refreshAllConds();
  syncVisibleOverlays();

  new MutationObserver(() => {
    for (const [, entry] of ACTIVE_INPUTS) {
      entry.element.remove();
    }
    ACTIVE_INPUTS.clear();
    syncVisibleOverlays();
  }).observe(document.documentElement, {
    attributes: true,
    attributeFilter: ['data-layout'],
  });

  const registerSvgAction = window.registerSvgAction;
  registerSvgAction('input', {
    press: (id, ctx) =>
        spawnInputOverlay(id, ctx.anchorNode, ctx.pageObject, ctx.initialKey),
    keyboard: 'text',
    arrows: (id, ctx) =>
        spawnInputOverlay(id, ctx.anchorNode, ctx.pageObject, ctx.initialKey),
    validate: (id) => id.length > 0,
  });
  registerSvgAction('form-action', {
    press: (id, ctx) => {
      if (ctx) {
        const g = ctx.anchorNode.parentElement;
        g.style.opacity = '0.5';
        setTimeout(() => { g.style.opacity = ''; }, 150);
      }
      execFormAction(id);
    },
    validate: (id) => id.length > 0,
  });
  registerSvgAction('checkbox', {
    press: (id) => toggleCheckbox(id),
    validate: (id) => id.length > 0,
  });
  registerSvgAction('radio', {
    press: (arg) => {
      const p = splitRadio(arg);
      if (p) selectRadio(p[0], p[1]);
    },
    arrows: cycleRadio,
    validate: (arg) => splitRadio(arg) !== null,
  });
})();
