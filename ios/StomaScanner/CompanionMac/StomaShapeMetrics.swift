import CoreGraphics
import Foundation
import simd

/// Named length measurements used for multi-landmark photo↔mesh scale estimation.
enum StomaMetricKind: String, CaseIterable, Identifiable, Sendable {
    case feretMajor
    case feretMinor
    case feret45
    case feret135
    case perimeter
    case sqrtArea
    case radial0
    case radial45
    case radial90
    case radial135
    case radial180
    case radial225
    case radial270
    case radial315

    var id: String { rawValue }

    var title: String {
        switch self {
        case .feretMajor: return "Feret major"
        case .feretMinor: return "Feret minor"
        case .feret45: return "Feret 45°"
        case .feret135: return "Feret 135°"
        case .perimeter: return "Perimeter"
        case .sqrtArea: return "√area"
        case .radial0: return "Radial 0°"
        case .radial45: return "Radial 45°"
        case .radial90: return "Radial 90°"
        case .radial135: return "Radial 135°"
        case .radial180: return "Radial 180°"
        case .radial225: return "Radial 225°"
        case .radial270: return "Radial 270°"
        case .radial315: return "Radial 315°"
        }
    }

    /// Relative weight for robust scale combination (length-like features preferred).
    var weight: Double {
        switch self {
        case .feretMajor, .feretMinor: return 1.4
        case .feret45, .feret135: return 1.1
        case .perimeter: return 1.0
        case .sqrtArea: return 1.2
        case .radial0, .radial45, .radial90, .radial135,
             .radial180, .radial225, .radial270, .radial315:
            return 0.7
        }
    }
}

struct StomaShapeMetrics: Equatable, Sendable {
    /// Values keyed by metric; units are whatever the input outline used (mm or scene units).
    var values: [StomaMetricKind: Double]
    var centroid: SIMD2<Double>
    var principalAngleRadians: Double

    subscript(_ kind: StomaMetricKind) -> Double? { values[kind] }

    static func compute(from points: [CGPoint]) -> StomaShapeMetrics? {
        guard points.count >= 8 else { return nil }
        var pts = points.map { SIMD2(Double($0.x), Double($0.y)) }
        // Ensure closed for perimeter/area.
        if simd_distance(pts[0], pts[pts.count - 1]) > 1e-6 {
            pts.append(pts[0])
        }

        let centroid = polygonCentroid(pts)
        let centered = pts.map { $0 - centroid }
        let angle = principalAxisAngle(centered)
        let aligned = centered.map { rotate($0, by: -angle) }

        let feretMajor = feretDiameter(aligned, axisAngle: 0)
        let feretMinor = feretDiameter(aligned, axisAngle: .pi / 2)
        let feret45 = feretDiameter(aligned, axisAngle: .pi / 4)
        let feret135 = feretDiameter(aligned, axisAngle: 3 * .pi / 4)
        let peri = polygonPerimeter(pts)
        let area = abs(signedArea(pts))
        let sqrtA = area > 0 ? sqrt(area) : 0

        var values: [StomaMetricKind: Double] = [
            .feretMajor: feretMajor,
            .feretMinor: feretMinor,
            .feret45: feret45,
            .feret135: feret135,
            .perimeter: peri,
            .sqrtArea: sqrtA,
        ]

        let radialAngles: [(StomaMetricKind, Double)] = [
            (.radial0, 0), (.radial45, .pi / 4), (.radial90, .pi / 2), (.radial135, 3 * .pi / 4),
            (.radial180, .pi), (.radial225, 5 * .pi / 4), (.radial270, 3 * .pi / 2), (.radial315, 7 * .pi / 4),
        ]
        for (kind, th) in radialAngles {
            values[kind] = radialDistance(aligned, angle: th)
        }

        // Drop non-positive measurements.
        values = values.filter { $0.value > 1e-9 }

        return StomaShapeMetrics(values: values, centroid: centroid, principalAngleRadians: angle)
    }

