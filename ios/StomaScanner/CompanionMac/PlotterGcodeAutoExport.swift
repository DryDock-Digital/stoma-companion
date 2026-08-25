import Foundation

/// Writes perimeter G-code into `firmware/test_patterns/` for StomaPlotter / `send_gcode.py`.
enum PlotterGcodeAutoExport {
    /// Overwritten on each successful perimeter export (stream this file to the plotter).
    static let fileName = "base_perimeter.gcode"
    static let idealFitFileName = "ideal_fit.gcode"
    static let polarFileName = PolarPathExport.polarFileName

    private static let bookmarkKey = "plotterGcodeExportDirectoryBookmark"

    /// Resolves `…/Module2/firmware/test_patterns` (or `STOMA_REPO_ROOT` override).
    static func resolveTestPatternsDirectory() -> URL? {
        if let root = ProcessInfo.processInfo.environment["STOMA_REPO_ROOT"], !root.isEmpty {
            let candidate = URL(fileURLWithPath: root, isDirectory: true)
                .appendingPathComponent("firmware/test_patterns", isDirectory: true)
            if FileManager.default.fileExists(atPath: candidate.path) {
                return candidate
            }
        }

        if let bookmarked = resolveBookmarkedDirectory() {
            return bookmarked
        }

        var url = URL(fileURLWithPath: #filePath).deletingLastPathComponent()
        for _ in 0 ..< 10 {
            let candidate = url.appendingPathComponent("firmware/test_patterns", isDirectory: true)
            if FileManager.default.fileExists(atPath: candidate.path) {
                return candidate
            }
            let parent = url.deletingLastPathComponent()
            if parent.path == url.path { break }
            url = parent
        }
        return nil
    }

    @discardableResult
    static func write(gcode: String, fileName: String = fileName) -> URL? {
        guard let dir = resolveTestPatternsDirectory() else { return nil }
        let fm = FileManager.default
        do {
            try fm.createDirectory(at: dir, withIntermediateDirectories: true)
            let dest = dir.appendingPathComponent(fileName, isDirectory: false)
            try gcode.write(to: dest, atomically: true, encoding: .utf8)
            saveBookmark(for: dir)
            return dest
        } catch {
            return nil
        }
    }

    @discardableResult
    static func writeIdealFit(gcode: String) -> URL? {
        write(gcode: gcode, fileName: idealFitFileName)
    }

    @discardableResult
    static func writePolar(text: String) -> URL? {
        write(gcode: text, fileName: polarFileName)
    }

    static func saveExportDirectoryBookmark(from url: URL) {
        saveBookmark(for: url)
    }

    private static func resolveBookmarkedDirectory() -> URL? {
        guard let data = UserDefaults.standard.data(forKey: bookmarkKey) else { return nil }
        var stale = false
        guard let url = try? URL(
            resolvingBookmarkData: data,
            options: [.withSecurityScope],
            bookmarkDataIsStale: &stale
        ) else { return nil }
        if stale, let refreshed = try? url.bookmarkData(options: .withSecurityScope) {
            UserDefaults.standard.set(refreshed, forKey: bookmarkKey)
        }
        guard url.hasDirectoryPath else { return nil }
        let accessing = url.startAccessingSecurityScopedResource()
        defer {
            if accessing { url.stopAccessingSecurityScopedResource() }
        }
        return fmExists(url) ? url : nil
    }

    private static func fmExists(_ url: URL) -> Bool {
        var isDir: ObjCBool = false
        return FileManager.default.fileExists(atPath: url.path, isDirectory: &isDir) && isDir.boolValue
    }

    private static func saveBookmark(for dir: URL) {
        guard let data = try? dir.bookmarkData(options: .withSecurityScope) else { return }
        UserDefaults.standard.set(data, forKey: bookmarkKey)
    }
}
