import Foundation

/// Guards around schema-guided argument repair. The repairing model may only fix structure; any path,
/// identifier, filename or long literal that was in the rejected arguments must survive unchanged
/// (on 26 Oct a repair dropped one character from a chart path, which the worker then rejected).
enum ArgumentRepair {
    static let identifier = try! NSRegularExpression(pattern: #"^(?:data|doc|analysis|chart|draft|task|objective|tpl|template)_[0-9a-fA-F-]{4,}$"#)

    /// Strings the repair must preserve verbatim: absolute paths, worker ids, filenames with an extension,
    /// and any literal of 24+ characters (prose the user wrote or a tool returned).
    static func protectedStrings(in value: Any) -> [String] {
        var out: [String] = []
        func walk(_ v: Any) {
            if let s = v as? String {
                let t = s.trimmingCharacters(in: .whitespacesAndNewlines)
                guard !t.isEmpty else { return }
                let isPath = t.hasPrefix("/") || t.hasPrefix("~/")
                let isID = identifier.firstMatch(in: t, range: NSRange(t.startIndex..., in: t)) != nil
                let isFilename = t.range(of: #"^[^/\n]{1,120}\.(?:docx|pptx|xlsx|csv|pdf|md|txt|html|svg|png|json)$"#, options: [.regularExpression, .caseInsensitive]) != nil
                if isPath || isID || isFilename { out.append(t); return }
                // Prose may be re-structured (a "## Heading\nBody" string split into heading and text), so
                // each sentence is protected rather than the block as a whole; markdown markers are ignored.
                for raw in t.components(separatedBy: CharacterSet.newlines).flatMap({ $0.components(separatedBy: ". ") }) {
                    let sentence = raw.trimmingCharacters(in: CharacterSet(charactersIn: "#*-•> \t")).trimmingCharacters(in: .whitespaces)
                    if sentence.count >= 24 { out.append(sentence) }
                }
            } else if let a = v as? [Any] { a.forEach(walk) }
            else if let d = v as? [String: Any] { d.values.forEach(walk) }
        }
        walk(value)
        return out
    }

    /// True when every protected string of the original arguments occurs verbatim in the repaired JSON.
    static func preservesContent(original: [String: Any], repairedJSON: String) -> Bool {
        let protected = protectedStrings(in: original)
        guard !protected.isEmpty else { return true }
        // JSON escaping can alter quotes and slashes; compare on an unescaped, whitespace-normalised form.
        let haystack = repairedJSON.replacingOccurrences(of: "\\/", with: "/").replacingOccurrences(of: "\\\"", with: "\"")
            .replacingOccurrences(of: "\\n", with: "\n").replacingOccurrences(of: "\\t", with: "\t")
        let squashed = haystack.components(separatedBy: .whitespacesAndNewlines).filter { !$0.isEmpty }.joined(separator: " ")
        for item in protected {
            let needle = item.components(separatedBy: .whitespacesAndNewlines).filter { !$0.isEmpty }.joined(separator: " ")
            if !squashed.contains(needle) { return false }
        }
        return true
    }

    /// Which protected strings went missing (for the log).
    static func missing(original: [String: Any], repairedJSON: String) -> [String] {
        let squashed = repairedJSON.replacingOccurrences(of: "\\/", with: "/").replacingOccurrences(of: "\\\"", with: "\"")
            .components(separatedBy: .whitespacesAndNewlines).filter { !$0.isEmpty }.joined(separator: " ")
        return protectedStrings(in: original).filter { !squashed.contains($0.components(separatedBy: .whitespacesAndNewlines).filter { !$0.isEmpty }.joined(separator: " ")) }
    }
}
