// JARVIS.app — приложение строки меню macOS (без Xcode-проекта: собирается одной командой swiftc в install.sh).
//
// Что делает:
//   • иконка ◉ в строке меню: цвет статуса (HUD / gateway / обновление);
//   • меню: открыть HUD, голосовой чат, старт/стоп сервисов, база знаний, обновление, настройки, логи;
//   • раз в 5 минут — проверяет статус сервисов и файл update.json (его пишет updater);
//   • при доступном обновлении — пункт «Обновить до X» и уведомление; обновление идёт в фоне через scripts/update.py.
// Всё «тяжёлое» делает команда `jarvis` — приложение лишь удобная кнопка над ней.

import AppKit
import Foundation

let hermesHome: String = {
    if let env = ProcessInfo.processInfo.environment["HERMES_HOME"], !env.isEmpty { return env }
    let cfg = NSHomeDirectory() + "/.jarvis-home"           // install.sh записывает сюда путь, если HERMES_HOME нестандартный
    if let s = try? String(contentsOfFile: cfg, encoding: .utf8) { return s.trimmingCharacters(in: .whitespacesAndNewlines) }
    return NSHomeDirectory() + "/.hermes"
}()
let jarvisHome = hermesHome + "/jarvis"
let jarvisBin = NSHomeDirectory() + "/.local/bin/jarvis"
let hudURL = "http://127.0.0.1:" + (ProcessInfo.processInfo.environment["JARVIS_HUD_PORT"] ?? "8765")
let hermesAPI = "http://127.0.0.1:8642"

func readJSON(_ path: String) -> [String: Any] {
    guard let d = FileManager.default.contents(atPath: path),
          let j = try? JSONSerialization.jsonObject(with: d) as? [String: Any] else { return [:] }
    return j
}

/// Запуск команды в фоне (не блокируя UI); completion получает вывод.
func run(_ cmd: String, _ args: [String], env: [String: String] = [:], completion: ((Int32, String) -> Void)? = nil) {
    DispatchQueue.global().async {
        let p = Process()
        p.executableURL = URL(fileURLWithPath: cmd)
        p.arguments = args
        var e = ProcessInfo.processInfo.environment
        e["HERMES_HOME"] = hermesHome
        e["PATH"] = NSHomeDirectory() + "/.local/bin:/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin"
        env.forEach { e[$0.key] = $0.value }
        p.environment = e
        let pipe = Pipe(); p.standardOutput = pipe; p.standardError = pipe
        do { try p.run() } catch { DispatchQueue.main.async { completion?(-1, "\(error)") }; return }
        let data = pipe.fileHandleForReading.readDataToEndOfFile()
        p.waitUntilExit()
        let out = String(data: data, encoding: .utf8) ?? ""
        DispatchQueue.main.async { completion?(p.terminationStatus, out) }
    }
}

/// Открыть команду в Terminal.app (для интерактивного голосового чата).
func openInTerminal(_ command: String) {
    let script = "tell application \"Terminal\"\n activate\n do script \"export HERMES_HOME='\(hermesHome)'; \(command)\"\nend tell"
    run("/usr/bin/osascript", ["-e", script])
}

func http(_ url: String, timeout: Double = 2, completion: @escaping (Bool, [String: Any]) -> Void) {
    guard let u = URL(string: url) else { completion(false, [:]); return }
    var req = URLRequest(url: u); req.timeoutInterval = timeout
    URLSession.shared.dataTask(with: req) { data, resp, _ in
        let ok = (resp as? HTTPURLResponse)?.statusCode == 200
        let j = data.flatMap { try? JSONSerialization.jsonObject(with: $0) as? [String: Any] } ?? [:]
        DispatchQueue.main.async { completion(ok, j) }
    }.resume()
}

