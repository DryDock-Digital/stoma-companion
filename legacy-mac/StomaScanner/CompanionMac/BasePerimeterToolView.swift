import AppKit
import simd
import SwiftUI
import UniformTypeIdentifiers

private enum ScaleCalibrationSource: String, CaseIterable, Identifiable {
    case arucoInMesh
    case photoWithArUco
    case photoWithCoin
    case namedUSDZMesh

    var id: String { rawValue }

    var title: String {
        switch self {
        case .arucoInMesh: return "ArUco in USDZ (virtual camera)"
        case .photoWithArUco: return "Top-down photo + ArUco (multi-metric)"
        case .photoWithCoin: return "Top-down photo + US coin (manual fallback)"
        case .namedUSDZMesh: return "Named object in USDZ"
        }
    }
}

/// Perimeter tool file picks use `NSOpenPanel` (see `presentOpenPanel` in BasePerimeterToolView).
struct BasePerimeterToolView: View {
    let builtInUSDZURL: URL?
    /// Optional default still (e.g. `calibration_top.jpg` from video extract) for photo calibration.
    var defaultCalibrationImageURL: URL? = nil

    @State private var pickedUSDZ: URL?

    @State private var upAxis: BaseSliceUpAxis = .manualTilt
    @State private var manualSliceTilt = ManualSliceAxisTuning.default
    @State private var isSeedingManualTilt = false
    @State private var sliceOffsetPercent: Double = 15
    @State private var useMillimeters = true

    @State private var calibrationSource: ScaleCalibrationSource = .arucoInMesh
    @State private var referenceKind: ScaleReferenceKind = .usNickelDiameter
    @State private var customRealMmText = "100"
    @State private var referenceNameSubstring = "Reference"
    @State private var measuredMeshRefLength: Float?
    @State private var manualMeshRefLengthText = ""
    @State private var measureNote = ""
    @State private var useReferenceScale = true
    @State private var excludeReferenceFromOutline = true
    @State private var manualScaleOnlyText = "1.0"

    @State private var calibrationImageURL: URL?
    @State private var calibrationImage: NSImage?
    @State private var calibrationImportNote = ""
    @State private var isImportingCalibrationFrame = false
    @State private var photoCalibrationLines = PhotoCalibrationLines()
    @State private var photoCalibrationActiveLine: PhotoCalibrationLineKind = .coin
    @State private var photoCoinKind: PhotoCalibrationCoinKind = .usNickel

    @State private var arucoMarkerSideMMText = "27"
    @State private var arucoExpectedIDText = "-1"
    @State private var isAutoDetecting = false
    @State private var autoDetectNote = ""
    @State private var arucoOverlayCorners: [CGPoint] = []
    @State private var arucoOverlayMarkerID: Int? = nil
    @State private var photoOverlayContour: [CGPoint] = []
    @State private var photoScaleEstimate: PhotoScaleEstimateResult?
    @State private var allowInconsistentPhotoScale = false
    @State private var meshArUcoResult: MeshArUcoOrbitResult?
    @State private var meshArUcoNote = ""
    /// Test-mode validation snapshot (winning virtual camera + green rays).
    @State private var meshArUcoDebugImage: NSImage?
    /// Live virtual-camera orbit feed during measure.
    @State private var meshArUcoLiveImage: NSImage?
    @State private var meshArUcoLiveStatus = ""
    @State private var meshArUcoLiveDetected = false

    @State private var isExtracting = false
    @State private var isMeasuring = false
    @State private var extractionError: String?
    @State private var result: BasePerimeterResult?
    @State private var exportGcode = ""
    @State private var exportIdealFitGcode = ""
    @State private var autoExportURL: URL?
    @State private var autoExportIdealFitURL: URL?
    @State private var autoExportFailed = false

    @State private var realObjectLine1MMText = ""
    @State private var realObjectLine2MMText = ""
    @State private var outlineValidationLines = OutlineValidationLines()
    @State private var activeOutlineValidationLine: OutlineValidationLineKind = .line1
    @State private var validationExportNote = ""
    @State private var polarPlotterRPM: Double = Double(PolarPathExport.defaultRPM)
    @State private var autoExportPolarURL: URL?
    @State private var polarExportNote = ""

    private var activeUSDZ: URL? { pickedUSDZ ?? builtInUSDZURL }

    var body: some View {
        applyOnChangesPart2(applyOnChangesPart1(rootContent))
    }

    @ViewBuilder
    private func applyOnChangesPart1<V: View>(_ v: V) -> some View {
        v
            .onAppear {
                adoptDefaultCalibrationImageIfNeeded()
            }
            .onChange(of: defaultCalibrationImageURL) { _, _ in
                adoptDefaultCalibrationImageIfNeeded()
            }
            .onChange(of: useMillimeters) { _, _ in rebuildExports() }
            .onChange(of: useReferenceScale) { _, _ in rebuildExports() }
            .onChange(of: manualScaleOnlyText) { _, _ in rebuildExports() }
            .onChange(of: builtInUSDZURL?.path) { oldPath, newPath in
                guard oldPath != newPath else { return }
                pickedUSDZ = nil
                resetPerimeterStateForNewMesh()
                resetCalibrationImageForNewInput()
                adoptDefaultCalibrationImageIfNeeded()
            }
    }

    @ViewBuilder
    private func applyOnChangesPart2<V: View>(_ v: V) -> some View {
        v
            .onChange(of: defaultCalibrationImageURL?.path) { oldPath, newPath in
                guard oldPath != newPath else { return }
                resetCalibrationImageForNewInput()
                adoptDefaultCalibrationImageIfNeeded()
            }
            .onChange(of: referenceKind) { _, _ in rebuildExports() }
            .onChange(of: customRealMmText) { _, _ in rebuildExports() }
            .onChange(of: manualMeshRefLengthText) { _, _ in rebuildExports() }
            .onChange(of: measuredMeshRefLength) { _, _ in rebuildExports() }
            .onChange(of: calibrationSource) { _, newVal in
                handleCalibrationSourceChange(newVal)
            }
            .onChange(of: photoCalibrationLines) { _, _ in rebuildExports() }
            .onChange(of: photoCoinKind) { _, newVal in
                referenceKind = newVal.scaleReferenceKind
                rebuildExports()
            }
            .onChange(of: photoScaleEstimate?.scaleSceneToMillimeters) { _, _ in rebuildExports() }
            .onChange(of: allowInconsistentPhotoScale) { _, _ in rebuildExports() }
            .onChange(of: meshArUcoResult?.meanSideScene) { _, _ in rebuildExports() }
            .onChange(of: arucoMarkerSideMMText) { _, _ in rebuildExports() }
            .onChange(of: result) { _, _ in rebuildExports() }
    }

    private func handleCalibrationSourceChange(_ newVal: ScaleCalibrationSource) {
        if newVal == .photoWithCoin {
            referenceKind = photoCoinKind.scaleReferenceKind
            adoptDefaultCalibrationImageIfNeeded()
        } else if newVal == .photoWithArUco {
            adoptDefaultCalibrationImageIfNeeded()
        }
        rebuildExports()
    }

    private var meshArUcoExclusionAABB: WorldAABBExclusion? {
        meshArUcoResult?.exclusionAABB
    }

    private var meshArUcoWorldCorners: [SIMD3<Float>] {
        meshArUcoResult?.worldCorners ?? []
    }

    private func resetCalibrationImageForNewInput() {
        calibrationImageURL = nil
        calibrationImage = nil
        calibrationImportNote = ""
        photoCalibrationLines = PhotoCalibrationLines()
        arucoOverlayCorners = []
        arucoOverlayMarkerID = nil
        photoOverlayContour = []
        photoScaleEstimate = nil
        autoDetectNote = ""
        allowInconsistentPhotoScale = false
    }

    private func presentOpenPanel(
        allowedContentTypes: [UTType],
        message: String,
        handler: @escaping (URL) -> Void
    ) {
        guard let window = NSApp.keyWindow else {
            calibrationImportNote = "Could not present the file panel (no key window)."
            return
        }
        let panel = NSOpenPanel()
        panel.canChooseFiles = true
        panel.canChooseDirectories = false
        panel.allowsMultipleSelection = false
        panel.allowedContentTypes = allowedContentTypes
        panel.message = message
        panel.beginSheetModal(for: window) { response in
            guard response == .OK, let url = panel.url else { return }
            handler(url)
        }
    }

    private func chooseOtherUSDZ() {
        presentOpenPanel(
            allowedContentTypes: [.usdz],
            message: "Choose a USDZ mesh for perimeter extraction."
        ) { url in
            importPickedUSDZ(from: url)
        }
    }

    private func importPickedUSDZ(from url: URL) {
        let accessing = url.startAccessingSecurityScopedResource()
        guard accessing else {
            extractionError = "Could not access the selected USDZ."
            return
        }
        defer { url.stopAccessingSecurityScopedResource() }
        do {
            let temp = FileManager.default.temporaryDirectory
                .appendingPathComponent("perimeter-pick-\(UUID().uuidString).usdz", isDirectory: false)
            if FileManager.default.fileExists(atPath: temp.path) {
                try FileManager.default.removeItem(at: temp)
            }
            try FileManager.default.copyItem(at: url, to: temp)
            pickedUSDZ = temp
            extractionError = nil
            resetPerimeterStateForNewMesh()
        } catch {
            extractionError = error.localizedDescription
        }
    }

