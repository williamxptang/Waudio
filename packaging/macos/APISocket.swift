// The Waudio API's socket from Swift (docs/API.md): one JSON request, one JSON answer. Used by the
// Safari extension's handler (SafariExtension.swift), which passes the extension's requests on, as
// packaging/chrome/waudio_native.py does for Chrome. Kept apart so its test needs no Safari.
import Foundation

enum APISocket {
    /// Send `request` to the socket at `path`; the answer, or {"ok": false, "error": …} naming `app`.
    static func ask(_ request: [String: Any], at path: String, app: String) -> [String: Any] {
        guard let body = try? JSONSerialization.data(withJSONObject: request) else { return ["ok": false, "error": "not a request"] }
        let fd = socket(AF_UNIX, SOCK_STREAM, 0)
        guard fd >= 0 else { return ["ok": false, "error": "\(app) isn't running"] }
        defer { close(fd) }
        var addr = sockaddr_un()
        addr.sun_family = sa_family_t(AF_UNIX)
        let bytes = Array(path.utf8)
        guard bytes.count < MemoryLayout.size(ofValue: addr.sun_path) else { return ["ok": false, "error": "\(app) isn't running"] }
        withUnsafeMutableBytes(of: &addr.sun_path) { buf in for (i, b) in bytes.enumerated() { buf[i] = b } }
        let connected = withUnsafePointer(to: &addr) {
            $0.withMemoryRebound(to: sockaddr.self, capacity: 1) { connect(fd, $0, socklen_t(MemoryLayout<sockaddr_un>.size)) }
        }
        guard connected == 0 else { return ["ok": false, "error": "\(app) isn't running"] }
        var timeout = timeval(tv_sec: 10, tv_usec: 0)  // busy past this: no answer
        setsockopt(fd, SOL_SOCKET, SO_RCVTIMEO, &timeout, socklen_t(MemoryLayout<timeval>.size))
        var on: Int32 = 1  // the app may answer and hang up before all is sent (too much text): an error, not a crash
        setsockopt(fd, SOL_SOCKET, SO_NOSIGPIPE, &on, socklen_t(MemoryLayout<Int32>.size))
        var out = Data(body)
        out.append(0x0A)
        var sent = 0
        while sent < out.count {  // a large request goes in several writes
            let n = out.withUnsafeBytes { write(fd, $0.baseAddress! + sent, out.count - sent) }
            if n < 0 && errno == EINTR { continue }
            if n <= 0 { break }  // hung up: its answer, if any, is read below
            sent += n
        }
        var answer = Data()
        var chunk = [UInt8](repeating: 0, count: 65536)
        while answer.last != 0x0A {
            let n = read(fd, &chunk, chunk.count)
            if n <= 0 { break }
            answer.append(contentsOf: chunk[0..<n])
        }
        guard let parsed = try? JSONSerialization.jsonObject(with: answer) as? [String: Any] else {
            return ["ok": false, "error": answer.isEmpty ? "\(app) didn't answer" : "no answer from \(app)"]
        }
        return parsed
    }
}
