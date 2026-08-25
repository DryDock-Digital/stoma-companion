#import <Foundation/Foundation.h>
#import <CoreGraphics/CoreGraphics.h>

NS_ASSUME_NONNULL_BEGIN

/// One detected ArUco marker in image-pixel space (origin top-left, +y down).
@interface ArUcoDetectionResult : NSObject
@property (nonatomic, readonly) NSInteger markerID;
@property (nonatomic, readonly) NSArray<NSValue *> *corners; // CGPoint, ordered TL→TR→BR→BL
@property (nonatomic, readonly) double reprojectionError;
@property (nonatomic, readonly) double confidence;
@end

/// Planar pixel→millimeter homography derived from a known-size ArUco marker.
@interface ArUcoHomographyResult : NSObject
@property (nonatomic, readonly) ArUcoDetectionResult *marker;
/// Row-major 3×3 matrix H such that [x_mm, y_mm, w]^T ≈ H · [u_px, v_px, 1]^T.
@property (nonatomic, readonly) NSArray<NSNumber *> *matrixRowMajor;
@property (nonatomic, readonly) double markerSideMillimeters;
@property (nonatomic, readonly) double meanCornerResidualMillimeters;
@end

@interface ArUcoDetectorBridge : NSObject

/// Detect markers from a grayscale or BGRA `CGImage`. Uses DICT_4X4_50.
+ (nullable NSArray<ArUcoDetectionResult *> *)detectMarkersInImage:(CGImageRef)image
                                                             error:(NSError * _Nullable * _Nullable)error;

/// Prefer `expectedID` when non-negative; otherwise pick the highest-confidence marker.
+ (nullable ArUcoHomographyResult *)homographyFromImage:(CGImageRef)image
                                  markerSideMillimeters:(double)sideMM
                                             expectedID:(NSInteger)expectedID
                                                  error:(NSError * _Nullable * _Nullable)error;

/// Apply a 3×3 row-major homography: pixel → millimeters.
+ (CGPoint)applyHomography:(NSArray<NSNumber *> *)matrixRowMajor toPixel:(CGPoint)pixel;

@end

NS_ASSUME_NONNULL_END
