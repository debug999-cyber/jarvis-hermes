"""
Обработчики инструментов плагина jarvis-macos.

Контракт Hermes для обработчика:
    def handler(args: dict, **kwargs) -> str      # ВСЕГДА возвращает JSON-строку
    * не бросает исключений — все ошибки превращаются в {"success": false, "error": "..."}
    * принимает **kwargs (Hermes может передавать доп. контекст)

Каждый обработчик — тонкая обёртка над функциями из mac.py.
"""

from __future__ import annotations

import datetime as dt
import json
import subprocess
import time
from pathlib import Path

from . import mac, native
from .mac import MacError, as_str, json_err, json_ok, osascript, run, which

# Настройки плагина подставляются из __init__.register() через configure()
_SETTINGS = {
    "allow_raw_applescript": False,
    "screenshot_dir": "",            # пусто → $HERMES_HOME/cache/jarvis/screenshots
    "default_player": "auto",
    "hud_url": "http://127.0.0.1:8765",
}


def configure(**settings) -> None:
    _SETTINGS.update({k: v for k, v in settings.items() if v is not None})


def guarded(fn):
    """Декоратор: проверка macOS + перевод исключений в JSON-ошибку."""

    def wrapper(args: dict | None = None, **kwargs) -> str:
        args = args or {}
        try:
            mac.require_mac()
            return fn(args)
        except MacError as e:
            return json_err(str(e))
        except Exception as e:  # обработчик не должен падать
            return json_err(f"{type(e).__name__}: {e}")

    wrapper.__name__ = fn.__name__
    wrapper.__doc__ = fn.__doc__
    return wrapper


# ═══════════════════════════════ Приложения ════════════════════════════════

@guarded
def mac_app(args: dict) -> str:
    action = args.get("action")
    app = (args.get("app") or "").strip()

    if action == "list_running":
        return json_ok(apps=mac.running_apps(), frontmost=mac.frontmost_app())
    if not app:
        return json_err("Укажите имя приложения (app)")

    if action == "open":
        run(["open", "-a", app])
        return json_ok(message=f"Открываю {app}")
    if action == "activate":
        osascript(f"tell application {as_str(app)} to activate")
        return json_ok(message=f"{app} на переднем плане")
    if action == "quit":
        osascript(f"tell application {as_str(app)} to quit")
        return json_ok(message=f"{app} закрыт")
    if action == "force_quit":
        run(["pkill", "-x", app], check=False)
        return json_ok(message=f"{app} принудительно завершён")
    if action == "hide":
        osascript(f'tell application "System Events" to set visible of process {as_str(app)} to false')
        return json_ok(message=f"{app} скрыт")
    if action == "is_running":
        running = app.lower() in {a.lower() for a in mac.running_apps()}
        return json_ok(app=app, running=running)
    return json_err(f"Неизвестное действие: {action}")


@guarded
def mac_open(args: dict) -> str:
    target = mac.resolve_target(args.get("target", ""))
    if not target:
        return json_err("Укажите target")
    cmd = ["open"]
    if args.get("app"):
        cmd += ["-a", args["app"]]
    cmd.append(target)
    run(cmd)
    return json_ok(opened=target)


@guarded
def mac_spotlight(args: dict) -> str:
    query = args.get("query", "").strip()
    if not query:
        return json_err("Пустой запрос")
    cmd = ["mdfind"]
    if args.get("only_dir"):
        cmd += ["-onlyin", str(Path(args["only_dir"]).expanduser())]
    cmd.append(query)
    out = run(cmd, timeout=20, check=False)
    limit = int(args.get("limit") or 20)
    results = mac.safe_list(out.splitlines())[:limit]
    return json_ok(query=query, count=len(results), results=results)


@guarded
def mac_finder(args: dict) -> str:
    action = args.get("action")
    if action == "reveal":
        p = str(Path(args.get("path", "~")).expanduser())
        run(["open", "-R", p])
        return json_ok(revealed=p)
    if action == "new_window":
        p = str(Path(args.get("path", "~")).expanduser())
        run(["open", p])
        return json_ok(opened=p)
    if action == "open_trash":
        run(["open", str(Path("~/.Trash").expanduser())])
        return json_ok(message="Корзина открыта")
    if action == "empty_trash":
        osascript('tell application "Finder" to empty trash')
        return json_ok(message="Корзина очищена")
    return json_err(f"Неизвестное действие: {action}")


# ═══════════════════════════════ Система ═══════════════════════════════════

