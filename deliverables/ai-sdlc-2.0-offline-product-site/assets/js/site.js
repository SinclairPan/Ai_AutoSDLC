(() => {
  "use strict";
  document.documentElement.classList.add("js");

  const setupMobileNavigation = (root = document) => {
    const toggle = root.querySelector("[data-nav-toggle]");
    const menu = root.querySelector("[data-nav-menu]");
    if (!toggle || !menu) return;
    const setOpen = (open) => {
      toggle.setAttribute("aria-expanded", String(open));
      menu.dataset.open = String(open);
    };
    toggle.addEventListener("click", () => {
      setOpen(toggle.getAttribute("aria-expanded") !== "true");
    });
    root.addEventListener("keydown", (event) => {
      if (
        event.key !== "Escape" ||
        toggle.getAttribute("aria-expanded") !== "true"
      )
        return;
      setOpen(false);
      toggle.focus();
    });
  };

  const setupExternalLinks = (root = document) => {
    root
      .querySelectorAll('a[href^="http://"], a[href^="https://"]')
      .forEach((link) => {
        link.target = "_blank";
        link.rel = "noopener noreferrer";
      });
  };

  const setupTabs = (root = document) => {
    root.querySelectorAll("[data-tabs]").forEach((group) => {
      const tabs = [...group.querySelectorAll("[data-tab]")];
      const panels = [...group.querySelectorAll("[data-tab-panel]")];
      const activate = (id, push) => {
        const selected = tabs.find((tab) => tab.dataset.tab === id) || tabs[0];
        if (!selected) return;
        tabs.forEach((tab) => {
          const active = tab === selected;
          tab.setAttribute("aria-selected", String(active));
          tab.tabIndex = active ? 0 : -1;
        });
        panels.forEach((panel) => {
          panel.hidden = panel.id !== selected.getAttribute("aria-controls");
        });
        if (push && location.hash !== `#${selected.dataset.tab}`) {
          history.pushState(null, "", `#${selected.dataset.tab}`);
        }
      };
      const restore = () => activate(location.hash.slice(1), false);
      tabs.forEach((tab, index) => {
        tab.addEventListener("click", () => activate(tab.dataset.tab, true));
        tab.addEventListener("keydown", (event) => {
          const keyMoves = {
            ArrowLeft: (index - 1 + tabs.length) % tabs.length,
            ArrowRight: (index + 1) % tabs.length,
            Home: 0,
            End: tabs.length - 1,
          };
          if (!(event.key in keyMoves)) return;
          event.preventDefault();
          tabs[keyMoves[event.key]].click();
          tabs[keyMoves[event.key]].focus();
        });
      });
      window.addEventListener("popstate", restore);
      window.addEventListener("hashchange", restore);
      restore();
    });
  };

  document.addEventListener("DOMContentLoaded", () => {
    setupMobileNavigation();
    setupTabs();
    setupExternalLinks();
  });
})();
