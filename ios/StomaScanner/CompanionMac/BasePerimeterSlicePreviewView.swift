import AppKit
import SceneKit
import simd
import SwiftUI

/// Extra magnification when framing the mesh in slice preview (1.5 = 50% closer than fit).
private let slicePreviewDefaultZoomMagnification: Float = 1.5

/// 3D mesh + moving slice plane (matches `BasePerimeterExtractor` height range).
struct BasePerimeterSlicePreviewView: View {
    let usdzURL: URL?
    /// True when `usdzURL` is user-picked (needs security-scoped read for ModelIO / SceneKit).
    let requiresSecurityScopedAccess: Bool
    let excludeNameContains: String?
    /// Optional world AABB to exclude (mesh ArUco) from height bounds / framing.
    var excludeWorldAABB: WorldAABBExclusion? = nil
    /// Optional ArUco world corners (USDZ space) for green confirmation overlay.
    var markerWorldCorners: [SIMD3<Float>] = []
    let upAxis: BaseSliceUpAxis
    var manualTilt: ManualSliceAxisTuning = .default
    let sliceOffsetFraction: Float

    @State private var loadError: String?
    @State private var zoomToFitTrigger: Int = 0
    @State private var floorDetectionHint: String?

    var body: some View {
        VStack(alignment: .leading, spacing: 6) {
            HStack(alignment: .firstTextBaseline) {
                Text("Slice preview")
                    .font(.subheadline.weight(.semibold))
                Spacer(minLength: 8)
                Button("Zoom to fit") {
                    zoomToFitTrigger += 1
                }
                .buttonStyle(.bordered)
                .controlSize(.small)
                .disabled(usdzURL == nil)
                .help("Frames the subject (respects name exclusion when enabled). Click again after resizing the window.")
            }
            Text("Drag to orbit. Slice plane updates when you change tilt or offset; your camera position is kept.")
                .font(.caption2)
                .foregroundStyle(.secondary)
                .fixedSize(horizontal: false, vertical: true)

            if markerWorldCorners.count == 4 {
                Label("ArUco square locked (green)", systemImage: "checkmark.seal.fill")
                    .font(.caption2)
                    .foregroundStyle(.green)
            }

            if let hint = floorDetectionHint {
                Text(hint)
                    .font(.caption2)
                    .foregroundStyle(.orange)
                    .fixedSize(horizontal: false, vertical: true)
            }

            ZStack(alignment: .bottomLeading) {
                RoundedRectangle(cornerRadius: 8, style: .continuous)
                    .fill(Color(nsColor: .controlBackgroundColor))

                if usdzURL != nil {
                    SliceSceneKitContainer(
                        usdzURL: usdzURL!,
                        requiresSecurityScopedAccess: requiresSecurityScopedAccess,
                        excludeNameContains: excludeNameContains,
                        excludeWorldAABB: excludeWorldAABB,
                        markerWorldCorners: markerWorldCorners,
                        upAxis: upAxis,
                        manualTilt: manualTilt,
                        sliceOffsetFraction: sliceOffsetFraction,
                        zoomToFitTrigger: $zoomToFitTrigger,
                        loadError: $loadError,
                        floorDetectionHint: $floorDetectionHint
                    )
                    .clipShape(RoundedRectangle(cornerRadius: 8, style: .continuous))
                } else {
                    Text("Choose a USDZ (or use the built-in scan) to preview the slice plane.")
                        .font(.caption)
                        .foregroundStyle(.secondary)
                        .padding(12)
                }

                if let loadError {
                    Text(loadError)
                        .font(.caption2)
                        .foregroundStyle(.red)
                        .padding(8)
                        .frame(maxWidth: .infinity, alignment: .leading)
                        .background(.ultraThinMaterial)
                }
            }
            .frame(minHeight: 280)
        }
    }
}

// MARK: - SceneKit

private struct SliceSceneKitContainer: NSViewRepresentable {
    let usdzURL: URL
    let requiresSecurityScopedAccess: Bool
    let excludeNameContains: String?
    let excludeWorldAABB: WorldAABBExclusion?
    let markerWorldCorners: [SIMD3<Float>]
    let upAxis: BaseSliceUpAxis
    var manualTilt: ManualSliceAxisTuning = .default
    let sliceOffsetFraction: Float
    @Binding var zoomToFitTrigger: Int
    @Binding var loadError: String?
    @Binding var floorDetectionHint: String?