    /// Build metrics from mesh slice samples in the slice plane (scene units).
    static func compute(fromMeshSamples samples: [BasePlaneSample]) -> StomaShapeMetrics? {
        let pts = samples.map { CGPoint(x: CGFloat($0.x), y: CGFloat($0.y)) }
        return compute(from: pts)
    }

    // MARK: - Internals

    private static func polygonCentroid(_ pts: [SIMD2<Double>]) -> SIMD2<Double> {
        let n = pts.count
        guard n >= 3 else { return pts.first ?? .zero }
        var a = 0.0, cx = 0.0, cy = 0.0
        for i in 0 ..< (n - 1) {
            let cross = pts[i].x * pts[i + 1].y - pts[i + 1].x * pts[i].y
            a += cross
            cx += (pts[i].x + pts[i + 1].x) * cross
            cy += (pts[i].y + pts[i + 1].y) * cross
        }
        a *= 0.5
        if abs(a) < 1e-14 {
            var s = SIMD2<Double>.zero
            for p in pts { s += p }
            return s / Double(n)
        }
        return SIMD2(cx / (6 * a), cy / (6 * a))
    }

    private static func signedArea(_ pts: [SIMD2<Double>]) -> Double {
        var a = 0.0
        for i in 0 ..< (pts.count - 1) {
            a += pts[i].x * pts[i + 1].y - pts[i + 1].x * pts[i].y
        }
        return a * 0.5
    }

    private static func polygonPerimeter(_ pts: [SIMD2<Double>]) -> Double {
        var p = 0.0
        for i in 0 ..< (pts.count - 1) {
            p += simd_distance(pts[i], pts[i + 1])
        }
        return p
    }

    private static func principalAxisAngle(_ centered: [SIMD2<Double>]) -> Double {
        var cxx = 0.0, cyy = 0.0, cxy = 0.0
        for p in centered {
            cxx += p.x * p.x
            cyy += p.y * p.y
            cxy += p.x * p.y
        }
        let n = Double(max(centered.count, 1))
        cxx /= n
        cyy /= n
        cxy /= n
        // Angle of largest eigenvector of covariance.
        return 0.5 * atan2(2 * cxy, cxx - cyy)
    }

    private static func rotate(_ p: SIMD2<Double>, by angle: Double) -> SIMD2<Double> {
        let c = cos(angle), s = sin(angle)
        return SIMD2(c * p.x - s * p.y, s * p.x + c * p.y)
    }

    /// Projection span onto unit direction `(cos θ, sin θ)`.
    private static func feretDiameter(_ aligned: [SIMD2<Double>], axisAngle: Double) -> Double {
        let dir = SIMD2(cos(axisAngle), sin(axisAngle))
        var minP = Double.greatestFiniteMagnitude
        var maxP = -Double.greatestFiniteMagnitude
        for p in aligned {
            let d = simd_dot(p, dir)
            minP = min(minP, d)
            maxP = max(maxP, d)
        }
        return maxP - minP
    }

    private static func radialDistance(_ aligned: [SIMD2<Double>], angle: Double) -> Double {
        let dir = SIMD2(cos(angle), sin(angle))
        var best = 0.0
        // Ray from origin along dir; intersect polygon edges.
        for i in 0 ..< (aligned.count - 1) {
            if let t = raySegmentIntersect(origin: .zero, dir: dir, a: aligned[i], b: aligned[i + 1]), t > best {
                best = t
            }
        }
        return best
    }

    private static func raySegmentIntersect(
        origin: SIMD2<Double>,
        dir: SIMD2<Double>,
        a: SIMD2<Double>,
        b: SIMD2<Double>
    ) -> Double? {
        let v = b - a
        let wo = a - origin
        let denom = dir.x * v.y - dir.y * v.x
        if abs(denom) < 1e-12 { return nil }
        let t = (wo.x * v.y - wo.y * v.x) / denom
        let s = (dir.x * wo.y - dir.y * wo.x) / denom
        if t >= 0, s >= 0, s <= 1 { return t }
        return nil
    }
}
