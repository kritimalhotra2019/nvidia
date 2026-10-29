import Foundation
@main struct Checks {
    static func main() throws {
        for url in ["http://127.0.0.1:8766/v1", "http://localhost:1234/v1/", "http://[::1]:8766/v1"] { try MLXConfiguration.validateEndpoint(url) }
        for url in ["https://api.example.com/v1", "http://example.com/v1", "http://127.0.0.1.evil.test/v1", "http://user:secret@localhost/v1", "http://localhost/v1?key=secret", "http://localhost/v1#fragment", "file:///v1", "http://localhost/other", "http://localhost\\@evil.test/v1"] {
            do { try MLXConfiguration.validateEndpoint(url); fatalError("Accepted unsafe MLX endpoint") } catch { }
        }
        print("12 MLX loopback endpoint checks passed")
    }
}
