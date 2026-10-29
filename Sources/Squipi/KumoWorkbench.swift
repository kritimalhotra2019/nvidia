import AppKit
import SwiftUI
import UniformTypeIdentifiers

enum KumoSpecialist: String, CaseIterable, Identifiable {
    case forecast, relational, tabular
    var id: String { rawValue }
    var title: String {
        switch self {
        case .forecast: return "NVIDIA Kumo Forecast"
        case .relational: return "NVIDIA Kumo Relational"
        case .tabular: return "NVIDIA Kumo Tabular Small"
        }
    }
    var summary: String {
        switch self {
        case .forecast: return "Forecast a regular time series from at least 512 complete observations."
        case .relational: return "Predict an outcome using a main table and explicitly linked tables."
        case .tabular: return "Predict a category or numeric outcome from one prepared table."
        }
    }
    var example: String {
        switch self {
        case .forecast: return "Forecast daily clinic attendance for the next 14 days."
        case .relational: return "Predict customer churn or student dropout using linked engagement snapshots."
        case .tabular: return "Predict customer churn or student dropout from one prepared table."
        }
    }
}

private struct KumoDatasetChoice: Identifiable {
    let id: String
    let label: String
    let columns: [String]
    init?(_ value: [String: Any]) {
        guard let id = value["id"] as? String else { return nil }
        self.id = id
        var label = value["name"] as? String ?? id
        if let sheet = value["sheet"] as? String { label += " · " + sheet }
        if let rows = value["rows"] as? Int { label += " · \(rows.formatted()) rows" }
        self.label = label
        self.columns = value["columns"] as? [String] ?? []
    }
}

private struct KumoLinkedSelection: Identifiable {
    let id = UUID()
    var datasetID = ""
    var foreignKey = ""
    var features: [String] = []
    var manualFeatures = ""
}

/// A direct local model form. Import only registers the selected file. Inference
/// starts exclusively from Run and never invokes the conversational agent/LLM.
@MainActor
struct KumoWorkbenchView: View {
    let specialist: KumoSpecialist
    @Environment(\.dismiss) private var dismiss
    @ObservedObject private var conversation = Conversation.shared
    @ObservedObject private var analysis = Analysis.shared
    @ObservedObject private var runtime = KumoRuntime.shared
    @State private var datasets: [KumoDatasetChoice] = []
    @State private var datasetID = ""
    @State private var timeColumn = ""
    @State private var targetColumn = ""
    @State private var groupColumn = ""
    @State private var idColumn = ""
    @State private var horizon = 14
    @State private var predictionTask = "classification"
    @State private var features: [String] = []
    @State private var manualFeatures = ""
    @State private var linked: [KumoLinkedSelection] = [KumoLinkedSelection()]
    @State private var crossSectionalConfirmed = false
    @State private var createWord = false
    @State private var working = false
    @State private var importing = false
    @State private var stopping = false
    @State private var progress = 0
    @State private var status = ""
    @State private var error = ""
    @State private var summary = ""
    @State private var csvPath = ""
    @State private var chartPath = ""
    @State private var wordPath = ""
    @State private var operation: Task<Void, Never>?

    private var active: Bool { working || importing }
    private var unavailable: Bool {
        active || conversation.busy || analysis.busy || runtime.settingUp || runtime.forecasting
    }
    private var baseColumns: [String] { columns(for: datasetID) }

