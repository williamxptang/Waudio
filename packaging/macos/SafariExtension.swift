// Waudio for Safari's native side: Safari gives the extension's sendNativeMessage requests (from
// browser/waudio.js) to this handler inside Waudio.app, which passes each to the Waudio API's
// socket in the app group's folder — the one place this sandboxed extension can reach. The app
// group and the app's name come from its Info.plist (build_app.sh fills them in from __init__.py).
import Foundation
import SafariServices

@objc(SafariWebExtensionHandler)
final class SafariWebExtensionHandler: NSObject, NSExtensionRequestHandling {
    func beginRequest(with context: NSExtensionContext) {
        let info = Bundle.main.infoDictionary ?? [:]
        let app = info["HostAppName"] as? String ?? "The app"
        let item = context.inputItems.first as? NSExtensionItem
        let request = item?.userInfo?[SFExtensionMessageKey] as? [String: Any]
        let answer: [String: Any]
        if let request, let group = info["HostAppGroup"] as? String,
           let folder = FileManager.default.containerURL(forSecurityApplicationGroupIdentifier: group) {
            answer = APISocket.ask(request, at: folder.appendingPathComponent("waudio.sock").path, app: app)
        } else {
            answer = ["ok": false, "error": request == nil ? "not a request"
                      : "\(app)'s app group isn't available: rebuild \(app) with your Apple Development certificate"]
        }
        let reply = NSExtensionItem()
        reply.userInfo = [SFExtensionMessageKey: answer]
        context.completeRequest(returningItems: [reply], completionHandler: nil)
    }
}
