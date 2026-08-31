(() => {
  "use strict";

  function install() {
    const kInput = document.querySelector("#s4-cluster-k");
    if (!kInput || document.querySelector("#s4-cluster-auto")) return false;
    const label = kInput.closest("label");
    if (!label) return false;

    label.firstChild.textContent = "群數 Clusters ";
    const autoLabel = document.createElement("label");
    autoLabel.className = "checkbox-line";
    autoLabel.innerHTML = '<input id="s4-cluster-auto" type="checkbox"> 自動決定群數 Auto K（允許 K=1）';
    label.insertAdjacentElement("afterend", autoLabel);

    const hint = document.createElement("div");
    hint.id = "s4-cluster-auto-hint";
    hint.className = "hint";
    hint.style.gridColumn = "1 / -1";
    autoLabel.insertAdjacentElement("afterend", hint);

    const auto = autoLabel.querySelector("input");
    const method = document.querySelector("#s4-cluster-method");

    function renderHint() {
      const criterion = method?.value === "gmm" ? "BIC" : "K-means spherical BIC";
      hint.textContent = auto.checked
        ? `Auto K 會從 K=1 開始比較，使用 ${criterion}；K 上限依樣本數自動調整，並拒絕過小群。結果會另外輸出每個候選 K 的診斷。`
        : "手動模式維持原本 K=2～50。";
    }

    function setAuto(enabled, preserveManual = true) {
      if (enabled) {
        if (preserveManual && Number(kInput.value) >= 2) kInput.dataset.manualK = kInput.value;
        kInput.min = "0";
        kInput.value = "0";
        kInput.disabled = true;
        auto.checked = true;
      } else {
        kInput.disabled = false;
        kInput.min = "2";
        if (Number(kInput.value) < 2) kInput.value = kInput.dataset.manualK || "3";
        auto.checked = false;
      }
      renderHint();
    }

    auto.addEventListener("change", () => setAuto(auto.checked));
    method?.addEventListener("change", renderHint);
    document.addEventListener("treepolo:analysis-options-changed", () => {
      if (Number(kInput.value) === 0) setAuto(true, false);
      else if (auto.checked) setAuto(false, false);
    });

    if (Number(kInput.value) === 0) setAuto(true, false);
    else renderHint();
    return true;
  }

  if (!install()) {
    const observer = new MutationObserver(() => {
      if (install()) observer.disconnect();
    });
    observer.observe(document.documentElement, { childList:true, subtree:true });
    setTimeout(() => observer.disconnect(), 15000);
  }
})();