    func makeCoordinator() -> Coordinator {
        Coordinator()
    }

    func makeNSView(context: Context) -> SCNView {
        let persp = SCNView()
        persp.backgroundColor = NSColor(white: 0.12, alpha: 1)
        persp.allowsCameraControl = true
        persp.autoenablesDefaultLighting = false
        persp.antialiasingMode = .multisampling4X
        context.coordinator.configure(perspView: persp)
        return persp
    }

    func updateNSView(_ perspView: SCNView, context: Context) {
        context.coordinator.update(
            usdzURL: usdzURL,
            requiresSecurityScopedAccess: requiresSecurityScopedAccess,
            excludeNameContains: excludeNameContains,
            excludeWorldAABB: excludeWorldAABB,
            markerWorldCorners: markerWorldCorners,
            upAxis: upAxis,
            manualTilt: manualTilt,
            sliceOffsetFraction: sliceOffsetFraction,
            zoomToFitTrigger: zoomToFitTrigger,
            loadError: $loadError,
            floorDetectionHint: $floorDetectionHint
        )
    }

    final class Coordinator {
        private weak var perspView: SCNView?

        private var scene: SCNScene?
        private var planeNode: SCNNode?
        private var floorPlaneNode: SCNNode?
        private var markerOverlayNode: SCNNode?
        private var perspCameraNode: SCNNode?
        private var heightBounds: BaseSliceHeightBounds?
        private var worldMin = SIMD3<Float>(repeating: 0)
        private var worldMax = SIMD3<Float>(repeating: 0)
        private var worldDiagonal: Float = 1
        private var meshRootPositionBeforeFloor = SIMD3<Float>.zero
        private var boundsExcludePattern: String?
        private var lastMarkerCorners: [SIMD3<Float>] = []

        private var loadedSceneKey: SceneKey?
        private var loadedOrientationKey: OrientationKey?
        private var inflightSceneKey: SceneKey?
        private var inflightOrientationKey: OrientationKey?
        private var loadGeneration = 0
        private var pendingFraction: Float = 0
        private var lastProcessedZoomTrigger: Int = 0
        private var lastSeenZoomTrigger: Int = 0

        private static let markerOverlayNodeName = "arucoMarkerOverlay"

        private struct SceneKey: Equatable {
            let path: String
            let excludeLowercased: String?
            let aabbMin: SIMD3<Float>?
            let aabbMax: SIMD3<Float>?
        }

        private struct OrientationKey: Equatable {
            let axis: BaseSliceUpAxis
            let manualTilt: ManualSliceAxisTuning
        }

        func configure(perspView: SCNView) {
            self.perspView = perspView
        }