    private func chooseCalibrationStill() {
        presentOpenPanel(
            allowedContentTypes: [.jpeg, .png, .heic, .heif, .tiff, .image],
            message: "Choose a top-down still (JPEG, PNG, or HEIC) with a US quarter or nickel in frame."
        ) { url in
            importCalibrationStill(from: url, securityScoped: true)
        }
    }

    private func chooseCalibrationVideoFirstFrame() {
        presentOpenPanel(
            allowedContentTypes: [.movie, .mpeg4Movie, .quickTimeMovie, .video],
            message: "Choose the source video — only the first frame is extracted for calibration (no photogrammetry)."
        ) { url in
            importCalibrationFirstFrame(fromVideo: url)
        }
    }

    private func importCalibrationStill(from pickedURL: URL, securityScoped: Bool) {
        var access = false
        if securityScoped {
            access = pickedURL.startAccessingSecurityScopedResource()
            guard access else {
                calibrationImportNote = "Could not access the selected image."
                calibrationImage = nil
                return
            }
        }
        defer {
            if access {
                pickedURL.stopAccessingSecurityScopedResource()
            }
        }

        let ext = pickedURL.pathExtension.isEmpty ? "jpg" : pickedURL.pathExtension
        let dest = FileManager.default.temporaryDirectory
            .appendingPathComponent("calibration-still-\(UUID().uuidString).\(ext)", isDirectory: false)
        do {
            if FileManager.default.fileExists(atPath: dest.path) {
                try FileManager.default.removeItem(at: dest)
            }
            try FileManager.default.copyItem(at: pickedURL, to: dest)
            calibrationImageURL = dest
            photoCalibrationLines = PhotoCalibrationLines()
            calibrationImage = NSImage(contentsOf: dest)
            if calibrationImage == nil {
                calibrationImportNote = "Could not decode that image. Try JPEG or PNG."
            } else {
                calibrationImportNote = ""
            }
        } catch {
            calibrationImportNote = error.localizedDescription
            calibrationImage = nil
        }
    }

    private func importCalibrationFirstFrame(fromVideo videoURL: URL) {
        isImportingCalibrationFrame = true
        calibrationImportNote = ""
        Task {
            do {
                let accessing = videoURL.startAccessingSecurityScopedResource()
                guard accessing else {
                    throw NSError(
                        domain: "BasePerimeter",
                        code: 10,
                        userInfo: [NSLocalizedDescriptionKey: "Could not access the selected video."]
                    )
                }
                defer { videoURL.stopAccessingSecurityScopedResource() }

                let dest = FileManager.default.temporaryDirectory
                    .appendingPathComponent("calibration-top-\(UUID().uuidString).jpg", isDirectory: false)
                try await VideoFrameExporter.exportFirstFrameJPEG(videoURL: videoURL, outputURL: dest)

                await MainActor.run {
                    calibrationImageURL = dest
                    photoCalibrationLines = PhotoCalibrationLines()
                    calibrationImage = NSImage(contentsOf: dest)
                    if calibrationImage == nil {
                        calibrationImportNote = "Extracted a frame but could not display it."
                    } else {
                        calibrationImportNote = "Loaded first video frame."
                    }
                    isImportingCalibrationFrame = false
                }
            } catch {
                await MainActor.run {
                    calibrationImportNote = error.localizedDescription
                    isImportingCalibrationFrame = false
                }
            }
        }
    }

    private var rootContent: some View {
        Group {
            referenceSection
            perimeterSection
            if let r = result {
                resultGcodePreviewSection(result: r)
                validationSection(result: r)
                exportSection
            }
        }
    }

    private func scaledGcodePlaneXYForPreview(result: BasePerimeterResult) -> [(Float, Float)] {
        BasePerimeterExport.scaledGcodePlaneXY(
            result: result,
            unitsMillimeters: useMillimeters,
            userScale: computedUserScale()
        )
    }

    private func clearanceDistanceForIdealFit() -> Float {
        useMillimeters ? IdealFitOutlineGenerator.defaultClearanceMM : 0.003
    }

    private func resetPerimeterStateForNewMesh() {
        result = nil
        extractionError = nil
        exportGcode = ""
        exportIdealFitGcode = ""
        autoExportURL = nil
        autoExportIdealFitURL = nil
        autoExportFailed = false
        measuredMeshRefLength = nil
        measureNote = ""
        meshArUcoResult = nil
        meshArUcoNote = ""
        meshArUcoDebugImage = nil
        meshArUcoLiveImage = nil
        meshArUcoLiveStatus = ""
        meshArUcoLiveDetected = false
        validationExportNote = ""
        outlineValidationLines = OutlineValidationLines()
        activeOutlineValidationLine = .line1
        isExtracting = false
        isMeasuring = false
    }

    private func idealFitForResult(_ result: BasePerimeterResult) -> IdealFitResult? {
        let primary = scaledGcodePlaneXYForPreview(result: result)
        return IdealFitOutlineGenerator.generate(
            primary: primary,
            clearanceMM: clearanceDistanceForIdealFit(),
            toleranceMM: useMillimeters ? IdealFitOutlineGenerator.defaultToleranceMM : 0.001
        )
    }

    @ViewBuilder
    private func resultGcodePreviewSection(result: BasePerimeterResult) -> some View {
        let primary = scaledGcodePlaneXYForPreview(result: result)
        let ideal = idealFitForResult(result)
        Section("G-code XY path (preview)") {
            BasePerimeterVisualizerView(
                planeXY: primary,
                idealFitXY: ideal?.points,
                clearanceStats: ideal?.clearanceStats,
                unitsMillimeters: useMillimeters,
                validationLines: $outlineValidationLines,
                activeValidationLine: $activeOutlineValidationLine
            )
        }
    }

    @ViewBuilder
    private func validationSection(result: BasePerimeterResult) -> some View {
        let primary = scaledGcodePlaneXYForPreview(result: result)
        let ideal = idealFitForResult(result)
        let sw01 = ValidationExport.sw01Metrics(
            validationLines: outlineValidationLines,
            realLine1MM: parseOptionalDouble(realObjectLine1MMText),
            realLine2MM: parseOptionalDouble(realObjectLine2MMText),
            unitsMillimeters: useMillimeters
        )
        let unit = useMillimeters ? "mm" : "m"

        Section {
            Group {
                Text("SW-01 — size accuracy (custom validation lines)")
                    .font(.subheadline.weight(.semibold))
                Text("Draw Line 1 and Line 2 on the outline preview above, then enter the same spans measured on the physical object.")
                    .font(.caption2)
                    .foregroundStyle(.secondary)
                LabeledContent("Real object Line 1 (mm, calipers)") {
                    TextField("line 1", text: $realObjectLine1MMText)
                        .frame(maxWidth: 100)
                }
                LabeledContent("Real object Line 2 (mm, calipers)") {
                    TextField("line 2", text: $realObjectLine2MMText)
                        .frame(maxWidth: 100)
                }
                LabeledContent("Measured Line 1 (outline)") {
                    Text(sw01.measuredLine1MM.map { String(format: "%.2f mm", $0) } ?? "—")
                        .font(.caption.monospacedDigit())
                }
                LabeledContent("Measured Line 2 (outline)") {
                    Text(sw01.measuredLine2MM.map { String(format: "%.2f mm", $0) } ?? "—")
                        .font(.caption.monospacedDigit())
                }
                if let err = sw01.line1ErrorMM, let pct = sw01.line1PercentError {
                    Text(String(format: "Line 1 error: %+.2f mm (%+.1f%%)", err, pct))
                        .font(.caption2)
                }
                if let err = sw01.line2ErrorMM, let pct = sw01.line2PercentError {
                    Text(String(format: "Line 2 error: %+.2f mm (%+.1f%%)", err, pct))
                        .font(.caption2)
                }
            }

            Divider()

            Group {
                Text("FR-02 — slice at \(String(format: "%.1f", sliceOffsetPercent))% height")
                    .font(.subheadline.weight(.semibold))
                Text("Compare the slice preview above qualitatively to the physical object at the same height.")
                    .font(.caption2)
                    .foregroundStyle(.secondary)
                Button("Export validation snapshot…") {
                    exportValidationSnapshot(result: result, primary: primary, ideal: ideal)
                }
            }

            Divider()

            Group {
                Text("SW-07 — Ideal Fit clearance (software)")
                    .font(.subheadline.weight(.semibold))
                if let ideal {
                    Text(
                        String(
                            format: "Clearance min %.2f / mean %.2f / max %.2f %@ (target %.0f ± %.0f %@)",
                            ideal.clearanceStats.min,
                            ideal.clearanceStats.mean,
                            ideal.clearanceStats.max,
                            unit,
                            ideal.clearanceStats.targetMM,
                            ideal.clearanceStats.toleranceMM,
                            unit
                        )
                    )
                    .font(.caption2)
                    Text(ideal.passesSW07 ? "SW-07: Pass" : "SW-07: Fail")
                        .font(.caption.weight(.semibold))
                        .foregroundStyle(ideal.passesSW07 ? .green : .red)
                } else {
                    Text("Could not compute Ideal Fit outline.")
                        .font(.caption2)
                        .foregroundStyle(.orange)
                }
                Button("Export verification report (CSV)…") {
                    exportVerificationReportCSV(result: result, primary: primary, ideal: ideal)
                }

                Button("Export clearance report (CSV)…") {
                    exportClearanceCSV(result: result, primary: primary, ideal: ideal)
                }
                .disabled(ideal == nil)
            }

            Divider()

            Button("Export DVR test record…") {
                exportDVRBundle(result: result, primary: primary, ideal: ideal)
            }

            if !validationExportNote.isEmpty {
                Text(validationExportNote)
                    .font(.caption2)
                    .foregroundStyle(.secondary)
                    .textSelection(.enabled)
            }
        } header: {
            Text("Validation (DVR-002)")
        } footer: {
            Text(
                "SW-01 compares your two custom validation lines (drawn on the outline) against caliper measurements on the object. "
                    + "SW-07 verifies the Ideal Fit band is \(String(format: "%.0f", clearanceDistanceForIdealFit())) \(unit) ± tolerance. "
                    + "DVR bundle includes verification_report.csv (one summary row per export)."
            )
            .font(.footnote)
        }
    }

