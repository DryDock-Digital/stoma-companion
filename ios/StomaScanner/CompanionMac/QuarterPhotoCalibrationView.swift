import AppKit
import SwiftUI

/// Which line the user is drawing on the calibration still.
enum PhotoCalibrationLineKind: Int, CaseIterable, Identifiable {
    case coin = 0
    case stoma = 1

    var id: Int { rawValue }

    func title(coin: PhotoCalibrationCoinKind) -> String {
        switch self {
        case .coin: return coin.lineLabel
        case .stoma: return "Stoma span (match slice outline)"
        }
    }
}

/// Endpoints in **image pixel space** (origin top-left, +x right, +y down).
struct PhotoCalibrationLines: Equatable {
    var coinA: CGPoint?
    var coinB: CGPoint?
    var stomaA: CGPoint?
    var stomaB: CGPoint?

    var coinPixelLength: CGFloat? {
        length(a: coinA, b: coinB)
    }

    var stomaPixelLength: CGFloat? {
        length(a: stomaA, b: stomaB)
    }

    private func length(a: CGPoint?, b: CGPoint?) -> CGFloat? {
        guard let a, let b else { return nil }
        let d = hypot(b.x - a.x, b.y - a.y)
        return d > 1 ? d : nil
    }
}

/// Top-down calibration still: drag a line for the coin, then for the stoma (same plane as the photo).
struct QuarterPhotoCalibrationEditor: NSViewRepresentable {
    let image: NSImage?
    @Binding var lines: PhotoCalibrationLines
    @Binding var activeLine: PhotoCalibrationLineKind
    /// Optional ArUco corners (TL→TR→BR→BL) and stoma outline in image pixel space.
    var overlayMarkerCorners: [CGPoint] = []
    /// Shown next to the green confirmation square when ≥ 0.
    var overlayMarkerID: Int? = nil
    var overlayContour: [CGPoint] = []

    func makeCoordinator() -> Coordinator {
        Coordinator(self)
    }

    func makeNSView(context: Context) -> CalibrationImageNSView {
        let v = CalibrationImageNSView()
        v.coordinator = context.coordinator
        return v
    }

    func updateNSView(_ nsView: CalibrationImageNSView, context: Context) {
        context.coordinator.parent = self
        nsView.coordinator = context.coordinator
        nsView.image = image
        nsView.lines = lines
        nsView.activeLine = activeLine
        nsView.overlayMarkerCorners = overlayMarkerCorners
        nsView.overlayMarkerID = overlayMarkerID
        nsView.overlayContour = overlayContour
        nsView.needsDisplay = true
    }

    final class Coordinator: NSObject {
        var parent: QuarterPhotoCalibrationEditor

        init(_ parent: QuarterPhotoCalibrationEditor) {
            self.parent = parent
        }

        func commitLine(from a: CGPoint, to b: CGPoint) {
            var next = parent.lines
            switch parent.activeLine {
            case .coin:
                next.coinA = a
                next.coinB = b
            case .stoma:
                next.stomaA = a
                next.stomaB = b
            }
            parent.lines = next
        }
    }
}

final class CalibrationImageNSView: NSView {
    weak var coordinator: QuarterPhotoCalibrationEditor.Coordinator?

    var image: NSImage?
    var lines = PhotoCalibrationLines()
    var activeLine: PhotoCalibrationLineKind = .coin
    var overlayMarkerCorners: [CGPoint] = []
    var overlayMarkerID: Int? = nil
    var overlayContour: [CGPoint] = []

    private var dragStartImage: CGPoint?
    private var dragCurrentView: CGPoint?

    override var isFlipped: Bool { true }

    override var acceptsFirstResponder: Bool { true }

    override func acceptsFirstMouse(for event: NSEvent?) -> Bool { true }

    private func imageRectInBounds() -> CGRect {
        guard let img = image, img.size.width > 1, img.size.height > 1 else { return .zero }
        let iw = img.size.width
        let ih = img.size.height
        let bw = bounds.width
        let bh = bounds.height
        guard bw > 1, bh > 1 else { return .zero }
        let scale = min(bw / iw, bh / ih)
        let rw = iw * scale
        let rh = ih * scale
        let ox = (bw - rw) * 0.5
        let oy = (bh - rh) * 0.5
        return CGRect(x: ox, y: oy, width: rw, height: rh)
    }

    /// View point (flipped, top-left origin) → pixel in full image coordinates.
    private func viewToImagePixel(_ viewPt: CGPoint) -> CGPoint? {
        guard let img = image else { return nil }
        let r = imageRectInBounds()
        guard r.width > 1, r.height > 1, r.contains(viewPt) else { return nil }
        let u = (viewPt.x - r.origin.x) / r.width
        let v = (viewPt.y - r.origin.y) / r.height
        return CGPoint(x: u * img.size.width, y: v * img.size.height)
    }

    override func mouseDown(with event: NSEvent) {
        let p = convert(event.locationInWindow, from: nil)
        dragStartImage = viewToImagePixel(p)
        dragCurrentView = p
    }

    override func mouseDragged(with event: NSEvent) {
        let p = convert(event.locationInWindow, from: nil)
        dragCurrentView = p
        needsDisplay = true
    }

    override func mouseUp(with event: NSEvent) {
        let endView = convert(event.locationInWindow, from: nil)
        defer {
            dragStartImage = nil
            dragCurrentView = nil
            needsDisplay = true
        }
        guard let a = dragStartImage, let b = viewToImagePixel(endView) else { return }
        coordinator?.commitLine(from: a, to: b)
    }

