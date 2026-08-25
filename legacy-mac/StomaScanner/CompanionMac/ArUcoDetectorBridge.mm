#import "ArUcoDetectorBridge.h"
#import <AppKit/AppKit.h>
#import <vector>
#import <cmath>
#import <algorithm>
#import <numeric>
#import <array>

NSErrorDomain const ArUcoDetectorErrorDomain = @"ArUcoDetectorErrorDomain";

enum {
    ArUcoErrorNoMarker = 1,
    ArUcoErrorBadImage = 2,
    ArUcoErrorHomographyFailed = 3,
    ArUcoErrorIDMismatch = 4,
};

@implementation ArUcoDetectionResult {
    NSInteger _markerID;
    NSArray<NSValue *> *_corners;
    double _reprojectionError;
    double _confidence;
}
- (instancetype)initWithID:(NSInteger)mid corners:(NSArray<NSValue *> *)c err:(double)e conf:(double)cf {
    self = [super init];
    if (self) {
        _markerID = mid;
        _corners = [c copy];
        _reprojectionError = e;
        _confidence = cf;
    }
    return self;
}
- (NSInteger)markerID { return _markerID; }
- (NSArray<NSValue *> *)corners { return _corners; }
- (double)reprojectionError { return _reprojectionError; }
- (double)confidence { return _confidence; }
@end

@implementation ArUcoHomographyResult {
    ArUcoDetectionResult *_marker;
    NSArray<NSNumber *> *_matrixRowMajor;
    double _markerSideMillimeters;
    double _meanCornerResidualMillimeters;
}
- (instancetype)initWithMarker:(ArUcoDetectionResult *)m
                        matrix:(NSArray<NSNumber *> *)mat
                         sideMM:(double)side
                      residual:(double)res {
    self = [super init];
    if (self) {
        _marker = m;
        _matrixRowMajor = [mat copy];
        _markerSideMillimeters = side;
        _meanCornerResidualMillimeters = res;
    }
    return self;
}
- (ArUcoDetectionResult *)marker { return _marker; }
- (NSArray<NSNumber *> *)matrixRowMajor { return _matrixRowMajor; }
- (double)markerSideMillimeters { return _markerSideMillimeters; }
- (double)meanCornerResidualMillimeters { return _meanCornerResidualMillimeters; }
@end

#pragma mark - Geometry helpers

static inline double clampf(double v, double lo, double hi) {
    return std::max(lo, std::min(hi, v));
}

struct Vec2 {
    double x, y;
    Vec2() : x(0), y(0) {}
    Vec2(double x_, double y_) : x(x_), y(y_) {}
    Vec2 operator+(const Vec2 &o) const { return {x + o.x, y + o.y}; }
    Vec2 operator-(const Vec2 &o) const { return {x - o.x, y - o.y}; }
    Vec2 operator*(double s) const { return {x * s, y * s}; }
    double len() const { return std::sqrt(x * x + y * y); }
    double cross(const Vec2 &o) const { return x * o.y - y * o.x; }
    double dot(const Vec2 &o) const { return x * o.x + y * o.y; }
};

static double dist(const Vec2 &a, const Vec2 &b) { return (a - b).len(); }

/// OpenCV DICT_4X4_50 bit patterns (row-major 4×4, MSB first; dark cell = 1).
/// Verified against OpenCV `generateImageMarker(DICT_4X4_50, id, …)`.
static const uint16_t kDict4x4_50[50] = {
    0x4ACD, 0xF065, 0xCCD2, 0x66B9, 0xAB61, 0x8632, 0x61D1, 0x3B0D,
    0x0125, 0x30A9, 0x066E, 0xEE58, 0xF148, 0xD5F0, 0xDB4E, 0xD9C1,
    0xB99A, 0x99FF, 0x93A1, 0x8950, 0x7974, 0x4FD4, 0x332A, 0x227D,
    0x01B8, 0x6B8E, 0x531B, 0x5AAB, 0xDEDC, 0xCB90, 0xBBEA, 0xA84D,
    0x6130, 0x0F34, 0xF751, 0xF6D6, 0xE78A, 0xFB00, 0xF209, 0xE3A5,
    0xE8E7, 0xD5D7, 0xCD73, 0xC74D, 0xDB17, 0xD114, 0xD2C0, 0xB49B,
    0xAFD1, 0xAFEC
};

/// Rotate a 4×4 bit pattern 90° CW.
static uint16_t rotateBits4(uint16_t bits) {
    uint16_t out = 0;
    for (int r = 0; r < 4; ++r) {
        for (int c = 0; c < 4; ++c) {
            int src = r * 4 + c;
            int bit = (bits >> (15 - src)) & 1;
            int nr = c;
            int nc = 3 - r;
            int dst = nr * 4 + nc;
            if (bit) out |= (uint16_t)(1u << (15 - dst));
        }
    }
    return out;
}

static int hamming16(uint16_t a, uint16_t b) {
    return __builtin_popcount((unsigned)(a ^ b));
}

/// Reflect a 4×4 bit pattern about the vertical axis (left↔right).
static uint16_t mirrorBits4V(uint16_t bits) {
    uint16_t out = 0;
    for (int r = 0; r < 4; ++r) {
        for (int c = 0; c < 4; ++c) {
            int src = r * 4 + c;
            int bit = (bits >> (15 - src)) & 1;
            int dst = r * 4 + (3 - c);
            if (bit) out |= (uint16_t)(1u << (15 - dst));
        }
    }
    return out;
}

/// Reflect a 4×4 bit pattern about the horizontal axis (top↔bottom).
static uint16_t mirrorBits4H(uint16_t bits) {
    uint16_t out = 0;
    for (int r = 0; r < 4; ++r) {
        for (int c = 0; c < 4; ++c) {
            int src = r * 4 + c;
            int bit = (bits >> (15 - src)) & 1;
            int dst = (3 - r) * 4 + c;
            if (bit) out |= (uint16_t)(1u << (15 - dst));
        }
    }
    return out;
}

