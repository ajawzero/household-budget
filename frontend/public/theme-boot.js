// Applies the saved theme before the app loads, so there is no flash.
// Keep the key and values in sync with src/lib/theme.ts.
(function () {
  try {
    var pref = window.localStorage.getItem("budget.theme");
    if (pref === "light" || pref === "dark") {
      document.documentElement.setAttribute("data-theme", pref);
    }
  } catch (e) {
    // Storage blocked. Auto theme it is.
  }
})();
