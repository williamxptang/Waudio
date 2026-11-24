// Waudio macOS host — a thin AppKit/WKWebView shell.
//
// 1. Starts the Python backend (`python -m waudio` in <project>/backend) as a child process,
//    so macOS gives Microphone and Accessibility to Waudio.app, not to Python.
//    The Python and project paths come from Info.plist (WAPython, WAProject), filled in by
//    build_app.sh, so editing the Python code never changes the app or its signature.
// 2. Reads the child's stdout; a line `WAUDIO_SETTINGS=<url>` (menu bar → Settings…) opens
//    the settings window, which shows the frontend/ page served by the backend. `WAUDIO_URL=`
//    (printed at every backend start) re-points an open window after a restart.
// 3. Owns the macOS permissions: only this process (the signed app, whose Info.plist carries the
//    microphone description) can make macOS show its prompts, and its checks update live. It
//    writes `WAUDIO_PERMISSIONS {json}` to the child's stdin every second, and runs a prompt
//    when the child prints `WAUDIO_PERMISSION=<name>` (Settings → Permissions → Allow). The same report
//    carries whether Waudio for Safari (inside this app) is on — only the containing app may ask
//    Safari — and `WAUDIO_SAFARI_SETTINGS` opens Safari's Extensions settings at it.
// 4. Shows a Dock icon while the settings window is open; menu-bar-only otherwise.
//    Draws the floating pill (HUD below) from `WAUDIO_HUD=<state>|<detail>` lines; those
//    lines can carry dictated text, so they are never written to host.log; nor are `WAUDIO_LEVEL=<0..1>`
//    (the microphone's loudness for the waveform, ~12 a second), `WAUDIO_PILL_MIC=` and `WAUDIO_PILL_HINT=`
//    (the pill's key-hint row). `WAUDIO_PILL={json}`
//    carries its settings (on/off, transparency, size, theme, position, spot); `WAUDIO_PILL_PLACE=1|0`
//    turns placing mode on / off (the pill shown and draggable; each drop goes back as
//    `WAUDIO_DO pill_spot <across> <down>`).
// 5. Quits when the backend exits (menu bar → Quit); exit code 75 means "restart me" and the host
//    starts a fresh backend (replacing the process in place breaks its menu-bar icon). Opening the
//    app while it runs sends `WAUDIO_DO open_settings`; sleep, wake, screen lock and unlock send
//    `WAUDIO_DO sleep|wake|locked|unlocked`. On quit, SIGTERM → 5 s → SIGKILL.
// 6. macOS Services (Info.plist NSServices): "Read Aloud with Waudio" passes the selected text to
//    the backend as `WAUDIO_READ ["<text>"]` (JSON, one line; stdin only, never host.log);
//    "Stop Reading with Waudio" sends `WAUDIO_DO stop_reading`.
// Child output is appended to ~/Library/Logs/Waudio/host.log.
//
// Built without an Xcode project (swiftc + hand-assembled .app, see build_app.sh).

import AppKit
import AVFoundation
import SafariServices
import ApplicationServices
import WebKit

/// The app's name and bundle id, from Info.plist (build_app.sh fills them in from backend/waudio/__init__.py).
let appName = Bundle.main.object(forInfoDictionaryKey: "CFBundleName") as? String ?? "app"
let appID = Bundle.main.bundleIdentifier ?? "app"

final class AppDelegate: NSObject, NSApplicationDelegate, NSWindowDelegate, WKNavigationDelegate {
    private var window: NSWindow?
    private var webView: WKWebView?
    private var loadedQuery: String?  // the token the page was loaded with: it takes it out of its own address
    private var backend: Process?
    private var logHandle: FileHandle?
    private var toChild: FileHandle?
    private let hud = HUD()
    private var quitting = false

    override init() {
        super.init()
        hud.log = { [weak self] line in self?.logHandle?.write(Data((line + "\n").utf8)) }
        hud.send = { [weak self] line in try? self?.toChild?.write(contentsOf: Data((line + "\n").utf8)) }
    }

    // MARK: launch

    private let services = ServiceProvider()
    private let serviceWrites = DispatchQueue(label: appID + ".service-writes")  // off the main thread

    func applicationDidFinishLaunching(_ note: Notification) {
        installMenu()
        services.send = { [weak self] line in
            guard let pipe = self?.toChild else { return }
            self?.serviceWrites.async { try? pipe.write(contentsOf: Data((line + "\n").utf8)) }
        }
        NSApp.servicesProvider = services
        NSUpdateDynamicServices()  // macOS picks up the Services from Info.plist
        do {
            try spawnBackend()
        } catch {
            presentFatal("The \(appName) backend failed to start:\n\(error.localizedDescription)")
        }
        Timer.scheduledTimer(withTimeInterval: 1, repeats: true) { [weak self] _ in self?.reportPermissions() }
        reportPermissions()
        watchSleepAndLock()
    }

    /// Sleep, screen lock and switching user end a recording (the backend cancels it and ignores
    /// the shortcut until wake / unlock, then rebuilds its keyboard hook, which often dies across
    /// sleep). Sent as `WAUDIO_DO sleep | wake | locked | unlocked`.
    private func watchSleepAndLock() {
        let tell = { [weak self] (what: String) in
            try? self?.toChild?.write(contentsOf: Data("WAUDIO_DO \(what)\n".utf8))
        }
        let ws = NSWorkspace.shared.notificationCenter
        ws.addObserver(forName: NSWorkspace.willSleepNotification, object: nil, queue: .main) { _ in tell("sleep") }
        ws.addObserver(forName: NSWorkspace.didWakeNotification, object: nil, queue: .main) { _ in tell("wake") }
        ws.addObserver(forName: NSWorkspace.sessionDidResignActiveNotification, object: nil, queue: .main) { _ in tell("locked") }
        ws.addObserver(forName: NSWorkspace.sessionDidBecomeActiveNotification, object: nil, queue: .main) { _ in tell("unlocked") }
        let dn = DistributedNotificationCenter.default()
        dn.addObserver(forName: Notification.Name("com.apple.screenIsLocked"), object: nil, queue: .main) { _ in tell("locked") }
        dn.addObserver(forName: Notification.Name("com.apple.screenIsUnlocked"), object: nil, queue: .main) { _ in tell("unlocked") }
    }

    // MARK: permissions (asked and checked here, in the app's own process)

    /// true = allowed, false = refused, nil = not asked yet (microphone only).
    private func permissions() -> [String: Bool?] {
        let mic: Bool?
        switch AVCaptureDevice.authorizationStatus(for: .audio) {
        case .notDetermined: mic = nil
        case .authorized: mic = true
        default: mic = false
        }
        return ["accessibility": AXIsProcessTrusted(),  // also what the key listener runs on
                "microphone": mic, "safari_extension": safariOn]
    }

    // MARK: Waudio for Safari (Contents/PlugIns): on or off, asked of Safari here only — Safari answers
    // the app that contains the extension, no one else (SFErrorDomain 1)

    private var safariOn: Bool?  // nil: not known (not registered yet, or Safari didn't say)
    private var safariAsked = Date.distantPast
    private var safariID: String { (Bundle.main.bundleIdentifier ?? "") + ".safari" }

    private func refreshSafari() {
        guard Date().timeIntervalSince(safariAsked) > 3 else { return }  // every few seconds is enough
        safariAsked = Date()
        SFSafariExtensionManager.getStateOfSafariExtension(withIdentifier: safariID) { [weak self] state, _ in
            DispatchQueue.main.async { self?.safariOn = state?.isEnabled }
        }
    }

    /// Settings → Permissions → Open in Safari: Safari's Extensions settings with ours selected.
    private func openSafariSettings() {
        SFSafariApplication.showPreferencesForExtension(withIdentifier: safariID) { [weak self] error in
            if let error { self?.logHandle?.write(Data("Safari settings: \((error as NSError).domain) \((error as NSError).code)\n".utf8)) }
        }
        safariAsked = .distantPast  // ask again soon: it may be switched on now
    }

