import Foundation
import ModelIO
import simd

enum FloorDetectionMethod: String, Sendable, Codable {
    case supportPlane = "support_plane"
    case meshYFallback = "mesh_y_fallback"
    case vertexMin = "vertex_min"
}

struct FloorDetectionResult: Sendable, Equatable {
    let planeNormal: SIMD3<Float>
    let axisU: SIMD3<Float>
    let axisV: SIMD3<Float>
    let floorH: Float
    let maxH: Float
    let vertexMinH: Float
    let method: FloorDetectionMethod
    let confidence: Float
    let planeRMSE: Float?
    let candidateCount: Int
    let downwardAreaFraction: Float
    let tiltDegFromWorldY: Float
}

enum BaseSliceUpAxis: String, CaseIterable, Identifiable {
    /// Infer slice **up** from a detected table/skin support plane; fall back to mesh +Y if none found.
    case automatic
    /// User-defined tilt from a base axis (see `ManualSliceAxisTuning`).
    case manualTilt
    case positiveY
    case negativeY
    case positiveX
    case negativeX
    case positiveZ
    case negativeZ

    var id: String { rawValue }

    /// Axes usable as the **base** direction before manual tilt is applied.
    static let manualTiltBaseChoices: [BaseSliceUpAxis] = [
        .positiveY, .negativeY, .positiveX, .negativeX, .positiveZ, .negativeZ,
    ]

    /// Short label for pickers (the raw values stay stable for `CaseIterable` / `Identifiable`).
    var menuLabel: String {
        switch self {
        case .automatic:
            return "Automatic (parallel to base)"
        case .manualTilt:
            return "Manual tilt"
        default:
            return rawValue
        }
    }

    var unitNormal: SIMD3<Float> {
        switch self {
        case .automatic, .manualTilt:
            preconditionFailure("BaseSliceUpAxis.\(rawValue) has no fixed unitNormal; resolve via BasePerimeterExtractor.")
        case .positiveY: SIMD3(0, 1, 0)
        case .negativeY: SIMD3(0, -1, 0)
        case .positiveX: SIMD3(1, 0, 0)
        case .negativeX: SIMD3(-1, 0, 0)
        case .positiveZ: SIMD3(0, 0, 1)
        case .negativeZ: SIMD3(0, 0, -1)
        }
    }
}

/// Manual slice-plane orientation: base axis + Euler tilts (mesh X/Y/Z) + in-plane spin around the slice normal.
struct ManualSliceAxisTuning: Equatable, Sendable {
    var baseAxis: BaseSliceUpAxis = .positiveY
    /// Rotation applied around mesh **+X** (degrees), then Y, then Z.
    var tiltXDegrees: Double = 0
    var tiltYDegrees: Double = 0
    var tiltZDegrees: Double = 0
    /// Twist of G-code **X/Y** axes around the slice normal (degrees).
    var spinDegrees: Double = 0

    static let `default` = ManualSliceAxisTuning()

    var baseUnitNormal: SIMD3<Float> {
        simd_normalize(baseAxis.unitNormal)
    }
}

struct BasePlaneSample: Sendable, Equatable {
    let index: Int
    let thetaRadians: Float
    let r: Float
    let x: Float
    let y: Float
}

struct BasePerimeterResult: Sendable, Equatable {
    let samples: [BasePlaneSample]
    let centroidWorld: SIMD3<Float>
    let axisU: SIMD3<Float>
    let axisV: SIMD3<Float>
    let planeNormal: SIMD3<Float>
    let planeConstant: Float
    let loopVertexCount: Int
    let sliceOffsetFraction: Float
    let floorDetection: FloorDetectionResult
}

extension BasePerimeterExtractor {
    /// Maximum Euclidean distance between any two perimeter samples in the slice **x/y** plane (scene units).
    /// Matches the “longest diameter” of the resampled outline; use with photo calibration as `meshLen`.
    static func maxPlanarChordLength(samples: [BasePlaneSample]) -> Float {
        let n = samples.count
        guard n >= 2 else { return 0 }
        var best: Float = 0
        for i in 0 ..< n {
            for j in (i + 1) ..< n {
                let dx = samples[i].x - samples[j].x
                let dy = samples[i].y - samples[j].y
                let d = sqrt(dx * dx + dy * dy)
                if d > best { best = d }
            }
        }
        return best
    }
}

/// Subject-only projected height range along the slice normal (same rules as `extract`). Use with
/// `sliceOffsetFraction` to place a preview plane without rebuilding triangle lists.
struct BaseSliceHeightBounds: Sendable {
    let planeNormal: SIMD3<Float>
    let axisU: SIMD3<Float>
    let axisV: SIMD3<Float>
    /// Support-plane height (0% slice) along `planeNormal`.
    let floorH: Float
    let maxH: Float
    let floorDetection: FloorDetectionResult

    /// Same as `floorH` (legacy preview field name).
    var minH: Float { floorH }
}

/// World-space axis-aligned box used to drop triangles (e.g. reconstructed ArUco marker).
struct WorldAABBExclusion: Sendable, Equatable {
    var min: SIMD3<Float>
    var max: SIMD3<Float>

    func contains(_ p: SIMD3<Float>) -> Bool {
        p.x >= min.x && p.x <= max.x
            && p.y >= min.y && p.y <= max.y
            && p.z >= min.z && p.z <= max.z
    }

    /// True if the triangle centroid lies inside the box.
    func containsTriangleCentroid(_ a: SIMD3<Float>, _ b: SIMD3<Float>, _ c: SIMD3<Float>) -> Bool {
        contains((a + b + c) / 3)
    }
}

enum BasePerimeterExtractorError: Error, LocalizedError {
    case emptyAsset
    case noTriangles
    case noSliceSegments
    case couldNotFormLoop
    case degeneratePolygon
    case couldNotInferSliceOrientation

    var errorDescription: String? {
        switch self {
        case .emptyAsset: "The USDZ did not load any geometry."
        case .noTriangles: "No triangle meshes were found (after excluding the reference subtree, if any)."
        case .noSliceSegments: "The slice plane did not intersect the subject mesh. Try another up axis or slice offset."
        case .couldNotFormLoop: "Could not trace a closed perimeter from the slice. Try a different offset."
        case .degeneratePolygon: "The slice outline was too small or degenerate to resample."
        case .couldNotInferSliceOrientation:
            "Could not infer orientation (no clear opening rim and weak bottom-triangle signal). Pick a fixed up axis or adjust exclusion."
        }
    }
}

enum BasePerimeterExtractor {
    static let sampleCount = 100

    private static let floorMinCandidateCount = 100
    private static let floorDownwardAreaFractionMin: Float = 0.05
    private static let floorPlanarityRMSEMaxFraction: Float = 0.05
    private static let floorNormalTiltMin: Float = 0.85
    private static let floorPercentileBandHigh: Float = 0.15
    private static let floorDownwardFaceDotMin: Float = 0.5
    private static let meshYUpNormal = SIMD3<Float>(0, 1, 0)

