import AppKit
import Foundation
import Metal
import SceneKit
import simd

enum MeshArUcoOrbitError: Error, LocalizedError {
    case noMetal
    case emptyScene
    case noMarkerFound
    /// Marker was found in a render; `hitCount` of 4 corner rays hit mesh. `debugPNG` is the validation snapshot.
    case unprojectFailed(hitCount: Int, debugPNG: Data?)
    case inconsistentSquare(String)

    var errorDescription: String? {
        switch self {
        case .noMetal: return "Metal is required for virtual-camera ArUco detection."
        case .emptyScene: return "USDZ scene has no renderable geometry."
        case .noMarkerFound:
            return "No ArUco DICT_4X4_50 marker found in virtual-camera orbit. Ensure the marker texture is visible on the mesh and try again."
        case let .unprojectFailed(hitCount, _):
            return "Detected marker in a render, but could not raycast corners onto the mesh (\(hitCount)/4 hits). See validation snapshot."
        case let .inconsistentSquare(m): return m
        }
    }

    /// Validation camera snapshot when available (raycast failure path).
    var debugPNG: Data? {
        if case let .unprojectFailed(_, png) = self { return png }
        return nil
    }
}

struct MeshArUcoOrbitResult: Sendable, Equatable {
    var markerID: Int
    var meanSideScene: Float
    var sideCoefficientOfVariation: Float
    var worldCorners: [SIMD3<Float>] // TL→TR→BR→BL (approx)
    var confidence: Float
    var viewIndex: Int
    var note: String
    /// Test-mode: winning virtual-camera view with bright-green rays + 2D marker overlay (PNG).
    var debugSnapshotPNG: Data?

    /// Axis-aligned world box around the marker (with margin) for perimeter exclusion.
    var exclusionAABB: WorldAABBExclusion {
        var lo = SIMD3<Float>(repeating: .greatestFiniteMagnitude)
        var hi = SIMD3<Float>(repeating: -.greatestFiniteMagnitude)
        for c in worldCorners {
            lo = simd_min(lo, c)
            hi = simd_max(hi, c)
        }
        let diag = simd_length(hi - lo)
        let margin = max(diag * 0.15, meanSideScene * 0.2)
        return WorldAABBExclusion(
            min: lo - SIMD3(repeating: margin),
            max: hi + SIMD3(repeating: margin)
        )
    }
}

/// One frame from the virtual-camera orbit (for live UI feed).
struct MeshArUcoLiveFrameInfo: Sendable, Equatable {
    enum Phase: String, Sendable {
        case orbit
        case unproject
        case sideView
    }

    var phase: Phase
    var viewIndex: Int
    var viewCount: Int
    var detected: Bool
    var markerID: Int?
    var confidence: Float?
    /// Banner / status line for the UI.
    var status: String
}

/// Orbits a virtual camera around a USDZ, detects ArUco in textured renders, unprojects corners to mesh.
enum MeshArUcoOrbitDetector {
    private static let renderSize = CGSize(width: 1024, height: 1024)
    /// Max edge for ArUco detect (keeps morphology tractable across many orbit views).
    private static let detectMaxEdge: CGFloat = 640
    private static let orbitViewCount = 28
    private static let maxSideCV: Float = 0.12

    /// Optional live-feed callback. Invoked on the main queue with an annotated camera image.
    typealias LiveFrameHandler = (_ image: NSImage, _ info: MeshArUcoLiveFrameInfo) -> Void

    static func measure(
        usdzURL: URL,
        markerSideMillimeters: Double,
        expectedID: Int,
        onLiveFrame: LiveFrameHandler? = nil
    ) throws -> MeshArUcoOrbitResult {
        _ = NSApplication.shared
        let scene = try SCNScene(url: usdzURL, options: [
            .checkConsistency: true,
            .createNormalsIfAbsent: true,
        ])
        return try measure(
            scene: scene,
            markerSideMillimeters: markerSideMillimeters,
            expectedID: expectedID,
            onLiveFrame: onLiveFrame
        )
    }

