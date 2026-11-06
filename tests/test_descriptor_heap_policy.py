#!/usr/bin/env python3
# Copyright 2026 Erhan Bilgili
# SPDX-License-Identifier: LGPL-2.1-or-later
"""Test NVIDIA descriptor heap policy without creating a Vulkan device.

Production extension filtering, device policy, config flags and chain removal
are compiled with recorded adapter capabilities.
"""

import argparse
import os
from pathlib import Path
import re
import shlex
import subprocess
import tempfile


ROOT = Path(__file__).resolve().parents[1]


def block(source, marker, terminated=False):
    start = source.index(marker)
    end = source.index("{", start) + 1
    depth = 1
    while depth:
        depth += (source[end] == "{") - (source[end] == "}")
        end += 1
    return source[start:end] + (";" if terminated else "")


def generate(source):
    policy = source.read_text()
    device = (ROOT / "libs/vkd3d/device.c").read_text()
    private = (ROOT / "libs/vkd3d/vkd3d_private.h").read_text()
    config = (ROOT / "include/private/config_flags.h").read_text().replace(
        '#include "config_flag_decl.h"', (ROOT / "include/private/config_flag_decl.h").read_text())
    function = block(policy, "void vkd3d_physical_device_info_apply_workarounds(")
    fields = sorted(set(re.findall(r"(?:info->|device->device_info\.)([a-z0-9_]+)", function)))
    declarations = [re.search(r"^    Vk\w+ " + field + ";$", private, re.M).group(0) for field in fields]
    members = sorted(set(re.findall(r"device->vk_info\.([A-Za-z0-9_]+)", function)))
    structs = "struct vkd3d_physical_device_info {\n" + "\n".join(declarations) + "\n};\n"
    structs += "struct vkd3d_vulkan_info {\n" + "\n".join("    bool " + member + ";" for member in members) + "\n};\n"
    structs += "struct d3d12_device { struct vkd3d_physical_device_info device_info; struct vkd3d_vulkan_info vk_info; };"
    version_macros = "\n".join(re.findall(r"^#define VKD3D_DRIVER_VERSION_.*_NV\([^\n]+", private, re.M))
    optional_macros = device[device.index("#define VK_EXTENSION(name"):device.index("static const struct vkd3d_optional_extension_info optional_instance_extensions")]
    heap_entry = re.search(r"^    VK_EXTENSION_DISABLE_COND\(EXT_DESCRIPTOR_HEAP,[^\n]+", device, re.M).group(0)
    return "\n".join([
        PREFIX, config, "union vkd3d_config_flags vkd3d_config_flags;", version_macros, structs,
        block(policy, "enum vkd3d_application_feature_override", True),
        "static enum vkd3d_application_feature_override vkd3d_application_feature_override;",
        block(private, "static inline void vk_remove_struct("), function,
        block(device, "struct vkd3d_optional_extension_info\n", True), optional_macros,
        "static const struct vkd3d_optional_extension_info heap_extensions[] = {", heap_entry, "};",
        "static bool is_extension_disabled(const char *name) { (void)name; return false; }",
        block(device, "static bool has_extension("),
        block(device, "static unsigned int vkd3d_check_extensions("), TESTS])


PREFIX = r'''
#ifdef NDEBUG
#undef NDEBUG
#endif
#include <assert.h>
#include <stdbool.h>
#include <stddef.h>
#include <stdint.h>
#include <stdio.h>
#include <string.h>
#include <vulkan/vulkan.h>
#define ARRAY_SIZE(a) (sizeof(a) / sizeof((a)[0]))
#define STATIC_ASSERT(expr) _Static_assert(expr, #expr)
#define WARN(...) ((void)0)
#define ERR(...) ((void)0)
#define TRACE(...) ((void)0)
#define INFO(...) ((void)0)
static unsigned int vkd3d_popcount(uint32_t value) { return __builtin_popcount(value); }
'''