    private func parseOptionalDouble(_ text: String) -> Double? {
        let t = text.trimmingCharacters(in: .whitespacesAndNewlines)
        guard !t.isEmpty else { return nil }
        return Double(t.replacingOccurrences(of: ",", with: "."))
    }

    private func chooseExportDirectory(completion: @escaping (URL?) -> Void) {
        guard let window = NSApp.keyWindow else {
            completion(nil)
            return
        }
        let panel = NSOpenPanel()
        panel.canChooseFiles = false
        panel.canChooseDirectories = true
        panel.allowsMultipleSelection = false
        panel.canCreateDirectories = true
        panel.message = "Choose a folder for validation export."
        panel.prompt = "Export"
        panel.beginSheetModal(for: window) { response in
            completion(response == .OK ? panel.url : nil)
        }
    }

    private func exportClearanceCSV(result: BasePerimeterResult, primary: [(Float, Float)], ideal: IdealFitResult?) {
        guard let ideal, let window = NSApp.keyWindow else { return }
        let panel = NSSavePanel()
        panel.allowedContentTypes = [.commaSeparatedText]
        panel.nameFieldStringValue = "clearance_report.csv"
        panel.beginSheetModal(for: window) { response in
            guard response == .OK, let dest = panel.url else { return }
            let csv = ValidationExport.clearanceCSV(primary: primary, ideal: ideal.points, stats: ideal.clearanceStats)
            do {
                try csv.write(to: dest, atomically: true, encoding: .utf8)
                validationExportNote = "Saved \(dest.path)"
            } catch {
                validationExportNote = error.localizedDescription
            }
        }
    }

    private func exportVerificationReportCSV(
        result: BasePerimeterResult,
        primary: [(Float, Float)],
        ideal: IdealFitResult?
    ) {
        guard let window = NSApp.keyWindow else { return }
        let panel = NSSavePanel()
        panel.allowedContentTypes = [.commaSeparatedText]
        panel.nameFieldStringValue = "verification_report.csv"
        panel.beginSheetModal(for: window) { response in
            guard response == .OK, let dest = panel.url else { return }
            let summary = ValidationExport.makeSummary(
                result: result,
                primary: primary,
                idealFit: ideal,
                validationLines: outlineValidationLines,
                realLine1MM: parseOptionalDouble(realObjectLine1MMText),
                realLine2MM: parseOptionalDouble(realObjectLine2MMText),
                unitsMillimeters: useMillimeters,
                userScale: computedUserScale(),
                usdzName: activeUSDZ?.lastPathComponent
            )
            let csv = ValidationExport.verificationReportCSV(
                summary: summary,
                loopVertexCount: result.loopVertexCount,
                outlineSampleCount: primary.count
            )
            do {
                try csv.write(to: dest, atomically: true, encoding: .utf8)
                validationExportNote = "Saved \(dest.path)"
            } catch {
                validationExportNote = error.localizedDescription
            }
        }
    }

    private func exportValidationSnapshot(result: BasePerimeterResult, primary: [(Float, Float)], ideal: IdealFitResult?) {
        chooseExportDirectory { dir in
            guard let dir else { return }
            Task { @MainActor in
                let png = ValidationExport.renderOutlinePNG(
                    primary: primary,
                    idealFit: ideal?.points,
                    clearanceStats: ideal?.clearanceStats,
                    unitsMillimeters: useMillimeters,
                    validationLines: outlineValidationLines
                )
                let summary = ValidationExport.makeSummary(
                    result: result,
                    primary: primary,
                    idealFit: ideal,
                    validationLines: outlineValidationLines,
                    realLine1MM: parseOptionalDouble(realObjectLine1MMText),
                    realLine2MM: parseOptionalDouble(realObjectLine2MMText),
                    unitsMillimeters: useMillimeters,
                    userScale: computedUserScale(),
                    usdzName: activeUSDZ?.lastPathComponent
                )
                do {
                    try ValidationExport.exportDVRBundle(
                        to: dir,
                        result: result,
                        primary: primary,
                        idealFit: ideal,
                        summary: summary,
                        outlinePreviewPNG: png
                    )
                    validationExportNote = "Snapshot saved to \(dir.path)"
                } catch {
                    validationExportNote = error.localizedDescription
                }
            }
        }
    }

    private func exportDVRBundle(result: BasePerimeterResult, primary: [(Float, Float)], ideal: IdealFitResult?) {
        chooseExportDirectory { dir in
            guard let dir else { return }
            Task { @MainActor in
                let png = ValidationExport.renderOutlinePNG(
                    primary: primary,
                    idealFit: ideal?.points,
                    clearanceStats: ideal?.clearanceStats,
                    unitsMillimeters: useMillimeters,
                    validationLines: outlineValidationLines
                )
                let summary = ValidationExport.makeSummary(
                    result: result,
                    primary: primary,
                    idealFit: ideal,
                    validationLines: outlineValidationLines,
                    realLine1MM: parseOptionalDouble(realObjectLine1MMText),
                    realLine2MM: parseOptionalDouble(realObjectLine2MMText),
                    unitsMillimeters: useMillimeters,
                    userScale: computedUserScale(),
                    usdzName: activeUSDZ?.lastPathComponent
                )
                do {
                    try ValidationExport.exportDVRBundle(
                        to: dir,
                        result: result,
                        primary: primary,
                        idealFit: ideal,
                        summary: summary,
                        outlinePreviewPNG: png
                    )
                    validationExportNote = "DVR record saved to \(dir.path)"
                } catch {
                    validationExportNote = error.localizedDescription
                }
            }
        }
    }

    private func adoptDefaultCalibrationImageIfNeeded() {
        guard calibrationSource == .photoWithCoin || calibrationSource == .photoWithArUco else { return }
        guard calibrationImageURL == nil, let def = defaultCalibrationImageURL,
              FileManager.default.fileExists(atPath: def.path) else { return }
        importCalibrationStill(from: def, securityScoped: false)
    }

    @ViewBuilder
    private var referenceSection: some View {
        Section {
            Text(
                "Prefer **ArUco in USDZ**: orbit a virtual camera, detect the printed DICT_4X4_50 marker in textured renders, "
                    + "and set scale from printed side mm ÷ mesh side. Photo ArUco, coin, and named-object paths remain as fallbacks."
            )
            .font(.footnote)
            .foregroundStyle(.secondary)

            Button("Open other USDZ…") {
                chooseOtherUSDZ()
            }
            .disabled(isExtracting || isMeasuring)

            Picker("Scale from", selection: $calibrationSource) {
                ForEach(ScaleCalibrationSource.allCases) { src in
                    Text(src.title).tag(src)
                }
            }
            .disabled(isExtracting || isMeasuring)

            switch calibrationSource {
            case .arucoInMesh:
                meshArucoReferenceControls
            case .namedUSDZMesh:
                namedMeshReferenceControls
            case .photoWithCoin:
                photoCoinReferenceControls
            case .photoWithArUco:
                photoArucoReferenceControls
            }

            LabeledContent("Name contains") {
                TextField("Reference", text: $referenceNameSubstring)
            }
            .disabled(isExtracting || isMeasuring)

            Toggle("Exclude matching name from perimeter slice", isOn: $excludeReferenceFromOutline)
                .disabled(isExtracting || isMeasuring)

            Toggle("G-code XY in millimeters (G21)", isOn: $useMillimeters)
                .disabled(isExtracting)
        } header: {
            Text("Reference scale")
        }
    }