    /// Orbit / detect / unproject against an already-loaded SceneKit scene (same space for render + hits).
    static func measure(
        scene: SCNScene,
        markerSideMillimeters: Double,
        expectedID: Int,
        onLiveFrame: LiveFrameHandler? = nil
    ) throws -> MeshArUcoOrbitResult {
        // SceneKit offscreen snapshots need an AppKit app context on macOS.
        _ = NSApplication.shared

        guard markerSideMillimeters > 0.5, markerSideMillimeters < 500 else {
            throw MeshArUcoOrbitError.inconsistentSquare("Marker side must be between 0.5 and 500 mm.")
        }
        guard let device = MTLCreateSystemDefaultDevice() else {
            throw MeshArUcoOrbitError.noMetal
        }

        prepareLighting(on: scene.rootNode)

        let (center, radius) = boundingSphere(of: scene.rootNode)
        guard radius > 1e-6 else { throw MeshArUcoOrbitError.emptyScene }

        let cameraNode = SCNNode()
        let camera = SCNCamera()
        camera.zNear = Double(max(radius * 0.01, 1e-4))
        camera.zFar = Double(radius * 20)
        camera.fieldOfView = 45
        cameraNode.camera = camera
        scene.rootNode.addChildNode(cameraNode)

        let renderer = SCNRenderer(device: device, options: nil)
        renderer.scene = scene
        renderer.pointOfView = cameraNode
        renderer.autoenablesDefaultLighting = false

        // Lazy SceneKit-space triangle soup only if hitTest misses (same transform graph as render).
        var scnTriangles: [(SIMD3<Float>, SIMD3<Float>, SIMD3<Float>)]?

        var best: (score: Float, id: Int, corners2D: [CGPoint], viewIndex: Int, eye: SIMD3<Float>)?
        let poses = orbitPoses(center: center, radius: radius, count: orbitViewCount)

        for (viewIndex, eye) in poses.enumerated() {
            cameraNode.simdPosition = eye
            cameraNode.look(at: SCNVector3(center.x, center.y, center.z), up: SCNVector3(0, 1, 0), localFront: SCNVector3(0, 0, -1))

            // Fast search renders (no MSAA); quality is enough for ArUco on baked textures.
            let nsImage = renderer.snapshot(atTime: 0, with: renderSize, antialiasingMode: .none)
            guard let cgImage = nsImage.cgImage(forProposedRect: nil, context: nil, hints: nil) else {
                emitLiveFrame(
                    onLiveFrame,
                    image: nsImage,
                    corners: nil,
                    info: MeshArUcoLiveFrameInfo(
                        phase: .orbit,
                        viewIndex: viewIndex,
                        viewCount: orbitViewCount,
                        detected: false,
                        markerID: nil,
                        confidence: nil,
                        status: "Orbit \(viewIndex + 1)/\(orbitViewCount) · no bitmap"
                    )
                )
                continue
            }
            guard let detectImage = downscaleForDetect(cgImage) else { continue }
            let sx = CGFloat(cgImage.width) / CGFloat(detectImage.width)
            let sy = CGFloat(cgImage.height) / CGFloat(detectImage.height)

            var corners: [CGPoint]?
            var markerID: Int?
            var confidence: Float?
            var status = "Orbit \(viewIndex + 1)/\(orbitViewCount) · searching…"

            if let markers = try? ArUcoDetectorBridge.detectMarkers(in: detectImage), !markers.isEmpty {
                let chosen: ArUcoDetectionResult
                if expectedID >= 0, let match = markers.first(where: { $0.markerID == expectedID }) {
                    chosen = match
                } else {
                    chosen = markers.max(by: { $0.confidence < $1.confidence })!
                }
                let scaled = chosen.corners.map(\.pointValue).map { CGPoint(x: $0.x * sx, y: $0.y * sy) }
                if scaled.count == 4 {
                    corners = scaled
                    markerID = Int(chosen.markerID)
                    confidence = Float(chosen.confidence)
                    let score = confidence!
                    status = String(
                        format: "Orbit %d/%d · DETECTED id %d · conf %.2f",
                        viewIndex + 1, orbitViewCount, markerID!, score
                    )
                    if best == nil || score > best!.score {
                        best = (score, markerID!, scaled, viewIndex, eye)
                        status += " · best so far"
                    }
                    if score >= 0.9 {
                        emitLiveFrame(
                            onLiveFrame,
                            image: nsImage,
                            corners: corners,
                            info: MeshArUcoLiveFrameInfo(
                                phase: .orbit,
                                viewIndex: viewIndex,
                                viewCount: orbitViewCount,
                                detected: true,
                                markerID: markerID,
                                confidence: confidence,
                                status: status + " · locking"
                            )
                        )
                        break
                    }
                }
            }

            emitLiveFrame(
                onLiveFrame,
                image: nsImage,
                corners: corners,
                info: MeshArUcoLiveFrameInfo(
                    phase: .orbit,
                    viewIndex: viewIndex,
                    viewCount: orbitViewCount,
                    detected: corners != nil,
                    markerID: markerID,
                    confidence: confidence,
                    status: status
                )
            )
        }

        guard let hit = best else { throw MeshArUcoOrbitError.noMarkerFound }

        // Re-pose camera to the winning view for correct unprojection rays.
        cameraNode.simdPosition = hit.eye
        cameraNode.look(at: SCNVector3(center.x, center.y, center.z), up: SCNVector3(0, 1, 0), localFront: SCNVector3(0, 0, -1))
        renderer.pointOfView = cameraNode
        // Force a snapshot so renderer projection matrices match this POV.
        let lockImage = renderer.snapshot(atTime: 0, with: renderSize, antialiasingMode: .none)

        var worldCorners: [SIMD3<Float>] = []
        worldCorners.reserveCapacity(4)
        var hitMask: [Bool] = []
        hitMask.reserveCapacity(4)
        let debugRoot = SCNNode()
        debugRoot.name = "arucoRayDebug"
        scene.rootNode.addChildNode(debugRoot)
        defer { debugRoot.removeFromParentNode() }

        // Miss rays stop near the subject (not far clip) so side-view beams stay readable.
        let missRayLen = max(simd_length(hit.eye - center) * 1.35, radius * 1.5)
        let ballR = max(radius * 0.008, 1e-4)
        let ignoreNodes: Set<ObjectIdentifier> = [ObjectIdentifier(cameraNode), ObjectIdentifier(debugRoot)]

        // 2D detection is correct (live feed). Screen→SceneKit axis mapping is ambiguous
        // (NSImage/CGImage vs SCNRenderer viewport). Try axis maps; keep the one whose
        // hits reproject closest to the detected corners.
        let unproject = bestCornerUnproject(
            renderer: renderer,
            detectorCorners: hit.corners2D,
            ignoreNodes: ignoreNodes,
            scnTriangles: &scnTriangles,
            root: scene.rootNode,
            missRayLen: missRayLen
        )
        worldCorners = unproject.worldCorners
        hitMask = unproject.hitMask

        emitLiveFrame(
            onLiveFrame,
            image: lockImage,
            corners: hit.corners2D,
            reprojectedCorners: unproject.reprojectedDetectorCorners,
            info: MeshArUcoLiveFrameInfo(
                phase: .unproject,
                viewIndex: hit.viewIndex,
                viewCount: orbitViewCount,
                detected: true,
                markerID: hit.id,
                confidence: hit.score,
                status: String(
                    format: "Unproject · map %@ · reproj %.1f px · hits %d/4",
                    unproject.mapName,
                    unproject.meanReprojError,
                    unproject.hitMask.filter(\.self).count
                )
            )
        )

        for (i, originEnd) in unproject.raySegments.enumerated() {
            let didHit = i < hitMask.count && hitMask[i]
            addDebugRay(from: originEnd.origin, to: originEnd.end, hit: didHit, parent: debugRoot, ballRadius: ballR)
        }

        // Mark the detecting camera eye, then orbit 45° so rays read as lines (not end-on fans).
        addDebugCameraMarker(at: hit.eye, parent: debugRoot, radius: ballR * 1.6)
        let sideEye = debugSideViewEye(from: hit.eye, lookingAt: center, yawDegrees: 45)
        cameraNode.simdPosition = sideEye
        cameraNode.look(
            at: SCNVector3(center.x, center.y, center.z),
            up: SCNVector3(0, 1, 0),
            localFront: SCNVector3(0, 0, -1)
        )
        renderer.pointOfView = cameraNode

        let rayImage = renderer.snapshot(atTime: 0, with: renderSize, antialiasingMode: .multisampling4X)
        let hits = hitMask.filter(\.self).count
        emitLiveFrame(
            onLiveFrame,
            image: rayImage,
            corners: nil,
            info: MeshArUcoLiveFrameInfo(
                phase: .sideView,
                viewIndex: hit.viewIndex,
                viewCount: orbitViewCount,
                detected: true,
                markerID: hit.id,
                confidence: hit.score,
                status: String(
                    format: "Side view +45° · map %@ · ray hits %d/4 · reproj %.1f px",
                    unproject.mapName, hits, unproject.meanReprojError
                )
            )
        )

        let debugPNG = annotatedDebugPNG(
            snapshot: rayImage,
            hitMask: hitMask,
            markerID: hit.id,
            viewIndex: hit.viewIndex
        )

        let hitCount = hitMask.filter(\.self).count
        guard hitCount == 4, worldCorners.count == 4 else {
            throw MeshArUcoOrbitError.unprojectFailed(hitCount: hitCount, debugPNG: debugPNG)
        }

        // Reject mappings that clearly don't match the 2D detection (e.g. mirrored axis).
        if unproject.meanReprojError > 40 {
            throw MeshArUcoOrbitError.inconsistentSquare(
                String(
                    format: "Corner unprojection does not match detection (reproj %.1f px, map %@). See live feed cyan vs green.",
                    unproject.meanReprojError,
                    unproject.mapName
                )
            )
        }

        let refined = refinePlanarSquare(corners: worldCorners)
        let sides = (0 ..< 4).map { i in
            simd_length(refined[(i + 1) % 4] - refined[i])
        }
        let meanSide = sides.reduce(0, +) / 4
        guard meanSide > 1e-8 else {
            throw MeshArUcoOrbitError.inconsistentSquare("Degenerate marker square after unprojection.")
        }
        let mean = meanSide
        let variance = sides.map { let d = $0 - mean; return d * d }.reduce(0, +) / 4
        let cv = sqrt(variance) / mean
        guard cv <= maxSideCV else {
            throw MeshArUcoOrbitError.inconsistentSquare(
                String(format: "Unprojected marker sides are inconsistent (CV %.1f%%). Try a clearer reconstruction.", cv * 100)
            )
        }

        let conf = max(0.2, min(0.99, hit.score * (1 - cv / maxSideCV)))
        let note = String(
            format: "Marker ID %d · side %.5f scene · CV %.1f%% · view %d/%d · map %@ · reproj %.1f px",
            hit.id, meanSide, cv * 100, hit.viewIndex + 1, orbitViewCount,
            unproject.mapName, unproject.meanReprojError
        )

        return MeshArUcoOrbitResult(
            markerID: hit.id,
            meanSideScene: meanSide,
            sideCoefficientOfVariation: cv,
            worldCorners: refined,
            confidence: conf,
            viewIndex: hit.viewIndex,
            note: note,
            debugSnapshotPNG: debugPNG
        )
    }

