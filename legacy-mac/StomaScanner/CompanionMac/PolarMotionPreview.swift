import Foundation

/// Fixed-ω polar pen path (Option A) for StomaPlotter v2 motion preview.
enum PolarMotionPreview {
    /// Vertices in export order — one revolution, linear r vs θ per segment.
    static func machinePlanePath(planeXY: [(Float, Float)], rpm: Float = PolarPathExport.defaultRPM) -> [(Float, Float)] {
        guard let plan = PolarPathExport.build(planeXY: planeXY, rpm: rpm) else { return [] }
        return PolarPathExport.penPathMachinePlane(plan: plan)
    }

    /// Legacy chord/arc approximation (pre-v2 Cartesian executor).
    static func legacyMachinePlanePath(planeXY: [(Float, Float)]) -> [(Float, Float)] {
        guard let first = planeXY.first, planeXY.count >= 2 else { return [] }
        let segmentMM: Float = 0.35
        var path: [(Float, Float)] = [(0, 0)]
        path.append(contentsOf: arcLeadIn(to: first, segmentMM: segmentMM))
        for i in 0 ..< (planeXY.count - 1) {
            path.append(contentsOf: chordSubsteps(from: planeXY[i], to: planeXY[i + 1], segmentMM: segmentMM))
        }
        path.append(contentsOf: chordSubsteps(from: planeXY[planeXY.count - 1], to: first, segmentMM: segmentMM))
        return path
    }

    private static func arcLeadIn(to p1: (Float, Float), segmentMM: Float) -> [(Float, Float)] {
        let r1 = hypot(p1.0, p1.1)
        guard r1 >= 0.5 else { return [] }
        let theta1 = atan2(p1.1, p1.0)
        var dTheta = theta1
        if abs(dTheta) > Float.pi { dTheta += dTheta > 0 ? -2 * Float.pi : 2 * Float.pi }
        var n = Int(r1 / segmentMM) + Int(abs(dTheta) * r1 / segmentMM) + 2
        n = min(max(n, 4), 120)
        var out: [(Float, Float)] = []
        for i in 1 ... n {
            let t = Float(i) / Float(n)
            let phi = dTheta * t
            out.append((r1 * cos(phi), r1 * sin(phi)))
        }
        return out
    }

    private static func chordSubsteps(
        from a: (Float, Float), to b: (Float, Float), segmentMM: Float
    ) -> [(Float, Float)] {
        let dx = b.0 - a.0, dy = b.1 - a.1
        let len = hypot(dx, dy)
        if len < 0.001 { return [] }
        if len <= segmentMM { return [b] }
        var n = Int(len / segmentMM)
        if n < 2 { n = 2 }
        var out: [(Float, Float)] = []
        for i in 1 ... n {
            let t = Float(i) / Float(n)
            out.append((a.0 + dx * t, a.1 + dy * t))
        }
        return out
    }
}