    override func draw(_ dirtyRect: NSRect) {
        super.draw(dirtyRect)
        guard let context = NSGraphicsContext.current?.cgContext else { return }

        NSColor.textBackgroundColor.withAlphaComponent(0.35).setFill()
        bounds.fill()

        let r = imageRectInBounds()
        if let img = image, r.width > 1 {
            img.draw(in: r, from: .zero, operation: .sourceOver, fraction: 1)
            NSColor.separatorColor.withAlphaComponent(0.6).setStroke()
            let border = NSBezierPath(roundedRect: r, xRadius: 4, yRadius: 4)
            border.lineWidth = 1
            border.stroke()
        } else {
            let hint =
                image == nil
                ? "Load a calibration image, then click and drag on the photo to place each line."
                : "Image size is invalid."
            let p = NSMutableParagraphStyle()
            p.alignment = .center
            let attrs: [NSAttributedString.Key: Any] = [
                .font: NSFont.systemFont(ofSize: 13),
                .foregroundColor: NSColor.secondaryLabelColor,
                .paragraphStyle: p,
            ]
            let s = NSAttributedString(string: hint, attributes: attrs)
            let pad: CGFloat = 16
            let box = bounds.insetBy(dx: pad, dy: pad)
            s.draw(with: box, options: [.usesLineFragmentOrigin, .usesFontLeading])
        }

        func strokeLine(_ a: CGPoint?, _ b: CGPoint?, color: NSColor) {
            guard let img = image, let a, let b, r.width > 1 else { return }
            let scale = r.width / img.size.width
            let va = CGPoint(x: r.origin.x + a.x * scale, y: r.origin.y + a.y * scale)
            let vb = CGPoint(x: r.origin.x + b.x * scale, y: r.origin.y + b.y * scale)
            context.setStrokeColor(color.cgColor)
            context.setLineWidth(2)
            context.move(to: va)
            context.addLine(to: vb)
            context.strokePath()
        }

        if let img = image, r.width > 1, !overlayContour.isEmpty {
            let scale = r.width / img.size.width
            context.setStrokeColor(NSColor.systemTeal.withAlphaComponent(0.9).cgColor)
            context.setLineWidth(1.5)
            for (i, p) in overlayContour.enumerated() {
                let v = CGPoint(x: r.origin.x + p.x * scale, y: r.origin.y + p.y * scale)
                if i == 0 { context.move(to: v) } else { context.addLine(to: v) }
            }
            if let first = overlayContour.first {
                let v = CGPoint(x: r.origin.x + first.x * scale, y: r.origin.y + first.y * scale)
                context.addLine(to: v)
            }
            context.strokePath()
        }

        if let img = image, r.width > 1, overlayMarkerCorners.count == 4 {
            let scale = r.width / img.size.width
            let viewCorners: [CGPoint] = overlayMarkerCorners.map { p in
                CGPoint(x: r.origin.x + p.x * scale, y: r.origin.y + p.y * scale)
            }

            // Soft green fill so the lock-on is obvious on busy photos.
            context.saveGState()
            context.beginPath()
            context.move(to: viewCorners[0])
            for i in 1..<4 { context.addLine(to: viewCorners[i]) }
            context.closePath()
            context.setFillColor(NSColor.systemGreen.withAlphaComponent(0.18).cgColor)
            context.fillPath()
            context.restoreGState()

            context.setStrokeColor(NSColor.systemGreen.cgColor)
            context.setLineWidth(3)
            context.setLineJoin(.miter)
            context.beginPath()
            context.move(to: viewCorners[0])
            for i in 1..<4 { context.addLine(to: viewCorners[i]) }
            context.closePath()
            context.strokePath()

            for v in viewCorners {
                context.setFillColor(NSColor.systemGreen.cgColor)
                context.fillEllipse(in: CGRect(x: v.x - 4, y: v.y - 4, width: 8, height: 8))
                context.setStrokeColor(NSColor.white.withAlphaComponent(0.9).cgColor)
                context.setLineWidth(1)
                context.strokeEllipse(in: CGRect(x: v.x - 4, y: v.y - 4, width: 8, height: 8))
            }

            let label: String
            if let mid = overlayMarkerID, mid >= 0 {
                label = "ArUco ID \(mid)"
            } else {
                label = "ArUco detected"
            }
            let attrs: [NSAttributedString.Key: Any] = [
                .font: NSFont.systemFont(ofSize: 11, weight: .semibold),
                .foregroundColor: NSColor.white,
            ]
            let text = NSAttributedString(string: label, attributes: attrs)
            let textSize = text.size()
            let top = viewCorners.min(by: { $0.y < $1.y }) ?? viewCorners[0]
            let labelOrigin = CGPoint(
                x: max(r.minX + 4, min(top.x - textSize.width * 0.5, r.maxX - textSize.width - 4)),
                y: max(r.minY + 4, top.y - textSize.height - 10)
            )
            let pad: CGFloat = 4
            let badge = CGRect(
                x: labelOrigin.x - pad,
                y: labelOrigin.y - pad * 0.5,
                width: textSize.width + pad * 2,
                height: textSize.height + pad
            )
            NSColor.systemGreen.setFill()
            NSBezierPath(roundedRect: badge, xRadius: 4, yRadius: 4).fill()
            text.draw(at: labelOrigin)
        }

        strokeLine(lines.coinA, lines.coinB, color: .systemYellow)
        strokeLine(lines.stomaA, lines.stomaB, color: .systemGreen)

        if let a = dragStartImage, let cur = dragCurrentView, let img = image, r.width > 1 {
            let scale = r.width / img.size.width
            let va = CGPoint(x: r.origin.x + a.x * scale, y: r.origin.y + a.y * scale)
            context.setStrokeColor(NSColor.systemOrange.cgColor)
            context.setLineWidth(1.5)
            context.move(to: va)
            context.addLine(to: cur)
            context.strokePath()
        }
    }
}
