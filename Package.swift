// swift-tools-version: 6.2
import PackageDescription

let package = Package(
    name: "Squipi",
    platforms: [.macOS(.v26)],
    dependencies: [
        .package(url: "https://github.com/eastriverlee/LLM.swift", from: "3.0.3")
    ],
    targets: [
        .executableTarget(
            name: "Squipi",
            dependencies: [
                .product(name: "LLM", package: "LLM.swift")
            ],
            path: "Sources/Squipi",
            swiftSettings: [.swiftLanguageMode(.v5)]
        )
    ]
)
