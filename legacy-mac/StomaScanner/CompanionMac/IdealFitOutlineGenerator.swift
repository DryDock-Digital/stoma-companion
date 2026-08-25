import Foundation

struct ClearanceStats: Sendable, Equatable {
    let min: Float
    let mean: Float
    let max: Float
    let p95: Float
    /// Shortest distance from each Ideal Fit sample to the primary polyline.
    let perSample: [Float]
    let targetMM: Float
    let toleranceMM: Float

    var passesSW07: Bool {
        let lo = targetMM - toleranceMM
        let hi = targetMM + toleranceMM
        return mean >= lo && mean <= hi && max <= hi
    }
}

struct IdealFitResult: Sendable {
    let points: [(Float, Float)]
    let clearanceStats: ClearanceStats
    let usedRadialFallbackIndices: [Int]

    var passesSW07: Bool { clearanceStats.passesSW07 }
}

enum IdealFitOutlineGenerator {
    static let defaultClearanceMM: Float = 3.0
    static let defaultToleranceMM: Float = 1.0

    static func generate(
        primary: [(Float, Float)],
        clearanceMM: Float = defaultClearanceMM,
        toleranceMM: Float = defaultToleranceMM
    ) -> IdealFitResult? {
        guard primary.count >= 3 else { return nil }

        let centroid = ringCentroid(primary)
        var ideal: [(Float, Float)] = []
        ideal.reserveCapacity(primary.count)
        var radialFallback: [Int] = []

        for i in 0 ..< primary.count {
            let prev = primary[(i - 1 + primary.count) % primary.count]
            let cur = primary[i]
            let next = primary[(i + 1) % primary.count]

            if let n = outwardNormal(at: cur, prev: prev, next: next, centroid: centroid) {
                ideal.append((cur.0 + clearanceMM * n.0, cur.1 + clearanceMM * n.1))
            } else {
                radialFallback.append(i)
                ideal.append(radialOffsetPoint(cur, centroid: centroid, distance: clearanceMM))
            }
        }

        if segmentsIntersectSelf(ideal) {
            ideal = primary.map { radialOffsetPoint($0, centroid: centroid, distance: clearanceMM) }
            radialFallback = Array(0 ..< primary.count)
        }

        let stats = measureClearance(primary: primary, ideal: ideal, targetMM: clearanceMM, toleranceMM: toleranceMM)
        return IdealFitResult(points: ideal, clearanceStats: stats, usedRadialFallbackIndices: radialFallback)
    }

    static func measureClearance(
        primary: [(Float, Float)],
        ideal: [(Float, Float)],
        targetMM: Float = defaultClearanceMM,
        toleranceMM: Float = defaultToleranceMM
    ) -> ClearanceStats {
        let distances = ideal.map { shortestDistanceToPolyline(point: $0, polyline: primary) }
        guard !distances.isEmpty else {
            return ClearanceStats(min: 0, mean: 0, max: 0, p95: 0, perSample: [], targetMM: targetMM, toleranceMM: toleranceMM)
        }
        let sorted = distances.sorted()
        let sum = distances.reduce(0, +)
        let mean = sum / Float(distances.count)
        let p95Index = min(sorted.count - 1, Int(Float(sorted.count) * 0.95))
        return ClearanceStats(
            min: sorted.first ?? 0,
            mean: mean,
            max: sorted.last ?? 0,
            p95: sorted[p95Index],
            perSample: distances,
            targetMM: targetMM,
            toleranceMM: toleranceMM
        )
    }

    /// Width and height of axis-aligned bounding box (SW-01).
    static func axisAlignedSpan(primary: [(Float, Float)]) -> (width: Float, height: Float) {
        guard !primary.isEmpty else { return (0, 0) }
        let xs = primary.map(\.0)
        let ys = primary.map(\.1)
        return ((xs.max() ?? 0) - (xs.min() ?? 0), (ys.max() ?? 0) - (ys.min() ?? 0))
    }

    // MARK: - Private

    private static func ringCentroid(_ ring: [(Float, Float)]) -> (Float, Float) {
        var sx: Float = 0
        var sy: Float = 0
        for p in ring {
            sx += p.0
            sy += p.1
        }
        let n = Float(ring.count)
        return (sx / n, sy / n)
    }