@guarded
def mac_volume(args: dict) -> str:
    action = args.get("action")
    level = args.get("level")

    def current() -> int:
        return int(osascript("output volume of (get volume settings)"))

    def muted() -> bool:
        return osascript("output muted of (get volume settings)") == "true"

    if action == "get":
        return json_ok(volume=current(), muted=muted())
    if action == "set":
        if level is None:
            return json_err("Для set нужен level 0–100")
        lv = max(0, min(100, int(level)))
        osascript(f"set volume output volume {lv}")
        return json_ok(volume=lv)
    if action in ("up", "down"):
        step = int(level or 10)
        lv = current() + (step if action == "up" else -step)
        lv = max(0, min(100, lv))
        osascript(f"set volume output volume {lv}")
        return json_ok(volume=lv)
    if action == "mute":
        osascript("set volume with output muted")
        return json_ok(muted=True)
    if action == "unmute":
        osascript("set volume without output muted")
        return json_ok(muted=False)
    if action == "toggle_mute":
        m = not muted()
        osascript("set volume with output muted" if m else "set volume without output muted")
        return json_ok(muted=m)
    return json_err(f"Неизвестное действие: {action}")


@guarded
def mac_brightness(args: dict) -> str:
    action = args.get("action")
    level = args.get("level")
    bin_ = which("brightness")
    if action == "set":
        if level is None:
            return json_err("Для set нужен level 0–100")
        lv = max(0, min(100, int(level)))
        if bin_:
            run([bin_, str(lv / 100)])
            return json_ok(brightness=lv)
        # запасной путь — нажатия клавиш яркости
        steps = 16
        for _ in range(steps):
            osascript('tell application "System Events" to key code 145')  # brightness down
        for _ in range(round(lv / 100 * steps)):
            osascript('tell application "System Events" to key code 144')  # brightness up
        return json_ok(brightness=lv, note="Установлено приблизительно (для точности: brew install brightness)")
    if action in ("up", "down"):
        n = int(level or 2)
        code = 144 if action == "up" else 145
        for _ in range(n):
            osascript(f'tell application "System Events" to key code {code}')
        return json_ok(message=f"Яркость {'увеличена' if action == 'up' else 'уменьшена'} на {n} шаг(ов)")
    return json_err(f"Неизвестное действие: {action}")


@guarded
def mac_dark_mode(args: dict) -> str:
    action = args.get("action")
    base = 'tell application "System Events" to tell appearance preferences to '
    if action == "get":
        return json_ok(dark=osascript(base + "get dark mode") == "true")
    if action == "toggle":
        osascript(base + "set dark mode to not dark mode")
    elif action in ("on", "off"):
        osascript(base + f"set dark mode to {'true' if action == 'on' else 'false'}")
    else:
        return json_err(f"Неизвестное действие: {action}")
    return json_ok(dark=osascript(base + "get dark mode") == "true")


@guarded
def mac_power(args: dict) -> str:
    action = args.get("action")
    confirmed = bool(args.get("confirmed"))
    dangerous = {"restart", "shutdown", "logout"}
    if action in dangerous and not confirmed:
        return json_err(f"Действие «{action}» требует явного подтверждения пользователя. Переспросите и передайте confirmed=true.")
    if action == "lock":
        # универсальный способ блокировки для современных macOS
        osascript('tell application "System Events" to keystroke "q" using {command down, control down}')
        return json_ok(message="Экран заблокирован")
    if action == "sleep":
        run(["pmset", "sleepnow"])
        return json_ok(message="Засыпаю")
    if action == "display_sleep":
        run(["pmset", "displaysleepnow"])
        return json_ok(message="Экран выключен")
    if action == "screensaver":
        run(["open", "-a", "ScreenSaverEngine"])
        return json_ok(message="Заставка запущена")
    if action == "restart":
        osascript('tell application "System Events" to restart')
        return json_ok(message="Перезагрузка…")
    if action == "shutdown":
        osascript('tell application "System Events" to shut down')
        return json_ok(message="Выключение…")
    if action == "logout":
        osascript('tell application "System Events" to log out')
        return json_ok(message="Выход из системы…")
    return json_err(f"Неизвестное действие: {action}")


def _wifi_device() -> str:
    out = run(["networksetup", "-listallhardwareports"])
    lines = out.splitlines()
    for i, line in enumerate(lines):
        if "Wi-Fi" in line or "AirPort" in line:
            for j in range(i, min(i + 3, len(lines))):
                if lines[j].startswith("Device:"):
                    return lines[j].split(":", 1)[1].strip()
    return "en0"


@guarded
def mac_wifi(args: dict) -> str:
    action = args.get("action")
    dev = _wifi_device()
    if action in ("on", "off"):
        run(["networksetup", "-setairportpower", dev, action])
        return json_ok(wifi=action, device=dev)
    if action == "status":
        power = run(["networksetup", "-getairportpower", dev], check=False)
        ssid = run(["networksetup", "-getairportnetwork", dev], check=False)
        return json_ok(device=dev, power=power, network=ssid)
    return json_err(f"Неизвестное действие: {action}")