    /// `excludeObjectNameSubstring`: if non-empty, skips entire subtrees whose `MDLObject.name` contains this string (case-insensitive) for both AABB and slicing.
    /// `excludeWorldAABB`: if set, drops triangles whose centroid falls inside the box (mesh ArUco exclusion).
    static func extract(
        usdzURL: URL,
        upAxis: BaseSliceUpAxis,
        manualTilt: ManualSliceAxisTuning = .default,
        sliceOffsetFraction: Float,
        excludeObjectNameSubstring: String?,
        excludeWorldAABB: WorldAABBExclusion? = nil
    ) throws -> BasePerimeterResult {
        let asset = MDLAsset(url: usdzURL)
        guard asset.count > 0 else { throw BasePerimeterExtractorError.emptyAsset }

        let trimmed = excludeObjectNameSubstring?.trimmingCharacters(in: .whitespacesAndNewlines) ?? ""
        let excludePattern: String? = trimmed.isEmpty ? nil : trimmed

        var triangles: [(SIMD3<Float>, SIMD3<Float>, SIMD3<Float>)] = []
        for i in 0 ..< asset.count {
            collectSubjectTriangles(
                from: asset.object(at: i),
                parentWorld: matrix_identity_float4x4,
                excludePattern: excludePattern,
                excludedAncestor: false,
                into: &triangles
            )
        }
        if let box = excludeWorldAABB {
            triangles = filterTriangles(triangles, excluding: box)
        }

        guard !triangles.isEmpty else { throw BasePerimeterExtractorError.noTriangles }

        let frame = try resolveSliceFrame(
            upAxis: upAxis,
            manualTilt: manualTilt,
            subjectTriangles: triangles
        )
        let n = frame.planeNormal
        let floorH = frame.floorH
        let maxH = frame.maxH

        let span = max(maxH - floorH, 1e-6)
        let offset = max(0, min(sliceOffsetFraction, 1))
        let planeD = floorH + offset * span

        let axisU = frame.axisU
        let axisV = frame.axisV
        let eps = max(span * 1e-5, 1e-6)

        var segments: [(SIMD2<Float>, SIMD2<Float>)] = []
        for tri in triangles {
            let more = intersectTrianglePlaneSegments(
                a: tri.0, b: tri.1, c: tri.2,
                normal: n, planeD: planeD,
                axisU: axisU, axisV: axisV,
                epsilon: eps
            )
            segments.append(contentsOf: more)
        }

        guard !segments.isEmpty else { throw BasePerimeterExtractorError.noSliceSegments }

        // Tighter snap than before: large snap was merging unrelated cut points and breaking degree-2 loops.
        let snapEps = max(eps * 10, 1e-6)
        let loop2D = try largestPerimeterLoop(from: segments, snapEps: snapEps)
        guard loop2D.count >= 3 else { throw BasePerimeterExtractorError.degeneratePolygon }

        let origin2D = polarOrigin2D(in: loop2D)
        let centroidWorld = pointOnPlane(u: origin2D.x, v: origin2D.y, planeD: planeD, n: n, axisU: axisU, axisV: axisV)
        let shifted = loop2D.map { $0 - origin2D }
        let samples = arcLengthResample100(polygon: shifted)

        return BasePerimeterResult(
            samples: samples,
            centroidWorld: centroidWorld,
            axisU: axisU,
            axisV: axisV,
            planeNormal: n,
            planeConstant: planeD,
            loopVertexCount: loop2D.count,
            sliceOffsetFraction: offset,
            floorDetection: frame
        )
    }

    /// Min/max of `dot(vertex, planeNormal)` over included mesh vertices (after subtree exclusion).
    /// Cheaper than `extract` for live slice preview.
    static func subjectSliceHeightBounds(
        usdzURL: URL,
        upAxis: BaseSliceUpAxis,
        manualTilt: ManualSliceAxisTuning = .default,
        excludeObjectNameSubstring: String?,
        excludeWorldAABB: WorldAABBExclusion? = nil
    ) throws -> BaseSliceHeightBounds {
        let asset = MDLAsset(url: usdzURL)
        guard asset.count > 0 else { throw BasePerimeterExtractorError.emptyAsset }

        let trimmed = excludeObjectNameSubstring?.trimmingCharacters(in: .whitespacesAndNewlines) ?? ""
        let excludePattern: String? = trimmed.isEmpty ? nil : trimmed

        var triangles: [(SIMD3<Float>, SIMD3<Float>, SIMD3<Float>)] = []
        for i in 0 ..< asset.count {
            collectSubjectTriangles(
                from: asset.object(at: i),
                parentWorld: matrix_identity_float4x4,
                excludePattern: excludePattern,
                excludedAncestor: false,
                into: &triangles
            )
        }
        if let box = excludeWorldAABB {
            triangles = filterTriangles(triangles, excluding: box)
        }
        guard !triangles.isEmpty else { throw BasePerimeterExtractorError.noTriangles }

        let frame = try resolveSliceFrame(
            upAxis: upAxis,
            manualTilt: manualTilt,
            subjectTriangles: triangles
        )
        guard frame.floorH.isFinite, frame.maxH.isFinite, frame.floorH <= frame.maxH else {
            throw BasePerimeterExtractorError.noTriangles
        }

        return BaseSliceHeightBounds(
            planeNormal: frame.planeNormal,
            axisU: frame.axisU,
            axisV: frame.axisV,
            floorH: frame.floorH,
            maxH: frame.maxH,
            floorDetection: frame
        )
    }

    private static func collectVertexHeightRange(
        from object: MDLObject,
        parentWorld: float4x4,
        excludePattern: String?,
        excludedAncestor: Bool,
        planeNormal n: SIMD3<Float>,
        minH: inout Float,
        maxH: inout Float
    ) {
        let local = object.transform.map(\.matrix) ?? matrix_identity_float4x4
        let world = parentWorld * local

        let nameHit = excludePattern.map { object.name.localizedCaseInsensitiveContains($0) } ?? false
        let excluded = excludedAncestor || nameHit

        if !excluded, let mesh = object as? MDLMesh {
            extendHeightRangeFromMeshVertices(from: mesh, world: world, planeNormal: n, minH: &minH, maxH: &maxH)
        }

        for child in object.children.objects {
            collectVertexHeightRange(
                from: child,
                parentWorld: world,
                excludePattern: excludePattern,
                excludedAncestor: excluded,
                planeNormal: n,
                minH: &minH,
                maxH: &maxH
            )
        }
    }

    private static func extendHeightRangeFromMeshVertices(
        from mesh: MDLMesh,
        world: float4x4,
        planeNormal n: SIMD3<Float>,
        minH: inout Float,
        maxH: inout Float
    ) {
        guard let posData = mesh.vertexAttributeData(forAttributeNamed: MDLVertexAttributePosition) else { return }
        let stride = posData.stride
        let posBase = UnsafeRawPointer(posData.dataStart)
        let format = posData.format
        guard format == .float3 || format == .float4 else { return }

        func vertex(at index: Int) -> SIMD3<Float> {
            let off = index * stride
            let x = posBase.load(fromByteOffset: off, as: Float.self)
            let y = posBase.load(fromByteOffset: off + 4, as: Float.self)
            let z = posBase.load(fromByteOffset: off + 8, as: Float.self)
            return SIMD3(x, y, z)
        }

        let vc = mesh.vertexCount
        for vi in 0 ..< vc {
            let p = transformPoint(world, vertex(at: vi))
            let h = simd_dot(p, n)
            minH = min(minH, h)
            maxH = max(maxH, h)
        }
    }

    /// Builds manual tilt settings that reproduce an automatic support-plane normal (for seeding the tuner).
    static func manualTiltSeededFromAutomatic(
        usdzURL: URL,
        excludeObjectNameSubstring: String?
    ) throws -> ManualSliceAxisTuning {
        let triangles = try loadSubjectTriangles(usdzURL: usdzURL, excludeObjectNameSubstring: excludeObjectNameSubstring)
        guard !triangles.isEmpty else { throw BasePerimeterExtractorError.noTriangles }
        let frame = try resolveAutomaticSliceFrame(subjectTriangles: triangles)
        return ManualSliceAxisTuning.decomposed(
            planeNormal: frame.planeNormal,
            baseAxis: .positiveY,
            spinDegrees: 0
        )
    }

    /// Slice normal after manual tilt (mesh coordinates).
    static func planeNormal(for manualTilt: ManualSliceAxisTuning) -> SIMD3<Float> {
        planeNormalFromManualTilt(manualTilt)
    }

    private static func loadSubjectTriangles(
        usdzURL: URL,
        excludeObjectNameSubstring: String?
    ) throws -> [(SIMD3<Float>, SIMD3<Float>, SIMD3<Float>)] {
        let asset = MDLAsset(url: usdzURL)
        guard asset.count > 0 else { throw BasePerimeterExtractorError.emptyAsset }
        let trimmed = excludeObjectNameSubstring?.trimmingCharacters(in: .whitespacesAndNewlines) ?? ""
        let excludePattern: String? = trimmed.isEmpty ? nil : trimmed
        var triangles: [(SIMD3<Float>, SIMD3<Float>, SIMD3<Float>)] = []
        for i in 0 ..< asset.count {
            collectSubjectTriangles(
                from: asset.object(at: i),
                parentWorld: matrix_identity_float4x4,
                excludePattern: excludePattern,
                excludedAncestor: false,
                into: &triangles
            )
        }
        return triangles
    }

