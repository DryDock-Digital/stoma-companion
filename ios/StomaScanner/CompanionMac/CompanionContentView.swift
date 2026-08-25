import AppKit
import SwiftUI
import UniformTypeIdentifiers

private enum PhotogrammetryInputKind: String, CaseIterable, Identifiable {
    case images
    case video

    var id: String { rawValue }

    var title: String {
        switch self {
        case .images: "Images"
        case .video: "Video"
        }
    }
}

/// Single `fileImporter` mode — stacking multiple `.fileImporter` on one view breaks folder/video/mesh pickers on macOS.
private enum CompanionFileImport: Equatable {
    case imageFolder
    case video
    case meshUSDZ

    var allowedContentTypes: [UTType] {
        switch self {
        case .imageFolder:
            #if os(macOS)
            return [.directory, .folder]
            #else
            return [.folder]
            #endif
        case .video:
            return [.movie, .mpeg4Movie, .quickTimeMovie, .video]
        case .meshUSDZ:
            return [.usdz]
        }
    }
}

struct CompanionContentView: View {
    @StateObject private var processor = PhotogrammetryProcessor()
    @State private var photogrammetryInput: PhotogrammetryInputKind = .video

    @State private var showFileImporter = false
    /// Kind of import for the open panel; kept separate from `showFileImporter` so SwiftUI cannot clear it
    /// in the `isPresented` setter before `onCompletion` runs (that race made video/folder picks no-op on macOS).
    @State private var fileImportKind: CompanionFileImport?

    /// Covers frame extraction + photogrammetry so the video action shows a spinner for the whole pipeline.
    @State private var videoWorkflowBusy = false
    @State private var imagePhotogrammetryBusy = false

    @State private var workRoot: URL?
    @State private var preparedImageFolder: URL?
    @State private var preparedImageCount = 0
    @State private var chosenFolderPath: String?

    @State private var pendingVideoSandboxURL: URL?
    @State private var chosenVideoPath: String?
    @State private var videoFrameInterval: Double = 0.1
    @State private var videoMaxFrames: Int = VideoFrameExporter.defaultFrameCap

    /// User-picked USDZ (copied to temp); used for perimeter tools when set. Otherwise last photogrammetry output.
    @State private var standaloneUSDZ: URL?
    @State private var standaloneUSDZPath: String?

    private var hasImageSetReady: Bool { preparedImageFolder != nil && pendingVideoSandboxURL == nil }

    /// Mesh URL for base-perimeter tools: explicit import wins over photogrammetry output.
    private var meshURLForTools: URL? { standaloneUSDZ ?? processor.outputURL }

    /// Prefer `calibration_top.jpg` (first frame at t=0) when present, else first extracted frame.
    private var defaultCalibrationStillURL: URL? {
        guard let folder = preparedImageFolder else { return nil }
        let calib = folder.appendingPathComponent("calibration_top.jpg", isDirectory: false)
        if FileManager.default.fileExists(atPath: calib.path) { return calib }
        let frame0 = folder.appendingPathComponent("frame_00000.jpg", isDirectory: false)
        if FileManager.default.fileExists(atPath: frame0.path) { return frame0 }
        return nil
    }

    private var runBusy: Bool { processor.isRunning || videoWorkflowBusy || imagePhotogrammetryBusy }