@guarded
def mac_bluetooth(args: dict) -> str:
    action = args.get("action")
    bu = which("blueutil")
    if not bu:
        if action == "status":
            out = run(["system_profiler", "SPBluetoothDataType"], timeout=20, check=False)
            state = "on" if "State: On" in out else "off"
            return json_ok(power=state, note="Для управления установите: brew install blueutil")
        return json_err("Нужна утилита blueutil: brew install blueutil")
    if action == "status":
        return json_ok(power="on" if run([bu, "-p"]) == "1" else "off")
    if action in ("on", "off"):
        run([bu, "-p", "1" if action == "on" else "0"])
        return json_ok(power=action)
    if action == "devices":
        return json_ok(connected=mac.safe_list(run([bu, "--connected"]).splitlines()))
    return json_err(f"Неизвестное действие: {action}")


@guarded
def mac_battery(args: dict) -> str:
    out = run(["pmset", "-g", "batt"])
    info = {"raw": out}
    for tok in out.replace(";", " ").split():
        if tok.endswith("%"):
            info["percent"] = int(tok.rstrip("%"))
    info["charging"] = "charging" in out and "discharging" not in out
    info["on_ac"] = "AC Power" in out
    if "remaining" in out:
        seg = out.split(";")[-1].strip()
        info["remaining"] = seg.split("remaining")[0].strip()
    return json_ok(**info)


@guarded
def mac_system_info(args: dict) -> str:
    section = args.get("section") or "all"
    data: dict = {}
    if section in ("all", "hardware"):
        data["model"] = run(["sysctl", "-n", "hw.model"], check=False)
        data["chip"] = run(["sysctl", "-n", "machdep.cpu.brand_string"], check=False)
        data["macos"] = run(["sw_vers", "-productVersion"], check=False)
        data["cores"] = run(["sysctl", "-n", "hw.ncpu"], check=False)
    if section in ("all", "memory"):
        total = int(run(["sysctl", "-n", "hw.memsize"], check=False) or 0)
        data["memory_total_gb"] = round(total / 1024 ** 3, 1)
        data["memory_pressure"] = run(["memory_pressure"], check=False).splitlines()[-1:] or ""
    if section in ("all", "disk"):
        data["disk"] = run(["df", "-h", "/"], check=False).splitlines()[-1:]
    if section in ("all", "network"):
        data["local_ip"] = run(["ipconfig", "getifaddr", _wifi_device()], check=False) or run(["ipconfig", "getifaddr", "en0"], check=False)
        data["hostname"] = run(["scutil", "--get", "ComputerName"], check=False)
    if section in ("all", "uptime"):
        data["uptime"] = run(["uptime"], check=False)
    if section in ("all", "processes"):
        top = run(["ps", "-Aro", "pcpu,comm"], check=False).splitlines()[1:8]
        data["top_cpu"] = [t.strip() for t in top]
    return json_ok(**data)


# ═══════════════════════════════ Медиа ═════════════════════════════════════

@guarded
def mac_media(args: dict) -> str:
    action = args.get("action")
    player = mac.detect_player(args.get("player") or _SETTINGS["default_player"])
    app = "Spotify" if player == "spotify" else "Music"
    tell = f"tell application {as_str(app)} to "

    if action in ("play", "pause", "next", "previous"):
        cmd = {"play": "play", "pause": "pause", "next": "next track", "previous": "previous track"}[action]
        osascript(tell + cmd)
    elif action == "toggle":
        osascript(tell + "playpause")
    elif action == "shuffle_on":
        osascript(tell + ("set shuffling to true" if player == "spotify" else "set shuffle enabled to true"))
    elif action == "shuffle_off":
        osascript(tell + ("set shuffling to false" if player == "spotify" else "set shuffle enabled to false"))
    elif action == "play_track":
        q = args.get("query", "")
        if player == "spotify":
            osascript(tell + f"play track (do shell script \"echo spotify:search:\" & quoted form of {as_str(q)})")
            # Более надёжно: открыть поиск и дать пользователю выбрать
            run(["open", f"spotify:search:{q}"])
        else:
            osascript(tell + f"play (first track of playlist \"Library\" whose name contains {as_str(q)})")
    elif action == "play_playlist":
        q = args.get("query", "")
        if player == "spotify":
            run(["open", f"spotify:search:{q}"])
        else:
            osascript(tell + f"play playlist {as_str(q)}")
    elif action != "now_playing":
        return json_err(f"Неизвестное действие: {action}")

    # Всегда возвращаем «что играет»
    try:
        state = osascript(tell + "player state as string")
        name = osascript(tell + "name of current track")
        artist = osascript(tell + "artist of current track")
        return json_ok(player=app, state=state, track=name, artist=artist)
    except MacError:
        return json_ok(player=app, state="unknown")