        func update(
            usdzURL: URL,
            requiresSecurityScopedAccess: Bool,
            excludeNameContains: String?,
            excludeWorldAABB: WorldAABBExclusion?,
            markerWorldCorners: [SIMD3<Float>],
            upAxis: BaseSliceUpAxis,
            manualTilt: ManualSliceAxisTuning,
            sliceOffsetFraction: Float,
            zoomToFitTrigger: Int,
            loadError: Binding<String?>,
            floorDetectionHint: Binding<String?>
        ) {
            lastSeenZoomTrigger = zoomToFitTrigger
            pendingFraction = sliceOffsetFraction
            let ex = excludeNameContains?.trimmingCharacters(in: .whitespacesAndNewlines)
            let excludeKey = (ex?.isEmpty == false) ? ex!.lowercased() : nil
            let sceneKey = SceneKey(
                path: usdzURL.path,
                excludeLowercased: excludeKey,
                aabbMin: excludeWorldAABB?.min,
                aabbMax: excludeWorldAABB?.max
            )
            let orientationKey = OrientationKey(
                axis: upAxis,
                manualTilt: upAxis == .manualTilt ? manualTilt : .default
            )
            let pattern: String? = (ex?.isEmpty == false) ? ex : nil
            let aabb = excludeWorldAABB

            if loadedSceneKey == sceneKey, scene != nil, heightBounds != nil {
                if loadedOrientationKey != orientationKey {
                    if inflightOrientationKey == orientationKey { return }
                    refreshOrientation(
                        usdzURL: usdzURL,
                        requiresSecurityScopedAccess: requiresSecurityScopedAccess,
                        upAxis: upAxis,
                        manualTilt: manualTilt,
                        orientationKey: orientationKey,
                        pattern: pattern,
                        excludeWorldAABB: aabb,
                        fraction: sliceOffsetFraction,
                        loadError: loadError,
                        floorDetectionHint: floorDetectionHint
                    )
                } else {
                    applyPlaneTransform(fraction: sliceOffsetFraction)
                    updateMarkerOverlay(corners: markerWorldCorners)
                    processZoomToFitIfNeeded()
                    deferLoadErrorClear(loadError)
                }
                return
            }

            if inflightSceneKey == sceneKey { return }

            loadGeneration += 1
            let gen = loadGeneration
            inflightSceneKey = sceneKey
            inflightOrientationKey = nil
            floorDetectionHint.wrappedValue = nil

            let cornersForLoad = markerWorldCorners
            Task.detached(priority: .utility) { [usdzURL, requiresSecurityScopedAccess, upAxis, manualTilt, pattern, aabb, gen, orientationKey, cornersForLoad] in
                var accessStarted = false
                if requiresSecurityScopedAccess {
                    accessStarted = usdzURL.startAccessingSecurityScopedResource()
                    guard accessStarted else {
                        await MainActor.run {
                            if gen == self.loadGeneration {
                                self.inflightSceneKey = nil
                                loadError.wrappedValue = "Could not access the USDZ file."
                            }
                        }
                        return
                    }
                }
                defer {
                    if accessStarted {
                        usdzURL.stopAccessingSecurityScopedResource()
                    }
                }

                let bounds: BaseSliceHeightBounds
                do {
                    bounds = try BasePerimeterExtractor.subjectSliceHeightBounds(
                        usdzURL: usdzURL,
                        upAxis: upAxis,
                        manualTilt: manualTilt,
                        excludeObjectNameSubstring: pattern,
                        excludeWorldAABB: aabb
                    )
                } catch {
                    await MainActor.run {
                        guard gen == self.loadGeneration else { return }
                        self.inflightSceneKey = nil
                        self.loadedSceneKey = nil
                        self.loadedOrientationKey = nil
                        self.scene = nil
                        self.heightBounds = nil
                        self.perspView?.scene = nil
                        loadError.wrappedValue = error.localizedDescription
                    }
                    return
                }

                let loadedScene: SCNScene
                do {
                    loadedScene = try SCNScene(url: usdzURL, options: nil)
                } catch {
                    await MainActor.run {
                        guard gen == self.loadGeneration else { return }
                        self.inflightSceneKey = nil
                        self.loadedSceneKey = nil
                        loadError.wrappedValue = error.localizedDescription
                    }
                    return
                }

                await MainActor.run {
                    guard gen == self.loadGeneration else { return }

                    self.applyOpacityExclusion(root: loadedScene.rootNode, pattern: pattern)

                    self.meshRootPositionBeforeFloor = loadedScene.rootNode.simdPosition
                    self.applyFloorShift(bounds: bounds, to: loadedScene.rootNode)

                    self.addDefaultLighting(to: loadedScene.rootNode)

                    let (wMin, wMax, diag) = Self.worldBoundsForFraming(
                        root: loadedScene.rootNode,
                        excludePattern: pattern
                    )
                    self.boundsExcludePattern = pattern
                    self.worldMin = wMin
                    self.worldMax = wMax
                    self.worldDiagonal = max(diag, 1e-3)

                    self.heightBounds = bounds
                    self.scene = loadedScene
                    self.buildOrUpdatePlanes(in: loadedScene, bounds: bounds, diagonal: self.worldDiagonal)
                    self.setupPerspectiveCamera(scene: loadedScene, diagonal: self.worldDiagonal)

                    self.perspView?.scene = loadedScene
                    if let p = self.perspCameraNode { self.perspView?.pointOfView = p }

                    self.loadedSceneKey = sceneKey
                    self.loadedOrientationKey = orientationKey
                    self.inflightSceneKey = nil
                    self.applyPlaneTransform(fraction: self.pendingFraction)
                    self.updateMarkerOverlay(corners: cornersForLoad)
                    self.processZoomToFitIfNeeded()
                    loadError.wrappedValue = nil
                    floorDetectionHint.wrappedValue = Self.floorHint(for: bounds.floorDetection)
                }
            }
        }

