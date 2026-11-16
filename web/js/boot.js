try { const t = localStorage.getItem("atlas-theme"); if (t) document.documentElement.dataset.theme = t;
  else if (matchMedia("(prefers-color-scheme: dark)").matches) document.documentElement.dataset.theme = "dark"; } catch (e) {}
