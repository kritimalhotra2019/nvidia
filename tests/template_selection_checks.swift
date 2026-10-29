import Foundation

@main struct TemplateSelectionChecks {
    static func main() {
        let defaults = ["docx": "tpl_word", "pptx": "tpl_slides"]
        let docx = TemplateSelection.applying(defaults: defaults, tool: "export_document", arguments: ["format": "docx", "title": "Report"])
        precondition(docx["template_id"] as? String == "tpl_word")
        precondition(docx["title"] as? String == "Report")
        for explicit in ["none", "tpl_other"] {
            let output = TemplateSelection.applying(defaults: defaults, tool: "export_document", arguments: ["format": "docx", "template_id": explicit])
            precondition(output["template_id"] as? String == explicit)
        }
        let empty = TemplateSelection.applying(defaults: [:], tool: "export_document", arguments: ["format": "pptx"])
        precondition(empty["template_id"] as? String == "none", "No default is pinned too, so a new UI default cannot leak into an ongoing turn")
        let report = TemplateSelection.applying(defaults: defaults, tool: "report_dataset", arguments: ["docx_template_id": "none", "dataset_id": "ds_1"])
        precondition(report["docx_template_id"] as? String == "none")
        precondition(report["pptx_template_id"] as? String == "tpl_slides")
        precondition(report["dataset_id"] as? String == "ds_1")
        precondition(TemplateSelection.applying(defaults: defaults, tool: "read_document", arguments: [:]).isEmpty)
        precondition(TemplateSelection.applying(defaults: defaults, tool: "export_document", arguments: ["format": "html"])["template_id"] == nil)
        print("PASS: 10 template-selection checks")
    }
}
