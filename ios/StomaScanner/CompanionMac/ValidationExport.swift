import AppKit
import Foundation
import SwiftUI

struct ValidationSummary: Codable {
    struct SW01: Codable {
        let realLine1MM: Double?
        let realLine2MM: Double?
        let measuredLine1MM: Float?
        let measuredLine2MM: Float?
        let line1ErrorMM: Double?
        let line2ErrorMM: Double?
        let line1PercentError: Double?
        let line2PercentError: Double?
    }

    struct SW07: Codable {
        let targetClearanceMM: Float
        let toleranceMM: Float
        let minClearanceMM: Float
        let meanClearanceMM: Float
        let maxClearanceMM: Float
        let p95ClearanceMM: Float
        let passes: Bool
    }

    struct SliceMeta: Codable {
        let sliceOffsetFraction: Float
        let axisU: [Float]
        let axisV: [Float]
        let planeNormal: [Float]
        let floorMethod: String
        let floorHSceneUnits: Float
        let vertexMinHSceneUnits: Float
        let floorPlaneRMSE: Float?
        let floorConfidence: Float
        let floorCandidateCount: Int
        let floorDownwardAreaFraction: Float
        let floorTiltDegFromWorldY: Float
    }

    let timestamp: String
    let usdzName: String?
    let unitsMillimeters: Bool
    let userScale: Float
    let sw01: SW01
    let sw07: SW07?
    let slice: SliceMeta
}

enum ValidationExport {
    static func sw01Metrics(
        validationLines: OutlineValidationLines,
        realLine1MM: Double?,
        realLine2MM: Double?,
        unitsMillimeters: Bool
    ) -> ValidationSummary.SW01 {
        let toMM: Float = unitsMillimeters ? 1 : 1000
        let measured1 = validationLines.length(kind: .line1).map { $0 * toMM }
        let measured2 = validationLines.length(kind: .line2).map { $0 * toMM }
        let line1Err = realLine1MM.flatMap { real in measured1.map { Double($0) - real } }
        let line2Err = realLine2MM.flatMap { real in measured2.map { Double($0) - real } }
        let line1Pct = realLine1MM.flatMap { real in
            line1Err.flatMap { real > 0 ? ($0 / real) * 100 : nil }
        }
        let line2Pct = realLine2MM.flatMap { real in
            line2Err.flatMap { real > 0 ? ($0 / real) * 100 : nil }
        }
        return ValidationSummary.SW01(
            realLine1MM: realLine1MM,
            realLine2MM: realLine2MM,
            measuredLine1MM: measured1,
            measuredLine2MM: measured2,
            line1ErrorMM: line1Err,
            line2ErrorMM: line2Err,
            line1PercentError: line1Pct,
            line2PercentError: line2Pct
        )
    }

    static func clearanceCSV(
        primary: [(Float, Float)],
        ideal: [(Float, Float)],
        stats: ClearanceStats
    ) -> String {
        var lines = ["index,primary_x,primary_y,ideal_x,ideal_y,clearance_mm,pass"]
        let lo = stats.targetMM - stats.toleranceMM
        let hi = stats.targetMM + stats.toleranceMM
        let n = min(primary.count, ideal.count, stats.perSample.count)
        for i in 0 ..< n {
            let d = stats.perSample[i]
            let pass = d >= lo && d <= hi
            let p = primary[i]
            let q = ideal[i]
            lines.append("\(i),\(p.0),\(p.1),\(q.0),\(q.1),\(d),\(pass)")
        }
        return lines.joined(separator: "\n") + "\n"
    }

    static func outlineCSV(label: String, points: [(Float, Float)]) -> String {
        var lines = ["index,x,y,label"]
        for (i, p) in points.enumerated() {
            lines.append("\(i),\(p.0),\(p.1),\(label)")
        }
        return lines.joined(separator: "\n") + "\n"
    }

