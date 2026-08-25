import AppKit
import CoreGraphics
import Foundation
import Vision

enum PhotoStomaContourError: Error, LocalizedError {
    case noCGImage
    case visionFailed(String)
    case noContour
    case touchesImageEdge
    case tooSmall
    case fragmented

    var errorDescription: String? {
        switch self {
        case .noCGImage: return "Could not read a CGImage from the calibration still."
        case let .visionFailed(m): return m
        case .noContour: return "Could not segment a stoma outline in the photo."
        case .touchesImageEdge: return "Detected outline touches the image edge — reframe so the stoma is fully inside."
        case .tooSmall: return "Detected outline is too small relative to the image."
        case .fragmented: return "Detected outline is fragmented; try better lighting or a cleaner background."
        }
    }
}

struct PhotoStomaContourResult: Equatable {
    /// Closed outline in image pixel space (top-left origin, +y down).
    var pixelPoints: [CGPoint]
    /// Same outline mapped to marker-plane millimeters via ArUco homography.
    var millimeterPoints: [CGPoint]
    var confidence: Double
    var areaPixels: Double
    var note: String
}

/// Vision-based stoma outline from a top-down calibration still, excluding the ArUco marker polygon.
enum PhotoStomaContourDetector {
    static func detect(
        in image: NSImage,
        excludingMarkerCorners markerCorners: [CGPoint]?,
        homographyRowMajor: [Double]?
    ) throws -> PhotoStomaContourResult {
        guard let cgImage = image.cgImage(forProposedRect: nil, context: nil, hints: nil) else {
            throw PhotoStomaContourError.noCGImage
        }
        let width = CGFloat(cgImage.width)
        let height = CGFloat(cgImage.height)

        let contour = try bestSubjectContour(cgImage: cgImage, width: width, height: height)
        var filtered = contour
        if let marker = markerCorners, marker.count >= 3 {
            filtered = contour.filter { !pointInPolygon($0, marker) }
            // If filtering removed most points, keep original and shrink away from marker AABB.
            if filtered.count < max(12, contour.count / 4) {
                filtered = contour.filter { !pointNearPolygon($0, marker, margin: min(width, height) * 0.02) }
            }
        }
        guard filtered.count >= 16 else { throw PhotoStomaContourError.fragmented }

        let simplified = resampleClosed(filtered, count: 120)
        guard simplified.count >= 16 else { throw PhotoStomaContourError.noContour }

        if touchesImageEdge(simplified, width: width, height: height, margin: 2) {
            throw PhotoStomaContourError.touchesImageEdge
        }

        let areaPx = abs(signedArea(simplified))
        let imgArea = Double(width * height)
        guard areaPx > imgArea * 0.002 else { throw PhotoStomaContourError.tooSmall }
        guard areaPx < imgArea * 0.85 else { throw PhotoStomaContourError.fragmented }

        var mmPoints: [CGPoint] = []
        if let H = homographyRowMajor, H.count == 9 {
            let numbers = H.map { NSNumber(value: $0) }
            mmPoints = simplified.map { ArUcoDetectorBridge.applyHomography(numbers, toPixel: $0) }
        }

        let conf = min(0.95, max(0.35, 0.55 + 0.4 * (areaPx / imgArea).squareRoot()))
        return PhotoStomaContourResult(
            pixelPoints: simplified,
            millimeterPoints: mmPoints,
            confidence: conf,
            areaPixels: areaPx,
            note: String(format: "Outline %d pts · area %.0f px²", simplified.count, areaPx)
        )
    }

    // MARK: - Vision

    private static func bestSubjectContour(cgImage: CGImage, width: CGFloat, height: CGFloat) throws -> [CGPoint] {
        // Prefer instance masks when available; fall back to contour detection.
        if let fromMask = try? contourFromForegroundMask(cgImage: cgImage, width: width, height: height),
           fromMask.count >= 16 {
            return fromMask
        }
        return try contourFromVNContours(cgImage: cgImage, width: width, height: height)
    }