    var body: some View {
        VStack(alignment: .leading, spacing: 14) {
            HStack(alignment: .top) {
                VStack(alignment: .leading, spacing: 5) {
                    Text(specialist.title).font(.title2.bold())
                    Text("Local specialist · Experimental").font(.caption).foregroundStyle(.secondary)
                }
                Spacer()
                Button("Close") { dismiss() }.disabled(active)
            }
            Text(specialist.summary)
            Text("Example: " + specialist.example).font(.callout).foregroundStyle(.secondary)
            ScrollView {
                VStack(alignment: .leading, spacing: 17) {
                    sourceSection
                    if specialist == .forecast { forecastSection }
                    else { predictionSection }
                    Toggle("Also save a Word summary", isOn: $createWord)
                        .disabled(active)
                    Text(specialist == .forecast
                         ? "Point forecasts with a last-value reference. No calibrated intervals or causal effects."
                         : "Known targets are examples; missing targets are the rows to predict. Scores are uncalibrated and are not causal effects or measured accuracy.")
                        .font(.caption).foregroundStyle(.secondary)
                    if active {
                        ProgressView(value: Double(progress), total: 100)
                        Text(status).font(.callout).textSelection(.enabled)
                    } else if !status.isEmpty {
                        Text(status).font(.caption).foregroundStyle(.secondary).textSelection(.enabled)
                    }
                    if !error.isEmpty {
                        Text(error).foregroundStyle(.red).font(.callout).textSelection(.enabled)
                    }
                    if !summary.isEmpty { resultSection }
                }
                .padding(.trailing, 4)
            }
            Divider()
            HStack {
                VStack(alignment: .leading, spacing: 2) {
                    Text("Runs on this Mac. Originals are preserved.").font(.caption)
                    if (conversation.busy || analysis.busy) && !active {
                        Text("Finish or stop the current task before running Kumo.")
                            .font(.caption).foregroundStyle(.orange)
                    }
                }
                Spacer()
                if active {
                    Button(stopping ? "Stopping…" : "Cancel") { cancel() }.disabled(stopping)
                }
                Button("Run " + (specialist == .forecast ? "forecast" : "prediction")) { run() }
                    .buttonStyle(.borderedProminent).tint(Color.petAccent)
                    .disabled(unavailable || datasetID.isEmpty || (specialist != .forecast && !crossSectionalConfirmed))
            }
        }
        .padding(22).frame(width: 680, height: 710)
        .interactiveDismissDisabled(active)
        .onAppear { datasets = analysis.datasets.compactMap(KumoDatasetChoice.init) }
        .onChange(of: datasetID) { _ in
            timeColumn = ""; targetColumn = ""; groupColumn = ""; idColumn = ""
            features = []; manualFeatures = ""; crossSectionalConfirmed = false
        }
        .onChange(of: targetColumn) { target in
            features.removeAll { $0 == target }; crossSectionalConfirmed = false
            for index in linked.indices { linked[index].features.removeAll { $0 == target } }
        }
        .onChange(of: idColumn) { key in
            features.removeAll { $0 == key }; crossSectionalConfirmed = false
        }
    }

    private var sourceSection: some View {
        VStack(alignment: .leading, spacing: 9) {
            Text(specialist == .relational ? "Main table" : "Dataset").font(.headline)
            datasetPicker("Choose a registered dataset", selection: $datasetID)
            HStack {
                Button("Import CSV or Excel…") { importFile(into: nil) }.disabled(unavailable)
                Button("Refresh datasets") { refresh() }.disabled(unavailable)
            }
            Text("Import registers the file so you can choose columns. It does not start a prediction.")
                .font(.caption).foregroundStyle(.secondary)
        }
    }

    private var forecastSection: some View {
        VStack(alignment: .leading, spacing: 10) {
            columnPicker("Date / time column", selection: $timeColumn, columns: baseColumns)
            columnPicker("Numeric value to forecast", selection: $targetColumn, columns: baseColumns)
            columnPicker("Separate series by (optional)", selection: $groupColumn, columns: baseColumns, optional: true)
            Stepper("Forecast horizon: \(horizon) periods", value: $horizon, in: 1...72)
            Text("Each series needs 512 complete, regularly spaced observations. Dates and missing values are checked before the model starts.")
                .font(.caption).foregroundStyle(.secondary)
        }.disabled(active)
    }

