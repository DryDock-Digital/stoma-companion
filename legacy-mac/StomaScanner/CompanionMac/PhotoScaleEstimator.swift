import Foundation

struct PhotoScaleComponentEstimate: Equatable, Identifiable {
    var id: String { kind.rawValue }
    var kind: StomaMetricKind
    /// Uniform scale candidate: photo_mm / mesh_scene → scene→mm multiplier for mesh coords when useMillimeters.
    /// Actually: userScale such that meshScene * userScale = millimeters (when G21 mm export).
    var scaleSceneToMillimeters: Double
    var photoMillimeters: Double
    var meshSceneUnits: Double
}

struct PhotoScaleEstimateResult: Equatable {
    var components: [PhotoScaleComponentEstimate]
    /// Robust combined scale: multiply mesh scene lengths by this to get millimeters.
    var scaleSceneToMillimeters: Double
    var coefficientOfVariation: Double
    var maxRelativeResidual: Double
    var confidence: Double
    var passesConsistency: Bool
    var note: String

    /// Relative CV threshold above which automatic export should warn / block.
    static let defaultMaxCoefficientOfVariation: Double = 0.08
    static let defaultMaxRelativeResidual: Double = 0.12
}

enum PhotoScaleEstimator {
    /// Estimate a single uniform scale from corresponding photo (mm) and mesh (scene) metrics.
    /// Tries principal-axis alignment and a reflected correspondence; keeps the lower disagreement.
    static func estimate(
        photoMM: StomaShapeMetrics,
        meshScene: StomaShapeMetrics,
        maxCV: Double = PhotoScaleEstimateResult.defaultMaxCoefficientOfVariation,
        maxResidual: Double = PhotoScaleEstimateResult.defaultMaxRelativeResidual
    ) -> PhotoScaleEstimateResult? {
        let candidates = [
            pairMetrics(photo: photoMM, mesh: meshScene, reflectMesh: false),
            pairMetrics(photo: photoMM, mesh: meshScene, reflectMesh: true),
            pairMetrics(photo: photoMM, mesh: swapMajorMinor(meshScene), reflectMesh: false),
            pairMetrics(photo: photoMM, mesh: swapMajorMinor(meshScene), reflectMesh: true),
        ].compactMap { $0 }

        guard let best = candidates.min(by: { $0.coefficientOfVariation < $1.coefficientOfVariation }) else {
            return nil
        }

        var result = best
        result.passesConsistency =
            result.coefficientOfVariation <= maxCV && result.maxRelativeResidual <= maxResidual
        if result.passesConsistency {
            result.note = String(
                format: "Robust scale %.4f mm/scene · CV %.1f%% · %d metrics",
                result.scaleSceneToMillimeters,
                result.coefficientOfVariation * 100,
                result.components.count
            )
        } else {
            result.note = String(
                format: "Inconsistent shape metrics (CV %.1f%%, max residual %.1f%%). Recapture or adjust contour; do not apply anisotropic warp.",
                result.coefficientOfVariation * 100,
                result.maxRelativeResidual * 100
            )
        }
        return result
    }

    // MARK: - Pairing

    private static func swapMajorMinor(_ m: StomaShapeMetrics) -> StomaShapeMetrics {
        var v = m.values
        if let a = v[.feretMajor], let b = v[.feretMinor] {
            v[.feretMajor] = b
            v[.feretMinor] = a
        }
        if let a = v[.feret45], let b = v[.feret135] {
            v[.feret45] = b
            v[.feret135] = a
        }
        return StomaShapeMetrics(values: v, centroid: m.centroid, principalAngleRadians: m.principalAngleRadians + .pi / 2)
    }

    private static func pairMetrics(
        photo: StomaShapeMetrics,
        mesh: StomaShapeMetrics,
        reflectMesh: Bool
    ) -> PhotoScaleEstimateResult? {
        var meshValues = mesh.values
        if reflectMesh {
            // Reflect radial landmarks about major axis (swap complementary angles).
            func swap(_ a: StomaMetricKind, _ b: StomaMetricKind) {
                let va = meshValues[a]
                meshValues[a] = meshValues[b]
                meshValues[b] = va
            }
            swap(.radial45, .radial315)
            swap(.radial90, .radial270)
            swap(.radial135, .radial225)
        }

        var components: [PhotoScaleComponentEstimate] = []
        for kind in StomaMetricKind.allCases {
            guard let p = photo.values[kind], let m = meshValues[kind], p > 1e-9, m > 1e-9 else { continue }
            components.append(
                PhotoScaleComponentEstimate(
                    kind: kind,
                    scaleSceneToMillimeters: p / m,
                    photoMillimeters: p,
                    meshSceneUnits: m
                )
            )
        }
        guard components.count >= 4 else { return nil }

        let scales = components.map(\.scaleSceneToMillimeters)
        let weights = components.map(\.kind.weight)
        let median = weightedMedian(values: scales, weights: weights)
        guard median > 1e-12 else { return nil }

        // Huber-like reweight: down-weight outliers beyond 1.5× MAD.
        let absDev = scales.map { abs($0 - median) }
        let mad = medianOf(absDev)
        let huberK = max(mad * 1.4826 * 1.5, median * 0.02)
        var refinedNum = 0.0
        var refinedDen = 0.0
        for (i, s) in scales.enumerated() {
            let r = abs(s - median)
            let w = weights[i] * (r <= huberK ? 1.0 : huberK / r)
            refinedNum += w * s
            refinedDen += w
        }
        let robust = refinedDen > 0 ? refinedNum / refinedDen : median

        let mean = scales.reduce(0, +) / Double(scales.count)
        let variance = scales.map { let d = $0 - mean; return d * d }.reduce(0, +) / Double(scales.count)
        let cv = mean > 1e-12 ? sqrt(variance) / mean : 1

        var maxRes = 0.0
        for s in scales {
            maxRes = max(maxRes, abs(s - robust) / max(robust, 1e-12))
        }

        let conf = max(0.05, min(0.99, 1.0 - cv * 4.0 - maxRes))
        return PhotoScaleEstimateResult(
            components: components.sorted { $0.kind.title < $1.kind.title },
            scaleSceneToMillimeters: robust,
            coefficientOfVariation: cv,
            maxRelativeResidual: maxRes,
            confidence: conf,
            passesConsistency: true,
            note: ""
        )
    }

    private static func weightedMedian(values: [Double], weights: [Double]) -> Double {
        let pairs = zip(values, weights).sorted { $0.0 < $1.0 }
        let total = pairs.map(\.1).reduce(0, +)
        var acc = 0.0
        for (v, w) in pairs {
            acc += w
            if acc >= total * 0.5 { return v }
        }
        return pairs.last?.0 ?? 0
    }

    private static func medianOf(_ values: [Double]) -> Double {
        guard !values.isEmpty else { return 0 }
        let s = values.sorted()
        let m = s.count / 2
        if s.count.isMultiple(of: 2) {
            return (s[m - 1] + s[m]) / 2
        }
        return s[m]
    }
}
