// dctl_host.swift
//
// Compiles a .dctl file as a Metal compute kernel at runtime and runs it over
// a PNG on this machine's GPU. No Xcode needed: MTLDevice.makeLibrary(source:)
// uses the Metal compiler that ships with macOS.
//
// This is not an emulator. The DCTL body text is handed to the same GPU
// compiler Resolve uses on macOS, so a pass here means the code is valid GPU
// code and the pixels are real GPU output. What it does NOT prove is that
// Resolve accepts the file: that needs Resolve itself.
//
//   swiftc -O dctl_host.swift -o dctl_host
//   ./dctl_host shader.dctl in.png out.png [--set name=value ...] [--emit-metal f]

import Foundation
import Metal
import CoreGraphics
import ImageIO
import UniformTypeIdentifiers

// ---------------------------------------------------------------- arguments

var args = Array(CommandLine.arguments.dropFirst())
var overrides: [String: String] = [:]
var emitMetal: String? = nil
var positional: [String] = []

var i = 0
while i < args.count {
    switch args[i] {
    case "--set":
        i += 1
        let kv = args[i].split(separator: "=", maxSplits: 1).map(String.init)
        overrides[kv[0]] = kv[1]
    case "--emit-metal":
        i += 1
        emitMetal = args[i]
    default:
        positional.append(args[i])
    }
    i += 1
}

guard positional.count >= 3 else {
    FileHandle.standardError.write("usage: dctl_host shader.dctl in.png out.png [--set k=v]\n".data(using: .utf8)!)
    exit(2)
}
let dctlPath = positional[0]
let inPath   = positional[1]
let outPath  = positional[2]

// -------------------------------------------------- DCTL to Metal translation

// The DCTL runtime, expressed in Metal. Every name here is taken from the
// function list in Blackmagic's DaVinciCTL/README.txt.
let prelude = """
#include <metal_stdlib>
using namespace metal;

#define __DEVICE__ inline
#define __CONSTANT__ constant
#define __CONSTANTREF__ constant
#define make_float2 float2
#define make_float3 float3
#define make_float4 float4

struct DCTLTex { device const float *p; int w; int h; };
#define __TEXTURE__ DCTLTex
inline float _tex2D(DCTLTex t, int x, int y) {
    x = clamp(x, 0, t.w - 1);
    y = clamp(y, 0, t.h - 1);
    return t.p[y * t.w + x];
}

inline float _saturatef(float x)              { return saturate(x); }
inline float _clampf(float x, float a, float b){ return clamp(x, a, b); }
inline float _fabs(float x)                   { return fabs(x); }
inline float _powf(float x, float y)          { return pow(x, y); }
inline float _logf(float x)                   { return log(x); }
inline float _log2f(float x)                  { return log2(x); }
inline float _log10f(float x)                 { return log10(x); }
inline float _expf(float x)                   { return exp(x); }
inline float _exp2f(float x)                  { return exp2(x); }
inline float _exp10f(float x)                 { return pow(10.0f, x); }
inline float _copysignf(float x, float y)     { return copysign(x, y); }
inline float _fmaxf(float x, float y)         { return max(x, y); }
inline float _fminf(float x, float y)         { return min(x, y); }
inline float _sqrtf(float x)                  { return sqrt(x); }
inline float _ceilf(float x)                  { return ceil(x); }
inline float _floorf(float x)                 { return floor(x); }
inline float _truncf(float x)                 { return trunc(x); }
inline float _round(float x)                  { return round(x); }
inline float _fmod(float x, float y)          { return fmod(x, y); }
inline float _hypotf(float x, float y)        { return sqrt(x * x + y * y); }
inline float _cosf(float x)                   { return cos(x); }
inline float _sinf(float x)                   { return sin(x); }
inline float _cospif(float x)                 { return cospi(x); }
inline float _sinpif(float x)                 { return sinpi(x); }
inline float _tanf(float x)                   { return tan(x); }
inline float _acosf(float x)                  { return acos(x); }
inline float _asinf(float x)                  { return asin(x); }
inline float _atan2f(float y, float x)        { return atan2(y, x); }
inline float _acoshf(float x)                 { return acosh(x); }
inline float _asinhf(float x)                 { return asinh(x); }
inline float _atanhf(float x)                 { return atanh(x); }
inline float _coshf(float x)                  { return cosh(x); }
inline float _sinhf(float x)                  { return sinh(x); }
inline float _tanhf(float x)                  { return tanh(x); }
inline float _fdimf(float x, float y)         { return fdim(x, y); }
inline float _fmaf(float x, float y, float z) { return fma(x, y, z); }
inline float _rsqrtf(float x)                 { return rsqrt(x); }
inline float _fdivide(float x, float y)       { return x / y; }
inline float _frecip(float x)                 { return 1.0f / x; }
#define _mix mix

constant int TIMELINE_FRAME_INDEX = 1;
constant float TRANSITION_PROGRESS = 0.0f;

"""

