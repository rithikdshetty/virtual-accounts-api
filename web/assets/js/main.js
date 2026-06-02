/* Landing-page interactions: scroll reveal, sticky nav, code tabs, copy, mobile menu */
(function () {
  "use strict";

  // Sticky nav background on scroll
  const nav = document.getElementById("nav");
  const onScroll = () => {
    if (!nav) return;
    nav.classList.toggle("scrolled", window.scrollY > 12);
  };
  window.addEventListener("scroll", onScroll, { passive: true });
  onScroll();

  // Mobile menu toggle
  const toggle = document.getElementById("navToggle");
  if (toggle && nav) {
    toggle.addEventListener("click", () => nav.classList.toggle("open"));
    document.querySelectorAll("#navLinks a").forEach((a) =>
      a.addEventListener("click", () => nav.classList.remove("open"))
    );
  }

  // Scroll reveal via IntersectionObserver
  const reveals = document.querySelectorAll(".reveal");
  if ("IntersectionObserver" in window && reveals.length) {
    const io = new IntersectionObserver(
      (entries) => {
        entries.forEach((e) => {
          if (e.isIntersecting) {
            e.target.classList.add("in");
            io.unobserve(e.target);
          }
        });
      },
      { threshold: 0.12, rootMargin: "0px 0px -40px 0px" }
    );
    reveals.forEach((el) => io.observe(el));
  } else {
    reveals.forEach((el) => el.classList.add("in"));
  }

  // Code language tabs
  const tabs = document.querySelectorAll(".code-tab");
  const panes = document.querySelectorAll(".code-pane");
  tabs.forEach((tab) => {
    tab.addEventListener("click", () => {
      const lang = tab.dataset.lang;
      tabs.forEach((t) => t.classList.toggle("active", t === tab));
      panes.forEach((p) => p.classList.toggle("active", p.dataset.lang === lang));
    });
  });

  // Copy active code pane
  const copyBtn = document.getElementById("copyBtn");
  if (copyBtn) {
    copyBtn.addEventListener("click", async () => {
      const active = document.querySelector(".code-pane.active pre");
      if (!active) return;
      try {
        await navigator.clipboard.writeText(active.innerText);
        const prev = copyBtn.textContent;
        copyBtn.textContent = "Copied ✓";
        setTimeout(() => (copyBtn.textContent = prev), 1600);
      } catch (_) {
        copyBtn.textContent = "Press ⌘C";
      }
    });
  }
})();
