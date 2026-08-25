import Foundation
import simd

enum BasePerimeterExport {
    /// Default linear feed for `G1` when exporting millimeters (mm/min).
    private static let gcodeFeedMmPerMin: Float = 1200

    static func spatialMultiplier(unitsMillimeters: Bool, userScale: Float) -> Float {
        (unitsMillimeters ? 1000 : 1) * userScale
    }

    /// Scaled slice-plane **X/Y** in export units (m or mm). Points are **arc-length** samples along the closed slice outline (same order as G-code).
    static func scaledGcodePlaneXY(result: BasePerimeterResult, unitsMillimeters: Bool, userScale: Float) -> [(Float, Float)] {
        let mul = spatialMultiplier(unitsMillimeters: unitsMillimeters, userScale: userScale)
        return result.samples.map { ($0.x * mul, $0.y * mul) }
    }

    static func gcodeBlock(result: BasePerimeterResult, unitsMillimeters: Bool, userScale: Float) -> String {
        let ring = scaledGcodePlaneXY(result: result, unitsMillimeters: unitsMillimeters, userScale: userScale)
        return gcodeBlockFromRing(
            ring: ring,
            result: result,
            unitsMillimeters: unitsMillimeters,
            userScale: userScale,
            headerExtra: nil
        )
    }

    /// G-code for the Ideal Fit (wafer cut) ring — same wrapper as primary perimeter.
    static func idealFitGcodeBlock(
        idealRing: [(Float, Float)],
        result: BasePerimeterResult,
        unitsMillimeters: Bool,
        userScale: Float
    ) -> String {
        gcodeBlockFromRing(
            ring: idealRing,
            result: result,
            unitsMillimeters: unitsMillimeters,
            userScale: userScale,
            headerExtra: "; Outline: Ideal Fit (wafer cut, outward clearance from primary)"
        )
    }

    private static func gcodeBlockFromRing(
        ring: [(Float, Float)],
        result: BasePerimeterResult,
        unitsMillimeters: Bool,
        userScale: Float,
        headerExtra: String?
    ) -> String {
        guard let first = ring.first else {
            return "; Empty perimeter\n"
        }

        let mul = spatialMultiplier(unitsMillimeters: unitsMillimeters, userScale: userScale)
        let c = result.centroidWorld * mul
        func f6(_ v: Float) -> String { String(format: "%.6f", v) }

        var lines: [String] = []
        lines.append("; Base perimeter — Stoma Companion")
        if let extra = headerExtra {
            lines.append(extra)
        }
        lines.append("; Vertices in slice plane: X along axisU, Y along axisV (not machine homing / WCS).")
        lines.append("; polar_origin_world = (\(f6(c.x)), \(f6(c.y)), \(f6(c.z)))")
        lines.append("; planeNormal = (\(f6(result.planeNormal.x)), \(f6(result.planeNormal.y)), \(f6(result.planeNormal.z)))")
        lines.append("; axisU = (\(f6(result.axisU.x)), \(f6(result.axisU.y)), \(f6(result.axisU.z)))")
        lines.append("; axisV = (\(f6(result.axisV.x)), \(f6(result.axisV.y)), \(f6(result.axisV.z)))")
        lines.append("; samples = \(BasePerimeterExtractor.sampleCount) loopVertices = \(result.loopVertexCount) sliceOffsetFraction = \(f6(result.sliceOffsetFraction))")
        lines.append("; path: equal arc-length spacing along closed slice outline (continuous G1 chain)")
        lines.append("; user_linear_scale = \(userScale) combined_spatial_multiplier = \(mul)")
        if unitsMillimeters {
            lines.append("; Units: millimeters (G21). G1 feed F\(Int(gcodeFeedMmPerMin)) mm/min.")
        } else {
            lines.append("; Units: meters (no G21). G1 moves omit F — set feed on your controller if needed.")
        }
        lines.append("; StomaPlotter: G28 homes to limit switch then moves carriage to center (0,0).")
        lines.append("")

        lines.append("G28")
        if unitsMillimeters {
            lines.append("G21")
        }
        lines.append("G90")
        lines.append("; Lead-in: StomaPlotter arcs from center to first point (no G0 to P1).")

        let feedSuffix = unitsMillimeters ? " F\(Int(gcodeFeedMmPerMin))" : ""

        lines.append("G1 X\(f6(first.0)) Y\(f6(first.1)) Z0.000000\(feedSuffix)")

        for i in 1 ..< ring.count {
            let p = ring[i]
            lines.append("G1 X\(f6(p.0)) Y\(f6(p.1)) Z0.000000\(feedSuffix)")
        }
        lines.append("G1 X\(f6(first.0)) Y\(f6(first.1)) Z0.000000\(feedSuffix)")
        lines.append("M2")
        return lines.joined(separator: "\n") + "\n"
    }
}