    private var predictionSection: some View {
        VStack(alignment: .leading, spacing: 11) {
            Picker("Prediction type", selection: $predictionTask) {
                Text("Category / probability").tag("classification")
                Text("Numeric outcome").tag("regression")
            }
            columnPicker("Unique entity ID", selection: $idColumn, columns: baseColumns)
            columnPicker("Outcome / target column", selection: $targetColumn, columns: baseColumns)
            featurePicker("Main-table predictors", columns: baseColumns.filter { $0 != idColumn && $0 != targetColumn },
                          selected: $features, manual: $manualFeatures)
            if specialist == .relational {
                Divider()
                Text("Linked tables").font(.headline)
                Text("Each linked row must point to one entity in the main table. Use prepared snapshots; direct event histories and repeated time anchors are not supported.")
                    .font(.caption).foregroundStyle(.secondary)
                ForEach($linked) { $link in
                    VStack(alignment: .leading, spacing: 8) {
                        HStack {
                            Text("Linked table \((linked.firstIndex(where: { $0.id == link.id }) ?? 0) + 1)").font(.subheadline.bold())
                            Spacer()
                            if linked.count > 1 {
                                Button("Remove") { linked.removeAll { $0.id == link.id } }
                            }
                        }
                        datasetPicker("Dataset", selection: $link.datasetID)
                        Button("Import linked CSV or Excel…") { importFile(into: link.id) }.disabled(unavailable)
                        columnPicker("Foreign key → main entity ID", selection: $link.foreignKey, columns: columns(for: link.datasetID))
                        featurePicker("Linked predictors", columns: columns(for: link.datasetID).filter { $0 != link.foreignKey && $0 != targetColumn },
                                      selected: $link.features, manual: $link.manualFeatures)
                    }
                    .padding(10).background(Color.primary.opacity(0.035), in: RoundedRectangle(cornerRadius: 10))
                    .onChange(of: link.datasetID) { _ in
                        link.foreignKey = ""; link.features = []; link.manualFeatures = ""
                        crossSectionalConfirmed = false
                    }
                }
                if linked.count < 3 {
                    Button("Add linked table") { linked.append(KumoLinkedSelection()) }
                }
            }
            Toggle("I prepared a cross-sectional snapshot, with all predictors known before the outcome.", isOn: $crossSectionalConfirmed)
                .font(.callout)
            Text("20–1,024 labelled examples; 1–256 missing-target rows. Classification needs 2–10 classes with at least five examples each. Select only justified, de-identified predictors.")
                .font(.caption).foregroundStyle(.secondary)
        }.disabled(active)
    }

    private var resultSection: some View {
        VStack(alignment: .leading, spacing: 9) {
            Text("Saved results").font(.headline)
            Text(summary).font(.callout).textSelection(.enabled)
            HStack {
                if existing(csvPath) { Button("Open CSV") { open(csvPath) } }
                if existing(chartPath) { Button("Open chart") { open(chartPath) } }
                if existing(wordPath) { Button("Open Word summary") { open(wordPath) } }
            }
        }.padding(12).background(Color.petAccent.opacity(0.08), in: RoundedRectangle(cornerRadius: 10))
    }

    private func columns(for id: String) -> [String] { datasets.first { $0.id == id }?.columns ?? [] }

    private func datasetPicker(_ title: String, selection: Binding<String>) -> some View {
        Picker(title, selection: selection) {
            Text("Choose…").tag("")
            ForEach(datasets) { item in Text(item.label).tag(item.id) }
        }.disabled(active)
    }

    @ViewBuilder
    private func columnPicker(_ title: String, selection: Binding<String>, columns: [String], optional: Bool = false) -> some View {
        if columns.isEmpty {
            TextField(title, text: selection).textFieldStyle(.roundedBorder)
        } else {
            Picker(title, selection: selection) {
                Text(optional ? "None" : "Choose…").tag("")
                ForEach(columns, id: \.self) { Text($0).tag($0) }
            }
        }
    }