    /// Single-row DVR-002 verification report for spreadsheet / evidence document (DVR-002 protocol 6a–6c).
    static func verificationReportCSV(
        summary: ValidationSummary,
        loopVertexCount: Int,
        outlineSampleCount: Int
    ) -> String {
        let header = [
            "timestamp_iso",
            "usdz_name",
            "user_linear_scale",
            "units_millimeters",
            "slice_offset_percent",
            "loop_vertex_count",
            "outline_sample_count",
            "sw01_real_line1_mm",
            "sw01_real_line2_mm",
            "sw01_measured_line1_mm",
            "sw01_measured_line2_mm",
            "sw01_line1_error_mm",
            "sw01_line2_error_mm",
            "sw01_line1_percent_error",
            "sw01_line2_percent_error",
            "sw07_target_clearance_mm",
            "sw07_tolerance_mm",
            "sw07_min_clearance_mm",
            "sw07_mean_clearance_mm",
            "sw07_max_clearance_mm",
            "sw07_p95_clearance_mm",
            "sw07_pass",
            "fr02_slice_qualitative_notes",
            "fr02_pass",
            "floor_method",
            "floor_h_scene_units",
            "vertex_min_h_scene_units",
            "floor_plane_rmse",
            "floor_confidence",
            "floor_candidate_count",
            "floor_downward_area_fraction",
            "floor_tilt_deg_from_world_y",
        ]

        let sw01 = summary.sw01
        let sw07 = summary.sw07
        let slice = summary.slice
        let slicePct = String(format: "%.2f", slice.sliceOffsetFraction * 100)

        let row: [String] = [
            csvField(summary.timestamp),
            csvField(summary.usdzName ?? ""),
            String(format: "%.6f", summary.userScale),
            summary.unitsMillimeters ? "true" : "false",
            slicePct,
            String(loopVertexCount),
            String(outlineSampleCount),
            formatOptionalDouble(sw01.realLine1MM),
            formatOptionalDouble(sw01.realLine2MM),
            sw01.measuredLine1MM.map { String(format: "%.4f", $0) } ?? "",
            sw01.measuredLine2MM.map { String(format: "%.4f", $0) } ?? "",
            formatOptionalDouble(sw01.line1ErrorMM, signed: true),
            formatOptionalDouble(sw01.line2ErrorMM, signed: true),
            formatOptionalDouble(sw01.line1PercentError, signed: true),
            formatOptionalDouble(sw01.line2PercentError, signed: true),
            sw07.map { String(format: "%.4f", $0.targetClearanceMM) } ?? "",
            sw07.map { String(format: "%.4f", $0.toleranceMM) } ?? "",
            sw07.map { String(format: "%.4f", $0.minClearanceMM) } ?? "",
            sw07.map { String(format: "%.4f", $0.meanClearanceMM) } ?? "",
            sw07.map { String(format: "%.4f", $0.maxClearanceMM) } ?? "",
            sw07.map { String(format: "%.4f", $0.p95ClearanceMM) } ?? "",
            sw07.map { $0.passes ? "true" : "false" } ?? "",
            "",
            "",
            csvField(slice.floorMethod),
            String(format: "%.6f", slice.floorHSceneUnits),
            String(format: "%.6f", slice.vertexMinHSceneUnits),
            slice.floorPlaneRMSE.map { String(format: "%.6f", $0) } ?? "",
            String(format: "%.4f", slice.floorConfidence),
            String(slice.floorCandidateCount),
            String(format: "%.6f", slice.floorDownwardAreaFraction),
            String(format: "%.4f", slice.floorTiltDegFromWorldY),
        ]

        return header.joined(separator: ",") + "\n" + row.joined(separator: ",") + "\n"
    }