    private static func resolveSliceFrame(
        upAxis: BaseSliceUpAxis,
        manualTilt: ManualSliceAxisTuning,
        subjectTriangles: [(SIMD3<Float>, SIMD3<Float>, SIMD3<Float>)]
    ) throws -> FloorDetectionResult {
        switch upAxis {
        case .automatic:
            return try resolveAutomaticSliceFrame(subjectTriangles: subjectTriangles)
        case .manualTilt:
            return resolveManualTiltSliceFrame(
                tuning: manualTilt,
                subjectTriangles: subjectTriangles
            )
        default:
            return resolveFixedAxisSliceFrame(
                upAxis: upAxis,
                subjectTriangles: subjectTriangles
            )
        }
    }

    private static func resolveManualTiltSliceFrame(
        tuning: ManualSliceAxisTuning,
        subjectTriangles: [(SIMD3<Float>, SIMD3<Float>, SIMD3<Float>)]
    ) -> FloorDetectionResult {
        let n = planeNormalFromManualTilt(tuning)
        return resolveSliceFrameWithNormal(
            n: n,
            spinDegrees: tuning.spinDegrees,
            subjectTriangles: subjectTriangles
        )
    }

    private static func resolveFixedAxisSliceFrame(
        upAxis: BaseSliceUpAxis,
        subjectTriangles: [(SIMD3<Float>, SIMD3<Float>, SIMD3<Float>)]
    ) -> FloorDetectionResult {
        let n = simd_normalize(upAxis.unitNormal)
        return resolveSliceFrameWithNormal(n: n, spinDegrees: 0, subjectTriangles: subjectTriangles)
    }

    private static func resolveSliceFrameWithNormal(
        n: SIMD3<Float>,
        spinDegrees: Double,
        subjectTriangles: [(SIMD3<Float>, SIMD3<Float>, SIMD3<Float>)]
    ) -> FloorDetectionResult {
        let normal = simd_normalize(n)
        let collection = collectSupportSurfaceData(subjectTriangles: subjectTriangles, nSeed: normal)
        let (vertexMinH, maxH) = heightExtrema(subjectTriangles: subjectTriangles, normal: normal)
        let (axisU, axisV) = sliceBasis(normal: normal, spinDegrees: spinDegrees)

        if let fit = collection.planeFit,
           passesSupportGates(collection: collection, planeRMSE: fit.rmse, planeNormal: fit.normal, nSeed: normal) {
            let floorH = median(collection.candidateHeights(on: normal))
            let confidence = supportPlaneConfidence(
                collection: collection,
                planeRMSE: fit.rmse,
                bboxDiagonal: collection.bboxDiagonal
            )
            return FloorDetectionResult(
                planeNormal: normal,
                axisU: axisU,
                axisV: axisV,
                floorH: floorH,
                maxH: maxH,
                vertexMinH: vertexMinH,
                method: .supportPlane,
                confidence: confidence,
                planeRMSE: fit.rmse,
                candidateCount: collection.candidates.count,
                downwardAreaFraction: collection.downwardAreaFraction,
                tiltDegFromWorldY: tiltDegreesFromWorldY(normal)
            )
        }

        return FloorDetectionResult(
            planeNormal: normal,
            axisU: axisU,
            axisV: axisV,
            floorH: vertexMinH,
            maxH: maxH,
            vertexMinH: vertexMinH,
            method: .vertexMin,
            confidence: 0.35,
            planeRMSE: collection.planeFit?.rmse,
            candidateCount: collection.candidates.count,
            downwardAreaFraction: collection.downwardAreaFraction,
            tiltDegFromWorldY: tiltDegreesFromWorldY(normal)
        )
    }

    private static func resolveAutomaticSliceFrame(
        subjectTriangles: [(SIMD3<Float>, SIMD3<Float>, SIMD3<Float>)]
    ) throws -> FloorDetectionResult {
        let nSeed = (try? inferMeshBaseUpNormal(from: subjectTriangles)) ?? meshYUpNormal
        let collection = collectSupportSurfaceData(subjectTriangles: subjectTriangles, nSeed: nSeed)
        let centroid = triangleSoupVertexMean(subjectTriangles)

        if let fit = collection.planeFit,
           passesSupportGates(collection: collection, planeRMSE: fit.rmse, planeNormal: fit.normal, nSeed: nSeed) {
            var n = fit.normal
            if simd_dot(centroid - fit.centroid, n) < 0 {
                n = -n
            }
            n = simd_normalize(n)
            let floorH = median(collection.candidateHeights(on: n))
            let (vertexMinH, maxH) = heightExtrema(subjectTriangles: subjectTriangles, normal: n)
            let (axisU, axisV) = orthonormalBasis(onPlaneWithNormal: n)
            let confidence = supportPlaneConfidence(
                collection: collection,
                planeRMSE: fit.rmse,
                bboxDiagonal: collection.bboxDiagonal
            )
            return FloorDetectionResult(
                planeNormal: n,
                axisU: axisU,
                axisV: axisV,
                floorH: floorH,
                maxH: maxH,
                vertexMinH: vertexMinH,
                method: .supportPlane,
                confidence: confidence,
                planeRMSE: fit.rmse,
                candidateCount: collection.candidates.count,
                downwardAreaFraction: collection.downwardAreaFraction,
                tiltDegFromWorldY: tiltDegreesFromWorldY(n)
            )
        }

        let n = meshYUpNormal
        let (floorH, maxH, vertexMinH) = heightExtremaWithMin(subjectTriangles: subjectTriangles, normal: n)
        let (axisU, axisV) = orthonormalBasis(onPlaneWithNormal: n)
        return FloorDetectionResult(
            planeNormal: n,
            axisU: axisU,
            axisV: axisV,
            floorH: floorH,
            maxH: maxH,
            vertexMinH: vertexMinH,
            method: .meshYFallback,
            confidence: 0.5,
            planeRMSE: collection.planeFit?.rmse,
            candidateCount: collection.candidates.count,
            downwardAreaFraction: collection.downwardAreaFraction,
            tiltDegFromWorldY: tiltDegreesFromWorldY(n)
        )
    }

    private struct SupportSurfaceCollection {
        let candidates: [SIMD3<Float>]
        let planeFit: (normal: SIMD3<Float>, rmse: Float, centroid: SIMD3<Float>)?
        let downwardAreaFraction: Float
        let totalTriangleArea: Float
        let bboxDiagonal: Float

        func candidateHeights(on normal: SIMD3<Float>) -> [Float] {
            candidates.map { simd_dot($0, normal) }
        }
    }

    private static func collectSupportSurfaceData(
        subjectTriangles: [(SIMD3<Float>, SIMD3<Float>, SIMD3<Float>)],
        nSeed: SIMD3<Float>
    ) -> SupportSurfaceCollection {
        let seed = simd_normalize(nSeed)
        let meta = subjectTriangleMetaList(from: subjectTriangles)
        let totalArea = meta.reduce(Float(0)) { $0 + $1.area }

        var vertexHeights: [Float] = []
        vertexHeights.reserveCapacity(subjectTriangles.count * 3)
        for tri in subjectTriangles {
            for p in [tri.0, tri.1, tri.2] {
                vertexHeights.append(simd_dot(p, seed))
            }
        }
        guard let hMin = vertexHeights.min(), let hMax = vertexHeights.max(), hMax - hMin > 1e-8 else {
            let pts = subjectTriangles.flatMap { [$0.0, $0.1, $0.2] }
            return SupportSurfaceCollection(
                candidates: pts,
                planeFit: fitPlaneNormalRMSEWithCentroid(points: pts),
                downwardAreaFraction: 0,
                totalTriangleArea: max(totalArea, 1e-8),
                bboxDiagonal: bboxDiagonal(pts)
            )
        }

        let span = hMax - hMin
        let bandCutoff = hMin + span * floorPercentileBandHigh

        var candidates: [SIMD3<Float>] = []
        var downwardAreaInBand: Float = 0

        for tri in subjectTriangles {
            for p in [tri.0, tri.1, tri.2] {
                if simd_dot(p, seed) <= bandCutoff {
                    candidates.append(p)
                }
            }
        }

        for t in meta {
            let h = simd_dot(t.centroid, seed)
            guard h <= bandCutoff else { continue }
            var faceUp = t.normal
            if simd_dot(faceUp, seed) < 0 { faceUp = -faceUp }
            if simd_dot(faceUp, seed) > floorDownwardFaceDotMin {
                candidates.append(t.centroid)
                downwardAreaInBand += t.area
            }
        }

        let downwardFraction = totalArea > 1e-8 ? downwardAreaInBand / totalArea : 0
        let planeFit = fitPlaneNormalRMSEWithCentroid(points: candidates)
        let allPts = subjectTriangles.flatMap { [$0.0, $0.1, $0.2] }

        return SupportSurfaceCollection(
            candidates: candidates,
            planeFit: planeFit,
            downwardAreaFraction: downwardFraction,
            totalTriangleArea: max(totalArea, 1e-8),
            bboxDiagonal: bboxDiagonal(allPts)
        )
    }

