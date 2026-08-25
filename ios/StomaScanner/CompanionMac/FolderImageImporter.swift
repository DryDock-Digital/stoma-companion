import Foundation

/// Copies supported image files from a user-chosen folder into a flat `input` directory for `PhotogrammetrySession`.
enum FolderImageImporter {
    private static let extensions = ["jpg", "jpeg", "png", "heic", "heif", "tif", "tiff", "dng"]

    static func copyFlatImages(from sourceFolder: URL, to inputFolder: URL) throws -> Int {
        let fm = FileManager.default
        try fm.createDirectory(at: inputFolder, withIntermediateDirectories: true)

        guard let enumerator = fm.enumerator(
            at: sourceFolder,
            includingPropertiesForKeys: [.isRegularFileKey],
            options: [.skipsHiddenFiles]
        ) else {
            throw NSError(
                domain: "FolderImageImporter",
                code: 1,
                userInfo: [NSLocalizedDescriptionKey: "Could not read the selected folder."]
            )
        }

        var files: [(url: URL, ext: String)] = []
        while let item = enumerator.nextObject() as? URL {
            let ext = item.pathExtension.lowercased()
            guard Self.extensions.contains(ext) else { continue }
            var isDir: ObjCBool = false
            guard fm.fileExists(atPath: item.path, isDirectory: &isDir), !isDir.boolValue else { continue }
            files.append((item, ext))
        }

        files.sort { $0.url.path < $1.url.path }

        guard !files.isEmpty else {
            throw NSError(
                domain: "FolderImageImporter",
                code: 2,
                userInfo: [NSLocalizedDescriptionKey: "No supported images (JPEG, PNG, HEIC, TIFF, DNG) were found in that folder."]
            )
        }

        for (index, pair) in files.enumerated() {
            let dest = inputFolder.appendingPathComponent("capture_\(index).\(pair.ext)", isDirectory: false)
            if fm.fileExists(atPath: dest.path) {
                try fm.removeItem(at: dest)
            }
            try fm.copyItem(at: pair.url, to: dest)
        }

        return files.count
    }
}
