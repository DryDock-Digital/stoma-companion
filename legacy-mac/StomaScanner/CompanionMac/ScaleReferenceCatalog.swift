import Foundation

/// US coins supported for top-down photo diameter calibration.
enum PhotoCalibrationCoinKind: String, CaseIterable, Identifiable {
    case usQuarter
    case usNickel

    var id: String { rawValue }

    var title: String {
        switch self {
        case .usQuarter: "US quarter (24.26 mm diameter)"
        case .usNickel: "US nickel (21.21 mm diameter)"
        }
    }

    var lineLabel: String {
        switch self {
        case .usQuarter: "Quarter diameter"
        case .usNickel: "Nickel diameter"
        }
    }

    /// Physical coin diameter in meters.
    var diameterMeters: Double {
        switch self {
        case .usQuarter: 0.02426
        case .usNickel: 0.02121
        }
    }

    var scaleReferenceKind: ScaleReferenceKind {
        switch self {
        case .usQuarter: .usQuarterDiameter
        case .usNickel: .usNickelDiameter
        }
    }
}

/// Known real-world lengths for a 1D scale ratio (mesh length of the same feature in scene units).
enum ScaleReferenceKind: String, CaseIterable, Identifiable {
    case none
    case cubeEdge100mm
    case id1CardLong
    case id1CardShort
    case usQuarterDiameter
    case usNickelDiameter
    case custom

    var id: String { rawValue }

    var title: String {
        switch self {
        case .none: "None (manual scale only)"
        case .cubeEdge100mm: "100 mm cube edge"
        case .id1CardLong: "ID-1 card long edge (85.6 mm)"
        case .id1CardShort: "ID-1 card short edge (53.98 mm)"
        case .usQuarterDiameter: "US quarter diameter (24.26 mm)"
        case .usNickelDiameter: "US nickel diameter (21.21 mm)"
        case .custom: "Custom length (mm)"
        }
    }

    /// Physical length in **meters** for the dimension that should match the mesh measurement (e.g. longest AABB edge of the named reference subtree).
    var realLengthMeters: Double? {
        switch self {
        case .none:
            return nil
        case .cubeEdge100mm:
            return 0.1
        case .id1CardLong:
            return 0.0856
        case .id1CardShort:
            return 0.05398
        case .usQuarterDiameter:
            return PhotoCalibrationCoinKind.usQuarter.diameterMeters
        case .usNickelDiameter:
            return PhotoCalibrationCoinKind.usNickel.diameterMeters
        case .custom:
            return nil
        }
    }
}