/// Match bits against dictionary; returns marker id and rotation (0–3), or -1.
/// Tries mirrors and inverted polarity (legacy prints used an inverted bit dictionary).
static int matchDictionary(uint16_t bits, int *outRotation) {
    int bestID = -1;
    int bestDist = 100;
    int secondDist = 100;
    int bestRot = 0;
    const uint16_t polarities[2] = { bits, (uint16_t)(~bits) };
    for (uint16_t pol : polarities) {
        const uint16_t variants[4] = {
            pol,
            mirrorBits4H(pol),
            mirrorBits4V(pol),
            mirrorBits4V(mirrorBits4H(pol)),
        };
        for (uint16_t candidate : variants) {
            for (int id = 0; id < 50; ++id) {
                uint16_t pat = kDict4x4_50[id];
                for (int rot = 0; rot < 4; ++rot) {
                    int d = hamming16(candidate, pat);
                    if (d < bestDist) {
                        secondDist = bestDist;
                        bestDist = d;
                        bestID = id;
                        bestRot = rot;
                    } else if (d < secondDist) {
                        secondDist = d;
                    }
                    pat = rotateBits4(pat);
                }
            }
        }
    }
    // Require a clear winner — busy backgrounds otherwise invent IDs.
    // Allow up to 3 bit errors for speckled / glare prints when the runner-up is clearly worse.
    if (bestDist > 3) return -1;
    if (bestDist >= 1 && secondDist - bestDist < 2) return -1;
    if (outRotation) *outRotation = bestRot;
    return bestID;
}

#pragma mark - Image / contours

struct GrayImage {
    int w = 0, h = 0;
    std::vector<uint8_t> px;
    uint8_t at(int x, int y) const {
        if (x < 0 || y < 0 || x >= w || y >= h) return 255;
        return px[(size_t)y * (size_t)w + (size_t)x];
    }
    void set(int x, int y, uint8_t v) {
        if (x < 0 || y < 0 || x >= w || y >= h) return;
        px[(size_t)y * (size_t)w + (size_t)x] = v;
    }
};

static bool cgImageToGray(CGImageRef image, GrayImage &out) {
    if (!image) return false;
    const size_t w = CGImageGetWidth(image);
    const size_t h = CGImageGetHeight(image);
    if (w < 8 || h < 8 || w > 12000 || h > 12000) return false;
    out.w = (int)w;
    out.h = (int)h;
    out.px.assign(w * h, 0);

    CGColorSpaceRef cs = CGColorSpaceCreateDeviceRGB();
    std::vector<uint8_t> rgba(w * h * 4);
    CGContextRef ctx = CGBitmapContextCreate(
        rgba.data(), w, h, 8, w * 4, cs,
        kCGImageAlphaPremultipliedLast | kCGBitmapByteOrder32Big);
    CGColorSpaceRelease(cs);
    if (!ctx) return false;
    // CGBitmapContext has origin at bottom-left; flip so row 0 of our buffer is the image top.
    CGContextTranslateCTM(ctx, 0, (CGFloat)h);
    CGContextScaleCTM(ctx, 1, -1);
    CGContextDrawImage(ctx, CGRectMake(0, 0, (CGFloat)w, (CGFloat)h), image);
    CGContextRelease(ctx);

    for (size_t i = 0; i < w * h; ++i) {
        const uint8_t r = rgba[i * 4 + 0];
        const uint8_t g = rgba[i * 4 + 1];
        const uint8_t b = rgba[i * 4 + 2];
        out.px[i] = (uint8_t)((r * 30 + g * 59 + b * 11) / 100);
    }
    return true;
}

static void otsuThreshold(const GrayImage &src, GrayImage &dst) {
    dst.w = src.w;
    dst.h = src.h;
    dst.px.resize((size_t)src.w * (size_t)src.h);
    int hist[256] = {};
    for (uint8_t v : src.px) hist[v]++;
    const int total = src.w * src.h;
    double sum = 0;
    for (int i = 0; i < 256; ++i) sum += i * hist[i];
    double sumB = 0;
    int wB = 0;
    double maxVar = -1;
    int thresh = 127;
    for (int t = 0; t < 256; ++t) {
        wB += hist[t];
        if (wB == 0) continue;
        int wF = total - wB;
        if (wF == 0) break;
        sumB += t * hist[t];
        double mB = sumB / wB;
        double mF = (sum - sumB) / wF;
        double var = (double)wB * (double)wF * (mB - mF) * (mB - mF);
        if (var > maxVar) {
            maxVar = var;
            thresh = t;
        }
    }
    if (thresh <= 0) thresh = 127;
    for (size_t i = 0; i < src.px.size(); ++i) {
        dst.px[i] = src.px[i] <= (uint8_t)thresh ? 0 : 255;
    }
}

/// Local adaptive threshold — critical when a dark subject dominates global Otsu.
static void adaptiveThreshold(const GrayImage &src, GrayImage &dst, int blockSize, int C) {
    dst.w = src.w;
    dst.h = src.h;
    dst.px.resize((size_t)src.w * (size_t)src.h);
    if (blockSize % 2 == 0) blockSize += 1;
    blockSize = std::max(blockSize, 15);
    const int w = src.w, h = src.h;
    // Integral image (1-indexed).
    std::vector<uint64_t> integ((size_t)(w + 1) * (size_t)(h + 1), 0);
    for (int y = 1; y <= h; ++y) {
        uint64_t row = 0;
        for (int x = 1; x <= w; ++x) {
            row += src.px[(size_t)(y - 1) * (size_t)w + (size_t)(x - 1)];
            integ[(size_t)y * (size_t)(w + 1) + (size_t)x] =
                integ[(size_t)(y - 1) * (size_t)(w + 1) + (size_t)x] + row;
        }
    }
    auto rectSum = [&](int x0, int y0, int x1, int y1) -> uint64_t {
        x0 = std::max(x0, 0); y0 = std::max(y0, 0);
        x1 = std::min(x1, w - 1); y1 = std::min(y1, h - 1);
        // inclusive → integral uses exclusive end = +1
        int A = x0, B = y0, C2 = x1 + 1, D = y1 + 1;
        return integ[(size_t)D * (w + 1) + C2] - integ[(size_t)B * (w + 1) + C2]
             - integ[(size_t)D * (w + 1) + A] + integ[(size_t)B * (w + 1) + A];
    };
    const int r = blockSize / 2;
    for (int y = 0; y < h; ++y) {
        for (int x = 0; x < w; ++x) {
            int x0 = x - r, y0 = y - r, x1 = x + r, y1 = y + r;
            int bw = std::min(x1, w - 1) - std::max(x0, 0) + 1;
            int bh = std::min(y1, h - 1) - std::max(y0, 0) + 1;
            double mean = (double)rectSum(x0, y0, x1, y1) / std::max(bw * bh, 1);
            uint8_t v = src.px[(size_t)y * (size_t)w + (size_t)x];
            dst.px[(size_t)y * (size_t)w + (size_t)x] = (v < mean - C) ? 0 : 255;
        }
    }
}

