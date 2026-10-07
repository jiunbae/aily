(function () {
  var root = document.documentElement;
  var fallback = root.dataset.themeDefault || "dark";
  var preference = fallback;

  try {
    preference = localStorage.getItem("aily-theme") || fallback;
  } catch (_error) {
    preference = fallback;
  }

  var resolved = preference;
  if (preference === "system") {
    resolved = window.matchMedia("(prefers-color-scheme: light)").matches
      ? "light"
      : "dark";
  }

  root.classList.toggle("dark", resolved === "dark");
  root.classList.toggle("light", resolved === "light");
  root.style.colorScheme = resolved;
})();
