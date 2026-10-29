// Runs before the page is drawn, so a dark-theme user never sees a flash of the light theme.
// theme.js takes over once the page has loaded (buttons, remembering the choice, following the system).
(function () {
  var choice = "system";
  try {
    var stored = localStorage.getItem("live-assist-theme");
    if (stored === "light" || stored === "dark" || stored === "system") choice = stored;
  } catch (e) { /* storage may be blocked; follow the system */ }
  var dark = choice === "dark" || (choice === "system" && window.matchMedia("(prefers-color-scheme: dark)").matches);
  document.documentElement.dataset.theme = choice;
  document.documentElement.dataset.resolved = dark ? "dark" : "light";
})();