static void contrastStretch(const GrayImage &src, GrayImage &dst) {
    dst.w = src.w; dst.h = src.h; dst.px.resize(src.px.size());
    int hist[256] = {};
    for (uint8_t v : src.px) hist[v]++;
    int lo = 0, hi = 255, cum = 0;
    const int n = (int)src.px.size();
    const int loCut = n / 50, hiCut = n - n / 50;
    for (int i = 0; i < 256; ++i) { cum += hist[i]; if (cum >= loCut) { lo = i; break; } }
    cum = 0;
    for (int i = 255; i >= 0; --i) { cum += hist[i]; if (cum >= n / 50) { hi = i; break; } }
    if (hi <= lo) { dst.px = src.px; return; }
    for (size_t i = 0; i < src.px.size(); ++i) {
        double t = (src.px[i] - lo) * 255.0 / (hi - lo);
        dst.px[i] = (uint8_t)clampf(t, 0, 255);
    }
}

/// Box blur — reduces print speckles / paper texture before thresholding.
static void boxBlur(const GrayImage &src, GrayImage &dst, int radius) {
    dst.w = src.w; dst.h = src.h; dst.px.resize(src.px.size());
    if (radius < 1) { dst.px = src.px; return; }
    const int w = src.w, h = src.h;
    std::vector<uint64_t> integ((size_t)(w + 1) * (size_t)(h + 1), 0);
    for (int y = 1; y <= h; ++y) {
        uint64_t row = 0;
        for (int x = 1; x <= w; ++x) {
            row += src.px[(size_t)(y - 1) * (size_t)w + (size_t)(x - 1)];
            integ[(size_t)y * (size_t)(w + 1) + (size_t)x] =
                integ[(size_t)(y - 1) * (size_t)(w + 1) + (size_t)x] + row;
        }
    }
    for (int y = 0; y < h; ++y) {
        for (int x = 0; x < w; ++x) {
            int x0 = std::max(0, x - radius), y0 = std::max(0, y - radius);
            int x1 = std::min(w - 1, x + radius), y1 = std::min(h - 1, y + radius);
            int A = x0, B = y0, C2 = x1 + 1, D = y1 + 1;
            uint64_t sum = integ[(size_t)D * (w + 1) + C2] - integ[(size_t)B * (w + 1) + C2]
                         - integ[(size_t)D * (w + 1) + A] + integ[(size_t)B * (w + 1) + A];
            int area = (x1 - x0 + 1) * (y1 - y0 + 1);
            dst.px[(size_t)y * (size_t)w + (size_t)x] = (uint8_t)(sum / std::max(area, 1));
        }
    }
}

/// Morphological close on binary (0=ink, 255=paper) to fill speckles in black cells.
static void morphCloseBinary(GrayImage &img, int radius) {
    if (radius < 1) return;
    GrayImage tmp = img;
    const int w = img.w, h = img.h;
    // Dilate black (min filter).
    for (int y = 0; y < h; ++y) {
        for (int x = 0; x < w; ++x) {
            uint8_t m = 255;
            for (int dy = -radius; dy <= radius; ++dy) {
                for (int dx = -radius; dx <= radius; ++dx) {
                    m = std::min(m, img.at(x + dx, y + dy));
                }
            }
            tmp.set(x, y, m);
        }
    }
    // Erode black (max filter).
    for (int y = 0; y < h; ++y) {
        for (int x = 0; x < w; ++x) {
            uint8_t m = 0;
            for (int dy = -radius; dy <= radius; ++dy) {
                for (int dx = -radius; dx <= radius; ++dx) {
                    m = std::max(m, tmp.at(x + dx, y + dy));
                }
            }
            img.set(x, y, m);
        }
    }
}

/// Trace exterior contour (Moore neighborhood) starting at a black pixel with a white left neighbor.
static bool traceContour(const GrayImage &bin, int sx, int sy, std::vector<Vec2> &out) {
    static const int dx[8] = {1, 1, 0, -1, -1, -1, 0, 1};
    static const int dy[8] = {0, 1, 1, 1, 0, -1, -1, -1};
    out.clear();
    int x = sx, y = sy;
    int dir = 7; // coming from left
    const int maxSteps = bin.w * bin.h;
    for (int step = 0; step < maxSteps; ++step) {
        out.push_back({(double)x, (double)y});
        int start = (dir + 6) % 8; // turn left relative to arrival
        bool found = false;
        for (int k = 0; k < 8; ++k) {
            int nd = (start + k) % 8;
            int nx = x + dx[nd];
            int ny = y + dy[nd];
            if (bin.at(nx, ny) == 0) {
                x = nx;
                y = ny;
                dir = nd;
                found = true;
                break;
            }
        }
        if (!found) return false;
        if (x == sx && y == sy && out.size() > 4) break;
    }
    return out.size() >= 20;
}

static void douglasPeucker(const std::vector<Vec2> &in, double eps, std::vector<Vec2> &out) {
    if (in.size() < 3) {
        out = in;
        return;
    }
    double maxD = 0;
    size_t idx = 0;
    Vec2 a = in.front(), b = in.back();
    Vec2 ab = b - a;
    double abLen2 = ab.dot(ab);
    for (size_t i = 1; i + 1 < in.size(); ++i) {
        double d;
        if (abLen2 < 1e-12) {
            d = dist(in[i], a);
        } else {
            double t = clampf((in[i] - a).dot(ab) / abLen2, 0.0, 1.0);
            d = dist(in[i], a + ab * t);
        }
        if (d > maxD) {
            maxD = d;
            idx = i;
        }
    }
    if (maxD > eps) {
        std::vector<Vec2> leftIn(in.begin(), in.begin() + (long)idx + 1);
        std::vector<Vec2> rightIn(in.begin() + (long)idx, in.end());
        std::vector<Vec2> leftOut, rightOut;
        douglasPeucker(leftIn, eps, leftOut);
        douglasPeucker(rightIn, eps, rightOut);
        out = leftOut;
        out.insert(out.end(), rightOut.begin() + 1, rightOut.end());
    } else {
        out = {a, b};
    }
}

