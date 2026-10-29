import SwiftUI

struct ResearchServicesView: View {
    @ObservedObject private var worker = Worker.shared
    @State private var brightToken = ""
    @State private var openAlexToken = ""
    @State private var status = ""
    @AppStorage("brightdataEnabled") private var enabled = false
    @AppStorage("brightdataWebZone") private var webZone = ""
    @AppStorage("brightdataSerpZone") private var serpZone = ""
    @AppStorage("brightdataRequestLimit") private var limit = 8
    /// Reported by the worker at hello: meaning-based recall runs on nomic-embed-text through the local
    /// Ollama daemon (loopback only) and falls back to keyword matching when the model is absent.
    private var embeddingLine: String {
        guard let info = worker.info["embeddings"] as? [String: Any], info["enabled"] as? Bool == true else {
            return "Meaning-based recall: off (keyword matching only)."
        }
        let model = info["model"] as? String ?? "nomic-embed-text"
        return "Meaning-based recall and passage search: \(model) via local Ollama (nothing leaves this Mac; keyword fallback when the model is not installed: ollama pull \(model))."
    }
    var body: some View {
        VStack(alignment: .leading, spacing: 8) {
            SectionTitle(text: "Research services")
            Text("Pandera · Presidio · Docling · PyFixest · DoWhy · R survey").font(.system(size: 12, weight: .semibold, design: .rounded))
            Text("Research libraries run locally. Package/model setup is separate from research. Missing dependencies are reported explicitly.")
            Text(embeddingLine).font(.system(size: 11)).foregroundStyle(.secondary)
            Button("Install or repair local research pack") { Task { await worker.installResearch() } }.buttonStyle(Pill(filled: false))
            Text("Setup downloads pinned libraries and OCR models. Research files stay on this Mac.")
            if !worker.researchSetup.isEmpty { Text(worker.researchSetup).textSelection(.enabled) }
            Button("Check installed research tools") {
                Task {
                    do {
                        let result = try await Worker.shared.call("research_capabilities", [:])
                        let packages = result["packages"] as? [String: Any] ?? [:]
                        status = packages.keys.sorted().map { "\($0): \(packages[$0] is NSNull ? "not installed" : String(describing: packages[$0]!))" }.joined(separator: " · ")
                        status += " · R: \(result["rscript_available"] as? Bool == true ? "available" : "not configured")"
                    } catch { status = error.localizedDescription }
                }
            }.buttonStyle(Pill(filled: false))
            SecureField("OpenAlex API key · enter to save or replace", text: $openAlexToken)
            Toggle("Enable Bright Data web retrieval", isOn: $enabled).toggleStyle(.switch)
            SecureField("Bright Data API token · enter to save or replace", text: $brightToken)
            TextField("Web Unlocker zone", text: $webZone)
            TextField("SERP zone", text: $serpZone)
            Stepper("Bright Data requests per objective: \(limit)", value: $limit, in: 1...20)
            Button("Save service keys") {
                if !brightToken.isEmpty { Keychain.set(brightToken.trimmingCharacters(in: .whitespacesAndNewlines), account: "brightdata"); brightToken = "" }
                if !openAlexToken.isEmpty { Keychain.set(openAlexToken.trimmingCharacters(in: .whitespacesAndNewlines), account: "openalex"); openAlexToken = "" }
                status = "Save requested. Blank fields leave existing keys unchanged; failed authorization leaves the service unavailable."
            }.buttonStyle(Pill(filled: false))
            Text("Keys are kept in macOS Keychain and excluded from model prompts and research receipts. External requests ask for approval and may incur provider charges. Bright Data is optional and does not guarantee access to every site.")
            if !status.isEmpty { Text(status).textSelection(.enabled) }
        }.font(.system(size: 11, design: .rounded)).foregroundStyle(Color.petMuted).card()
    }
}
