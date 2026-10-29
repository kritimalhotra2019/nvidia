import Foundation

/// Separate from cloud settings: this backend can only contact this Mac.
enum MLXConfiguration {
    static let baseURL = "http://127.0.0.1:8766/v1"
    static let modelID = "mlx-community/Qwen3.5-9B-4bit"
    static var root: URL {
        FileManager.default.urls(for: .applicationSupportDirectory, in: .userDomainMask)[0]
            .appendingPathComponent("Squipi", isDirectory: true)
    }
    static var python: URL { root.appendingPathComponent("mlx-env/bin/python") }
    static var modelDirectory: URL { root.appendingPathComponent("mlx-model", isDirectory: true) }
    static var installed: Bool {
        FileManager.default.isExecutableFile(atPath: python.path) &&
        FileManager.default.fileExists(atPath: modelDirectory.appendingPathComponent("installation.json").path)
    }
    static func validateEndpoint(_ text: String) throws {
        guard let u = URLComponents(string: text), u.scheme == "http",
              ["127.0.0.1", "localhost", "::1", "[::1]"].contains(u.host?.lowercased() ?? ""),
              u.user == nil, u.password == nil, u.query == nil, u.fragment == nil,
              ["/v1", "/v1/"].contains(u.path) else {
            throw NSError(domain: "Squipi.MLX", code: 1, userInfo: [NSLocalizedDescriptionKey: "MLX requires an HTTP loopback address ending in /v1. It cannot send requests to a remote server."])
        }
    }
}

/// A loopback server must not redirect private prompts to another host.
final class MLXNoRedirect: NSObject, URLSessionTaskDelegate, @unchecked Sendable {
    func urlSession(_ session: URLSession, task: URLSessionTask,
                    willPerformHTTPRedirection response: HTTPURLResponse, newRequest request: URLRequest,
                    completionHandler: @escaping (URLRequest?) -> Void) { completionHandler(nil) }
}
