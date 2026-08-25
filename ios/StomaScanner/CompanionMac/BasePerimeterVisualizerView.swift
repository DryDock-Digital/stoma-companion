import AppKit
import SwiftUI

/// 2D preview of the same **X/Y** polyline written to the G-code file (slice plane, scaled export units).
struct BasePerimeterVisualizerView: View {
    /// Scaled slice-plane points in export order (same as `BasePerimeterExport.scaledGcodePlaneXY`).
    let planeXY: [(Float, Float)]
    /// Optional Ideal Fit (wafer cut) outline — outward offset from primary.
    var idealFitXY: [(Float, Float)]? = nil
    var clearanceStats: ClearanceStats? = nil
    /// Matches G-code export: millimeters vs. meters for labels and scale bar.
    let unitsMillimeters: Bool
    /// Hide polar overlay toggle (e.g. for PNG export).
    var showPolarControls: Bool = true
    /// User-drawn validation spans; pass bindings to enable click-drag placement.
    var validationLines: Binding<OutlineValidationLines>? = nil
    var activeValidationLine: Binding<OutlineValidationLineKind>? = nil
    /// Frozen lines for PNG export when bindings are nil.
    var exportedValidationLines: OutlineValidationLines = OutlineValidationLines()

    @State private var showPolarApproximation = false

    private static let canvasSize = CGSize(width: 320, height: 320)

    private var resolvedValidationLines: OutlineValidationLines {
        validationLines?.wrappedValue ?? exportedValidationLines
    }

    private var validationDrawingEnabled: Bool {
        validationLines != nil && activeValidationLine != nil
    }