    private static func contourFromForegroundMask(cgImage: CGImage, width: CGFloat, height: CGFloat) throws -> [CGPoint] {
        let request = VNGenerateForegroundInstanceMaskRequest()
        let handler = VNImageRequestHandler(cgImage: cgImage, orientation: .up, options: [:])
        do {
            try handler.perform([request])
        } catch {
            throw PhotoStomaContourError.visionFailed(error.localizedDescription)
        }
        guard let result = request.results?.first else {
            throw PhotoStomaContourError.noContour
        }

        // Pick the largest instance (by mask pixel count).
        var bestInstances: IndexSet?
        var bestCount = 0
        for idx in result.allInstances {
            let set = IndexSet(integer: idx)
            guard let mask = try? result.generateMask(forInstances: set) else { continue }
            let count = nonzeroCount(mask)
            if count > bestCount {
                bestCount = count
                bestInstances = set
            }
        }
        guard let bestInstances,
              let mask = try? result.generateMask(forInstances: bestInstances) else {
            throw PhotoStomaContourError.noContour
        }

        return try contourFromPixelBufferMask(mask, imageWidth: width, imageHeight: height)
    }

    private static func nonzeroCount(_ buffer: CVPixelBuffer) -> Int {
        CVPixelBufferLockBaseAddress(buffer, .readOnly)
        defer { CVPixelBufferUnlockBaseAddress(buffer, .readOnly) }
        guard let base = CVPixelBufferGetBaseAddress(buffer) else { return 0 }
        let w = CVPixelBufferGetWidth(buffer)
        let h = CVPixelBufferGetHeight(buffer)
        let stride = CVPixelBufferGetBytesPerRow(buffer)
        let ptr = base.assumingMemoryBound(to: Float.self)
        // VN masks are often Float32 one-channel; also handle UInt8.
        let format = CVPixelBufferGetPixelFormatType(buffer)
        var count = 0
        if format == kCVPixelFormatType_OneComponent8 {
            let u8 = base.assumingMemoryBound(to: UInt8.self)
            for y in 0 ..< h {
                for x in 0 ..< w {
                    if u8[y * stride + x] > 0 { count += 1 }
                }
            }
        } else {
            let floatsPerRow = stride / MemoryLayout<Float>.size
            for y in 0 ..< h {
                for x in 0 ..< w {
                    if ptr[y * floatsPerRow + x] > 0.5 { count += 1 }
                }
            }
        }
        return count
    }

    private static func contourFromPixelBufferMask(
        _ buffer: CVPixelBuffer,
        imageWidth: CGFloat,
        imageHeight: CGFloat
    ) throws -> [CGPoint] {
        CVPixelBufferLockBaseAddress(buffer, .readOnly)
        defer { CVPixelBufferUnlockBaseAddress(buffer, .readOnly) }
        guard let base = CVPixelBufferGetBaseAddress(buffer) else { throw PhotoStomaContourError.noContour }
        let mw = CVPixelBufferGetWidth(buffer)
        let mh = CVPixelBufferGetHeight(buffer)
        let stride = CVPixelBufferGetBytesPerRow(buffer)
        let format = CVPixelBufferGetPixelFormatType(buffer)

        func isOn(_ x: Int, _ y: Int) -> Bool {
            if format == kCVPixelFormatType_OneComponent8 {
                let u8 = base.assumingMemoryBound(to: UInt8.self)
                return u8[y * stride + x] > 0
            }
            let floatsPerRow = stride / MemoryLayout<Float>.size
            let ptr = base.assumingMemoryBound(to: Float.self)
            return ptr[y * floatsPerRow + x] > 0.5
        }

        // Collect boundary pixels (mask on, neighbor off).
        var boundary: [CGPoint] = []
        boundary.reserveCapacity(1024)
        for y in 1 ..< (mh - 1) {
            for x in 1 ..< (mw - 1) {
                guard isOn(x, y) else { continue }
                if !isOn(x - 1, y) || !isOn(x + 1, y) || !isOn(x, y - 1) || !isOn(x, y + 1) {
                    let px = CGFloat(x) / CGFloat(mw) * imageWidth
                    // Vision / CVPixelBuffer often bottom-left; our image space is top-left.
                    let py = (1 - CGFloat(y) / CGFloat(mh)) * imageHeight
                    boundary.append(CGPoint(x: px, y: py))
                }
            }
        }
        guard boundary.count >= 16 else { throw PhotoStomaContourError.noContour }
        return orderBoundaryClockwise(boundary)
    }

