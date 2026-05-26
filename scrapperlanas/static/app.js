document.addEventListener("DOMContentLoaded", () => {
  const shell = document.getElementById("app-shell");
  const toggle = document.querySelector("[data-sidebar-toggle]");
  const sectionLinks = Array.from(document.querySelectorAll("[data-section-target]"));
  const sections = Array.from(document.querySelectorAll(".app-section"));
  const copyButtons = Array.from(document.querySelectorAll("[data-copy-target]"));

  if (toggle && shell) {
    toggle.addEventListener("click", () => {
      shell.classList.toggle("sidebar-collapsed");
    });
  }

  copyButtons.forEach((button) => {
    button.addEventListener("click", async () => {
      const target = document.getElementById(button.dataset.copyTarget);
      if (!target) {
        return;
      }

      const text = target.value || target.textContent || "";
      if (!text.trim()) {
        return;
      }

      const originalLabel = button.dataset.originalLabel || button.textContent;
      button.dataset.originalLabel = originalLabel;

      try {
        await navigator.clipboard.writeText(text);
        button.textContent = "Copiado";
        const csrfInput = document.querySelector('input[name="csrf_token"]');
        const logUrl = button.dataset.copyLogUrl;
        if (logUrl && csrfInput) {
          fetch(logUrl, {
            method: "POST",
            headers: {
              "X-CSRFToken": csrfInput.value,
              "X-Requested-With": "fetch",
              "Accept": "application/json",
            },
          }).catch(() => {});
        }
        window.setTimeout(() => {
          button.textContent = originalLabel;
        }, 1600);
      } catch (_error) {
        button.textContent = "No copiado";
        window.setTimeout(() => {
          button.textContent = originalLabel;
        }, 1600);
      }
    });
  });

  if (!sectionLinks.length || !sections.length) {
    return;
  }

  const activateSection = (sectionId) => {
    const safeId = sections.some((section) => section.id === `section-${sectionId}`) ? sectionId : "inbox";

    sections.forEach((section) => {
      const isActive = section.id === `section-${safeId}`;
      section.classList.toggle("hidden", !isActive);
      section.classList.toggle("block", isActive);
    });

    sectionLinks.forEach((link) => {
      const isActive = link.dataset.sectionTarget === safeId;
      link.classList.toggle("is-nav-active", isActive);
    });
  };

  const readHash = () => window.location.hash.replace(/^#/, "");

  sectionLinks.forEach((link) => {
    link.addEventListener("click", (event) => {
      const target = link.dataset.sectionTarget;
      const linkUrl = new URL(link.href, window.location.origin);

      if (linkUrl.pathname === window.location.pathname) {
        event.preventDefault();
        activateSection(target);
        window.history.replaceState(null, "", `#${target}`);
      }
    });
  });

  window.addEventListener("hashchange", () => {
    activateSection(readHash());
  });

  activateSection(readHash() || "inbox");
});