    var body: some View {
        let polarXY = PolarMotionPreview.machinePlanePath(planeXY: planeXY)
        let lay = layout(
            in: Self.canvasSize,
            primaryRaw: planeXY.map { (CGFloat($0.0), CGFloat($0.1)) },
            idealRaw: idealFitXY?.map { (CGFloat($0.0), CGFloat($0.1)) },
            polarXY: showPolarControls && showPolarApproximation ? polarXY : nil
        )
        let unit = unitsMillimeters ? "mm" : "m"
        let lines = resolvedValidationLines

        VStack(alignment: .leading, spacing: 8) {
            Text("Outline preview")
                .font(.caption)
                .foregroundStyle(.secondary)

            if idealFitXY != nil {
                Text("Blue: Primary. Green: Ideal Fit. Purple/cyan dashed: validation lines. Red dashed: longest diameter.")
                    .font(.caption2)
                    .foregroundStyle(.tertiary)
            } else if showPolarControls {
                Text("Blue: exported G1 polyline. Purple/cyan dashed: validation lines. Red dashed: longest diameter.")
                    .font(.caption2)
                    .foregroundStyle(.tertiary)
            } else {
                Text("Blue: exported G1 polyline. Purple/cyan dashed: validation lines. Red dashed: longest diameter.")
                    .font(.caption2)
                    .foregroundStyle(.tertiary)
            }

            if validationDrawingEnabled, let activeBinding = activeValidationLine {
                validationLineControls(activeBinding: activeBinding, lines: lines, unit: unit)
            }

            if showPolarControls {
                Toggle("Fixed-ω polar path preview", isOn: $showPolarApproximation)
                    .font(.caption)
                Text("Orange dashed: fixed-ω polar path (one revolution, Option A).")
                    .font(.caption2)
                    .foregroundStyle(.tertiary)
                if let plan = PolarPathExport.build(planeXY: planeXY) {
                    Text(
                        "Polar chord error: max \(formatData(CGFloat(plan.validation.maxChordErrorMm))) \(unit) · rev \(String(format: "%.1f", plan.revDurationSec)) s @ \(String(format: "%.1f", plan.rpm)) RPM"
                    )
                    .font(.caption2)
                    .foregroundStyle(.secondary)
                }
            }

            if let stats = clearanceStats {
                HStack(spacing: 6) {
                    Text(
                        "Clearance: min \(formatClearance(stats.min)) / mean \(formatClearance(stats.mean)) / max \(formatClearance(stats.max)) \(unit)"
                    )
                    .font(.caption2)
                    .foregroundStyle(.secondary)
                    Text(stats.passesSW07 ? "SW-07: Pass" : "SW-07: Fail")
                        .font(.caption2.weight(.semibold))
                        .foregroundStyle(stats.passesSW07 ? .green : .red)
                }
            }

            if lay.hasPath {
                Text(
                    "Slice size: ΔX \(formatData(lay.spanX)) × ΔY \(formatData(lay.spanY)) \(unit); closed path \(formatData(lay.pathLength)) \(unit)"
                )
                .font(.caption2)
                .foregroundStyle(.secondary)
                if lay.longestDiameterData > 1e-9 {
                    Text("Longest diameter: \(formatData(lay.longestDiameterData)) \(unit) (red dashed)")
                        .font(.caption2)
                        .foregroundStyle(.secondary)
                }
                validationLineLengthRows(lines: lines, unit: unit)
            }

            ZStack(alignment: .bottomLeading) {
                Canvas { context, _ in
                    guard lay.primaryPoints.count >= 3 else { return }

                    if let idealPts = lay.idealPoints, idealPts.count == lay.primaryPoints.count {
                        for i in 0 ..< lay.primaryPoints.count {
                            let j = (i + 1) % lay.primaryPoints.count
                            var band = Path()
                            band.move(to: lay.primaryPoints[i])
                            band.addLine(to: lay.primaryPoints[j])
                            band.addLine(to: idealPts[j])
                            band.addLine(to: idealPts[i])
                            band.closeSubpath()
                            context.fill(band, with: .color(.teal.opacity(0.18)))
                        }
                    }

                    if let polar = lay.polarPoints, polar.count >= 2 {
                        var polarPath = Path()
                        polarPath.move(to: polar[0])
                        for i in 1 ..< polar.count {
                            polarPath.addLine(to: polar[i])
                        }
                        context.stroke(
                            polarPath,
                            with: .color(.orange.opacity(0.75)),
                            style: StrokeStyle(lineWidth: 1.0, dash: [4, 3])
                        )
                    }

                    if let idealPts = lay.idealPoints, idealPts.count >= 3 {
                        var idealPath = Path()
                        idealPath.move(to: idealPts[0])
                        for i in 1 ..< idealPts.count {
                            idealPath.addLine(to: idealPts[i])
                        }
                        idealPath.closeSubpath()
                        context.stroke(idealPath, with: .color(.green.opacity(0.9)), lineWidth: 1.75)
                    }

                    var path = Path()
                    path.move(to: lay.primaryPoints[0])
                    for i in 1 ..< lay.primaryPoints.count {
                        path.addLine(to: lay.primaryPoints[i])
                    }
                    path.closeSubpath()

                    context.fill(path, with: .color(.accentColor.opacity(0.10)))
                    context.stroke(path, with: .color(.accentColor.opacity(0.85)), lineWidth: 1.5)

                    if let (d0, d1) = lay.longestDiameterEndpoints {
                        var diameterPath = Path()
                        diameterPath.move(to: d0)
                        diameterPath.addLine(to: d1)
                        context.stroke(
                            diameterPath,
                            with: .color(.red.opacity(0.92)),
                            style: StrokeStyle(lineWidth: 1.75, dash: [5, 4])
                        )
                    }

                    let dot = Path(ellipseIn: CGRect(x: lay.origin.x - 3, y: lay.origin.y - 3, width: 6, height: 6))
                    context.fill(dot, with: .color(.secondary))
                }
                .frame(width: Self.canvasSize.width, height: Self.canvasSize.height)
                .background(Color(nsColor: .textBackgroundColor).opacity(0.6))
                .clipShape(RoundedRectangle(cornerRadius: 8, style: .continuous))
                .overlay {
                    RoundedRectangle(cornerRadius: 8, style: .continuous)
                        .strokeBorder(Color.secondary.opacity(0.25), lineWidth: 1)
                }

                if validationDrawingEnabled,
                   let linesBinding = validationLines,
                   let activeBinding = activeValidationLine {
                    OutlineValidationLineEditor(
                        transform: lay.transform,
                        lines: linesBinding,
                        activeLine: activeBinding
                    )
                    .frame(width: Self.canvasSize.width, height: Self.canvasSize.height)
                    .clipShape(RoundedRectangle(cornerRadius: 8, style: .continuous))
                } else if !validationDrawingEnabled {
                    ValidationLinesOverlay(lines: lines, transform: lay.transform)
                        .frame(width: Self.canvasSize.width, height: Self.canvasSize.height)
                        .clipShape(RoundedRectangle(cornerRadius: 8, style: .continuous))
                        .allowsHitTesting(false)
                }

                if lay.hasPath, lay.scaleBarScreenWidth >= 12 {
                    VStack(alignment: .leading, spacing: 4) {
                        HStack(spacing: 0) {
                            Rectangle()
                                .fill(Color.primary.opacity(0.9))
                                .frame(width: lay.scaleBarScreenWidth, height: 4)
                            Rectangle()
                                .fill(Color.primary.opacity(0.9))
                                .frame(width: 2, height: 10)
                                .offset(y: -3)
                        }
                        Text(lay.scaleBarLabel)
                            .font(.caption2.monospacedDigit())
                            .foregroundStyle(.secondary)
                    }
                    .padding(10)
                }
            }
            .frame(maxWidth: 360, alignment: .leading)
        }
        .frame(maxWidth: 360, alignment: .leading)
    }