    private static func csvField(_ value: String) -> String {
        if value.contains(",") || value.contains("\"") || value.contains("\n") {
            return "\"\(value.replacingOccurrences(of: "\"", with: "\"\""))\""
        }
        return value
    }

    private static func formatOptionalDouble(_ value: Double?, signed: Bool = false) -> String {
        guard let value else { return "" }
        if signed {
            return String(format: "%+.4f", value)
        }
        return String(format: "%.4f", value)
    }

    static func exportDVRBundle(
        to directory: URL,
        result: BasePerimeterResult,
        primary: [(Float, Float)],
        idealFit: IdealFitResult?,
        summary: ValidationSummary,
        outlinePreviewPNG: Data?
    ) throws {
        let fm = FileManager.default
        try fm.createDirectory(at: directory, withIntermediateDirectories: true)

        try outlineCSV(label: "primary", points: primary).write(
            to: directory.appendingPathComponent("outline_primary.csv"),
            atomically: true,
            encoding: .utf8
        )

        if let ideal = idealFit {
            try outlineCSV(label: "ideal_fit", points: ideal.points).write(
                to: directory.appendingPathComponent("outline_ideal_fit.csv"),
                atomically: true,
                encoding: .utf8
            )
            try clearanceCSV(primary: primary, ideal: ideal.points, stats: ideal.clearanceStats).write(
                to: directory.appendingPathComponent("clearance_report.csv"),
                atomically: true,
                encoding: .utf8
            )
        }

        try verificationReportCSV(
            summary: summary,
            loopVertexCount: result.loopVertexCount,
            outlineSampleCount: primary.count
        ).write(
            to: directory.appendingPathComponent("verification_report.csv"),
            atomically: true,
            encoding: .utf8
        )

        let encoder = JSONEncoder()
        encoder.outputFormatting = [.prettyPrinted, .sortedKeys]
        let jsonData = try encoder.encode(summary)
        try jsonData.write(to: directory.appendingPathComponent("validation_summary.json"))

        if let png = outlinePreviewPNG {
            try png.write(to: directory.appendingPathComponent("outline_2d_preview.png"))
        }
    }

    static func makeSummary(
        result: BasePerimeterResult,
        primary: [(Float, Float)],
        idealFit: IdealFitResult?,
        validationLines: OutlineValidationLines,
        realLine1MM: Double?,
        realLine2MM: Double?,
        unitsMillimeters: Bool,
        userScale: Float,
        usdzName: String?
    ) -> ValidationSummary {
        let sw01 = sw01Metrics(
            validationLines: validationLines,
            realLine1MM: realLine1MM,
            realLine2MM: realLine2MM,
            unitsMillimeters: unitsMillimeters
        )
        let sw07: ValidationSummary.SW07? = idealFit.map { ideal in
            let s = ideal.clearanceStats
            return ValidationSummary.SW07(
                targetClearanceMM: s.targetMM,
                toleranceMM: s.toleranceMM,
                minClearanceMM: s.min,
                meanClearanceMM: s.mean,
                maxClearanceMM: s.max,
                p95ClearanceMM: s.p95,
                passes: ideal.passesSW07
            )
        }
        let iso = ISO8601DateFormatter().string(from: Date())
        let floor = result.floorDetection
        return ValidationSummary(
            timestamp: iso,
            usdzName: usdzName,
            unitsMillimeters: unitsMillimeters,
            userScale: userScale,
            sw01: sw01,
            sw07: sw07,
            slice: ValidationSummary.SliceMeta(
                sliceOffsetFraction: result.sliceOffsetFraction,
                axisU: [result.axisU.x, result.axisU.y, result.axisU.z],
                axisV: [result.axisV.x, result.axisV.y, result.axisV.z],
                planeNormal: [result.planeNormal.x, result.planeNormal.y, result.planeNormal.z],
                floorMethod: floor.method.rawValue,
                floorHSceneUnits: floor.floorH,
                vertexMinHSceneUnits: floor.vertexMinH,
                floorPlaneRMSE: floor.planeRMSE,
                floorConfidence: floor.confidence,
                floorCandidateCount: floor.candidateCount,
                floorDownwardAreaFraction: floor.downwardAreaFraction,
                floorTiltDegFromWorldY: floor.tiltDegFromWorldY
            )
        )
    }

    @MainActor
    static func renderOutlinePNG(
        primary: [(Float, Float)],
        idealFit: [(Float, Float)]?,
        clearanceStats: ClearanceStats?,
        unitsMillimeters: Bool,
        validationLines: OutlineValidationLines = OutlineValidationLines()
    ) -> Data? {
        let view = BasePerimeterVisualizerView(
            planeXY: primary,
            idealFitXY: idealFit,
            clearanceStats: clearanceStats,
            unitsMillimeters: unitsMillimeters,
            showPolarControls: false,
            exportedValidationLines: validationLines
        )
        .frame(width: 360, height: 420)
        let renderer = ImageRenderer(content: view)
        renderer.scale = 2.0
        guard let cgImage = renderer.cgImage else { return nil }
        let rep = NSBitmapImageRep(cgImage: cgImage)
        return rep.representation(using: .png, properties: [:])
    }
}
