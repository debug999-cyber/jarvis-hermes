"""Vault — хранилище файлов и проектов: индексация, поиск по содержимому, чтение, подключение проектов, безопасность."""

from __future__ import annotations

import json
import os
import zipfile

import pytest

from conftest import FakeCtx, load_plugin


@pytest.fixture()
def brain(tmp_path, monkeypatch):
    monkeypatch.setenv("JARVIS_BRAIN_DIR", str(tmp_path / "db"))
    monkeypatch.setenv("JARVIS_VAULT_DIR", str(tmp_path / "JARVIS"))
    mod = load_plugin("jarvis-brain")
    mod._brain = None
    mod._vault = None
    mod._cfg["vault_scan_minutes"] = 0
    yield mod
    mod._vault = None
    if mod._brain:
        mod._brain.close()
        mod._brain = None


def _j(s):
    return json.loads(s)


def _seed(root):
    (root / "inbox").mkdir(parents=True, exist_ok=True)
    (root / "inbox" / "договор Acme.md").write_text(
        "# Договор с Acme\n\nСрок действия до 31 декабря 2026. Оплата 5000 CHF ежемесячно.\n"
        "Контактное лицо — Анна Мюллер.\n", encoding="utf-8")
    (root / "notes.txt").write_text("Идеи на отпуск: Лиссабон в октябре, Порту, серфинг в Эрисейре.\n", encoding="utf-8")
    (root / ".env").write_text("OPENAI_API_KEY=sk-secretsecretsecretsecret1234567890\n")
    (root / "id_rsa").write_text("-----BEGIN OPENSSH PRIVATE KEY-----\nxxx\n-----END OPENSSH PRIVATE KEY-----\n")
    (root / "photo.jpg").write_bytes(b"\xff\xd8\xff\x00" * 100)
    (root / "node_modules").mkdir()
    (root / "node_modules" / "index.js").write_text("module.exports = 'должно быть пропущено';")


def test_layout_and_index(brain, tmp_path):
    v = brain.vault()
    v.ensure_layout()
    root = tmp_path / "JARVIS"
    assert (root / "README.md").exists() and (root / "inbox").is_dir() and (root / "projects").is_dir()
    _seed(root)
    st = v.reindex()
    assert st["indexed"] >= 3  # README, договор, notes
    files = {f["rel"] for f in v.list_files()}
    assert "inbox/договор Acme.md" in files and "notes.txt" in files
    assert ".env" not in files and "id_rsa" not in files and "photo.jpg" not in files
    assert not any("node_modules" in f for f in files)


def test_search_by_content_not_name(brain, tmp_path):
    v = brain.vault()
    v.ensure_layout()
    _seed(tmp_path / "JARVIS")
    v.reindex()
    hits = v.search("оплата ежемесячно CHF")
    assert hits and hits[0]["rel"] == "inbox/договор Acme.md" and "5000" in hits[0]["snippet"]
    hits = v.search("серфинг отпуск")
    assert hits and hits[0]["rel"] == "notes.txt"
    assert v.search("") == []
    # ограничение папкой
    assert all(h["rel"].startswith("inbox/") for h in v.search("Acme", prefix="inbox"))
    assert v.search("серфинг", prefix="inbox") == []


def test_tool_search_and_read(brain, tmp_path):
    v = brain.vault()
    v.ensure_layout()
    _seed(tmp_path / "JARVIS")
    v.reindex()
    res = _j(brain.tool_vault_search({"query": "контактное лицо Acme"}))
    assert res["success"] and res["results"][0]["name"] == "договор Acme.md"
    path = res["results"][0]["path"]
    rd = _j(brain.tool_vault_read({"path": path}))
    assert rd["success"] and "Анна Мюллер" in rd["content"] and rd["next_offset"] is None
    # относительный путь и постраничное чтение
    rd = _j(brain.tool_vault_read({"path": "notes.txt", "limit": 10}))
    assert rd["success"] and rd["chars"] == 10 and rd["next_offset"] == 10
    rd = _j(brain.tool_vault_read({"path": "nope.txt"}))
    assert not rd["success"]
    # папка → дерево
    rd = _j(brain.tool_vault_read({"path": "inbox"}))
    assert rd["success"] and rd["dir"] and any("договор" in e for e in rd["entries"])


def test_empty_vault_hint(brain):
    res = _j(brain.tool_vault_search({"query": "что угодно"}))
    assert res["success"] and res["results"] == [] and "хранилище пустое" in res["hint"]