    @ViewBuilder
    private var meshArucoReferenceControls: some View {
        VStack(alignment: .leading, spacing: 8) {
            Text(
                "Place a printed DICT_4X4_50 marker on the flange so Object Capture bakes it into the USDZ texture. "
                    + "Measure orbits a virtual camera, detects the pattern, and unprojects the square onto the mesh."
            )
            .font(.caption2)
            .foregroundStyle(.tertiary)
            .fixedSize(horizontal: false, vertical: true)

            HStack(spacing: 12) {
                LabeledContent("Marker side (mm)") {
                    TextField("27", text: $arucoMarkerSideMMText)
                        .frame(maxWidth: 80)
                }
                LabeledContent("Expected ID (−1 = any)") {
                    TextField("-1", text: $arucoExpectedIDText)
                        .frame(maxWidth: 60)
                }
            }
            .font(.caption)
            .disabled(isExtracting || isMeasuring)

            Toggle("Use mesh ArUco scale", isOn: $useReferenceScale)

            if !useReferenceScale {
                LabeledContent("Manual scale (×)") {
                    TextField("1.0", text: $manualScaleOnlyText)
                        .frame(maxWidth: 120)
                }
            }

            HStack(spacing: 10) {
                Button {
                    measureArUcoInMesh()
                } label: {
                    HStack(spacing: 6) {
                        if isMeasuring {
                            ProgressView()
                                .controlSize(.small)
                        }
                        Text("Measure ArUco in USDZ")
                    }
                }
                .disabled(activeUSDZ == nil || isMeasuring || isExtracting)

                Button("Export ArUco marker PNG…") {
                    exportArucoMarkerPNG()
                }
                .disabled(isExtracting)
            }

            if !meshArUcoNote.isEmpty {
                Text(meshArUcoNote)
                    .font(.caption)
                    .foregroundStyle(meshArUcoResult == nil ? Color.secondary : Color.green)
                    .fixedSize(horizontal: false, vertical: true)
            }

            if let r = meshArUcoResult {
                LabeledContent("Mesh marker side (scene)") {
                    Text(String(format: "%.6f", r.meanSideScene))
                        .font(.caption)
                }
                LabeledContent("userScale (m/scene)") {
                    Text(String(format: "%.6f", computedUserScale()))
                        .font(.caption)
                }
            }

            if isMeasuring || meshArUcoLiveImage != nil {
                VStack(alignment: .leading, spacing: 4) {
                    HStack(spacing: 8) {
                        Text("Virtual camera feed")
                            .font(.caption2.weight(.semibold))
                        if isMeasuring {
                            ProgressView()
                                .controlSize(.mini)
                        }
                        if meshArUcoLiveDetected {
                            Label("ArUco", systemImage: "viewfinder")
                                .font(.caption2)
                                .foregroundStyle(.green)
                        }
                        Spacer(minLength: 0)
                    }
                    Text("Green = 2D detect; cyan dashed = 3D hits reprojected (should overlap)")
                        .font(.caption2)
                        .foregroundStyle(.secondary)
                    if !meshArUcoLiveStatus.isEmpty {
                        Text(meshArUcoLiveStatus)
                            .font(.caption2)
                            .foregroundStyle(meshArUcoLiveDetected ? Color.green : Color.secondary)
                            .lineLimit(2)
                    }
                    if let live = meshArUcoLiveImage {
                        Image(nsImage: live)
                            .resizable()
                            .aspectRatio(contentMode: .fit)
                            .frame(maxWidth: 420, maxHeight: 420)
                            .clipShape(RoundedRectangle(cornerRadius: 6, style: .continuous))
                            .overlay(
                                RoundedRectangle(cornerRadius: 6, style: .continuous)
                                    .strokeBorder(
                                        meshArUcoLiveDetected ? Color.green : Color.cyan.opacity(0.6),
                                        lineWidth: meshArUcoLiveDetected ? 2 : 1
                                    )
                            )
                    } else if isMeasuring {
                        RoundedRectangle(cornerRadius: 6, style: .continuous)
                            .fill(Color(nsColor: .controlBackgroundColor))
                            .frame(height: 180)
                            .overlay {
                                Text("Waiting for first orbit frame…")
                                    .font(.caption)
                                    .foregroundStyle(.secondary)
                            }
                    }
                }
            }

            if let debug = meshArUcoDebugImage {
                VStack(alignment: .leading, spacing: 4) {
                    Text("Validation snapshot (test) — +45° side view; cyan = detect cam; green hit / orange miss")
                        .font(.caption2)
                        .foregroundStyle(.secondary)
                    Image(nsImage: debug)
                        .resizable()
                        .aspectRatio(contentMode: .fit)
                        .frame(maxWidth: 420, maxHeight: 420)
                        .clipShape(RoundedRectangle(cornerRadius: 6, style: .continuous))
                        .overlay(
                            RoundedRectangle(cornerRadius: 6, style: .continuous)
                                .strokeBorder(Color.green.opacity(0.5), lineWidth: 1)
                        )
                }
            }
        }
    }

    @ViewBuilder
    private var namedMeshReferenceControls: some View {
        Group {
            Picker("Reference preset", selection: $referenceKind) {
                ForEach(ScaleReferenceKind.allCases) { k in
                    Text(k.title).tag(k)
                }
            }
            .disabled(isExtracting || isMeasuring)

            if referenceKind == .custom {
                LabeledContent("Real length (mm)") {
                    TextField("mm", text: $customRealMmText)
                        .frame(maxWidth: 120)
                }
            }

            Toggle("Use reference for export scale", isOn: $useReferenceScale)

            if !useReferenceScale {
                LabeledContent("Manual scale (×)") {
                    TextField("1.0", text: $manualScaleOnlyText)
                        .frame(maxWidth: 120)
                }
            }

            Button {
                measureReference()
            } label: {
                HStack(spacing: 8) {
                    if isMeasuring {
                        ProgressView()
                            .controlSize(.small)
                    }
                    Text("Measure reference in USDZ")
                }
                .frame(maxWidth: 280, alignment: .leading)
            }
            .disabled(activeUSDZ == nil || isMeasuring || isExtracting)

            if !measureNote.isEmpty {
                Text(measureNote)
                    .font(.caption)
                    .foregroundStyle(.secondary)
            }

            if measuredMeshRefLength != nil {
                LabeledContent("Mesh ref length (scene)") {
                    Text(String(format: "%.6f", measuredMeshRefLength!))
                        .font(.caption)
                }
            }

            LabeledContent("Mesh length override") {
                TextField("optional", text: $manualMeshRefLengthText)
                    .frame(maxWidth: 140)
            }
            .font(.caption)
        }
    }