    private func featurePicker(_ title: String, columns: [String], selected: Binding<[String]>, manual: Binding<String>) -> some View {
        VStack(alignment: .leading, spacing: 5) {
            Text(title).font(.subheadline)
            if columns.isEmpty {
                TextField("Column names, separated by commas", text: manual).textFieldStyle(.roundedBorder)
            } else {
                Menu("Select predictor columns (\(selected.wrappedValue.count))") {
                    ForEach(columns, id: \.self) { name in
                        Toggle(name, isOn: Binding(get: { selected.wrappedValue.contains(name) }, set: { on in
                            if on { if !selected.wrappedValue.contains(name) { selected.wrappedValue.append(name) } }
                            else { selected.wrappedValue.removeAll { $0 == name } }
                        }))
                    }
                }
                if !selected.wrappedValue.isEmpty {
                    Text(selected.wrappedValue.joined(separator: ", ")).font(.caption).textSelection(.enabled)
                }
            }
        }
    }

    private func featureNames(selected: [String], manual: String, dataset: String) -> [String] {
        columns(for: dataset).isEmpty
            ? manual.split(separator: ",").map { $0.trimmingCharacters(in: .whitespacesAndNewlines) }.filter { !$0.isEmpty }
            : selected
    }

    private func arguments() throws -> [String: Any] {
        func require(_ condition: Bool, _ message: String) throws {
            if !condition { throw Worker.Failure(code: "invalid_arguments", message: message) }
        }
        try require(datasets.contains { $0.id == datasetID }, "Choose the exact dataset to use, or import it first.")
        try require(!targetColumn.isEmpty, "Choose the outcome or numeric target column.")
        var args: [String: Any] = ["dataset_id": datasetID, "target_column": targetColumn]
        if specialist == .forecast {
            try require(!timeColumn.isEmpty, "Choose the date / time column.")
            args["time_column"] = timeColumn; args["horizon"] = horizon
            if !groupColumn.isEmpty { args["group_column"] = groupColumn }
        } else {
            try require(crossSectionalConfirmed, "Confirm that these are prepared cross-sectional predictors known before the outcome.")
            try require(!idColumn.isEmpty, "Choose a unique entity ID column.")
            let chosen = featureNames(selected: features, manual: manualFeatures, dataset: datasetID)
            try require(!chosen.isEmpty && chosen.count <= 20, "Choose between 1 and 20 predictor columns.")
            args["id_column"] = idColumn; args["feature_columns"] = chosen
            args["task"] = predictionTask; args["data_mode"] = "cross_sectional"
            args["engine"] = specialist == .relational ? "relational" : "tabular"
            if specialist == .relational {
                var links: [[String: Any]] = []
                var used = Set([datasetID])
                for link in linked {
                    try require(datasets.contains { $0.id == link.datasetID }, "Choose a dataset for each linked table.")
                    try require(used.insert(link.datasetID).inserted, "The main and linked tables must be distinct datasets.")
                    try require(!link.foreignKey.isEmpty, "Choose each linked table's foreign key.")
                    let names = featureNames(selected: link.features, manual: link.manualFeatures, dataset: link.datasetID)
                    try require(!names.isEmpty && names.count <= 20, "Choose 1–20 predictors for every linked table.")
                    links.append(["dataset_id": link.datasetID, "foreign_key": link.foreignKey, "feature_columns": names])
                }
                args["linked_tables"] = links
            }
        }
        return args
    }

    private func refresh() {
        guard !unavailable else { return }
        importing = true; error = ""; status = "Reading registered datasets…"
        operation = Task { @MainActor in
            defer { importing = false; operation = nil; stopping = false }
            do {
                let inventory = try await Worker.shared.inventory()
                datasets = inventory.datasets.compactMap(KumoDatasetChoice.init)
                status = "\(datasets.count) registered datasets. Choose the input explicitly."
            } catch { self.error = error.localizedDescription }
        }
    }

