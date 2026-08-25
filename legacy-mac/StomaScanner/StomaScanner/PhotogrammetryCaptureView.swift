import PhotosUI
import SwiftUI

struct PhotogrammetryCaptureView: View {
    @Environment(\.dismiss) private var dismiss
    @StateObject private var processor = PhotogrammetryProcessor()
    @State private var selectedItems: [PhotosPickerItem] = []
    @State private var selectedVideoItems: [PhotosPickerItem] = []
    @State private var videoFrameInterval: Double = VideoFrameExporter.defaultIntervalSeconds
    @State private var videoMaxFrames: Int = VideoFrameExporter.defaultFrameCap
    @State private var showOutputPreview = false

    var body: some View {
        NavigationStack {
            Form {
                if !PhotogrammetryProcessor.isDeviceSupported {
                    Section {
                        Text(
                            "On-device Object Capture requires a supported iPhone or iPad with LiDAR. "
                                + "Process the same images or video on a Mac with Stoma Companion when supported."
                        )
                        .font(.footnote)
                        .foregroundStyle(.secondary)
                    }
                }

                Section {
                    Text(
                        "Select 20–350 overlapping photos of a static object. "
                            + "For scale later on Mac, include a separate named reference object in the same capture when possible."
                    )
                    .font(.footnote)
                    .foregroundStyle(.secondary)

                    PhotosPicker(
                        selection: $selectedItems,
                        maxSelectionCount: 350,
                        matching: .images
                    ) {
                        Label(
                            selectedItems.isEmpty ? "Choose photos" : "\(selectedItems.count) photos selected",
                            systemImage: "photo.on.rectangle.angled"
                        )
                    }
                    .disabled(processor.isRunning)

                    Button {
                        Task {
                            guard !selectedItems.isEmpty else { return }
                            do {
                                let folder = try await Self.exportPhotoItemsToTempFolder(selectedItems)
                                await processor.reconstruct(inputFolder: folder)
                            } catch {
                                processor.reportPreparationError(error.localizedDescription)
                            }
                        }
                    } label: {
                        if processor.isRunning {
                            ProgressView()
                                .frame(maxWidth: .infinity)
                        } else {
                            Text("Build USDZ")
                                .frame(maxWidth: .infinity)
                        }
                    }
                    .buttonStyle(.borderedProminent)
                    .disabled(!PhotogrammetryProcessor.isDeviceSupported || selectedItems.isEmpty || processor.isRunning)
                } header: {
                    Text("From photos")
                }

                Section {
                    Text(
                        "Pick one video (slow pan). Frames are sampled at the interval below."
                    )
                    .font(.footnote)
                    .foregroundStyle(.secondary)

                    PhotosPicker(
                        selection: $selectedVideoItems,
                        maxSelectionCount: 1,
                        matching: .videos
                    ) {
                        Label(
                            selectedVideoItems.isEmpty ? "Choose video" : "Video selected",
                            systemImage: "video.fill"
                        )
                    }
                    .disabled(processor.isRunning)

                    VStack(alignment: .leading, spacing: 6) {
                        Text(String(format: "Frame interval: %.2f s", videoFrameInterval))
                            .font(.caption)
                        Slider(
                            value: $videoFrameInterval,
                            in: VideoFrameExporter.minIntervalSeconds...VideoFrameExporter.maxIntervalSeconds,
                            step: 0.01
                        )
                        Text(
                            "Shorter interval = more frames (min \(String(format: "%.2f", VideoFrameExporter.minIntervalSeconds)) s)."
                        )
                        .font(.caption2)
                        .foregroundStyle(.secondary)
                        Stepper("Max frames: \(videoMaxFrames)", value: $videoMaxFrames, in: VideoFrameExporter.minFrameCap...VideoFrameExporter.maxFrameCap, step: 25)
                            .font(.caption)
                    }
                    .disabled(processor.isRunning)

                    Button {
                        Task {
                            guard let item = selectedVideoItems.first else { return }
                            do {
                                processor.setPreparationStatus("Loading video…")
                                guard let movie = try await item.loadTransferable(type: PickableMovie.self) else {
                                    processor.reportPreparationError("Could not load the selected video.")
                                    return
                                }
                                let workDir = FileManager.default.temporaryDirectory
                                    .appendingPathComponent("ObjectCaptureVideo-\(UUID().uuidString)", isDirectory: true)
                                let inputFolder = workDir.appendingPathComponent("input", isDirectory: true)
                                try FileManager.default.createDirectory(at: inputFolder, withIntermediateDirectories: true)

                                let count = try await VideoFrameExporter.exportJPEGFrames(
                                    videoURL: movie.url,
                                    outputFolder: inputFolder,
                                    intervalSeconds: videoFrameInterval,
                                    maxFrames: videoMaxFrames,
                                    onProgress: { msg in
                                        Task { @MainActor in
                                            processor.setPreparationStatus(msg)
                                        }
                                    }
                                )
                                try? FileManager.default.removeItem(at: movie.url)

                                processor.setPreparationStatus("Extracted \(count) frames · starting photogrammetry…")
                                await processor.reconstruct(inputFolder: inputFolder)
                            } catch {
                                processor.reportPreparationError(error.localizedDescription)
                            }
                        }
                    } label: {
                        if processor.isRunning {
                            ProgressView()
                                .frame(maxWidth: .infinity)
                        } else {
                            Text("Extract frames & build USDZ")
                                .frame(maxWidth: .infinity)
                        }
                    }
                    .buttonStyle(.bordered)
                    .disabled(
                        !PhotogrammetryProcessor.isDeviceSupported
                            || selectedVideoItems.isEmpty
                            || processor.isRunning
                    )
                } header: {
                    Text("From video")
                }

                if processor.isRunning {
                    Section {
                        Button("Cancel", role: .cancel) {
                            processor.cancel()
                        }
                    }
                }

                if !processor.statusText.isEmpty || processor.progress > 0 {
                    Section("Progress") {
                        if processor.progress > 0 {
                            ProgressView(value: processor.progress)
                        }
                        Text(processor.statusText.isEmpty ? "…" : processor.statusText)
                            .font(.footnote)
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
                        Button("Preview model") {
                            showOutputPreview = true
                        }
                        ShareLink(item: url, subject: Text("Object capture"), message: Text("USDZ model")) {
                            Label("Share USDZ", systemImage: "square.and.arrow.up")
                        }
                    }
                }
            }
            .navigationTitle("Object capture")
            .toolbar {
                ToolbarItem(placement: .cancellationAction) {
                    Button("Close") { dismiss() }
                }
            }
            .sheet(isPresented: $showOutputPreview) {
                if let url = processor.outputURL {
                    NavigationStack {
                        USDZQuickLookPreview(url: url)
                            .ignoresSafeArea()
                            .navigationTitle("Preview")
                            .toolbar {
                                ToolbarItem(placement: .cancellationAction) {
                                    Button("Done") { showOutputPreview = false }
                                }
                            }
                    }
                }
            }
        }
    }

    private static func exportPhotoItemsToTempFolder(_ items: [PhotosPickerItem]) async throws -> URL {
        let workDir = FileManager.default.temporaryDirectory
            .appendingPathComponent("ObjectCaptureInput-\(UUID().uuidString)", isDirectory: true)
        let inputFolder = workDir.appendingPathComponent("input", isDirectory: true)
        try FileManager.default.createDirectory(at: inputFolder, withIntermediateDirectories: true)

        for (index, item) in items.enumerated() {
            guard let data = try await item.loadTransferable(type: Data.self) else {
                throw NSError(domain: "PhotogrammetryCaptureView", code: 1, userInfo: [NSLocalizedDescriptionKey: "Could not load image \(index + 1)."])
            }
            let ext = preferredFileExtension(for: item) ?? "jpg"
            let fileURL = inputFolder.appendingPathComponent("capture_\(index).\(ext)", isDirectory: false)
            try data.write(to: fileURL, options: .atomic)
        }
        return inputFolder
    }

    private static func preferredFileExtension(for item: PhotosPickerItem) -> String? {
        for type in item.supportedContentTypes {
            if let ext = type.preferredFilenameExtension {
                return ext
            }
        }
        return nil
    }
}