    @ViewBuilder
    private var photoArucoReferenceControls: some View {
        Group {
            Text(
                "Print a **DICT_4X4_50** marker (use Export below), place it coplanar with the stoma in a top-down still, "
                    + "then run detection. Scale uses multiple outline measurements — not a single diameter."
            )
            .font(.caption2)
            .foregroundStyle(.tertiary)
            .fixedSize(horizontal: false, vertical: true)

            HStack(spacing: 12) {
                LabeledContent("Marker side (mm)") {
                    TextField("27", text: $arucoMarkerSideMMText)
                        .frame(maxWidth: 80)
                }
                LabeledContent("Expected ID (−1 = any)") {
                    TextField("-1", text: $arucoExpectedIDText)
                        .frame(maxWidth: 60)
                }
            }
            .font(.caption)
            .disabled(isExtracting || isAutoDetecting)

            Toggle("Use ArUco multi-metric scale", isOn: $useReferenceScale)

            if !useReferenceScale {
                LabeledContent("Manual scale (×)") {
                    TextField("1.0", text: $manualScaleOnlyText)
                        .frame(maxWidth: 120)
                }
            }

            VStack(alignment: .leading, spacing: 8) {
                HStack(spacing: 10) {
                    Button("Choose calibration image…") {
                        chooseCalibrationStill()
                    }
                    .disabled(isExtracting || isImportingCalibrationFrame)

                    Button {
                        chooseCalibrationVideoFirstFrame()
                    } label: {
                        HStack(spacing: 6) {
                            if isImportingCalibrationFrame {
                                ProgressView()
                                    .controlSize(.small)
                            }
                            Text("First frame from video…")
                        }
                    }
                    .disabled(isExtracting || isImportingCalibrationFrame)

                    if calibrationImageURL != nil {
                        Button("Clear image") {
                            resetCalibrationImageForNewInput()
                        }
                        .disabled(isExtracting)
                    }
                }

                HStack(spacing: 10) {
                    Button {
                        runArucoMultiMetricCalibration()
                    } label: {
                        HStack(spacing: 6) {
                            if isAutoDetecting {
                                ProgressView()
                                    .controlSize(.small)
                            }
                            Text("Detect marker & analyze outline")
                        }
                    }
                    .disabled(calibrationImage == nil || result == nil || isExtracting || isAutoDetecting)

                    Button("Export ArUco marker PNG…") {
                        exportArucoMarkerPNG()
                    }
                    .disabled(isExtracting)
                }
            }

            if arucoOverlayCorners.count == 4 {
                Label(
                    arucoOverlayMarkerID.map { "Marker locked — ID \($0) (green square on photo)" }
                        ?? "Marker locked (green square on photo)",
                    systemImage: "checkmark.seal.fill"
                )
                .font(.caption)
                .foregroundStyle(.green)
            }

            if !autoDetectNote.isEmpty {
                Text(autoDetectNote)
                    .font(.caption2)
                    .foregroundStyle(photoScaleEstimate?.passesConsistency == false ? .orange : .secondary)
                    .fixedSize(horizontal: false, vertical: true)
            }

            if let est = photoScaleEstimate {
                LabeledContent("Robust scale (mm / scene)") {
                    Text(String(format: "%.6f", est.scaleSceneToMillimeters))
                        .font(.caption.monospacedDigit())
                }
                LabeledContent("Consistency") {
                    Text(
                        String(
                            format: "CV %.1f%% · max residual %.1f%% · conf %.0f%%",
                            est.coefficientOfVariation * 100,
                            est.maxRelativeResidual * 100,
                            est.confidence * 100
                        )
                    )
                    .font(.caption2)
                }
                if !est.passesConsistency {
                    Toggle("Allow export despite inconsistency", isOn: $allowInconsistentPhotoScale)
                        .font(.caption)
                }
                DisclosureGroup("Per-metric scale estimates") {
                    ForEach(est.components) { c in
                        HStack {
                            Text(c.kind.title)
                            Spacer()
                            Text(String(format: "%.4f  (%.2f mm / %.5f)", c.scaleSceneToMillimeters, c.photoMillimeters, c.meshSceneUnits))
                                .font(.caption2.monospacedDigit())
                                .foregroundStyle(.secondary)
                        }
                    }
                }
                .font(.caption)
            }

            if !calibrationImportNote.isEmpty {
                Text(calibrationImportNote)
                    .font(.caption2)
                    .foregroundStyle(calibrationImage == nil ? .orange : .secondary)
            }

            if let path = calibrationImageURL?.path {
                Text(path)
                    .font(.caption2)
                    .foregroundStyle(.secondary)
                    .lineLimit(2)
            }

            Text("Manual coin/stoma lines remain available below as a fallback if auto-detect fails.")
                .font(.caption2)
                .foregroundStyle(.tertiary)

            Picker("Draw line (fallback)", selection: $photoCalibrationActiveLine) {
                ForEach(PhotoCalibrationLineKind.allCases) { k in
                    Text(k.title(coin: photoCoinKind)).tag(k)
                }
            }
            .pickerStyle(.segmented)
            .disabled(calibrationImage == nil || isExtracting)

            QuarterPhotoCalibrationEditor(
                image: calibrationImage,
                lines: $photoCalibrationLines,
                activeLine: $photoCalibrationActiveLine,
                overlayMarkerCorners: arucoOverlayCorners,
                overlayMarkerID: arucoOverlayMarkerID,
                overlayContour: photoOverlayContour
            )
            .frame(maxWidth: .infinity, minHeight: 280, idealHeight: 280, maxHeight: 280)
            .clipShape(RoundedRectangle(cornerRadius: 8, style: .continuous))
            .overlay {
                RoundedRectangle(cornerRadius: 8, style: .continuous)
                    .strokeBorder(
                        arucoOverlayCorners.count == 4 ? Color.green.opacity(0.55) : Color.secondary.opacity(0.25),
                        lineWidth: arucoOverlayCorners.count == 4 ? 2 : 1
                    )
                    .allowsHitTesting(false)
            }
        }
    }

    @ViewBuilder
    private var photoCoinReferenceControls: some View {
        Group {
            Picker("Reference coin", selection: $photoCoinKind) {
                ForEach(PhotoCalibrationCoinKind.allCases) { coin in
                    Text(coin.title).tag(coin)
                }
            }
            .disabled(isExtracting || isMeasuring)

            Text(
                "Draw the **\(photoCoinKind.lineLabel.lowercased())** on the still, then a **stoma span** in the same plane "
                    + "(match the **longest diameter** shown in the outline preview after extraction). Assumes a top-down view with weak perspective."
            )
            .font(.caption2)
            .foregroundStyle(.tertiary)
            .fixedSize(horizontal: false, vertical: true)

            Toggle("Use photo for export scale", isOn: $useReferenceScale)

            if !useReferenceScale {
                LabeledContent("Manual scale (×)") {
                    TextField("1.0", text: $manualScaleOnlyText)
                        .frame(maxWidth: 120)
                }
            }

            VStack(alignment: .leading, spacing: 8) {
                HStack(spacing: 10) {
                    Button("Choose calibration image…") {
                        chooseCalibrationStill()
                    }
                    .disabled(isExtracting || isImportingCalibrationFrame)

                    Button {
                        chooseCalibrationVideoFirstFrame()
                    } label: {
                        HStack(spacing: 6) {
                            if isImportingCalibrationFrame {
                                ProgressView()
                                    .controlSize(.small)
                            }
                            Text("First frame from video…")
                        }
                    }
                    .disabled(isExtracting || isImportingCalibrationFrame)

                    if calibrationImageURL != nil {
                        Button("Clear image") {
                            resetCalibrationImageForNewInput()
                        }
                        .disabled(isExtracting)
                    }
                }

                Text(
                    "Use an existing mesh and load calibration separately — no need to re-run photogrammetry. "
                        + "**First frame from video** extracts `t = 0` only."
                )
                .font(.caption2)
                .foregroundStyle(.tertiary)
                .fixedSize(horizontal: false, vertical: true)
            }

            if !calibrationImportNote.isEmpty {
                Text(calibrationImportNote)
                    .font(.caption2)
                    .foregroundStyle(calibrationImage == nil ? .orange : .secondary)
            }

            if let path = calibrationImageURL?.path {
                Text(path)
                    .font(.caption2)
                    .foregroundStyle(.secondary)
                    .lineLimit(2)
            }

            Picker("Draw line", selection: $photoCalibrationActiveLine) {
                ForEach(PhotoCalibrationLineKind.allCases) { k in
                    Text(k.title(coin: photoCoinKind)).tag(k)
                }
            }
            .pickerStyle(.segmented)
            .disabled(calibrationImage == nil || isExtracting)

            if calibrationImageURL != nil, calibrationImage == nil {
                Text("Could not load that image. Try **Choose calibration image…** with a JPEG, PNG, or HEIC.")
                    .font(.caption)
                    .foregroundStyle(.orange)
            }

            Text(
                "Use the segment control to choose **which** line you are placing, then **click and drag on the photo** below (press, drag, release). "
                    + "Both ends of the drag must stay on the image."
            )
            .font(.caption)
            .foregroundStyle(.secondary)
            .fixedSize(horizontal: false, vertical: true)

            QuarterPhotoCalibrationEditor(
                image: calibrationImage,
                lines: $photoCalibrationLines,
                activeLine: $photoCalibrationActiveLine
            )
            .frame(maxWidth: .infinity, minHeight: 280, idealHeight: 280, maxHeight: 280)
            .clipShape(RoundedRectangle(cornerRadius: 8, style: .continuous))
            .overlay {
                RoundedRectangle(cornerRadius: 8, style: .continuous)
                    .strokeBorder(Color.secondary.opacity(0.25), lineWidth: 1)
                    .allowsHitTesting(false)
            }

            if let coinPx = photoCalibrationLines.coinPixelLength {
                Text(String(format: "%@: %.1f px", photoCoinKind.lineLabel, Double(coinPx)))
                    .font(.caption2.monospacedDigit())
            }
            if let s = photoCalibrationLines.stomaPixelLength {
                Text(String(format: "Stoma span: %.1f px", Double(s)))
                    .font(.caption2.monospacedDigit())
            }
            if let r = photoDerivedRealStomaMeters() {
                Text(String(format: "Derived stoma span (from photo): %.2f mm", r * 1000))
                    .font(.caption2)
                    .foregroundStyle(.secondary)
            }

            if let r = result, calibrationSource == .photoWithCoin {
                let chord = BasePerimeterExtractor.maxPlanarChordLength(samples: r.samples)
                LabeledContent("Mesh longest diameter (scene)") {
                    Text(String(format: "%.6f", chord))
                        .font(.caption)
                }
            }

            LabeledContent("Mesh length override") {
                TextField("optional (scene units)", text: $manualMeshRefLengthText)
                    .frame(maxWidth: 160)
            }
            .font(.caption)
        }
    }

    private func photoDerivedRealStomaMeters() -> Double? {
        guard let coinPx = photoCalibrationLines.coinPixelLength,
              let sPx = photoCalibrationLines.stomaPixelLength,
              coinPx > 1, sPx > 1 else { return nil }
        return Double(sPx / coinPx) * photoCoinKind.diameterMeters
    }

