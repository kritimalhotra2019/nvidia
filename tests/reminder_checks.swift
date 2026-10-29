import Foundation

// These stubs deliberately fail if tests accidentally touch real application state.
struct Store { static let shared = Store(); var dir: URL { fatalError("Tests must inject a temporary mind file") } }
struct Brain { static let userName = "Synthetic user" }

@main
struct ReminderChecks {
    @MainActor static func main() throws {
        var assertions = 0
        func check(_ condition: Bool, _ message: String) {
            assertions += 1
            if !condition { fatalError(message) }
        }
        func rejects(_ operation: () throws -> Void, _ message: String) {
            do { try operation(); fatalError(message) } catch { assertions += 1 }
        }
        func date(_ value: String) -> Date { ISO8601DateFormatter().date(from: value)! }
        let now = date("2026-01-01T00:00:00Z")
        let utc = TimeZone(secondsFromGMT: 0)!
        let india = TimeZone(identifier: "Asia/Kolkata")!
        let newYork = TimeZone(identifier: "America/New_York")!
        let schedule = ReminderSchedule.self
        check(try schedule.resolve(arguments: [:], now: now, timeZone: utc) == nil, "An explicitly untimed reminder stays untimed")
        check(try schedule.resolve(arguments: ["in_minutes": 0], now: now, timeZone: utc) == now, "Zero minutes means now")
        check(try schedule.resolve(arguments: ["in_minutes": 2.5], now: now, timeZone: utc) == now.addingTimeInterval(150), "Relative time uses elapsed seconds")
        check(try schedule.resolve(arguments: ["due": "2026-10-21 12:00"], now: now, timeZone: india) == date("2026-10-21T06:30:00Z"), "Local India clock must use the correct offset")
        check(try schedule.resolve(arguments: ["due": "2026-10-21T12:00:00+05:30"], now: now, timeZone: utc) == date("2026-10-21T06:30:00Z"), "Explicit offset must override local timezone")
        check(try schedule.resolve(arguments: ["due": "2026-10-21T06:30Z"], now: now, timeZone: india) == date("2026-10-21T06:30:00Z"), "Minute precision with UTC must work")
        for bad: [String: Any] in [["in_minutes": -1], ["in_minutes": true], ["in_minutes": "5"], ["in_minutes": Double.infinity],
                                   ["in_minutes": Double.nan], ["in_minutes": Double.greatestFiniteMagnitude],
                                   ["in_minutes": 1, "due": "2026-10-21 12:00"], ["due": "2026-02-30 12:00"],
                                   ["due": "2025-12-31T23:58:00Z"], ["due": "2026-10-21"], ["due": ""], ["due": 5]] {
            rejects({ _ = try schedule.resolve(arguments: bad, now: now, timeZone: utc) }, "Invalid time must be rejected, never saved without a time")
        }
        for localTime in ["2026-03-08 02:30", "2026-11-01 01:30"] {
            rejects({ _ = try schedule.resolve(arguments: ["due": localTime], now: now, timeZone: newYork) }, "DST gap/fold needs an explicit offset")
        }
        let firstFold = try schedule.resolve(arguments: ["due": "2026-11-01T01:30:00-04:00"], now: now, timeZone: newYork)!
        let secondFold = try schedule.resolve(arguments: ["due": "2026-11-01T01:30:00-05:00"], now: now, timeZone: newYork)!
        check(secondFold.timeIntervalSince(firstFold) == 3600, "Explicit offsets disambiguate repeated clocks")
        let beforeJump = date("2026-03-08T06:55:00Z")
        check(try schedule.resolve(arguments: ["in_minutes": 10], now: beforeJump, timeZone: newYork) == date("2026-03-08T07:05:00Z"), "Relative timers must survive a daylight-saving jump")

        let root = URL(fileURLWithPath: CommandLine.arguments[1], isDirectory: true)
        let file = root.appendingPathComponent("native/mind.json")
        let mind = Mind(url: file, now: { now })
        check(mind.addReminder("Overdue", due: now.addingTimeInterval(-3600)), "Save overdue fixture")
        check(mind.addReminder("Due now", due: now), "Save current fixture")
        check(mind.addReminder("Future", due: now.addingTimeInterval(600)), "Save future fixture")
        check(mind.addReminder("No time", due: nil), "Save untimed fixture")
        var dismissed = Reminder(text: "Already done", due: now, done: true, created: now)
        check(mind.addReminder(dismissed.text, due: dismissed.due), "Save completed fixture")
        dismissed = mind.reminders.last!; dismissed.done = true; mind.update(dismissed)
        let restarted = Mind(url: file, now: { now })
        check(restarted.reminders.count == 5, "Restart recovers all reminders")
        check(restarted.reminders.allSatisfy { $0.created == now }, "Synthetic clock controls storage timestamps")
        let playFile = file.deletingLastPathComponent().appendingPathComponent("wellbeing.json")
        let playMemory = Mind(url: playFile, now: { now })
        check(playMemory.addReminder("Wellbeing break", due: now, breakGame: true), "Save explicit game break")
        let gameDue = try Mind(url: playFile, now: { now }).claimDue()
        check(gameDue.count == 1 && gameDue[0].breakGame == true, "Game action survives restart and durable due claim")
        check(try Mind(url: playFile, now: { now }).claimDue().isEmpty, "Game break fires only once")
        let batch = try restarted.claimDue()
        check(batch.map(\.text) == ["Overdue", "Due now"], "Claim all overdue reminders once in due order")
        check(try restarted.claimDue().isEmpty, "Repeated timer ticks must not redeliver")
        check(try Mind(url: file, now: { now }).claimDue().isEmpty, "Restart must not redeliver durable claims")
        check(try restarted.claimDue(at: now.addingTimeInterval(601)).map(\.text) == ["Future"], "Only future scheduled reminders become due")
        check(restarted.reminders.first { $0.text == "No time" }?.done == false, "Untimed reminders never auto-fire")
        let external = Mind(url: file, now: { now })
        external.addFacts(["External client fact"])
        check(external.addReminder("External client", due: now), "External writer saves fixture")
        check(restarted.addReminder("Native addition", due: nil), "Stale native instance refreshes before editing")
        check(restarted.facts.contains("External client fact") && restarted.reminders.contains { $0.text == "External client" }, "Edits preserve completed external writes")
        check(try restarted.claimDue().map(\.text) == ["External client"], "Polling reloads external reminders")
        let before = restarted.reminders.first { $0.text == "No time" }!.due
        check(before == nil && Mind(url: file, now: { now }).reminders.contains { $0.text == "No time" && $0.due == nil }, "Untimed date survives JSON encoding")

        enum DiskFailure: Error { case unavailable }
        var failWrites = false
        let failureFile = root.appendingPathComponent("write-failure/mind.json")
        let failing = Mind(url: failureFile, now: { now }, writeData: { data, url in
            if failWrites { throw DiskFailure.unavailable }
            try data.write(to: url, options: .atomic)
        })
        check(failing.addReminder("Must remain pending", due: now), "Create failure fixture")
        failWrites = true
        rejects({ _ = try failing.claimDue() }, "Failed persistence must not claim a reminder")
        check(failing.reminders.first?.done == false && failing.persistenceError != nil, "Failed save retains pending state and exposes error")
        check(Mind(url: failureFile, now: { now }).reminders.first?.done == false, "Failed save stays pending after restart")
        check(!failing.addReminder("Not saved", due: now), "Creation cannot report success when storage fails")
        failWrites = false
        check(try failing.claimDue().count == 1, "Delivery can recover after storage becomes writable")
        let corrupt = root.appendingPathComponent("corrupt.json")
        try Data("invalid json".utf8).write(to: corrupt)
        let corrupted = Mind(url: corrupt, now: { now })
        check(!corrupted.addReminder("Do not erase", due: now), "Corrupt storage must not be silently replaced")
        check(try String(contentsOf: corrupt, encoding: .utf8) == "invalid json", "Corrupt original is preserved")

        // Python produced this synthetic file in the shell harness; Foundation decodes Swift's epoch.
        let interop = Mind(url: root.appendingPathComponent("python/mind.json"), now: { date("2026-10-21T06:30:01Z") })
        check(interop.reminders.count == 2, "Swift decodes Python's base64 storage")
        check(interop.reminders.allSatisfy { $0.due == date("2026-10-21T06:30:00Z") }, "Both languages agree on absolute due instants")
        check(try interop.claimDue().count == 2, "Swift durably claims Python-created reminders")
        print("Reminder checks passed: \(assertions) assertions; fake clock, temporary storage, no speech or real reminders.")
    }
}
