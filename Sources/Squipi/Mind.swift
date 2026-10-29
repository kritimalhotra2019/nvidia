import Foundation
import Combine
import CoreFoundation

struct Reminder: Codable, Identifiable, Equatable {
    var id = UUID()
    var text: String
    var due: Date?
    var done = false
    var created = Date()
    var breakGame: Bool? = nil

    func isDue(at now: Date) -> Bool { !done && (due.map { $0 <= now } ?? false) }
}

/// Shared, clock-injectable validation for native reminder tools. Stored dates are absolute instants.
enum ReminderSchedule {
    struct InvalidTime: LocalizedError {
        let message: String
        var errorDescription: String? { message }
    }

    static func resolve(arguments: [String: Any], now: Date = Date(), timeZone: TimeZone = .current) throws -> Date? {
        let minutes = arguments["in_minutes"].flatMap { $0 is NSNull ? nil : $0 }
        let due = arguments["due"].flatMap { $0 is NSNull ? nil : $0 }
        guard minutes == nil || due == nil else { throw InvalidTime(message: "Use either in_minutes or due, not both.") }
        if let minutes {
            guard let number = minutes as? NSNumber, CFGetTypeID(number) != CFBooleanGetTypeID(),
                  number.doubleValue.isFinite, number.doubleValue >= 0,
                  (number.doubleValue * 60).isFinite else {
                throw InvalidTime(message: "in_minutes must be a finite, nonnegative number.")
            }
            return now.addingTimeInterval(number.doubleValue * 60)
        }
        guard let due else { return nil }
        guard let text = due as? String else { throw InvalidTime(message: "due must be a date and time string.") }
        let pattern = #"^(\d{4})-(\d{2})-(\d{2})[ T](\d{2}):(\d{2})(?::(\d{2})(?:\.(\d{1,6}))?)?(Z|[+-]\d{2}:\d{2})?$"#
        let regex = try NSRegularExpression(pattern: pattern)
        let range = NSRange(text.startIndex..<text.endIndex, in: text)
        guard let match = regex.firstMatch(in: text, range: range) else {
            throw InvalidTime(message: "due must be 'YYYY-MM-DD HH:MM' local time or an ISO datetime with a UTC offset.")
        }
        func part(_ index: Int) -> String? { Range(match.range(at: index), in: text).map { String(text[$0]) } }
        let fields = DateComponents(year: Int(part(1)!), month: Int(part(2)!), day: Int(part(3)!),
                                    hour: Int(part(4)!), minute: Int(part(5)!), second: Int(part(6) ?? "0"))
        var calendar = Calendar(identifier: .gregorian)
        calendar.timeZone = timeZone
        if let offset = part(8) {
            if offset == "Z" { calendar.timeZone = TimeZone(secondsFromGMT: 0)! }
            else {
                let pieces = offset.dropFirst().split(separator: ":").compactMap { Int($0) }
                guard pieces.count == 2, pieces[0] <= 23, pieces[1] < 60,
                      let zone = TimeZone(secondsFromGMT: (offset.first == "-" ? -1 : 1) * (pieces[0] * 3600 + pieces[1] * 60)) else {
                    throw InvalidTime(message: "Invalid UTC offset in due.")
                }
                calendar.timeZone = zone
            }
        }
        let units: Set<Calendar.Component> = [.year, .month, .day, .hour, .minute, .second]
        guard let candidate = calendar.date(from: fields), calendar.dateComponents(units, from: candidate) == fields else {
            throw InvalidTime(message: "due is not a valid local time; it may fall in a daylight-saving gap.")
        }
        var instant = candidate
        if part(8) == nil {
            let start = calendar.startOfDay(for: candidate).addingTimeInterval(-1)
            guard let first = calendar.nextDate(after: start, matching: fields, matchingPolicy: .strict, repeatedTimePolicy: .first),
                  let last = calendar.nextDate(after: start, matching: fields, matchingPolicy: .strict, repeatedTimePolicy: .last) else {
                throw InvalidTime(message: "due is not a valid local time.")
            }
            guard first == last else {
                throw InvalidTime(message: "This local time occurs twice during a daylight-saving change; include an explicit UTC offset.")
            }
            instant = first
        }
        if let fraction = part(7), let value = Double("0." + fraction) { instant = instant.addingTimeInterval(value) }
        guard instant >= now.addingTimeInterval(-60) else { throw InvalidTime(message: "due is in the past; choose a future time.") }
        return instant
    }
}

/// What the pet remembers about you, plus reminders it's holding. JSON in Application Support.
@MainActor
final class Mind: ObservableObject {
    static let shared = Mind(url: Store.shared.dir.appendingPathComponent("mind.json"))
    @Published var facts: [String] = []
    @Published var reminders: [Reminder] = []
    @Published private(set) var persistenceError: String?
    private let url: URL
    private let now: () -> Date
    private let writeData: (Data, URL) throws -> Void
    private struct Snapshot { var facts: [String]; var reminders: [Reminder] }

