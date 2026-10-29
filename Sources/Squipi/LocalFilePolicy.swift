import Foundation
import CryptoKit

enum LocalFilePolicy {
    static func contains(root: URL, candidate: URL) -> Bool {
        let r = root.standardizedFileURL.resolvingSymlinksInPath().path
        // Foundation may leave the entire path unresolved when the final file
        // does not exist. Resolve its closest existing ancestor before appending
        // new destination components, so a symlinked parent cannot escape.
        var ancestor = candidate.standardizedFileURL
        var tail: [String] = []
        while !FileManager.default.fileExists(atPath: ancestor.path) {
            if (try? FileManager.default.destinationOfSymbolicLink(atPath: ancestor.path)) != nil { return false }
            guard ancestor.path != "/" else { return false }
            tail.insert(ancestor.lastPathComponent, at: 0)
            ancestor.deleteLastPathComponent()
        }
        var resolved = ancestor.resolvingSymlinksInPath()
        for component in tail { resolved.appendPathComponent(component) }
        let c = resolved.path
        return c == r || c.hasPrefix(r + "/")
    }
    static func directoryListing(_ root: URL, query: String = "", extensions: [String] = [], offset: Int = 0, limit: Int = 100, sort: String = "name", sinceDays: Int? = nil) throws -> [String: Any] {
        guard offset >= 0, (1...300).contains(limit), query.count <= 256, ["name", "newest", "oldest"].contains(sort), (sinceDays ?? 1) >= 1,
              extensions.count <= 20, extensions.allSatisfy({ $0.range(of: #"^\.?[a-zA-Z0-9]{1,12}$"#, options: .regularExpression) != nil }) else {
            throw NSError(domain: "Squipi directory listing", code: 1, userInfo: [NSLocalizedDescriptionKey: "Use offset >= 0, limit 1–300, a short query, plain file extensions, sort name|newest|oldest and since_days >= 1."])
        }
        let keys: Set<URLResourceKey> = [.isDirectoryKey, .fileSizeKey, .contentModificationDateKey, .addedToDirectoryDateKey]
        var files = try FileManager.default.contentsOfDirectory(at: root, includingPropertiesForKeys: Array(keys), options: [.skipsHiddenFiles])
        func stamp(_ url: URL) -> Date { let v = try? url.resourceValues(forKeys: keys); return v?.addedToDirectoryDate ?? v?.contentModificationDate ?? .distantPast }
        switch sort {
        case "newest": files.sort { stamp($0) > stamp($1) }
        case "oldest": files.sort { stamp($0) < stamp($1) }
        default: files.sort { $0.lastPathComponent < $1.lastPathComponent }
        }
        let cutoff = sinceDays.map { Date().addingTimeInterval(-Double($0) * 86_400) }
        // A query matches whole words in any order; a term that matches nothing is dropped (with a note) rather than
        // hiding the file: "J-PAL impact evaluation" still finds "2016.08.31-Impact-Evaluation-Methods.pdf".
        let folded = { (s: String) in s.folding(options: [.caseInsensitive, .diacriticInsensitive], locale: Locale(identifier: "en_US_POSIX")) }
        let terms = folded(query).split(whereSeparator: { !$0.isLetter && !$0.isNumber }).map(String.init).filter { $0.count >= 2 }
        let types = Set(extensions.map { $0.lowercased().trimmingCharacters(in: CharacterSet(charactersIn: ".")) })
        let candidates = files.filter { url in (types.isEmpty || types.contains(url.pathExtension.lowercased())) && (cutoff.map { stamp(url) >= $0 } ?? true) }
        let usefulTerms = terms.filter { term in candidates.contains { folded($0.lastPathComponent).contains(term) } }
        let droppedTerms = terms.filter { !usefulTerms.contains($0) }
        // Files are ranked by how many query words they carry (a file with three of four words outranks one with
        // one), then by the requested order. Requiring every word returned nothing for "J-PAL impact evaluation
        // methods" against "2016.08.31-Impact-Evaluation-Methods.pdf" (27 Oct); a file needs at least half the
        // useful words, or one when nothing reaches that.
        func hits(_ url: URL) -> Int { let name = folded(url.lastPathComponent); return usefulTerms.filter(name.contains).count }
        var matched = candidates
        if !terms.isEmpty, usefulTerms.isEmpty {
            matched = []                                   // a query that matches no name at all finds nothing
        } else if !usefulTerms.isEmpty {
            let need = max(1, (usefulTerms.count + 1) / 2)
            let scored = candidates.map { ($0, hits($0)) }.filter { $0.1 >= 1 }
            let strong = scored.filter { $0.1 >= need }
            matched = (strong.isEmpty ? scored : strong).enumerated().sorted { a, b in a.element.1 != b.element.1 ? a.element.1 > b.element.1 : a.offset < b.offset }.map { $0.element.0 }
        }
        let start = min(offset, matched.count), end = min(start + limit, matched.count)
        let f = ISO8601DateFormatter()
        return ["path": root.path, "total": files.count, "matched_total": matched.count, "query": query, "sort": sort, "since_days": sinceDays as Any,
                "ignored_query_terms": droppedTerms, "extensions": Array(types).sorted(), "offset": start, "returned": end - start,
                "next_offset": end < matched.count ? end as Any : NSNull(), "has_more": end < matched.count,
                "truncated": end < matched.count, "search_scope": "All immediate filenames were searched before pagination; no recursive scan.",
                "summary_text": "Found \(matched.count) matching files among \(files.count); returned \(end-start), offset \(start)." + (usefulTerms.count > 1 ? " Ranked by how many of the \(usefulTerms.count) query words the name carries (matched_terms per file)." : "") + (end < matched.count ? " Continue with next_offset or narrow the filename query." : ""),
                "files": matched[start..<end].map { url -> [String: Any] in
                    let v = try? url.resourceValues(forKeys: keys)
                    var row: [String: Any] = ["name": url.lastPathComponent, "path": url.path, "directory": v?.isDirectory ?? false, "bytes": v?.fileSize ?? 0,
                            "added": f.string(from: stamp(url)), "modified": (v?.contentModificationDate).map { f.string(from: $0) } ?? ""]
                    if usefulTerms.count > 1 { row["matched_terms"] = hits(url) }
                    return row
                }]
    }

    static func digest(_ url: URL) throws -> String {
        let stream = try FileHandle(forReadingFrom: url); defer { try? stream.close() }
        var hash = SHA256()
        while let bytes = try stream.read(upToCount: 1_048_576), !bytes.isEmpty { hash.update(data: bytes) }
        return hash.finalize().map { String(format:"%02x",$0) }.joined()
    }
}
