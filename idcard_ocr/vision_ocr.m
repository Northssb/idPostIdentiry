#import <CoreImage/CoreImage.h>
#import <Foundation/Foundation.h>
#import <ImageIO/ImageIO.h>
#import <Vision/Vision.h>

static CIVector *ScaledPoint(CGPoint normalized, CGRect extent) {
    return [CIVector vectorWithX:extent.origin.x + normalized.x * extent.size.width
                              Y:extent.origin.y + normalized.y * extent.size.height];
}

static CIImage *CorrectPerspective(CIImage *image) {
    VNDetectRectanglesRequest *request = [[VNDetectRectanglesRequest alloc] init];
    request.maximumObservations = 1;
    request.minimumConfidence = 0.55;
    request.minimumAspectRatio = 0.45;
    request.maximumAspectRatio = 1.0;
    request.minimumSize = 0.25;
    VNImageRequestHandler *handler = [[VNImageRequestHandler alloc] initWithCIImage:image options:@{}];
    if (![handler performRequests:@[request] error:nil] || request.results.count == 0) {
        return image;
    }
    VNRectangleObservation *rectangle = request.results.firstObject;
    CIFilter *filter = [CIFilter filterWithName:@"CIPerspectiveCorrection"];
    if (rectangle == nil || filter == nil) {
        return image;
    }
    [filter setValue:image forKey:kCIInputImageKey];
    [filter setValue:ScaledPoint(rectangle.topLeft, image.extent) forKey:@"inputTopLeft"];
    [filter setValue:ScaledPoint(rectangle.topRight, image.extent) forKey:@"inputTopRight"];
    [filter setValue:ScaledPoint(rectangle.bottomLeft, image.extent) forKey:@"inputBottomLeft"];
    [filter setValue:ScaledPoint(rectangle.bottomRight, image.extent) forKey:@"inputBottomRight"];
    return filter.outputImage ?: image;
}

static NSArray<NSString *> *Recognize(CIImage *image) {
    VNRecognizeTextRequest *request = [[VNRecognizeTextRequest alloc] init];
    request.recognitionLevel = VNRequestTextRecognitionLevelAccurate;
    request.recognitionLanguages = @[@"zh-Hans", @"en-US"];
    request.usesLanguageCorrection = NO;
    VNImageRequestHandler *handler = [[VNImageRequestHandler alloc] initWithCIImage:image options:@{}];
    if (![handler performRequests:@[request] error:nil]) {
        return @[];
    }
    NSMutableArray<NSString *> *lines = [NSMutableArray array];
    for (VNRecognizedTextObservation *observation in request.results) {
        VNRecognizedText *candidate = [observation topCandidates:1].firstObject;
        if (candidate.string != nil) {
            [lines addObject:candidate.string];
        }
    }
    return lines;
}

static NSInteger Score(NSArray<NSString *> *lines) {
    NSString *text = [lines componentsJoinedByString:@"\n"];
    NSString *compact = [[text componentsSeparatedByCharactersInSet:
        [NSCharacterSet whitespaceAndNewlineCharacterSet]] componentsJoinedByString:@""];
    NSRegularExpression *expression = [NSRegularExpression
        regularExpressionWithPattern:@"[0-9]{17}[0-9Xx]" options:0 error:nil];
    BOOL hasId = [expression firstMatchInString:compact options:0
        range:NSMakeRange(0, compact.length)] != nil;
    BOOL hasName = [text containsString:@"姓名"];
    return (hasId ? 10000 : 0) + (hasName ? 1000 : 0) + text.length;
}

int main(int argc, const char *argv[]) {
    @autoreleasepool {
        if (argc != 2) {
            return 1;
        }
        NSString *path = [NSString stringWithUTF8String:argv[1]];
        CIImage *original = [CIImage imageWithContentsOfURL:[NSURL fileURLWithPath:path]
            options:@{kCIImageApplyOrientationProperty: @YES}];
        if (original == nil) {
            return 1;
        }

        NSArray<NSNumber *> *orientations = @[
            @(kCGImagePropertyOrientationUp),
            @(kCGImagePropertyOrientationRight),
            @(kCGImagePropertyOrientationDown),
            @(kCGImagePropertyOrientationLeft)
        ];
        NSArray<NSString *> *bestLines = @[];
        NSInteger bestScore = NSIntegerMin;
        for (NSNumber *orientation in orientations) {
            CIImage *rotated = [original imageByApplyingOrientation:orientation.intValue];
            NSArray<NSString *> *lines = Recognize(CorrectPerspective(rotated));
            NSInteger candidateScore = Score(lines);
            if (candidateScore > bestScore) {
                bestScore = candidateScore;
                bestLines = lines;
            }
        }

        NSData *json = [NSJSONSerialization dataWithJSONObject:
            @{ @"text": [bestLines componentsJoinedByString:@"\n"] } options:0 error:nil];
        if (json == nil) {
            return 1;
        }
        [[NSFileHandle fileHandleWithStandardOutput] writeData:json];
        return 0;
    }
}