static bool orderQuadTLTRBRBL(std::array<Vec2, 4> &q) {
    // Sort by y then x for top two / bottom two.
    std::sort(q.begin(), q.end(), [](const Vec2 &a, const Vec2 &b) {
        if (std::abs(a.y - b.y) > 1.0) return a.y < b.y;
        return a.x < b.x;
    });
    Vec2 tl = q[0].x < q[1].x ? q[0] : q[1];
    Vec2 tr = q[0].x < q[1].x ? q[1] : q[0];
    Vec2 bl = q[2].x < q[3].x ? q[2] : q[3];
    Vec2 br = q[2].x < q[3].x ? q[3] : q[2];
    q = {tl, tr, br, bl};
    // Ensure CCW / positive area for orientation consistency.
    double area = 0;
    for (int i = 0; i < 4; ++i) {
        const Vec2 &a = q[i];
        const Vec2 &b = q[(i + 1) % 4];
        area += a.cross(b);
    }
    if (area < 0) {
        // flip TR/BR with BL path → reverse to CW→CCW: TL, BL, BR, TR → remap to TL,TR,BR,BL
        q = {tl, tr, br, bl}; // already that order
    }
    return true;
}

#pragma mark - Homography

static bool solveHomographyDLT(const std::array<Vec2, 4> &src, const std::array<Vec2, 4> &dst, double H[9]) {
    // Solve Ah = 0 for 8 DOF homography (h33=1).
    double A[8][8] = {};
    double b[8] = {};
    for (int i = 0; i < 4; ++i) {
        double x = src[i].x, y = src[i].y;
        double u = dst[i].x, v = dst[i].y;
        A[2 * i][0] = x;
        A[2 * i][1] = y;
        A[2 * i][2] = 1;
        A[2 * i][6] = -u * x;
        A[2 * i][7] = -u * y;
        b[2 * i] = u;
        A[2 * i + 1][3] = x;
        A[2 * i + 1][4] = y;
        A[2 * i + 1][5] = 1;
        A[2 * i + 1][6] = -v * x;
        A[2 * i + 1][7] = -v * y;
        b[2 * i + 1] = v;
    }
    // Gaussian elimination with partial pivoting.
    for (int col = 0; col < 8; ++col) {
        int piv = col;
        for (int r = col + 1; r < 8; ++r) {
            if (std::abs(A[r][col]) > std::abs(A[piv][col])) piv = r;
        }
        if (std::abs(A[piv][col]) < 1e-12) return false;
        if (piv != col) {
            for (int c = 0; c < 8; ++c) std::swap(A[col][c], A[piv][c]);
            std::swap(b[col], b[piv]);
        }
        for (int r = col + 1; r < 8; ++r) {
            double f = A[r][col] / A[col][col];
            for (int c = col; c < 8; ++c) A[r][c] -= f * A[col][c];
            b[r] -= f * b[col];
        }
    }
    double h[8] = {};
    for (int i = 7; i >= 0; --i) {
        double s = b[i];
        for (int j = i + 1; j < 8; ++j) s -= A[i][j] * h[j];
        h[i] = s / A[i][i];
    }
    for (int i = 0; i < 8; ++i) H[i] = h[i];
    H[8] = 1.0;
    return true;
}

static Vec2 applyH(const double H[9], const Vec2 &p) {
    double w = H[6] * p.x + H[7] * p.y + H[8];
    if (std::abs(w) < 1e-12) return {0, 0};
    return {(H[0] * p.x + H[1] * p.y + H[2]) / w, (H[3] * p.x + H[4] * p.y + H[5]) / w};
}

static void sampleBilinear(const GrayImage &img, double x, double y, double &out) {
    int x0 = (int)std::floor(x);
    int y0 = (int)std::floor(y);
    double fx = x - x0, fy = y - y0;
    double v00 = img.at(x0, y0);
    double v10 = img.at(x0 + 1, y0);
    double v01 = img.at(x0, y0 + 1);
    double v11 = img.at(x0 + 1, y0 + 1);
    out = (1 - fx) * (1 - fy) * v00 + fx * (1 - fy) * v10 + (1 - fx) * fy * v01 + fx * fy * v11;
}

/// Warp quad to a square grid and decode 4×4 ArUco bits (inner cells of 6×6 including border).
static bool decodeMarkerBits(const GrayImage &gray, const std::array<Vec2, 4> &quad,
                             uint16_t *outBits, double *outBorderScore) {
    // Destination: unit square 0..1 with TL,TR,BR,BL.
    std::array<Vec2, 4> dst = {Vec2{0, 0}, {1, 0}, {1, 1}, {0, 1}};
    double H[9];
    if (!solveHomographyDLT(dst, quad, H)) return false; // maps unit → image

    const int cells = 6;
    const int samplesPerCell = 7;
    double cellMean[6][6];
    for (int r = 0; r < cells; ++r) {
        for (int c = 0; c < cells; ++c) {
            double sum = 0;
            int n = 0;
            for (int sy = 1; sy < samplesPerCell - 1; ++sy) {
                for (int sx = 1; sx < samplesPerCell - 1; ++sx) {
                    double u = (c + (sx + 0.5) / samplesPerCell) / cells;
                    double v = (r + (sy + 0.5) / samplesPerCell) / cells;
                    Vec2 p = applyH(H, {u, v});
                    double val;
                    sampleBilinear(gray, p.x, p.y, val);
                    sum += val;
                    ++n;
                }
            }
            cellMean[r][c] = sum / std::max(n, 1);
        }
    }

    // Border must be dark; interior threshold from Otsu-ish mid of all cells.
    double borderSum = 0;
    int borderN = 0;
    for (int i = 0; i < cells; ++i) {
        borderSum += cellMean[0][i] + cellMean[cells - 1][i];
        borderN += 2;
        if (i > 0 && i < cells - 1) {
            borderSum += cellMean[i][0] + cellMean[i][cells - 1];
            borderN += 2;
        }
    }
    double borderMean = borderSum / borderN;
    double innerSum = 0;
    int innerN = 0;
    for (int r = 1; r < 5; ++r) {
        for (int c = 1; c < 5; ++c) {
            innerSum += cellMean[r][c];
            ++innerN;
        }
    }
    double innerMean = innerSum / innerN;
    // Glare can brighten parts of the border; require border darker on average, with slack.
    if (borderMean > innerMean + 25) return false;
    if (borderMean > 200 && innerMean > 180) return false; // both washed out
    double thresh = (borderMean + innerMean) * 0.5;
    // If contrast is weak, bias toward mid-gray.
    if (std::abs(innerMean - borderMean) < 25) thresh = 127;
    if (outBorderScore) {
        *outBorderScore = clampf((innerMean - borderMean) / 80.0, 0.05, 1.0);
    }

    uint16_t bits = 0;
    for (int r = 0; r < 4; ++r) {
        for (int c = 0; c < 4; ++c) {
            int bit = cellMean[r + 1][c + 1] < thresh ? 1 : 0; // dark = 1 in ArUco
            if (bit) bits |= (uint16_t)(1u << (15 - (r * 4 + c)));
        }
    }
    *outBits = bits;
    return true;
}

