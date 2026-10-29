import Foundation

@main
struct ArgumentRepairChecks {
    static var passed = 0
    static func check(_ ok: Bool, _ name: String) { if ok { passed += 1 } else { FileHandle.standardError.write("FAILED: \(name)\n".data(using: .utf8)!); exit(1) } }
    static func main() {
        let original: [String: Any] = ["filename": "Cash transfer impact brief.docx", "dataset_id": "data_1ab2c3d4",
                                       "sections": "## Balance\nBaseline consumption was balanced across arms (difference 3.1, p = 0.42).",
                                       "chart_path": "/private/tmp/claude-501/x/projects/default/artifacts/20261026_chart/chart.svg", "title": "Brief"]
        let good = #"{"filename":"Cash transfer impact brief.docx","dataset_id":"data_1ab2c3d4","title":"Brief","sections":[{"heading":"Balance","text":"Baseline consumption was balanced across arms (difference 3.1, p = 0.42)."}],"chart_path":"\/private\/tmp\/claude-501\/x\/projects\/default\/artifacts\/20261026_chart\/chart.svg"}"#
        check(ArgumentRepair.preservesContent(original: original, repairedJSON: good), "structure fixed, content and path intact → accepted")
        let droppedChar = good.replacingOccurrences(of: "claude-501", with: "claude-51")
        check(!ArgumentRepair.preservesContent(original: original, repairedJSON: droppedChar), "a changed path is rejected")
        check(ArgumentRepair.missing(original: original, repairedJSON: droppedChar).count == 1, "the missing path is reported")
        let renamed = good.replacingOccurrences(of: "Cash transfer impact brief.docx", with: "Cash transfer brief.docx")
        check(!ArgumentRepair.preservesContent(original: original, repairedJSON: renamed), "a changed filename is rejected")
        let reworded = good.replacingOccurrences(of: "was balanced across arms", with: "was similar across arms")
        check(!ArgumentRepair.preservesContent(original: original, repairedJSON: reworded), "reworded prose is rejected")
        let newID = good.replacingOccurrences(of: "data_1ab2c3d4", with: "data_ffffffff")
        check(!ArgumentRepair.preservesContent(original: original, repairedJSON: newID), "a changed id is rejected")
        check(ArgumentRepair.preservesContent(original: ["kind": "bar", "x": "treatment"], repairedJSON: #"{"kind":"bar","x":"treatment","dataset_id":"data_1"}"#), "short literals are not protected")
        print("Argument repair checks passed: \(passed) assertions")
    }
}