    @ViewBuilder
    private var manualSliceTiltControls: some View {
        VStack(alignment: .leading, spacing: 8) {
            Text(
                "Tilt the slice plane in mesh coordinates. **Slice offset** still moves the plane up/down along this normal (0% = floor)."
            )
            .font(.caption2)
            .foregroundStyle(.tertiary)
            .fixedSize(horizontal: false, vertical: true)

            Picker("Base axis", selection: $manualSliceTilt.baseAxis) {
                ForEach(BaseSliceUpAxis.manualTiltBaseChoices) { axis in
                    Text(axis.menuLabel).tag(axis)
                }
            }
            .disabled(isExtracting)

            tiltSlider(label: "Tilt around mesh X", value: $manualSliceTilt.tiltXDegrees)
            tiltSlider(label: "Tilt around mesh Y", value: $manualSliceTilt.tiltYDegrees)
            tiltSlider(label: "Tilt around mesh Z", value: $manualSliceTilt.tiltZDegrees)
            tiltSlider(label: "In-plane spin (G-code X/Y twist)", value: $manualSliceTilt.spinDegrees, range: -180 ... 180)

            let n = BasePerimeterExtractor.planeNormal(for: manualSliceTilt)
            Text(
                String(
                    format: "Slice normal: (%.3f, %.3f, %.3f)",
                    n.x, n.y, n.z
                )
            )
            .font(.caption.monospacedDigit())
            .foregroundStyle(.secondary)
            .textSelection(.enabled)

            HStack(spacing: 10) {
                Button("Reset tilts") {
                    manualSliceTilt = ManualSliceAxisTuning(baseAxis: manualSliceTilt.baseAxis)
                }
                .disabled(isExtracting)

                Button {
                    seedManualTiltFromAutomatic()
                } label: {
                    HStack(spacing: 6) {
                        if isSeedingManualTilt {
                            ProgressView()
                                .controlSize(.small)
                        }
                        Text("Seed from automatic")
                    }
                }
                .disabled(activeUSDZ == nil || isExtracting || isSeedingManualTilt)
            }
        }
        .frame(maxWidth: 360, alignment: .leading)
    }

    private func tiltSlider(
        label: String,
        value: Binding<Double>,
        range: ClosedRange<Double> = -45 ... 45
    ) -> some View {
        VStack(alignment: .leading, spacing: 4) {
            Text(String(format: "%@: %.1f°", label, value.wrappedValue))
                .font(.caption)
            Slider(value: value, in: range, step: 0.5)
                .frame(maxWidth: 360)
        }
    }

    private func seedManualTiltFromAutomatic() {
        guard let src = activeUSDZ else { return }
        isSeedingManualTilt = true
        let exclude = runPerimeterExclusionPattern()
        let isPicked = pickedUSDZ != nil

        Task {
            do {
                let seeded: ManualSliceAxisTuning = try await Task.detached(priority: .userInitiated) {
                    let workURL: URL
                    if isPicked {
                        let accessing = src.startAccessingSecurityScopedResource()
                        guard accessing else {
                            throw NSError(domain: "BasePerimeter", code: 2, userInfo: [NSLocalizedDescriptionKey: "Could not access the selected file."])
                        }
                        defer { src.stopAccessingSecurityScopedResource() }
                        let temp = FileManager.default.temporaryDirectory
                            .appendingPathComponent("tilt-seed-\(UUID().uuidString).usdz", isDirectory: false)
                        try FileManager.default.copyItem(at: src, to: temp)
                        workURL = temp
                    } else {
                        workURL = src
                    }
                    defer {
                        if isPicked {
                            try? FileManager.default.removeItem(at: workURL)
                        }
                    }
                    return try BasePerimeterExtractor.manualTiltSeededFromAutomatic(
                        usdzURL: workURL,
                        excludeObjectNameSubstring: exclude
                    )
                }.value

                await MainActor.run {
                    manualSliceTilt = seeded
                    upAxis = .manualTilt
                    isSeedingManualTilt = false
                }
            } catch {
                await MainActor.run {
                    extractionError = error.localizedDescription
                    isSeedingManualTilt = false
                }
            }
        }
    }

    @ViewBuilder
    private var perimeterSection: some View {
        Section {
            HStack(alignment: .top, spacing: 16) {
                VStack(alignment: .leading, spacing: 10) {
                    Text(
                        "Perimeter uses **subject-only** triangles when exclusion is on. Slice height uses the same set."
                    )
                    .font(.footnote)
                    .foregroundStyle(.secondary)

                    Picker("Up axis", selection: $upAxis) {
                        ForEach(BaseSliceUpAxis.allCases) { axis in
                            Text(axis.menuLabel).tag(axis)
                        }
                    }
                    .disabled(isExtracting)

                    if upAxis == .automatic {
                        Text(
                            "Detects a **table/skin support plane** so slices stay parallel to that surface (0% = support). "
                                + "If no large flat support is found, falls back to mesh **+Y**. Pick a fixed axis to override."
                        )
                        .font(.caption2)
                        .foregroundStyle(.tertiary)
                        .fixedSize(horizontal: false, vertical: true)
                    }

                    if upAxis == .manualTilt {
                        manualSliceTiltControls
                    }

                    VStack(alignment: .leading, spacing: 6) {
                        Text(String(format: "Slice offset: %.2f%% of height", sliceOffsetPercent))
                            .font(.caption)
                        Slider(value: $sliceOffsetPercent, in: 0 ... 100, step: 0.5)
                            .frame(maxWidth: 360)
                        Text("Extraction starts at this offset, then tries every +2.5% up to 100% until a perimeter succeeds. The slider jumps to the offset that worked.")
                            .font(.caption2)
                            .foregroundStyle(.tertiary)
                    }
                    .frame(maxWidth: .infinity, alignment: .leading)
                    .disabled(isExtracting)

                    Button {
                        runPerimeterExtraction()
                    } label: {
                        HStack(spacing: 8) {
                            if isExtracting {
                                ProgressView()
                                    .controlSize(.small)
                            }
                            Text("Extract base perimeter (100 points)")
                        }
                        .frame(maxWidth: 320, alignment: .leading)
                    }
                    .disabled(activeUSDZ == nil || isExtracting)

                    if let extractionError {
                        Text(extractionError)
                            .font(.footnote)
                            .foregroundStyle(.red)
                    }

                    if let result {
                        LabeledContent("Loop vertices") {
                            Text("\(result.loopVertexCount)")
                                .font(.caption)
                        }
                    }
                }
                .frame(maxWidth: 440, alignment: .leading)

                BasePerimeterSlicePreviewView(
                    usdzURL: activeUSDZ,
                    requiresSecurityScopedAccess: pickedUSDZ != nil,
                    excludeNameContains: excludeReferenceFromOutline ? referenceNameSubstring : nil,
                    excludeWorldAABB: meshArUcoExclusionAABB,
                    markerWorldCorners: meshArUcoWorldCorners,
                    upAxis: upAxis,
                    manualTilt: manualSliceTilt,
                    sliceOffsetFraction: Float(sliceOffsetPercent / 100)
                )
                .id(activeUSDZ?.path ?? "no-mesh")
                .frame(minWidth: 300, idealWidth: 380, maxWidth: 520, minHeight: 300, maxHeight: 420)
            }
        } header: {
            Text("Base perimeter")
        }
    }

    @ViewBuilder
    private var exportSection: some View {
        Section {
            if let url = autoExportURL {
                Text(
                    "Saved for plotter: \(url.path)\n"
                        + "Stream: python3 firmware/send_gcode.py \"\(url.path)\""
                )
                .font(.caption2)
                .foregroundStyle(.secondary)
                .textSelection(.enabled)
                if let idealURL = autoExportIdealFitURL {
                    Text("Ideal Fit G-code: \(idealURL.path)")
                        .font(.caption2)
                        .foregroundStyle(.secondary)
                        .textSelection(.enabled)
                }
                Button("Reveal in Finder") {
                    NSWorkspace.shared.activateFileViewerSelecting([url])
                }
            } else if autoExportFailed {
                Text(
                    "Could not write to firmware/test_patterns. Choose that folder once, or set STOMA_REPO_ROOT to your Module2 path."
                )
                .font(.caption)
                .foregroundStyle(.orange)
                Button("Choose plotter export folder…") {
                    choosePlotterExportFolder()
                }
            }

            LabeledContent("Polar plotter RPM") {
                    TextField("RPM", value: $polarPlotterRPM, format: .number)
                        .frame(maxWidth: 80)
                        .onChange(of: polarPlotterRPM) { _, _ in rebuildExports() }
                }
                if !polarExportNote.isEmpty {
                    Text(polarExportNote)
                        .font(.caption2)
                        .foregroundStyle(.secondary)
                }
                if let polarURL = autoExportPolarURL {
                    Text("Polar job: \(polarURL.path)")
                        .font(.caption2)
                        .foregroundStyle(.secondary)
                        .textSelection(.enabled)
                }

            Button("Copy G-code") {
                NSPasteboard.general.clearContents()
                NSPasteboard.general.setString(exportGcode, forType: .string)
            }
            Button("Save G-code…") {
                saveExport()
            }
            ScrollView {
                Text(exportGcode)
                    .font(.system(.caption, design: .monospaced))
                    .textSelection(.enabled)
            }
            .frame(maxHeight: 220)
        } header: {
            Text("G-code export")
        } footer: {
            Text(
                "Each perimeter update overwrites `\(PlotterGcodeAutoExport.fileName)` and `\(PolarPathExport.polarFileName)` in `firmware/test_patterns/` when that folder is found next to this project."
            )
            .font(.footnote)
        }
    }

    private func effectiveRealMeters() -> Double? {
        switch referenceKind {
        case .none:
            return nil
        case .custom:
            let v = Double(customRealMmText.replacingOccurrences(of: ",", with: ".")) ?? 0
            guard v > 0 else { return nil }
            return v / 1000
        default:
            return referenceKind.realLengthMeters
        }
    }