static bool componentToQuad(const GrayImage &bin, const std::vector<Vec2> &pts, std::array<Vec2, 4> &outQ) {
    if (pts.size() < 40) return false;
    double minx = 1e9, maxx = -1e9, miny = 1e9, maxy = -1e9;
    for (const auto &p : pts) {
        minx = std::min(minx, p.x); maxx = std::max(maxx, p.x);
        miny = std::min(miny, p.y); maxy = std::max(maxy, p.y);
    }
    double bw = maxx - minx, bh = maxy - miny;
    if (bw < 18 || bh < 18) return false;
    double aspect = bw / std::max(bh, 1e-6);
    if (aspect < 0.45 || aspect > 2.2) return false; // allow rotated squares (AABB taller)

    GrayImage mask;
    mask.w = bin.w; mask.h = bin.h;
    mask.px.assign(bin.px.size(), 0);
    for (const auto &p : pts) mask.set((int)p.x, (int)p.y, 1);

    double minX = 1e9;
    Vec2 seed{0, 0};
    for (const auto &p : pts) {
        if (p.x < minX) { minX = p.x; seed = p; }
    }
    GrayImage componentBin = bin;
    for (size_t i = 0; i < componentBin.px.size(); ++i) {
        if (!mask.px[i]) componentBin.px[i] = 255;
    }

    std::vector<Vec2> contour;
    if (traceContour(componentBin, (int)seed.x, (int)seed.y, contour) && contour.size() >= 40) {
        double peri = 0;
        for (size_t i = 0; i < contour.size(); ++i) peri += dist(contour[i], contour[(i + 1) % contour.size()]);
        std::vector<Vec2> closed = contour;
        if (dist(closed.front(), closed.back()) > 1.0) closed.push_back(closed.front());
        for (double frac : {0.01, 0.015, 0.02, 0.03, 0.05, 0.08, 0.12, 0.18}) {
            std::vector<Vec2> approx;
            douglasPeucker(closed, std::max(1.5, peri * frac), approx);
            if (!approx.empty() && dist(approx.front(), approx.back()) < 2.0) approx.pop_back();
            if (approx.size() != 4) continue;
            std::array<Vec2, 4> q = {approx[0], approx[1], approx[2], approx[3]};
            double sides[4];
            bool ok = true;
            for (int i = 0; i < 4; ++i) {
                sides[i] = dist(q[i], q[(i + 1) % 4]);
                if (sides[i] < 14) ok = false;
            }
            if (!ok) continue;
            double meanSide = (sides[0] + sides[1] + sides[2] + sides[3]) / 4.0;
            for (double s : sides) if (s < meanSide * 0.45 || s > meanSide * 1.9) ok = false;
            if (!ok) continue;
            orderQuadTLTRBRBL(q);
            outQ = q;
            return true;
        }
    }

    // AABB fallback (works for axis-aligned markers).
    double ix = std::max(0.5, bw * 0.02);
    double iy = std::max(0.5, bh * 0.02);
    outQ = {
        Vec2{minx + ix, miny + iy}, {maxx - ix, miny + iy},
        {maxx - ix, maxy - iy}, {minx + ix, maxy - iy}
    };
    orderQuadTLTRBRBL(outQ);
    return true;
}

static std::vector<std::array<Vec2, 4>> findCandidateQuads(const GrayImage &bin) {
    std::vector<std::array<Vec2, 4>> quads;
    GrayImage seen;
    seen.w = bin.w;
    seen.h = bin.h;
    seen.px.assign(bin.px.size(), 0);
    const int imgArea = bin.w * bin.h;
    const int minCount = std::max(80, imgArea / 8000);
    // Only reject blobs that fill most of the *frame* (dark silicone disc / background).
    // A printed marker can easily be >35% black pixels of a tight crop.
    const int maxCount = std::max(minCount + 1, (imgArea * 70) / 100);

    struct Comp { int count; std::vector<Vec2> pts; };
    std::vector<Comp> comps;

    for (int y = 1; y < bin.h - 1; ++y) {
        for (int x = 1; x < bin.w - 1; ++x) {
            if (bin.at(x, y) != 0 || seen.at(x, y)) continue;
            std::vector<std::pair<int, int>> stack = {{x, y}};
            seen.set(x, y, 1);
            std::vector<Vec2> pts;
            pts.reserve(512);
            int count = 0;
            while (!stack.empty()) {
                std::pair<int, int> cur = stack.back();
                stack.pop_back();
                int cx = cur.first, cy = cur.second;
                ++count;
                pts.push_back({(double)cx, (double)cy});
                for (int dy = -1; dy <= 1; ++dy) {
                    for (int dx = -1; dx <= 1; ++dx) {
                        if (dx == 0 && dy == 0) continue;
                        int nx = cx + dx, ny = cy + dy;
                        if (bin.at(nx, ny) != 0 || seen.at(nx, ny)) continue;
                        seen.set(nx, ny, 1);
                        stack.push_back({nx, ny});
                    }
                }
            }
            if (count < minCount || count > maxCount) continue;
            {
                double minx=1e9,maxx=-1e9,miny=1e9,maxy=-1e9;
                for (const auto &p : pts) {
                    minx=std::min(minx,p.x); maxx=std::max(maxx,p.x);
                    miny=std::min(miny,p.y); maxy=std::max(maxy,p.y);
                }
                double cov = ((maxx-minx)*(maxy-miny)) / std::max((double)imgArea, 1.0);
                if (cov > 0.85) continue; // whole-frame blob
            }
            comps.push_back({count, std::move(pts)});
        }
    }

    // Prefer mid-sized components (markers) over tiny noise.
    std::sort(comps.begin(), comps.end(), [](const Comp &a, const Comp &b) {
        return a.count > b.count;
    });
    const size_t limit = std::min(comps.size(), (size_t)24);
    for (size_t i = 0; i < limit; ++i) {
        std::array<Vec2, 4> q;
        if (!componentToQuad(bin, comps[i].pts, q)) {
            continue;
        }
        // Dedup similar quads.
        bool dup = false;
        Vec2 c = (q[0] + q[1] + q[2] + q[3]) * 0.25;
        for (const auto &ex : quads) {
            Vec2 ec = (ex[0] + ex[1] + ex[2] + ex[3]) * 0.25;
            if (dist(c, ec) < 12) { dup = true; break; }
        }
        if (!dup) quads.push_back(q);
    }
    return quads;
}