    var body: some View {
        NavigationStack {
            Form {
                if !PhotogrammetryProcessor.isDeviceSupported {
                    Section {
                        Text(
                            "PhotogrammetrySession is not supported on this Mac. "
                                + "Apple documents support on recent Apple Silicon systems with sufficient GPU memory."
                        )
                        .font(.footnote)
                        .foregroundStyle(.secondary)
                    }
                }

                Section {
                    Text(
                        "Build a USDZ from overlapping photos or from a video, then measure and export the base perimeter below. "
                            + "For **scale**, prefer a **top-down still with a printed ArUco DICT_4X4_50 marker** "
                            + "(after video extract, `calibration_top.jpg` loads by default). "
                            + "Coin single-diameter and named USDZ references remain as fallbacks."
                    )
                    .font(.footnote)
                    .foregroundStyle(.secondary)

                    Picker("Input type", selection: $photogrammetryInput) {
                        ForEach(PhotogrammetryInputKind.allCases) { kind in
                            Text(kind.title).tag(kind)
                        }
                    }
                    .pickerStyle(.segmented)
                    .disabled(runBusy)

                    switch photogrammetryInput {
                    case .images:
                        imagesPhotogrammetryContent
                    case .video:
                        videoPhotogrammetryContent
                    }

                    runPhotogrammetryButton

                    if hasAnyImportState || processor.outputURL != nil || processor.isRunning || runBusy {
                        Button("Reset import & start over", role: .destructive) {
                            resetEntireWorkflow()
                        }
                    }
                } header: {
                    Text("Photogrammetry")
                } footer: {
                    switch photogrammetryInput {
                    case .images:
                        Text("Copy a folder of images (JPEG/HEIC/PNG, etc.) from Finder or AirDrop.")
                            .font(.footnote)
                    case .video:
                        Text(
                            "Start from a **bird’s-eye** view with an ArUco marker (or coin) in frame if you plan photo calibration; "
                                + "the exporter saves `calibration_top.jpg` at the first instant and numbered frames at your interval."
                        )
                        .font(.footnote)
                    }
                }

                Section {
                    Text(
                        "Skip reconstruction if you already have a **.usdz** mesh. This file is used for reference scale and base perimeter; "
                            + "you can still open a different USDZ from inside the perimeter section."
                    )
                    .font(.footnote)
                    .foregroundStyle(.secondary)

                    Button("Choose USDZ…") {
                        prepareForNewFileImport()
                        fileImportKind = .meshUSDZ
                        showFileImporter = true
                    }

                    if let path = standaloneUSDZPath {
                        LabeledContent("Mesh file") {
                            Text(path)
                                .font(.caption)
                                .foregroundStyle(.secondary)
                                .textSelection(.enabled)
                        }
                    }

                    if standaloneUSDZ != nil {
                        Button("Clear imported mesh", role: .destructive) {
                            clearStandaloneUSDZ()
                        }
                    }
                } header: {
                    Text("Existing mesh (USDZ)")
                }

                if processor.isRunning {
                    Section {
                        Button("Cancel", role: .cancel) {
                            processor.cancel()
                        }
                    }
                }

                if processor.isRunning || !processor.statusText.isEmpty {
                    Section(processor.isRunning ? "Progress" : "Status") {
                        if processor.isRunning {
                            // At exactly 100%, `progress == 1` must not use an indeterminate `ProgressView()` — that
                            // keeps spinning until `isRunning` flips after the session output stream ends.
                            if processor.progress > 0, processor.progress < 1 {
                                ProgressView(value: processor.progress)
                                    .frame(maxWidth: 360)
                            } else if processor.progress >= 1 {
                                ProgressView(value: 1)
                                    .frame(maxWidth: 360)
                            } else {
                                ProgressView()
                                    .controlSize(.small)
                                    .frame(maxWidth: 360, alignment: .leading)
                            }
                        }
                        if !processor.statusText.isEmpty {
                            Text(processor.statusText)
                                .font(.footnote)
                        }
                    }
                }

                if let err = processor.lastError {
                    Section("Error") {
                        Text(err)
                            .foregroundStyle(.red)
                            .font(.footnote)
                    }
                }

                if let url = processor.outputURL {
                    Section("Result") {
                        Button("Reveal photogrammetry USDZ in Finder") {
                            NSWorkspace.shared.activateFileViewerSelecting([url])
                        }
                    }
                }

                BasePerimeterToolView(
                    builtInUSDZURL: meshURLForTools,
                    defaultCalibrationImageURL: defaultCalibrationStillURL
                )
            }
            .formStyle(.grouped)
            .navigationTitle("Stoma Companion")
            .fileImporter(
                isPresented: $showFileImporter,
                allowedContentTypes: fileImportKind?.allowedContentTypes ?? [.item],
                allowsMultipleSelection: false
            ) { result in
                let capturedKind = fileImportKind
                fileImportKind = nil
                showFileImporter = false
                switch result {
                case .success(let urls):
                    guard let url = urls.first, let importKind = capturedKind else { return }
                    switch importKind {
                    case .imageFolder:
                        Task { await importChosenFolder(url) }
                    case .video:
                        Task { await importChosenVideo(url) }
                    case .meshUSDZ:
                        Task { await importStandaloneUSDZ(url) }
                    }
                case .failure(let error):
                    if (error as NSError).code != NSUserCancelledError {
                        processor.reportPreparationError(error.localizedDescription)
                    }
                }
            }
        }
    }

    private var hasAnyImportState: Bool {
        preparedImageFolder != nil
            || pendingVideoSandboxURL != nil
            || chosenFolderPath != nil
            || chosenVideoPath != nil
            || standaloneUSDZ != nil
    }

    @ViewBuilder
    private var imagesPhotogrammetryContent: some View {
        Button {
            prepareForNewFileImport()
            fileImportKind = .imageFolder
            showFileImporter = true
        } label: {
            Label(
                preparedImageFolder == nil && chosenFolderPath == nil
                    ? "Choose image folder…"
                    : "Choose a different image folder…",
                systemImage: "folder"
            )
        }

        if let path = chosenFolderPath, pendingVideoSandboxURL == nil {
            LabeledContent("Folder") {
                Text(path)
                    .font(.caption)
                    .foregroundStyle(.secondary)
                    .textSelection(.enabled)
            }
        }

        if hasImageSetReady {
            LabeledContent("Images copied") {
                Text("\(preparedImageCount)")
                    .font(.caption)
            }
        }
    }