    private static func passesSupportGates(
        collection: SupportSurfaceCollection,
        planeRMSE: Float,
        planeNormal: SIMD3<Float>,
        nSeed: SIMD3<Float>
    ) -> Bool {
        guard collection.candidates.count >= floorMinCandidateCount else { return false }
        guard collection.downwardAreaFraction >= floorDownwardAreaFractionMin else { return false }
        guard collection.bboxDiagonal > 1e-8 else { return false }
        guard planeRMSE <= floorPlanarityRMSEMaxFraction * collection.bboxDiagonal else { return false }
        let nPlane = simd_normalize(planeNormal)
        guard abs(simd_dot(nPlane, simd_normalize(nSeed))) >= floorNormalTiltMin else { return false }
        return true
    }

    private static func supportPlaneConfidence(
        collection: SupportSurfaceCollection,
        planeRMSE: Float,
        bboxDiagonal: Float
    ) -> Float {
        var c: Float = 1
        if bboxDiagonal > 1e-8 {
            let rmseRatio = planeRMSE / bboxDiagonal
            if rmseRatio > 0.03 {
                c = max(0.55, 1 - (rmseRatio - 0.03) / 0.02)
            }
        }
        if collection.candidates.count < floorMinCandidateCount * 2 {
            c *= 0.85
        }
        return min(max(c, 0), 1)
    }

    private static func heightExtrema(
        subjectTriangles: [(SIMD3<Float>, SIMD3<Float>, SIMD3<Float>)],
        normal n: SIMD3<Float>
    ) -> (minH: Float, maxH: Float) {
        let triple = heightExtremaWithMin(subjectTriangles: subjectTriangles, normal: n)
        return (triple.0, triple.1)
    }

    private static func heightExtremaWithMin(
        subjectTriangles: [(SIMD3<Float>, SIMD3<Float>, SIMD3<Float>)],
        normal n: SIMD3<Float>
    ) -> (floorH: Float, maxH: Float, vertexMinH: Float) {
        var minH: Float = .greatestFiniteMagnitude
        var maxH: Float = -.greatestFiniteMagnitude
        for tri in subjectTriangles {
            for p in [tri.0, tri.1, tri.2] {
                let h = simd_dot(p, n)
                minH = min(minH, h)
                maxH = max(maxH, h)
            }
        }
        return (minH, maxH, minH)
    }

    private static func median(_ values: [Float]) -> Float {
        guard !values.isEmpty else { return 0 }
        let sorted = values.sorted()
        let mid = sorted.count / 2
        if sorted.count.isMultiple(of: 2) {
            return (sorted[mid - 1] + sorted[mid]) / 2
        }
        return sorted[mid]
    }

    private static func tiltDegreesFromWorldY(_ n: SIMD3<Float>) -> Float {
        let u = simd_normalize(n)
        let dotY = min(max(abs(simd_dot(u, meshYUpNormal)), 0), 1)
        return acos(dotY) * 180 / Float.pi
    }

    private static func planeNormalFromManualTilt(_ tuning: ManualSliceAxisTuning) -> SIMD3<Float> {
        let base = simd_normalize(tuning.baseUnitNormal)
        let m = rotationMatrixZ(degrees: Float(tuning.tiltZDegrees))
            * rotationMatrixY(degrees: Float(tuning.tiltYDegrees))
            * rotationMatrixX(degrees: Float(tuning.tiltXDegrees))
        return simd_normalize(m * base)
    }

    private static func sliceBasis(normal n: SIMD3<Float>, spinDegrees: Double) -> (SIMD3<Float>, SIMD3<Float>) {
        let (axisU, axisV) = orthonormalBasis(onPlaneWithNormal: n)
        let spin = Float(spinDegrees)
        guard abs(spin) > 1e-6 else { return (axisU, axisV) }
        let rad = spin * Float.pi / 180
        let c = cos(rad)
        let s = sin(rad)
        let u2 = c * axisU + s * axisV
        let v2 = -s * axisU + c * axisV
        return (simd_normalize(u2), simd_normalize(v2))
    }

    private static func rotationMatrixX(degrees: Float) -> simd_float3x3 {
        let r = degrees * Float.pi / 180
        let c = cos(r)
        let s = sin(r)
        return simd_float3x3(
            SIMD3(1, 0, 0),
            SIMD3(0, c, s),
            SIMD3(0, -s, c)
        )
    }

    private static func rotationMatrixY(degrees: Float) -> simd_float3x3 {
        let r = degrees * Float.pi / 180
        let c = cos(r)
        let s = sin(r)
        return simd_float3x3(
            SIMD3(c, 0, -s),
            SIMD3(0, 1, 0),
            SIMD3(s, 0, c)
        )
    }

    private static func rotationMatrixZ(degrees: Float) -> simd_float3x3 {
        let r = degrees * Float.pi / 180
        let c = cos(r)
        let s = sin(r)
        return simd_float3x3(
            SIMD3(c, s, 0),
            SIMD3(-s, c, 0),
            SIMD3(0, 0, 1)
        )
    }

    private static func fitPlaneNormalRMSEWithCentroid(points: [SIMD3<Float>]) -> (normal: SIMD3<Float>, rmse: Float, centroid: SIMD3<Float>)? {
        guard let fit = fitPlaneNormalRMSE(points: points) else { return nil }
        var mu = SIMD3<Float>.zero
        for p in points { mu += p }
        mu /= Float(points.count)
        return (fit.normal, fit.rmse, mu)
    }

    private static func planeNormal(
        upAxis: BaseSliceUpAxis,
        asset: MDLAsset,
        excludePattern: String?,
        subjectTriangles: [(SIMD3<Float>, SIMD3<Float>, SIMD3<Float>)]
    ) throws -> SIMD3<Float> {
        switch upAxis {
        case .automatic:
            let centroid = triangleSoupVertexMean(subjectTriangles)
            if let rim = inferOpeningRimBaseUp(asset: asset, excludePattern: excludePattern, subjectCentroid: centroid) {
                return rim
            }
            return try inferMeshBaseUpNormal(from: subjectTriangles)
        case .manualTilt:
            return meshYUpNormal
        default:
            return simd_normalize(upAxis.unitNormal)
        }
    }

    private static func triangleSoupVertexMean(_ triangles: [(SIMD3<Float>, SIMD3<Float>, SIMD3<Float>)]) -> SIMD3<Float> {
        var s = SIMD3<Float>.zero
        var count = 0
        for t in triangles {
            s += t.0 + t.1 + t.2
            count += 3
        }
        return count > 0 ? s / Float(count) : .zero
    }

    private struct UndirectedMeshEdge: Hashable {
        let lo: Int
        let hi: Int
        init(_ a: Int, _ b: Int) {
            if a < b {
                lo = a
                hi = b
            } else {
                lo = b
                hi = a
            }
        }
    }