    private static func normalize2(_ v: (Float, Float)) -> (Float, Float)? {
        let len = hypot(v.0, v.1)
        guard len > 1e-8 else { return nil }
        return (v.0 / len, v.1 / len)
    }

    private static func outwardNormal(
        at cur: (Float, Float),
        prev: (Float, Float),
        next: (Float, Float),
        centroid: (Float, Float)
    ) -> (Float, Float)? {
        guard let t1 = normalize2((cur.0 - prev.0, cur.1 - prev.1)),
              let t2 = normalize2((next.0 - cur.0, next.1 - cur.1)) else { return nil }
        var tx = t1.0 + t2.0
        var ty = t1.1 + t2.1
        guard let tangent = normalize2((tx, ty)) else { return nil }

        var nx = -tangent.1
        var ny = tangent.0
        let vx = cur.0 - centroid.0
        let vy = cur.1 - centroid.1
        if nx * vx + ny * vy < 0 {
            nx = -nx
            ny = -ny
        }
        return (nx, ny)
    }

    private static func radialOffsetPoint(_ p: (Float, Float), centroid: (Float, Float), distance: Float) -> (Float, Float) {
        let vx = p.0 - centroid.0
        let vy = p.1 - centroid.1
        guard let dir = normalize2((vx, vy)) else { return (p.0 + distance, p.1) }
        return (p.0 + distance * dir.0, p.1 + distance * dir.1)
    }

    private static func segmentsIntersectSelf(_ ring: [(Float, Float)]) -> Bool {
        let n = ring.count
        guard n >= 4 else { return false }
        for i in 0 ..< n {
            let a0 = ring[i]
            let a1 = ring[(i + 1) % n]
            let jStart = i + 2
            guard jStart < n else { continue }
            for j in jStart ..< n {
                if j == n - 1 && i == 0 { continue }
                let b0 = ring[j]
                let b1 = ring[(j + 1) % n]
                if segmentsIntersect(a0, a1, b0, b1) { return true }
            }
        }
        return false
    }

    private static func segmentsIntersect(
        _ a0: (Float, Float), _ a1: (Float, Float),
        _ b0: (Float, Float), _ b1: (Float, Float)
    ) -> Bool {
        func cross(_ o: (Float, Float), _ a: (Float, Float), _ b: (Float, Float)) -> Float {
            (a.0 - o.0) * (b.1 - o.1) - (a.1 - o.1) * (b.0 - o.0)
        }
        let d1 = cross(a0, a1, b0)
        let d2 = cross(a0, a1, b1)
        let d3 = cross(b0, b1, a0)
        let d4 = cross(b0, b1, a1)
        if ((d1 > 0 && d2 < 0) || (d1 < 0 && d2 > 0)) && ((d3 > 0 && d4 < 0) || (d3 < 0 && d4 > 0)) {
            return true
        }
        return false
    }

    private static func shortestDistanceToPolyline(point: (Float, Float), polyline: [(Float, Float)]) -> Float {
        guard polyline.count >= 2 else { return 0 }
        var best = Float.greatestFiniteMagnitude
        let n = polyline.count
        for i in 0 ..< n {
            let a = polyline[i]
            let b = polyline[(i + 1) % n]
            best = min(best, distancePointToSegment(point: point, segA: a, segB: b))
        }
        return best
    }

    private static func distancePointToSegment(
        point: (Float, Float),
        segA: (Float, Float),
        segB: (Float, Float)
    ) -> Float {
        let dx = segB.0 - segA.0
        let dy = segB.1 - segA.1
        let lenSq = dx * dx + dy * dy
        if lenSq < 1e-12 {
            return hypot(point.0 - segA.0, point.1 - segA.1)
        }
        var t = ((point.0 - segA.0) * dx + (point.1 - segA.1) * dy) / lenSq
        t = max(0, min(1, t))
        let px = segA.0 + t * dx
        let py = segA.1 + t * dy
        return hypot(point.0 - px, point.1 - py)
    }
}