    // MARK: - Debug visualization (test version)

    private static func emitLiveFrame(
        _ handler: LiveFrameHandler?,
        image: NSImage,
        corners: [CGPoint]?,
        reprojectedCorners: [CGPoint]? = nil,
        info: MeshArUcoLiveFrameInfo
    ) {
        guard let handler else { return }
        let annotated = annotateLiveFeedImage(
            image,
            corners: corners,
            reprojectedCorners: reprojectedCorners,
            info: info
        )
        DispatchQueue.main.async {
            handler(annotated, info)
        }
    }

    private static func annotateLiveFeedImage(
        _ image: NSImage,
        corners: [CGPoint]?,
        reprojectedCorners: [CGPoint]? = nil,
        info: MeshArUcoLiveFrameInfo
    ) -> NSImage {
        guard let cg = image.cgImage(forProposedRect: nil, context: nil, hints: nil) else { return image }
        let w = cg.width
        let h = cg.height
        let colorSpace = CGColorSpaceCreateDeviceRGB()
        guard let ctx = CGContext(
            data: nil,
            width: w,
            height: h,
            bitsPerComponent: 8,
            bytesPerRow: 0,
            space: colorSpace,
            bitmapInfo: CGImageAlphaInfo.premultipliedLast.rawValue
        ) else { return image }

        // Top-left drawing space (matches ArUco detector coords).
        ctx.translateBy(x: 0, y: CGFloat(h))
        ctx.scaleBy(x: 1, y: -1)
        ctx.draw(cg, in: CGRect(x: 0, y: 0, width: w, height: h))

        if let corners, corners.count == 4 {
            let stroke = info.detected ? NSColor.systemGreen : NSColor.systemYellow
            ctx.setStrokeColor(stroke.cgColor)
            ctx.setLineWidth(4)
            ctx.beginPath()
            ctx.move(to: corners[0])
            for i in 1 ..< 4 { ctx.addLine(to: corners[i]) }
            ctx.closePath()
            ctx.strokePath()
            ctx.setFillColor(stroke.cgColor)
            for p in corners {
                ctx.fillEllipse(in: CGRect(x: p.x - 7, y: p.y - 7, width: 14, height: 14))
            }
        }

        // Cyan = 3D hits reprojected to the detect image (should overlap green if mapping is right).
        if let reproj = reprojectedCorners, reproj.count == 4 {
            ctx.setStrokeColor(NSColor.cyan.cgColor)
            ctx.setLineWidth(2)
            ctx.setLineDash(phase: 0, lengths: [6, 4])
            ctx.beginPath()
            ctx.move(to: reproj[0])
            for i in 1 ..< 4 { ctx.addLine(to: reproj[i]) }
            ctx.closePath()
            ctx.strokePath()
            ctx.setLineDash(phase: 0, lengths: [])
            ctx.setFillColor(NSColor.cyan.cgColor)
            for p in reproj {
                ctx.fillEllipse(in: CGRect(x: p.x - 5, y: p.y - 5, width: 10, height: 10))
            }
        }

        // Status banner
        let barH: CGFloat = 36
        ctx.setFillColor(NSColor.black.withAlphaComponent(0.62).cgColor)
        ctx.fill(CGRect(x: 0, y: 0, width: CGFloat(w), height: barH))
        // Progress strip under banner
        let progress = CGFloat(info.viewIndex + 1) / CGFloat(max(info.viewCount, 1))
        ctx.setFillColor((info.detected ? NSColor.systemGreen : NSColor.systemCyan).cgColor)
        ctx.fill(CGRect(x: 0, y: barH, width: CGFloat(w) * progress, height: 4))

        NSGraphicsContext.saveGraphicsState()
        NSGraphicsContext.current = NSGraphicsContext(cgContext: ctx, flipped: true)
        let attrs: [NSAttributedString.Key: Any] = [
            .font: NSFont.monospacedSystemFont(ofSize: 13, weight: .semibold),
            .foregroundColor: info.detected ? NSColor.systemGreen : NSColor.white,
        ]
        (info.status as NSString).draw(at: CGPoint(x: 10, y: 10), withAttributes: attrs)
        NSGraphicsContext.restoreGraphicsState()

        guard let out = ctx.makeImage() else { return image }
        return NSImage(cgImage: out, size: NSSize(width: w, height: h))
    }