def test_add_project_symlink_and_remove(brain, tmp_path):
    proj = tmp_path / "Projects" / "shop"
    (proj / "src").mkdir(parents=True)
    (proj / "src" / "auth.py").write_text("def login(user, password):\n    # проверка пароля через bcrypt\n    return True\n")
    (proj / ".git").mkdir()
    (proj / ".git" / "config").write_text("[core]")
    (proj / "README.md").write_text("# Shop\nИнтернет-магазин на FastAPI.")
    res = _j(brain.tool_vault_manage({"action": "add", "path": str(proj)}))
    assert res["success"] and res["name"] == "shop" and res["indexed"] >= 2
    link = tmp_path / "JARVIS" / "projects" / "shop"
    assert link.is_symlink() and link.resolve() == proj.resolve()
    hits = brain.vault().search("bcrypt проверка пароля")
    assert hits and hits[0]["rel"] == "projects/shop/src/auth.py" and hits[0]["source"] == "project:shop"
    assert not any(".git" in f["rel"] for f in brain.vault().list_files())
    st = _j(brain.tool_vault_manage({"action": "status"}))
    assert st["sources"][0]["name"] == "shop"
    # повторное добавление той же папки — не ошибка
    assert _j(brain.tool_vault_manage({"action": "add", "path": str(proj)}))["success"]
    # другая папка под тем же именем — отказ
    other = tmp_path / "other" / "shop"
    other.mkdir(parents=True)
    assert not _j(brain.tool_vault_manage({"action": "add", "path": str(other)}))["success"]
    res = _j(brain.tool_vault_manage({"action": "remove", "name": "shop"}))
    assert res["success"] and res["removed"] and not link.exists()
    assert brain.vault().search("bcrypt") == []
    assert proj.exists()  # оригинал не тронут


def test_reindex_tracks_changes_and_deletions(brain, tmp_path):
    v = brain.vault()
    v.ensure_layout()
    root = tmp_path / "JARVIS"
    f = root / "todo.md"
    f.write_text("купить молоко")
    v.reindex()
    assert v.search("молоко")
    f.write_text("купить хлеб")
    os.utime(f, (f.stat().st_atime, f.stat().st_mtime + 5))
    st = v.reindex()
    assert st["indexed"] == 1 and v.search("хлеб") and not v.search("молоко")
    f.unlink()
    st = v.reindex()
    assert st["removed"] == 1 and not v.search("хлеб")


def test_secrets_redacted_inside_text(brain, tmp_path):
    v = brain.vault()
    v.ensure_layout()
    (tmp_path / "JARVIS" / "setup.md").write_text("Деплой: export TOKEN=ghp_abcdefghijklmnopqrstuvwxyz0123456789 и запустить make deploy")
    v.reindex()
    hits = v.search("деплой make")
    assert hits and "ghp_abc" not in hits[0]["snippet"]
    row = brain.brain()._conn.execute("SELECT content FROM file_chunks WHERE name='setup.md'").fetchone()
    assert "ghp_abc" not in row["content"] and "[скрыто]" in row["content"]


def test_office_zip_extraction(brain, tmp_path):
    v = brain.vault()
    v.ensure_layout()
    p = tmp_path / "JARVIS" / "deck.pptx"
    with zipfile.ZipFile(p, "w") as z:
        z.writestr("ppt/slides/slide1.xml", '<p:sld><a:t>Квартальные итоги продаж</a:t><a:t>Рост 12 процентов</a:t></p:sld>')
    v.reindex()
    hits = v.search("квартальные итоги")
    assert hits and hits[0]["name"] == "deck.pptx"


def test_context_injection_includes_vault(brain, tmp_path):
    v = brain.vault()
    v.ensure_layout()
    (tmp_path / "JARVIS" / "trip.md").write_text("Бронь отеля в Лиссабоне: Hotel Avenida, 12–19 октября, номер брони 88213.")
    v.reindex()
    ctx = FakeCtx()
    brain.register(ctx)
    out = ctx.hooks["pre_llm_call"][0](session_id="s1", user_message="какой у меня номер брони отеля в Лиссабоне?")
    assert out and "[JARVIS vault]" in out["context"] and "trip.md" in out["context"]
    # короткие реплики («привет») файлы не тянут
    out = ctx.hooks["pre_llm_call"][0](session_id="s2", user_message="привет")
    assert not out or "[JARVIS vault]" not in (out.get("context") or "")


def test_chunking_keeps_lines_and_overlap(brain):
    from plug_jarvis_brain.vault import chunk_text
    text = "\n".join(f"строка {i} " + "x" * 50 for i in range(100))
    chunks = chunk_text(text, size=600, overlap=100)
    assert len(chunks) > 3
    assert chunks[0][0] == 1 and all(c[1] for c in chunks)
    # перекрытие: конец одного куска встречается в начале следующего
    assert chunks[0][1].splitlines()[-1] in chunks[1][1]