/// Уведомление через osascript: не требует UserNotifications-фреймворка, который падает
/// («bundleProxyForCurrentProcess is nil») у приложений, собранных без Xcode и запущенных не из /Applications.
func notify(_ title: String, _ body: String) {
    let esc = { (s: String) in s.replacingOccurrences(of: "\\", with: "\\\\").replacingOccurrences(of: "\"", with: "\\\"") }
    run("/usr/bin/osascript", ["-e", "display notification \"\(esc(body))\" with title \"\(esc(title))\""])
}

/// Мастер первого запуска: 3 шага, каждый — одно окно с кнопками. Без терминала, без чтения документации.
///   1. где живёт JARVIS (строка меню) → 2. модель отвечает? (иначе — открыть мастер модели)
///   3. права macOS (selftest --fix) → 4. открыть HUD и папку ~/JARVIS.
/// Повторить в любой момент: меню → «Мастер настройки».
func doctorJSON(_ completion: @escaping ([String: Any]) -> Void) {
    run(jarvisBin, ["doctor", "--json", "--quick"]) { _, out in
        // doctor печатает JSON; вокруг могут быть строки от hermes — берём от первой «{» до конца
        if let i = out.firstIndex(of: "{"), let d = String(out[i...]).data(using: .utf8),
           let j = try? JSONSerialization.jsonObject(with: d) as? [String: Any] { DispatchQueue.main.async { completion(j) } }
        else { DispatchQueue.main.async { completion([:]) } }
    }
}

func alert(_ title: String, _ text: String, _ buttons: [String]) -> Int {
    let a = NSAlert(); a.messageText = title; a.informativeText = text
    buttons.forEach { a.addButton(withTitle: $0) }
    NSApp.activate(ignoringOtherApps: true)
    return a.runModal().rawValue - NSApplication.ModalResponse.alertFirstButtonReturn.rawValue   // 0 = первая кнопка
}

func firstRunWizard(force: Bool = false) {
    let marker = jarvisHome + "/.app-first-run-done"
    guard force || !FileManager.default.fileExists(atPath: marker) else { return }
    try? "1".write(toFile: marker, atomically: true, encoding: .utf8)

    // шаг 1 — где искать
    let step1 = alert("Добро пожаловать. Я JARVIS.",
                      "Я живу в строке меню — значок ◉ справа сверху, рядом с часами. Окна в Dock у меня нет.\n\nСейчас за минуту проверим, что всё готово: модель, права macOS, папка для ваших файлов.",
                      ["Проверить", "Позже"])
    guard step1 == 0 else { return }

    doctorJSON { j in
        let checks = (j["checks"] as? [[String: Any]]) ?? []
        func status(_ name: String) -> (String, String, String) {
            let c = checks.first { ($0["name"] as? String)?.hasPrefix(name) == true }
            return (c?["status"] as? String ?? "skip", c?["note"] as? String ?? "", c?["fix"] as? String ?? "")
        }
        // шаг 2 — модель
        let model = status("Модель")
        if model.0 == "fail" {
            let r = alert("Модель не настроена или не отвечает", model.1 + "\n\nБез модели JARVIS не может думать. Рекомендую OpenRouter (один ключ — сотни моделей) или Ollama (локально, бесплатно).",
                          ["Открыть мастер модели", "Пропустить"])
            if r == 0 { openInTerminal("hermes model && jarvis gateway restart") }
        }
        // шаг 3 — права macOS (полный selftest долгий — предлагаем, не навязываем)
        let r3 = alert("Права macOS", "Чтобы читать календарь, напоминания, управлять окнами и делать скриншоты, macOS попросит разрешения. Проверка займёт ~20 секунд и откроет нужные панели настроек.",
                       ["Проверить права", "Позже"])
        if r3 == 0 { openInTerminal("jarvis selftest --fix") }
        // шаг 4 — хранилище и HUD
        let vault = status("Хранилище")
        let r4 = alert("Ваши файлы", "Папка ~/JARVIS — моё хранилище: кладите туда документы, PDF, заметки, целые проекты (`jarvis vault add <папка>`). Я читаю их содержимое и ищу по нему.\n\n" + (vault.1.isEmpty ? "" : "Сейчас: " + vault.1),
                       ["Открыть папку и HUD", "Только HUD"])
        run(jarvisBin, ["vault", "init"])
        if r4 == 0 { run("/usr/bin/open", [NSHomeDirectory() + "/JARVIS"]) }
        NSWorkspace.shared.open(URL(string: hudURL)!)
        let apiOk = status("Gateway").0 == "ok"
        if !apiOk { notify("JARVIS", "Сервисы поднимаются… если через минуту HUD пишет «нет связи» — меню → Диагностика.") }
    }
}