var raw: String
do { raw = try String(contentsOfFile: dctlPath, encoding: .utf8) }
catch { FileHandle.standardError.write("cannot read \(dctlPath)\n".data(using: .utf8)!); exit(2) }

// Turn each DEFINE_UI_PARAMS into a compile time constant. In Resolve these
// become OFX controls; here they become the default, or a --set override.
var defines: [String] = []
var uiReport: [(String, String, String)] = []
var body: [String] = []

let uiRe = try! NSRegularExpression(pattern: #"^\s*DEFINE_UI_PARAMS\s*\((.*)\)\s*$"#)

for line in raw.components(separatedBy: .newlines) {
    if line.trimmingCharacters(in: .whitespaces).hasPrefix("DEFINE_UI_TOOLTIP") { body.append(""); continue }
    if line.trimmingCharacters(in: .whitespaces).hasPrefix("DEFINE_DCTL_ALPHA_MODE") { body.append(""); continue }

    let ns = line as NSString
    if let m = uiRe.firstMatch(in: line, range: NSRange(location: 0, length: ns.length)) {
        // split on commas that are not inside braces
        let inner = ns.substring(with: m.range(at: 1))
        var parts: [String] = []
        var depth = 0, cur = ""
        for ch in inner {
            if ch == "{" { depth += 1 }
            if ch == "}" { depth -= 1 }
            if ch == "," && depth == 0 { parts.append(cur); cur = "" } else { cur.append(ch) }
        }
        parts.append(cur)
        let fields = parts.map { $0.trimmingCharacters(in: .whitespaces) }
        let name = fields[0]
        let kind = fields.count > 2 ? fields[2] : "?"
        var value: String
        if kind == "DCTLUI_COLOR_PICKER" {
            value = "float3(\(fields[3]), \(fields[4]), \(fields[5]))"
        } else {
            value = fields.count > 3 ? fields[3] : "0"
        }
        if let ov = overrides[name] { value = ov }
        defines.append("#define \(name) (\(value))")
        uiReport.append((name, kind, value))
        body.append("")
        continue
    }
    body.append(line)
}

// Resolve accepts four transform signatures. Detect which one this file uses
// and call it that way, otherwise a plain float3 DCTL fails to link here for a
// reason Resolve would never report.
let sigLine = body.first(where: { $0.contains("transform(") && $0.contains("p_Width") }) ?? ""
let usesTextures = sigLine.contains("__TEXTURE__")
let returnsAlpha = sigLine.contains("float4 transform")

var callArgs = usesTextures ? "tR, tG, tB, tA" : "inR[i], inG[i], inB[i]"
if !usesTextures && returnsAlpha { callArgs += ", inA[i]" }
let callExpr = returnsAlpha
    ? "const float4 v = transform(W, H, x, y, \(callArgs));"
    : "const float3 rgb = transform(W, H, x, y, \(callArgs));\n    const float4 v = float4(rgb, inA[i]);"

let kernelWrap = """

kernel void dctl_main(device const float *inR   [[buffer(0)]],
                      device const float *inG   [[buffer(1)]],
                      device const float *inB   [[buffer(2)]],
                      device const float *inA   [[buffer(3)]],
                      device float *outRGBA     [[buffer(4)]],
                      constant int &W           [[buffer(5)]],
                      constant int &H           [[buffer(6)]],
                      uint2 gid [[thread_position_in_grid]])
{
    if ((int)gid.x >= W || (int)gid.y >= H) return;
    const int x = (int)gid.x;
    const int y = (int)gid.y;
    const int i = y * W + x;
    DCTLTex tR = { inR, W, H };
    DCTLTex tG = { inG, W, H };
    DCTLTex tB = { inB, W, H };
    DCTLTex tA = { inA, W, H };
    \(callExpr)
    const int o = i * 4;
    outRGBA[o + 0] = v.x;
    outRGBA[o + 1] = v.y;
    outRGBA[o + 2] = v.z;
    outRGBA[o + 3] = v.w;
}
"""

let source = prelude + defines.joined(separator: "\n") + "\n\n"
           + body.joined(separator: "\n") + "\n" + kernelWrap

if let p = emitMetal { try? source.write(toFile: p, atomically: true, encoding: .utf8) }

// ---------------------------------------------------------------- image load

guard let srcRef = CGImageSourceCreateWithURL(URL(fileURLWithPath: inPath) as CFURL, nil),
      let cg = CGImageSourceCreateImageAtIndex(srcRef, 0, nil) else {
    FileHandle.standardError.write("cannot read image \(inPath)\n".data(using: .utf8)!); exit(2)
}
let W = cg.width, H = cg.height
var rgba8 = [UInt8](repeating: 0, count: W * H * 4)
let cs = CGColorSpaceCreateDeviceRGB()
rgba8.withUnsafeMutableBytes { buf in
    let ctx = CGContext(data: buf.baseAddress, width: W, height: H, bitsPerComponent: 8,
                        bytesPerRow: W * 4, space: cs,
                        bitmapInfo: CGImageAlphaInfo.premultipliedLast.rawValue)!
    ctx.draw(cg, in: CGRect(x: 0, y: 0, width: W, height: H))
}

var planeR = [Float](repeating: 0, count: W * H)
var planeG = planeR, planeB = planeR, planeA = planeR
for p in 0..<(W * H) {
    planeR[p] = Float(rgba8[p * 4 + 0]) / 255.0
    planeG[p] = Float(rgba8[p * 4 + 1]) / 255.0
    planeB[p] = Float(rgba8[p * 4 + 2]) / 255.0
    planeA[p] = Float(rgba8[p * 4 + 3]) / 255.0
}

// ------------------------------------------------------------------- compile

guard let dev = MTLCreateSystemDefaultDevice() else { print("NO GPU"); exit(1) }
let t0 = Date()
let lib: MTLLibrary
do { lib = try dev.makeLibrary(source: source, options: nil) }
catch {
    FileHandle.standardError.write("DCTL BUILD ERROR\n\(error)\n".data(using: .utf8)!)
    exit(1)
}
let compileMs = Date().timeIntervalSince(t0) * 1000

let fn = lib.makeFunction(name: "dctl_main")!
let pipe = try! dev.makeComputePipelineState(function: fn)
let q = dev.makeCommandQueue()!

func buf(_ a: [Float]) -> MTLBuffer {
    return a.withUnsafeBytes { dev.makeBuffer(bytes: $0.baseAddress!, length: $0.count, options: .storageModeShared)! }
}
let bR = buf(planeR), bG = buf(planeG), bB = buf(planeB), bA = buf(planeA)
let bOut = dev.makeBuffer(length: W * H * 4 * MemoryLayout<Float>.size, options: .storageModeShared)!
var w32 = Int32(W), h32 = Int32(H)

// one warm up, then a timed run
var runMs = 0.0
for pass in 0..<2 {
    let t = Date()
    let cb = q.makeCommandBuffer()!
    let enc = cb.makeComputeCommandEncoder()!
    enc.setComputePipelineState(pipe)
    enc.setBuffer(bR, offset: 0, index: 0)
    enc.setBuffer(bG, offset: 0, index: 1)
    enc.setBuffer(bB, offset: 0, index: 2)
    enc.setBuffer(bA, offset: 0, index: 3)
    enc.setBuffer(bOut, offset: 0, index: 4)
    enc.setBytes(&w32, length: 4, index: 5)
    enc.setBytes(&h32, length: 4, index: 6)
    let tg = MTLSize(width: 16, height: 16, depth: 1)
    enc.dispatchThreads(MTLSize(width: W, height: H, depth: 1), threadsPerThreadgroup: tg)
    enc.endEncoding()
    cb.commit()
    cb.waitUntilCompleted()
    if pass == 1 { runMs = Date().timeIntervalSince(t) * 1000 }
}

// ------------------------------------------------------------------ write out

let outF = bOut.contents().bindMemory(to: Float.self, capacity: W * H * 4)
var out8 = [UInt8](repeating: 0, count: W * H * 4)
for p in 0..<(W * H * 4) {
    out8[p] = UInt8(max(0, min(255, (outF[p] * 255.0).rounded())))
}
// straight alpha: store the matte in alpha but keep the RGB unpremultiplied
out8.withUnsafeMutableBytes { b in
    let ctx = CGContext(data: b.baseAddress, width: W, height: H, bitsPerComponent: 8,
                        bytesPerRow: W * 4, space: cs,
                        bitmapInfo: CGImageAlphaInfo.noneSkipLast.rawValue)!
    if let img = ctx.makeImage(),
       let dst = CGImageDestinationCreateWithURL(URL(fileURLWithPath: outPath) as CFURL,
                                                 UTType.png.identifier as CFString, 1, nil) {
        CGImageDestinationAddImage(dst, img, nil)
        CGImageDestinationFinalize(dst)
    }
}
// matte as its own file
let mattePath = (outPath as NSString).deletingPathExtension + "_matte.png"
var m8 = [UInt8](repeating: 0, count: W * H * 4)
for p in 0..<(W * H) {
    let v = out8[p * 4 + 3]
    m8[p * 4 + 0] = v; m8[p * 4 + 1] = v; m8[p * 4 + 2] = v; m8[p * 4 + 3] = 255
}
m8.withUnsafeMutableBytes { b in
    let ctx = CGContext(data: b.baseAddress, width: W, height: H, bitsPerComponent: 8,
                        bytesPerRow: W * 4, space: cs,
                        bitmapInfo: CGImageAlphaInfo.noneSkipLast.rawValue)!
    if let img = ctx.makeImage(),
       let dst = CGImageDestinationCreateWithURL(URL(fileURLWithPath: mattePath) as CFURL,
                                                 UTType.png.identifier as CFString, 1, nil) {
        CGImageDestinationAddImage(dst, img, nil)
        CGImageDestinationFinalize(dst)
    }
}

print("GPU               : \(dev.name)")
print("DCTL              : \(dctlPath)")
print("UI params found   : \(uiReport.count)")
for (n, k, v) in uiReport { print(String(format: "  %-12@ %-22@ = %@", n as NSString, k as NSString, v as NSString)) }
print("Metal compile     : OK in \(String(format: "%.0f", compileMs)) ms")
print("Frame             : \(W)x\(H)")
print("GPU run           : \(String(format: "%.2f", runMs)) ms  (\(String(format: "%.1f", 1000.0 / runMs)) fps)")
print("Wrote             : \(outPath)")
print("Wrote matte       : \(mattePath)")