TESTS = r'''
static unsigned int cases;

static void check_policy(VkDriverId driver, uint32_t version, bool opt_in, bool opt_out,
        bool skip_workarounds, bool extension, bool feature, bool policy_blocks)
{
    static const VkExtensionProperties available[] = { { VK_EXT_DESCRIPTOR_HEAP_EXTENSION_NAME, 1 } };
    struct d3d12_device device = {0};
    struct vkd3d_physical_device_info *info = &device.device_info;
    VkBaseInStructure before = { .sType = VK_STRUCTURE_TYPE_PHYSICAL_DEVICE_VULKAN_1_1_FEATURES };
    VkBaseInStructure after = { .sType = VK_STRUCTURE_TYPE_PHYSICAL_DEVICE_VULKAN_1_3_FEATURES };
    VkPhysicalDeviceDescriptorHeapFeaturesEXT expected_heap;
    union vkd3d_config_flags original;
    unsigned int count;
    bool discovered, disabled;

    vkd3d_config_flags = VKD3D_CONFIG_FLAG_INIT(.DESCRIPTOR_HEAP = opt_in, .NO_DESCRIPTOR_HEAP = opt_out,
            .SKIP_DRIVER_WORKAROUNDS = skip_workarounds, .NO_DXR = 1);
    original = vkd3d_config_flags;
    count = vkd3d_check_extensions(available, extension ? ARRAY_SIZE(available) : 0,
            NULL, 0, heap_extensions, ARRAY_SIZE(heap_extensions), NULL, 0, NULL, 0, NULL,
            &device.vk_info, "device");
    discovered = extension && !opt_out;
    assert(count == discovered);
    assert(device.vk_info.EXT_descriptor_heap == discovered);

    info->features2.sType = VK_STRUCTURE_TYPE_PHYSICAL_DEVICE_FEATURES_2;
    info->features2.pNext = &before;
    before.pNext = discovered ? (VkBaseInStructure *)&info->descriptor_heap_features : &after;
    if (discovered)
    {
        info->descriptor_heap_features.sType = VK_STRUCTURE_TYPE_PHYSICAL_DEVICE_DESCRIPTOR_HEAP_FEATURES_EXT;
        info->descriptor_heap_features.pNext = &after;
        info->descriptor_heap_features.descriptorHeap = feature;
        info->descriptor_heap_features.descriptorHeapCaptureReplay = feature;
    }
    expected_heap = info->descriptor_heap_features;
    info->vulkan_1_2_properties.driverID = driver;
    info->properties2.properties.driverVersion = version;
    device.vk_info.KHR_fragment_shader_barycentric = true;
    disabled = discovered && policy_blocks && !opt_in;

    vkd3d_physical_device_info_apply_workarounds(info, &device);
    assert(!memcmp(&vkd3d_config_flags, &original, sizeof(original)));
    assert(device.vk_info.EXT_descriptor_heap == (discovered && !disabled));
    assert(info->features2.pNext == &before);
    assert(before.pNext == (discovered && !disabled ? (VkBaseInStructure *)&info->descriptor_heap_features : &after));
    assert(after.pNext == NULL);
    assert(before.sType == VK_STRUCTURE_TYPE_PHYSICAL_DEVICE_VULKAN_1_1_FEATURES);
    assert(after.sType == VK_STRUCTURE_TYPE_PHYSICAL_DEVICE_VULKAN_1_3_FEATURES);
    if (disabled)
        memset(&expected_heap, 0, sizeof(expected_heap));
    assert(!memcmp(&info->descriptor_heap_features, &expected_heap, sizeof(expected_heap)));
    cases++;
}

static void check_chain_position(unsigned int position)
{
    struct d3d12_device device = {0};
    struct vkd3d_physical_device_info *info = &device.device_info;
    VkBaseInStructure node = { .sType = VK_STRUCTURE_TYPE_PHYSICAL_DEVICE_VULKAN_1_1_FEATURES };
    VkPhysicalDeviceDescriptorHeapFeaturesEXT empty = {0};

    vkd3d_config_flags = (union vkd3d_config_flags)VKD3D_CONFIG_FLAGS_NONE;
    device.vk_info.EXT_descriptor_heap = true;
    info->vulkan_1_2_properties.driverID = VK_DRIVER_ID_NVIDIA_PROPRIETARY;
    info->properties2.properties.driverVersion = VKD3D_DRIVER_VERSION_MAKE_NV(615, 71, 9);
    info->descriptor_heap_features.sType = VK_STRUCTURE_TYPE_PHYSICAL_DEVICE_DESCRIPTOR_HEAP_FEATURES_EXT;
    info->descriptor_heap_features.descriptorHeap = true;
    info->features2.pNext = position == 0 ? (void *)&info->descriptor_heap_features : (void *)&node;
    if (position == 0)
        info->descriptor_heap_features.pNext = &node;
    else if (position == 1)
        node.pNext = (VkBaseInStructure *)&info->descriptor_heap_features;
    /* Position 2 exercises the feature struct without a corresponding chain node. */
    vkd3d_physical_device_info_apply_workarounds(info, &device);
    assert(info->features2.pNext == &node);
    assert(!node.pNext);
    assert(!device.vk_info.EXT_descriptor_heap);
    assert(!memcmp(&info->descriptor_heap_features, &empty, sizeof(empty)));
    cases++;
}

static void check_mixed_adapters(void)
{
    struct d3d12_device old = {0}, current = {0}, radv = {0};
    union vkd3d_config_flags original;

    vkd3d_config_flags = VKD3D_CONFIG_FLAG(NO_DXR);
    original = vkd3d_config_flags;
    old.vk_info.EXT_descriptor_heap = current.vk_info.EXT_descriptor_heap = radv.vk_info.EXT_descriptor_heap = true;
    old.device_info.descriptor_heap_features.descriptorHeap = true;
    current.device_info.descriptor_heap_features.descriptorHeap = true;
    radv.device_info.descriptor_heap_features.descriptorHeap = true;
    old.device_info.vulkan_1_2_properties.driverID = VK_DRIVER_ID_NVIDIA_PROPRIETARY;
    current.device_info.vulkan_1_2_properties.driverID = VK_DRIVER_ID_NVIDIA_PROPRIETARY;
    radv.device_info.vulkan_1_2_properties.driverID = VK_DRIVER_ID_MESA_RADV;
    old.device_info.properties2.properties.driverVersion = VKD3D_DRIVER_VERSION_MAKE_NV(615, 71, 9);
    current.device_info.properties2.properties.driverVersion = VKD3D_DRIVER_VERSION_MAKE_NV(620, 70, 6);
    radv.device_info.properties2.properties.driverVersion = 1;
    vkd3d_physical_device_info_apply_workarounds(&old.device_info, &old);
    vkd3d_physical_device_info_apply_workarounds(&current.device_info, &current);
    vkd3d_physical_device_info_apply_workarounds(&radv.device_info, &radv);
    assert(!old.vk_info.EXT_descriptor_heap);
    assert(current.vk_info.EXT_descriptor_heap && radv.vk_info.EXT_descriptor_heap);
    assert(current.device_info.descriptor_heap_features.descriptorHeap);
    assert(radv.device_info.descriptor_heap_features.descriptorHeap);
    assert(!memcmp(&vkd3d_config_flags, &original, sizeof(original)));
    cases++;
}

int main(void)
{
    static const struct { uint32_t version; bool blocked; } versions[] =
    {
        { VKD3D_DRIVER_VERSION_MAKE_NV(615, 71, 9), true },
        { VKD3D_DRIVER_VERSION_MAKE_NV(619, 99, 99), true },
        { VKD3D_DRIVER_VERSION_MAKE_NV(620, 69, 99), true },
        { VKD3D_DRIVER_VERSION_MAKE_NV(620, 70, 5), true },
        { VKD3D_DRIVER_VERSION_MAKE_NV(620, 70, 6) - 1, true },
        { VKD3D_DRIVER_VERSION_MAKE_NV(620, 70, 6), false },
        { VKD3D_DRIVER_VERSION_MAKE_NV(620, 70, 6) + 1, false },
        { VKD3D_DRIVER_VERSION_MAKE_NV(620, 70, 7), false },
        { VKD3D_DRIVER_VERSION_MAKE_NV(620, 71, 0), false },
        { VKD3D_DRIVER_VERSION_MAKE_NV(621, 0, 0), false },
    };
    static const VkDriverId vendors[] = { VK_DRIVER_ID_MESA_RADV, VK_DRIVER_ID_INTEL_OPEN_SOURCE_MESA,
        VK_DRIVER_ID_MESA_NVK, VK_DRIVER_ID_AMD_PROPRIETARY };
    unsigned int version, flags, extension, feature, vendor;

    for (version = 0; version < ARRAY_SIZE(versions); version++)
    for (flags = 0; flags < 8; flags++)
    for (extension = 0; extension < 2; extension++)
    for (feature = 0; feature < 2; feature++)
        check_policy(VK_DRIVER_ID_NVIDIA_PROPRIETARY, versions[version].version,
                flags & 1, flags & 2, flags & 4, extension, feature, versions[version].blocked);
    for (vendor = 0; vendor < ARRAY_SIZE(vendors); vendor++)
    for (flags = 0; flags < 8; flags++)
        check_policy(vendors[vendor], 1, flags & 1, flags & 2, flags & 4, true, true, false);
    for (version = 0; version < 3; version++)
        check_chain_position(version);
    check_mixed_adapters();
    printf("Descriptor heap policy tests passed (%u cases).\n", cases);
    return 0;
}
'''


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, default=ROOT / "libs/vkd3d/device_workarounds.c")
    parser.add_argument("--generate", type=Path)
    parser.add_argument("--build-dir", type=Path)
    args = parser.parse_args()
    harness = generate(args.source)
    if args.generate:
        args.generate.write_text(harness)
    else:
        build = args.build_dir or Path(tempfile.mkdtemp(prefix="descriptor-heap-policy-"))
        build.mkdir(parents=True, exist_ok=True)
        source, binary = build / "descriptor_heap_policy.c", build / "descriptor_heap_policy"
        source.write_text(harness)
        command = shlex.split(os.environ.get("CC", "cc"))
        command += ["-std=c11", "-O1", "-g", "-UNDEBUG", "-Wall", "-Wextra", "-Werror",
                    "-Wno-unused-parameter", "-I" + str(ROOT / "khronos/Vulkan-Headers/include")]
        if os.environ.get("DESCRIPTOR_TEST_SANITIZE") == "1":
            command += ["-fsanitize=address,undefined", "-fno-omit-frame-pointer", "-no-pie"]
        subprocess.run(command + [str(source), "-o", str(binary)], check=True)
        subprocess.run([str(binary)], check=True, timeout=10)
        print("Retained harness:", build)