def _stop_speech() -> list[str]:
    """Остановить всё, что говорит вслух: `say`, `afplay`, и попросить HUD заглушить озвучку браузера."""
    killed = []
    for proc in ("say", "afplay"):
        try:
            if subprocess.run(["pkill", "-x", proc], capture_output=True, timeout=3).returncode == 0:
                killed.append(proc)
        except (OSError, subprocess.SubprocessError):
            pass
    try:
        import urllib.request
        urllib.request.urlopen(urllib.request.Request(_SETTINGS.get("hud_url", "http://127.0.0.1:8765") + "/api/hush",
                                                      data=b"{}", headers={"Content-Type": "application/json"}, method="POST"), timeout=1).close()
        killed.append("hud")
    except Exception:  # HUD может быть выключен
        pass
    return killed


@guarded
def mac_say(args: dict) -> str:
    if args.get("action") == "stop":
        return json_ok(stopped=_stop_speech())
    text = args.get("text", "")
    if not text:
        return json_err("Пустой текст")
    _stop_speech()  # новая фраза всегда перебивает предыдущую — никаких наложений
    cmd = ["say"]
    if args.get("voice"):
        cmd += ["-v", args["voice"]]
    if args.get("rate"):
        cmd += ["-r", str(int(args["rate"]))]
    cmd.append(text)
    subprocess.Popen(cmd)  # не блокируем агента
    return json_ok(spoken=text)


@guarded
def mac_notify(args: dict) -> str:
    title, msg = args.get("title", "JARVIS"), args.get("message", "")
    script = f"display notification {as_str(msg)} with title {as_str(title)}"
    if args.get("subtitle"):
        script += f" subtitle {as_str(args['subtitle'])}"
    if args.get("sound", True):
        script += ' sound name "Glass"'
    osascript(script)
    return json_ok(shown=True)


@guarded
def mac_screenshot(args: dict) -> str:
    out_dir = Path(_SETTINGS["screenshot_dir"]).expanduser() if _SETTINGS.get("screenshot_dir") else mac.cache_dir("screenshots")
    out_dir.mkdir(parents=True, exist_ok=True)
    path = out_dir / mac.stamp("screen", "png")
    mode = args.get("mode") or "screen"
    if args.get("ocr"):
        ocr = native.screen_text(path, mode)
        if ocr:
            return json_ok(backend="peekaboo", text="\n".join(ocr["lines"]), **ocr)
    if native.screenshot(path, mode):  # peekaboo (если установлен): точный захват окна без AppleScript
        return json_ok(path=str(path), backend="peekaboo", hint="Передайте path в vision_analyze, чтобы описать содержимое экрана")
    cmd = ["screencapture", "-x"]  # -x: без звука затвора
    if mode == "front_window":
        # Получаем id активного окна через JXA и снимаем его
        try:
            wid = osascript(
                "ObjC.import('CoreGraphics');"
                "var app=Application.currentApplication();app.includeStandardAdditions=true;"
                "var se=Application('System Events');var p=se.processes.whose({frontmost:true})[0];"
                "p.windows[0].name();'ok'",
                language="javascript",
            )
            cmd += ["-l", wid] if wid.isdigit() else ["-w"]
        except MacError:
            cmd += ["-w"]
    else:
        cmd += ["-D", str(int(args.get("display") or 1))]
    cmd.append(str(path))
    run(cmd, timeout=15)
    if not path.exists():
        return json_err("Скриншот не создан. Проверьте разрешение «Запись экрана» для терминала в настройках конфиденциальности.")
    return json_ok(path=str(path), hint="Передайте path в vision_analyze, чтобы описать содержимое экрана")


@guarded
def mac_camera_snap(args: dict) -> str:
    bin_ = which("imagesnap")
    if not bin_:
        return json_err("Нужна утилита imagesnap: brew install imagesnap")
    out_dir = mac.cache_dir("camera")
    path = out_dir / mac.stamp("cam", "jpg")
    run([bin_, "-w", str(float(args.get("warmup") or 1.0)), str(path)], timeout=20)
    return json_ok(path=str(path), hint="Передайте path в vision_analyze")


@guarded
def mac_wallpaper(args: dict) -> str:
    p = Path(args.get("path", "")).expanduser()
    if not p.exists():
        return json_err(f"Файл не найден: {p}")
    osascript(f'tell application "System Events" to tell every desktop to set picture to {as_str(str(p))}')
    return json_ok(wallpaper=str(p))


# ═══════════════════════════════ Продуктивность ════════════════════════════

