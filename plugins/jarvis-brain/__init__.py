"""
jarvis-brain — «живая» база знаний JARVIS.

Идея: у Hermes есть встроенная память (MEMORY.md/USER.md — короткие текстовые блоки в промпте).
Она хороша для 20–40 строк, но не масштабируется и не структурируется. jarvis-brain добавляет
полноценное хранилище (SQLite + FTS5), которое агент:

  * САМ пополняет   — инструмент brain_remember + автозахват фраз «запомни …» (страховка);
  * САМ использует  — хук pre_llm_call подмешивает 3–5 релевантных знаний к каждому ходу
                      (поиск по тексту сообщения), инструмент brain_recall — для целевого поиска;
  * САМ правит      — brain_forget (архив/исправление), brain_entity (карточки и связи);
  * САМ пересматривает ночью — cron «JARVIS: ночная ревизия базы знаний» (03:30) вызывает
                      brain_review: авто-уборка → план кандидатов → модель принимает решения →
                      apply → дневник дня из журнала ходов → отчёт. Всё через changelog, с бэкапом.

Файлы: db.py (хранилище), schemas.py (схемы инструментов), skills/brain-nightly-review/SKILL.md (процедура ревизии).
"""

from __future__ import annotations

import json
import logging
import os
import re
import threading
import urllib.request
from pathlib import Path

from . import schemas
from .db import Brain, BrainError
from .vault import Vault, VaultWatcher

logger = logging.getLogger(__name__)

TOOLSET = "jarvis_brain"
_SKILLS_DIR = Path(__file__).parent / "skills"

_cfg = {
    "inject_context": True,
    "context_limit": 5,
    "min_score": 1.2,
    "log_turns": True,
    "auto_capture": True,
    "hud_url": "http://127.0.0.1:8765",
    "export_path": "",              # пусто → $HERMES_HOME/jarvis/BRAIN.md
    "backup_mirror": "",            # напр. "~/Library/Mobile Documents/com~apple~CloudDocs/JARVIS" → iCloud
    "log_failures": True,           # журнал сбоев инструментов → ночная самодиагностика
    "stt_vocabulary": True,         # имена из базы → подсказка распознаванию речи
    "voice_polish": True,           # для голосовых платформ убирать markdown из финального ответа
    "vault_dir": "",                # хранилище файлов/проектов (пусто → ~/JARVIS)
    "vault_context": True,          # фрагменты файлов из хранилища в контекст хода
    "vault_scan_minutes": 10,       # фоновая переиндексация; 0 — выкл
}
_VOICE_PLATFORMS = {"voice", "voice_mode", "cli_voice", "discord_voice", "phone"}

_brain: Brain | None = None
_vault: Vault | None = None
_vault_watcher: VaultWatcher | None = None
_turn_tools: dict[str, set] = {}  # session_id → инструменты, вызванные в текущем ходе
_CAPTURE_RE = re.compile(
    r"^\s*(?:jarvis[,!]?\s*|джарвис[,!]?\s*)?(?:запомни|запиши|remember|note that|заметь)[,:\s]+(.+)$",
    re.IGNORECASE | re.DOTALL,
)


_FIRST_PERSON = [
    (re.compile(r"^(что\s+)?я\s+", re.I), "Пользователь "),
    (re.compile(r"^(что\s+)?у\s+меня\s+", re.I), "У пользователя "),
    (re.compile(r"^(что\s+)?мо(й|я|ё|и|ю|ей|им|их|его|ем|ими)\s+", re.I), "Пользователя: "),
    (re.compile(r"^(that\s+)?i\s+", re.I), "User "),
    (re.compile(r"^(that\s+)?my\s+", re.I), "User's "),
]


def _third_person(text: str) -> str:
    """Грубая нормализация «я живу в Берне» → «Пользователь живу в Берне» (ночью модель перефразирует точно)."""
    for pat, rep in _FIRST_PERSON:
        if pat.search(text):
            return pat.sub(rep, text, count=1)
    return text[:1].upper() + text[1:] if text else text


def brain() -> Brain:
    global _brain
    if _brain is None:
        _brain = Brain()
    return _brain


