document.addEventListener("DOMContentLoaded", () => {
  const shell = document.getElementById("app-shell");
  const sectionLinks = Array.from(document.querySelectorAll("[data-section-target]"));
  const sections = Array.from(document.querySelectorAll(".app-section"));

  if (!sectionLinks.length || !sections.length || !shell) {
    return;
  }

  const activateSection = (sectionId, shouldScroll = false) => {
    if (sectionId === "top" || sectionId === "home") {
      sections.forEach((section) => {
        const isHomeSection = section.id === "section-home";
        section.classList.toggle("hidden", !isHomeSection);
        section.classList.toggle("block", isHomeSection);
      });

      sectionLinks.forEach((link) => {
        link.classList.toggle("is-nav-active", link.dataset.sectionTarget === "home");
      });

      if (shouldScroll) {
        window.scrollTo({ top: 0, behavior: "smooth" });
      }
      return;
    }

    const safeId = sections.some((section) => section.id === `section-${sectionId}`) ? sectionId : "inbox";
    const activeSection = sections.find((section) => section.id === `section-${safeId}`);

    sections.forEach((section) => {
      const isActive = section.id === `section-${safeId}`;
      section.classList.toggle("hidden", !isActive);
      section.classList.toggle("block", isActive);
    });

    sectionLinks.forEach((link) => {
      const isActive = link.dataset.sectionTarget === safeId;
      link.classList.toggle("is-nav-active", isActive);
    });

    if (activeSection && shouldScroll) {
      requestAnimationFrame(() => {
        activeSection.scrollIntoView({ behavior: "smooth", block: "start" });
      });
    }
  };

  const readHash = () => window.location.hash.replace(/^#/, "");

  sectionLinks.forEach((link) => {
    link.addEventListener("click", (event) => {
      const target = link.dataset.sectionTarget;
      const linkUrl = new URL(link.href, window.location.origin);

      if (linkUrl.pathname === window.location.pathname) {
        event.preventDefault();
        activateSection(target, true);
        window.history.pushState(null, "", `#${target}`);
      }
    });
  });

  window.addEventListener("popstate", () => {
    activateSection(readHash());
  });
  window.addEventListener("hashchange", () => {
    activateSection(readHash());
  });

  const initialHash = readHash();
  activateSection(initialHash || "home", Boolean(initialHash));
});