def _events_for_day(day: dt.date) -> list[dict]:
    """Список событий календаря на дату через AppleScript (Calendar.app)."""
    script = f'''
    set startDate to (current date)
    set {{year of startDate, month of startDate, day of startDate, hours of startDate, minutes of startDate, seconds of startDate}} to {{{day.year}, {day.month}, {day.day}, 0, 0, 0}}
    set endDate to startDate + 1 * days
    set output to ""
    tell application "Calendar"
        repeat with cal in calendars
            set evs to (every event of cal whose start date ≥ startDate and start date < endDate)
            repeat with ev in evs
                set output to output & (name of cal) & "|" & (summary of ev) & "|" & (time string of (start date of ev)) & "|" & (time string of (end date of ev)) & linefeed
            end repeat
        end repeat
    end tell
    return output
    '''
    out = osascript(script, timeout=60)
    events = []
    for line in mac.safe_list(out.splitlines()):
        parts = line.split("|")
        if len(parts) >= 4:
            events.append({"calendar": parts[0], "title": parts[1], "start": parts[2], "end": parts[3]})
    events.sort(key=lambda e: e["start"])
    return events


def _day_events(day: dt.date) -> tuple[list[dict], str]:
    """Сначала нативный `ical` (EventKit, JSON), иначе AppleScript. Возвращает (события, backend)."""
    native_events = native.calendar_events(day)
    if native_events is not None:
        return native_events, "ical"
    return _events_for_day(day), "applescript"


@guarded
def mac_calendar(args: dict) -> str:
    action = args.get("action")
    today = dt.date.today()
    if action in ("today", "tomorrow", "on_date"):
        d = {"today": today, "tomorrow": today + dt.timedelta(days=1)}.get(action) or dt.date.fromisoformat(args["date"])
        events, backend = _day_events(d)
        return json_ok(date=str(d), events=events, backend=backend)
    if action == "create":
        title = args.get("title") or "Событие"
        d = dt.date.fromisoformat(args.get("date") or str(today))
        hh, mm = (args.get("start_time") or "12:00").split(":")
        dur = int(args.get("duration_min") or 60)
        res = native.calendar_create(title, dt.datetime(d.year, d.month, d.day, int(hh), int(mm)), dur, args.get("calendar"))
        if res is not None:
            if res.get("error"):
                return json_err(res["error"], backend="ical")
            return json_ok(created=title, date=str(d), start=f"{int(hh):02d}:{int(mm):02d}", duration_min=dur, backend="ical")
        cal_clause = f'calendar {as_str(args["calendar"])}' if args.get("calendar") else "first calendar whose writable is true"
        script = f'''
        set startDate to (current date)
        set {{year of startDate, month of startDate, day of startDate, hours of startDate, minutes of startDate, seconds of startDate}} to {{{d.year}, {d.month}, {d.day}, {int(hh)}, {int(mm)}, 0}}
        set endDate to startDate + ({dur} * minutes)
        tell application "Calendar"
            tell ({cal_clause})
                make new event with properties {{summary:{as_str(title)}, start date:startDate, end date:endDate}}
            end tell
        end tell
        return "ok"
        '''
        osascript(script, timeout=60)
        return json_ok(created=title, date=str(d), start=f"{int(hh):02d}:{int(mm):02d}", duration_min=dur)
    return json_err(f"Неизвестное действие: {action}")


@guarded
def mac_reminders(args: dict) -> str:
    action = args.get("action")
    list_name = args.get("list_name")
    list_clause = f"list {as_str(list_name)}" if list_name else "default list"
    if action == "list":
        items = native.reminders_list(list_name)
        if items is not None:  # remindctl: с датами, списками и стабильными id
            return json_ok(reminders=[i["title"] for i in items], items=items, backend="remindctl")
        out = osascript(f'tell application "Reminders" to get name of every reminder of {list_clause} whose completed is false', timeout=60)
        return json_ok(reminders=mac.safe_list(out.split(",")), backend="applescript")
    if action == "add":
        title = args.get("title")
        if not title:
            return json_err("Нужен title")
        res = native.reminders_add(title, args.get("due"), list_name, args.get("notes"))
        if res is not None:
            if res.get("error"):
                return json_err(res["error"], backend="remindctl")
            return json_ok(added=title, due=args.get("due"), backend="remindctl")
        if args.get("due"):
            d = dt.datetime.strptime(args["due"], "%Y-%m-%d %H:%M")
            script = f'''
            set dueDate to (current date)
            set {{year of dueDate, month of dueDate, day of dueDate, hours of dueDate, minutes of dueDate, seconds of dueDate}} to {{{d.year}, {d.month}, {d.day}, {d.hour}, {d.minute}, 0}}
            tell application "Reminders" to tell {list_clause} to make new reminder with properties {{name:{as_str(title)}, due date:dueDate}}
            '''
        else:
            script = f'tell application "Reminders" to tell {list_clause} to make new reminder with properties {{name:{as_str(title)}}}'
        osascript(script, timeout=60)
        return json_ok(added=title, due=args.get("due"))
    if action == "complete":
        title = args.get("title", "")
        res = native.reminders_complete(title, list_name)
        if res is not None:
            if res.get("error"):
                return json_err(res["error"], backend="remindctl")
            return json_ok(completed=res.get("completed", title), backend="remindctl")
        osascript(
            f'tell application "Reminders" to set completed of (first reminder of {list_clause} whose name contains {as_str(title)} and completed is false) to true',
            timeout=60,
        )
        return json_ok(completed=title, backend="applescript")
    return json_err(f"Неизвестное действие: {action}")