    /// Orbit the detecting eye 45° around the look-at target (prefer yaw about +Y).
    private static func debugSideViewEye(
        from eye: SIMD3<Float>,
        lookingAt center: SIMD3<Float>,
        yawDegrees: Float
    ) -> SIMD3<Float> {
        let offset = eye - center
        let up = SIMD3<Float>(0, 1, 0)
        let axis: SIMD3<Float>
        if abs(simd_dot(simd_normalize(offset), up)) > 0.92 {
            // Nearly top-down: yaw about +X instead so the move is visible.
            axis = SIMD3(1, 0, 0)
        } else {
            axis = up
        }
        let q = simd_quatf(angle: yawDegrees * .pi / 180, axis: axis)
        return center + q.act(offset)
    }

    private static func addDebugCameraMarker(at eye: SIMD3<Float>, parent: SCNNode, radius: Float) {
        let ball = SCNSphere(radius: CGFloat(radius))
        ball.firstMaterial?.diffuse.contents = NSColor.cyan
        ball.firstMaterial?.emission.contents = NSColor.cyan
        ball.firstMaterial?.lightingModel = .constant
        let node = SCNNode(geometry: ball)
        node.simdPosition = eye
        node.renderingOrder = 510
        parent.addChildNode(node)
    }

    private static func addDebugRay(
        from start: SIMD3<Float>,
        to end: SIMD3<Float>,
        hit: Bool,
        parent: SCNNode,
        ballRadius: Float
    ) {
        let color = hit ? NSColor.systemGreen : NSColor.systemOrange
        let mid = (start + end) * 0.5
        let delta = end - start
        let len = simd_length(delta)
        // Thin tube (side view) — thick enough to see, not end-on screen fill.
        if len > 1e-6 {
            let cyl = SCNCylinder(radius: CGFloat(ballRadius * 0.12), height: CGFloat(len))
            cyl.firstMaterial?.diffuse.contents = color
            cyl.firstMaterial?.emission.contents = color
            cyl.firstMaterial?.lightingModel = .constant
            let beam = SCNNode(geometry: cyl)
            beam.simdPosition = mid
            beam.simdLook(at: end, up: SIMD3(0, 1, 0), localFront: SIMD3(0, 1, 0))
            beam.renderingOrder = 501
            parent.addChildNode(beam)
        }

        let ball = SCNSphere(radius: CGFloat(ballRadius))
        ball.firstMaterial?.diffuse.contents = color
        ball.firstMaterial?.emission.contents = color
        ball.firstMaterial?.lightingModel = .constant
        let ballNode = SCNNode(geometry: ball)
        ballNode.simdPosition = end
        ballNode.renderingOrder = 502
        parent.addChildNode(ballNode)
    }