    private func reportPermissions() {
        refreshSafari()
        let p = permissions().mapValues { $0.map { $0 ? "true" : "false" } ?? "null" }
        let json = "{" + p.keys.sorted().map { "\"\($0)\": \(p[$0]!)" }.joined(separator: ", ") + "}"
        try? toChild?.write(contentsOf: Data("WAUDIO_PERMISSIONS \(json)\n".utf8))  // backend gone: ignore
    }

    /// Make macOS show its own prompt. macOS asks only once per app; after that the settings
    /// page offers the System Settings button instead.
    private func requestPermission(_ name: String) {
        NSApp.activate(ignoringOtherApps: true)
        switch name {
        case "accessibility":
            let key = kAXTrustedCheckOptionPrompt.takeUnretainedValue() as String
            _ = AXIsProcessTrustedWithOptions([key: true] as CFDictionary)
            _ = CGRequestPostEventAccess()  // pasting (⌘V) is listed under Accessibility too
        case "microphone":
            AVCaptureDevice.requestAccess(for: .audio) { _ in
                DispatchQueue.main.async { self.reportPermissions() }
            }
        default:
            return
        }
        reportPermissions()
    }

    /// No visible menu bar (menu-bar-only app), but the menu still provides ⌘C / ⌘V / ⌘A / ⌘W
    /// inside the settings window.
    private func installMenu() {
        let main = NSMenu()
        let appItem = NSMenuItem()
        appItem.submenu = NSMenu()
        appItem.submenu?.addItem(withTitle: "Close Window", action: #selector(NSWindow.performClose(_:)), keyEquivalent: "w")
        main.addItem(appItem)
        let editItem = NSMenuItem()
        let edit = NSMenu(title: "Edit")
        edit.addItem(withTitle: "Undo", action: Selector(("undo:")), keyEquivalent: "z")
        edit.addItem(withTitle: "Redo", action: Selector(("redo:")), keyEquivalent: "Z")
        edit.addItem(withTitle: "Cut", action: #selector(NSText.cut(_:)), keyEquivalent: "x")
        edit.addItem(withTitle: "Copy", action: #selector(NSText.copy(_:)), keyEquivalent: "c")
        edit.addItem(withTitle: "Paste", action: #selector(NSText.paste(_:)), keyEquivalent: "v")
        edit.addItem(withTitle: "Select All", action: #selector(NSText.selectAll(_:)), keyEquivalent: "a")
        editItem.submenu = edit
        main.addItem(editItem)
        NSApp.mainMenu = main
    }

    // MARK: settings window

    private func showSettings(_ url: URL) {
        if window == nil {
            let rect = NSRect(x: 0, y: 0, width: 880, height: 620)
            let w = NSWindow(contentRect: rect, styleMask: [.titled, .closable, .miniaturizable, .resizable],
                             backing: .buffered, defer: false)
            w.title = "\(appName) Settings"
            w.minSize = NSSize(width: 720, height: 480)
            w.isReleasedWhenClosed = false
            w.center()
            w.setFrameAutosaveName("\(appName)Settings")
            w.delegate = self
            let web = WKWebView(frame: rect, configuration: WKWebViewConfiguration())
            web.allowsBackForwardNavigationGestures = false
            web.navigationDelegate = self
            web.setValue(false, forKey: "drawsBackground")  // no white flash before the page paints
            w.contentView = web
            window = w
            webView = web
        }
        if webView?.url?.port != url.port || loadedQuery != url.query {
            loadedQuery = url.query
            webView?.load(URLRequest(url: url))
        } else if let frag = url.fragment {
            webView?.evaluateJavaScript("location.hash = '\(frag)'")
        }
        NSApp.setActivationPolicy(.regular)  // Dock icon while the window is open
        window?.makeKeyAndOrderFront(nil)
        NSApp.activate(ignoringOtherApps: true)
    }

    func windowWillClose(_ note: Notification) {
        NSApp.setActivationPolicy(.accessory)  // back to menu bar only
    }

    /// Clicking the Dock icon brings the settings window back.
    /// Opening the app again while it runs (Finder, Launchpad, Dock): show Settings.
    func applicationShouldHandleReopen(_ app: NSApplication, hasVisibleWindows: Bool) -> Bool {
        try? toChild?.write(contentsOf: Data("WAUDIO_DO open_settings\n".utf8))
        return true
    }

    /// After a backend restart, point an open settings window at the new address (same pane).
    private func followRestart(_ url: URL) {
        guard let web = webView, window?.isVisible == true else { return }
        var c = URLComponents(url: url, resolvingAgainstBaseURL: false)
        c?.fragment = web.url?.fragment
        if let next = c?.url { web.load(URLRequest(url: next)) }
    }

    /// Links that leave the local settings page open in the default browser.
    func webView(_ webView: WKWebView, decidePolicyFor action: WKNavigationAction,
                 decisionHandler: @escaping (WKNavigationActionPolicy) -> Void) {
        if let url = action.request.url, url.host != "127.0.0.1" {
            NSWorkspace.shared.open(url)
            return decisionHandler(.cancel)
        }
        decisionHandler(.allow)
    }

    // MARK: backend subprocess

    private func setting(_ key: String) -> String? {
        Bundle.main.object(forInfoDictionaryKey: key) as? String
    }

    private func openLog() -> FileHandle? {
        let dir = FileManager.default.homeDirectoryForCurrentUser.appendingPathComponent("Library/Logs/\(appName)")
        try? FileManager.default.createDirectory(at: dir, withIntermediateDirectories: true)
        let file = dir.appendingPathComponent("host.log")
        if !FileManager.default.fileExists(atPath: file.path) {
            FileManager.default.createFile(atPath: file.path, contents: nil)
        }
        let h = try? FileHandle(forWritingTo: file)
        h?.seekToEndOfFile()
        return h
    }

    private func spawnBackend() throws {
        guard let python = setting("WAPython"), let project = setting("WAProject"),
              FileManager.default.isExecutableFile(atPath: python) else {
            throw NSError(domain: appID, code: 1, userInfo: [
                NSLocalizedDescriptionKey: "Python not found (Info.plist WAPython). Rebuild with packaging/macos/build_app.sh.",
            ])
        }
        // "Waudio Engine" (Python under our name and icon, see build_app.sh) running the venv
        // at WAPython; the venv's own python if the helper is missing.
        let engine = Bundle.main.bundleURL.appendingPathComponent(
            "Contents/Helpers/\(appName) Engine.app/Contents/MacOS/\(appName) Engine")
        let useEngine = FileManager.default.isExecutableFile(atPath: engine.path)
        let p = Process()
        p.executableURL = useEngine ? engine : URL(fileURLWithPath: python)
        p.arguments = ["-m", "waudio"]
        p.currentDirectoryURL = URL(fileURLWithPath: project).appendingPathComponent("backend")

        var env = ProcessInfo.processInfo.environment
        env["WAUDIO_HOST"] = "1"
        if useEngine { env["__PYVENV_LAUNCHER__"] = python }  // the venv, as its python would set it
        env["WAUDIO_APP"] = Bundle.main.bundlePath
        env["PATH"] = "/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin"  // Finder-launched apps get a minimal PATH
        p.environment = env

        let stdin = Pipe()
        let stdout = Pipe()
        let stderr = Pipe()
        p.standardInput = stdin
        p.standardOutput = stdout
        p.standardError = stderr
        toChild = stdin.fileHandleForWriting
        logHandle = openLog()

        var lineBuffer = Data()
        stdout.fileHandleForReading.readabilityHandler = { [weak self] h in
            let chunk = h.availableData
            guard let self, !chunk.isEmpty else { return }
            lineBuffer.append(chunk)
            while let nl = lineBuffer.firstIndex(of: 0x0A) {
                let line = String(decoding: lineBuffer[..<nl], as: UTF8.self)
                lineBuffer.removeSubrange(...nl)
                if line.contains("WAUDIO_HUD=") {  // may carry dictated text: never logged
                    guard line.hasPrefix("WAUDIO_HUD=") else { continue }  // garbled: drop it
                    let parts = line.dropFirst("WAUDIO_HUD=".count).split(separator: "|", maxSplits: 1)
                    let state = parts.first.map(String.init) ?? "hide"
                    let detail = parts.count > 1 ? String(parts[1]) : ""
                    DispatchQueue.main.async { self.hud.show(state, detail) }
                    continue
                }
                if line.hasPrefix("WAUDIO_LEVEL=") {  // the microphone's loudness, ~12 a second: not logged
                    let v = Double(line.dropFirst("WAUDIO_LEVEL=".count).trimmingCharacters(in: .whitespaces)) ?? 0
                    DispatchQueue.main.async { self.hud.level(CGFloat(v)) }
                    continue
                }
                if line.hasPrefix("WAUDIO_PILL_MIC=") || line.hasPrefix("WAUDIO_PILL_HINT=") {  // the hint row
                    let mic = line.hasPrefix("WAUDIO_PILL_MIC=")
                    let v = String(line.drop { $0 != "=" }.dropFirst())
                    DispatchQueue.main.async { mic ? self.hud.microphone(v) : self.hud.note(v) }
                    continue
                }
                if line.hasPrefix("WAUDIO_PILL=") {  // Settings → General → Pill
                    let json = String(line.dropFirst("WAUDIO_PILL=".count))
                    DispatchQueue.main.async { self.hud.configure(json) }
                    continue
                }
                if line.hasPrefix("WAUDIO_PILL_PLACE=") {  // Settings → Pill → Place… / Done
                    let on = line.dropFirst("WAUDIO_PILL_PLACE=".count).trimmingCharacters(in: .whitespaces) == "1"
                    DispatchQueue.main.async { self.hud.placeMode(on) }
                    continue
                }
                self.logHandle?.write(Data((line + "\n").utf8))
                if line.hasPrefix("WAUDIO_SETTINGS="),
                   let url = URL(string: String(line.dropFirst("WAUDIO_SETTINGS=".count)).trimmingCharacters(in: .whitespaces)) {
                    DispatchQueue.main.async { self.showSettings(url) }
                } else if line.hasPrefix("WAUDIO_SAFARI_SETTINGS") {
                    DispatchQueue.main.async { self.openSafariSettings() }
                } else if line.hasPrefix("WAUDIO_PERMISSION=") {
                    let name = String(line.dropFirst("WAUDIO_PERMISSION=".count)).trimmingCharacters(in: .whitespaces)
                    DispatchQueue.main.async { self.requestPermission(name) }
                } else if line.hasPrefix("WAUDIO_URL="),
                          let url = URL(string: String(line.dropFirst("WAUDIO_URL=".count)).trimmingCharacters(in: .whitespaces)) {
                    DispatchQueue.main.async { self.followRestart(url) }  // backend restarted on a new port
                }
            }
        }
        stderr.fileHandleForReading.readabilityHandler = { [weak self] h in
            let chunk = h.availableData
            if !chunk.isEmpty { self?.logHandle?.write(chunk) }
        }
        p.terminationHandler = { [weak self] proc in
            DispatchQueue.main.async {
                guard let self, !self.quitting else { return }
                if proc.terminationStatus == 75 {  // the backend asked to be restarted (menu → Restart)
                    do { try self.spawnBackend() } catch { self.presentFatal(error.localizedDescription) }
                    return
                }
                if proc.terminationStatus != 0 {
                    self.presentFatal("\(appName) stopped (code \(proc.terminationStatus)).\nSee ~/Library/Logs/\(appName)/")
                }
                NSApp.terminate(nil)
            }
        }
        try p.run()
        backend = p
    }

    // MARK: quit — SIGTERM → 5 s → SIGKILL

    func applicationShouldTerminateAfterLastWindowClosed(_ app: NSApplication) -> Bool { false }

    func applicationWillTerminate(_ note: Notification) {
        quitting = true
        guard let p = backend, p.isRunning else { return }
        kill(p.processIdentifier, SIGTERM)
        let deadline = Date().addingTimeInterval(5)
        while p.isRunning && Date() < deadline { usleep(100_000) }
        if p.isRunning { kill(p.processIdentifier, SIGKILL) }
        logHandle?.closeFile()
    }

    // MARK: errors

    private func presentFatal(_ message: String) {
        let alert = NSAlert()
        alert.messageText = "\(appName) could not start"
        alert.informativeText = message
        alert.alertStyle = .critical
        NSApp.activate(ignoringOtherApps: true)
        alert.runModal()
        NSApp.terminate(nil)
    }
}

// MARK: - macOS Services

/// Right-click selected text → Services → Read Aloud / Stop Reading with Waudio. The text is
/// handed to the backend over its stdin as one JSON line; it is never logged.
final class ServiceProvider: NSObject {
    var send: ((String) -> Void)?
    static let maxChars = 200_000  // a whole book selected must not freeze anything

    @objc func readAloud(_ pboard: NSPasteboard, userData: String?, error: AutoreleasingUnsafeMutablePointer<NSString?>) {
        guard let full = pboard.string(forType: .string), !full.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty,
              case let text = String(full.prefix(ServiceProvider.maxChars)),
              let data = try? JSONSerialization.data(withJSONObject: [text]),
              let json = String(data: data, encoding: .utf8) else {
            error.pointee = "No text to read" as NSString
            return
        }
        send?("WAUDIO_READ " + json)
    }

    @objc func stopReading(_ pboard: NSPasteboard, userData: String?, error: AutoreleasingUnsafeMutablePointer<NSString?>) {
        send?("WAUDIO_DO stop_reading")
    }
}

// MARK: - floating pill

/// Settings → General → Pill, sent by the backend as `WAUDIO_PILL={json}` at start and on change.
struct PillConfig {
    var enabled = true
    var opacity: CGFloat = 0.5    // the pill's background and waveform; the words get half its transparency
    var scale: CGFloat = 1        // its size, 0.8 – 1.8: every measure in it (HUD.metrics)
    var theme = "system"          // follows Settings → General → Appearance
    var position = "text"         // text: above the text box · pointer: under the pointer · fixed: at spot
    var spot = CGPoint(x: 0.5, y: 0)  // fixed: share of the screen's room across (0 left) and down (0 top)
    var waveform = true           // bars that follow the microphone's level while dictating
    var hints = true              // a row under it: the microphone, Stop <shortcut>, Cancel Esc
    var stopKey = ""              // the dictation shortcut, as the menu shows it (⌥D)
    var followPointer: Bool { position == "pointer" }
}

/// The pill's waveform, a row of its own above the words: one bar per level the backend sends (~every
/// 80 ms), newest on the right, mirrored about the middle, as many bars as fit the pill's width (it
/// grows with the words). Bars rise at once and fall off gently (the only smoothing). Layers, not
/// redrawing: only their frames change, with implicit animations off. Decorative: VoiceOver skips it.
final class WaveView: NSView {
    static let release: CGFloat = 0.72   // each new bar keeps at least this share of the one before
    static let keep = 240                // levels remembered: enough for the widest pill
    private var levels: [CGFloat] = []
    private var bars: [CALayer] = []
    var color = NSColor.labelColor.cgColor { didSet { bars.forEach { $0.backgroundColor = color } } }
    var barWidth: CGFloat = 3 { didSet { layoutBars() } }
    var gap: CGFloat = 2 { didSet { layoutBars() } }
    var width: CGFloat = 200 { didSet { if width != oldValue { invalidateIntrinsicContentSize() } } }  // set by the pill

    override init(frame: NSRect) {
        super.init(frame: frame)
        wantsLayer = true
        setAccessibilityElement(false)
    }
    required init?(coder: NSCoder) { fatalError("not from a nib") }

    override var intrinsicContentSize: NSSize { NSSize(width: width, height: NSView.noIntrinsicMetric) }

    /// The bar a new level gets: as loud as it is, or a little less than the bar before (falling off).
    static func next(_ level: CGFloat, after previous: CGFloat) -> CGFloat {
        let v = level.isFinite ? min(1, max(0, level)) : 0
        return max(v, previous * release)
    }

    /// How many bars fill a row this wide: as many whole bars and gaps as fit, at least one.
    static func barCount(width: CGFloat, barWidth: CGFloat, gap: CGFloat) -> Int {
        guard width.isFinite, barWidth > 0, gap >= 0 else { return 1 }
        return max(1, Int(((width + gap) / (barWidth + gap)).rounded(.down)))
    }

    /// The waveform's width: the pill's widest row (the words, the hints), never less than its least.
    static func rowWidth(least: CGFloat, words: CGFloat, hints: CGFloat) -> CGFloat {
        max(least, words, hints)
    }

    func push(_ level: CGFloat) {
        levels.append(WaveView.next(level, after: levels.last ?? 0))
        if levels.count > WaveView.keep { levels.removeFirst(levels.count - WaveView.keep) }
        layoutBars()
    }

    func reset() { levels = []; layoutBars() }

    func sample() {  // the preview: a made-up phrase, so the bars can be seen in Settings
        levels = (0..<WaveView.keep).map { (i: Int) -> CGFloat in  // typed step by step: older Swift times out on one expression
            let wave: CGFloat = abs(sin(CGFloat(i) * 0.55))
            let dip: CGFloat = i % 7 == 3 ? 0.3 : 1
            return 0.15 + 0.6 * wave * dip
        }
        layoutBars()
    }

    override func layout() { super.layout(); layoutBars() }

    private func layoutBars() {
        CATransaction.begin()
        CATransaction.setDisableActions(true)
        let count = WaveView.barCount(width: bounds.width, barWidth: barWidth, gap: gap)
        while bars.count < count {
            let b = CALayer()
            b.backgroundColor = color
            layer?.addSublayer(b)
            bars.append(b)
        }
        while bars.count > count { bars.removeLast().removeFromSuperlayer() }
        let used = CGFloat(count) * barWidth + CGFloat(count - 1) * gap, left = (bounds.width - used) / 2  // centred
        let h = bounds.height, least = max(2, barWidth * 0.7)
        let shown = Array(levels.suffix(count))
        let padded = Array(repeating: CGFloat(0), count: count - shown.count) + shown  // newest on the right
        for (i, b) in bars.enumerated() {
            let bh = least + (h - least) * padded[i]
            b.frame = NSRect(x: left + CGFloat(i) * (barWidth + gap), y: (h - bh) / 2, width: barWidth, height: bh)
            b.cornerRadius = barWidth / 2
            b.opacity = padded[i] > 0 ? 1 : 0.45  // silence: small dots
        }
        CATransaction.commit()
    }
}

/// The pill's background. In placing mode the panel takes clicks and this view gets them all
/// (the words inside never do), so the pill can be dragged; the rest of the time clicks go through.
final class PillView: NSView {
    var onPress: (() -> Void)?
    var onDrag: ((NSPoint) -> Void)?  // how far the pointer has moved since the press, in screen points
    var onDrop: (() -> Void)?
    private var start = NSPoint.zero

    override func hitTest(_ point: NSPoint) -> NSView? { super.hitTest(point) == nil ? nil : self }
    override func acceptsFirstMouse(for event: NSEvent?) -> Bool { true }
    override func mouseDown(with event: NSEvent) {
        start = NSEvent.mouseLocation
        NSCursor.closedHand.set()
        onPress?()
    }
    override func mouseDragged(with event: NSEvent) {
        let at = NSEvent.mouseLocation
        onDrag?(NSPoint(x: at.x - start.x, y: at.y - start.y))
    }
    override func mouseUp(with event: NSEvent) {
        NSCursor.arrow.set()
        onDrop?()
    }
}

/// The floating pill: only what you are dictating, or the sentence being read aloud. While
/// listening a waveform (WaveView: one bar per `WAUDIO_LEVEL` line, a number only, never logged;
/// Reduce Motion: the dot, brightening with the level) and a key-hint row (the microphone from
/// `WAUDIO_PILL_MIC`, Stop <shortcut>, Cancel Esc; a note from `WAUDIO_PILL_HINT` for 2 s) — or, with
/// both off in Settings, a pulsing dot — then the words (confirmed ones solid, the unfinished end faded), then
/// it fades out; while reading aloud a steel-blue dot and the sentence. It sits just above the
/// text you are typing into (the caret line, else the text box, else above the bottom of the
/// focused window, else the top of the screen) — or just under the mouse pointer, moving with it,
/// or at a fixed spot you dragged it to — uses the settings window's colours (the design tokens, light
/// or dark), never takes focus and lets clicks through (except in placing mode). Status messages
/// (errors, "stopped") go to the menu-bar icon instead.
final class HUD {
    private let panel: NSPanel
    private let box = PillView()
    private let dot = NSView()
    private let wave = WaveView(frame: .zero)
    private let text = NSTextField(labelWithString: "")
    private let hintRow = NSTextField(labelWithString: "")
    private var outer: NSStackView!
    private var waveHeight: NSLayoutConstraint!
    private var padLeft: NSLayoutConstraint!
    private var padRight: NSLayoutConstraint!
    private var mic = ""                 // the microphone in use (WAUDIO_PILL_MIC)
    private var noteText = ""            // a note of ours in the hint row for a moment ("Esc again to cancel")
    private var noteTimer: Timer?
    private var hideTimer: Timer?
    private var generation = 0  // a fade only hides the panel if nothing was shown since
    private var config = PillConfig()
    private var state = "hide"
    private var dotWidth: NSLayoutConstraint!
    private var dotHeight: NSLayoutConstraint!
    private var textMax: NSLayoutConstraint!
    private var row: NSStackView!
    private var anchor: NSRect?          // where the pill goes, in Cocoa screen coordinates
    private var anchorAt = Date.distantPast
    private let axQueue = DispatchQueue(label: appID + ".pill-position")
    private var placing = false
    private var words = ("", "")         // what the pill shows, to redraw after a style change
    private var watchdog: Timer?         // hides a pill no longer updated (a lost "hide")
    private var appearanceWatch: NSKeyValueObservation?
    private var follow: Timer?           // moves the pill with the pointer while it is shown
    private var followedAt = NSPoint(x: -1, y: -1)
    private static var manualAX = Set<pid_t>()  // apps already asked for their AX tree (axQueue only)
    var log: ((String) -> Void)?        // host.log: where the pill went (never any words)
    var send: ((String) -> Void)?       // a line to the backend (WAUDIO_DO pill_spot …)
    private var dragging = false         // placing mode: shown, draggable, takes clicks
    private var placeTimer: Timer?       // ends a placing mode nobody ended
    private var dragFrom = NSPoint.zero

    private struct Palette { let bg, line, fg, faded, listening, working, reading: UInt32 }
    // frontend/css/tokens.css: --bg-elev / --line / --fg / --fg-dim / --err / --warn / --accent
    private static let light = Palette(bg: 0xFBF9F2, line: 0xD9D3C0, fg: 0x18171A, faded: 0x8A857A,
                                       listening: 0xA33449, working: 0x9C6510, reading: 0x3A5C7A)
    private static let dark = Palette(bg: 0x1B1916, line: 0x3A352B, fg: 0xF2EFE6, faded: 0x8E887A,
                                      listening: 0xE0728A, working: 0xD9A24E, reading: 0x7FB0D6)
    /// Every measure of the pill, from its one size setting (Settings → Pill → Size, 0.8 – 1.8), so a
    /// bigger pill is the same pill, larger: the words, the hints (~0.8 of them), the waveform (~1.75 of
    /// them), its bars, the one-line height, the longest line, the margins and the gaps between rows.
    struct Metrics { let font, hint, wave, height, width, bar, gap, pad, spacing, vpad: CGFloat }
    static func metrics(_ scale: CGFloat) -> Metrics {
        let k = scale.isFinite ? min(1.8, max(0.8, scale)) : 1
        let font = (16 * k).rounded()
        return Metrics(font: font, hint: (font * 0.8).rounded(), wave: (font * 1.75).rounded(),
                       height: (40 * k).rounded(), width: (460 * k).rounded(), bar: max(2, (3 * k).rounded()),
                       gap: max(1.5, (2 * k).rounded()), pad: (14 * k).rounded(), spacing: (3 * k).rounded(),
                       vpad: (8 * k).rounded())
    }

    private static func rgb(_ hex: UInt32, _ a: CGFloat = 1) -> NSColor {
        NSColor(srgbRed: CGFloat(hex >> 16 & 0xFF) / 255, green: CGFloat(hex >> 8 & 0xFF) / 255,
                blue: CGFloat(hex & 0xFF) / 255, alpha: a)
    }

    init() {
        panel = NSPanel(contentRect: NSRect(x: 0, y: 0, width: 120, height: 36),
                        styleMask: [.borderless, .nonactivatingPanel], backing: .buffered, defer: true)
        panel.isOpaque = false
        panel.backgroundColor = .clear
        panel.hasShadow = true
        panel.level = .statusBar
        panel.ignoresMouseEvents = true
        panel.collectionBehavior = [.canJoinAllSpaces, .fullScreenAuxiliary, .stationary, .ignoresCycle]
        panel.hidesOnDeactivate = false

        box.wantsLayer = true
        box.layer?.borderWidth = 1
        dot.wantsLayer = true
        text.lineBreakMode = .byTruncatingHead  // the newest words stay visible
        text.cell?.usesSingleLineMode = true
        row = NSStackView(views: [dot, text])
        row.orientation = .horizontal
        row.alignment = .centerY
        row.spacing = 8
        hintRow.lineBreakMode = .byTruncatingMiddle
        hintRow.cell?.usesSingleLineMode = true
        hintRow.isHidden = true
        wave.isHidden = true
        outer = NSStackView(views: [wave, row, hintRow])  // the waveform on top, then the words, then the hints
        outer.orientation = .vertical
        outer.alignment = .leading
        outer.spacing = 3
        outer.translatesAutoresizingMaskIntoConstraints = false
        box.addSubview(outer)
        dotWidth = dot.widthAnchor.constraint(equalToConstant: 9)
        dotHeight = dot.heightAnchor.constraint(equalToConstant: 9)
        textMax = text.widthAnchor.constraint(lessThanOrEqualToConstant: 460)
        waveHeight = wave.heightAnchor.constraint(equalToConstant: 22)
        padLeft = outer.leadingAnchor.constraint(equalTo: box.leadingAnchor, constant: 14)
        padRight = outer.trailingAnchor.constraint(equalTo: box.trailingAnchor, constant: -14)
        NSLayoutConstraint.activate([
            dotWidth, dotHeight, textMax, waveHeight,
            padLeft, padRight,
            outer.centerYAnchor.constraint(equalTo: box.centerYAnchor),
        ])
        panel.contentView = box
        box.onPress = { [weak self] in self.map { $0.dragFrom = $0.panel.frame.origin } }
        box.onDrag = { [weak self] d in self?.dragBy(d) }
        box.onDrop = { [weak self] in self?.dropped() }
        AXUIElementSetMessagingTimeout(AXUIElementCreateSystemWide(), 0.15)  // a hung app can't stall us
        appearanceWatch = NSApp.observe(\.effectiveAppearance) { [weak self] _, _ in  // macOS light/dark switched
            DispatchQueue.main.async { self?.applyStyle() }
        }
        NSWorkspace.shared.notificationCenter.addObserver(  // Reduce Motion switched: dot or bars from now on
            forName: NSWorkspace.accessibilityDisplayOptionsDidChangeNotification, object: nil, queue: .main) { [weak self] _ in
            guard let self, self.state != "hide" else { return }
            self.decorate()
            self.resize()
        }
        applyStyle()
    }

    func configure(_ json: String) {
        guard let d = (try? JSONSerialization.jsonObject(with: Data(json.utf8))) as? [String: Any] else { return }
        if let v = d["enabled"] as? Bool { config.enabled = v }
        if let v = d["opacity"] as? Double { config.opacity = HUD.alphas(CGFloat(v)).background }
        if let v = d["scale"] as? Double, v.isFinite { config.scale = CGFloat(min(1.8, max(0.8, v))) }
        if let v = d["theme"] as? String { config.theme = v }
        if let v = d["waveform"] as? Bool { config.waveform = v }
        if let v = d["hints"] as? Bool { config.hints = v }
        if let v = d["stop_key"] as? String { config.stopKey = v }
        let was = config.position
        if let v = d["position"] as? String, ["text", "pointer", "fixed"].contains(v) { config.position = v }
        if let v = d["spot"] as? [Double], v.count == 2,
           abs(v[0] - config.spot.x) > 0.0015 || abs(v[1] - config.spot.y) > 0.0015 {  // not just our own drop, rounded
            config.spot = CGPoint(x: min(1, max(0, v[0])), y: min(1, max(0, v[1])))
        }
        if was != "text" && config.position == "text" && state != "hide" {  // back above the text box, found afresh
            anchorAt = .distantPast
            updateAnchor()
        }
        applyStyle()
        if state != "hide" { decorate(); resize() }
        if config.followPointer && state != "hide" && !dragging { startFollowing() } else { stopFollowing() }
        if !config.enabled {
            placeMode(false)
            generation += 1
            hideTimer?.invalidate()
            watchdog?.invalidate()
            panel.orderOut(nil)
            state = "hide"
            anchor = nil
            stopFollowing()
        }
    }

    private var palette: Palette {
        switch config.theme {
        case "light": return HUD.light
        case "dark": return HUD.dark
        default:
            let dark = NSApp.effectiveAppearance.bestMatch(from: [.aqua, .darkAqua]) == .darkAqua
            return dark ? HUD.dark : HUD.light
        }
    }

    private func applyStyle() {
        let p = palette, s = HUD.metrics(config.scale)
        let a = HUD.alphas(config.opacity)
        box.layer?.backgroundColor = HUD.rgb(p.bg, a.background).cgColor
        box.layer?.borderColor = HUD.rgb(p.line, min(1, a.background + 0.05)).cgColor
        box.layer?.cornerRadius = s.height / 2
        let d = (s.font * 0.7).rounded()
        dotWidth.constant = d
        dotHeight.constant = d
        dot.layer?.cornerRadius = d / 2
        dot.layer?.backgroundColor = HUD.rgb(state == "reading" ? p.reading
                                             : state == "listening" || state == "live" ? p.listening : p.working).cgColor
        textMax.constant = s.width
        wave.barWidth = s.bar
        wave.gap = s.gap
        waveHeight.constant = s.wave
        wave.color = HUD.rgb(p.listening, a.wave).cgColor
        padLeft.constant = s.pad
        padRight.constant = -s.pad
        outer.spacing = s.spacing
        row.spacing = (s.font * 0.5).rounded()
        hintRow.font = .systemFont(ofSize: s.hint)
        hintRow.textColor = HUD.rgb(p.faded, wordAlpha)
        text.font = .systemFont(ofSize: s.font)
        text.textColor = HUD.rgb(p.fg, wordAlpha)
        setWords(words.0, words.1)  // the words carry their own colours and font
        if state != "hide" { resize() }
    }

    /// state: listening | live (detail = confirmed \u{1} unfinished) | transcribing | loading |
    /// done (detail = the final text) | preview | sizing (a Settings slider being dragged: no auto-hide) |
    /// sizing_done (it was let go: hides a sizing pill only) | reading (detail = the sentence being read aloud;
    /// empty while the first one is made) | hide. Everything else (ready, starting, nothing,
    /// stopped, error) hides the pill: those are status messages, shown by the menu-bar icon.
    func show(_ newState: String, _ detail: String) {
        guard config.enabled, !dragging else { return }  // placing mode keeps its own words
        if newState == "sizing_done" {  // a Settings slider let go: its live preview goes at once (only that)
            if HUD.letGoHides(state) { hideTimer?.invalidate(); watchdog?.invalidate(); fade() }
            return
        }
        let shown: Bool
        switch newState {
        case "listening", "live", "transcribing", "loading", "done", "preview", "sizing", "reading": shown = true
        default: shown = false
        }
        hideTimer?.invalidate()
        watchdog?.invalidate()
        guard shown else { return fade() }
        let wasShown = state != "hide"
        generation += 1  // cancels a fade that is still running
        state = newState
        if ["transcribing", "loading", "reading"].contains(state) {  // the backend always follows these up
            watchdog = Timer.scheduledTimer(withTimeInterval: 90, repeats: false) { [weak self] _ in self?.fade() }
        }
        let p = palette
        dot.layer?.removeAllAnimations()
        dot.layer?.backgroundColor = HUD.rgb(state == "reading" ? p.reading
                                             : ["listening", "live", "preview", "sizing"].contains(state)
                                             ? p.listening : p.working).cgColor
        switch state {
        case "listening":
            setWords("", "")
            wave.reset()
            noteText = ""
            decorate()
            if dotPulses { pulse() }
        case "live", "preview", "sizing":
            let parts = detail.components(separatedBy: "\u{1}")
            setWords(parts[0], parts.count > 1 ? parts[1] : "")
            if state != "live" { wave.sample() }
            decorate()
            if words.0.isEmpty && words.1.isEmpty && dotPulses { pulse() }  // nothing heard yet
            if state == "preview" { hide(after: 2.5) }
            if state == "sizing" {  // shown while a slider is dragged; a lost "let go" still ends it
                watchdog = Timer.scheduledTimer(withTimeInterval: 15, repeats: false) { [weak self] _ in self?.fade() }
            }
        case "transcribing", "loading":
            decorate()
            pulse()  // the words so far stay until the final text arrives
        case "reading":
            decorate()
            setWords(detail, "")
            if detail.isEmpty { pulse() }  // the first sentence is still being made
        default:  // done
            decorate()
            if !detail.isEmpty { setWords(detail, "") }
            hide(after: 1.4)
        }
        panel.alphaValue = 1
        if config.position != "text" {  // no text box to find: it goes to the pointer or its spot
            resize()
            panel.orderFrontRegardless()
            if config.followPointer { startFollowing() }
            return
        }
        if wasShown {
            updateAnchor()
            resize()
            if panel.isVisible || !placing { panel.orderFrontRegardless() }  // else: still finding the box
            return
        }
        // appearing: find the text box first (off the main thread), then show it there, so the pill
        // neither jumps nor makes the app wait on a slow one
        let app = NSWorkspace.shared.frontmostApplication
        let pid = app?.processIdentifier, top = NSScreen.screens.first?.frame.maxY ?? 0
        placing = true
        anchorAt = Date()
        axQueue.async { [weak self] in
            let found = pid.flatMap { HUD.textRect(pid: $0, screenTop: top) }
            DispatchQueue.main.async {
                guard let self else { return }
                self.placing = false
                self.anchor = found?.rect
                self.log?("pill: \(self.state) above \(found?.kind ?? "top of screen") in \(app?.bundleIdentifier ?? "?")"
                          + (found.map { String(format: " at %.0f,%.0f", $0.rect.minX, $0.rect.maxY) } ?? ""))
                guard self.state != "hide" else { return }
                self.resize()
                self.panel.orderFrontRegardless()
            }
        }
    }

    // MARK: waveform and key hints

    private var reduceMotion: Bool { NSWorkspace.shared.accessibilityDisplayShouldReduceMotion }
    private var dictating: Bool { ["listening", "live", "preview", "sizing"].contains(state) }
    /// Reduce Motion: no moving bars; the dot stays and its brightness follows the level instead.
    private var dotFollowsLevel: Bool { dictating && config.waveform && reduceMotion }
    private var dotPulses: Bool { !dotFollowsLevel && wave.isHidden }

    /// Bars or the dot, and the hint row, for the state the pill is in.
    private func decorate() {
        let bars = dictating && config.waveform && !reduceMotion
        wave.isHidden = !bars
        dot.isHidden = bars
        row.isHidden = dot.isHidden && text.isHidden  // nothing said yet: only the waveform (and the hints)
        dot.alphaValue = 1
        if dotFollowsLevel { dot.layer?.removeAnimation(forKey: "pulse") }
        hintRow.isHidden = !HUD.hintShown(dictating: dictating, hints: config.hints, note: noteText)
        hintRow.stringValue = hintRow.isHidden ? "" : HUD.hintLine(mic: mic, stopKey: config.stopKey, note: noteText)
    }

    /// The pill's height: one line (no waveform, no hints) is the size's own height; more rows grow to fit.
    static func pillHeight(line: CGFloat, fitting: CGFloat, pad: CGFloat, oneLine: Bool) -> CGFloat {
        oneLine ? line : max(line, (fitting + 2 * pad).rounded(.up))
    }

    /// A slider let go hides the pill only while it shows that slider's live preview — never a dictation.
    static func letGoHides(_ state: String) -> Bool { state == "sizing" }

    /// How see-through the pill is, from its setting (the background's opacity, 0.1 = 90 % transparent … 1):
    /// the background and the waveform at that, the words and hints half as transparent (50 % -> words at 75 %).
    struct Alphas { let background: CGFloat; let wave: CGFloat; let words: CGFloat }
    static func alphas(_ opacity: CGFloat) -> Alphas {
        let o = opacity.isFinite ? max(0.1, min(1, opacity)) : 1
        return Alphas(background: o, wave: o, words: (1 + o) / 2)
    }

    /// The hint row shows while dictating with Key hints on — and a note ("Esc again to cancel") even with them off.
    static func hintShown(dictating: Bool, hints: Bool, note: String) -> Bool {
        dictating && (hints || !note.isEmpty)
    }

    /// The hint row: a note of ours if there is one, else the microphone, then Stop <shortcut> · Cancel Esc.
    static func hintLine(mic: String, stopKey: String, note: String) -> String {
        if !note.isEmpty { return note }
        let keys = [stopKey.isEmpty ? "" : "Stop \(stopKey)", "Cancel Esc"].filter { !$0.isEmpty }.joined(separator: "   ")
        return mic.isEmpty ? keys : "\(mic)   ·   \(keys)"
    }

    /// The microphone's loudness now (WAUDIO_LEVEL, 0..1): one more bar.
    func level(_ v: CGFloat) {
        guard state == "listening" || state == "live", !dragging else { return }
        if !wave.isHidden { wave.push(v) }
        else if dotFollowsLevel { dot.alphaValue = 0.35 + 0.65 * min(1, max(0, v.isFinite ? v : 0)) }
    }

    func microphone(_ name: String) {
        mic = name
        if state != "hide" && !hintRow.isHidden { decorate(); resize() }
    }

    /// A note in the hint row for 2 seconds ("Esc again to cancel").
    func note(_ text: String) {
        noteText = text
        noteTimer?.invalidate()
        noteTimer = Timer.scheduledTimer(withTimeInterval: 2, repeats: false) { [weak self] _ in
            guard let self else { return }
            self.noteText = ""
            if self.state != "hide" { self.decorate(); self.resize() }
        }
        if state != "hide" { decorate(); resize() }
    }

    /// The words (and hints) are half as transparent as the background: HUD.alphas.
    private var wordAlpha: CGFloat { HUD.alphas(config.opacity).words }

    private func setWords(_ firm: String, _ unfinished: String) {
        words = (firm, unfinished)
        let p = palette, font = text.font ?? .systemFont(ofSize: 13)
        let para = NSMutableParagraphStyle()
        para.lineBreakMode = .byTruncatingHead
        let line = NSMutableAttributedString(string: firm, attributes: [
            .font: font, .foregroundColor: HUD.rgb(p.fg, wordAlpha), .paragraphStyle: para])
        if !unfinished.isEmpty {
            let italic = NSFontManager.shared.convert(font, toHaveTrait: .italicFontMask)
            line.append(NSAttributedString(string: unfinished, attributes: [
                .font: italic, .foregroundColor: HUD.rgb(p.faded, wordAlpha), .paragraphStyle: para]))  // still firming up
        }
        text.attributedStringValue = line
        text.isHidden = line.length == 0
        row.isHidden = dot.isHidden && text.isHidden
    }

    private func pulse() {
        let a = CABasicAnimation(keyPath: "opacity")
        a.fromValue = 1; a.toValue = 0.3; a.duration = 0.6
        a.autoreverses = true; a.repeatCount = .infinity
        dot.layer?.add(a, forKey: "pulse")
    }

    // MARK: position

    private func resize() {
        let s = HUD.metrics(config.scale)
        if !wave.isHidden {  // full width: as wide as the words or the hints, at least about half a line
            wave.width = WaveView.rowWidth(least: (s.width * 0.45).rounded(), words: row.isHidden ? 0 : row.fittingSize.width,
                                           hints: hintRow.isHidden ? 0 : hintRow.fittingSize.width)
        }
        box.layoutSubtreeIfNeeded()
        let w = max(s.height, outer.fittingSize.width + 2 * s.pad)
        let h = HUD.pillHeight(line: s.height, fitting: outer.fittingSize.height, pad: s.vpad, oneLine: wave.isHidden && hintRow.isHidden)
        place(NSSize(width: w, height: h))
    }

    private func place(_ size: NSSize) {
        let mouse = NSEvent.mouseLocation
        if dragging || config.position == "fixed" { return placeAtSpot(size, mouse) }
        if config.followPointer { return placeAtPointer(size, mouse) }
        let target = anchor ?? {
            let screen = NSScreen.screens.first { $0.frame.contains(mouse) } ?? NSScreen.main
            let area = screen?.visibleFrame ?? .zero
            return NSRect(x: area.midX - size.width / 2, y: area.maxY - 14, width: 0, height: 0)  // top of the screen
        }()
        let screen = NSScreen.screens.first { $0.frame.intersects(target.insetBy(dx: -1, dy: -1)) } ?? NSScreen.main
        let area = screen?.visibleFrame ?? NSRect(x: 0, y: 0, width: 1440, height: 900)
        var x = target.minX, y = target.maxY + 6                   // just above the text
        if y + size.height > area.maxY { y = target.minY - 6 - size.height }  // no room: just below
        x = min(max(x, area.minX + 8), area.maxX - size.width - 8)
        y = min(max(y, area.minY + 8), area.maxY - size.height - 4)
        panel.setFrame(NSRect(x: x, y: y, width: size.width, height: size.height), display: true)
    }

    /// At its fixed spot, on the screen your active window is on (else the pointer's; while being
    /// dragged: the screen the pill is on).
    private func placeAtSpot(_ size: NSSize, _ mouse: NSPoint) {
        let f = panel.frame, middle = NSPoint(x: f.midX, y: f.midY)
        let screens = NSScreen.screens
        let screen = dragging && panel.isVisible ? screens.first { $0.frame.contains(middle) } ?? NSScreen.main
            : HUD.activeWindow().flatMap { w in HUD.screenIndex(w, screens.map(\.frame)).map { screens[$0] } }
              ?? screens.first { $0.frame.insetBy(dx: -1, dy: -1).contains(mouse) } ?? NSScreen.main
        let area = screen?.visibleFrame ?? NSRect(x: 0, y: 0, width: 1440, height: 900)
        panel.setFrame(HUD.spotFrame(config.spot, size, in: area), display: true)
    }

    /// A spot -> the pill's frame. The spot is a share of the room the pill has to move in, inside
    /// the screen's usable area (menu bar and Dock left out) less the margins: across from the left
    /// (0) to the right (1), down from the top (0) to the bottom (1). So 0.5 keeps it centred
    /// whatever its width, and a spot means the same place on a screen of any size.
    static func spotFrame(_ spot: CGPoint, _ size: NSSize, in area: NSRect) -> NSRect {
        let roomX = max(0, area.width - size.width - 16), roomY = max(0, area.height - size.height - 12)
        return NSRect(x: area.minX + 8 + spot.x * roomX, y: area.maxY - 4 - size.height - spot.y * roomY,
                      width: size.width, height: size.height)
    }

    /// Where a dragged pill goes: kept inside the area (the same margins as spotFrame), and snapped
    /// to the middle across, or flush to an edge, when within 12 points of it.
    static func dragFrame(_ frame: NSRect, in area: NSRect) -> NSRect {
        var f = spotFrame(spotOf(frame, in: area), frame.size, in: area)
        let left = area.minX + 8, right = area.maxX - 8, top = area.maxY - 4, bottom = area.minY + 8
        if abs(f.midX - area.midX) < 12 { f.origin.x = area.midX - f.width / 2 }
        else if f.minX - left < 12 { f.origin.x = left }
        else if right - f.maxX < 12 { f.origin.x = right - f.width }
        if top - f.maxY < 12 { f.origin.y = top - f.height }
        else if f.minY - bottom < 12 { f.origin.y = bottom }
        return f
    }

    /// Which of the screens holds most of a window (Cocoa coordinates); nil when it is on none.
    static func screenIndex(_ window: NSRect, _ screens: [NSRect]) -> Int? {
        let overlap = screens.map { s -> CGFloat in let i = s.intersection(window); return i.isNull ? 0 : i.width * i.height }
        guard let best = overlap.max(), best > 0 else { return nil }
        return overlap.firstIndex(of: best)
    }

    /// The front app's frontmost ordinary window, in Cocoa coordinates, from the window list
    /// (its bounds need no permission). nil when it has none on screen.
    static func activeWindow() -> NSRect? {
        guard let pid = NSWorkspace.shared.frontmostApplication?.processIdentifier,
              let list = CGWindowListCopyWindowInfo([.optionOnScreenOnly, .excludeDesktopElements], kCGNullWindowID)
                as? [[String: Any]] else { return nil }
        let top = NSScreen.screens.first?.frame.maxY ?? 0
        for w in list where (w[kCGWindowOwnerPID as String] as? pid_t) == pid && (w[kCGWindowLayer as String] as? Int) == 0 {
            guard let b = w[kCGWindowBounds as String] as? NSDictionary,
                  let r = CGRect(dictionaryRepresentation: b as CFDictionary), r.width > 50, r.height > 50 else { continue }
            return NSRect(x: r.minX, y: top - r.maxY, width: r.width, height: r.height)  // front to back: the first is on top
        }
        return nil
    }

    /// The pill's frame -> its spot (the reverse of spotFrame, kept between 0 and 1).
    static func spotOf(_ frame: NSRect, in area: NSRect) -> CGPoint {
        let roomX = area.width - frame.width - 16, roomY = area.height - frame.height - 12
        let x = roomX > 0 ? (frame.minX - area.minX - 8) / roomX : 0.5
        let y = roomY > 0 ? (area.maxY - 4 - frame.maxY) / roomY : 0
        return CGPoint(x: min(1, max(0, x)), y: min(1, max(0, y)))
    }

    // MARK: placing mode

    /// Settings → Pill → Place…: the pill stays shown with a hint and can be dragged; each drop is
    /// sent to the backend, which saves it as [pill] spot. Done (or two minutes) ends it.
    func placeMode(_ on: Bool) {
        placeTimer?.invalidate()
        if on {
            guard config.enabled else { return }
            dragging = true
            panel.ignoresMouseEvents = false
            hideTimer?.invalidate()
            watchdog?.invalidate()
            stopFollowing()
            generation += 1
            state = "preview"
            dot.layer?.removeAllAnimations()
            dot.layer?.backgroundColor = HUD.rgb(palette.listening).cgColor
            setWords("Drag me where the pill should sit", "")
            wave.sample()
            decorate()
            panel.alphaValue = 1
            resize()
            panel.orderFrontRegardless()
            placeTimer = Timer.scheduledTimer(withTimeInterval: 120, repeats: false) { [weak self] _ in self?.placeMode(false) }
            log?("pill: placing")
        } else if dragging {
            dragging = false
            panel.ignoresMouseEvents = true
            NSCursor.arrow.set()  // ended mid-drag: no closed hand left behind
            fade()
            send?("WAUDIO_DO pill_placing 0")  // the page shows Place… again, however it ended
            log?("pill: placed")
        }
    }

    /// Move with the pointer while dragging, on the screen the pill's middle is on (dragFrame).
    private func dragBy(_ d: NSPoint) {
        guard dragging else { return }
        var f = panel.frame
        f.origin = NSPoint(x: dragFrom.x + d.x, y: dragFrom.y + d.y)
        let middle = NSPoint(x: f.midX, y: f.midY)
        let screen = NSScreen.screens.first { $0.frame.contains(middle) } ?? NSScreen.main
        panel.setFrameOrigin(HUD.dragFrame(f, in: screen?.visibleFrame ?? f).origin)
    }

    private func dropped() {
        guard dragging else { return }
        let f = panel.frame
        let screen = NSScreen.screens.first { $0.frame.contains(NSPoint(x: f.midX, y: f.midY)) } ?? NSScreen.main
        let spot = HUD.spotOf(f, in: screen?.visibleFrame ?? f)
        config.spot = spot
        send?(String(format: "WAUDIO_DO pill_spot %.3f %.3f", spot.x, spot.y))
        placeTimer?.invalidate()  // two more minutes from the last drop
        placeTimer = Timer.scheduledTimer(withTimeInterval: 120, repeats: false) { [weak self] _ in self?.placeMode(false) }
    }

    /// Like a tooltip: ~20 px under the pointer and a little to its right; above it when there is
    /// no room below, and kept on the pointer's screen.
    private func placeAtPointer(_ size: NSSize, _ mouse: NSPoint) {
        let screen = NSScreen.screens.first { $0.frame.insetBy(dx: -1, dy: -1).contains(mouse) } ?? NSScreen.main  // its edges too
        let area = screen?.visibleFrame ?? NSRect(x: 0, y: 0, width: 1440, height: 900)
        var x = mouse.x + 12, y = mouse.y - 20 - size.height     // Cocoa: y grows upwards
        if y < area.minY + 4 { y = mouse.y + 20 }                // no room below: above the pointer
        x = min(max(x, area.minX + 8), area.maxX - size.width - 8)
        y = min(max(y, area.minY + 4), area.maxY - size.height - 4)
        panel.setFrame(NSRect(x: x, y: y, width: size.width, height: size.height), display: true)
        followedAt = mouse
    }

    /// Read the pointer ~30 times a second, only while the pill is shown (no permission needed).
    private func startFollowing() {
        guard follow == nil else { return }
        follow = Timer.scheduledTimer(withTimeInterval: 1.0 / 30, repeats: true) { [weak self] _ in
            guard let self, self.state != "hide", NSEvent.mouseLocation != self.followedAt else { return }
            self.placeAtPointer(self.panel.frame.size, NSEvent.mouseLocation)
        }
    }

    private func stopFollowing() {
        follow?.invalidate()
        follow = nil
    }

    /// Ask Accessibility where the text being dictated into is (off the main thread, at most every
    /// 0.3 s), then move the pill there.
    private func updateAnchor() {
        guard config.position == "text", !placing, Date().timeIntervalSince(anchorAt) > 0.3 else { return }
        placing = true
        anchorAt = Date()
        guard let pid = NSWorkspace.shared.frontmostApplication?.processIdentifier else { placing = false; return }
        let top = NSScreen.screens.first?.frame.maxY ?? 0
        axQueue.async { [weak self] in
            let found = HUD.textRect(pid: pid, screenTop: top)
            DispatchQueue.main.async {
                guard let self else { return }
                self.placing = false
                self.anchor = found?.rect
                if self.state != "hide" { self.resize() }
            }
        }
    }

    /// The caret line, else the focused text box (if it is not a whole document), else a strip
    /// just above the bottom of the focused window — in Cocoa screen coordinates (Accessibility
    /// measures from the top-left of the main screen, AppKit from its bottom-left). nil = unknown.
    /// Runs on axQueue; pid and the main screen's top are read on the main thread.
    static func textRect(pid: pid_t, screenTop: CGFloat) -> (rect: NSRect, kind: String)? {
        let appEl = AXUIElementCreateApplication(pid)
        AXUIElementSetMessagingTimeout(appEl, 0.15)
        // Electron and Chromium apps (Claude, Chrome, VS Code) only build their accessibility tree
        // when asked; this is the switch assistive apps use. Asked once per app.
        if !manualAX.contains(pid) {
            manualAX.insert(pid)
            AXUIElementSetAttributeValue(appEl, "AXManualAccessibility" as CFString, kCFBooleanTrue)
        }
        let cocoa = { (r: CGRect) in NSRect(x: r.minX, y: screenTop - r.maxY, width: r.width, height: r.height) }

        var focused: CFTypeRef?
        if AXUIElementCopyAttributeValue(appEl, kAXFocusedUIElementAttribute as CFString, &focused) == .success,
           let f = focused, CFGetTypeID(f) == AXUIElementGetTypeID() {
            let el = f as! AXUIElement
            if let caret = caretRect(el) { return (cocoa(caret), "the caret") }
            if let r = frame(el), r.height < 220, r.width > 20 { return (cocoa(r), "the text box") }
        }
        var window: CFTypeRef?
        if AXUIElementCopyAttributeValue(appEl, kAXFocusedWindowAttribute as CFString, &window) == .success,
           let w = window, CFGetTypeID(w) == AXUIElementGetTypeID(), let f = frame(w as! AXUIElement) {
            let r = cocoa(f)  // chat apps keep their input box at the bottom of the window
            return (NSRect(x: r.midX - 160, y: r.minY + 70, width: 320, height: 0), "the window's bottom")
        }
        return nil
    }

    private static func caretRect(_ el: AXUIElement) -> NSRect? {
        var range: CFTypeRef?
        guard AXUIElementCopyAttributeValue(el, kAXSelectedTextRangeAttribute as CFString, &range) == .success,
              let r = range else { return nil }
        var bounds: CFTypeRef?
        guard AXUIElementCopyParameterizedAttributeValue(el, kAXBoundsForRangeParameterizedAttribute as CFString,
                                                         r, &bounds) == .success, let b = bounds,
              CFGetTypeID(b) == AXValueGetTypeID() else { return nil }
        var rect = CGRect.zero
        guard AXValueGetValue(b as! AXValue, .cgRect, &rect), rect.height > 0, rect.height < 200,
              rect.origin != .zero else { return nil }
        return rect
    }

    private static func frame(_ el: AXUIElement) -> NSRect? {
        var pos: CFTypeRef?, size: CFTypeRef?
        guard AXUIElementCopyAttributeValue(el, kAXPositionAttribute as CFString, &pos) == .success,
              AXUIElementCopyAttributeValue(el, kAXSizeAttribute as CFString, &size) == .success,
              let p = pos, let s = size else { return nil }
        var point = CGPoint.zero, sz = CGSize.zero
        guard AXValueGetValue(p as! AXValue, .cgPoint, &point), AXValueGetValue(s as! AXValue, .cgSize, &sz),
              sz.width > 0, sz.height > 0 else { return nil }
        return CGRect(origin: point, size: sz)  // Accessibility coordinates: textRect converts
    }

    // MARK: hiding

    private func hide(after seconds: TimeInterval) {
        hideTimer = Timer.scheduledTimer(withTimeInterval: seconds, repeats: false) { [weak self] _ in self?.fade() }
    }

    private func fade() {
        let gen = generation
        NSAnimationContext.runAnimationGroup({ ctx in
            ctx.duration = 0.25
            panel.animator().alphaValue = 0
        }, completionHandler: { [weak self] in
            guard let self, self.generation == gen else { return }
            self.panel.orderOut(nil)
            self.state = "hide"
            self.anchor = nil
            self.stopFollowing()
        })
    }
}

// MARK: - main

signal(SIGPIPE, SIG_IGN)  // a write to an exited backend must not kill the app
let app = NSApplication.shared
let delegate = AppDelegate()
app.delegate = delegate
app.setActivationPolicy(.accessory)  // menu bar only; the Python side draws the menu-bar icon
app.run()
