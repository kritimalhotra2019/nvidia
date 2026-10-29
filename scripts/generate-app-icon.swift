#!/usr/bin/env swift
// Native vector artwork for Squipi's blue flapjack octopus with round glasses.
// The palette matches the animated pet. No external assets or dependencies.
// Run: swiftc -framework AppKit scripts/generate-app-icon.swift -o /tmp/squipi-icon
// Then: /tmp/squipi-icon [output.iconset] [output.icns]
import AppKit

let output = URL(fileURLWithPath: CommandLine.arguments.dropFirst().first ?? "build/Squipi.iconset")
try FileManager.default.createDirectory(at: output, withIntermediateDirectories: true)
func color(_ hex: Int) -> NSColor {
    NSColor(srgbRed: Double((hex >> 16) & 255) / 255, green: Double((hex >> 8) & 255) / 255,
            blue: Double(hex & 255) / 255, alpha: 1)
}
let ink = color(0x172b3a), blue = color(0x58aadd), light = color(0xc2e7f8), ocean = color(0x2b73a6)
func paint(_ path: NSBezierPath, _ fill: NSColor, line: CGFloat = 10) {
    fill.setFill(); path.fill()
    if line > 0 { ink.setStroke(); path.lineWidth = line; path.lineJoinStyle = .round; path.stroke() }
}
func oval(_ x: CGFloat, _ y: CGFloat, _ w: CGFloat, _ h: CGFloat, _ fill: NSColor, line: CGFloat = 0) {
    paint(NSBezierPath(ovalIn: NSRect(x: x, y: y, width: w, height: h)), fill, line: line)
}
func draw() {
    let tile = NSBezierPath(roundedRect: NSRect(x: 64, y: 64, width: 896, height: 896), xRadius: 196, yRadius: 196)
    NSGraphicsContext.saveGraphicsState()
    let shadow = NSShadow(); shadow.shadowColor = ink.withAlphaComponent(0.20)
    shadow.shadowBlurRadius = 24; shadow.shadowOffset = NSSize(width: 0, height: -10); shadow.set()
    color(0xf7f6f2).setFill(); tile.fill()
    NSGraphicsContext.restoreGraphicsState()
    oval(213, 192, 598, 83, color(0xdbe6e8))

    // Eight soft arms; their upper joints sit behind the head.
    let arms: [(CGFloat, CGFloat, CGFloat)] = [
        (326, 235, 184), (366, 284, 230), (418, 353, 216), (474, 451, 205),
        (550, 573, 205), (606, 671, 216), (658, 740, 230), (698, 789, 284)
    ]
    for (start, end, y) in arms {
        let p = NSBezierPath()
        p.move(to: NSPoint(x: start, y: 420))
        p.curve(to: NSPoint(x: end, y: y), controlPoint1: NSPoint(x: start, y: 315), controlPoint2: NSPoint(x: end, y: y + 65))
        p.lineWidth = 100; p.lineCapStyle = .round; ink.setStroke(); p.stroke()
        p.lineWidth = 78; (start < 418 || start > 606 ? ocean : blue).setStroke(); p.stroke()
    }
    // Ear-fins are the flapjack octopus's recognizable silhouette.
    let leftFin = NSBezierPath()
    leftFin.move(to: NSPoint(x: 343, y: 660))
    leftFin.curve(to: NSPoint(x: 218, y: 799), controlPoint1: NSPoint(x: 225, y: 642), controlPoint2: NSPoint(x: 160, y: 753))
    leftFin.curve(to: NSPoint(x: 381, y: 711), controlPoint1: NSPoint(x: 274, y: 852), controlPoint2: NSPoint(x: 355, y: 788))
    leftFin.close(); paint(leftFin, blue)
    let rightFin = leftFin.copy() as! NSBezierPath
    let mirror = AffineTransform(m11: -1, m12: 0, m21: 0, m22: 1, tX: 1024, tY: 0)
    rightFin.transform(using: mirror); paint(rightFin, blue)
    oval(220, 713, 105, 65, color(0xc9e2e6))
    oval(699, 713, 105, 65, color(0xc9e2e6))

    let head = NSBezierPath()
    head.move(to: NSPoint(x: 278, y: 405))
    head.curve(to: NSPoint(x: 512, y: 820), controlPoint1: NSPoint(x: 222, y: 654), controlPoint2: NSPoint(x: 310, y: 820))
    head.curve(to: NSPoint(x: 746, y: 405), controlPoint1: NSPoint(x: 714, y: 820), controlPoint2: NSPoint(x: 802, y: 654))
    head.curve(to: NSPoint(x: 278, y: 405), controlPoint1: NSPoint(x: 675, y: 321), controlPoint2: NSPoint(x: 349, y: 321))
    head.close(); paint(head, blue)
    oval(340, 709, 211, 66, light)
    oval(321, 435, 102, 51, color(0xe6b7a5))
    oval(601, 435, 102, 51, color(0xe6b7a5))
    for x: CGFloat in [394, 630] {
        oval(x - 47, 501, 94, 124, color(0xffffff))
        oval(x - 30, 511, 60, 102, ink)
        oval(x - 17, 567, 23, 30, .white)
        oval(x + 8, 533, 10, 12, light)
    }
    // Round navy frames stay visible at Dock sizes without obscuring the eyes.
    for x: CGFloat in [394, 630] {
        let lens = NSBezierPath(ovalIn: NSRect(x: x - 86, y: 476, width: 172, height: 172))
        ink.setStroke(); lens.lineWidth = 15; lens.stroke()
        let glint = NSBezierPath()
        glint.move(to: NSPoint(x: x - 56, y: 609))
        glint.curve(to: NSPoint(x: x - 26, y: 628), controlPoint1: NSPoint(x: x - 49, y: 620), controlPoint2: NSPoint(x: x - 38, y: 626))
        color(0xe8f8ff).withAlphaComponent(0.80).setStroke()
        glint.lineWidth = 7; glint.lineCapStyle = .round; glint.stroke()
    }
    let bridge = NSBezierPath()
    bridge.move(to: NSPoint(x: 480, y: 576))
    bridge.curve(to: NSPoint(x: 544, y: 576), controlPoint1: NSPoint(x: 497, y: 590), controlPoint2: NSPoint(x: 527, y: 590))
    bridge.move(to: NSPoint(x: 308, y: 583)); bridge.line(to: NSPoint(x: 282, y: 599))
    bridge.move(to: NSPoint(x: 716, y: 583)); bridge.line(to: NSPoint(x: 742, y: 599))
    ink.setStroke(); bridge.lineWidth = 15; bridge.lineCapStyle = .round; bridge.stroke()
    let smile = NSBezierPath()
    smile.move(to: NSPoint(x: 479, y: 470))
    smile.curve(to: NSPoint(x: 545, y: 470), controlPoint1: NSPoint(x: 491, y: 443), controlPoint2: NSPoint(x: 533, y: 443))
    smile.lineWidth = 11; smile.lineCapStyle = .round; ink.setStroke(); smile.stroke()
}