        private func refreshOrientation(
            usdzURL: URL,
            requiresSecurityScopedAccess: Bool,
            upAxis: BaseSliceUpAxis,
            manualTilt: ManualSliceAxisTuning,
            orientationKey: OrientationKey,
            pattern: String?,
            excludeWorldAABB: WorldAABBExclusion?,
            fraction: Float,
            loadError: Binding<String?>,
            floorDetectionHint: Binding<String?>
        ) {
            loadGeneration += 1
            let gen = loadGeneration
            inflightOrientationKey = orientationKey

            Task.detached(priority: .utility) { [usdzURL, requiresSecurityScopedAccess, upAxis, manualTilt, pattern, excludeWorldAABB, gen] in
                var accessStarted = false
                if requiresSecurityScopedAccess {
                    accessStarted = usdzURL.startAccessingSecurityScopedResource()
                    guard accessStarted else {
                        await MainActor.run {
                            if gen == self.loadGeneration {
                                self.inflightOrientationKey = nil
                                loadError.wrappedValue = "Could not access the USDZ file."
                            }
                        }
                        return
                    }
                }
                defer {
                    if accessStarted {
                        usdzURL.stopAccessingSecurityScopedResource()
                    }
                }

                let bounds: BaseSliceHeightBounds
                do {
                    bounds = try BasePerimeterExtractor.subjectSliceHeightBounds(
                        usdzURL: usdzURL,
                        upAxis: upAxis,
                        manualTilt: manualTilt,
                        excludeObjectNameSubstring: pattern,
                        excludeWorldAABB: excludeWorldAABB
                    )
                } catch {
                    await MainActor.run {
                        guard gen == self.loadGeneration else { return }
                        self.inflightOrientationKey = nil
                        loadError.wrappedValue = error.localizedDescription
                    }
                    return
                }

                await MainActor.run {
                    guard gen == self.loadGeneration else { return }
                    guard let sc = self.scene else {
                        self.inflightOrientationKey = nil
                        return
                    }

                    self.applyFloorShift(bounds: bounds, to: sc.rootNode)
                    self.heightBounds = bounds
                    self.buildOrUpdatePlanes(in: sc, bounds: bounds, diagonal: self.worldDiagonal)
                    self.loadedOrientationKey = orientationKey
                    self.inflightOrientationKey = nil
                    self.applyPlaneTransform(fraction: fraction)
                    loadError.wrappedValue = nil
                    floorDetectionHint.wrappedValue = Self.floorHint(for: bounds.floorDetection)
                }
            }
        }

        private func applyFloorShift(bounds: BaseSliceHeightBounds, to root: SCNNode) {
            let n = bounds.planeNormal
            let floorShift = -n * bounds.floorH
            root.simdPosition = meshRootPositionBeforeFloor + floorShift
        }

