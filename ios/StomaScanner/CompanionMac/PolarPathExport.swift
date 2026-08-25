import Foundation

/// Option A fixed-ω polar plan: unwrapped φ in perimeter order, one platter revolution.
struct PolarPathSegment {
    let r0Mm: Float
    let r1Mm: Float
    let dThetaRad: Float
}

struct PolarPathValidation {
    let maxChordErrorMm: Float
    let meanChordErrorMm: Float
    let maxRadialSpeedMmPerS: Float
    let windingRad: Float
    let minRadiusMm: Float
    let passesRadiusMin: Bool
    let passesRadialSpeed: Bool
    let passesWinding: Bool
    let rayQAWarning: String?
}

struct PolarPathPlan {
    let planeXY: [(Float, Float)]
    let radiiMm: [Float]
    let phiUnwrappedRad: [Float]
    let segments: [PolarPathSegment]
    let segmentRadialSpeedMmPerS: [Float]
    let startPhiRad: Float
    let startRMm: Float
    let rpm: Float
    let centerOffsetMm: Float
    /// Original φ₀; pen path is rotated back for display in export XY.
    let rotationOffsetRad: Float
    let validation: PolarPathValidation

    var revDurationSec: Float { rpm > 0 ? 60 / rpm : 0 }
}

enum PolarPathExport {
    /// Positive area = CCW in standard math coords (+Y up). Swift/atan2 same — keep CCW traversal.
    private static func signedArea(_ poly: [(Float, Float)]) -> Float {
        let n = poly.count
        guard n >= 3 else { return 0 }
        var a: Float = 0
        for i in 0 ..< n {
            let j = (i + 1) % n
            a += poly[i].0 * poly[j].1 - poly[j].0 * poly[i].1
        }
        return a * 0.5
    }

    /// Keep index 0 fixed; reverse remainder so angles increase monotonically 0 → 2π.
    private static func ensureCCWPerimeter(_ poly: [(Float, Float)]) -> [(Float, Float)] {
        guard poly.count >= 3 else { return poly }
        if signedArea(poly) >= 0 { return poly }
        let p0 = poly[0]
        let rest = Array(poly.dropFirst().reversed())
        return [p0] + rest
    }


    static let formatVersion = "stoma_polar_v1"
    static let defaultRPM: Float = 3
    static let centerFromHomeMm: Float = 38
    static let minRadiusMm: Float = 2
    static let defaultMaxRadialSpeedMmPerS: Float = 8
    static let polarFileName = "base_perimeter.polar"

    static func build(
        planeXY: [(Float, Float)],
        rpm: Float = defaultRPM,
        maxRadialSpeedMmPerS: Float = defaultMaxRadialSpeedMmPerS,
        samplesPerSegment: Int = 8
    ) -> PolarPathPlan? {
        guard planeXY.count >= 3 else { return nil }
        let n = planeXY.count

        var phiRaw = [Float]()
        for p in planeXY { phiRaw.append(atan2(p.1, p.0)) }
        let phi0 = phiRaw[0]
        let c0 = cos(-phi0)
        let s0 = sin(-phi0)
        var rotatedXY: [(Float, Float)] = planeXY.map { p in
            (p.0 * c0 - p.1 * s0, p.0 * s0 + p.1 * c0)
        }
        rotatedXY = ensureCCWPerimeter(rotatedXY)

        var radii = [Float]()
        var phiUnwrapped = [Float]()
        for p in rotatedXY {
            radii.append(hypot(p.0, p.1))
            phiUnwrapped.append(atan2(p.1, p.0))
        }
        for i in 1 ..< n {
            var d = phiUnwrapped[i] - phiUnwrapped[i - 1]
            while d > Float.pi { d -= 2 * Float.pi }
            while d < -Float.pi { d += 2 * Float.pi }
            phiUnwrapped[i] = phiUnwrapped[i - 1] + d
        }

        var segments = [PolarPathSegment]()
        var speeds = [Float]()
        let omega = rpm * 2 * Float.pi / 60
        for i in 0 ..< n {
            let r0 = radii[i]
            let r1 = radii[(i + 1) % n]
            let dTheta: Float = i < n - 1
                ? phiUnwrapped[i + 1] - phiUnwrapped[i]
                : (2 * Float.pi - phiUnwrapped[n - 1])
            segments.append(PolarPathSegment(r0Mm: r0, r1Mm: r1, dThetaRad: dTheta))
            speeds.append(abs(dTheta) > 1e-9 ? abs(r1 - r0) * omega / abs(dTheta) : 0)
        }

        let winding = segments.reduce(0) { $0 + $1.dThetaRad }
        let minR = radii.min() ?? 0
        let maxV = speeds.max() ?? 0
        let chord = chordErrorMetrics(
            planeXY: rotatedXY,
            phiUnwrapped: phiUnwrapped,
            segments: segments,
            samplesPerSegment: samplesPerSegment
        )

        let validation = PolarPathValidation(
            maxChordErrorMm: chord.max,
            meanChordErrorMm: chord.mean,
            maxRadialSpeedMmPerS: maxV,
            windingRad: winding,
            minRadiusMm: minR,
            passesRadiusMin: minR >= minRadiusMm,
            passesRadialSpeed: maxV <= maxRadialSpeedMmPerS,
            passesWinding: abs(winding - 2 * Float.pi) < 0.15 && segments.allSatisfy { $0.dThetaRad > 0 && $0.dThetaRad <= Float.pi },
            rayQAWarning: optionalRayQAWarning(planeXY: planeXY)
        )

        return PolarPathPlan(
            planeXY: planeXY,
            radiiMm: radii,
            phiUnwrappedRad: phiUnwrapped,
            segments: segments,
            segmentRadialSpeedMmPerS: speeds,
            startPhiRad: 0,
            startRMm: radii[0],
            rpm: rpm,
            centerOffsetMm: centerFromHomeMm,
            rotationOffsetRad: phi0,
            validation: validation
        )
    }