    @ViewBuilder
    private func validationLineControls(
        activeBinding: Binding<OutlineValidationLineKind>,
        lines: OutlineValidationLines,
        unit: String
    ) -> some View {
        VStack(alignment: .leading, spacing: 6) {
            Picker("Draw validation line", selection: activeBinding) {
                ForEach(OutlineValidationLineKind.allCases) { kind in
                    Text(kind.title).tag(kind)
                }
            }
            .pickerStyle(.segmented)
            .labelsHidden()

            Text(
                "Draw \(activeBinding.wrappedValue.title.lowercased()) on the outline, then measure the same span on the physical object with calipers."
            )
            .font(.caption2)
            .foregroundStyle(.secondary)

            HStack(spacing: 8) {
                Button("Clear lines") {
                    validationLines?.wrappedValue = OutlineValidationLines()
                }
                .font(.caption)
                .disabled(lines.line1A == nil && lines.line2A == nil)
            }
        }
    }

    @ViewBuilder
    private func validationLineLengthRows(lines: OutlineValidationLines, unit: String) -> some View {
        if let len = lines.length(kind: .line1) {
            Text("Line 1: \(formatData(CGFloat(len))) \(unit) (purple dashed)")
                .font(.caption2)
                .foregroundStyle(.secondary)
        }
        if let len = lines.length(kind: .line2) {
            Text("Line 2: \(formatData(CGFloat(len))) \(unit) (cyan dashed)")
                .font(.caption2)
                .foregroundStyle(.secondary)
        }
    }

    private func formatClearance(_ v: Float) -> String {
        unitsMillimeters ? String(format: "%.2f", v) : String(format: "%.4f", v)
    }

    private func formatData(_ v: CGFloat) -> String {
        let f = Float(v)
        if unitsMillimeters {
            if f >= 100 { return String(format: "%.0f", f) }
            if f >= 10 { return String(format: "%.1f", f) }
            return String(format: "%.2f", f)
        }
        if f >= 1 { return String(format: "%.3f", f) }
        if f >= 0.01 { return String(format: "%.4f", f) }
        return String(format: "%.6f", f)
    }

    private func closedPathLength(_ raw: [(CGFloat, CGFloat)]) -> CGFloat {
        guard raw.count >= 2 else { return 0 }
        var s: CGFloat = 0
        let n = raw.count
        for i in 0 ..< n {
            let j = (i + 1) % n
            s += hypot(raw[j].0 - raw[i].0, raw[j].1 - raw[i].1)
        }
        return s
    }

    private func scaleBarSpec(span: CGFloat, pixelsPerData: CGFloat, canvasWidth: CGFloat) -> (data: CGFloat, screen: CGFloat, label: String) {
        guard span > 1e-12, pixelsPerData > 1e-12 else { return (0, 0, "") }
        let target = max(span / 5, span * 1e-4)
        var barData = niceStepLength(target: target)
        var barPx = barData * pixelsPerData
        let maxPx = max(canvasWidth * 0.35, 48)
        while barPx > maxPx && barData > 1e-12 {
            barData = niceStepLength(target: barData * 0.35)
            barPx = barData * pixelsPerData
        }
        let unit = unitsMillimeters ? "mm" : "m"
        let label: String
        if unitsMillimeters {
            if barData >= 10 { label = String(format: "%.0f %@", barData, unit) }
            else if barData >= 1 { label = String(format: "%.1f %@", barData, unit) }
            else { label = String(format: "%.2f %@", barData, unit) }
        } else {
            label = String(format: "%.4f %@", barData, unit)
        }
        return (barData, barPx, label)
    }

    private func niceStepLength(target: CGFloat) -> CGFloat {
        guard target > 1e-15 else { return 1e-6 }
        let t = Double(target)
        let exp = floor(log10(t))
        let pow10 = pow(10.0, exp)
        let m = t / pow10
        let step: Double = m <= 1 ? 1 : m <= 2 ? 2 : m <= 5 ? 5 : 10
        return CGFloat(step * pow10)
    }