@guarded
def mac_notes(args: dict) -> str:
    action = args.get("action")
    folder = args.get("folder") or "Notes"
    if action == "create":
        title = args.get("title") or f"JARVIS {time.strftime('%d.%m.%Y %H:%M')}"
        body = (args.get("body") or "").replace("\n", "<br>")
        html = f"<h1>{title}</h1><div>{body}</div>"
        osascript(
            f'tell application "Notes" to tell folder {as_str(folder)} to make new note with properties {{name:{as_str(title)}, body:{as_str(html)}}}',
            timeout=60,
        )
        return json_ok(created=title, folder=folder)
    if action == "search":
        q = args.get("query", "")
        out = osascript(f'tell application "Notes" to get name of every note whose name contains {as_str(q)} or plaintext contains {as_str(q)}', timeout=60)
        return json_ok(query=q, notes=mac.safe_list(out.split(",")))
    if action == "read":
        t = args.get("title", "")
        out = osascript(f'tell application "Notes" to get plaintext of (first note whose name contains {as_str(t)})', timeout=60)
        return json_ok(title=t, text=out)
    return json_err(f"Неизвестное действие: {action}")


@guarded
def mac_clipboard(args: dict) -> str:
    if args.get("action") == "get":
        return json_ok(text=run(["pbpaste"], check=False))
    if args.get("action") == "set":
        subprocess.run(["pbcopy"], input=(args.get("text") or "").encode(), check=True)
        return json_ok(copied=True)
    return json_err("action должен быть get или set")


@guarded
def mac_type(args: dict) -> str:
    action = args.get("action")
    text = args.get("text", "")
    if action == "type_text":
        osascript(f'tell application "System Events" to keystroke {as_str(text)}')
        return json_ok(typed=len(text))
    if action == "keystroke":
        allowed = {"command": "command", "cmd": "command", "option": "option", "alt": "option",
                   "control": "control", "ctrl": "control", "shift": "shift"}
        mods = []
        for m in args.get("modifiers") or []:
            if str(m).lower() not in allowed:  # только известные модификаторы — в AppleScript уходит строго allow-list
                return json_err(f"Неизвестный модификатор {m!r}; допустимы: command, option, control, shift")
            mods.append(allowed[str(m).lower()])
        using = ""
        if mods:
            using = " using {" + ", ".join(f"{m} down" for m in mods) + "}"
        special = {"return": 36, "enter": 36, "tab": 48, "space": 49, "escape": 53, "esc": 53, "delete": 51, "backspace": 51,
                   "up": 126, "down": 125, "left": 123, "right": 124}
        key = text.lower()
        if key in special:
            osascript(f'tell application "System Events" to key code {special[key]}{using}')
        else:
            osascript(f'tell application "System Events" to keystroke {as_str(text)}{using}')
        return json_ok(pressed=text, modifiers=mods)
    return json_err(f"Неизвестное действие: {action}")


def _window_geometry(action: str, w: int, h: int, menubar: int = 25) -> tuple[int, int, int, int] | None:
    """Целевые (x, y, ширина, высота) для раскладок; None — действие не про геометрию."""
    if action == "maximize":
        return 0, menubar, w, h - menubar
    if action == "left_half":
        return 0, menubar, w // 2, h - menubar
    if action == "right_half":
        return w // 2, menubar, w // 2, h - menubar
    if action == "center":
        cw, ch = int(w * 0.7), int(h * 0.7)
        return (w - cw) // 2, (h - ch) // 2, cw, ch
    return None


@guarded
def mac_window(args: dict) -> str:
    action = args.get("action")
    app = args.get("app") or mac.frontmost_app()
    if action == "list":
        rows = native.window_list(app)
        if rows is not None:  # peekaboo: id окон, кто активен, кто свёрнут
            return json_ok(app=app, windows=[r["title"] for r in rows], items=rows, backend="peekaboo")
        out = osascript(f'tell application "System Events" to get name of every window of process {as_str(app)}')
        return json_ok(app=app, windows=mac.safe_list(out.split(",")), backend="applescript")
    # размеры основного экрана
    bounds = osascript('tell application "Finder" to get bounds of window of desktop')
    x0, y0, x1, y1 = [int(v) for v in bounds.split(",")]
    w, h = x1 - x0, y1 - y0
    menubar = 25
    geo = _window_geometry(action, w, h, menubar)
    done = native.window_action(app, "minimize") if action == "minimize" else (native.window_action(app, "set-bounds", geo) if geo else None)
    if done:
        return json_ok(app=app, action=action, backend="peekaboo")
    tell = f'tell application "System Events" to tell process {as_str(app)} to tell window 1 to '
    if action == "minimize":
        osascript(tell + "set value of attribute \"AXMinimized\" to true")
    elif geo:
        x, y, gw, gh = geo
        osascript(tell + f"set {{position, size}} to {{{{{x}, {y}}}, {{{gw}, {gh}}}}}")
    else:
        return json_err(f"Неизвестное действие: {action}")
    return json_ok(app=app, action=action, backend="applescript")