/// On dark silicone the black marker merges with the base. Prefer dark blobs that sit
/// next to a bright quiet-zone (white paper margin).
static std::vector<std::array<Vec2, 4>> findQuietZoneAssistedQuads(const GrayImage &gray) {
    GrayImage white;
    white.w = gray.w; white.h = gray.h;
    white.px.assign(gray.px.size(), 255);
    // Bright quiet-zone threshold — paper white vs brown silicone.
    for (size_t i = 0; i < gray.px.size(); ++i) {
        white.px[i] = gray.px[i] >= 185 ? 0 : 255; // 0 = "ink" role for CC reuse = white paper
    }
    // Dilate white paper so neighborhood covers the marker.
    GrayImage whiteDil = white;
    morphCloseBinary(whiteDil, 3); // dilate-ish via close on inverted sense: our morphClose dilates black(0)
    // whiteDil black(0) = expanded white paper region

    GrayImage darkNear;
    darkNear.w = gray.w; darkNear.h = gray.h;
    darkNear.px.assign(gray.px.size(), 255);
    for (size_t i = 0; i < gray.px.size(); ++i) {
        bool nearWhite = whiteDil.px[i] == 0;
        bool dark = gray.px[i] < 145;
        darkNear.px[i] = (nearWhite && dark) ? 0 : 255;
    }
    morphCloseBinary(darkNear, 2);
    return findCandidateQuads(darkNear);
}

/// When the marker is cut out and placed on dark brown, there is no white quiet zone —
/// only the interior white data cells. Cluster those bright cells and expand to a
/// candidate outer square (data + black border ≈ 6/4 of the cell span).
static std::vector<std::array<Vec2, 4>> findWhiteCellClusterQuads(const GrayImage &gray) {
    std::vector<std::array<Vec2, 4>> quads;
    const int imgArea = gray.w * gray.h;
    const int minCell = std::max(20, imgArea / 200000);
    const int maxCell = std::max(minCell + 1, imgArea / 800);

    GrayImage bright;
    bright.w = gray.w; bright.h = gray.h;
    bright.px.assign(gray.px.size(), 255);
    for (size_t i = 0; i < gray.px.size(); ++i) {
        bright.px[i] = gray.px[i] >= 195 ? 0 : 255; // 0 = bright paper cell
    }

    GrayImage seen;
    seen.w = gray.w; seen.h = gray.h;
    seen.px.assign(gray.px.size(), 0);

    struct Cell { double cx, cy, minx, maxx, miny, maxy; int count; };
    std::vector<Cell> cells;
    cells.reserve(64);

    for (int y = 1; y < gray.h - 1; ++y) {
        for (int x = 1; x < gray.w - 1; ++x) {
            if (bright.at(x, y) != 0 || seen.at(x, y)) continue;
            std::vector<std::pair<int, int>> stack = {{x, y}};
            seen.set(x, y, 1);
            double sx = 0, sy = 0;
            double minx = x, maxx = x, miny = y, maxy = y;
            int count = 0;
            while (!stack.empty()) {
                auto cur = stack.back();
                stack.pop_back();
                int cx = cur.first, cy = cur.second;
                ++count;
                sx += cx; sy += cy;
                minx = std::min(minx, (double)cx); maxx = std::max(maxx, (double)cx);
                miny = std::min(miny, (double)cy); maxy = std::max(maxy, (double)cy);
                for (int dy = -1; dy <= 1; ++dy) {
                    for (int dx = -1; dx <= 1; ++dx) {
                        if (!dx && !dy) continue;
                        int nx = cx + dx, ny = cy + dy;
                        if (bright.at(nx, ny) != 0 || seen.at(nx, ny)) continue;
                        seen.set(nx, ny, 1);
                        stack.push_back({nx, ny});
                    }
                }
            }
            if (count < minCell || count > maxCell) continue;
            double bw = maxx - minx, bh = maxy - miny;
            if (bw < 4 || bh < 4) continue;
            double aspect = bw / std::max(bh, 1e-6);
            if (aspect < 0.35 || aspect > 2.8) continue;
            cells.push_back({sx / count, sy / count, minx, maxx, miny, maxy, count});
        }
    }

    if (cells.size() < 3) return quads;

    // Greedy clusters of nearby cell centers.
    std::vector<char> used(cells.size(), 0);
    for (size_t seed = 0; seed < cells.size(); ++seed) {
        if (used[seed]) continue;
        std::vector<size_t> members = {seed};
        used[seed] = 1;
        double radius = 180;
        // Grow radius from typical cell size.
        double cellSpan = std::max(cells[seed].maxx - cells[seed].minx, cells[seed].maxy - cells[seed].miny);
        radius = std::max(80.0, cellSpan * 8.0);

        bool grew = true;
        while (grew) {
            grew = false;
            for (size_t i = 0; i < cells.size(); ++i) {
                if (used[i]) continue;
                for (size_t m : members) {
                    double d = hypot(cells[i].cx - cells[m].cx, cells[i].cy - cells[m].cy);
                    if (d <= radius) {
                        used[i] = 1;
                        members.push_back(i);
                        grew = true;
                        break;
                    }
                }
            }
        }
        if (members.size() < 3 || members.size() > 20) continue;

        double meanCell = 0;
        Vec2 centroid{0, 0};
        for (size_t m : members) {
            meanCell += std::max(cells[m].maxx - cells[m].minx, cells[m].maxy - cells[m].miny);
            centroid.x += cells[m].cx;
            centroid.y += cells[m].cy;
        }
        meanCell /= members.size();
        centroid.x /= members.size();
        centroid.y /= members.size();

        // Oriented square from cell centers (markers are often ~45° rotated).
        // PCA-lite: use farthest point pair as one diagonal hint, else AABB.
        double maxD = 0;
        size_t ia = 0, ib = 0;
        for (size_t i = 0; i < members.size(); ++i) {
            for (size_t j = i + 1; j < members.size(); ++j) {
                double d = hypot(cells[members[i]].cx - cells[members[j]].cx,
                                 cells[members[i]].cy - cells[members[j]].cy);
                if (d > maxD) { maxD = d; ia = members[i]; ib = members[j]; }
            }
        }
        if (maxD < 30) continue;

        Vec2 axis = Vec2{cells[ib].cx - cells[ia].cx, cells[ib].cy - cells[ia].cy};
        double axisLen = axis.len();
        if (axisLen < 1e-6) continue;
        axis = axis * (1.0 / axisLen);
        Vec2 ortho{-axis.y, axis.x};

        double minA = 1e9, maxA = -1e9, minO = 1e9, maxO = -1e9;
        for (size_t m : members) {
            Vec2 p{cells[m].cx - centroid.x, cells[m].cy - centroid.y};
            // Include cell radius roughly.
            double half = meanCell * 0.5;
            for (double sa : {-half, half}) {
                for (double so : {-half, half}) {
                    Vec2 q = p + axis * sa + ortho * so;
                    double a = q.dot(axis);
                    double o = q.dot(ortho);
                    minA = std::min(minA, a); maxA = std::max(maxA, a);
                    minO = std::min(minO, o); maxO = std::max(maxO, o);
                }
            }
        }
        double dataA = maxA - minA, dataO = maxO - minO;
        if (dataA < 30 || dataO < 30) continue;
        double aspect = dataA / std::max(dataO, 1e-6);
        if (aspect < 0.55 || aspect > 1.8) continue;

        // Expand by ~one border cell (4 data + 2 border).
        double expand = meanCell * 1.2;
        minA -= expand; maxA += expand;
        minO -= expand; maxO += expand;

        // Corners in image space.
        auto cornerAt = [&](double a, double o) -> Vec2 {
            return centroid + axis * a + ortho * o;
        };
        std::array<Vec2, 4> q = {
            cornerAt(minA, minO), cornerAt(maxA, minO),
            cornerAt(maxA, maxO), cornerAt(minA, maxO)
        };
        orderQuadTLTRBRBL(q);

        // Reject if expanded square is mostly bright (not a real marker).
        int darkN = 0, totN = 0;
        for (int t = 0; t <= 20; ++t) {
            for (int s = 0; s <= 20; ++s) {
                double a = minA + (maxA - minA) * (t / 20.0);
                double o = minO + (maxO - minO) * (s / 20.0);
                Vec2 p = cornerAt(a, o);
                ++totN;
                if (gray.at((int)std::lround(p.x), (int)std::lround(p.y)) < 150) ++darkN;
            }
        }
        if (totN < 8 || (double)darkN / totN < 0.25) continue;

        bool dup = false;
        Vec2 c = (q[0] + q[1] + q[2] + q[3]) * 0.25;
        for (const auto &ex : quads) {
            Vec2 ec = (ex[0] + ex[1] + ex[2] + ex[3]) * 0.25;
            if (dist(c, ec) < 20) { dup = true; break; }
        }
        if (!dup) quads.push_back(q);
    }
    return quads;
}