    private static func contourFromVNContours(cgImage: CGImage, width: CGFloat, height: CGFloat) throws -> [CGPoint] {
        let request = VNDetectContoursRequest()
        request.detectsDarkOnLight = false
        request.contrastAdjustment = 1.0
        request.maximumImageDimension = 1024
        let handler = VNImageRequestHandler(cgImage: cgImage, orientation: .up, options: [:])
        do {
            try handler.perform([request])
        } catch {
            throw PhotoStomaContourError.visionFailed(error.localizedDescription)
        }
        guard let obs = request.results?.first else { throw PhotoStomaContourError.noContour }

        var best: [CGPoint] = []
        var bestArea: Double = 0
        func walk(_ c: VNContour) {
            let pts = c.normalizedPoints
            var poly: [CGPoint] = []
            poly.reserveCapacity(pts.count)
            for i in 0 ..< pts.count {
                let p = pts[i]
                // Vision normalized: origin bottom-left.
                poly.append(CGPoint(x: CGFloat(p.x) * width, y: (1 - CGFloat(p.y)) * height))
            }
            let a = abs(signedArea(poly))
            if a > bestArea {
                bestArea = a
                best = poly
            }
            for child in c.childContours {
                walk(child)
            }
        }
        for c in obs.topLevelContours {
            walk(c)
        }
        guard best.count >= 16 else { throw PhotoStomaContourError.noContour }
        return best
    }

    // MARK: - Geometry

    private static func orderBoundaryClockwise(_ pts: [CGPoint]) -> [CGPoint] {
        guard !pts.isEmpty else { return [] }
        let cx = pts.map(\.x).reduce(0, +) / CGFloat(pts.count)
        let cy = pts.map(\.y).reduce(0, +) / CGFloat(pts.count)
        return pts.sorted { a, b in
            atan2(a.y - cy, a.x - cx) < atan2(b.y - cy, b.x - cx)
        }
    }

    private static func resampleClosed(_ pts: [CGPoint], count: Int) -> [CGPoint] {
        guard pts.count >= 3, count >= 3 else { return pts }
        var edge: [CGFloat] = []
        var total: CGFloat = 0
        for i in 0 ..< pts.count {
            let j = (i + 1) % pts.count
            let d = hypot(pts[j].x - pts[i].x, pts[j].y - pts[i].y)
            edge.append(d)
            total += d
        }
        guard total > 1 else { return pts }
        var out: [CGPoint] = []
        out.reserveCapacity(count)
        for k in 0 ..< count {
            let target = total * CGFloat(k) / CGFloat(count)
            var walked: CGFloat = 0
            for e in 0 ..< pts.count {
                let el = edge[e]
                if walked + el >= target - 1e-4 {
                    let t = (target - walked) / max(el, 1e-6)
                    let a = pts[e]
                    let b = pts[(e + 1) % pts.count]
                    out.append(CGPoint(x: a.x + t * (b.x - a.x), y: a.y + t * (b.y - a.y)))
                    break
                }
                walked += el
            }
        }
        return out
    }

    private static func signedArea(_ poly: [CGPoint]) -> Double {
        guard poly.count >= 3 else { return 0 }
        var a: Double = 0
        for i in 0 ..< poly.count {
            let j = (i + 1) % poly.count
            a += Double(poly[i].x * poly[j].y - poly[j].x * poly[i].y)
        }
        return a * 0.5
    }

    private static func touchesImageEdge(_ poly: [CGPoint], width: CGFloat, height: CGFloat, margin: CGFloat) -> Bool {
        poly.contains { p in
            p.x <= margin || p.y <= margin || p.x >= width - margin || p.y >= height - margin
        }
    }

    private static func pointInPolygon(_ p: CGPoint, _ poly: [CGPoint]) -> Bool {
        guard poly.count >= 3 else { return false }
        var inside = false
        var j = poly.count - 1
        for i in 0 ..< poly.count {
            let pi = poly[i], pj = poly[j]
            let intersect = ((pi.y > p.y) != (pj.y > p.y))
                && (p.x < (pj.x - pi.x) * (p.y - pi.y) / max(pj.y - pi.y, 1e-9) + pi.x)
            if intersect { inside.toggle() }
            j = i
        }
        return inside
    }

    private static func pointNearPolygon(_ p: CGPoint, _ poly: [CGPoint], margin: CGFloat) -> Bool {
        if pointInPolygon(p, poly) { return true }
        for i in 0 ..< poly.count {
            let a = poly[i], b = poly[(i + 1) % poly.count]
            if distancePointToSegment(p, a, b) <= margin { return true }
        }
        return false
    }

    private static func distancePointToSegment(_ p: CGPoint, _ a: CGPoint, _ b: CGPoint) -> CGFloat {
        let abx = b.x - a.x, aby = b.y - a.y
        let len2 = abx * abx + aby * aby
        guard len2 > 1e-8 else { return hypot(p.x - a.x, p.y - a.y) }
        var t = ((p.x - a.x) * abx + (p.y - a.y) * aby) / len2
        t = max(0, min(1, t))
        let x = a.x + t * abx, y = a.y + t * aby
        return hypot(p.x - x, p.y - y)
    }
}
