import Foundation

@main
struct NumberLedgerChecks {
    static var passed = 0
    static func check(_ ok: Bool, _ name: String) { if ok { passed += 1 } else { FileHandle.standardError.write("FAILED: \(name)\n".data(using: .utf8)!); exit(1) } }
    static func main() {
        let records: [[String: Any]] = [["tool": "aggregate", "ok": true, "result": ["rows": [["Accepted", 13], ["Rejected", 140], ["Not decided", 155]], "summary_text": "6 groups by AP result; count_ID."]],
                                        ["tool": "profile_dataset", "ok": true, "result": ["rows": 518, "columns": 28]]]
        let ledger = NumberLedger.build(records: records, spans: "", instruction: "how many adverse possession cases out of total were successful?")
        check(ledger.untraceable(in: "13 of 518 cases were accepted (2.5%).").isEmpty, "verified percentage from two traceable counts is kept")
        check(ledger.untraceable(in: "Accepted: 13 out of 518 (2.51 %).").isEmpty, "two decimals within tolerance")
        check(ledger.untraceable(in: "13/518 = 3%").isEmpty, "slash fraction, rounded to a whole percent")
        check(ledger.untraceable(in: "13 of the 518 reviewed cases, about 3%, succeeded").isEmpty, "'of the' phrasing")
        check(ledger.untraceable(in: "140 of 518 cases (27%) were rejected and 155 of 518 (30%) not decided.").isEmpty, "two verified percentages on one line")
        check(ledger.untraceable(in: "13 of 518 cases were accepted (8.4%).") == ["8.4"], "wrong arithmetic stays untraceable")
        check(ledger.untraceable(in: "13 of 154 decided cases (8.4%).") == ["154", "8.4"], "an untraceable denominator verifies nothing")
        check(ledger.untraceable(in: "Roughly 2.5% succeeded.") == ["2.5"], "a percentage with no fraction on its line is still untraceable")
        check(ledger.untraceable(in: "13 of 518 (2.5%)\nabout 2.5% overall") == ["2.5"], "verification is per line")
        let filtered = ledger.filter("Of 518 cases, 13 (2.5%) were accepted.\nSo 2.5% is the success rate.")
        check(filtered.dropped == 1 && filtered.removed == ["2.5"] && filtered.text == "Of 518 cases, 13 (2.5%) were accepted.", "filter keeps the verified line and drops the bare one")
        print("number_ledger_checks: \(passed) passed")
    }
}