static NSArray<ArUcoDetectionResult *> *detectImpl(CGImageRef image, NSError **error) {
    GrayImage gray;
    if (!cgImageToGray(image, gray)) {
        if (error) {
            *error = [NSError errorWithDomain:ArUcoDetectorErrorDomain code:ArUcoErrorBadImage
                                     userInfo:@{NSLocalizedDescriptionKey: @"Could not read calibration image."}];
        }
        return nil;
    }

    GrayImage blurred;
    boxBlur(gray, blurred, std::max(1, std::min(gray.w, gray.h) / 400));

    auto runOnQuads = [&](const std::vector<std::array<Vec2, 4>> &quads,
                          const GrayImage &sampleGray,
                          NSMutableArray<ArUcoDetectionResult *> *results) {
        const double minSide = 18.0;
        // Allow near-full-frame markers (tight crops / synthetic tests).
        const double maxSide = std::min(gray.w, gray.h) * 0.98;
        for (auto q : quads) {
            double sideSum = 0;
            for (int i = 0; i < 4; ++i) sideSum += dist(q[i], q[(i + 1) % 4]);
            double meanSide = sideSum / 4.0;
            if (meanSide < minSide || meanSide > maxSide) continue;

            uint16_t bits = 0;
            double borderScore = 0;
            if (!decodeMarkerBits(sampleGray, q, &bits, &borderScore)) continue;
            if (borderScore < 0.20) continue;
            int rot = 0;
            int mid = matchDictionary(bits, &rot);
            if (mid < 0) continue;

            std::array<Vec2, 4> ordered = q;
            for (int r = 0; r < rot; ++r) {
                ordered = {ordered[3], ordered[0], ordered[1], ordered[2]};
            }

            std::array<Vec2, 4> unit = {Vec2{0, 0}, {1, 0}, {1, 1}, {0, 1}};
            double H[9];
            if (!solveHomographyDLT(unit, ordered, H)) continue;
            double err = 0;
            for (int i = 0; i < 4; ++i) {
                Vec2 p = applyH(H, unit[i]);
                err += dist(p, ordered[i]);
            }
            err /= 4.0;
            if (err > 4.0) continue;

            bool have = false;
            for (ArUcoDetectionResult *ex in results) {
                if (ex.markerID == mid) { have = true; break; }
            }
            if (have) continue;

            NSArray *corners = @[
                [NSValue valueWithPoint:NSMakePoint(ordered[0].x, ordered[0].y)],
                [NSValue valueWithPoint:NSMakePoint(ordered[1].x, ordered[1].y)],
                [NSValue valueWithPoint:NSMakePoint(ordered[2].x, ordered[2].y)],
                [NSValue valueWithPoint:NSMakePoint(ordered[3].x, ordered[3].y)],
            ];
            double conf = clampf(borderScore * (1.0 - err / 4.0), 0.05, 0.99);
            [results addObject:[[ArUcoDetectionResult alloc] initWithID:mid corners:corners err:err conf:conf]];
        }
    };

    auto runOnBinary = [&](const GrayImage &bin, NSMutableArray<ArUcoDetectionResult *> *results) {
        runOnQuads(findCandidateQuads(bin), gray, results);
        if (results.count == 0) runOnQuads(findCandidateQuads(bin), blurred, results);
    };

    NSMutableArray<ArUcoDetectionResult *> *results = [NSMutableArray array];

    auto tryBinary = [&](const GrayImage &src, int closeRadius) {
        GrayImage bin;
        otsuThreshold(src, bin);
        morphCloseBinary(bin, closeRadius);
        runOnBinary(bin, results);
        if (results.count) return;
        GrayImage stretched;
        contrastStretch(src, stretched);
        adaptiveThreshold(stretched, bin, std::max(21, std::min(src.w, src.h) / 12), 8);
        morphCloseBinary(bin, closeRadius);
        runOnBinary(bin, results);
        if (results.count) return;
        adaptiveThreshold(src, bin, std::max(31, std::min(src.w, src.h) / 8), 5);
        morphCloseBinary(bin, closeRadius);
        runOnBinary(bin, results);
    };

    tryBinary(gray, 1);
    if (results.count == 0) tryBinary(blurred, 2);

    // Quiet-zone assist: white paper margin next to dark ink on brown silicone.
    if (results.count == 0) {
        auto qz = findQuietZoneAssistedQuads(blurred);
        runOnQuads(qz, gray, results);
        if (results.count == 0) runOnQuads(qz, blurred, results);
        if (results.count == 0) {
            auto qz2 = findQuietZoneAssistedQuads(gray);
            runOnQuads(qz2, gray, results);
        }
    }

    // Cut-out marker on dark brown: no quiet zone — cluster interior white cells.
    if (results.count == 0) {
        auto wc = findWhiteCellClusterQuads(blurred);
        runOnQuads(wc, gray, results);
        if (results.count == 0) runOnQuads(wc, blurred, results);
        if (results.count == 0) {
            auto wc2 = findWhiteCellClusterQuads(gray);
            runOnQuads(wc2, gray, results);
            if (results.count == 0) runOnQuads(wc2, blurred, results);
        }
    }

    if (results.count == 0) {
        if (error) {
            NSString *msg =
                @"No ArUco DICT_4X4_50 marker found. Tips: keep a white margin around the black square "
                 "(don’t place a cut marker on dark brown); avoid flash glare on sharpie ink; "
                 "use a sharp top-down still; marker ≥ ~80 px across; Expected ID = -1 unless known. "
                 "Legacy inverted prints are supported — reprinting from Companion is optional.";
            *error = [NSError errorWithDomain:ArUcoDetectorErrorDomain code:ArUcoErrorNoMarker
                                     userInfo:@{NSLocalizedDescriptionKey: msg}];
        }
        return nil;
    }
    return results;
}

