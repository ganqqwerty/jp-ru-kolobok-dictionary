fetch("data.json").then(response => response.json()).then(data => {
  const search = document.querySelector("#search");
  const results = document.querySelector("#results");
  const count = document.querySelector("#count");
  const pageSize = 30;
  let page = 0;
  const pilotLinks = new Map();
  for (const article of data.articles) {
    for (const alias of article.aliases) {
      for (const key of [alias.term, alias.reading]) {
        const ids = pilotLinks.get(key) || new Set();
        ids.add(article.id);
        pilotLinks.set(key, ids);
      }
    }
  }

  function text(node) {
    if (typeof node === "string") return node;
    if (Array.isArray(node)) return node.map(text).join(" ");
    if (node && typeof node === "object") return text(node.content);
    return "";
  }

  function render(node) {
    if (typeof node === "string") return document.createTextNode(node);
    if (Array.isArray(node)) {
      const fragment = document.createDocumentFragment();
      node.forEach(child => fragment.append(render(child)));
      return fragment;
    }
    if (!node || typeof node !== "object") return document.createTextNode("");
    const allowed = ["div", "span", "a", "details", "summary", "ul", "li", "i", "em", "b", "strong", "br"];
    const element = document.createElement(allowed.includes(node.tag) ? node.tag : "span");
    if (node.tag === "a") {
      const sourceHref = typeof node.href === "string" ? node.href : "/";
      const resolved = new URL(sourceHref, "https://www.wadoku.de/");
      const query = resolved.searchParams.get("query");
      const matches = query && pilotLinks.get(query);
      element.href = matches?.size === 1 ? `#wadoku-${[...matches][0]}` :
        resolved.origin === "https://www.wadoku.de" ? resolved.href : "https://www.wadoku.de/";
      element.rel = "noopener";
    }
    if (typeof node.data?.wadokuTerm === "string") element.dataset.wadokuTerm = node.data.wadokuTerm;
    if (node.lang === "ja") element.lang = "ja";
    if (node.content !== undefined) element.append(render(node.content));
    return element;
  }

  function card(article) {
    const element = document.createElement("article");
    element.id = `wadoku-${article.id}`;
    const heading = document.createElement("h2");
    heading.textContent = `${article.term} 【${article.reading}】`;
    element.append(heading);
    const permalink = document.createElement("a");
    permalink.href = `#wadoku-${article.id}`;
    permalink.textContent = "Постоянная ссылка";
    element.append(permalink);
    const aliases = document.createElement("small");
    aliases.textContent = article.aliases.map(alias => `${alias.term} 【${alias.reading}】`).join(" · ");
    element.append(aliases);
    const body = document.createElement("div");
    body.append(render(article.content));
    element.append(body);
    const source = document.createElement("details");
    const summary = document.createElement("summary");
    summary.textContent = "Исходный текст";
    const pre = document.createElement("pre");
    pre.className = "source";
    pre.textContent = text(article.source_content);
    source.append(summary, pre);
    element.append(source);
    return element;
  }

  function show() {
    const query = search.value.trim().toLocaleLowerCase();
    const matches = data.articles.filter(article => !query ||
      `${article.term} ${article.reading} ${article.russian_text}`.toLocaleLowerCase().includes(query));
    const pageCount = Math.max(1, Math.ceil(matches.length / pageSize));
    page = Math.min(page, pageCount - 1);
    count.textContent = `${matches.length} статей · страница ${page + 1} из ${pageCount}`;
    results.replaceChildren();
    matches.slice(page * pageSize, (page + 1) * pageSize).forEach(article => results.append(card(article)));

    const nav = document.createElement("nav");
    const first = document.createElement("button");
    first.textContent = "Первая";
    first.disabled = page === 0;
    first.addEventListener("click", () => { page = 0; show(); });
    const previous = document.createElement("button");
    previous.textContent = "Назад";
    previous.disabled = page === 0;
    previous.addEventListener("click", () => { page--; show(); });
    const next = document.createElement("button");
    next.textContent = "Дальше";
    next.disabled = page + 1 >= pageCount;
    next.addEventListener("click", () => { page++; show(); });
    const last = document.createElement("button");
    last.textContent = "Последняя";
    last.disabled = page + 1 >= pageCount;
    last.addEventListener("click", () => { page = pageCount - 1; show(); });
    nav.append(first, previous, next, last);
    results.append(nav);
  }

  function openDirectLink() {
    const id = location.hash.slice(1).replace(/^wadoku-/, "");
    if (!id) return;
    const index = data.articles.findIndex(article => String(article.id) === id);
    if (index < 0) return;
    page = Math.floor(index / pageSize);
    show();
    document.getElementById(`wadoku-${id}`)?.scrollIntoView();
  }

  search.addEventListener("input", () => { page = 0; show(); });
  window.addEventListener("hashchange", openDirectLink);
  show();
  openDirectLink();
});
