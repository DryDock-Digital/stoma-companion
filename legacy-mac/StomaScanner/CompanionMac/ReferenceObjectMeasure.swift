import Foundation
import ModelIO
import simd

enum ReferenceObjectMeasureError: Error, LocalizedError {
    case emptyName
    case noMatch
    case noGeometry

    var errorDescription: String? {
        switch self {
        case .emptyName: "Enter a non-empty name substring to search for."
        case .noMatch: "No object whose name contains that substring was found."
        case .noGeometry: "No mesh geometry found under the matching object."
        }
    }
}

/// Measures a named calibration subtree in a USDZ (world-space AABB longest edge).
enum ReferenceObjectMeasure {
    /// Longest side of the world axis-aligned bounding box of all geometry under the first matching `MDLObject` name.
    static func longestWorldAABBEdge(
        usdzURL: URL,
        nameContains: String
    ) throws -> Float {
        let pattern = nameContains.trimmingCharacters(in: .whitespacesAndNewlines)
        guard !pattern.isEmpty else { throw ReferenceObjectMeasureError.emptyName }

        let asset = MDLAsset(url: usdzURL)
        guard asset.count > 0 else { throw ReferenceObjectMeasureError.noMatch }

        var hasMatch = false
        var minP = SIMD3<Float>(repeating: .greatestFiniteMagnitude)
        var maxP = SIMD3<Float>(repeating: -.greatestFiniteMagnitude)

        for i in 0 ..< asset.count {
            walk(
                object: asset.object(at: i),
                parentWorld: matrix_identity_float4x4,
                namePattern: pattern,
                inMatchingSubtree: false,
                hasMatch: &hasMatch,
                minP: &minP,
                maxP: &maxP
            )
        }

        guard hasMatch else { throw ReferenceObjectMeasureError.noMatch }
        let span = maxP - minP
        let edge = max(span.x, max(span.y, span.z))
        guard edge.isFinite, edge > 1e-8 else { throw ReferenceObjectMeasureError.noGeometry }
        return edge
    }

    private static func walk(
        object: MDLObject,
        parentWorld: float4x4,
        namePattern: String,
        inMatchingSubtree: Bool,
        hasMatch: inout Bool,
        minP: inout SIMD3<Float>,
        maxP: inout SIMD3<Float>
    ) {
        let local = object.transform.map(\.matrix) ?? matrix_identity_float4x4
        let world = parentWorld * local

        let selfMatches = object.name.localizedCaseInsensitiveContains(namePattern)
        let active = inMatchingSubtree || selfMatches
        if selfMatches {
            hasMatch = true
        }

        if active, let mesh = object as? MDLMesh {
            appendMeshVertices(from: mesh, world: world, minP: &minP, maxP: &maxP)
        }

        for child in object.children.objects {
            walk(
                object: child,
                parentWorld: world,
                namePattern: namePattern,
                inMatchingSubtree: active,
                hasMatch: &hasMatch,
                minP: &minP,
                maxP: &maxP
            )
        }
    }

    private static func appendMeshVertices(
        from mesh: MDLMesh,
        world: float4x4,
        minP: inout SIMD3<Float>,
        maxP: inout SIMD3<Float>
    ) {
        guard let posData = mesh.vertexAttributeData(forAttributeNamed: MDLVertexAttributePosition) else { return }
        let stride = posData.stride
        let posBase = UnsafeRawPointer(posData.dataStart)
        let format = posData.format
        guard format == .float3 || format == .float4 else { return }

        let vCount = mesh.vertexCount
        for vi in 0 ..< vCount {
            let off = vi * stride
            let x = posBase.load(fromByteOffset: off, as: Float.self)
            let y = posBase.load(fromByteOffset: off + 4, as: Float.self)
            let z = posBase.load(fromByteOffset: off + 8, as: Float.self)
            let p = transformPoint(world, SIMD3(x, y, z))
            minP = simd_min(minP, p)
            maxP = simd_max(maxP, p)
        }
    }

    private static func transformPoint(_ m: float4x4, _ p: SIMD3<Float>) -> SIMD3<Float> {
        let h = m * SIMD4<Float>(p.x, p.y, p.z, 1)
        return SIMD3(h.x, h.y, h.z) / max(h.w, 1e-8)
    }
}
