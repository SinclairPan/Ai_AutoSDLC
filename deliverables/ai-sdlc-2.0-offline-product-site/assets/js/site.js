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
        const selectedScenario = selected.dataset.guideTabScenario;
        if (selectedScenario) {
          tabs.forEach((tab) => {
            tab.hidden = tab.dataset.guideTabScenario !== selectedScenario;
          });
          group
            .querySelectorAll("[data-guide-scenario-selector]")
            .forEach((link) => {
              if (link.dataset.guideScenarioSelector === selectedScenario) {
                link.setAttribute("aria-current", "true");
              } else {
                link.removeAttribute("aria-current");
              }
            });
        }
        panels.forEach((panel) => {
          panel.hidden = panel.id !== selected.getAttribute("aria-controls");
        });
        if (push && location.hash !== `#${selected.dataset.tab}`) {
          history.pushState(null, "", `#${selected.dataset.tab}`);
        }
      };
      const restore = () => activate(location.hash.slice(1), false);
      tabs.forEach((tab) => {
        tab.addEventListener("click", () => activate(tab.dataset.tab, true));
        tab.addEventListener("keydown", (event) => {
          const visibleTabs = tabs.filter((candidate) => !candidate.hidden);
          const index = visibleTabs.indexOf(tab);
          if (index < 0) return;
          const keyMoves = {
            ArrowLeft: (index - 1 + visibleTabs.length) % visibleTabs.length,
            ArrowRight: (index + 1) % visibleTabs.length,
            Home: 0,
            End: visibleTabs.length - 1,
          };
          if (!(event.key in keyMoves)) return;
          event.preventDefault();
          visibleTabs[keyMoves[event.key]].click();
          visibleTabs[keyMoves[event.key]].focus();
        });
      });
      window.addEventListener("popstate", restore);
      window.addEventListener("hashchange", restore);
      restore();
    });
  };

  const fallbackCopyText = (text, root = document) => {
    const textarea = root.createElement("textarea");
    textarea.value = text;
    textarea.setAttribute("readonly", "");
    textarea.style.position = "fixed";
    textarea.style.opacity = "0";
    root.body.append(textarea);
    textarea.focus();
    textarea.select();
    const writeExactText = (event) => {
      if (!event.clipboardData) return;
      event.clipboardData.setData("text/plain", text);
      event.preventDefault();
    };
    root.addEventListener("copy", writeExactText, { once: true });
    let copied = false;
    try {
      copied = root.execCommand("copy");
    } catch {
      copied = false;
    }
    root.removeEventListener("copy", writeExactText);
    textarea.remove();
    return copied;
  };

  const copyText = async (text, root = document) => {
    if (window.isSecureContext && navigator.clipboard?.writeText) {
      try {
        await navigator.clipboard.writeText(text);
        return true;
      } catch {
        // 本地文件可能暴露 Clipboard API，但仍拒绝实际写入。
      }
    }
    return fallbackCopyText(text, root);
  };

  const setupCopyCommands = (root = document) => {
    root.querySelectorAll("[data-copy-command]").forEach((button) => {
      const commandId = button.dataset.copyCommand;
      const status = root.querySelector(`[data-copy-status="${commandId}"]`);
      const command = button
        .closest('[data-guide-part="command"]')
        ?.querySelector(`[data-guide-command="${commandId}"]`);
      button.addEventListener("click", async () => {
        button.disabled = true;
        const copied = Boolean(command) && (await copyText(command.textContent, root));
        button.disabled = false;
        button.focus();
        button.dataset.copyState = copied ? "success" : "error";
        button.textContent = copied ? "再次复制" : "重试复制";
        if (status) {
          status.textContent = copied
            ? "已复制完整命令"
            : "复制失败，请手动选择命令";
        }
      });
    });
  };

  const setupVideo = (root = document, config = window.AISDLC_VIDEO) => {
    const empty = root.querySelector("[data-video-empty]");
    const emptyPoster = root.querySelector("[data-video-empty-poster]");
    const video = root.querySelector("[data-video-player]");
    const title = root.querySelector("[data-video-title]");
    const trigger = root.querySelector("[data-video-trigger]");
    if (!empty || !video || !config) return;
    if (title && config.title) title.textContent = config.title;
    if (emptyPoster && config.poster) emptyPoster.src = config.poster;
    if (!config.src) {
      trigger?.addEventListener("click", (event) => {
        event.preventDefault();
        empty.focus();
      });
      return;
    }
    const source = document.createElement("source");
    source.src = config.src;
    source.type = config.type;
    video.append(source);
    if (config.captions) {
      const track = document.createElement("track");
      track.kind = "captions";
      track.srclang = "zh-CN";
      track.label = "中文字幕";
      track.src = config.captions;
      video.append(track);
    }
    video.poster = config.poster;
    video.hidden = false;
    empty.hidden = true;
  };

  document.addEventListener("DOMContentLoaded", () => {
    setupMobileNavigation();
    setupTabs();
    setupExternalLinks();
    setupCopyCommands();
    setupVideo();
  });
})();
