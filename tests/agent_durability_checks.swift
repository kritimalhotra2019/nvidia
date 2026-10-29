import Foundation

@main struct Checks {
    static func main() throws {
        var ledger = CloudApprovalLedger()
        let scope = "objective:one:https://api.example.test:model-a:ds-a"
        precondition(!ledger.permits(project:"p",scope:scope))
        ledger.approve(project:"p",scope:scope)
        var restored = try JSONDecoder().decode(CloudApprovalLedger.self,from:JSONEncoder().encode(ledger))
        precondition(restored.permits(project:"p",scope:scope))
        precondition(!restored.permits(project:"other",scope:scope))
        precondition(!restored.permits(project:"p",scope:scope+",ds-b"))
        precondition(!restored.permits(project:"p",scope:scope.replacingOccurrences(of:"model-a",with:"model-b")))
        restored.revoke(project:"p");precondition(!restored.permits(project:"p",scope:scope))
        let temp=FileManager.default.temporaryDirectory.appendingPathComponent(UUID().uuidString)
        defer { try? FileManager.default.removeItem(at:temp) }
        let root=temp.appendingPathComponent("granted"), other=temp.appendingPathComponent("granted-other")
        try FileManager.default.createDirectory(at:root,withIntermediateDirectories:true)
        try FileManager.default.createDirectory(at:other,withIntermediateDirectories:true)
        let file=root.appendingPathComponent("input.txt");try Data("abc".utf8).write(to:file)
        precondition(LocalFilePolicy.contains(root:root,candidate:file))
        precondition(!LocalFilePolicy.contains(root:root,candidate:other.appendingPathComponent("private.txt")))
        let link=root.appendingPathComponent("escape");try FileManager.default.createSymbolicLink(at:link,withDestinationURL:other)
        precondition(!LocalFilePolicy.contains(root:root,candidate:link.appendingPathComponent("private.txt")))
        let old=try LocalFilePolicy.digest(file);try Data("xyz".utf8).write(to:file)
        let changed = try LocalFilePolicy.digest(file)
        precondition(changed != old)
        let downloads = root.appendingPathComponent("Downloads")
        try FileManager.default.createDirectory(at: downloads, withIntermediateDirectories: true)
        for i in 0..<650 { try Data().write(to: downloads.appendingPathComponent(String(format: "A%04d.txt", i))) }
        try Data("fixture".utf8).write(to: downloads.appendingPathComponent("WFP-unique.PDF"))
        let first = try LocalFilePolicy.directoryListing(downloads, limit: 300)
        precondition(first["returned"] as? Int == 300 && first["next_offset"] as? Int == 300)
        let last = try LocalFilePolicy.directoryListing(downloads, offset: 600, limit: 100)
        precondition(last["returned"] as? Int == 51 && last["has_more"] as? Bool == false)
        let found = try LocalFilePolicy.directoryListing(downloads, query: "wfp", extensions: ["pdf"])
        precondition(found["matched_total"] as? Int == 1 && found["has_more"] as? Bool == false)
        precondition((found["files"] as? [[String: Any]])?.first?["name"] as? String == "WFP-unique.PDF")
        let absent = try LocalFilePolicy.directoryListing(downloads, query: "not-present", extensions: ["pdf"])
        precondition(absent["matched_total"] as? Int == 0 && absent["has_more"] as? Bool == false)
        let multipleTerms = try LocalFilePolicy.directoryListing(downloads, query: "wfp unique", extensions: [".PDF"])
        precondition(multipleTerms["matched_total"] as? Int == 1)
        // Ranking by matched words: "J-PAL impact evaluation methods" must surface the methods PDF (3 of 4 words) and
        // leave the brochure (1 word) out, instead of returning nothing because no name carries every word.
        try Data("x".utf8).write(to: downloads.appendingPathComponent("2016.08.31-Impact-Evaluation-Methods.pdf"))
        try Data("x".utf8).write(to: downloads.appendingPathComponent("J-PAL-brochure-2024.pdf"))
        let ranked = try LocalFilePolicy.directoryListing(downloads, query: "J-PAL impact evaluation methods", extensions: ["pdf"])
        precondition(ranked["matched_total"] as? Int == 1, "ranked listing: \(ranked["matched_total"] ?? "nil")")
        precondition((ranked["files"] as? [[String: Any]])?.first?["name"] as? String == "2016.08.31-Impact-Evaluation-Methods.pdf")
        precondition((ranked["files"] as? [[String: Any]])?.first?["matched_terms"] as? Int == 3)
        print("PASS: persistent objective approval, changed-scope rejection, revocation, folder boundaries, symlink escape and changed-content checks")
    }
}
