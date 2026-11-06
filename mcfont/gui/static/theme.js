// Runs before the page is drawn so it never shows the wrong theme
"use strict";
(function () {
  var STORAGE_KEY = "theme-preference";
  var pref = null;
  try { pref = localStorage.getItem(STORAGE_KEY); } catch (e) { /* storage blocked */ }
  var dark = window.matchMedia && window.matchMedia("(prefers-color-scheme: dark)").matches;
  var effective = pref === "light" || pref === "dark" ? pref : dark ? "dark" : "light";
  document.documentElement.setAttribute("data-theme", effective);
})();