    /// Uses the **largest mesh opening** (triangle edges used by only one face): that loop lies on the
    /// plane of the unscanned base, so its best-fit normal defines slice “up” parallel to that base.
    private static func inferOpeningRimBaseUp(
        asset: MDLAsset,
        excludePattern: String?,
        subjectCentroid: SIMD3<Float>
    ) -> SIMD3<Float>? {
        var bestNormal: SIMD3<Float>?
        var bestScore: Float = -1
        for i in 0 ..< asset.count {
            collectOpeningRimCandidates(
                from: asset.object(at: i),
                parentWorld: matrix_identity_float4x4,
                excludePattern: excludePattern,
                excludedAncestor: false,
                subjectCentroid: subjectCentroid,
                bestNormal: &bestNormal,
                bestScore: &bestScore
            )
        }
        guard let n = bestNormal, bestScore > 1e-8 else { return nil }
        return simd_normalize(n)
    }

    private static func collectOpeningRimCandidates(
        from object: MDLObject,
        parentWorld: float4x4,
        excludePattern: String?,
        excludedAncestor: Bool,
        subjectCentroid: SIMD3<Float>,
        bestNormal: inout SIMD3<Float>?,
        bestScore: inout Float
    ) {
        let local = object.transform.map(\.matrix) ?? matrix_identity_float4x4
        let world = parentWorld * local

        let nameHit = excludePattern.map { object.name.localizedCaseInsensitiveContains($0) } ?? false
        let excluded = excludedAncestor || nameHit

        if !excluded, let mesh = object as? MDLMesh {
            if let (n, score) = openingRimPlaneFromMesh(mesh: mesh, world: world, subjectCentroid: subjectCentroid), score > bestScore {
                bestScore = score
                bestNormal = n
            }
        }

        for child in object.children.objects {
            collectOpeningRimCandidates(
                from: child,
                parentWorld: world,
                excludePattern: excludePattern,
                excludedAncestor: excluded,
                subjectCentroid: subjectCentroid,
                bestNormal: &bestNormal,
                bestScore: &bestScore
            )
        }
    }

    /// Returns unit **up** (slice normal) and a quality score, or `nil` if there is no usable opening rim.
    private static func openingRimPlaneFromMesh(
        mesh: MDLMesh,
        world: float4x4,
        subjectCentroid: SIMD3<Float>
    ) -> (SIMD3<Float>, Float)? {
        guard let posData = mesh.vertexAttributeData(forAttributeNamed: MDLVertexAttributePosition) else { return nil }
        let stride = posData.stride
        let posBase = UnsafeRawPointer(posData.dataStart)
        let format = posData.format
        guard format == .float3 || format == .float4 else { return nil }

        func vertexLocal(at index: Int) -> SIMD3<Float> {
            let off = index * stride
            let x = posBase.load(fromByteOffset: off, as: Float.self)
            let y = posBase.load(fromByteOffset: off + 4, as: Float.self)
            let z = posBase.load(fromByteOffset: off + 8, as: Float.self)
            return SIMD3(x, y, z)
        }

        func worldPos(at index: Int) -> SIMD3<Float> {
            transformPoint(world, vertexLocal(at: index))
        }

        var edgeUseCount: [UndirectedMeshEdge: Int] = [:]
        let submeshes = mesh.submeshes as? [MDLSubmesh] ?? []
        for sub in submeshes {
            guard sub.geometryType == .triangles else { continue }
            let ib = sub.indexBuffer
            let indexCount = sub.indexCount
            let indexType = sub.indexType
            let map = ib.map()
            let indexBytes = UnsafeRawPointer(map.bytes)

            func readIndex(_ i: Int) -> Int {
                switch indexType {
                case .uInt32:
                    return Int(indexBytes.load(fromByteOffset: i * 4, as: UInt32.self))
                case .uInt16:
                    return Int(indexBytes.load(fromByteOffset: i * 2, as: UInt16.self))
                default:
                    return 0
                }
            }

            var i = 0
            while i + 2 < indexCount {
                let i0 = readIndex(i)
                let i1 = readIndex(i + 1)
                let i2 = readIndex(i + 2)
                i += 3
                for e in [UndirectedMeshEdge(i0, i1), UndirectedMeshEdge(i1, i2), UndirectedMeshEdge(i2, i0)] {
                    edgeUseCount[e, default: 0] += 1
                }
            }
        }

        let boundaryEdges: [(Int, Int)] = edgeUseCount.compactMap { key, c in
            guard c == 1 else { return nil }
            return (key.lo, key.hi)
        }
        guard boundaryEdges.count >= 3 else { return nil }

        var adj: [Int: [Int]] = [:]
        for (a, b) in boundaryEdges {
            adj[a, default: []].append(b)
            adj[b, default: []].append(a)
        }

        var verticesLeft = Set(adj.keys)
        var meshBestN: SIMD3<Float>?
        var meshBestScore: Float = -1
        while let start = verticesLeft.min() {
            var comp = Set<Int>()
            var stack = [start]
            while let v = stack.popLast() {
                if comp.contains(v) { continue }
                comp.insert(v)
                verticesLeft.remove(v)
                for u in adj[v, default: []] {
                    if !comp.contains(u) {
                        stack.append(u)
                    }
                }
            }
            guard comp.count >= 4 else { continue }

            var rimLength: Float = 0
            for (a, b) in boundaryEdges {
                guard comp.contains(a), comp.contains(b) else { continue }
                rimLength += simd_distance(worldPos(at: a), worldPos(at: b))
            }
            guard rimLength > 1e-8 else { continue }

            var pts: [SIMD3<Float>] = []
            pts.reserveCapacity(comp.count)
            for v in comp {
                pts.append(worldPos(at: v))
            }

            guard let plane = fitPlaneNormalRMSE(points: pts) else { continue }
            let diag = bboxDiagonal(pts)
            guard diag > 1e-8, plane.rmse < max(1e-4, 0.12 * diag) else { continue }

            var n = plane.normal
            let ringCentroid = pts.reduce(SIMD3<Float>.zero, +) / Float(pts.count)
            if simd_dot(subjectCentroid - ringCentroid, n) < 0 {
                n = -n
            }

            let score = rimLength / (plane.rmse + 1e-4)
            if score.isFinite, score > meshBestScore {
                meshBestScore = score
                meshBestN = n
            }
        }

        guard let nOut = meshBestN, meshBestScore > 1e-8 else { return nil }
        return (nOut, meshBestScore)
    }

    private static func bboxDiagonal(_ pts: [SIMD3<Float>]) -> Float {
        guard let fx = pts.map(\.x).min(), let nx = pts.map(\.x).max(),
              let fy = pts.map(\.y).min(), let ny = pts.map(\.y).max(),
              let fz = pts.map(\.z).min(), let nz = pts.map(\.z).max()
        else { return 0 }
        let d = SIMD3(nx - fx, ny - fy, nz - fz)
        return simd_length(d)
    }

    private static func fitPlaneNormalRMSE(points: [SIMD3<Float>]) -> (normal: SIMD3<Float>, rmse: Float)? {
        let nPts = points.count
        guard nPts >= 4 else { return nil }
        var mu = SIMD3<Float>.zero
        for p in points { mu += p }
        mu /= Float(nPts)

        var cxx: Float = 0, cyy: Float = 0, czz: Float = 0
        var cxy: Float = 0, cxz: Float = 0, cyz: Float = 0
        for p in points {
            let q = p - mu
            cxx += q.x * q.x
            cyy += q.y * q.y
            czz += q.z * q.z
            cxy += q.x * q.y
            cxz += q.x * q.z
            cyz += q.y * q.z
        }
        let invN = 1 / Float(nPts)
        let c = simd_float3x3(
            SIMD3(cxx * invN, cxy * invN, cxz * invN),
            SIMD3(cxy * invN, cyy * invN, cyz * invN),
            SIMD3(cxz * invN, cyz * invN, czz * invN)
        )

        var n = smallestEigenvectorSymmetric3x3(c)
        guard n.x.isFinite, simd_length_squared(n) > 1e-12 else { return nil }
        n = simd_normalize(n)

        var sumSq: Float = 0
        for p in points {
            let d = simd_dot(p - mu, n)
            sumSq += d * d
        }
        let rmse = sqrt(sumSq / Float(nPts))
        return (n, rmse)
    }