final class AppDelegate: NSObject, NSApplicationDelegate {
    var item: NSStatusItem!
    var hudUp = false, apiUp = false
    var version = "—", latest = "", updateAvailable = false, updating = false
    var timer: Timer?

    func applicationDidFinishLaunching(_ n: Notification) {
        item = NSStatusBar.system.statusItem(withLength: NSStatusItem.variableLength)
        item.button?.title = "◉"
        item.button?.font = NSFont.systemFont(ofSize: 15, weight: .medium)
        rebuildMenu()
        refresh()
        timer = Timer.scheduledTimer(withTimeInterval: 300, repeats: true) { [weak self] _ in self?.refresh() }
        // при первом запуске — поднять сервисы, если они не под launchd
        run(jarvisBin, ["gateway", "start"])
        run(jarvisBin, ["hud", "start"])
        DispatchQueue.main.asyncAfter(deadline: .now() + 0.5) { firstRunWizard() }
    }

    /// Повторный клик по JARVIS.app в Finder/Launchpad, когда он уже запущен — открываем меню, чтобы было видно, что он жив.
    func applicationShouldHandleReopen(_ sender: NSApplication, hasVisibleWindows: Bool) -> Bool {
        item.button?.performClick(nil)
        return false
    }

    // ───────── статус ─────────
    func refresh() {
        let inst = readJSON(jarvisHome + "/install.json")
        version = inst["version"] as? String ?? "—"
        let upd = readJSON(jarvisHome + "/update.json")
        updateAvailable = (upd["available"] as? Bool) ?? false
        latest = upd["latest"] as? String ?? ""
        http(hudURL + "/api/status") { ok, j in
            self.hudUp = ok
            if ok {
                self.apiUp = ((j["hermes"] as? [String: Any])?["up"] as? Bool) ?? false
                self.paint(); self.rebuildMenu()
            } else {
                // HUD лежит — состояние gateway проверяем напрямую, иначе «API ○» вводило бы в заблуждение
                http(hermesAPI + "/health") { apiOk, _ in self.apiUp = apiOk; self.paint(); self.rebuildMenu() }
            }
        }
    }

    func paint() {
        guard let b = item.button else { return }
        let color: NSColor = updating ? .systemOrange : (hudUp && apiUp ? .systemCyan : (hudUp || apiUp ? .systemYellow : .secondaryLabelColor))
        let title = updateAvailable && !updating ? "◉ ⬆" : "◉"
        b.attributedTitle = NSAttributedString(string: title, attributes: [.foregroundColor: color, .font: NSFont.systemFont(ofSize: 15, weight: .medium)])
        b.toolTip = "JARVIS \(version) · HUD \(hudUp ? "on" : "off") · API \(apiUp ? "on" : "off")" + (updateAvailable ? " · доступно \(latest)" : "")
    }

