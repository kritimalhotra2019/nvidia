import Foundation

/// Pin saved defaults for the duration of a task. Explicit selections, including
/// "none", always win; changing the library applies to the next task.
enum TemplateSelection {
    static func applying(defaults: [String: String], tool: String, arguments: [String: Any]) -> [String: Any] {
        var result = arguments
        if tool == "export_document", let format = result["format"] as? String,
           ["docx", "pptx"].contains(format), result["template_id"] == nil {
            result["template_id"] = defaults[format] ?? "none"
        } else if tool == "report_dataset" {
            for format in ["docx", "pptx"] where result[format + "_template_id"] == nil {
                result[format + "_template_id"] = defaults[format] ?? "none"
            }
        }
        return result
    }
}