    /// Smallest-magnitude eigenvector of a symmetric PSD `3×3` (plane normal for nearly coplanar points).
    private static func smallestEigenvectorSymmetric3x3(_ c: simd_float3x3) -> SIMD3<Float> {
        var v = SIMD3<Float>(1, 0, 0)
        let id = matrix_identity_float3x3
        let eps: Float = 1e-9
        for _ in 0 ..< 48 {
            let a = c + eps * id
            let invA = simd_inverse(a)
            let w = invA * v
            let len = simd_length(w)
            guard len > 1e-12 else { return SIMD3(0, 1, 0) }
            v = w / len
        }
        return simd_normalize(v)
    }

    private struct SubjectTriangleMeta {
        let centroid: SIMD3<Float>
        let normal: SIMD3<Float>
        let area: Float
    }

    private static func subjectTriangleMetaList(from triangles: [(SIMD3<Float>, SIMD3<Float>, SIMD3<Float>)]) -> [SubjectTriangleMeta] {
        var out: [SubjectTriangleMeta] = []
        out.reserveCapacity(triangles.count)
        for (a, b, c) in triangles {
            let e1 = b - a
            let e2 = c - a
            let cr = simd_cross(e1, e2)
            let len = simd_length(cr)
            guard len > 1e-14 else { continue }
            let area = 0.5 * len
            let n = cr / len
            out.append(SubjectTriangleMeta(centroid: (a + b + c) / 3, normal: n, area: area))
        }
        return out
    }

    /// Estimates **unit up** (slice plane normal) when there is no usable opening rim. Uses bottom-facing
    /// triangle reinforcement with multiple gravity seeds.
    private static func inferMeshBaseUpNormal(from triangles: [(SIMD3<Float>, SIMD3<Float>, SIMD3<Float>)]) throws -> SIMD3<Float> {
        let meta = subjectTriangleMetaList(from: triangles)
        guard !meta.isEmpty else { throw BasePerimeterExtractorError.couldNotInferSliceOrientation }

        let seeds: [SIMD3<Float>] = [
            SIMD3(0, -1, 0), SIMD3(0, 0, -1), SIMD3(-1, 0, 0),
            SIMD3(0, 1, 0), SIMD3(0, 0, 1), SIMD3(1, 0, 0),
        ]

        var bestUp = SIMD3<Float>(0, 1, 0)
        var bestScore: Float = -1
        var gotRefinement = false
        for sd in seeds {
            let downHint = simd_normalize(sd)
            guard simd_length_squared(downHint) > 1e-8 else { continue }
            guard let downFinal = refineDownHint(meta: meta, downSeed: downHint) else { continue }
            gotRefinement = true
            let up = -downFinal
            let score = bottomSupportScore(meta: meta, up: up)
            if score > bestScore {
                bestScore = score
                bestUp = up
            }
        }

        guard gotRefinement else { throw BasePerimeterExtractorError.couldNotInferSliceOrientation }
        return simd_normalize(bestUp)
    }

    private static func refineDownHint(meta: [SubjectTriangleMeta], downSeed: SIMD3<Float>) -> SIMD3<Float>? {
        var downHint = simd_normalize(downSeed)
        guard simd_length_squared(downHint) > 1e-8 else { return nil }

        for _ in 0 ..< 6 {
            let heights = meta.map { simd_dot($0.centroid, downHint) }
            guard let hMin = heights.min(), let hMax = heights.max(), hMax - hMin > 1e-8 else { return nil }
            let span = hMax - hMin
            let cutoff = hMin + span * 0.2

            var acc = SIMD3<Float>.zero
            for (i, t) in meta.enumerated() {
                guard heights[i] <= cutoff else { continue }
                var nOut = t.normal
                if simd_dot(nOut, downHint) < 0 { nOut = -nOut }
                if simd_dot(nOut, downHint) < 0.15 { continue }
                acc += t.area * nOut
            }
            let al = simd_length(acc)
            guard al > 1e-10 else { return nil }
            downHint = acc / al
        }
        return downHint
    }

    private static func bottomSupportScore(meta: [SubjectTriangleMeta], up: SIMD3<Float>) -> Float {
        let u = simd_normalize(up)
        guard simd_length_squared(u) > 0.5 else { return 0 }
        let heights = meta.map { simd_dot($0.centroid, u) }
        guard let hMin = heights.min(), let hMax = heights.max(), hMax - hMin > 1e-8 else { return 0 }
        let cut = hMin + (hMax - hMin) * 0.35
        var score: Float = 0
        for (i, t) in meta.enumerated() {
            guard heights[i] <= cut else { continue }
            var nO = t.normal
            if simd_dot(nO, u) < 0 { nO = -nO }
            if simd_dot(nO, u) > 0.18 { score += t.area }
        }
        return score
    }

    private static func filterTriangles(
        _ triangles: [(SIMD3<Float>, SIMD3<Float>, SIMD3<Float>)],
        excluding box: WorldAABBExclusion
    ) -> [(SIMD3<Float>, SIMD3<Float>, SIMD3<Float>)] {
        triangles.filter { !box.containsTriangleCentroid($0.0, $0.1, $0.2) }
    }

    private static func collectSubjectTriangles(
        from object: MDLObject,
        parentWorld: float4x4,
        excludePattern: String?,
        excludedAncestor: Bool,
        into triangles: inout [(SIMD3<Float>, SIMD3<Float>, SIMD3<Float>)]
    ) {
        let local = object.transform.map(\.matrix) ?? matrix_identity_float4x4
        let world = parentWorld * local

        let nameHit = excludePattern.map { object.name.localizedCaseInsensitiveContains($0) } ?? false
        let excluded = excludedAncestor || nameHit

        if !excluded, let mesh = object as? MDLMesh {
            appendSubjectTriangles(from: mesh, world: world, into: &triangles)
        }

        for child in object.children.objects {
            collectSubjectTriangles(
                from: child,
                parentWorld: world,
                excludePattern: excludePattern,
                excludedAncestor: excluded,
                into: &triangles
            )
        }
    }

    private static func appendSubjectTriangles(
        from mesh: MDLMesh,
        world: float4x4,
        into triangles: inout [(SIMD3<Float>, SIMD3<Float>, SIMD3<Float>)]
    ) {
        guard let posData = mesh.vertexAttributeData(forAttributeNamed: MDLVertexAttributePosition) else { return }
        let stride = posData.stride
        let posBase = UnsafeRawPointer(posData.dataStart)
        let format = posData.format
        guard format == .float3 || format == .float4 else { return }

        func vertex(at index: Int) -> SIMD3<Float> {
            let off = index * stride
            let x = posBase.load(fromByteOffset: off, as: Float.self)
            let y = posBase.load(fromByteOffset: off + 4, as: Float.self)
            let z = posBase.load(fromByteOffset: off + 8, as: Float.self)
            return SIMD3(x, y, z)
        }

        let submeshes = mesh.submeshes as? [MDLSubmesh] ?? []
        for sub in submeshes {
            guard sub.geometryType == .triangles else { continue }
            let ib = sub.indexBuffer
            let indexCount = sub.indexCount
            let indexType = sub.indexType
            let map = ib.map()
            let indexBytes = UnsafeRawPointer(map.bytes)

            func readIndex(_ i: Int) -> Int {
                switch indexType {
                case .uInt32:
                    return Int(indexBytes.load(fromByteOffset: i * 4, as: UInt32.self))
                case .uInt16:
                    return Int(indexBytes.load(fromByteOffset: i * 2, as: UInt16.self))
                default:
                    return 0
                }
            }

            var i = 0
            while i + 2 < indexCount {
                let i0 = readIndex(i)
                let i1 = readIndex(i + 1)
                let i2 = readIndex(i + 2)
                i += 3
                let p0 = transformPoint(world, vertex(at: i0))
                let p1 = transformPoint(world, vertex(at: i1))
                let p2 = transformPoint(world, vertex(at: i2))
                triangles.append((p0, p1, p2))
            }
        }
    }

