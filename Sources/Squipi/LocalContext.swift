import Foundation
import CoreLocation

/// Time, place and weather from this Mac's own services, never from the model: the clock, CoreLocation
/// with the user's permission (reverse-geocoded to a town, coordinates rounded), and Apple Weather through
/// a Shortcut the user creates once ("Squipi Weather"). Apple's on-device model has no weather tool of its
/// own; the Shortcuts app reaches Apple Weather without a developer account, and the answer stays local.
@MainActor
final class LocalContext: NSObject, ObservableObject, CLLocationManagerDelegate {
    static let shared = LocalContext()
    static let weatherShortcut = "Squipi Weather"

    @Published private(set) var placeDescription: String = ""
    @Published private(set) var placeStatus: String = "Location not requested yet."
    @Published private(set) var lastWeather: String = ""
    @Published private(set) var weatherStatus: String = ""

    private let manager = CLLocationManager()
    private var lastFix: Date?
    private var lastWeatherAt: Date?

    private override init() {
        super.init()
        manager.delegate = self
        manager.desiredAccuracy = kCLLocationAccuracyKilometer
    }

    // MARK: time
    static func now() -> String {
        let f = DateFormatter(); f.locale = Locale(identifier: "en_GB"); f.dateFormat = "EEEE d MMMM yyyy, HH:mm zzz"
        return f.string(from: Date())
    }

    // MARK: place
    var locationAllowed: Bool { [.authorizedAlways, .authorized].contains(manager.authorizationStatus) }

    func requestPlace() {
        if !CLLocationManager.locationServicesEnabled() { placeStatus = "Location Services are off in System Settings → Privacy & Security."; return }
        switch manager.authorizationStatus {
        case .notDetermined:
            placeStatus = "Asking macOS for permission…"
            manager.requestWhenInUseAuthorization()
        case .denied, .restricted:
            placeStatus = "Location access is denied for Squipi. Allow it in System Settings → Privacy & Security → Location Services."
        default:
            placeStatus = "Finding the Mac's location…"
            manager.requestLocation()
        }
    }

    nonisolated func locationManagerDidChangeAuthorization(_ manager: CLLocationManager) {
        Task { @MainActor in
            if self.locationAllowed { self.placeStatus = "Location allowed. Finding the Mac's location…"; manager.requestLocation() }
            else if manager.authorizationStatus == .denied { self.placeStatus = "Location access is denied for Squipi." }
        }
    }

    nonisolated func locationManager(_ manager: CLLocationManager, didUpdateLocations locations: [CLLocation]) {
        guard let location = locations.last else { return }
        Task { @MainActor in
            self.lastFix = Date()
            let lat = (location.coordinate.latitude * 100).rounded() / 100, lon = (location.coordinate.longitude * 100).rounded() / 100
            self.placeDescription = String(format: "near %.2f, %.2f", lat, lon)
            self.placeStatus = "Location found; naming the place…"
            CLGeocoder().reverseGeocodeLocation(location) { marks, _ in
                Task { @MainActor in
                    if let m = marks?.first {
                        let parts = [m.locality ?? m.subAdministrativeArea, m.administrativeArea, m.country].compactMap { $0 }.filter { !$0.isEmpty }
                        if !parts.isEmpty { self.placeDescription = parts.joined(separator: ", ") }
                    }
                    self.placeStatus = "Place: \(self.placeDescription) (coordinates rounded to about a kilometre; nothing leaves this Mac)."
                }
            }
        }
    }

    nonisolated func locationManager(_ manager: CLLocationManager, didFailWithError error: Error) {
        Task { @MainActor in self.placeStatus = "Could not get the location: \(error.localizedDescription)" }
    }

    /// The place if known and recent (an hour), asking for a fresh fix in the background otherwise.
    func place(refresh: Bool = true) -> String? {
        if refresh, locationAllowed, lastFix.map({ Date().timeIntervalSince($0) > 3600 }) ?? true { manager.requestLocation() }
        return placeDescription.isEmpty ? nil : placeDescription
    }