def test_cli_entrypoint(brain, tmp_path, monkeypatch):
    import subprocess
    import sys
    from pathlib import Path
    root = tmp_path / "JARVIS"
    v = brain.vault()
    v.ensure_layout()
    (root / "a.md").write_text("Пароль от wifi дома: спросить у соседа")  # это не секрет по паттернам — просто текст
    env = {**os.environ, "JARVIS_VAULT_DIR": str(root), "JARVIS_BRAIN_DIR": str(tmp_path / "db")}
    script = Path(__file__).resolve().parents[1] / "plugins" / "jarvis-brain" / "vault.py"
    brain.brain().close(); brain._brain = None; brain._vault = None
    r = subprocess.run([sys.executable, str(script), "reindex"], capture_output=True, text=True, env=env)
    assert r.returncode == 0 and "проиндексировано" in r.stdout
    r = subprocess.run([sys.executable, str(script), "search", "сосед", "--json"], capture_output=True, text=True, env=env)
    assert r.returncode == 0 and json.loads(r.stdout)[0]["rel"] == "a.md"
    r = subprocess.run([sys.executable, str(script), "status"], capture_output=True, text=True, env=env)
    assert r.returncode == 0 and "Файлов в индексе: 2" in r.stdout


def _prep(brain, tmp_path):
    v = brain.vault(); v.ensure_layout(); root = tmp_path / "JARVIS"; _seed(root); v.reindex()
    return v, root


def test_write_move_trash_inside_only(brain, tmp_path, monkeypatch):
    v, root = _prep(brain, tmp_path)
    # write создаёт файл и сразу индексирует
    r = v.write("inbox/заметка.md", "Совещание по проекту Atlas перенесено на пятницу")
    assert r["created"] and r["indexed"] and (root / "inbox" / "заметка.md").exists()
    assert any("заметка.md" in h["rel"] for h in v.search("совещание Atlas пятницу"))
    # append
    v.write("inbox/заметка.md", "Дополнение: пригласить Анну", mode="append")
    assert "Дополнение" in (root / "inbox" / "заметка.md").read_text()
    # move в новую папку + индекс переезжает
    v.mkdir("inbox/встречи")
    m = v.move("inbox/заметка.md", "inbox/встречи")
    assert m["rel"] == "inbox/встречи/заметка.md" and not (root / "inbox" / "заметка.md").exists()
    hits = v.search("совещание Atlas пятницу")
    assert hits and all("встречи/заметка.md" in h["rel"] for h in hits)
    # вне хранилища — нельзя
    outside = tmp_path / "outside.txt"
    with pytest.raises(PermissionError):
        v.write(str(outside), "x")
    with pytest.raises(PermissionError):
        v.move("inbox/встречи/заметка.md", str(tmp_path / "elsewhere.md"))
    # секреты не пишем
    with pytest.raises(PermissionError):
        v.write("inbox/.env", "TOKEN=1")
    # trash → в .trash (не macOS), индекс чистится
    t = v.trash("inbox/встречи/заметка.md")
    assert t["trashed"] and not (root / "inbox" / "встречи" / "заметка.md").exists()
    assert v.search("совещание Atlas пятницу") == []
    with pytest.raises(PermissionError):
        v.trash("inbox")


def test_pending_summaries_and_mark(brain, tmp_path):
    v, root = _prep(brain, tmp_path)
    (root / "inbox" / "договор.md").write_text("Договор с Acme до 31.12.2026, сумма 12 000 CHF")
    v.reindex()
    pend = v.pending_summaries()
    names = [p["name"] for p in pend]
    assert "договор.md" in names and "README.md" not in names
    fid = next(p["id"] for p in pend if p["name"] == "договор.md")
    note = brain.brain().remember("inbox/договор.md: договор с Acme до конца 2026, 12 000 CHF", kind="document", tags="vault")
    v.mark_summarized(fid, note["id"])
    assert fid not in [p["id"] for p in v.pending_summaries()]
    # инструмент: pending / write / move / trash через tool_vault_manage
    out = json.loads(brain.tool_vault_manage({"action": "write", "path": "inbox/план.md", "content": "1. купить кофе"}))
    assert out["success"] and out["created"]
    out = json.loads(brain.tool_vault_manage({"action": "move", "path": "inbox/план.md", "to": "inbox/личное/план.md"}))
    assert out["success"] and out["rel"] == "inbox/личное/план.md"
    out = json.loads(brain.tool_vault_manage({"action": "write", "path": "/etc/evil", "content": "x"}))
    assert not out["success"] and "вне хранилища" in out["error"]


def test_connect_obsidian_lookup(brain, tmp_path, monkeypatch):
    v, root = _prep(brain, tmp_path)
    obs = tmp_path / "ObsVault"; obs.mkdir(); (obs / "note.md").write_text("Идея: сделать JARVIS ещё умнее")
    monkeypatch.setattr(v, "_find_obsidian", staticmethod(lambda: str(obs)))
    res = v.connect("notes-obsidian")
    assert res["name"] == "Obsidian" and (root / "projects" / "Obsidian").is_symlink()
    v.reindex()
    assert any("note.md" in h["rel"] for h in v.search("сделать JARVIS умнее"))
    with pytest.raises(ValueError):
        v.connect("dropbox")
