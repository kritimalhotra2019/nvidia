import Foundation
import CryptoKit

/// Consent identity excludes changing conversation text, but includes the native
/// objective, provider/model and selected input scope supplied by the caller.
/// A project-objective approval expires after `lifetime`; it is not forever.
struct CloudApprovalLedger: Codable {
    static let lifetime: TimeInterval = 7 * 24 * 3600
    var grants: [String: [String: Double]] = [:]     // project → scope hash → approved-at (unix seconds)
    init() {}
    init(from decoder: Decoder) throws {
        let container = try decoder.container(keyedBy: CodingKeys.self)
        if let dated = try? container.decode([String: [String: Double]].self, forKey: .grants) { grants = dated; return }
        // Older ledgers stored undated sets; treat them as approved now so they expire on schedule.
        if let sets = try? container.decode([String: Set<String>].self, forKey: .grants) {
            let now = Date().timeIntervalSince1970
            grants = sets.mapValues { Dictionary(uniqueKeysWithValues: $0.map { ($0, now) }) }
        }
    }
    static func key(_ scope: String) -> String { SHA256.hash(data: Data(scope.utf8)).map { String(format: "%02x", $0) }.joined() }
    func permits(project: String, scope: String, now: Date = Date()) -> Bool {
        guard let at = grants[project]?[Self.key(scope)] else { return false }
        return now.timeIntervalSince1970 - at < Self.lifetime
    }
    mutating func approve(project: String, scope: String, at: Date = Date()) { grants[project, default: [:]][Self.key(scope)] = at.timeIntervalSince1970 }
    mutating func revoke(project: String) { grants[project] = nil }
}