        private func updateMarkerOverlay(corners: [SIMD3<Float>]) {
            guard let scene else { return }
            if corners == lastMarkerCorners, markerOverlayNode != nil { return }
            lastMarkerCorners = corners
            markerOverlayNode?.removeFromParentNode()
            markerOverlayNode = nil
            guard corners.count == 4 else { return }

            let root = SCNNode()
            root.name = Self.markerOverlayNodeName

            var verts: [SCNVector3] = corners.map { SCNVector3($0.x, $0.y, $0.z) }
            verts.append(verts[0])
            let sources = [SCNGeometrySource(vertices: verts)]
            let indices: [Int32] = [0, 1, 1, 2, 2, 3, 3, 0]
            let elements = [SCNGeometryElement(indices: indices, primitiveType: .line)]
            let geom = SCNGeometry(sources: sources, elements: elements)
            geom.firstMaterial?.diffuse.contents = NSColor.systemGreen
            geom.firstMaterial?.emission.contents = NSColor.systemGreen
            geom.firstMaterial?.lightingModel = .constant
            geom.firstMaterial?.isDoubleSided = true
            let lineNode = SCNNode(geometry: geom)
            lineNode.renderingOrder = 200
            root.addChildNode(lineNode)

            let ballR = max(worldDiagonal * 0.0015, 5e-5)
            for c in corners {
                let ball = SCNSphere(radius: CGFloat(ballR))
                ball.firstMaterial?.diffuse.contents = NSColor.systemGreen
                ball.firstMaterial?.emission.contents = NSColor.systemGreen
                ball.firstMaterial?.lightingModel = .constant
                let n = SCNNode(geometry: ball)
                n.simdPosition = c
                n.renderingOrder = 201
                root.addChildNode(n)
            }

            scene.rootNode.addChildNode(root)
            markerOverlayNode = root
        }

        private static func floorHint(for detection: FloorDetectionResult) -> String? {
            switch detection.method {
            case .meshYFallback:
                return "No flat support surface detected; using mesh Y axis."
            case .vertexMin:
                return "Support plane not detected; 0% slice uses vertex minimum along the chosen axis."
            case .supportPlane:
                return nil
            }
        }

        /// Avoid `loadError.wrappedValue = …` synchronously from `updateNSView` (SwiftUI undefined behavior).
        private func deferLoadErrorClear(_ loadError: Binding<String?>) {
            guard loadError.wrappedValue != nil else { return }
            DispatchQueue.main.async {
                loadError.wrappedValue = nil
            }
        }

        private func processZoomToFitIfNeeded() {
            guard lastSeenZoomTrigger != lastProcessedZoomTrigger else { return }
            lastProcessedZoomTrigger = lastSeenZoomTrigger
            zoomToFit()
        }

        /// Frames **subject** (respecting exclusion) so it fills most of the view. Recomputes bounds from
        /// the live scene so the preview plane node is never included.
        private func zoomToFit() {
            guard let sc = scene,
                  let pNode = perspCameraNode,
                  let pView = perspView,
                  let pCam = pNode.camera
            else { return }

            let (mn, mx, diag) = Self.worldBoundsForFraming(root: sc.rootNode, excludePattern: boundsExcludePattern)
            let center = (mn + mx) * 0.5
            let diagonal = max(diag, 1e-4)

            let fovDeg = pCam.fieldOfView > 0 ? Float(pCam.fieldOfView) : 45
            let halfFovRad = fovDeg * Float.pi / 360
            let tanHalf = tan(max(halfFovRad, 1e-4))
            let margin: Float = 1.06
            let dist = (diagonal * 0.5) / tanHalf * margin * 0.92 / slicePreviewDefaultZoomMagnification
            let dir = simd_normalize(SIMD3<Float>(0.55, 0.45, 0.6))
            let rootT = sc.rootNode.simdPosition
            pNode.simdPosition = center + dir * dist - rootT
            pNode.look(at: SCNVector3(center.x, center.y, center.z), up: SCNVector3(0, 1, 0), localFront: SCNVector3(0, 0, -1))
            pCam.zFar = max(Double(diagonal * 25), Double(dist * 12))

            applyPlaneTransform(fraction: pendingFraction)
            pView.pointOfView = pNode
            pView.setNeedsDisplay(pView.bounds)
        }