    private struct GcodePreviewLayout {
        let primaryPoints: [CGPoint]
        let idealPoints: [CGPoint]?
        let polarPoints: [CGPoint]?
        let origin: CGPoint
        let transform: OutlinePreviewTransform
        let spanX: CGFloat
        let spanY: CGFloat
        let pathLength: CGFloat
        let scaleBarScreenWidth: CGFloat
        let scaleBarLabel: String
        let longestDiameterData: CGFloat
        let longestDiameterEndpoints: (CGPoint, CGPoint)?

        var hasPath: Bool { primaryPoints.count >= 3 }
    }

    private func layout(
        in size: CGSize,
        primaryRaw: [(CGFloat, CGFloat)],
        idealRaw: [(CGFloat, CGFloat)]?,
        polarXY: [(Float, Float)]?
    ) -> GcodePreviewLayout {
        var raw = primaryRaw
        if let ideal = idealRaw { raw.append(contentsOf: ideal) }
        if let polar = polarXY { raw.append(contentsOf: polar.map { (CGFloat($0.0), CGFloat($0.1)) }) }

        guard primaryRaw.count >= 2 else {
            return GcodePreviewLayout(
                primaryPoints: [], idealPoints: nil, polarPoints: nil, origin: .zero,
                transform: OutlinePreviewTransform(scale: 1, screenOrigin: .zero),
                spanX: 0, spanY: 0, pathLength: 0, scaleBarScreenWidth: 0, scaleBarLabel: "",
                longestDiameterData: 0, longestDiameterEndpoints: nil
            )
        }

        var extentX: CGFloat = 0
        var extentY: CGFloat = 0
        for p in raw {
            extentX = max(extentX, abs(p.0))
            extentY = max(extentY, abs(p.1))
        }
        let pad: CGFloat = 20
        let w = max(size.width - 2 * pad, 1)
        let h = max(size.height - 2 * pad, 1)
        let spanX = max(extentX * 2, CGFloat(1e-6))
        let spanY = max(extentY * 2, CGFloat(1e-6))
        let scale = min(w / spanX, h / spanY)
        let ox = pad + w * 0.5
        let oy = pad + h * 0.5
        let transform = OutlinePreviewTransform(scale: scale, screenOrigin: CGPoint(x: ox, y: oy))

        func map(_ px: CGFloat, _ py: CGFloat) -> CGPoint {
            transform.toScreen(x: px, y: py)
        }

        let primaryPts = primaryRaw.map { map($0.0, $0.1) }
        let idealPts = idealRaw.map { pts in pts.map { map($0.0, $0.1) } }
        let polarPts = polarXY.map { pts in pts.map { map(CGFloat($0.0), CGFloat($0.1)) } }

        let bar = scaleBarSpec(span: max(spanX, spanY), pixelsPerData: scale, canvasWidth: w)
        let chord = Self.longestChordEndpoints(primaryRaw)
        let diameterScreen: (CGPoint, CGPoint)? = chord.map { (map($0.0.0, $0.0.1), map($0.1.0, $0.1.1)) }

        return GcodePreviewLayout(
            primaryPoints: primaryPts,
            idealPoints: idealPts,
            polarPoints: polarPts,
            origin: map(0, 0),
            transform: transform,
            spanX: spanX,
            spanY: spanY,
            pathLength: closedPathLength(primaryRaw),
            scaleBarScreenWidth: bar.screen,
            scaleBarLabel: bar.label,
            longestDiameterData: chord?.length ?? 0,
            longestDiameterEndpoints: diameterScreen
        )
    }

    private static func longestChordEndpoints(_ raw: [(CGFloat, CGFloat)]) -> (a: (CGFloat, CGFloat), b: (CGFloat, CGFloat), length: CGFloat)? {
        let n = raw.count
        guard n >= 2 else { return nil }
        var bestI = 0
        var bestJ = 1
        var best: CGFloat = 0
        for i in 0 ..< n {
            for j in (i + 1) ..< n {
                let d = hypot(raw[j].0 - raw[i].0, raw[j].1 - raw[i].1)
                if d > best {
                    best = d
                    bestI = i
                    bestJ = j
                }
            }
        }
        guard best > 1e-9 else { return nil }
        return (raw[bestI], raw[bestJ], best)
    }
}

/// Read-only validation line overlay for PNG export.
private struct ValidationLinesOverlay: NSViewRepresentable {
    let lines: OutlineValidationLines
    let transform: OutlinePreviewTransform

    func makeNSView(context: Context) -> OutlineValidationLineNSView {
        let view = OutlineValidationLineNSView()
        view.transform = transform
        view.lines = lines
        return view
    }

    func updateNSView(_ nsView: OutlineValidationLineNSView, context: Context) {
        nsView.transform = transform
        nsView.lines = lines
        nsView.needsDisplay = true
    }
}
