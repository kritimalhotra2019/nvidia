import Foundation

/// The figures an answer or a model-written document may contain: numbers that appear in a tool
/// result, in a document span the host read, or in the user's own request. Anything else is
/// untraceable and is removed before the user sees it. Tolerances allow honest rounding
/// (25,000 for 24,989.4; 17 for 16.86) but not invention.
struct NumberLedger {
    private(set) var values: [Double] = []
    private(set) var literals: Set<String> = []
    private static let pattern = try! NSRegularExpression(pattern: #"(?<![\w.])\d[\d,]*(?:\.\d+)?"#)

    struct Found { let literal: String; let value: Double; let percent: Bool; let money: Bool; let decimals: Int }

    static func numbers(in text: String) -> [Found] {
        let ns = text as NSString
        return pattern.matches(in: text, range: NSRange(location: 0, length: ns.length)).compactMap { match in
            let literal = ns.substring(with: match.range)
            guard let value = Double(literal.replacingOccurrences(of: ",", with: "")) else { return nil }
            let after = match.range.location + match.range.length < ns.length ? ns.substring(with: NSRange(location: match.range.location + match.range.length, length: min(9, ns.length - match.range.location - match.range.length))) : ""
            let before = ns.substring(with: NSRange(location: max(0, match.range.location - 6), length: min(6, match.range.location)))
            let afterTrimmed = after.trimmingCharacters(in: .whitespaces).lowercased()
            let percent = afterTrimmed.hasPrefix("%") || afterTrimmed.hasPrefix("percent") || afterTrimmed.hasPrefix("per cent")
            let money = before.contains("$") || before.contains("€") || before.contains("£") || before.contains("₹") || before.uppercased().contains("USD") || before.uppercased().contains("INR") || before.uppercased().contains("EUR") || before.uppercased().contains("GBP")
            let decimals = literal.contains(".") ? literal.split(separator: ".").last!.count : 0
            return Found(literal: literal, value: value, percent: percent, money: money, decimals: decimals)
        }
    }

    mutating func add(text: String) {
        for found in Self.numbers(in: text) { values.append(found.value); literals.insert(found.literal.replacingOccurrences(of: ",", with: "")) }
    }
    mutating func add(json: Any, depth: Int = 0) {
        guard depth < 8 else { return }
        switch json {
        case let n as NSNumber: values.append(n.doubleValue)
        case let s as String: add(text: s)
        case let list as [Any]: for item in list.prefix(400) { add(json: item, depth: depth + 1) }
        case let dict as [String: Any]: for (_, v) in dict { add(json: v, depth: depth + 1) }
        default: break
        }
    }

    func traceable(_ f: Found) -> Bool {
        if literals.contains(f.literal.replacingOccurrences(of: ",", with: "")) { return true }
        if f.decimals == 0, f.literal.count == 4, f.value >= 1900, f.value <= 2100 { return true }      // a year
        if f.decimals == 0, !f.percent, !f.money, abs(f.value) <= 12 { return true }                     // "three rounds", "2 charts"
        let tolerance = max(0.5 * pow(10.0, -Double(f.decimals)), abs(f.value) * 0.006)
        return values.contains { abs($0 - f.value) <= tolerance }
    }

    /// Percentages on one line that the host can verify from two traceable counts on that line
    /// ("13 of 518 cases (2.5%)", "13/518 = 3%", "Of 518 cases, 13 (2.5%)"). Verified arithmetic is not
    /// invention: the ledger has checked the figure instead of removing the one sentence that answers the
    /// question (27 Oct 2026, adverse-possession case review). Any ordered pair of traceable whole counts
    /// on the line may be the fraction; the stated figure must match to the precision written.
    func verifiedPercents(in line: String) -> Set<String> {
        let found = Self.numbers(in: line)
        let percents = found.filter(\.percent)
        guard !percents.isEmpty else { return [] }
        let counts = found.filter { !$0.percent && !$0.money && $0.decimals == 0 && $0.value >= 0 && traceable($0) }.prefix(12)
        guard counts.count >= 2 else { return [] }
        var verified: Set<String> = []
        for p in percents {
            let tolerance = 0.5 * pow(10.0, -Double(p.decimals)) + 0.000001
            for a in counts { for b in counts where b.value > 0 && a.value <= b.value && a.literal != b.literal {
                if abs(100 * a.value / b.value - p.value) <= tolerance { verified.insert(p.literal) }
            } }
        }
        return verified
    }

    func untraceable(in text: String) -> [String] {
        var out: [String] = []
        for line in text.components(separatedBy: "\n") {
            let verified = verifiedPercents(in: line)
            for f in Self.numbers(in: line) where !traceable(f) && !verified.contains(f.literal) { if !out.contains(f.literal) { out.append(f.literal) } }
        }
        return out
    }

    /// Markdown lines are the unit so formatting survives; a line with any untraceable figure is removed.
    func filter(_ text: String) -> (text: String, dropped: Int, removed: [String]) {
        var kept: [String] = []; var dropped = 0; var removed: [String] = []
        for line in text.components(separatedBy: "\n") {
            let bad = untraceable(in: line)
            if bad.isEmpty { kept.append(line) } else { dropped += 1; removed.append(contentsOf: bad) }
        }
        return (kept.joined(separator: "\n"), dropped, Array(NSOrderedSet(array: removed)).compactMap { $0 as? String })
    }

    static func build(records: [[String: Any]], spans: String, instruction: String) -> NumberLedger {
        var ledger = NumberLedger()
        ledger.add(text: instruction)
        ledger.add(text: spans)
        for record in records where record["ok"] as? Bool == true {
            if let result = record["result"] { ledger.add(json: result) }
        }
        return ledger
    }
}