        private func buildOrUpdatePlanes(in scene: SCNScene, bounds: BaseSliceHeightBounds, diagonal: Float) {
            planeNode?.removeFromParentNode()
            floorPlaneNode?.removeFromParentNode()

            let n = bounds.planeNormal
            let side = CGFloat(diagonal * 1.35)

            let floorPlane = SCNPlane(width: side, height: side)
            floorPlane.firstMaterial?.diffuse.contents = NSColor(calibratedRed: 0.2, green: 0.75, blue: 0.35, alpha: 0.22)
            floorPlane.firstMaterial?.isDoubleSided = true
            floorPlane.firstMaterial?.lightingModel = .constant
            floorPlane.firstMaterial?.writesToDepthBuffer = false
            floorPlane.firstMaterial?.readsFromDepthBuffer = true

            let floorNode = SCNNode(geometry: floorPlane)
            floorNode.name = Self.floorPlaneNodeName
            floorNode.renderingOrder = 90
            floorNode.simdOrientation = simd_quatf(from: SIMD3<Float>(0, 0, 1), to: n)
            floorNode.simdPosition = .zero
            scene.rootNode.addChildNode(floorNode)
            floorPlaneNode = floorNode

            let slicePlane = SCNPlane(width: side, height: side)
            slicePlane.firstMaterial?.diffuse.contents = NSColor(calibratedRed: 0.25, green: 0.55, blue: 1, alpha: 0.38)
            slicePlane.firstMaterial?.isDoubleSided = true
            slicePlane.firstMaterial?.lightingModel = .constant
            slicePlane.firstMaterial?.writesToDepthBuffer = false
            slicePlane.firstMaterial?.readsFromDepthBuffer = true

            let node = SCNNode(geometry: slicePlane)
            node.name = Self.slicePlaneNodeName
            node.renderingOrder = 100
            node.simdOrientation = simd_quatf(from: SIMD3<Float>(0, 0, 1), to: n)
            scene.rootNode.addChildNode(node)
            planeNode = node
        }

        private func setupPerspectiveCamera(scene: SCNScene, diagonal: Float) {
            perspCameraNode?.removeFromParentNode()

            let center = (worldMin + worldMax) * 0.5
            let dist = diagonal * 1.4 / slicePreviewDefaultZoomMagnification

            let pCam = SCNCamera()
            pCam.zFar = Double(diagonal * 20)
            pCam.fieldOfView = 45
            let pNode = SCNNode()
            pNode.camera = pCam
            let rootT = scene.rootNode.simdPosition
            pNode.simdPosition = center + SIMD3<Float>(0.55, 0.45, 0.6) * dist - rootT
            pNode.look(at: SCNVector3(center.x, center.y, center.z), up: SCNVector3(0, 1, 0), localFront: SCNVector3(0, 0, -1))
            scene.rootNode.addChildNode(pNode)
            perspCameraNode = pNode
        }

        private func applyPlaneTransform(fraction: Float) {
            guard let bounds = heightBounds, let planeNode else { return }
            let n = bounds.planeNormal
            let span = max(bounds.maxH - bounds.floorH, 1e-6)
            let t = max(0, min(fraction, 1))
            let planeD = bounds.floorH + t * span
            let anchor = n * planeD
            planeNode.simdPosition = anchor
            planeNode.simdOrientation = simd_quatf(from: SIMD3<Float>(0, 0, 1), to: n)
            floorPlaneNode?.simdPosition = .zero
            floorPlaneNode?.simdOrientation = simd_quatf(from: SIMD3<Float>(0, 0, 1), to: n)
        }

        private func applyOpacityExclusion(root: SCNNode, pattern: String?) {
            func visit(_ node: SCNNode, excludedAncestor: Bool) {
                let name = node.name ?? ""
                let hit = pattern.map { name.localizedCaseInsensitiveContains($0) } ?? false
                let excluded = excludedAncestor || hit
                if excluded {
                    node.opacity = 0.12
                } else if node.geometry != nil {
                    node.opacity = 1
                }
                for c in node.childNodes {
                    visit(c, excludedAncestor: excluded)
                }
            }
            visit(root, excludedAncestor: false)
        }

        private func addDefaultLighting(to root: SCNNode) {
            let ambient = SCNNode()
            ambient.light = SCNLight()
            ambient.light?.type = .ambient
            ambient.light?.color = NSColor(white: 0.55, alpha: 1)
            ambient.light?.intensity = 400
            root.addChildNode(ambient)

            let omni = SCNNode()
            omni.light = SCNLight()
            omni.light?.type = .omni
            omni.light?.color = NSColor.white
            omni.light?.intensity = 600
            omni.simdPosition = SIMD3<Float>(2, 4, 6)
            root.addChildNode(omni)
        }

