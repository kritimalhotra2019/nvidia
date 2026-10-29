import Foundation

/// Every factual line of a document answer must point at a span the host actually read. The number
/// ledger already stops invented figures; this closes the gap for wordy claims ("one section mentioned
/// no income effect") by requiring a source tag per line and checking the line's distinctive words
/// against that span's text and verified note. Lines without a supporting span are removed and
/// counted; the user sees the tags and a source list, so every statement can be checked by offset.
enum Citations {
    struct Span {
        let tag: String      // S1, S2, …
        let id: String       // document id
        let start: Int
        let end: Int
        let text: String     // the span as read
        let note: String     // the verified reading note for the span
        var haystack: String { (text + "\n" + note).lowercased().replacingOccurrences(of: ",", with: "") }
    }

    struct Result {
        var text: String
        var dropped: Int
        var attributed: Int
        var removed: [String]
    }

    static let stopwords: Set<String> = ["the", "and", "that", "this", "with", "from", "have", "were", "which", "their", "there", "these", "those", "about", "after", "before", "between", "within", "while", "where", "when", "also", "than", "then", "them", "they", "been", "being", "into", "over", "under", "report", "document", "section", "authors", "study", "evaluation", "programme", "program", "households", "household", "treatment", "comparison", "control", "villages", "village", "results", "findings", "effect", "effects", "percent", "percentage", "estimate", "estimates", "overall", "however", "because", "should", "would", "could", "across", "during", "through", "without", "against", "according", "including", "following", "specific", "specifically", "respectively", "significant", "significantly", "evidence", "analysis", "approach", "design", "method", "methods", "page", "pages", "scope", "spans", "matching", "question", "whole"]