    private static func annotatedDebugPNG(
        snapshot: NSImage,
        hitMask: [Bool],
        markerID: Int,
        viewIndex: Int
    ) -> Data? {
        guard let cg = snapshot.cgImage(forProposedRect: nil, context: nil, hints: nil) else {
            return snapshot.tiffRepresentation
        }
        let w = cg.width
        let h = cg.height
        let colorSpace = CGColorSpaceCreateDeviceRGB()
        guard let ctx = CGContext(
            data: nil,
            width: w,
            height: h,
            bitsPerComponent: 8,
            bytesPerRow: 0,
            space: colorSpace,
            bitmapInfo: CGImageAlphaInfo.premultipliedLast.rawValue
        ) else { return nil }

        ctx.translateBy(x: 0, y: CGFloat(h))
        ctx.scaleBy(x: 1, y: -1)
        ctx.draw(cg, in: CGRect(x: 0, y: 0, width: w, height: h))

        let hits = hitMask.filter(\.self).count
        let label = "TEST  marker \(markerID)  view \(viewIndex + 1)  hits \(hits)/4  +45° side view  cyan=detect cam  green=hit  orange=miss"
        ctx.setFillColor(NSColor.black.withAlphaComponent(0.6).cgColor)
        ctx.fill(CGRect(x: 0, y: 0, width: w, height: 32))
        NSGraphicsContext.saveGraphicsState()
        let nsCtx = NSGraphicsContext(cgContext: ctx, flipped: true)
        NSGraphicsContext.current = nsCtx
        let attrs: [NSAttributedString.Key: Any] = [
            .font: NSFont.monospacedSystemFont(ofSize: 12, weight: .semibold),
            .foregroundColor: NSColor.white,
        ]
        (label as NSString).draw(at: CGPoint(x: 10, y: 8), withAttributes: attrs)
        NSGraphicsContext.restoreGraphicsState()

        guard let out = ctx.makeImage() else { return nil }
        let rep = NSBitmapImageRep(cgImage: out)
        return rep.representation(using: .png, properties: [:])
    }