    private func effectiveMeshRefLength() -> Float? {
        if calibrationSource == .photoWithCoin, let r = result {
            let chord = BasePerimeterExtractor.maxPlanarChordLength(samples: r.samples)
            if chord > 1e-10 { return chord }
        }
        if let m = measuredMeshRefLength, m > 0 { return m }
        let t = manualMeshRefLengthText.trimmingCharacters(in: .whitespacesAndNewlines)
        guard let v = Float(t.replacingOccurrences(of: ",", with: ".")), v > 0 else { return nil }
        return v
    }

    private func computedUserScale() -> Float {
        if useReferenceScale {
            if calibrationSource == .arucoInMesh {
                let sideMM = Double(arucoMarkerSideMMText.replacingOccurrences(of: ",", with: ".")) ?? 0
                if let r = meshArUcoResult, r.meanSideScene > 1e-10, sideMM > 0.5 {
                    // meters/scene = (printed side meters) / (mesh side scene)
                    return Float((sideMM / 1000.0) / Double(r.meanSideScene))
                }
                return Float(manualScaleOnlyText.replacingOccurrences(of: ",", with: ".")) ?? 1
            }
            if calibrationSource == .photoWithArUco {
                if let est = photoScaleEstimate {
                    if est.passesConsistency || allowInconsistentPhotoScale {
                        // Export path: mesh scene × userScale → mm when useMillimeters.
                        // BasePerimeterExport multiplies plane XY by userScale, and when unitsMillimeters
                        // also multiplies by 1000 if coords were meters. Our estimator returns mm/scene.
                        // Existing coin path: realMeters/meshLen gives meters/scene; with useMillimeters
                        // the export does *1000. So we need the same convention: meters per scene unit.
                        return Float(est.scaleSceneToMillimeters / 1000.0)
                    }
                }
                // Fall through to coin-line fallback if present, else manual.
                if let realM = photoDerivedRealStomaMeters(),
                   let meshLen = effectiveMeshRefLength(), meshLen > 0 {
                    return Float(realM / Double(meshLen))
                }
                return Float(manualScaleOnlyText.replacingOccurrences(of: ",", with: ".")) ?? 1
            }
            if calibrationSource == .photoWithCoin {
                guard let realM = photoDerivedRealStomaMeters(),
                      let meshLen = effectiveMeshRefLength(), meshLen > 0 else {
                    return Float(manualScaleOnlyText.replacingOccurrences(of: ",", with: ".")) ?? 1
                }
                return Float(realM / Double(meshLen))
            }
            guard let realM = effectiveRealMeters(), let meshLen = effectiveMeshRefLength(), meshLen > 0 else {
                return Float(manualScaleOnlyText.replacingOccurrences(of: ",", with: ".")) ?? 1
            }
            return Float(realM / Double(meshLen))
        }
        return Float(manualScaleOnlyText.replacingOccurrences(of: ",", with: ".")) ?? 1
    }

    private func runArucoMultiMetricCalibration() {
        guard let image = calibrationImage,
              let cgImage = image.cgImage(forProposedRect: nil, context: nil, hints: nil),
              let perimeter = result else {
            autoDetectNote = "Need a calibration image and an extracted perimeter."
            return
        }

        let sideMM = Double(arucoMarkerSideMMText.replacingOccurrences(of: ",", with: ".")) ?? 0
        let expectedID = Int(arucoExpectedIDText.trimmingCharacters(in: .whitespacesAndNewlines)) ?? -1
        isAutoDetecting = true
        autoDetectNote = "Detecting ArUco marker…"
        allowInconsistentPhotoScale = false
        arucoOverlayCorners = []
        arucoOverlayMarkerID = nil
        photoOverlayContour = []

        // Detector returns CGImage pixel coords; NSImage.draw uses `size` (may differ).
        let cgW = CGFloat(cgImage.width)
        let cgH = CGFloat(cgImage.height)
        let imgW = image.size.width
        let imgH = image.size.height
        let scaleX = cgW > 1 ? imgW / cgW : 1
        let scaleY = cgH > 1 ? imgH / cgH : 1

        Task.detached(priority: .userInitiated) {
            do {
                let homo = try ArUcoDetectorBridge.homography(
                    from: cgImage,
                    markerSideMillimeters: sideMM,
                    expectedID: expectedID
                )
                let corners = homo.marker.corners.map { value -> CGPoint in
                    let p = value.pointValue
                    return CGPoint(x: p.x * scaleX, y: p.y * scaleY)
                }
                let markerID = Int(homo.marker.markerID)

                // Show green confirmation as soon as the marker is found.
                await MainActor.run {
                    arucoOverlayCorners = corners
                    arucoOverlayMarkerID = markerID
                    autoDetectNote = "Marker ID \(markerID) locked — analyzing stoma outline…"
                }

                let H = homo.matrixRowMajor.map(\.doubleValue)

                let contour = try PhotoStomaContourDetector.detect(
                    in: image,
                    excludingMarkerCorners: corners,
                    homographyRowMajor: H
                )
                guard !contour.millimeterPoints.isEmpty else {
                    throw PhotoStomaContourError.noContour
                }
                guard let photoMetrics = StomaShapeMetrics.compute(from: contour.millimeterPoints),
                      let meshMetrics = StomaShapeMetrics.compute(fromMeshSamples: perimeter.samples),
                      let estimate = PhotoScaleEstimator.estimate(photoMM: photoMetrics, meshScene: meshMetrics)
                else {
                    throw PhotoStomaContourError.noContour
                }

                await MainActor.run {
                    arucoOverlayCorners = corners
                    arucoOverlayMarkerID = markerID
                    photoOverlayContour = contour.pixelPoints
                    photoScaleEstimate = estimate
                    autoDetectNote = "Marker ID \(markerID) · "
                        + String(format: "residual %.2f mm · ", homo.meanCornerResidualMillimeters)
                        + estimate.note
                        + " · "
                        + contour.note
                    isAutoDetecting = false
                    rebuildExports()
                }
            } catch {
                await MainActor.run {
                    // Keep green square if the marker itself was found but later steps failed.
                    photoScaleEstimate = nil
                    if arucoOverlayCorners.count == 4 {
                        autoDetectNote = "Marker found (green square on photo), but outline/scale failed: "
                            + error.localizedDescription
                    } else {
                        arucoOverlayCorners = []
                        arucoOverlayMarkerID = nil
                        autoDetectNote = error.localizedDescription
                    }
                    isAutoDetecting = false
                }
            }
        }
    }

    private func exportArucoMarkerPNG() {
        let id = Int(arucoExpectedIDText.trimmingCharacters(in: .whitespacesAndNewlines)) ?? 0
        let markerID = max(0, min(49, id < 0 ? 0 : id))
        guard let url = ArUcoMarkerGenerator.exportPNG(markerID: markerID) else {
            autoDetectNote = "Could not export ArUco marker PNG."
            return
        }
        guard let window = NSApp.keyWindow else {
            NSWorkspace.shared.activateFileViewerSelecting([url])
            autoDetectNote = "Exported marker ID \(markerID) — measure the black square side accurately after printing."
            return
        }
        let panel = NSSavePanel()
        panel.allowedContentTypes = [.png]
        panel.nameFieldStringValue = "aruco_4x4_id\(markerID).png"
        panel.message = "Save printable DICT_4X4_50 marker. After printing, enter the measured black-square side length (mm)."
        panel.beginSheetModal(for: window) { response in
            guard response == .OK, let dest = panel.url else { return }
            do {
                if FileManager.default.fileExists(atPath: dest.path) {
                    try FileManager.default.removeItem(at: dest)
                }
                try FileManager.default.copyItem(at: url, to: dest)
                autoDetectNote = "Saved marker ID \(markerID). Measure the black square side and enter it above."
            } catch {
                autoDetectNote = error.localizedDescription
            }
        }
    }

    private func rebuildExports() {
        guard let r = result else { return }
        let us = computedUserScale()
        exportGcode = BasePerimeterExport.gcodeBlock(result: r, unitsMillimeters: useMillimeters, userScale: us)
        if let ideal = idealFitForResult(r) {
            exportIdealFitGcode = BasePerimeterExport.idealFitGcodeBlock(
                idealRing: ideal.points,
                result: r,
                unitsMillimeters: useMillimeters,
                userScale: us
            )
            autoExportIdealFitURL = PlotterGcodeAutoExport.writeIdealFit(gcode: exportIdealFitGcode)
        } else {
            exportIdealFitGcode = ""
            autoExportIdealFitURL = nil
        }
        if let dest = PlotterGcodeAutoExport.write(gcode: exportGcode) {
            autoExportURL = dest
            autoExportFailed = false
        } else {
            autoExportURL = nil
            autoExportFailed = true
        }
        let primaryXY = BasePerimeterExport.scaledGcodePlaneXY(result: r, unitsMillimeters: useMillimeters, userScale: us)
        if let plan = PolarPathExport.build(planeXY: primaryXY, rpm: Float(polarPlotterRPM)),
           let polarURL = PlotterGcodeAutoExport.writePolar(text: PolarPathExport.polarFileText(plan: plan)) {
            autoExportPolarURL = polarURL
            var notes: [String] = []
            notes.append(String(format: "Chord err max %.2f mm", plan.validation.maxChordErrorMm))
            if !plan.validation.passesRadialSpeed {
                notes.append(String(format: "⚠ max radial speed %.1f mm/s — lower RPM", plan.validation.maxRadialSpeedMmPerS))
            }
            if let w = plan.validation.rayQAWarning { notes.append(w) }
            polarExportNote = notes.joined(separator: " · ")
        } else {
            autoExportPolarURL = nil
            polarExportNote = ""
        }
    }