    // MARK: weather (Apple Weather via the Shortcuts app)
    static var shortcutExists: Bool {
        let p = Process(); p.executableURL = URL(fileURLWithPath: "/usr/bin/shortcuts"); p.arguments = ["list"]
        let pipe = Pipe(); p.standardOutput = pipe; p.standardError = FileHandle.nullDevice
        guard (try? p.run()) != nil else { return false }
        p.waitUntilExit()
        let names = String(decoding: pipe.fileHandleForReading.readDataToEndOfFile(), as: UTF8.self).split(separator: "\n").map { $0.trimmingCharacters(in: .whitespaces) }
        return names.contains(weatherShortcut)
    }

    static let setupText = "Open the Shortcuts app, create a shortcut named \"\(weatherShortcut)\" with two actions: Weather → Get Current Weather, then Scripting → Stop and Output (set to the weather result). Run it once in Shortcuts to grant it Location and Weather access. Squipi then reads today's conditions from Apple Weather on this Mac; nothing goes to any other service."

    /// Runs the shortcut with a bounded wait; returns its text or nil with a status explaining why.
    func weather(maxWait: TimeInterval = 20) async -> String? {
        if let at = lastWeatherAt, Date().timeIntervalSince(at) < 600, !lastWeather.isEmpty { return lastWeather }
        guard Self.shortcutExists else { weatherStatus = "No shortcut named \"\(Self.weatherShortcut)\" yet. " + Self.setupText; return nil }
        weatherStatus = "Asking Apple Weather through the Shortcut…"
        let result: String? = await withCheckedContinuation { continuation in
            DispatchQueue.global().async {
                let p = Process(); p.executableURL = URL(fileURLWithPath: "/usr/bin/shortcuts"); p.arguments = ["run", Self.weatherShortcut]
                let out = Pipe(), err = Pipe(); p.standardOutput = out; p.standardError = err
                guard (try? p.run()) != nil else { continuation.resume(returning: nil); return }
                let deadline = DispatchWorkItem { if p.isRunning { p.terminate() } }
                DispatchQueue.global().asyncAfter(deadline: .now() + maxWait, execute: deadline)
                p.waitUntilExit(); deadline.cancel()
                let text = String(decoding: out.fileHandleForReading.readDataToEndOfFile(), as: UTF8.self).trimmingCharacters(in: .whitespacesAndNewlines)
                let error = String(decoding: err.fileHandleForReading.readDataToEndOfFile(), as: UTF8.self).trimmingCharacters(in: .whitespacesAndNewlines)
                continuation.resume(returning: text.isEmpty ? (error.isEmpty ? nil : "ERROR: " + error) : text)
            }
        }
        if let result, !result.hasPrefix("ERROR: ") {
            lastWeather = String(result.prefix(600)); lastWeatherAt = Date(); weatherStatus = "Weather read from Apple Weather at \(Self.now())."
            return lastWeather
        }
        weatherStatus = result.map { String($0.dropFirst(7)) } ?? "The shortcut returned nothing within \(Int(maxWait)) seconds. Run it once in the Shortcuts app to grant Location and Weather access."
        return nil
    }

    // MARK: prompt line
    static let asksLocalContext = #"\b(?:weather|temperature|forecast|rain|raining|sunny|cold|hot|snow|wind|umbrella|what time|the time|today's date|what day|what date|where am i|my location|where are we|nearby|outside)\b"#

    /// One line of real-world context for the prompt when the message asks about time, place or weather.
    func contextLine(for text: String) async -> String {
        guard text.range(of: Self.asksLocalContext, options: [.regularExpression, .caseInsensitive]) != nil else { return "" }
        var parts = ["time: \(Self.now())"]
        if let place = place() { parts.append("place: \(place)") }
        if text.range(of: #"\b(?:weather|temperature|forecast|rain|raining|sunny|cold|hot|snow|wind|umbrella|outside)\b"#, options: [.regularExpression, .caseInsensitive]) != nil {
            if let w = await weather() { parts.append("weather now (Apple Weather): \(w)") } else { parts.append("weather: unavailable (\(weatherStatus.prefix(160)))") }
        }
        return "\nLocal context from this Mac (authoritative; the model must not guess these): " + parts.joined(separator: "; ") + "\n"
    }
}
