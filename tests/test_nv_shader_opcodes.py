#!/usr/bin/env python3
# Salkim modifications by Erhan Bilgili on 2026-11-17.

"""Exercise production NVAPI opcode reporting across every feature combination.

The reporting function is extracted unchanged. The capability expectations are
shared with the GPU test, and Vulkan feature types and NVAPI opcodes come from
the real headers. No Vulkan device or Windows runtime is required.
"""

import argparse
import os
from pathlib import Path
import shlex
import subprocess
import tempfile

ROOT = Path(__file__).resolve().parents[1]


def function(source, signature):
    start = source.index(signature)
    opening = source.index("{", start)
    depth = 1
    for end in range(opening + 1, len(source)):
        if source[end] == "{":
            depth += 1
        elif source[end] == "}":
            depth -= 1
            if not depth:
                return source[start:end + 1]
    raise ValueError("Unterminated function: " + signature)


def generate(source, test_source):
    production = function(source, "static BOOL STDMETHODCALLTYPE d3d12_device_vkd3d_ext_IsNvShaderExtnOpCodeSupported(")
    gpu_test = function(test_source, "void test_raytracing_lss_shader_opcode_support(void)")
    declarations = gpu_test[gpu_test.index("    enum\n"):
                            gpu_test.index("    VkPhysicalDeviceRayTracingLinearSweptSpheresFeaturesNV lss;")]
    return PREFIX + production + "\nint main(void)\n{\n" + declarations + MAIN


PREFIX = r"""
#include <stdbool.h>
#include <stdint.h>
#include <stdio.h>
#include <inttypes.h>
#include <vulkan/vulkan.h>
#include "nvShaderExtnEnums.h"

#define STDMETHODCALLTYPE
#define TRACE(...) ((void)0)
#define TRUE 1
#define FALSE 0
#define ARRAY_SIZE(a) (sizeof(a) / sizeof((a)[0]))
typedef int BOOL;
typedef uint32_t UINT32;

struct d3d12_device
{
    struct { struct { bool initialized; } nv_shader; } vendor_hacks;
    struct
    {
        VkPhysicalDeviceRayTracingLinearSweptSpheresFeaturesNV ray_tracing_linear_swept_spheres_features_nv;
        VkPhysicalDeviceRayTracingInvocationReorderFeaturesNV ray_tracing_invocation_reorder_features_nv;
        VkPhysicalDeviceRayTracingPipelineFeaturesKHR ray_tracing_pipeline_features;
        VkPhysicalDeviceRayQueryFeaturesKHR ray_query_features;
        VkPhysicalDeviceRayTracingPositionFetchFeaturesKHR ray_tracing_position_fetch_features;
    } device_info;
};
typedef struct d3d12_device d3d12_device_vkd3d_ext_iface;

static struct d3d12_device *d3d12_device_from_ID3D12DeviceExt(d3d12_device_vkd3d_ext_iface *iface)
{
    return iface;
}
"""

MAIN = r"""
    struct d3d12_device device = {0};
    unsigned int features, supported, initialized, i, checks = 0;
    bool expected, actual;

    for (initialized = 0; initialized < 2; initialized++)
    {
        for (features = 0; features < ANY_GEOMETRY; features++)
        {
            device.vendor_hacks.nv_shader.initialized = initialized;
            device.device_info.ray_tracing_linear_swept_spheres_features_nv.spheres = !!(features & SPHERES);
            device.device_info.ray_tracing_linear_swept_spheres_features_nv.linearSweptSpheres = !!(features & LSS);
            device.device_info.ray_tracing_pipeline_features.rayTracingPipeline = !!(features & PIPELINE);
            device.device_info.ray_query_features.rayQuery = !!(features & QUERY);
            device.device_info.ray_tracing_invocation_reorder_features_nv.rayTracingInvocationReorder = !!(features & SER);
            supported = features;
            if (features & (SPHERES | LSS))
                supported |= ANY_GEOMETRY;

            for (i = 0; i < ARRAY_SIZE(tests); i++)
            {
                expected = initialized && (supported & tests[i].requirements) == tests[i].requirements;
                actual = !!d3d12_device_vkd3d_ext_IsNvShaderExtnOpCodeSupported(&device, tests[i].opcode);
                if (actual != expected)
                {
                    fprintf(stderr, "Opcode %u, features %#x, initialized %u: got %u, expected %u\n",
                            tests[i].opcode, features, initialized, actual, expected);
                    return 1;
                }
                checks++;
            }
            if (d3d12_device_vkd3d_ext_IsNvShaderExtnOpCodeSupported(&device, UINT32_MAX))
            {
                fprintf(stderr, "Invalid opcode accepted with features %#x, initialized %u\n",
                        features, initialized);
                return 1;
            }
            checks++;
        }
    }
    printf("NVAPI shader opcodes: %u checks passed across 64 feature/initialization combinations\n", checks);
    return 0;
}
"""


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, default=ROOT / "libs/vkd3d/device_vkd3d_ext.c")
    parser.add_argument("--test-source", type=Path, default=ROOT / "tests/d3d12_raytracing.c")
    parser.add_argument("--generate", type=Path)
    parser.add_argument("--build-dir", type=Path)
    parser.add_argument("--sanitize", action="store_true")
    args = parser.parse_args()
    harness = generate(args.source.read_text(), args.test_source.read_text())
    if args.generate:
        args.generate.write_text(harness)
        return
    output = args.build_dir or Path(tempfile.mkdtemp(prefix="vkd3d-nv-shader-opcodes-"))
    output.mkdir(parents=True, exist_ok=True)
    generated, binary = output / "nv_shader_opcodes.c", output / "nv_shader_opcodes"
    generated.write_text(harness)
    flags = ["-std=c11", "-O2", "-g", "-UNDEBUG", "-Wall", "-Wextra", "-Werror"]
    if args.sanitize:
        flags += ["-fsanitize=address,undefined", "-fno-omit-frame-pointer", "-fno-pie", "-no-pie"]
    subprocess.run(shlex.split(os.environ.get("CC", "cc")) + flags +
                   shlex.split(os.environ.get("CFLAGS", "")) +
                   ["-I" + str(ROOT / "khronos/Vulkan-Headers/include"),
                    "-I" + str(ROOT / "include/private"), str(generated), "-o", str(binary)], check=True)
    subprocess.run([str(binary)], check=True, timeout=15)


if __name__ == "__main__":
    main()