def vault() -> Vault:
    global _vault
    if _vault is None:
        _vault = Vault(brain(), _cfg.get("vault_dir") or None)
    return _vault


def _ok(**data) -> str:
    return json.dumps({"success": True, **data}, ensure_ascii=False, default=str)


def _err(msg: str, **data) -> str:
    return json.dumps({"success": False, "error": msg, **data}, ensure_ascii=False)


def _hud(event: str, data: dict) -> None:
    """Best-effort событие на HUD (без зависимости от jarvis-core)."""

    def _send():
        try:
            body = json.dumps({"event": event, "data": data}, ensure_ascii=False).encode()
            req = urllib.request.Request(_cfg["hud_url"].rstrip("/") + "/api/event", data=body,
                                         headers={"Content-Type": "application/json"}, method="POST")
            urllib.request.urlopen(req, timeout=1).close()
        except Exception:
            pass

    threading.Thread(target=_send, daemon=True).start()


# ══════════════════════════════ хуки ═══════════════════════════════════════

_last_injected: dict[str, list[int]] = {}   # session_id → id заметок, подсказанных в прошлом ходе (для обратной связи)
_FEEDBACK_NEG = re.compile(
    r"^\s*(?:нет[,.!]?\s*)?(?:это\s+)?(?:не\s*(?:так|верно|правда|актуально)|неверно|неправда|ошиб|устарел|уже\s+не|больше\s+не|"
    r"откуда\s+ты\s+взял|я\s+такого\s+не\s+говорил|wrong|incorrect|not\s+true|outdated)", re.I)
_FEEDBACK_POS = re.compile(r"^\s*(?:да[,.!]?\s*)?(?:всё\s+)?(?:верно|точно|правильно|так\s+и\s+есть|именно|correct|exactly|right)\b[.!]?\s*$", re.I)


def apply_feedback(session_id: str, user_message: str) -> list[dict]:
    """Если в прошлом ходе подсказали заметки, а пользователь ответил «это не так» / «верно» — двигаем уверенность."""
    ids = _last_injected.get(session_id) or []
    if not ids or not (user_message or "").strip():
        return []
    text = user_message.strip()
    if _FEEDBACK_NEG.search(text):
        res = brain().feedback(ids, -0.3)
        for r in res:
            _hud("brain.update", {"action": "doubt", "id": r["id"], "content": f"уверенность ↓ {r['confidence']}: {r['content'][:90]}"})
        return res
    if _FEEDBACK_POS.match(text):
        return brain().feedback(ids, +0.1)
    return []


def build_memory_context(user_message: str, is_first_turn: bool = False, session_id: str = "") -> str:
    """Короткий блок релевантных знаний для модели. Пусто — если нечего сказать."""
    parts: list[str] = []
    try:
        b = brain()  # открытие базы может упасть (нет прав на каталог, диск полон) — ход агента от этого страдать не должен
        if is_first_turn:
            eps = b.episodes(1)
            if eps:
                parts.append(f"Вчера/последний день ({eps[0]['day']}): {eps[0]['summary'][:300]}")
        hits = b.recall(user_message, limit=_cfg["context_limit"], touch=True) if (user_message or "").strip() else []
        hits = [h for h in hits if h["score"] >= _cfg["min_score"]]
        if session_id:
            _last_injected[session_id] = [h["id"] for h in hits]
            if len(_last_injected) > 200:
                for old in list(_last_injected)[:100]:
                    _last_injected.pop(old, None)
        for h in hits:
            ent = f" ({h['entity']})" if h.get("entity") else ""
            parts.append(f"- #{h['id']} [{h['kind']}]{ent} {h['content']}")
    except Exception as e:
        logger.debug("brain context failed: %s", e)
    files: list[str] = []
    if _cfg.get("vault_context", True) and len((user_message or "").split()) >= 3:
        try:
            for h in vault().search(user_message, limit=3):
                if h["score"] >= 1.0 or h["score"] == 0.0:
                    files.append(f"- {h['rel']}:{h['line']} — {h['snippet'][:200]}")
        except Exception as e:
            logger.debug("vault context failed: %s", e)
    if not parts and not files:
        return ""
    out = ""
    if parts:
        out += "[JARVIS memory] Что уже известно по теме (используй, не переспрашивай; поправь через brain_forget, если устарело):\n" + "\n".join(parts)
    if files:
        out += ("\n" if out else "") + "[JARVIS vault] Похожие места в файлах пользователя (полный текст — vault_read/read_file по пути):\n" + "\n".join(files)
    return out


