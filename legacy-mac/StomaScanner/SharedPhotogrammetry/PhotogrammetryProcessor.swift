import Combine
import Foundation
import RealityKit

/// Runs RealityKit Object Capture (`PhotogrammetrySession`) on a folder of images.
@MainActor
final class PhotogrammetryProcessor: ObservableObject {
    @Published private(set) var statusText: String = ""
    @Published private(set) var progress: Double = 0
    @Published private(set) var outputURL: URL?
    @Published private(set) var lastError: String?
    @Published private(set) var isRunning: Bool = false

    private var activeSession: PhotogrammetrySession?

    static var isDeviceSupported: Bool {
        PhotogrammetrySession.isSupported
    }

    private static var unsupportedDeviceMessage: String {
        #if os(iOS)
        "This device does not support on-device Object Capture. Use an iPhone or iPad with LiDAR, or process the same image folder with Stoma Companion on a supported Mac."
        #elseif os(macOS)
        "This Mac cannot run PhotogrammetrySession. Try a recent Apple Silicon Mac with an up-to-date macOS."
        #else
        "Object Capture is not supported on this system."
        #endif
    }

    func cancel() {
        activeSession?.cancel()
        activeSession = nil
        if isRunning {
            statusText = "Cancelled"
            isRunning = false
        }
    }

    func reportPreparationError(_ message: String) {
        lastError = message
        statusText = "Failed"
    }

    func clearResultsForNewInput() {
        lastError = nil
        outputURL = nil
        progress = 0
        if !isRunning {
            statusText = ""
        }
    }

    /// Stops any active session and clears all published reconstruction state.
    func resetWorkflow() {
        cancel()
        lastError = nil
        outputURL = nil
        progress = 0
        statusText = ""
        isRunning = false
        activeSession = nil
    }

    func setPreparationStatus(_ text: String) {
        statusText = text
    }

    func reconstruct(inputFolder: URL) async {
        lastError = nil
        outputURL = nil
        progress = 0
        guard Self.isDeviceSupported else {
            lastError = Self.unsupportedDeviceMessage
            return
        }

        var isDir: ObjCBool = false
        guard FileManager.default.fileExists(atPath: inputFolder.path, isDirectory: &isDir), isDir.boolValue else {
            lastError = "Missing input image folder."
            return
        }

        let imageCount = (try? FileManager.default.contentsOfDirectory(atPath: inputFolder.path))?.filter { path in
            let ext = (path as NSString).pathExtension.lowercased()
            return ["jpg", "jpeg", "png", "heic", "heif", "tif", "tiff", "dng"].contains(ext)
        }.count ?? 0

        guard imageCount > 0 else {
            lastError = "No images found in the input folder."
            return
        }

        isRunning = true
        defer {
            isRunning = false
            activeSession = nil
            progress = 0
        }

        let parent = inputFolder.deletingLastPathComponent()
        let outputFile = parent.appendingPathComponent("model.usdz")

        do {
            statusText = "Starting photogrammetry (\(imageCount) images)…"
            progress = 0

            var configuration = PhotogrammetrySession.Configuration()
            configuration.featureSensitivity = .normal
            configuration.sampleOrdering = .unordered
            configuration.isObjectMaskingEnabled = true

            let session = try PhotogrammetrySession(input: inputFolder, configuration: configuration)
            activeSession = session

            let request = PhotogrammetrySession.Request.modelFile(url: outputFile)

            let outputsTask = Task {
                for try await output in session.outputs {
                    switch output {
                    case .processingComplete:
                        statusText = "Complete"
                        progress = 1
                    case let .requestProgress(_, fractionComplete):
                        progress = fractionComplete
                        statusText = String(format: "Reconstructing… %.0f%%", fractionComplete * 100)
                    case let .requestProgressInfo(_, info):
                        if let t = info.estimatedRemainingTime {
                            statusText = String(format: "Reconstructing… ~%.0fs left", t)
                        }
                    case let .requestComplete(_, result):
                        if case let .modelFile(url) = result {
                            outputURL = url
                        }
                    case let .requestError(_, error):
                        throw error
                    case .inputComplete:
                        statusText = "Processing images…"
                    case let .invalidSample(_, reason):
                        statusText = "Skipping invalid sample: \(reason)"
                    case .skippedSample:
                        break
                    case .automaticDownsampling:
                        statusText = "Downsampling input (resource limits)…"
                    case .processingCancelled:
                        statusText = "Cancelled"
                    case .stitchingIncomplete:
                        break
                    @unknown default:
                        break
                    }
                }
            }

            try session.process(requests: [request])
            try await outputsTask.value

            if outputURL == nil {
                outputURL = outputFile
            }
        } catch {
            lastError = error.localizedDescription
            statusText = "Failed"
        }
    }
}
