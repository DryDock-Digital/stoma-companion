import AppKit
import Foundation

/// Generates printable OpenCV DICT_4X4_50 ArUco markers (same dictionary as `ArUcoDetectorBridge`).
enum ArUcoMarkerGenerator {
    /// OpenCV DICT_4X4_50 rotation-0 bit patterns (must match `ArUcoDetectorBridge.mm`).
    /// Must match `ArUcoDetectorBridge.mm` / OpenCV DICT_4X4_50 (dark cell = 1).
    private static let dict4x4_50: [UInt16] = [
        0x4ACD, 0xF065, 0xCCD2, 0x66B9, 0xAB61, 0x8632, 0x61D1, 0x3B0D,
        0x0125, 0x30A9, 0x066E, 0xEE58, 0xF148, 0xD5F0, 0xDB4E, 0xD9C1,
        0xB99A, 0x99FF, 0x93A1, 0x8950, 0x7974, 0x4FD4, 0x332A, 0x227D,
        0x01B8, 0x6B8E, 0x531B, 0x5AAB, 0xDEDC, 0xCB90, 0xBBEA, 0xA84D,
        0x6130, 0x0F34, 0xF751, 0xF6D6, 0xE78A, 0xFB00, 0xF209, 0xE3A5,
        0xE8E7, 0xD5D7, 0xCD73, 0xC74D, 0xDB17, 0xD114, 0xD2C0, 0xB49B,
        0xAFD1, 0xAFEC,
    ]

    static func makeImage(markerID: Int, sidePixels: Int = 512, quietZoneCells: Int = 1) -> NSImage? {
        guard markerID >= 0, markerID < dict4x4_50.count, sidePixels >= 64 else { return nil }
        let bits = dict4x4_50[markerID]
        let cells = 4 + 2 // data + border
        let totalCells = cells + quietZoneCells * 2
        let cellPx = max(1, sidePixels / totalCells)
        let imgSide = cellPx * totalCells

        let image = NSImage(size: NSSize(width: imgSide, height: imgSide))
        image.lockFocus()
        NSColor.white.setFill()
        NSRect(x: 0, y: 0, width: imgSide, height: imgSide).fill()

        for r in 0 ..< cells {
            for c in 0 ..< cells {
                let onBorder = r == 0 || c == 0 || r == cells - 1 || c == cells - 1
                var black = onBorder
                if !onBorder {
                    let br = r - 1
                    let bc = c - 1
                    let bitIndex = br * 4 + bc
                    let bit = (bits >> (15 - bitIndex)) & 1
                    black = bit == 1
                }
                if black {
                    NSColor.black.setFill()
                    let x = (c + quietZoneCells) * cellPx
                    // Draw in top-left image space: AppKit is bottom-up, so flip.
                    let yTop = (r + quietZoneCells) * cellPx
                    let y = imgSide - yTop - cellPx
                    NSRect(x: x, y: y, width: cellPx, height: cellPx).fill()
                }
            }
        }
        image.unlockFocus()
        return image
    }

    static func exportPNG(markerID: Int, sidePixels: Int = 1024) -> URL? {
        guard let image = makeImage(markerID: markerID, sidePixels: sidePixels),
              let tiff = image.tiffRepresentation,
              let rep = NSBitmapImageRep(data: tiff),
              let png = rep.representation(using: .png, properties: [:]) else { return nil }
        let url = FileManager.default.temporaryDirectory
            .appendingPathComponent("aruco_4x4_id\(markerID).png", isDirectory: false)
        do {
            try png.write(to: url)
            return url
        } catch {
            return nil
        }
    }
}