def hook_pre_llm_call(session_id: str = "", user_message: str = "", is_first_turn: bool = False, **kwargs):
    if len(_turn_tools) > 200:  # сессии, для которых post_llm_call не пришёл (ошибка модели) — не копим бесконечно
        for old in list(_turn_tools)[:100]:
            _turn_tools.pop(old, None)
    _turn_tools[session_id] = set()
    if not _cfg.get("inject_context", True):
        return None
    try:
        fb = apply_feedback(session_id, user_message)  # реакция на подсказки прошлого хода — до нового поиска
    except Exception as e:
        logger.debug("feedback: %s", e)
        fb = []
    ctx = build_memory_context(user_message, is_first_turn, session_id=session_id)
    if fb and any(r["confidence"] < 0.6 for r in fb):
        ctx = (ctx + "\n" if ctx else "") + ("[JARVIS memory] Пользователь опроверг подсказку из памяти — уточни, что верно, "
                                            "и исправь заметку (brain_forget + brain_remember или brain_remember с supersede).")
    return {"context": ctx} if ctx else None


def hook_pre_tool_call(tool_name: str = "", args=None, task_id: str = "", session_id: str = "", **kwargs):
    # Hermes не всегда передаёт session_id в pre_tool_call — тогда помечаем все активные ходы
    targets = [session_id] if session_id in _turn_tools else list(_turn_tools)
    for s in targets:
        _turn_tools[s].add(tool_name)
    return None


def hook_post_tool_call(tool_name: str = "", args=None, result=None, status: str = "", error_type: str = "",
                        error_message: str = "", **kwargs):
    """Каждый сбой инструмента — в журнал сбоев (ночью JARVIS смотрит, что ломалось, и делает выводы)."""
    if not _cfg.get("log_failures", True):
        return
    try:
        failed, msg = False, ""
        if status and status not in ("ok", "success", "completed"):
            failed, msg = True, error_message or status
        elif isinstance(result, str) and result.lstrip().startswith("{"):
            data = json.loads(result)
            if data.get("success") is False or data.get("error"):
                failed, msg = True, str(data.get("error") or "")
        if failed and msg and "требует явного подтверждения" not in msg:
            brain().log_failure(tool_name, error_type or "tool_error", msg, json.dumps(args or {}, ensure_ascii=False)[:300])
    except Exception as e:
        logger.debug("brain failure log: %s", e)


def hook_pre_transcription(provider: str = "", prompt=None, source=None, **kwargs):
    """Имена людей/проектов из базы → initial_prompt Whisper: «Атлас», «Анна», «Acme» перестают распознаваться как шум."""
    if not _cfg.get("stt_vocabulary", True):
        return None
    try:
        vocab = brain().vocabulary(40)
        if not vocab:
            return None
        hint = ", ".join(vocab)
        return {"prompt": f"{prompt}. {hint}" if prompt else f"JARVIS. {hint}"}
    except Exception:
        return None


_MD_PATTERNS = [
    (re.compile(r"```[\s\S]*?```"), " (код опущен) "),
    (re.compile(r"`([^`]+)`"), r"\1"),
    (re.compile(r"^\s{0,3}#{1,6}\s*", re.M), ""),
    (re.compile(r"\*\*([^*]+)\*\*"), r"\1"),
    (re.compile(r"(?<!\w)[*_]([^*_]+)[*_](?!\w)"), r"\1"),
    (re.compile(r"^\s*[-*•]\s+", re.M), ""),
    (re.compile(r"^\s*\d+[.)]\s+", re.M), ""),
    (re.compile(r"\[([^\]]+)\]\([^)]+\)"), r"\1"),
    (re.compile(r"^\|.*\|\s*$", re.M), ""),
    (re.compile(r"https?://\S+"), "ссылка"),
]


