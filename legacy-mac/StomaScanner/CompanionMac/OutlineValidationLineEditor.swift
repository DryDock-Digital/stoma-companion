import AppKit
import SwiftUI

enum OutlineValidationLineKind: Int, CaseIterable, Identifiable {
    case line1 = 0
    case line2 = 1

    var id: Int { rawValue }

    var title: String {
        switch self {
        case .line1: return "Line 1"
        case .line2: return "Line 2"
        }
    }

    var strokeColor: NSColor {
        switch self {
        case .line1: return .systemPurple
        case .line2: return .systemCyan
        }
    }
}

/// User-drawn validation spans in slice-plane / G-code coordinates (origin at shape center).
struct OutlineValidationLines: Equatable {
    var line1A: (Float, Float)?
    var line1B: (Float, Float)?
    var line2A: (Float, Float)?
    var line2B: (Float, Float)?

    static func == (lhs: OutlineValidationLines, rhs: OutlineValidationLines) -> Bool {
        optionalPairEqual(lhs.line1A, rhs.line1A)
            && optionalPairEqual(lhs.line1B, rhs.line1B)
            && optionalPairEqual(lhs.line2A, rhs.line2A)
            && optionalPairEqual(lhs.line2B, rhs.line2B)
    }

    private static func optionalPairEqual(_ a: (Float, Float)?, _ b: (Float, Float)?) -> Bool {
        switch (a, b) {
        case (nil, nil): return true
        case let (a?, b?): return a.0 == b.0 && a.1 == b.1
        default: return false
        }
    }

    func endpoints(kind: OutlineValidationLineKind) -> ((Float, Float), (Float, Float))? {
        let pair: ((Float, Float)?, (Float, Float)?) = switch kind {
        case .line1: (line1A, line1B)
        case .line2: (line2A, line2B)
        }
        guard let a = pair.0, let b = pair.1 else { return nil }
        return (a, b)
    }

    func length(kind: OutlineValidationLineKind) -> Float? {
        guard let (a, b) = endpoints(kind: kind) else { return nil }
        let dx = b.0 - a.0
        let dy = b.1 - a.1
        let d = hypot(Double(dx), Double(dy))
        return d > 1e-9 ? Float(d) : nil
    }
}

/// Maps slice-plane coordinates to the fixed outline preview canvas (origin centered).
struct OutlinePreviewTransform: Equatable {
    let scale: CGFloat
    let screenOrigin: CGPoint

    func toScreen(x: CGFloat, y: CGFloat) -> CGPoint {
        CGPoint(x: screenOrigin.x + x * scale, y: screenOrigin.y - y * scale)
    }

    func toData(_ point: CGPoint) -> (CGFloat, CGFloat) {
        let x = (point.x - screenOrigin.x) / scale
        let y = (screenOrigin.y - point.y) / scale
        return (x, y)
    }
}

struct OutlineValidationLineEditor: NSViewRepresentable {
    let transform: OutlinePreviewTransform
    @Binding var lines: OutlineValidationLines
    @Binding var activeLine: OutlineValidationLineKind

    func makeCoordinator() -> Coordinator {
        Coordinator(self)
    }

    func makeNSView(context: Context) -> OutlineValidationLineNSView {
        let view = OutlineValidationLineNSView()
        view.coordinator = context.coordinator
        return view
    }

    func updateNSView(_ nsView: OutlineValidationLineNSView, context: Context) {
        context.coordinator.parent = self
        nsView.coordinator = context.coordinator
        nsView.transform = transform
        nsView.lines = lines
        nsView.activeLine = activeLine
        nsView.needsDisplay = true
    }

    final class Coordinator: NSObject {
        var parent: OutlineValidationLineEditor

        init(_ parent: OutlineValidationLineEditor) {
            self.parent = parent
        }

        func commitLine(from a: (Float, Float), to b: (Float, Float)) {
            var next = parent.lines
            switch parent.activeLine {
            case .line1:
                next.line1A = a
                next.line1B = b
            case .line2:
                next.line2A = a
                next.line2B = b
            }
            parent.lines = next
        }
    }
}

final class OutlineValidationLineNSView: NSView {
    weak var coordinator: OutlineValidationLineEditor.Coordinator?

    var transform = OutlinePreviewTransform(scale: 1, screenOrigin: .zero)
    var lines = OutlineValidationLines()
    var activeLine: OutlineValidationLineKind = .line1

    private var dragStartData: (Float, Float)?
    private var dragCurrentView: CGPoint?

    override var isFlipped: Bool { true }

    override var acceptsFirstResponder: Bool { true }

    override func acceptsFirstMouse(for event: NSEvent?) -> Bool { true }

    override func mouseDown(with event: NSEvent) {
        let viewPoint = convert(event.locationInWindow, from: nil)
        let data = transform.toData(viewPoint)
        dragStartData = (Float(data.0), Float(data.1))
        dragCurrentView = viewPoint
    }

    override func mouseDragged(with event: NSEvent) {
        dragCurrentView = convert(event.locationInWindow, from: nil)
        needsDisplay = true
    }

    override func mouseUp(with event: NSEvent) {
        let endView = convert(event.locationInWindow, from: nil)
        defer {
            dragStartData = nil
            dragCurrentView = nil
            needsDisplay = true
        }
        guard let start = dragStartData else { return }
        let endData = transform.toData(endView)
        let end: (Float, Float) = (Float(endData.0), Float(endData.1))
        let dx = end.0 - start.0
        let dy = end.1 - start.1
        guard hypot(Double(dx), Double(dy)) > 1e-6 else { return }
        coordinator?.commitLine(from: start, to: end)
    }

    override func draw(_ dirtyRect: NSRect) {
        super.draw(dirtyRect)

        func strokeDataLine(_ a: (Float, Float)?, _ b: (Float, Float)?, color: NSColor) {
            guard let a, let b else { return }
            let va = transform.toScreen(x: CGFloat(a.0), y: CGFloat(a.1))
            let vb = transform.toScreen(x: CGFloat(b.0), y: CGFloat(b.1))
            guard let context = NSGraphicsContext.current?.cgContext else { return }
            context.setStrokeColor(color.withAlphaComponent(0.95).cgColor)
            context.setLineWidth(2)
            context.setLineDash(phase: 0, lengths: [6, 4])
            context.move(to: va)
            context.addLine(to: vb)
            context.strokePath()
            context.setLineDash(phase: 0, lengths: [])
        }

        strokeDataLine(lines.line1A, lines.line1B, color: .systemPurple)
        strokeDataLine(lines.line2A, lines.line2B, color: .systemCyan)

        if let start = dragStartData, let cur = dragCurrentView {
            let va = transform.toScreen(x: CGFloat(start.0), y: CGFloat(start.1))
            guard let context = NSGraphicsContext.current?.cgContext else { return }
            context.setStrokeColor(activeLine.strokeColor.withAlphaComponent(0.85).cgColor)
            context.setLineWidth(1.75)
            context.move(to: va)
            context.addLine(to: cur)
            context.strokePath()
        }
    }
}