@guarded
def mac_shortcut(args: dict) -> str:
    action = args.get("action") or "run"
    if action == "list":
        return json_ok(shortcuts=mac.safe_list(run(["shortcuts", "list"], timeout=30).splitlines()))
    name = args.get("name")
    if not name:
        return json_err("Нужно имя быстрой команды (name)")
    cmd = ["shortcuts", "run", name]
    inp = args.get("input")
    proc = subprocess.run(cmd, input=(inp or "").encode() if inp else None, capture_output=True, timeout=120)
    if proc.returncode != 0:
        return json_err(proc.stderr.decode(errors="ignore").strip() or f"Код {proc.returncode}")
    return json_ok(ran=name, output=proc.stdout.decode(errors="ignore").strip())


@guarded
def mac_contacts(args: dict) -> str:
    """Контакты macOS (Contacts.app): поиск и выгрузка для импорта в базу знаний."""
    action = args.get("action") or "search"
    limit = int(args.get("limit") or 20)
    if action == "search":
        q = (args.get("query") or "").strip()
        if not q:
            return json_err("Нужен query")
        script = f'''
        set out to ""
        tell application "Contacts"
            set ppl to (every person whose name contains {as_str(q)})
            repeat with p in ppl
                set em to ""
                try
                    set em to value of first email of p
                end try
                set ph to ""
                try
                    set ph to value of first phone of p
                end try
                set org to ""
                try
                    set org to organization of p
                end try
                set out to out & (name of p) & "|" & em & "|" & ph & "|" & org & "|" & (note of p) & linefeed
            end repeat
        end tell
        return out'''
    elif action == "recent":
        # люди, с которыми есть события в календаре за последние 30 дней — самые релевантные для импорта
        script = '''
        set out to ""
        set startD to (current date) - 30 * days
        tell application "Calendar"
            repeat with cal in calendars
                repeat with ev in (every event of cal whose start date ≥ startD and start date ≤ (current date))
                    try
                        repeat with a in attendees of ev
                            set out to out & (display name of a) & "|" & (email of a) & "||" & (summary of ev) & linefeed
                        end repeat
                    end try
                end repeat
            end repeat
        end tell
        return out'''
    elif action == "list":
        script = '''
        set out to ""
        tell application "Contacts"
            set ppl to (every person whose organization is not "")
            repeat with p in ppl
                set out to out & (name of p) & "|||" & (organization of p) & "|" & linefeed
            end repeat
        end tell
        return out'''
    else:
        return json_err(f"Неизвестное действие: {action}")
    out = osascript(script, timeout=120)
    rows, seen = [], set()
    for line in mac.safe_list(out.splitlines()):
        parts = (line.split("|") + ["", "", "", "", ""])[:5]
        name = parts[0].strip()
        if not name or name in seen:
            continue
        seen.add(name)
        rows.append({"name": name, "email": parts[1].strip(), "phone": parts[2].strip(), "org": parts[3].strip(), "note": parts[4].strip()[:200]})
        if len(rows) >= limit:
            break
    return json_ok(action=action, count=len(rows), contacts=rows)


@guarded
def mac_focus(args: dict) -> str:
    """Focus macOS: get — текущий режим; set — через Shortcuts (нужна Быстрая команда «JARVIS Focus <name>»)."""
    action = args.get("action") or "get"
    base = Path.home() / "Library" / "DoNotDisturb" / "DB"
    if action == "get":
        try:
            assertions = json.loads((base / "Assertions.json").read_text())
            records = (assertions.get("data") or [{}])[0].get("storeAssertionRecords") or []
            if not records:
                return json_ok(focus="", active=False)
            rec = max(records, key=lambda r: r.get("assertionStartDateTimestamp", 0))
            mode_id = rec["assertionDetails"]["assertionDetailsModeIdentifier"]
            modes = (json.loads((base / "ModeConfigurations.json").read_text()).get("data") or [{}])[0].get("modeConfigurations") or {}
            name = modes.get(mode_id, {}).get("mode", {}).get("name") or mode_id.rsplit(".", 1)[-1]
            return json_ok(focus=name, active=True)
        except (OSError, ValueError, KeyError) as e:
            return json_err(f"Не удалось прочитать состояние Focus: {e}", hint="Нужен доступ к ~/Library/DoNotDisturb (Full Disk Access для терминала)")
    if action == "set":
        name = (args.get("name") or "").strip()
        if not name:
            return json_err("Нужно name (например: Работа, Не беспокоить, off)")
        proc = subprocess.run(["shortcuts", "run", f"JARVIS Focus {name}"], capture_output=True, timeout=20)
        if proc.returncode != 0:
            return json_err(f"Быстрая команда «JARVIS Focus {name}» не найдена",
                            hint="Создайте в Shortcuts команду с этим именем и действием «Установить фокус» — macOS не даёт менять Focus напрямую")
        return json_ok(focus=name, set=True)
    return json_err(f"Неизвестное действие: {action}")