    /// Inject a temporary file and clock for tests; the app uses the singleton above.
    init(url: URL, now: @escaping () -> Date = Date.init,
         writeData: @escaping (Data, URL) throws -> Void = { try $0.write(to: $1, options: .atomic) }) {
        self.url = url; self.now = now; self.writeData = writeData
        reload()
    }

    private func read() throws -> Snapshot {
        guard FileManager.default.fileExists(atPath: url.path) else { return Snapshot(facts: [], reminders: []) }
        let decoder = JSONDecoder()
        let stored = try decoder.decode([String: Data].self, from: Data(contentsOf: url))
        return Snapshot(facts: try stored["facts"].map { try decoder.decode([String].self, from: $0) } ?? [],
                        reminders: try stored["reminders"].map { try decoder.decode([Reminder].self, from: $0) } ?? [])
    }

    /// Re-read before edits so a completed Hermes write is not overwritten by stale in-memory state.
    func reload() {
        do { publish(try read()) } catch { persistenceError = error.localizedDescription }
    }

    private func publish(_ snapshot: Snapshot) {
        facts = snapshot.facts; reminders = snapshot.reminders; persistenceError = nil
    }

    private func save(_ snapshot: Snapshot) throws {
        let encoder = JSONEncoder(); encoder.outputFormatting = [.prettyPrinted, .sortedKeys]
        let data = try encoder.encode(["facts": encoder.encode(snapshot.facts), "reminders": encoder.encode(snapshot.reminders)])
        try FileManager.default.createDirectory(at: url.deletingLastPathComponent(), withIntermediateDirectories: true)
        try writeData(data, url)
        publish(snapshot)  // Never mark a reminder done in memory before its claim is durable.
    }

    private func edit(_ change: (inout Snapshot) -> Bool) -> Bool {
        do {
            var snapshot = try read()
            guard change(&snapshot) else { return false }
            try save(snapshot)
            return true
        } catch { persistenceError = error.localizedDescription; return false }
    }

    func addFacts(_ new: [String]) {
        _ = edit { state in
            for f in new {
                let t = f.trimmingCharacters(in: .whitespacesAndNewlines)
                guard t.count > 3, !state.facts.contains(where: { $0.caseInsensitiveCompare(t) == .orderedSame }) else { continue }
                state.facts.append(t)
            }
            if state.facts.count > 60 { state.facts.removeFirst(state.facts.count - 60) }
            return true
        }
    }
    func forget(_ fact: String) { _ = edit { $0.facts.removeAll { $0 == fact }; return true } }
    @discardableResult func addReminder(_ text: String, due: Date?, breakGame: Bool = false) -> Bool {
        let t = text.trimmingCharacters(in: .whitespacesAndNewlines)
        guard !t.isEmpty else { return false }
        return edit { $0.reminders.append(Reminder(text: t, due: due, created: now(), breakGame: breakGame ? true : nil)); return true }
    }
    func update(_ r: Reminder) {
        _ = edit { state in
            guard let i = state.reminders.firstIndex(where: { $0.id == r.id }) else { return false }
            state.reminders[i] = r; return true
        }
    }
    func remove(_ r: Reminder) { _ = edit { $0.reminders.removeAll { $0.id == r.id }; return true } }
    func forgetEverything() { _ = edit { $0 = Snapshot(facts: [], reminders: []); return true } }

    var dueNow: [Reminder] { reminders.filter { $0.isDue(at: now()) } }

    /// Claim an entire due batch once, including reminders missed while the app was closed.
    /// Durable claims prevent repeat delivery after restart; they cannot prove speech was heard.
    func claimDue(at date: Date? = nil) throws -> [Reminder] {
        do {
            var snapshot = try read()
            let instant = date ?? now()
            let batch = snapshot.reminders.filter { $0.isDue(at: instant) }.sorted { $0.due! < $1.due! }
            if batch.isEmpty { publish(snapshot); return [] }
            let ids = Set(batch.map(\.id))
            for index in snapshot.reminders.indices where ids.contains(snapshot.reminders[index].id) { snapshot.reminders[index].done = true }
            try save(snapshot)
            return batch
        } catch { persistenceError = error.localizedDescription; throw error }
    }

    /// Text the pet sees in its system prompt.
    func promptSection() -> String {
        var s = ""
        if !facts.isEmpty { s += "\nThings I remember about \(Brain.userName): " + facts.suffix(25).joined(separator: "; ") + "." }
        let open = reminders.filter { !$0.done }
        if !open.isEmpty {
            let f = DateFormatter(); f.dateFormat = "EEE d MMM HH:mm"
            s += "\nReminders I'm holding: " + open.prefix(10).map { $0.text + ($0.due.map { " (\(f.string(from: $0)))" } ?? "") }.joined(separator: "; ") + "."
        }
        return s
    }
}