    static let tagPattern = try! NSRegularExpression(pattern: #"\[(S\d+(?:\s*,\s*S\d+)*)\]"#)
    static let numberPattern = try! NSRegularExpression(pattern: #"\d[\d,]*(?:\.\d+)?"#)

    static func numbers(_ text: String) -> [String] {
        numberPattern.matches(in: text, range: NSRange(text.startIndex..., in: text)).compactMap { Range($0.range, in: text).map { String(text[$0]).replacingOccurrences(of: ",", with: "") } }
    }

    static func terms(_ text: String) -> [String] {
        let words = text.lowercased().components(separatedBy: CharacterSet.letters.inverted)
        var seen = Set<String>()
        return words.filter { $0.count >= 6 && !stopwords.contains($0) && seen.insert($0).inserted }
    }

    /// A line is factual when it carries a figure, or enough words and distinctive terms to make a
    /// claim. Headings, lead-ins ending in a colon and connective sentences are left alone.
    static func isFactual(_ line: String) -> Bool {
        let stripped = line.trimmingCharacters(in: .whitespaces)
        if stripped.isEmpty || stripped.hasPrefix("#") || stripped.hasPrefix("Scope:") || stripped.hasPrefix("Sources:") { return false }
        let bare = tagPattern.stringByReplacingMatches(in: stripped, range: NSRange(stripped.startIndex..., in: stripped), withTemplate: "").trimmingCharacters(in: .whitespaces)
        if bare.hasSuffix(":") { return false }
        if !numbers(bare).isEmpty { return true }
        let wordCount = bare.split(whereSeparator: { $0 == " " }).count
        return wordCount >= 8 && terms(bare).count >= 2
    }

    /// Does the span support the line? All of the line's figures occur in the span (years and the
    /// tag numbers aside) and at least one distinctive word does, or the line has no distinctive words.
    static func supports(_ span: Span, line: String) -> Bool { supports(haystack: span.haystack, line: line) }

    /// A sentence may combine facts from several spans ("11.6% [S4] … Lee bounds 6–12% [S5]"); the
    /// cited spans are then checked together.
    static func supports(_ spans: [Span], line: String) -> Bool {
        guard !spans.isEmpty else { return false }
        return supports(haystack: spans.map(\.haystack).joined(separator: "\n"), line: line)
    }

    /// A figure is present when it occurs verbatim or when a number in the haystack rounds to it
    /// (the tool reported 851.85, the answer says 851.9; the span says 11.6, the answer says 12).
    static func figurePresent(_ figure: String, in hay: String) -> Bool {
        if hay.contains(figure) { return true }
        guard let value = Double(figure) else { return false }
        let decimals = figure.split(separator: ".").count == 2 ? figure.split(separator: ".")[1].count : 0
        let tolerance = 0.5 * pow(10.0, Double(-decimals)) + 1e-9
        for match in numberPattern.matches(in: hay, range: NSRange(hay.startIndex..., in: hay)) {
            guard let r = Range(match.range, in: hay), let other = Double(hay[r].replacingOccurrences(of: ",", with: "")) else { continue }
            if abs(other - value) <= tolerance { return true }
        }
        return false
    }

    static func supports(haystack hay: String, line: String) -> Bool {
        let lineWithoutTags = tagPattern.stringByReplacingMatches(in: line, range: NSRange(line.startIndex..., in: line), withTemplate: "")
        let figures = numbers(lineWithoutTags).filter { !($0.count == 4 && $0.hasPrefix("20")) && $0 != "1" && $0 != "2" && $0 != "3" }
        guard figures.allSatisfy({ figurePresent($0, in: hay) }) else { return false }
        let words = terms(lineWithoutTags)
        if words.isEmpty { return true }
        let hits = words.filter { word in hay.contains(word) || hay.contains(String(word.dropLast(2))) }
        return hits.count >= min(2, words.count)
    }

    static func verify(answer: String, spans: [Span]) -> Result {
        guard !spans.isEmpty else { return Result(text: answer, dropped: 0, attributed: 0, removed: []) }
        let byTag = Dictionary(uniqueKeysWithValues: spans.map { ($0.tag, $0) })
        var kept: [String] = []
        var dropped = 0, attributed = 0
        var removed: [String] = []
        var used = Set<String>()
        for line in answer.components(separatedBy: "\n") {
            guard isFactual(line) else { kept.append(line); continue }
            let range = NSRange(line.startIndex..., in: line)
            let cited = tagPattern.matches(in: line, range: range).flatMap { match -> [String] in
                guard let r = Range(match.range(at: 1), in: line) else { return [] }
                return line[r].components(separatedBy: ",").map { $0.trimmingCharacters(in: .whitespaces) }
            }
            let citedSpans = cited.compactMap { byTag[$0] }
            if !citedSpans.isEmpty, supports(citedSpans, line: line) {
                kept.append(line); used.formUnion(citedSpans.map(\.tag)); continue
            }
            func cleaned() -> String {
                var clean = tagPattern.stringByReplacingMatches(in: line, range: range, withTemplate: "")
                for (from, to) in [(" .", "."), (" ,", ","), ("  ", " ")] { clean = clean.replacingOccurrences(of: from, with: to) }
                return clean.trimmingCharacters(in: .whitespaces)
            }
            // No valid citation: attribute to the single span that supports the line; failing that, to the
            // set of read spans that together hold every figure and word (a sentence combining two spans).
            if let match = spans.first(where: { supports($0, line: line) }) {
                kept.append(cleaned() + " [\(match.tag)]"); used.insert(match.tag); attributed += 1
            } else if supports(spans, line: line) {
                let figures = numbers(tagPattern.stringByReplacingMatches(in: line, range: range, withTemplate: ""))
                var holders = spans.filter { span in figures.contains { span.haystack.contains($0) } }.map(\.tag)
                if holders.isEmpty { holders = spans.filter { supports(haystack: $0.haystack, line: line.replacingOccurrences(of: #"\d[\d,]*(?:\.\d+)?"#, with: "", options: .regularExpression)) }.map(\.tag) }
                let tags = Array(holders.prefix(3))
                kept.append(cleaned() + " [\(tags.joined(separator: ", "))]"); used.formUnion(tags); attributed += 1
            } else {
                dropped += 1
                removed.append(String(line.trimmingCharacters(in: .whitespaces).prefix(80)))
            }
        }
        var text = kept.joined(separator: "\n").replacingOccurrences(of: "\n\n\n", with: "\n\n")
        if !used.isEmpty {
            let list = spans.filter { used.contains($0.tag) }.map { $0.start == 0 && $0.end == 0 ? "\($0.tag) = \($0.id) result" : "\($0.tag) = \($0.id) characters \($0.start)–\($0.end)" }
            text += "\n\nSources: " + list.joined(separator: "; ")
        }
        return Result(text: text, dropped: dropped, attributed: attributed, removed: removed)
    }

    /// A small model sometimes thinks aloud in its answer ("Wait, let me re-check…", "Hmm, this contradicts my
    /// calculation"). Those lines are deliberation, not findings; they are removed before the user sees the answer.
    static let deliberationPattern = try! NSRegularExpression(pattern: #"^\s*(?:[*_>-]+\s*)?(?:wait\b|hmm\b|okay,? (?:so|let|i)|ok,? (?:so|let)|let me (?:re-?check|re-?read|think|look|verify|see|recalculate|double-check)|actually,|alright,|right then|hold on|this contradicts my|i need to (?:re-?check|re-?read|verify)|unless .{1,40}? means|maybe it means|why would)"#, options: [.caseInsensitive])

    static func stripDeliberation(_ answer: String) -> (text: String, removed: Int) {
        var kept: [String] = []
        var removed = 0
        for line in answer.components(separatedBy: "\n") {
            let range = NSRange(line.startIndex..., in: line)
            if deliberationPattern.firstMatch(in: line, range: range) != nil { removed += 1; continue }
            // Deliberation can also trail a real sentence ("… non-dominated. Let me double-check this manually:").
            // Sentences are kept or dropped one by one; table rows and headings are left whole.
            if line.hasPrefix("|") || line.hasPrefix("#") || !line.contains(". ") { kept.append(line); continue }
            let sentences = line.components(separatedBy: ". ")
            let calm = sentences.filter { sentence in
                let s = sentence.trimmingCharacters(in: .whitespaces)
                return deliberationPattern.firstMatch(in: s, range: NSRange(s.startIndex..., in: s)) == nil
            }
            if calm.count < sentences.count { removed += sentences.count - calm.count }
            if !calm.isEmpty { kept.append(calm.joined(separator: ". ")) }
        }
        return (kept.joined(separator: "\n").replacingOccurrences(of: "\n\n\n", with: "\n\n"), removed)
    }

    /// The instruction appended to reading context so the model cites as it writes.
    static let instruction = "Cite the source tag in square brackets at the end of every sentence or bullet that states a fact, for example \"… 11.6% [S3]\". Use only the tags listed above. Sentences whose facts cannot be matched to the cited span are removed by the host before the user sees them."
}