@guarded
def mac_file_manage(args: dict) -> str:
    """Файловые операции «по-маковски»: удаление только в Корзину, через Finder."""
    action = args.get("action")
    src = Path(mac.resolve_target(args.get("path", ""))) if args.get("path") else None
    if action == "trash":
        if not src or not src.exists():
            return json_err("Файл не найден")
        osascript(f'tell application "Finder" to delete POSIX file {as_str(str(src))}', timeout=30)
        return json_ok(trashed=str(src))
    if action == "rename":
        new_name = args.get("new_name", "").strip()
        if not src or not src.exists() or not new_name or "/" in new_name:
            return json_err("Нужны существующий path и new_name без «/»")
        dst = src.with_name(new_name)
        if dst.exists():
            return json_err(f"Уже существует: {dst}")
        src.rename(dst)
        return json_ok(renamed=str(dst))
    if action == "move":
        dst_dir = Path(mac.resolve_target(args.get("destination", "")))
        if not src or not src.exists() or not dst_dir.is_dir():
            return json_err("Нужны существующий path и папка destination")
        dst = dst_dir / src.name
        if dst.exists():
            return json_err(f"Уже существует: {dst}")
        src.rename(dst)
        return json_ok(moved=str(dst))
    if action == "mkdir":
        if not src:
            return json_err("Нужен path")
        src.mkdir(parents=True, exist_ok=True)
        return json_ok(created=str(src))
    if action == "list":
        d = src or Path.home()
        if not d.is_dir():
            return json_err("Не папка")
        items = sorted(d.iterdir(), key=lambda p: p.stat().st_mtime, reverse=True)[: int(args.get("limit") or 30)]
        return json_ok(path=str(d), items=[{"name": p.name, "dir": p.is_dir(), "kb": round(p.stat().st_size / 1024, 1)} for p in items])
    if action == "info":
        if not src or not src.exists():
            return json_err("Файл не найден")
        st = src.stat()
        return json_ok(path=str(src), size_kb=round(st.st_size / 1024, 1), modified=dt.datetime.fromtimestamp(st.st_mtime).isoformat(), is_dir=src.is_dir())
    return json_err(f"Неизвестное действие: {action}")


@guarded
def mac_applescript(args: dict) -> str:
    if not _SETTINGS.get("allow_raw_applescript"):
        return json_err(
            "Выполнение произвольного AppleScript отключено. Включите в config.yaml Hermes: "
            "plugins.entries.jarvis-macos.settings.allow_raw_applescript: true"
        )
    out = osascript(args.get("script", ""), language=args.get("language") or "applescript", timeout=120)
    return json_ok(output=out)


# ─────────────────────────── таблица «имя → обработчик» ────────────────────

HANDLERS = {
    "mac_app": mac_app,
    "mac_open": mac_open,
    "mac_spotlight": mac_spotlight,
    "mac_finder": mac_finder,
    "mac_volume": mac_volume,
    "mac_brightness": mac_brightness,
    "mac_dark_mode": mac_dark_mode,
    "mac_power": mac_power,
    "mac_wifi": mac_wifi,
    "mac_bluetooth": mac_bluetooth,
    "mac_battery": mac_battery,
    "mac_system_info": mac_system_info,
    "mac_media": mac_media,
    "mac_say": mac_say,
    "mac_notify": mac_notify,
    "mac_screenshot": mac_screenshot,
    "mac_camera_snap": mac_camera_snap,
    "mac_wallpaper": mac_wallpaper,
    "mac_calendar": mac_calendar,
    "mac_reminders": mac_reminders,
    "mac_notes": mac_notes,
    "mac_clipboard": mac_clipboard,
    "mac_type": mac_type,
    "mac_window": mac_window,
    "mac_shortcut": mac_shortcut,
    "mac_file_manage": mac_file_manage,
    "mac_contacts": mac_contacts,
    "mac_focus": mac_focus,
    "mac_applescript": mac_applescript,
}