    private static func transformPoint(_ m: float4x4, _ p: SIMD3<Float>) -> SIMD3<Float> {
        let h = m * SIMD4<Float>(p.x, p.y, p.z, 1)
        return SIMD3(h.x, h.y, h.z) / max(h.w, 1e-8)
    }

    /// All line segments where a triangle meets the slice plane (0–3 per triangle). Replaces the old
    /// single-segment + longest-chord shortcut, which collapsed coplanar triangles to one wrong chord and
    /// starved the segment graph (few vertices, random loop success).
    private static func intersectTrianglePlaneSegments(
        a: SIMD3<Float>, b: SIMD3<Float>, c: SIMD3<Float>,
        normal n: SIMD3<Float>, planeD: Float,
        axisU: SIMD3<Float>, axisV: SIMD3<Float>,
        epsilon: Float
    ) -> [(SIMD2<Float>, SIMD2<Float>)] {
        let uv: (SIMD3<Float>) -> SIMD2<Float> = { p in
            SIMD2(simd_dot(p, axisU), simd_dot(p, axisV))
        }
        func signed(_ p: SIMD3<Float>) -> Float { simd_dot(p, n) - planeD }
        func on(_ h: Float) -> Bool { abs(h) <= epsilon }

        let ha = signed(a), hb = signed(b), hc = signed(c)
        let oa = on(ha), ob = on(hb), oc = on(hc)

        // Triangle lies in the slice plane: contribution is its three edges in U/V (not one diagonal).
        if oa && ob && oc {
            let ua = uv(a), ub = uv(b), uc = uv(c)
            let minLen = max(epsilon * 0.01, 1e-9)
            let s: [(SIMD2<Float>, SIMD2<Float>)] = [(ua, ub), (ub, uc), (uc, ua)]
            return s.filter { simd_distance($0.0, $0.1) > minLen }
        }

        var hits: [SIMD3<Float>] = []

        func addEdge(_ p: SIMD3<Float>, _ q: SIMD3<Float>, _ hp: Float, _ hq: Float) {
            if on(hp) && on(hq) {
                // Edge lies in plane (partial coplanar strip): keep as segment endpoints.
                if simd_distance(p, q) > epsilon * 0.01 {
                    hits.append(p)
                    hits.append(q)
                }
                return
            }
            if on(hp) { hits.append(p); return }
            if on(hq) { hits.append(q); return }
            if hp * hq < 0 {
                let t = hp / (hp - hq)
                hits.append(p + t * (q - p))
            }
        }

        addEdge(a, b, ha, hb)
        addEdge(b, c, hb, hc)
        addEdge(c, a, hc, ha)

        let tol = max(epsilon * 20, 1e-8)
        var uniq: [SIMD3<Float>] = []
        for p in hits {
            if !uniq.contains(where: { simd_distance($0, p) < tol }) {
                uniq.append(p)
            }
        }

        let minSeg = max(epsilon * 1e-3, 1e-9)
        switch uniq.count {
        case 0:
            return []
        case 1:
            return []
        case 2:
            let p0 = uv(uniq[0]), p1 = uv(uniq[1])
            return simd_distance(p0, p1) > minSeg ? [(p0, p1)] : []
        default:
            // Rare: >2 intersections (degeneracy); span in UV by extremal pair.
            let uvs = uniq.map { uv($0) }
            var bestI = 0, bestJ = 1
            var bestD: Float = 0
            for i in 0 ..< uvs.count {
                for j in (i + 1) ..< uvs.count {
                    let d = simd_distance(uvs[i], uvs[j])
                    if d > bestD {
                        bestD = d
                        bestI = i
                        bestJ = j
                    }
                }
            }
            if bestD > minSeg {
                return [(uvs[bestI], uvs[bestJ])]
            }
            return []
        }
    }

    private static func orthonormalBasis(onPlaneWithNormal n: SIMD3<Float>) -> (SIMD3<Float>, SIMD3<Float>) {
        let ref = abs(n.y) < Float(0.9) ? SIMD3<Float>(0, 1, 0) : SIMD3<Float>(1, 0, 0)
        let u = simd_normalize(simd_cross(n, ref))
        let v = simd_normalize(simd_cross(n, u))
        return (u, v)
    }

    private static func pointOnPlane(
        u: Float, v: Float, planeD: Float, n: SIMD3<Float>,
        axisU: SIMD3<Float>, axisV: SIMD3<Float>
    ) -> SIMD3<Float> {
        let denom = simd_dot(n, n)
        let anchor = (planeD / max(denom, 1e-8)) * n
        return anchor + u * axisU + v * axisV
    }

    private static func largestPerimeterLoop(from segments: [(SIMD2<Float>, SIMD2<Float>)], snapEps: Float) throws -> [SIMD2<Float>] {
        func snap(_ p: SIMD2<Float>) -> SIMD2<Float> {
            SIMD2(round(p.x / snapEps) * snapEps, round(p.y / snapEps) * snapEps)
        }

        var adj: [SIMD2<Float>: [SIMD2<Float>]] = [:]
        func addUndirected(_ p: SIMD2<Float>, _ q: SIMD2<Float>) {
            let sp = snap(p)
            let sq = snap(q)
            if simd_distance(sp, sq) < snapEps * 0.25 { return }
            adj[sp, default: []].append(sq)
            adj[sq, default: []].append(sp)
        }

        for s in segments {
            addUndirected(s.0, s.1)
        }

        // Duplicate edges from adjacent triangles inflate neighbor counts (>2) so no vertex qualifies
        // as a degree-2 loop start. Collapse duplicate neighbor entries (same snapped target).
        for key in Array(adj.keys) {
            guard let list = adj[key] else { continue }
            var unique: [SIMD2<Float>] = []
            for nb in list {
                if !unique.contains(where: { simd_distance($0, nb) < snapEps * 0.15 }) {
                    unique.append(nb)
                }
            }
            adj[key] = unique
        }

        var bestLoop: [SIMD2<Float>] = []
        var bestLen: Float = 0
        let maxTraceSteps = max(16_384, segments.count * 2, adj.count * 4)

        for start in adj.keys {
            guard adj[start]?.count == 2 else { continue }
            guard let loop = traceLoop(start: start, adj: adj, snapEps: snapEps, maxSteps: maxTraceSteps) else { continue }

            var len: Float = 0
            if loop.count >= 2 {
                for i in 0 ..< (loop.count - 1) {
                    len += simd_distance(loop[i], loop[i + 1])
                }
                len += simd_distance(loop[loop.count - 1], loop[0])
            }
            if len > bestLen {
                bestLen = len
                bestLoop = loop
            }
        }

        if bestLoop.isEmpty {
            throw BasePerimeterExtractorError.couldNotFormLoop
        }
        return bestLoop
    }

    private static func traceLoop(
        start: SIMD2<Float>,
        adj: [SIMD2<Float>: [SIMD2<Float>]],
        snapEps: Float,
        maxSteps: Int
    ) -> [SIMD2<Float>]? {
        guard let nb = adj[start], nb.count == 2 else { return nil }
        let close = max(snapEps * 0.01, 1e-6)
        var path: [SIMD2<Float>] = [start]
        var prev = start
        var cur = nb[0]

        for _ in 0 ..< maxSteps {
            if simd_distance(cur, start) < close && path.count >= 2 {
                return path
            }
            path.append(cur)
            guard let neighbors = adj[cur] else { return nil }
            guard let nxt = neighbors.first(where: { simd_distance($0, prev) > close }) else { return nil }
            if simd_distance(nxt, start) < close {
                return path
            }
            prev = cur
            cur = nxt
        }
        return nil
    }

    private static func polygonCentroid(_ poly: [SIMD2<Float>]) -> SIMD2<Float> {
        let n = poly.count
        guard n >= 3 else { return poly.first ?? .zero }
        var a: Float = 0
        var cx: Float = 0
        var cy: Float = 0
        for i in 0 ..< n {
            let j = (i + 1) % n
            let cross = poly[i].x * poly[j].y - poly[j].x * poly[i].y
            a += cross
            cx += (poly[i].x + poly[j].x) * cross
            cy += (poly[i].y + poly[j].y) * cross
        }
        a *= 0.5
        if abs(a) < 1e-14 {
            var s = SIMD2<Float>.zero
            for p in poly { s += p }
            return s / Float(n)
        }
        return SIMD2(cx / (6 * a), cy / (6 * a))
    }