    private func choosePlotterExportFolder() {
        guard let window = NSApp.keyWindow else { return }
        let panel = NSOpenPanel()
        panel.canChooseFiles = false
        panel.canChooseDirectories = true
        panel.allowsMultipleSelection = false
        panel.message = "Select the firmware/test_patterns folder for StomaPlotter G-code."
        panel.prompt = "Choose"
        panel.beginSheetModal(for: window) { response in
            guard response == .OK, let url = panel.url else { return }
            PlotterGcodeAutoExport.saveExportDirectoryBookmark(from: url)
            rebuildExports()
        }
    }

    private func measureReference() {
        guard let src = activeUSDZ else { return }
        isMeasuring = true
        measureNote = ""
        extractionError = nil
        let pattern = referenceNameSubstring
        let isPicked = pickedUSDZ != nil

        Task {
            do {
                let workURL: URL
                if isPicked {
                    let accessing = src.startAccessingSecurityScopedResource()
                    guard accessing else {
                        throw NSError(domain: "BasePerimeter", code: 1, userInfo: [NSLocalizedDescriptionKey: "Could not access the selected file."])
                    }
                    defer { src.stopAccessingSecurityScopedResource() }
                    let temp = FileManager.default.temporaryDirectory
                        .appendingPathComponent("ref-measure-\(UUID().uuidString).usdz", isDirectory: false)
                    try FileManager.default.copyItem(at: src, to: temp)
                    workURL = temp
                } else {
                    workURL = src
                }

                let len = try ReferenceObjectMeasure.longestWorldAABBEdge(usdzURL: workURL, nameContains: pattern)
                if isPicked {
                    try? FileManager.default.removeItem(at: workURL)
                }
                await MainActor.run {
                    measuredMeshRefLength = len
                    measureNote = String(format: "Found subtree · longest AABB edge ≈ %.6f (scene units).", len)
                    isMeasuring = false
                    rebuildExports()
                }
            } catch {
                await MainActor.run {
                    measuredMeshRefLength = nil
                    measureNote = error.localizedDescription
                    isMeasuring = false
                }
            }
        }
    }

    private func measureArUcoInMesh() {
        guard let src = activeUSDZ else { return }
        let sideMM = Double(arucoMarkerSideMMText.replacingOccurrences(of: ",", with: ".")) ?? 0
        let expectedID = Int(arucoExpectedIDText.trimmingCharacters(in: .whitespacesAndNewlines)) ?? -1
        isMeasuring = true
        meshArUcoNote = "Orbiting virtual camera… (may take a few seconds)"
        meshArUcoDebugImage = nil
        meshArUcoLiveImage = nil
        meshArUcoLiveStatus = "Starting orbit…"
        meshArUcoLiveDetected = false
        extractionError = nil
        let isPicked = pickedUSDZ != nil

        Task {
            do {
                let workURL: URL
                if isPicked {
                    let accessing = src.startAccessingSecurityScopedResource()
                    guard accessing else {
                        throw NSError(
                            domain: "BasePerimeter",
                            code: 1,
                            userInfo: [NSLocalizedDescriptionKey: "Could not access the selected file."]
                        )
                    }
                    defer { src.stopAccessingSecurityScopedResource() }
                    let temp = FileManager.default.temporaryDirectory
                        .appendingPathComponent("aruco-orbit-\(UUID().uuidString).usdz", isDirectory: false)
                    try FileManager.default.copyItem(at: src, to: temp)
                    workURL = temp
                } else {
                    workURL = src
                }

                let measured = try await Task.detached(priority: .userInitiated) { () -> MeshArUcoOrbitResult in
                    try MeshArUcoOrbitDetector.measure(
                        usdzURL: workURL,
                        markerSideMillimeters: sideMM,
                        expectedID: expectedID,
                        onLiveFrame: { image, info in
                            Task { @MainActor in
                                meshArUcoLiveImage = image
                                meshArUcoLiveStatus = info.status
                                meshArUcoLiveDetected = info.detected
                            }
                        }
                    )
                }.value

                if isPicked {
                    try? FileManager.default.removeItem(at: workURL)
                }

                await MainActor.run {
                    meshArUcoResult = measured
                    meshArUcoDebugImage = measured.debugSnapshotPNG.flatMap { NSImage(data: $0) }
                    meshArUcoNote = measured.note
                        + String(format: " · scale %.5f m/scene", (sideMM / 1000.0) / Double(measured.meanSideScene))
                    meshArUcoLiveStatus = measured.note
                    meshArUcoLiveDetected = true
                    isMeasuring = false
                    rebuildExports()
                    // Re-extract so the marker AABB is excluded from the perimeter loop.
                    runPerimeterExtraction()
                }
            } catch {
                await MainActor.run {
                    meshArUcoResult = nil
                    if let orbitErr = error as? MeshArUcoOrbitError, let png = orbitErr.debugPNG {
                        meshArUcoDebugImage = NSImage(data: png)
                    }
                    meshArUcoNote = error.localizedDescription
                    meshArUcoLiveStatus = error.localizedDescription
                    meshArUcoLiveDetected = false
                    isMeasuring = false
                }
            }
        }
    }

    private func runPerimeterExclusionPattern() -> String? {
        guard excludeReferenceFromOutline else { return nil }
        let t = referenceNameSubstring.trimmingCharacters(in: .whitespacesAndNewlines)
        return t.isEmpty ? nil : t
    }

    private func runPerimeterExtraction() {
        guard let src = activeUSDZ else { return }
        extractionError = nil
        result = nil
        isExtracting = true
        let axis = upAxis
        let tilt = manualSliceTilt
        let exclude = runPerimeterExclusionPattern()
        let excludeAABB = meshArUcoExclusionAABB
        let isPicked = pickedUSDZ != nil
        let startFraction = max(0, min(Float(sliceOffsetPercent / 100), 1))
        let startPercentForError = sliceOffsetPercent

        Task {
            do {
                let workURL: URL
                if isPicked {
                    let accessing = src.startAccessingSecurityScopedResource()
                    guard accessing else {
                        throw NSError(domain: "BasePerimeter", code: 2, userInfo: [NSLocalizedDescriptionKey: "Could not access the selected file."])
                    }
                    defer { src.stopAccessingSecurityScopedResource() }
                    let temp = FileManager.default.temporaryDirectory
                        .appendingPathComponent("perimeter-\(UUID().uuidString).usdz", isDirectory: false)
                    try FileManager.default.copyItem(at: src, to: temp)
                    workURL = temp
                } else {
                    workURL = src
                }

                let r = try await Task.detached(priority: .userInitiated) {
                    let step = Float(0.025)
                    var offset = startFraction
                    var lastError: Error?
                    while offset <= 1.0001 {
                        do {
                            return try BasePerimeterExtractor.extract(
                                usdzURL: workURL,
                                upAxis: axis,
                                manualTilt: tilt,
                                sliceOffsetFraction: offset,
                                excludeObjectNameSubstring: exclude,
                                excludeWorldAABB: excludeAABB
                            )
                        } catch {
                            lastError = error
                            offset += step
                        }
                    }
                    let tail = lastError.map { $0.localizedDescription } ?? "Unknown error."
                    let startLabel = String(format: "%.1f", startPercentForError)
                    throw NSError(
                        domain: "BasePerimeter",
                        code: 3,
                        userInfo: [
                            NSLocalizedDescriptionKey:
                                "No closed outline from \(startLabel)% through 100% of height (2.5% steps). Last attempt: \(tail)"
                        ]
                    )
                }.value

                if isPicked {
                    try? FileManager.default.removeItem(at: workURL)
                }

                await MainActor.run {
                    result = r
                    sliceOffsetPercent = Double(r.sliceOffsetFraction) * 100
                    outlineValidationLines = OutlineValidationLines()
                    activeOutlineValidationLine = .line1
                    realObjectLine1MMText = ""
                    realObjectLine2MMText = ""
                    isExtracting = false
                    rebuildExports()
                }
            } catch {
                await MainActor.run {
                    extractionError = error.localizedDescription
                    isExtracting = false
                }
            }
        }
    }

    private func saveExport() {
        guard result != nil, !exportGcode.isEmpty else { return }
        guard let window = NSApp.keyWindow else { return }
        let panel = NSSavePanel()
        panel.allowedContentTypes = [.plainText]
        panel.nameFieldStringValue = "base_perimeter.gcode"
        panel.beginSheetModal(for: window) { response in
            guard response == .OK, let dest = panel.url else { return }
            try? exportGcode.write(to: dest, atomically: true, encoding: .utf8)
        }
    }
}