def polish_for_voice(text: str, max_sentences: int = 4) -> str:
    """Убрать markdown и ужать до нескольких предложений — то, что реально приятно слушать."""
    out = text or ""
    for pat, rep in _MD_PATTERNS:
        out = pat.sub(rep, out)
    out = re.sub(r"[ \t]+", " ", out)
    out = re.sub(r"\n{2,}", "\n", out).strip()
    sentences = re.split(r"(?<=[.!?…])\s+", out.replace("\n", " "))
    sentences = [s.strip() for s in sentences if s.strip()]
    if len(sentences) > max_sentences:
        out = " ".join(sentences[:max_sentences]) + " Подробности — в текстовом ответе."
    return out


def hook_transform_llm_output(response_text: str = "", session_id: str = "", model: str = "", platform: str = "", **kwargs):
    if not _cfg.get("voice_polish", True) or (platform or "").lower() not in _VOICE_PLATFORMS:
        return None
    polished = polish_for_voice(response_text)
    return polished if polished and polished != response_text else None


def hook_post_llm_call(session_id: str = "", user_message: str = "", assistant_response: str = "",
                       platform: str = "", **kwargs):
    try:
        b = brain()
        if _cfg.get("log_turns", True) and (user_message or assistant_response):
            b.log_turn(session_id, platform, user_message, assistant_response)
        # страховка: пользователь сказал «запомни …», а модель не вызвала brain_remember
        if _cfg.get("auto_capture", True):
            m = _CAPTURE_RE.match(user_message or "")
            if m and "brain_remember" not in _turn_tools.get(session_id, set()):
                text = _third_person(m.group(1).strip().rstrip(".!"))
                if 3 <= len(text) <= 500:
                    res = b.remember(text, kind="fact", source="auto-capture", confidence=0.7, importance=3, tags="auto")
                    _hud("brain.update", {"action": res["action"], "id": res["id"], "content": text[:120]})
    except Exception as e:
        logger.debug("brain post_llm failed: %s", e)
    finally:
        _turn_tools.pop(session_id, None)


# ══════════════════════════════ инструменты ════════════════════════════════

def tool_brain_remember(args: dict, **kwargs) -> str:
    try:
        res = brain().remember(
            content=args.get("content", ""), kind=args.get("kind") or "fact", entity=args.get("entity"),
            tags=args.get("tags", ""), importance=args.get("importance", 3), confidence=args.get("confidence", 0.8),
            source="agent", valid_until=args.get("valid_until"),
        )
        _hud("brain.update", {"action": res["action"], "id": res["id"], "content": res["note"]["content"][:120]})
        msg = "Записано" if res["action"] == "created" else f"Обновлена похожая заметка (сходство {res['similarity']})"
        extra = {}
        if res.get("possible_conflicts"):
            extra["possible_conflicts"] = res["possible_conflicts"]
            extra["hint"] = "Есть похожие заметки — если новая их заменяет, вызови brain_forget для старых (id выше)."
        if res.get("previous"):
            extra["previous"] = res["previous"]
        if res.get("redacted"):
            extra["warning"] = "В тексте было похожее на секрет — скрыто. Секреты в базу не пишем."
        return _ok(action=res["action"], id=res["id"], message=msg, note=res["note"]["content"], **extra)
    except BrainError as e:
        return _err(str(e))


def tool_brain_recall(args: dict, **kwargs) -> str:
    try:
        scope = args.get("scope") or "notes"
        q = args.get("query", "")
        limit = int(args.get("limit") or 6)
        out: dict = {}
        if scope in ("notes", "all"):
            out["results"] = brain().recall(q, limit=limit, kinds=args.get("kinds"), entity=args.get("entity"))
        if scope in ("episodes", "all"):
            out["episodes"] = brain().search_episodes(q, limit=limit)
        found = bool(out.get("results") or out.get("episodes"))
        return _ok(count=len(out.get("results", [])) + len(out.get("episodes", [])), **out,
                   hint="" if found else "Ничего не найдено — можно спросить пользователя и затем brain_remember")
    except BrainError as e:
        return _err(str(e))


