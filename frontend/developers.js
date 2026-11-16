(() => {
  const search = document.getElementById("doc-search");
  const count = document.getElementById("search-count");
  const cards = [...document.querySelectorAll("[data-search]")];
  search?.addEventListener("input", () => {
    const q = search.value.trim().toLowerCase(); let visible = 0;
    for (const card of cards) {
      const match = !q || (card.dataset.search + " " + card.textContent).toLowerCase().includes(q);
      card.classList.toggle("hidden", !match); if (match) visible++;
    }
    count.textContent = q ? `${visible} matching sections` : "All guides";
  });
  document.querySelectorAll("[data-copy]").forEach(button => button.addEventListener("click", async () => {
    const target = document.getElementById(button.dataset.copy); if (!target) return;
    try { await navigator.clipboard.writeText(target.innerText); button.textContent = "Copied"; }
    catch { button.textContent = "Select text to copy"; }
    setTimeout(() => { button.textContent = "Copy"; }, 1800);
  }));
})();
