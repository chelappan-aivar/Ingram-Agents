// Adds a "Pipeline Runs" entry to the dashboard sidebar. Loaded by every page of the portal.
// The portal is a compiled React app that re-renders its sidebar, so a MutationObserver puts the link back if it is removed.
(function () {
  var ID = "pipeline-runs-nav";
  var LINK_CLASS =
    "block truncate rounded-lg px-3 py-1.5 text-sm leading-5 transition-colors text-[var(--color-gray-600)] hover:bg-[var(--color-primary-50)] hover:text-[var(--color-primary-700)]";

  function mount() {
    var nav = document.getElementById("dashboard-nav");
    if (!nav || document.getElementById(ID)) return;
    var host = nav.firstElementChild || nav;
    var wrap = document.createElement("div");
    wrap.id = ID;
    wrap.style.margin = "4px 0 12px";
    var a = document.createElement("a");
    a.href = "/pipeline/";
    a.className = LINK_CLASS;
    a.title = "Pipeline Runs";
    a.textContent = "Pipeline Runs";
    a.style.fontWeight = "600";
    if (location.pathname.indexOf("/pipeline") === 0) a.setAttribute("aria-current", "page");
    wrap.appendChild(a);
    host.insertBefore(wrap, host.firstChild);
  }

  new MutationObserver(mount).observe(document.documentElement, { childList: true, subtree: true });
  mount();
})();