def tool_brain_forget(args: dict, **kwargs) -> str:
    b = brain()
    try:
        note_id = args.get("id")
        if not note_id and args.get("query"):
            hits = b.recall(args["query"], limit=2, touch=False)
            if not hits:
                return _err("Заметка не найдена")
            if len(hits) > 1 and hits[1]["score"] > hits[0]["score"] * 0.85:
                return _err("Несколько похожих заметок — уточните id", candidates=hits)
            note_id = hits[0]["id"]
        if not note_id:
            return _err("Нужен id или query")
        if args.get("new_content"):
            note = b.update_note(int(note_id), actor="agent", content=args["new_content"], confidence=0.9)
            _hud("brain.update", {"action": "corrected", "id": note["id"], "content": note["content"][:120]})
            return _ok(action="corrected", id=note["id"], note=note["content"])
        res = b.forget(int(note_id), args.get("reason", ""), actor="agent")
        _hud("brain.update", {"action": "archived", "id": res["id"]})
        return _ok(action="archived", **res)
    except BrainError as e:
        return _err(str(e))


def tool_brain_entity(args: dict, **kwargs) -> str:
    b = brain()
    a = args.get("action")
    try:
        if a == "get":
            ent = b.entity_get(args.get("name", ""))
            return _ok(entity=ent) if ent else _err(f"Карточка «{args.get('name')}» не найдена")
        if a == "upsert":
            ent = b.entity_upsert(args.get("name", ""), args.get("kind") or "topic", args.get("summary", ""), args.get("tags", ""))
            return _ok(entity=ent)
        if a == "list":
            return _ok(entities=b.entity_list(args.get("kind"), int(args.get("limit") or 30)))
        if a == "relate":
            return _ok(relation=b.entity_link(args.get("src", ""), args.get("dst", ""), args.get("type", "related_to")))
        return _err(f"Неизвестное действие: {a}")
    except BrainError as e:
        return _err(str(e))


def tool_brain_review(args: dict, **kwargs) -> str:
    b = brain()
    a = args.get("action")
    try:
        if a == "stats":
            return _ok(stats=b.stats())
        if a == "maintain":
            rep = b.auto_maintenance(mirror_dir=_cfg.get("backup_mirror") or None)
            _hud("brain.review", {"stage": "maintain", "report": rep})
            return _ok(report=rep)
        if a == "plan":
            return _ok(plan=b.review_plan())
        if a == "apply":
            res = b.apply_ops(args.get("ops") or [])
            _hud("brain.review", {"stage": "apply", "applied": res["applied"], "errors": len(res["errors"])})
            return _ok(**res)
        if a == "digest_queue":
            return _ok(days=b.digest_queue())
        if a == "save_episode":
            if not args.get("day") or not args.get("summary"):
                return _err("Нужны day и summary")
            return _ok(**b.save_episode(args["day"], args["summary"], args.get("highlights", "")))
        if a == "finish":
            res = b.finish_review(args.get("report", ""))
            _hud("brain.review", {"stage": "finish", "report": (args.get("report") or "")[:300]})
            return _ok(**res)
        if a == "export":
            default_export = Path(os.environ.get("HERMES_HOME") or "~/.hermes").expanduser() / "jarvis" / "BRAIN.md"
            path = Path(args.get("path") or _cfg["export_path"] or default_export).expanduser()
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(b.export_markdown(), encoding="utf-8")
            profile = path.with_name("PROFILE.md")
            profile.write_text(b.export_profile(), encoding="utf-8")
            return _ok(path=str(path), profile=str(profile))
        if a == "episodes":
            return _ok(episodes=b.search_episodes(args.get("query") or "", int(args.get("limit") or 7)))
        if a == "profile":
            return _ok(profile=b.export_profile())
        if a == "failures":
            return _ok(failures=b.failures(int(args.get("limit") or 20)))
        if a == "verify":
            items = b.low_confidence_to_verify()
            for it in items:  # не спрашивать дважды
                b.update_note(it["id"], actor="system", tags=(b.get_note(it["id"])["tags"] + ",verify_asked").strip(","))
            return _ok(to_verify=items, hint="Спроси пользователя одной фразой и обнови confidence через brain_forget(new_content) или update")
        if a == "restore":
            src = b.restore_backup(args.get("path"))
            _hud("brain.review", {"stage": "restore", "report": src})
            return _ok(restored_from=src)
        if a == "changelog":
            return _ok(changes=b.changelog(int(args.get("limit") or 30)))
        return _err(f"Неизвестное действие: {a}")
    except BrainError as e:
        return _err(str(e))


