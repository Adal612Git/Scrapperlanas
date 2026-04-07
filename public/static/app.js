document.addEventListener("DOMContentLoaded", () => {
  const shell = document.getElementById("app-shell");
  const toggle = document.querySelector("[data-sidebar-toggle]");
  const sectionLinks = Array.from(document.querySelectorAll("[data-section-target]"));
  const sections = Array.from(document.querySelectorAll(".app-section"));

  if (toggle && shell) {
    toggle.addEventListener("click", () => {
      shell.classList.toggle("sidebar-collapsed");
    });
  }

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