@implementation ArUcoDetectorBridge

+ (NSArray<ArUcoDetectionResult *> *)detectMarkersInImage:(CGImageRef)image error:(NSError **)error {
    return detectImpl(image, error);
}

+ (ArUcoHomographyResult *)homographyFromImage:(CGImageRef)image
                         markerSideMillimeters:(double)sideMM
                                    expectedID:(NSInteger)expectedID
                                         error:(NSError **)error {
    if (sideMM <= 0.5 || sideMM > 500) {
        if (error) {
            *error = [NSError errorWithDomain:ArUcoDetectorErrorDomain code:ArUcoErrorHomographyFailed
                                     userInfo:@{NSLocalizedDescriptionKey: @"Marker side length must be between 0.5 and 500 mm."}];
        }
        return nil;
    }
    NSArray<ArUcoDetectionResult *> *markers = detectImpl(image, error);
    if (!markers) return nil;

    ArUcoDetectionResult *chosen = nil;
    if (expectedID >= 0) {
        for (ArUcoDetectionResult *m in markers) {
            if (m.markerID == expectedID) {
                chosen = m;
                break;
            }
        }
        if (!chosen) {
            if (error) {
                *error = [NSError errorWithDomain:ArUcoDetectorErrorDomain code:ArUcoErrorIDMismatch
                                         userInfo:@{NSLocalizedDescriptionKey:
                                                        [NSString stringWithFormat:
                                                            @"Expected ArUco ID %ld was not found (detected %lu marker(s)).",
                                                            (long)expectedID, (unsigned long)markers.count]}];
            }
            return nil;
        }
    } else {
        chosen = markers.firstObject;
        for (ArUcoDetectionResult *m in markers) {
            if (m.confidence > chosen.confidence) chosen = m;
        }
    }

    std::array<Vec2, 4> src;
    for (int i = 0; i < 4; ++i) {
        NSPoint p = chosen.corners[i].pointValue;
        src[i] = {p.x, p.y};
    }
    // Marker plane: TL(0,0), TR(s,0), BR(s,s), BL(0,s) in mm.
    std::array<Vec2, 4> dst = {
        Vec2{0, 0}, {sideMM, 0}, {sideMM, sideMM}, {0, sideMM}
    };
    double H[9];
    if (!solveHomographyDLT(src, dst, H)) {
        if (error) {
            *error = [NSError errorWithDomain:ArUcoDetectorErrorDomain code:ArUcoErrorHomographyFailed
                                     userInfo:@{NSLocalizedDescriptionKey: @"Failed to compute marker homography."}];
        }
        return nil;
    }

    double residual = 0;
    for (int i = 0; i < 4; ++i) {
        Vec2 p = applyH(H, src[i]);
        residual += dist(p, dst[i]);
    }
    residual /= 4.0;
    if (residual > sideMM * 0.08) {
        if (error) {
            *error = [NSError errorWithDomain:ArUcoDetectorErrorDomain code:ArUcoErrorHomographyFailed
                                     userInfo:@{NSLocalizedDescriptionKey:
                                                    [NSString stringWithFormat:
                                                        @"Homography residual too high (%.2f mm). Check print size and flatness.",
                                                        residual]}];
        }
        return nil;
    }

    NSMutableArray<NSNumber *> *mat = [NSMutableArray arrayWithCapacity:9];
    for (int i = 0; i < 9; ++i) [mat addObject:@(H[i])];

    return [[ArUcoHomographyResult alloc] initWithMarker:chosen matrix:mat sideMM:sideMM residual:residual];
}

+ (CGPoint)applyHomography:(NSArray<NSNumber *> *)matrixRowMajor toPixel:(CGPoint)pixel {
    if (matrixRowMajor.count != 9) return CGPointZero;
    double H[9];
    for (int i = 0; i < 9; ++i) H[i] = matrixRowMajor[i].doubleValue;
    Vec2 p = applyH(H, {pixel.x, pixel.y});
    return CGPointMake(p.x, p.y);
}

@end