    @ViewBuilder
    private var videoPhotogrammetryContent: some View {
        Button {
            prepareForNewFileImport()
            fileImportKind = .video
            showFileImporter = true
        } label: {
            Label(
                pendingVideoSandboxURL == nil ? "Choose video…" : "Choose a different video…",
                systemImage: "video.fill"
            )
        }

        if let path = chosenVideoPath {
            LabeledContent("Video") {
                Text(path)
                    .font(.caption)
                    .foregroundStyle(.secondary)
                    .textSelection(.enabled)
            }
        }

        VStack(alignment: .leading, spacing: 6) {
            Text(String(format: "Frame interval: %.2f s", videoFrameInterval))
                .font(.caption)
            Slider(
                value: $videoFrameInterval,
                in: VideoFrameExporter.minIntervalSeconds...VideoFrameExporter.maxIntervalSeconds,
                step: 0.01
            )
            .frame(maxWidth: 360)
            Text(
                "Shorter interval extracts more frames (down to \(String(format: "%.2f", VideoFrameExporter.minIntervalSeconds)) s). "
                    + "Cap limits total frames sent to photogrammetry."
            )
            .font(.caption2)
            .foregroundStyle(.secondary)
            Stepper("Max frames: \(videoMaxFrames)", value: $videoMaxFrames, in: VideoFrameExporter.minFrameCap...VideoFrameExporter.maxFrameCap, step: 25)
                .font(.caption)
        }
        .frame(maxWidth: .infinity, alignment: .leading)
        .disabled(!PhotogrammetryProcessor.isDeviceSupported)
    }

    @ViewBuilder
    private var runPhotogrammetryButton: some View {
        switch photogrammetryInput {
        case .images:
            Button {
                Task { @MainActor in
                    guard let input = preparedImageFolder else { return }
                    clearStandaloneUSDZ()
                    imagePhotogrammetryBusy = true
                    defer { imagePhotogrammetryBusy = false }
                    await processor.reconstruct(inputFolder: input)
                }
            } label: {
                HStack(spacing: 8) {
                    if imagePhotogrammetryBusy {
                        ProgressView()
                            .controlSize(.small)
                    }
                    Text("Run photogrammetry")
                }
                .frame(maxWidth: 280, alignment: .leading)
            }
            .buttonStyle(.borderedProminent)
            .disabled(
                !PhotogrammetryProcessor.isDeviceSupported
                    || !hasImageSetReady
                    || runBusy
            )
        case .video:
            Button {
                Task { @MainActor in
                    videoWorkflowBusy = true
                    defer { videoWorkflowBusy = false }
                    await extractVideoFramesAndReconstruct()
                }
            } label: {
                HStack(spacing: 8) {
                    if videoWorkflowBusy {
                        ProgressView()
                            .controlSize(.small)
                    }
                    Text("Extract frames & run photogrammetry")
                }
                .frame(maxWidth: 280, alignment: .leading)
            }
            .buttonStyle(.borderedProminent)
            .disabled(
                !PhotogrammetryProcessor.isDeviceSupported
                    || pendingVideoSandboxURL == nil
                    || runBusy
            )
        }
    }

    /// Cancels an in-flight reconstruction so a new file pick can replace the current workflow.
    private func prepareForNewFileImport() {
        if processor.isRunning {
            processor.cancel()
        }
        videoWorkflowBusy = false
        imagePhotogrammetryBusy = false
    }

    @MainActor
    private func resetEntireWorkflow() {
        prepareForNewFileImport()
        processor.resetWorkflow()
        clearStandaloneUSDZ()
        resetPhotogrammetryInputSelection()
    }

    @MainActor
    private func resetPhotogrammetryInputSelection() {
        discardWorkRoot()
        preparedImageFolder = nil
        preparedImageCount = 0
        chosenFolderPath = nil
        pendingVideoSandboxURL = nil
        chosenVideoPath = nil
    }

    @MainActor
    private func importStandaloneUSDZ(_ url: URL) async {
        prepareForNewFileImport()
        processor.clearResultsForNewInput()
        let accessing = url.startAccessingSecurityScopedResource()
        guard accessing else {
            processor.reportPreparationError("Could not access the selected USDZ.")
            return
        }
        defer {
            url.stopAccessingSecurityScopedResource()
        }

        do {
            clearStandaloneUSDZ()
            let dest = FileManager.default.temporaryDirectory
                .appendingPathComponent("companion-import-\(UUID().uuidString).usdz", isDirectory: false)
            try FileManager.default.copyItem(at: url, to: dest)
            standaloneUSDZ = dest
            standaloneUSDZPath = url.path
            processor.setPreparationStatus("Imported mesh · ready for perimeter tools.")
        } catch {
            processor.reportPreparationError(error.localizedDescription)
        }
    }