def tool_brain_reflect(args: dict, **kwargs) -> str:
    try:
        mat = brain().reflect_material(args.get("question", ""), int(args.get("limit") or 12))
        empty = not (mat["notes"] or mat["entities"] or mat["episodes"] or mat["history"])
        return _ok(**mat, hint="База ничего не знает по этому вопросу" if empty else
                   "Синтезируй связный ответ: текущее состояние, что изменилось, что важно помнить.")
    except BrainError as e:
        return _err(str(e))


def tool_brain_history(args: dict, **kwargs) -> str:
    b = brain()
    try:
        if args.get("action") == "supersede":
            if not args.get("id") or not args.get("new_content"):
                return _err("Нужны id и new_content")
            res = b.supersede(int(args["id"]), args["new_content"])
            _hud("brain.update", {"action": "corrected", "id": res["new_id"], "content": res["note"]["content"][:120]})
            return _ok(**res, message="Старая версия закрыта датой, новая записана")
        return _ok(history=b.history(args.get("query") or "", args.get("entity"), int(args.get("limit") or 10)))
    except BrainError as e:
        return _err(str(e))


NIGHTLY_PROMPT = (
    "Проведи ночную ревизию базы знаний по навыку brain-nightly-review: "
    "1) brain_review maintain; 2) brain_review digest_queue → для каждого дня save_episode с резюме 2–4 предложения и "
    "highlights (важные решения/факты) — и сохрани новые устойчивые факты через brain_remember; 3) brain_review plan → "
    "прими решения по дубликатам, конфликтам, устаревшему, некатегоризированному; при необходимости уточни таксономию "
    "(kind_define/kind_rename) и карточки (entity_merge/entity_update/relate); перефразируй auto_captured_to_rephrase в третье лицо; "
    "примени через brain_review apply; 4) рефлексия: из reflection_material выведи 0–2 наблюдения-паттерна (kind=insight, "
    "confidence 0.6) — только если они действительно новые; 5) brain_review export; 6) brain_review finish с отчётом в 3–5 предложений. Не трогай заметки с importance 5 без "
    "явной причины. Если база пуста — просто finish с отчётом «нечего пересматривать»."
)


# ══════════════════════════════ Vault: файлы и проекты ═════════════════════

def tool_vault_search(args: dict, **kwargs) -> str:
    try:
        hits = vault().search(args.get("query", ""), limit=int(args.get("limit") or 8), prefix=args.get("in"))
        if not hits:
            st = vault().stats()
            hint = ("хранилище пустое — положите файлы в " + st["root"] + " или подключите проект: vault_manage add"
                    if not st["files"] else "попробуйте другие слова или vault_manage list")
            return _ok(results=[], hint=hint)
        return _ok(results=hits, hint="Читай нужный файл: vault_read(path) или read_file(path); правь обычными инструментами.")
    except Exception as e:
        return _err(f"vault: {e}")


def tool_vault_read(args: dict, **kwargs) -> str:
    try:
        res = vault().read(args.get("path", ""), offset=int(args.get("offset") or 0), limit=int(args.get("limit") or 6000))
        return _err(res["error"], path=res.get("path")) if res.get("error") else _ok(**res)
    except Exception as e:
        return _err(f"vault: {e}")