    private func importFile(into linkedID: UUID?) {
        guard !unavailable else { return }
        let panel = NSOpenPanel()
        panel.allowedContentTypes = [.commaSeparatedText] + ["xlsx", "xls"].compactMap { UTType(filenameExtension: $0) }
        panel.canChooseDirectories = false; panel.allowsMultipleSelection = false
        panel.message = "Register a table, then choose columns and press Run. No model starts on import."
        panel.prompt = "Import"
        panel.begin { response in
            guard response == .OK, let file = panel.url else { return }
            Task { @MainActor in
                guard !unavailable else { return }
                importing = true; error = ""; status = "Importing \(file.lastPathComponent)…"
                operation = Task { @MainActor in
                    defer { importing = false; operation = nil; stopping = false }
                    do {
                        try await Worker.shared.allow([file.path])
                        let value = try await Worker.shared.call("import_dataset", ["path": file.path])
                        try Task.checkCancellation()
                        let inventory = try await Worker.shared.inventory()
                        datasets = inventory.datasets.compactMap(KumoDatasetChoice.init)
                        guard let id = value["dataset_id"] as? String else {
                            throw Worker.Failure(code: "import", message: "Import returned no dataset ID.")
                        }
                        if let linkedID, let index = linked.firstIndex(where: { $0.id == linkedID }) {
                            linked[index].datasetID = id
                        } else { datasetID = id }
                        status = value["summary_text"] as? String ?? "Imported. Choose columns before running."
                        await Analysis.shared.refreshInventory()
                    } catch { self.error = error.localizedDescription }
                }
            }
        }
    }

    private func run() {
        guard !unavailable else { return }
        let args: [String: Any]
        do { args = try arguments() }
        catch { self.error = error.localizedDescription; return }
        working = true; stopping = false; progress = 0; status = "Preparing local model…"
        error = ""; summary = ""; csvPath = ""; chartPath = ""; wordPath = ""
        operation = Task { @MainActor in
            defer { working = false; stopping = false; operation = nil }
            do {
                let tool = specialist == .forecast ? "forecast_series" : "predict_outcome"
                let value = try await Worker.shared.call(tool, args) { percent, note in
                    progress = min(100, max(0, percent)); status = note
                }
                csvPath = value["csv_path"] as? String ?? ""
                chartPath = value["chart_path"] as? String ?? ""
                summary = value["summary_text"] as? String ?? "Results saved. Review assumptions and limitations before use."
                try Task.checkCancellation()
                if createWord, let id = value["analysis_id"] as? String {
                    status = "Saving a Word summary of the results…"
                    do {
                        let word = try await Worker.shared.call("write_research_results_brief", ["analysis_ids": [id],
                            "title": specialist.title + " results", "filename": "Kumo \(specialist.rawValue) summary.docx"])
                        wordPath = word["docx_path"] as? String ?? ""
                    } catch {
                        if stopping || Task.isCancelled { throw CancellationError() }
                        self.error = "The model results are saved, but the Word summary did not finish: " + error.localizedDescription
                    }
                }
                status = "Finished. Open the saved files below."
                progress = 100
            } catch {
                status = stopping || Task.isCancelled ? "Stopped. Any already saved outputs remain available." : "The run did not finish."
                self.error = stopping || Task.isCancelled ? "" : error.localizedDescription
            }
        }
    }

    private func cancel() {
        guard active else { return }
        stopping = true; status = "Stopping this operation…"
        operation?.cancel()
    }

    private func existing(_ path: String) -> Bool {
        !path.isEmpty && path.hasPrefix("/") && FileManager.default.fileExists(atPath: path)
    }
    private func open(_ path: String) {
        guard existing(path) else { error = "This output is no longer available at its saved path."; return }
        NSWorkspace.shared.open(URL(fileURLWithPath: path))
    }
}
