(() => {
  "use strict";

  if (window.treepoloMultiField) return;

  function parse(value) {
    const seen = new Set();
    const values = [];
    String(value ?? "").split(",").forEach(token => {
      const item = token.trim();
      if (!item || seen.has(item)) return;
      seen.add(item);
      values.push(item);
    });
    return values;
  }

  function serialize(values) {
    return parse(Array.isArray(values) ? values.join(",") : values).join(",");
  }

  function isControl(control) {
    return Boolean(control?.matches?.('input[data-multi-field]'));
  }

  function values(control) {
    return isControl(control) ? parse(control.value) : [];
  }

  function write(control, nextValues, { emit = true } = {}) {
    if (!isControl(control)) return false;
    const next = serialize(nextValues);
    if (control.value === next) return false;
    control.value = next;
    if (emit) control.dispatchEvent(new Event("change", { bubbles:true }));
    return true;
  }

  window.treepoloMultiField = { parse, serialize, isControl, values, write };
})();