def tool_vault_manage(args: dict, **kwargs) -> str:
    a = args.get("action") or "status"
    v = vault()
    try:
        if a == "status":
            return _ok(**v.stats())
        if a == "list":
            return _ok(files=v.list_files(args.get("prefix"), recent=bool(args.get("recent"))))
        if a == "tree":
            return _ok(root=str(v.root), tree=v.tree(Path(args["path"]).expanduser() if args.get("path") else None, depth=int(args.get("depth") or 2)))
        if a == "add":
            if not args.get("path"):
                return _err("Нужен path существующей папки")
            res = v.add_source(args["path"], args.get("name"))
            st = v.reindex()
            _hud("vault.update", {"action": "add", "name": res["name"], "indexed": st["indexed"]})
            return _ok(**res, indexed=st["indexed"])
        if a == "remove":
            if not args.get("name"):
                return _err("Нужен name (как в projects/)")
            return _ok(**v.remove_source(args["name"]))
        if a == "reindex":
            v.ensure_layout()
            st = v.reindex(force=True)
            _hud("vault.update", {"action": "reindex", **{k: st[k] for k in ("indexed", "removed")}})
            return _ok(**st)
        return _err(f"Неизвестное действие {a}")
    except (OSError, ValueError) as e:
        return _err(str(e))


# ══════════════════════════════ регистрация ════════════════════════════════

