// Applies the portal's saved theme before first paint (same keys the dashboard uses).
(function () {
  try {
    var t = localStorage.getItem("ingram.theme");
    var dark = matchMedia("(prefers-color-scheme: dark)").matches;
    document.documentElement.dataset.theme = t === "dark" || ((t === "system" || t === null) && dark) ? "dark" : "light";
  } catch (e) { /* storage blocked: keep the light default */ }
})();