        private static func axisAlignedCorners(min: SIMD3<Float>, max: SIMD3<Float>) -> [SIMD3<Float>] {
            [
                SIMD3(min.x, min.y, min.z), SIMD3(max.x, min.y, min.z),
                SIMD3(min.x, max.y, min.z), SIMD3(max.x, max.y, min.z),
                SIMD3(min.x, min.y, max.z), SIMD3(max.x, min.y, max.z),
                SIMD3(min.x, max.y, max.z), SIMD3(max.x, max.y, max.z),
            ]
        }

        private static let slicePlaneNodeName = "BasePerimeterSlicePreviewPlane"
        private static let floorPlaneNodeName = "BasePerimeterSlicePreviewFloor"

        /// Subject-only bounds when `excludePattern` is used; otherwise full scene. Ignores the slice
        /// preview plane. Falls back to all geometry if subject-only yields nothing.
        private static func worldBoundsForFraming(root: SCNNode, excludePattern: String?) -> (SIMD3<Float>, SIMD3<Float>, Float) {
            let trimmed = excludePattern?.trimmingCharacters(in: .whitespacesAndNewlines) ?? ""
            let pattern: String? = trimmed.isEmpty ? nil : trimmed
            let subject = unionWorldBounds(root: root, excludePattern: pattern, subjectOnly: true)
            if subject.3 {
                return (subject.0, subject.1, subject.2)
            }
            let full = unionWorldBounds(root: root, excludePattern: nil, subjectOnly: false)
            return (full.0, full.1, full.2)
        }

        private static func unionWorldBounds(
            root: SCNNode,
            excludePattern: String?,
            subjectOnly: Bool
        ) -> (SIMD3<Float>, SIMD3<Float>, Float, Bool) {
            var mn = SIMD3<Float>(repeating: Float.greatestFiniteMagnitude)
            var mx = SIMD3<Float>(repeating: -Float.greatestFiniteMagnitude)
            var any = false

            func mergeNode(_ node: SCNNode) {
                let (bmin, bmax) = node.boundingBox
                guard bmin.x <= bmax.x else { return }
                let corners: [SIMD3<Float>] = [
                    SIMD3(Float(bmin.x), Float(bmin.y), Float(bmin.z)), SIMD3(Float(bmax.x), Float(bmin.y), Float(bmin.z)),
                    SIMD3(Float(bmin.x), Float(bmax.y), Float(bmin.z)), SIMD3(Float(bmax.x), Float(bmax.y), Float(bmin.z)),
                    SIMD3(Float(bmin.x), Float(bmin.y), Float(bmax.z)), SIMD3(Float(bmax.x), Float(bmin.y), Float(bmax.z)),
                    SIMD3(Float(bmin.x), Float(bmax.y), Float(bmax.z)), SIMD3(Float(bmax.x), Float(bmax.y), Float(bmax.z)),
                ]
                for c in corners {
                    let w = node.simdConvertPosition(c, to: nil)
                    mn = simd_min(mn, w)
                    mx = simd_max(mx, w)
                    any = true
                }
            }

            if !subjectOnly {
                func visitAll(_ node: SCNNode) {
                    if node.name == slicePlaneNodeName || node.name == floorPlaneNodeName { return }
                    mergeNode(node)
                    for ch in node.childNodes { visitAll(ch) }
                }
                visitAll(root)
            } else {
                func visitSubject(_ node: SCNNode, excludedAncestor: Bool) {
                    if node.name == slicePlaneNodeName || node.name == floorPlaneNodeName { return }
                    let name = node.name ?? ""
                    let nameHit = excludePattern.map { name.localizedCaseInsensitiveContains($0) } ?? false
                    let excluded = excludedAncestor || nameHit
                    if excluded { return }
                    mergeNode(node)
                    for ch in node.childNodes {
                        visitSubject(ch, excludedAncestor: false)
                    }
                }
                visitSubject(root, excludedAncestor: false)
            }

            if !any {
                return (SIMD3(repeating: -0.5), SIMD3(repeating: 0.5), 1, false)
            }
            let d = simd_length(mx - mn)
            return (mn, mx, d, true)
        }
    }
}
