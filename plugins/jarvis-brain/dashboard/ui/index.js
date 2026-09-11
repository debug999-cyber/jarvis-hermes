/* JARVIS — вкладка «База знаний» для web-панели Hermes.
 * Обычный IIFE без сборки: React и компоненты берём из window.__HERMES_PLUGIN_SDK__ (официальный Plugin SDK панели).
 * Данные — GET /api/plugins/jarvis-brain/overview (см. plugin_api.py). Только чтение.
 * Архитектор: ERTGYKI (github.com/debug999-cyber) */
(function () {
  "use strict";
  const SDK = window.__HERMES_PLUGIN_SDK__;
  if (!SDK) return;
  const { React } = SDK;
  const { useState, useEffect } = SDK.hooks;
  const { Card, CardHeader, CardTitle, CardContent, Input, Badge } = SDK.components;
  const h = React.createElement;

  function Section(title, children) {
    return h(Card, null, h(CardHeader, null, h(CardTitle, { className: "text-sm" }, title)), h(CardContent, null, children));
  }
  function kv(label, value) { return h("div", { className: "jb-kv", key: label }, h("span", null, label), h("b", null, value)); }
  function list(items, render, empty) {
    if (!items || !items.length) return h("div", { className: "text-muted-foreground text-xs" }, empty || "Пусто");
    return h("ul", { className: "jb-list" }, items.map((it, i) => h("li", { key: i }, render(it))));
  }

  function BrainPage() {
    const [q, setQ] = useState("");
    const [data, setData] = useState(null);
    const [err, setErr] = useState("");
    useEffect(() => {
      const t = setTimeout(() => {
        SDK.fetchJSON("/api/plugins/jarvis-brain/overview?q=" + encodeURIComponent(q))
          .then((d) => { setData(d); setErr(""); })
          .catch((e) => setErr(String(e && e.message || e)));
      }, q ? 250 : 0);
      return () => clearTimeout(t);
    }, [q]);

    const b = data && data.brain, f = data && data.facts;
    const notes = b && b.ok ? (b.search || b.recent) : [];
    const facts = f && f.ok ? (f.search || f.recent) : [];

    return h("div", { className: "space-y-4" },
      h("div", { className: "flex items-center gap-3" },
        h("h2", { className: "text-lg font-semibold" }, "База знаний JARVIS"),
        h("span", { className: "jb-sig" }, "read-only")),
      h(Input, { placeholder: "Поиск по заметкам и фактам…", value: q, onChange: (e) => setQ(e.target.value) }),
      err ? h("div", { className: "text-destructive text-sm" }, "Ошибка: " + err) : null,
      h("div", { className: "jb-grid" },
        Section("Память Hermes (holographic)", f && f.ok
          ? h("div", null,
              kv("Фактов", f.facts),
              h("div", { className: "mt-2" }, (f.categories || []).map((c) => h("span", { className: "jb-tag", key: c.category }, c.category, " ", h("b", null, c.n)))),
              h("div", { className: "mt-3" }, list(facts, (x) => h("span", null, h("small", null, "#" + x.fact_id + " · trust " + x.trust + " · " + x.category), h("br"), x.content), "Фактов пока нет — скажите JARVIS «запомни…»")))
          : h("div", { className: "text-muted-foreground text-xs" }, "memory_store.db ещё не создан — включите memory.provider: holographic (install.sh делает это сам)")),
        Section(q ? "Заметки — результаты" : "Заметки — недавнее", b && b.ok
          ? list(notes, (n) => h("span", null, h("small", null, n.kind + (n.entity ? " · " + n.entity : "")), h("br"), n.content), "Ничего не найдено")
          : h("div", { className: "text-muted-foreground text-xs" }, "brain.db ещё не создан — появится после первого разговора")),
        Section("Карточки", b && b.ok ? h("div", null, (b.top_entities || []).map((e) => h("span", { className: "jb-tag", key: e.name }, e.name, " ", h("b", null, e.notes)))) : null),
        Section("Дневник", b && b.ok ? list(b.diary, (d) => h("span", null, h("b", null, d.day), " — ", d.summary)) : null),
        Section("Хранилище файлов ~/JARVIS", b && b.ok && b.vault
          ? h("div", null, kv("Файлов проиндексировано", b.vault.files),
              h("div", { className: "mt-2" }, (b.vault.sources || []).map((s) => h(Badge, { key: s.name, variant: "secondary", className: "mr-1" }, s.name))),
              h("div", { className: "mt-2" }, list(b.vault.recent, (r) => h("small", null, r.rel), "Пусто")))
          : h("div", { className: "text-muted-foreground text-xs" }, "—")),
        Section("Статус", b && b.ok
          ? h("div", null, kv("Заметок · карточек · дней", b.notes + " · " + b.entities + " · " + b.episodes),
              kv("Необработанных ходов", b.pending_turns),
              kv("Последняя ревизия", b.last_review ? String(b.last_review.finished_at).slice(0, 16).replace("T", " ") : "ещё не было"),
              (b.failures && b.failures.length) ? h("div", { className: "mt-2" }, h("small", null, "Сбои инструментов: "), b.failures.map((x) => h("span", { className: "jb-tag", key: x.tool }, x.tool, " ×", x.count))) : null,
              data && data.paths ? h("div", { className: "mt-3 text-xs text-muted-foreground" }, data.paths.brain_db, h("br"), data.paths.facts_db) : null)
          : null)));
  }

  window.__HERMES_PLUGINS__.register("jarvis-brain", BrainPage);
})();