    static func penPathMachinePlane(plan: PolarPathPlan, samplesPerSegment: Int = 8) -> [(Float, Float)] {
        guard samplesPerSegment >= 2 else { return [] }
        var path: [(Float, Float)] = []
        var theta = plan.startPhiRad
        path.append((plan.startRMm * cos(theta), plan.startRMm * sin(theta)))
        for seg in plan.segments {
            for j in 1 ... samplesPerSegment {
                let t = Float(j) / Float(samplesPerSegment)
                let th = theta + seg.dThetaRad * t
                let r = seg.r0Mm + (seg.r1Mm - seg.r0Mm) * t
                path.append((r * cos(th), r * sin(th)))
            }
            theta += seg.dThetaRad
        }
        let c = cos(plan.rotationOffsetRad)
        let s = sin(plan.rotationOffsetRad)
        return path.map { p in
            (p.0 * c - p.1 * s, p.0 * s + p.1 * c)
        }
    }

    static func polarFileText(plan: PolarPathPlan) -> String {
        var lines: [String] = []
        lines.append(";\(formatVersion) RPM=\(formatF(plan.rpm)) COUNT=\(plan.segments.count)")
        lines.append("; CENTER_MM=\(formatF(plan.centerOffsetMm)) WINDING_RAD=\(formatF(plan.validation.windingRad))")
        lines.append("; MAX_CHORD_ERR_MM=\(formatF(plan.validation.maxChordErrorMm)) MAX_VR_MM_S=\(formatF(plan.validation.maxRadialSpeedMmPerS))")
        lines.append("; START_PHI_RAD=0 START_R_MM=\(formatF(plan.startRMm)) ROTATED_ORIG_PHI0=\(formatF(plan.phiUnwrappedRad.first ?? 0))")
        lines.append("M200 S\(formatF(plan.rpm))")
        lines.append("M201 S \(formatF(plan.startPhiRad)) \(formatF(plan.startRMm))")
        lines.append("M201 Q\(plan.segments.count)")
        for seg in plan.segments {
            lines.append("M201 P \(formatF(seg.r1Mm)) \(formatF(seg.dThetaRad))")
        }
        lines.append("M202")
        return lines.joined(separator: "\n") + "\n"
    }

    private static func formatF(_ v: Float) -> String { String(format: "%.6f", v) }

    private static func chordErrorMetrics(
        planeXY: [(Float, Float)],
        phiUnwrapped: [Float],
        segments: [PolarPathSegment],
        samplesPerSegment: Int
    ) -> (max: Float, mean: Float) {
        let n = planeXY.count
        var maxErr: Float = 0
        var sumErr: Float = 0
        var count: Float = 0
        for i in 0 ..< n {
            let a = planeXY[i]
            let b = planeXY[(i + 1) % n]
            let seg = segments[i]
            let theta0 = phiUnwrapped[i]
            for j in 1 ... samplesPerSegment {
                let t = Float(j) / Float(samplesPerSegment)
                let th = theta0 + seg.dThetaRad * t
                let r = seg.r0Mm + (seg.r1Mm - seg.r0Mm) * t
                let err = distanceToSegment(
                    px: r * cos(th), py: r * sin(th),
                    ax: a.0, ay: a.1, bx: b.0, by: b.1
                )
                maxErr = max(maxErr, err)
                sumErr += err
                count += 1
            }
        }
        return (maxErr, count > 0 ? sumErr / count : 0)
    }

    private static func distanceToSegment(
        px: Float, py: Float, ax: Float, ay: Float, bx: Float, by: Float
    ) -> Float {
        let dx = bx - ax, dy = by - ay
        let len2 = dx * dx + dy * dy
        if len2 < 1e-12 { return hypot(px - ax, py - ay) }
        var t = ((px - ax) * dx + (py - ay) * dy) / len2
        t = min(max(t, 0), 1)
        return hypot(px - (ax + t * dx), py - (ay + t * dy))
    }

    private static func optionalRayQAWarning(planeXY: [(Float, Float)]) -> String? {
        let samples = 72
        var bad = 0
        for i in 0 ..< samples {
            let theta = -Float.pi + (2 * Float.pi * Float(i) / Float(samples))
            if rayIntersectionCount(polygon: planeXY, theta: theta) != 1 { bad += 1 }
        }
        if bad > samples / 10 {
            return "Ray QA: \(bad)/\(samples) angles do not hit outline exactly once (informational)."
        }
        return nil
    }

    private static func rayIntersectionCount(polygon: [(Float, Float)], theta: Float) -> Int {
        let dx = cos(theta), dy = sin(theta)
        let n = polygon.count
        var hits = 0
        for i in 0 ..< n {
            let j = (i + 1) % n
            let x1 = polygon[i].0, y1 = polygon[i].1
            let x2 = polygon[j].0, y2 = polygon[j].1
            let sx = x2 - x1, sy = y2 - y1
            let denom = sx * dy - sy * dx
            if abs(denom) < 1e-12 { continue }
            let t = (x1 * dy - y1 * dx) / denom
            let u = (x1 * sy - y1 * sx) / denom
            if t >= 0 && t <= 1 && u >= 0 { hits += 1 }
        }
        return hits
    }
}