def register(ctx) -> None:
    for key in list(_cfg):
        try:
            val = ctx.get_config(key, default=None)
        except Exception:
            val = None
        if val is not None:
            _cfg[key] = val

    ctx.register_hook("pre_llm_call", hook_pre_llm_call)
    ctx.register_hook("post_llm_call", hook_post_llm_call)
    ctx.register_hook("pre_tool_call", hook_pre_tool_call)
    ctx.register_hook("post_tool_call", hook_post_tool_call)
    for name, fn in (("pre_transcription", hook_pre_transcription), ("transform_llm_output", hook_transform_llm_output)):
        try:
            ctx.register_hook(name, fn)
        except Exception as e:  # старые версии Hermes
            logger.debug("hook %s недоступен: %s", name, e)

    for schema, handler in (
        (schemas.VAULT_SEARCH, tool_vault_search),
        (schemas.VAULT_READ, tool_vault_read),
        (schemas.VAULT_MANAGE, tool_vault_manage),
        (schemas.BRAIN_REMEMBER, tool_brain_remember),
        (schemas.BRAIN_RECALL, tool_brain_recall),
        (schemas.BRAIN_FORGET, tool_brain_forget),
        (schemas.BRAIN_ENTITY, tool_brain_entity),
        (schemas.BRAIN_REVIEW, tool_brain_review),
        (schemas.BRAIN_REFLECT, tool_brain_reflect),
        (schemas.BRAIN_HISTORY, tool_brain_history),
    ):
        ctx.register_tool(name=schema["name"], toolset=TOOLSET, schema=schema, handler=handler)

    # хранилище файлов: создать ~/JARVIS и запустить фоновую индексацию
    global _vault_watcher
    try:
        v = vault()
        v.ensure_layout()
        minutes = int(_cfg.get("vault_scan_minutes") or 0)
        if minutes > 0 and _vault_watcher is None:
            _vault_watcher = VaultWatcher(v, interval_min=minutes,
                                          on_change=lambda st: _hud("vault.update", {"action": "scan", "indexed": st["indexed"], "removed": st["removed"]}))
            _vault_watcher.start()
    except Exception as e:
        logger.warning("vault недоступен: %s", e)

    if _SKILLS_DIR.exists():
        for child in sorted(_SKILLS_DIR.iterdir()):
            md = child / "SKILL.md"
            if child.is_dir() and md.exists():
                try:
                    ctx.register_skill(child.name, md)
                except Exception as e:
                    logger.debug("register_skill(%s): %s", child.name, e)

    # slash-команды
    def cmd_remember(raw: str) -> str:
        raw = raw.strip()
        if not raw:
            return "Использование: /remember <факт> [#тег …]"
        tags = " ".join(t for t in raw.split() if t.startswith("#"))
        text = " ".join(t for t in raw.split() if not t.startswith("#"))
        res = json.loads(tool_brain_remember({"content": text, "tags": tags, "confidence": 1.0}))
        return f"🧠 {res.get('message', res.get('error'))}: {res.get('note', '')}"

    def cmd_recall(raw: str) -> str:
        if not raw.strip():
            return "Использование: /recall <тема>"
        res = json.loads(tool_brain_recall({"query": raw, "limit": 8}))
        if not res.get("results"):
            return "🧠 Ничего не найдено."
        return "\n".join(f"#{r['id']} [{r['kind']}] {r['content']}" + (f"  ({r['entity']})" if r.get("entity") else "") for r in res["results"])

    def cmd_brain(raw: str) -> str:
        sub = (raw.strip().split() or ["stats"])[0].lower()
        if sub == "stats":
            s = brain().stats()
            kinds = ", ".join(f"{k['name']}:{k['notes']}" for k in s["kinds"] if k["notes"])
            last = s["last_review"]["finished_at"][:16].replace("T", " ") if s.get("last_review") else "ещё не было"
            return (f"🧠 База знаний: {s['notes_active']} заметок ({s['notes_archived']} в архиве), {s['entities']} карточек, "
                    f"{s['relations']} связей, {s['episodes']} дней в дневнике, {s['turns_pending']} ходов ждут ревизии. "
                    f"Типы: {kinds or '—'}. Последняя ревизия: {last}. Файл: {s['db_path']} ({s['db_size_kb']} КБ)")
        if sub == "export":
            return "🧠 Выгружено: " + json.loads(tool_brain_review({"action": "export"})).get("path", "?")
        if sub == "maintain":
            return "🧠 " + json.dumps(json.loads(tool_brain_review({"action": "maintain"})).get("report"), ensure_ascii=False)
        if sub == "review":
            try:
                ctx.inject_message(NIGHTLY_PROMPT, role="user")
                return ""
            except Exception:
                return NIGHTLY_PROMPT
        if sub in ("diary", "дневник"):
            eps = brain().episodes(7)
            return "\n".join(f"{e['day']}: {e['summary']}" for e in eps) or "дневник пуст"
        if sub == "profile":
            return brain().export_profile()
        if sub == "restore":
            try:
                return "🧠 Восстановлено из " + brain().restore_backup()
            except BrainError as e:
                return f"🧠 {e}"
        if sub == "failures":
            f = brain().failures(15)
            return "\n".join(f"{x['count']:>3}× {x['tool']:<18} {x['message'][:70]}" for x in f) or "сбоев нет"
        if sub == "history":
            h = brain().history(" ".join(raw.split()[1:]), limit=15)
            return "\n".join(f"{x['valid_from']} → {x['valid_until']}  [{x['status']}] {x['content']}" for x in h) or "истории нет"
        if sub == "log":
            ch = brain().changelog(15)
            return "\n".join(f"{c['ts'][5:16].replace('T', ' ')} {c['actor']:>8} {c['op']:<14} #{c['row_id'] or '-'}" for c in ch) or "пусто"
        if sub in ("entities", "cards"):
            return "\n".join(f"{e['name']} [{e['kind']}] — {e['notes']} заметок" for e in brain().entity_list(limit=30)) or "карточек нет"
        return "Использование: /brain stats | entities | diary | profile | history [тема] | failures | log | export | maintain | review | restore"

    for name, fn, desc in (
        ("remember", cmd_remember, "Запомнить факт в базу знаний: /remember кофе без сахара #preference"),
        ("recall", cmd_recall, "Поиск по базе знаний: /recall проект X"),
        ("brain", cmd_brain, "База знаний JARVIS: /brain stats | entities | diary | profile | log | export | maintain | review | restore"),
    ):
        try:
            ctx.register_command(name, fn, description=desc)
        except Exception as e:
            logger.debug("register_command(%s): %s", name, e)

    try:
        st = brain().stats()
        _hud("brain.stats", {"notes": st["notes_active"], "entities": st["entities"]})
        logger.info("jarvis-brain загружен: %s заметок, %s карточек (%s)", st["notes_active"], st["entities"], st["db_path"])
    except Exception as e:
        logger.warning("jarvis-brain: не удалось открыть базу: %s", e)
