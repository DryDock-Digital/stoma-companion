import AVFoundation
import CoreGraphics
import CoreMedia
import Foundation
import ImageIO
import UniformTypeIdentifiers

enum VideoFrameExporter {
    /// Shortest sampling interval (more frames per second of video).
    static let minIntervalSeconds = 0.03
    static let maxIntervalSeconds = 1.0
    static let defaultIntervalSeconds = 0.35

    static let minFrameCap = 100
    static let maxFrameCap = 500
    static let defaultFrameCap = 350

    static func clampInterval(_ seconds: Double) -> Double {
        min(maxIntervalSeconds, max(minIntervalSeconds, seconds))
    }

    static func clampFrameCap(_ count: Int) -> Int {
        min(maxFrameCap, max(minFrameCap, count))
    }

    static func exportJPEGFrames(
        videoURL: URL,
        outputFolder: URL,
        intervalSeconds: Double,
        maxFrames: Int,
        jpegQuality: CGFloat = 0.9,
        onProgress: (@Sendable (String) -> Void)? = nil
    ) async throws -> Int {
        let asset = AVURLAsset(url: videoURL)
        let duration = try await asset.load(.duration)
        let durationSeconds = CMTimeGetSeconds(duration)
        guard durationSeconds.isFinite, durationSeconds > 0.05 else {
            throw NSError(domain: "VideoFrameExporter", code: 1, userInfo: [NSLocalizedDescriptionKey: "Video has no usable duration."])
        }

        let interval = clampInterval(intervalSeconds)
        let frameCap = clampFrameCap(maxFrames)
        var sampleTimes: [CMTime] = []
        var t = 0.0
        while t < durationSeconds - 0.02, sampleTimes.count < frameCap {
            sampleTimes.append(CMTime(seconds: t, preferredTimescale: 600))
            t += interval
        }
        guard !sampleTimes.isEmpty else {
            throw NSError(domain: "VideoFrameExporter", code: 2, userInfo: [NSLocalizedDescriptionKey: "Interval is too long for this video length."])
        }

        let generator = AVAssetImageGenerator(asset: asset)
        generator.appliesPreferredTrackTransform = true
        generator.requestedTimeToleranceBefore = CMTime(seconds: 0.05, preferredTimescale: 600)
        generator.requestedTimeToleranceAfter = CMTime(seconds: 0.05, preferredTimescale: 600)
        generator.maximumSize = CGSize(width: 2560, height: 2560)

        // Exact first-frame still for top-down + quarter calibration (Companion picks this URL).
        do {
            let (cgImage, _) = try await generator.image(at: .zero)
            let calURL = outputFolder.appendingPathComponent("calibration_top.jpg", isDirectory: false)
            try writeJPEG(cgImage: cgImage, to: calURL, quality: jpegQuality)
        } catch {
            // Non-fatal: photogrammetry can still use frame_00000.jpg from the loop when t == 0.
        }

        var written = 0
        for (index, time) in sampleTimes.enumerated() {
            onProgress?("Extracting frame \(index + 1) / \(sampleTimes.count)…")
            do {
                let (cgImage, _) = try await generator.image(at: time)
                let fileURL = outputFolder.appendingPathComponent(String(format: "frame_%05d.jpg", written), isDirectory: false)
                try writeJPEG(cgImage: cgImage, to: fileURL, quality: jpegQuality)
                written += 1
            } catch {
                continue
            }
        }

        guard written >= 8 else {
            throw NSError(domain: "VideoFrameExporter", code: 3, userInfo: [NSLocalizedDescriptionKey: "Too few frames could be extracted (need at least 8). Try a shorter interval or a longer video."])
        }
        return written
    }

    /// Writes the video's first frame (t = 0) as JPEG — for calibration without full frame extraction.
    static func exportFirstFrameJPEG(
        videoURL: URL,
        outputURL: URL,
        jpegQuality: CGFloat = 0.92
    ) async throws {
        let asset = AVURLAsset(url: videoURL)
        let duration = try await asset.load(.duration)
        let durationSeconds = CMTimeGetSeconds(duration)
        guard durationSeconds.isFinite, durationSeconds > 0.01 else {
            throw NSError(
                domain: "VideoFrameExporter",
                code: 6,
                userInfo: [NSLocalizedDescriptionKey: "Video has no usable duration."]
            )
        }

        let generator = AVAssetImageGenerator(asset: asset)
        generator.appliesPreferredTrackTransform = true
        generator.requestedTimeToleranceBefore = .zero
        generator.requestedTimeToleranceAfter = CMTime(seconds: 0.05, preferredTimescale: 600)
        generator.maximumSize = CGSize(width: 2560, height: 2560)

        let (cgImage, _) = try await generator.image(at: .zero)
        try writeJPEG(cgImage: cgImage, to: outputURL, quality: jpegQuality)
    }

    private static func writeJPEG(cgImage: CGImage, to url: URL, quality: CGFloat) throws {
        guard let dest = CGImageDestinationCreateWithURL(url as CFURL, UTType.jpeg.identifier as CFString, 1, nil) else {
            throw NSError(domain: "VideoFrameExporter", code: 4, userInfo: [NSLocalizedDescriptionKey: "Could not create JPEG destination."])
        }
        let props = [kCGImageDestinationLossyCompressionQuality: quality] as CFDictionary
        CGImageDestinationAddImage(dest, cgImage, props)
        guard CGImageDestinationFinalize(dest) else {
            throw NSError(domain: "VideoFrameExporter", code: 5, userInfo: [NSLocalizedDescriptionKey: "Could not finalize JPEG."])
        }
    }
}