for points in [16, 32, 128, 256, 512] {
    for scale in [1, 2] {
        let size = points * scale
        let bitmap = NSBitmapImageRep(bitmapDataPlanes: nil, pixelsWide: size, pixelsHigh: size,
            bitsPerSample: 8, samplesPerPixel: 4, hasAlpha: true, isPlanar: false,
            colorSpaceName: .deviceRGB, bytesPerRow: 0, bitsPerPixel: 0)!
        NSGraphicsContext.saveGraphicsState()
        NSGraphicsContext.current = NSGraphicsContext(bitmapImageRep: bitmap)
        NSGraphicsContext.current?.imageInterpolation = .high
        let transform = NSAffineTransform(); transform.scale(by: CGFloat(size) / 1024); transform.concat()
        draw()
        NSGraphicsContext.restoreGraphicsState()
        let filename = "icon_\(points)x\(points)\(scale == 2 ? "@2x" : "").png"
        try bitmap.representation(using: .png, properties: [:])!.write(to: output.appendingPathComponent(filename))
    }
}
print("Wrote \(output.path)")

// ICNS permits PNG payloads; writing these blocks directly also works on hosts where iconutil is unavailable.
func uint32(_ value: Int) -> Data {
    var bytes = UInt32(value).bigEndian
    return withUnsafeBytes(of: &bytes) { Data($0) }
}
let blocks = [
    ("icp4", "icon_16x16.png"), ("icp5", "icon_32x32.png"), ("icp6", "icon_32x32@2x.png"),
    ("ic07", "icon_128x128.png"), ("ic08", "icon_256x256.png"), ("ic09", "icon_512x512.png"),
    ("ic10", "icon_512x512@2x.png"), ("ic11", "icon_16x16@2x.png"), ("ic12", "icon_32x32@2x.png"),
    ("ic13", "icon_128x128@2x.png"), ("ic14", "icon_256x256@2x.png")
]
var payload = Data()
for (type, filename) in blocks {
    let png = try Data(contentsOf: output.appendingPathComponent(filename))
    payload.append(Data(type.utf8)); payload.append(uint32(png.count + 8)); payload.append(png)
}
var icon = Data("icns".utf8); icon.append(uint32(payload.count + 8)); icon.append(payload)
let destination = URL(fileURLWithPath: CommandLine.arguments.dropFirst(2).first ?? "Resources/Squipi.icns")
try icon.write(to: destination, options: .atomic)
guard NSImage(contentsOf: destination)?.isValid == true else { fatalError("Generated icon cannot be read by AppKit") }
print("Wrote and verified \(destination.path)")