    private func clearStandaloneUSDZ() {
        if let u = standaloneUSDZ {
            try? FileManager.default.removeItem(at: u)
        }
        standaloneUSDZ = nil
        standaloneUSDZPath = nil
    }

    @MainActor
    private func importChosenFolder(_ url: URL) async {
        prepareForNewFileImport()
        processor.clearResultsForNewInput()
        clearStandaloneUSDZ()
        let accessing = url.startAccessingSecurityScopedResource()
        defer {
            if accessing {
                url.stopAccessingSecurityScopedResource()
            }
        }

        do {
            discardWorkRoot()
            pendingVideoSandboxURL = nil
            chosenVideoPath = nil
            preparedImageFolder = nil
            preparedImageCount = 0
            chosenFolderPath = url.path

            let root = try makeNewWorkRoot()
            let input = root.appendingPathComponent("input", isDirectory: true)

            let count = try FolderImageImporter.copyFlatImages(from: url, to: input)
            workRoot = root
            preparedImageFolder = input
            preparedImageCount = count
            processor.setPreparationStatus("Copied \(count) images · ready to run photogrammetry.")
        } catch {
            processor.reportPreparationError(error.localizedDescription)
            chosenFolderPath = nil
        }
    }

    @MainActor
    private func importChosenVideo(_ url: URL) async {
        prepareForNewFileImport()
        processor.clearResultsForNewInput()
        clearStandaloneUSDZ()
        let accessing = url.startAccessingSecurityScopedResource()
        defer {
            if accessing {
                url.stopAccessingSecurityScopedResource()
            }
        }

        do {
            discardWorkRoot()
            preparedImageFolder = nil
            preparedImageCount = 0
            chosenFolderPath = nil
            chosenVideoPath = url.path

            let root = try makeNewWorkRoot()

            let ext = url.pathExtension.isEmpty ? "mov" : url.pathExtension
            let dest = root.appendingPathComponent("source_video.\(ext)", isDirectory: false)
            let fm = FileManager.default
            if fm.fileExists(atPath: dest.path) {
                try fm.removeItem(at: dest)
            }
            try fm.copyItem(at: url, to: dest)

            workRoot = root
            pendingVideoSandboxURL = dest
            processor.setPreparationStatus("Video copied · ready to extract frames.")
        } catch {
            processor.reportPreparationError(error.localizedDescription)
            chosenVideoPath = nil
            pendingVideoSandboxURL = nil
        }
    }

    @MainActor
    private func extractVideoFramesAndReconstruct() async {
        guard let movieURL = pendingVideoSandboxURL, let root = workRoot else { return }
        processor.clearResultsForNewInput()
        clearStandaloneUSDZ()

        let inputFolder = root.appendingPathComponent("input", isDirectory: true)
        if FileManager.default.fileExists(atPath: inputFolder.path) {
            try? FileManager.default.removeItem(at: inputFolder)
        }

        do {
            try FileManager.default.createDirectory(at: inputFolder, withIntermediateDirectories: true)
            processor.setPreparationStatus("Extracting frames from video…")
            let count = try await VideoFrameExporter.exportJPEGFrames(
                videoURL: movieURL,
                outputFolder: inputFolder,
                intervalSeconds: videoFrameInterval,
                maxFrames: videoMaxFrames,
                onProgress: { msg in
                    Task { @MainActor in
                        processor.setPreparationStatus(msg)
                    }
                }
            )
            try? FileManager.default.removeItem(at: movieURL)
            pendingVideoSandboxURL = nil
            preparedImageFolder = inputFolder
            preparedImageCount = count
            chosenFolderPath = nil

            processor.setPreparationStatus("Extracted \(count) frames · starting photogrammetry…")
            await processor.reconstruct(inputFolder: inputFolder)
        } catch {
            processor.reportPreparationError(error.localizedDescription)
        }
    }

    private func makeNewWorkRoot() throws -> URL {
        let fm = FileManager.default
        let support = fm.urls(for: .applicationSupportDirectory, in: .userDomainMask).first
            ?? fm.temporaryDirectory
        let bundleFolder = (Bundle.main.bundleIdentifier ?? "StomaCompanion")
            .replacingOccurrences(of: "/", with: "-")
        let root = support
            .appendingPathComponent(bundleFolder, isDirectory: true)
            .appendingPathComponent("PhotogrammetryWork-\(UUID().uuidString)", isDirectory: true)
        try fm.createDirectory(at: root, withIntermediateDirectories: true)
        return root
    }

    private func discardWorkRoot() {
        if let old = workRoot {
            try? FileManager.default.removeItem(at: old)
        }
        workRoot = nil
    }
}