    // MARK: - Orbit / lighting

    private static func downscaleForDetect(_ image: CGImage) -> CGImage? {
        let w = CGFloat(image.width)
        let h = CGFloat(image.height)
        let longEdge = max(w, h)
        guard longEdge > detectMaxEdge else { return image }
        let scale = detectMaxEdge / longEdge
        let tw = max(1, Int((w * scale).rounded()))
        let th = max(1, Int((h * scale).rounded()))
        let colorSpace = image.colorSpace ?? CGColorSpaceCreateDeviceRGB()
        guard let ctx = CGContext(
            data: nil,
            width: tw,
            height: th,
            bitsPerComponent: 8,
            bytesPerRow: 0,
            space: colorSpace,
            bitmapInfo: CGImageAlphaInfo.premultipliedLast.rawValue
        ) else { return image }
        ctx.interpolationQuality = .medium
        ctx.draw(image, in: CGRect(x: 0, y: 0, width: tw, height: th))
        return ctx.makeImage()
    }

    private static func prepareLighting(on root: SCNNode) {
        let ambient = SCNNode()
        ambient.light = {
            let l = SCNLight()
            l.type = .ambient
            l.intensity = 600
            l.color = NSColor.white
            return l
        }()
        root.addChildNode(ambient)

        let key = SCNNode()
        key.light = {
            let l = SCNLight()
            l.type = .directional
            l.intensity = 700
            l.color = NSColor.white
            l.castsShadow = false
            return l
        }()
        key.eulerAngles = SCNVector3(-Float.pi / 4, Float.pi / 5, 0)
        root.addChildNode(key)
    }

    private static func boundingSphere(of root: SCNNode) -> (center: SIMD3<Float>, radius: Float) {
        let (minV, maxV) = root.boundingBox
        let mn = SIMD3<Float>(Float(minV.x), Float(minV.y), Float(minV.z))
        let mx = SIMD3<Float>(Float(maxV.x), Float(maxV.y), Float(maxV.z))
        let center = (mn + mx) * 0.5
        let radius = max(simd_length(mx - mn) * 0.5, 1e-4)
        return (center, radius)
    }

    /// Hemispherical-ish orbit biased toward +Y (top-down views for flange markers).
    private static func orbitPoses(center: SIMD3<Float>, radius: Float, count: Int) -> [SIMD3<Float>] {
        let dist = radius * 2.4
        var eyes: [SIMD3<Float>] = []
        // Top-down first.
        eyes.append(center + SIMD3<Float>(0, dist, 0.001))
        let rings = 4
        var remaining = max(0, count - 1)
        for ring in 0 ..< rings {
            let elev = Float.pi / 2 - Float(ring + 1) * (Float.pi / 2.4) / Float(rings) // near top → equator
            let nOnRing = max(4, remaining / (rings - ring))
            let take = min(nOnRing, remaining)
            remaining -= take
            for i in 0 ..< take {
                let az = Float(i) * (2 * Float.pi / Float(take))
                let x = dist * cos(elev) * cos(az)
                let y = dist * sin(elev)
                let z = dist * cos(elev) * sin(az)
                eyes.append(center + SIMD3(x, y, z))
            }
        }
        while eyes.count < count {
            eyes.append(center + SIMD3(0, dist, dist * 0.2))
        }
        return eyes
    }

    // MARK: - Mesh rays (SceneKit space only)

    private enum ScreenAxisMap: String, CaseIterable {
        case identity
        case flipY
        case flipX
        case flipXY