    // ───────── меню ─────────
    func rebuildMenu() {
        let m = NSMenu()
        func add(_ title: String, _ sel: Selector?, key: String = "") {
            let i = NSMenuItem(title: title, action: sel, keyEquivalent: key); i.target = self; m.addItem(i)
        }
        let st = NSMenuItem(title: "JARVIS \(version)  ·  HUD \(hudUp ? "●" : "○")  API \(apiUp ? "●" : "○")", action: nil, keyEquivalent: "")
        st.isEnabled = false; m.addItem(st)
        if updating {
            let u = NSMenuItem(title: "Обновление выполняется…", action: nil, keyEquivalent: ""); u.isEnabled = false; m.addItem(u)
        } else if updateAvailable {
            add("⬆ Обновить до \(latest)", #selector(doUpdate))
        }
        m.addItem(.separator())
        add("Открыть HUD", #selector(openHUD), key: "h")
        add("Голосовой чат в Terminal", #selector(openVoice), key: "j")
        add("Спросить…", #selector(ask), key: "a")
        add("База знаний (BRAIN)", #selector(openBrain), key: "k")
        add("Hermes Desktop (чат-приложение)", #selector(openDesktop))
        add("Панель Hermes (dashboard)", #selector(openDashboard))
        add("Замолчать", #selector(hush), key: ".")
        m.addItem(.separator())
        add(hudUp && apiUp ? "Остановить сервисы" : "Запустить сервисы", #selector(toggleServices))
        add("Утренний брифинг сейчас", #selector(brief))
        add("Проверить интеграции (selftest)", #selector(selftest))
        add("Диагностика и починка (doctor --fix)", #selector(doctorFix), key: "d")
        add("Папка файлов ~/JARVIS", #selector(openVault), key: "f")
        m.addItem(.separator())
        add("Проверить обновления", #selector(checkUpdates), key: "u")
        add("Откатить последнее обновление", #selector(rollback))
        add("Настройки (config.yaml)", #selector(openConfig), key: ",")
        add("Разрешения macOS", #selector(perms))
        add("Логи", #selector(logs))
        add("Мастер настройки…", #selector(wizard))
        m.addItem(.separator())
        add("Открыть на GitHub", #selector(github))
        add("О JARVIS…", #selector(about))
        add("Выйти из JARVIS.app", #selector(quit), key: "q")
        item.menu = m
    }

    @objc func openHUD() { NSWorkspace.shared.open(URL(string: hudURL)!); if !hudUp { run(jarvisBin, ["hud", "start"]) { _, _ in self.refresh() } } }
    @objc func openBrain() { NSWorkspace.shared.open(URL(string: hudURL + "/#brain")!) }
    @objc func openVoice() { openInTerminal("jarvis") }
    /// Официальный десктоп-клиент Hermes (тот же агент, конфиг и память) — вместо собственного чат-окна.
    @objc func openDesktop() { run(NSHomeDirectory() + "/.local/bin/hermes", ["desktop"]) }
    /// Веб-панель Hermes: сессии, cron, навыки, MCP, память — http://127.0.0.1:9119
    @objc func openDashboard() {
        http("http://127.0.0.1:9119/") { up, _ in
            if up { NSWorkspace.shared.open(URL(string: "http://127.0.0.1:9119")!) }
            else { run(NSHomeDirectory() + "/.local/bin/hermes", ["dashboard"]) }  // сам откроет браузер
        }
    }
    @objc func brief() { openInTerminal("jarvis brief") }
    @objc func hush() { run(jarvisBin, ["hush"]) }
    @objc func selftest() { openInTerminal("jarvis selftest") }
    @objc func doctorFix() { openInTerminal("jarvis doctor --fix") }
    @objc func openVault() { run(jarvisBin, ["vault", "init"]) { _, _ in DispatchQueue.main.async { run("/usr/bin/open", [NSHomeDirectory() + "/JARVIS"]) } } }
    @objc func wizard() { firstRunWizard(force: true) }
    @objc func logs() { openInTerminal("jarvis logs") }
    @objc func openConfig() { NSWorkspace.shared.open(URL(fileURLWithPath: hermesHome + "/config.yaml")) }
    @objc func perms() { run(jarvisBin, ["perms"]) }
    @objc func github() { let inst = readJSON(jarvisHome + "/install.json"); let repo = inst["repo"] as? String ?? "debug999-cyber/jarvis-hermes"; NSWorkspace.shared.open(URL(string: "https://github.com/\(repo)")!) }
    @objc func quit() { NSApp.terminate(nil) }
    @objc func about() {
        let a = NSAlert(); a.messageText = "J.A.R.V.I.S. \(version)"
        a.informativeText = "Just A Rather Very Intelligent System\n\nСоздал ERTGYKI · github.com/debug999-cyber\nЯдро: Hermes Agent © Nous Research (MIT)\n\n«Sometimes you gotta run before you can walk.»"
        a.addButton(withTitle: "OK"); NSApp.activate(ignoringOtherApps: true); a.runModal()
    }

    @objc func toggleServices() {
        if hudUp && apiUp {
            run(jarvisBin, ["hud", "stop"]); run(jarvisBin, ["gateway", "stop"]) { _, _ in self.refresh() }
        } else {
            run(jarvisBin, ["gateway", "start"]); run(jarvisBin, ["hud", "start"]) { _, _ in self.refresh() }
        }
    }

    @objc func ask() {
        let a = NSAlert(); a.messageText = "JARVIS слушает"; a.informativeText = "Вопрос уйдёт агенту, ответ придёт уведомлением и на HUD."
        let f = NSTextField(frame: NSRect(x: 0, y: 0, width: 360, height: 24)); f.placeholderString = "Что у меня сегодня в календаре?"
        a.accessoryView = f; a.addButton(withTitle: "Спросить"); a.addButton(withTitle: "Отмена")
        NSApp.activate(ignoringOtherApps: true)
        guard a.runModal() == .alertFirstButtonReturn, !f.stringValue.isEmpty else { return }
        run(jarvisBin, ["ask", f.stringValue]) { _, out in
            let text = out.trimmingCharacters(in: .whitespacesAndNewlines)
            notify("JARVIS", String(text.suffix(400)))
        }
    }

    // ───────── обновления ─────────
    @objc func checkUpdates() {
        run(jarvisBin, ["update", "--check", "--json"]) { _, out in
            self.refresh()
            if let d = out.data(using: .utf8), let j = try? JSONSerialization.jsonObject(with: d) as? [String: Any] {
                if let err = j["error"] as? String, !err.isEmpty { notify("JARVIS", "Не удалось проверить обновления: \(err)"); return }
                if (j["available"] as? Bool) == true { notify("JARVIS", "Доступно обновление \(j["latest"] ?? "")") }
                else { notify("JARVIS", "У вас последняя версия \(j["current"] ?? "")") }
            }
        }
    }

    @objc func doUpdate() {
        updating = true; paint(); rebuildMenu()
        run(jarvisBin, ["update", "--yes"]) { code, out in
            self.updating = false
            self.refresh()
            if code == 0 { notify("JARVIS обновлён", "Сервисы перезапущены. Откат — в меню.") }
            else { notify("JARVIS: обновление не удалось", String(out.suffix(300))) }
        }
    }

    @objc func rollback() {
        let a = NSAlert(); a.messageText = "Откатить JARVIS на предыдущую версию?"; a.addButton(withTitle: "Откатить"); a.addButton(withTitle: "Отмена")
        NSApp.activate(ignoringOtherApps: true)
        guard a.runModal() == .alertFirstButtonReturn else { return }
        run(jarvisBin, ["update", "--rollback"]) { code, out in
            self.refresh(); notify("JARVIS", code == 0 ? "Откат выполнен" : "Откат не удался: \(out.suffix(200))")
        }
    }
}

let app = NSApplication.shared
app.setActivationPolicy(.accessory)   // только строка меню, без иконки в Dock
let delegate = AppDelegate()
app.delegate = delegate
app.run()