    /// Interior point at the shape center: area centroid when inside, else closest interior point (concave slices can push the centroid outside).
    private static func polarOrigin2D(in loop: [SIMD2<Float>]) -> SIMD2<Float> {
        let centroid = polygonCentroid(loop)
        if pointInPolygon2D(centroid, loop) { return centroid }
        let bc = bboxCenter2D(loop)
        if pointInPolygon2D(bc, loop) { return bc }
        if let best = interiorPointClosest(to: centroid, in: loop) { return best }
        return centroid
    }

    private static func interiorPointClosest(to target: SIMD2<Float>, in loop: [SIMD2<Float>]) -> SIMD2<Float>? {
        let xs = loop.map(\.x)
        let ys = loop.map(\.y)
        guard let minX = xs.min(), let maxX = xs.max(), let minY = ys.min(), let maxY = ys.max() else {
            return nil
        }
        var best: SIMD2<Float>?
        var bestD = Float.greatestFiniteMagnitude
        for iy in 0 ... 20 {
            for ix in 0 ... 20 {
                let p = SIMD2(
                    minX + (maxX - minX) * Float(ix) / 20,
                    minY + (maxY - minY) * Float(iy) / 20
                )
                guard pointInPolygon2D(p, loop) else { continue }
                let d = simd_distance_squared(p, target)
                if d < bestD {
                    bestD = d
                    best = p
                }
            }
        }
        return best
    }

    private static func bboxCenter2D(_ poly: [SIMD2<Float>]) -> SIMD2<Float> {
        let xs = poly.map(\.x)
        let ys = poly.map(\.y)
        guard let minX = xs.min(), let maxX = xs.max(), let minY = ys.min(), let maxY = ys.max() else {
            return .zero
        }
        return SIMD2((minX + maxX) * 0.5, (minY + maxY) * 0.5)
    }

    /// Ray-crossing parity test (closed `poly`, CCW or CW).
    private static func pointInPolygon2D(_ p: SIMD2<Float>, _ poly: [SIMD2<Float>]) -> Bool {
        guard poly.count >= 3 else { return false }
        var inside = false
        var j = poly.count - 1
        for i in 0 ..< poly.count {
            let pi = poly[i]
            let pj = poly[j]
            let yi = pi.y > p.y
            let yj = pj.y > p.y
            if yi != yj {
                let xInt = (pj.x - pi.x) * (p.y - pi.y) / max(pj.y - pi.y, 1e-12) + pi.x
                if p.x < xInt {
                    inside.toggle()
                }
            }
            j = i
        }
        return inside
    }

    /// 100 points **evenly spaced by distance along the slice outline** (continuous path). Polar `(θ,r)` on each sample are derived from the Cartesian point for compatibility.
    private static func arcLengthResample100(polygon shifted: [SIMD2<Float>]) -> [BasePlaneSample] {
        let m = shifted.count
        guard m >= 3 else { return polarResample100(polygon: shifted) }

        var edgeLen: [Float] = []
        edgeLen.reserveCapacity(m)
        var total: Float = 0
        for i in 0 ..< m {
            let j = (i + 1) % m
            let d = simd_distance(shifted[i], shifted[j])
            edgeLen.append(d)
            total += d
        }
        guard total > 1e-8 else { return polarResample100(polygon: shifted) }

        var out: [BasePlaneSample] = []
        out.reserveCapacity(sampleCount)
        for i in 0 ..< sampleCount {
            let target = total * Float(i) / Float(sampleCount)
            var walked: Float = 0
            var found = false
            for e in 0 ..< m {
                let el = edgeLen[e]
                if walked + el >= target - 1e-7 {
                    let t = (target - walked) / max(el, 1e-10)
                    let a = shifted[e]
                    let b = shifted[(e + 1) % m]
                    let p = a + t * (b - a)
                    let theta = atan2(p.y, p.x)
                    let r = simd_length(p)
                    out.append(BasePlaneSample(index: i, thetaRadians: theta, r: r, x: p.x, y: p.y))
                    found = true
                    break
                }
                walked += el
            }
            if !found {
                let p = shifted[0]
                out.append(BasePlaneSample(index: i, thetaRadians: atan2(p.y, p.x), r: simd_length(p), x: p.x, y: p.y))
            }
        }
        return out
    }

    /// Legacy polar ray resample (can produce many `r == 0` and a non-continuous polyline when chained by index).
    private static func polarResample100(polygon shifted: [SIMD2<Float>]) -> [BasePlaneSample] {
        let m = shifted.count
        var edges: [(SIMD2<Float>, SIMD2<Float>)] = []
        for i in 0 ..< m {
            edges.append((shifted[i], shifted[(i + 1) % m]))
        }

        let twoPi = Float(2 * Double.pi)
        var out: [BasePlaneSample] = []
        out.reserveCapacity(sampleCount)
        for i in 0 ..< sampleCount {
            let theta = twoPi * Float(i) / Float(sampleCount)
            let dir = SIMD2(cos(theta), sin(theta))
            var bestT: Float = .greatestFiniteMagnitude
            var found = false
            for e in edges {
                if let t = raySegmentIntersect2D(origin: .zero, dir: dir, a: e.0, b: e.1), t > 1e-6 {
                    if !found || t < bestT {
                        bestT = t
                        found = true
                    }
                }
            }
            let r = found ? bestT : 0
            out.append(BasePlaneSample(index: i, thetaRadians: theta, r: r, x: r * cos(theta), y: r * sin(theta)))
        }
        return out
    }

    private static func raySegmentIntersect2D(origin: SIMD2<Float>, dir: SIMD2<Float>, a: SIMD2<Float>, b: SIMD2<Float>) -> Float? {
        let v = b - a
        let wo = a - origin
        let denom = cross2D(dir, v)
        if abs(denom) < 1e-10 { return nil }
        let t = cross2D(wo, v) / denom
        let s = cross2D(dir, wo) / denom
        if t >= 0, s >= 0, s <= 1 {
            return t
        }
        return nil
    }

    private static func cross2D(_ p: SIMD2<Float>, _ q: SIMD2<Float>) -> Float {
        p.x * q.y - p.y * q.x
    }
}

extension ManualSliceAxisTuning {
    /// Approximate mesh X→Y→Z Euler tilts that rotate `baseAxis` toward `planeNormal`.
    static func decomposed(
        planeNormal: SIMD3<Float>,
        baseAxis: BaseSliceUpAxis,
        spinDegrees: Double
    ) -> ManualSliceAxisTuning {
        let base = simd_normalize(baseAxis.unitNormal)
        let target = simd_normalize(planeNormal)
        let q = simd_quatf(from: base, to: target)
        let euler = eulerXYZDegrees(from: q)
        return ManualSliceAxisTuning(
            baseAxis: baseAxis,
            tiltXDegrees: euler.x,
            tiltYDegrees: euler.y,
            tiltZDegrees: euler.z,
            spinDegrees: spinDegrees
        )
    }

    private static func eulerXYZDegrees(from q: simd_quatf) -> SIMD3<Double> {
        let x = Double(q.imag.x)
        let y = Double(q.imag.y)
        let z = Double(q.imag.z)
        let w = Double(q.real)

        let sinY = 2 * (w * y - z * x)
        let pitchY = asin(max(-1, min(1, sinY)))

        let cosY = cos(pitchY)
        let rollX: Double
        let yawZ: Double
        if abs(cosY) > 1e-8 {
            rollX = atan2(2 * (w * x + y * z), 1 - 2 * (x * x + y * y))
            yawZ = atan2(2 * (w * z + x * y), 1 - 2 * (y * y + z * z))
        } else {
            rollX = atan2(-2 * (w * z - x * y), 1 - 2 * (x * x + z * z))
            yawZ = 0
        }

        return SIMD3(
            rollX * 180 / Double.pi,
            pitchY * 180 / Double.pi,
            yawZ * 180 / Double.pi
        )
    }
}