        func apply(_ p: CGPoint, size: CGSize) -> CGPoint {
            switch self {
            case .identity: return p
            case .flipY: return CGPoint(x: p.x, y: size.height - p.y)
            case .flipX: return CGPoint(x: size.width - p.x, y: p.y)
            case .flipXY: return CGPoint(x: size.width - p.x, y: size.height - p.y)
            }
        }
    }

    private struct CornerUnprojectResult {
        var mapName: String
        var worldCorners: [SIMD3<Float>]
        var hitMask: [Bool]
        var meanReprojError: Float
        var reprojectedDetectorCorners: [CGPoint]
        var raySegments: [(origin: SIMD3<Float>, end: SIMD3<Float>)]
    }

    /// Try screen-axis maps; pick the one with most hits and lowest reprojection error vs detector corners.
    private static func bestCornerUnproject(
        renderer: SCNRenderer,
        detectorCorners: [CGPoint],
        ignoreNodes: Set<ObjectIdentifier>,
        scnTriangles: inout [(SIMD3<Float>, SIMD3<Float>, SIMD3<Float>)]?,
        root: SCNNode,
        missRayLen: Float
    ) -> CornerUnprojectResult {
        let size = renderSize
        var best: CornerUnprojectResult?

        for map in ScreenAxisMap.allCases {
            var worlds: [SIMD3<Float>] = []
            var mask: [Bool] = []
            var reproj: [CGPoint] = []
            var segments: [(origin: SIMD3<Float>, end: SIMD3<Float>)] = []
            var errSum: Float = 0
            var errCount = 0

            for pDetect in detectorCorners {
                let screen = map.apply(pDetect, size: size)
                let near = renderer.unprojectPoint(SCNVector3(screen.x, screen.y, 0))
                let far = renderer.unprojectPoint(SCNVector3(screen.x, screen.y, 1))
                let origin = SIMD3<Float>(Float(near.x), Float(near.y), Float(near.z))
                let dest = SIMD3<Float>(Float(far.x), Float(far.y), Float(far.z))
                let dir = simd_normalize(dest - origin)

                var hitPt = sceneKitHitWorldPoint(
                    renderer: renderer,
                    screenPoint: screen,
                    ignoreNodes: ignoreNodes
                )
                if hitPt == nil {
                    if scnTriangles == nil {
                        scnTriangles = collectSceneKitTriangles(from: root, ignoring: ignoreNodes)
                    }
                    if let tris = scnTriangles, !tris.isEmpty {
                        hitPt = raycast(origin: origin, direction: dir, triangles: tris)
                    }
                }

                let end = hitPt ?? (origin + dir * missRayLen)
                segments.append((origin, end))
                let didHit = hitPt != nil
                mask.append(didHit)

                if let hitPt {
                    worlds.append(hitPt)
                    let proj = renderer.projectPoint(SCNVector3(hitPt.x, hitPt.y, hitPt.z))
                    // projectPoint is in the same viewport space as unproject/hitTest.
                    let projScreen = CGPoint(x: CGFloat(proj.x), y: CGFloat(proj.y))
                    let backDetect = map.apply(projScreen, size: size) // involution → detector space
                    reproj.append(backDetect)
                    let dx = Float(backDetect.x - pDetect.x)
                    let dy = Float(backDetect.y - pDetect.y)
                    errSum += sqrt(dx * dx + dy * dy)
                    errCount += 1
                } else {
                    reproj.append(pDetect)
                }
            }

            let hitCount = mask.filter(\.self).count
            let meanErr = errCount > 0 ? errSum / Float(errCount) : Float.greatestFiniteMagnitude
            // Prefer more hits; then lower reprojection error.
            let score = Float(hitCount) * 1_000_000 - meanErr
            let candidate = CornerUnprojectResult(
                mapName: map.rawValue,
                worldCorners: worlds,
                hitMask: mask,
                meanReprojError: meanErr,
                reprojectedDetectorCorners: reproj,
                raySegments: segments
            )
            if best == nil {
                best = candidate
            } else {
                let bestHit = best!.hitMask.filter(\.self).count
                let bestScore = Float(bestHit) * 1_000_000 - best!.meanReprojError
                if score > bestScore {
                    best = candidate
                }
            }
        }

        return best!
    }

    private static func sceneKitHitWorldPoint(
        renderer: SCNRenderer,
        screenPoint: CGPoint,
        ignoreNodes: Set<ObjectIdentifier>
    ) -> SIMD3<Float>? {
        let options: [SCNHitTestOption: Any] = [
            .searchMode: SCNHitTestSearchMode.closest.rawValue,
            .boundingBoxOnly: false,
            .ignoreHiddenNodes: true,
        ]
        let results = renderer.hitTest(screenPoint, options: options)
        for result in results {
            var node: SCNNode? = result.node
            var skip = false
            while let n = node {
                if ignoreNodes.contains(ObjectIdentifier(n)) { skip = true; break }
                if n.name == "arucoRayDebug" { skip = true; break }
                node = n.parent
            }
            if skip { continue }
            let w = result.worldCoordinates
            return SIMD3(Float(w.x), Float(w.y), Float(w.z))
        }
        return nil
    }

    private static func collectSceneKitTriangles(
        from root: SCNNode,
        ignoring ignoreNodes: Set<ObjectIdentifier>
    ) -> [(SIMD3<Float>, SIMD3<Float>, SIMD3<Float>)] {
        var triangles: [(SIMD3<Float>, SIMD3<Float>, SIMD3<Float>)] = []
        root.enumerateChildNodes { node, _ in
            if ignoreNodes.contains(ObjectIdentifier(node)) { return }
            if node.name == "arucoRayDebug" { return }
            guard let geom = node.geometry else { return }
            let sources = geom.sources(for: .vertex)
            guard let src = sources.first else { return }
            let stride = src.dataStride
            let offset = src.dataOffset
            let comps = src.componentsPerVector
            guard comps >= 3 else { return }
            let data = src.data
            func vertex(_ i: Int) -> SIMD3<Float> {
                let o = offset + i * stride
                var x: Float = 0, y: Float = 0, z: Float = 0
                data.withUnsafeBytes { raw in
                    let base = raw.baseAddress!.advanced(by: o).assumingMemoryBound(to: Float.self)
                    x = base[0]; y = base[1]; z = base[2]
                }
                return node.simdConvertPosition(SIMD3(x, y, z), to: nil)
            }
            for element in geom.elements {
                guard element.primitiveType == .triangles else { continue }
                let idxCount = element.primitiveCount * 3
                let bpe = element.bytesPerIndex
                let idxData = element.data
                func readIndex(_ i: Int) -> Int {
                    let o = i * bpe
                    if bpe == 2 {
                        return Int(idxData.withUnsafeBytes { $0.load(fromByteOffset: o, as: UInt16.self) })
                    }
                    return Int(idxData.withUnsafeBytes { $0.load(fromByteOffset: o, as: UInt32.self) })
                }
                var i = 0
                while i + 2 < idxCount {
                    triangles.append((vertex(readIndex(i)), vertex(readIndex(i + 1)), vertex(readIndex(i + 2))))
                    i += 3
                }
            }
        }
        return triangles
    }

    private static func raycast(
        origin: SIMD3<Float>,
        direction: SIMD3<Float>,
        triangles: [(SIMD3<Float>, SIMD3<Float>, SIMD3<Float>)]
    ) -> SIMD3<Float>? {
        var bestT: Float = .greatestFiniteMagnitude
        var best: SIMD3<Float>?
        for tri in triangles {
            if let t = rayTriangle(origin: origin, dir: direction, v0: tri.0, v1: tri.1, v2: tri.2), t > 1e-5, t < bestT {
                bestT = t
                best = origin + direction * t
            }
        }
        return best
    }

    /// Möller–Trumbore; returns t along ray.
    private static func rayTriangle(
        origin: SIMD3<Float>,
        dir: SIMD3<Float>,
        v0: SIMD3<Float>,
        v1: SIMD3<Float>,
        v2: SIMD3<Float>
    ) -> Float? {
        let eps: Float = 1e-7
        let e1 = v1 - v0
        let e2 = v2 - v0
        let pvec = simd_cross(dir, e2)
        let det = simd_dot(e1, pvec)
        if abs(det) < eps { return nil }
        let invDet = 1 / det
        let tvec = origin - v0
        let u = simd_dot(tvec, pvec) * invDet
        if u < 0 || u > 1 { return nil }
        let qvec = simd_cross(tvec, e1)
        let v = simd_dot(dir, qvec) * invDet
        if v < 0 || u + v > 1 { return nil }
        let t = simd_dot(e2, qvec) * invDet
        return t > eps ? t : nil
    }

    /// Project corners onto best-fit plane and lightly regularize toward a square.
    private static func refinePlanarSquare(corners: [SIMD3<Float>]) -> [SIMD3<Float>] {
        guard corners.count == 4 else { return corners }
        let c = corners.reduce(SIMD3<Float>.zero, +) / 4
        var n = simd_normalize(simd_cross(corners[1] - corners[0], corners[3] - corners[0]))
        if !n.x.isFinite { n = SIMD3(0, 1, 0) }
        let projected = corners.map { p -> SIMD3<Float> in
            let d = simd_dot(p - c, n)
            return p - n * d
        }
        return projected
    }
}
