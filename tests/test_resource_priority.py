#!/usr/bin/env python3
# SPDX-License-Identifier: LGPL-2.1-or-later
"""Run resource priority checks using the generated headers from a Meson build."""

import argparse
import os
import re
from pathlib import Path
import shlex
import subprocess
import tempfile


PREAMBLE = r'''
#define VKD3D_DBG_CHANNEL VKD3D_DBG_CHANNEL_API
#include "vkd3d_private.h"
#include <math.h>
#include <stdio.h>

static struct d3d12_resource texture;
const struct ID3D12Resource2Vtbl d3d12_resource_vtbl;
static struct vkd3d_format test_format = { .vk_aspect_mask = VK_IMAGE_ASPECT_COLOR_BIT };
static unsigned int allocations;
static VkDeviceSize expected_size = 4 * 1024 * 1024;
static float expected_priority;

static HRESULT check_allocation(const struct vkd3d_allocate_memory_info *info,
        const struct vkd3d_memory_allocation *allocation)
{
    assert(allocations < 2);
    assert(allocation == (allocations ? &texture.mem : &texture.private_mem));
    assert(info->heap_properties.Type == (allocations ? D3D12_HEAP_TYPE_GPU_UPLOAD : D3D12_HEAP_TYPE_DEFAULT));
    assert(info->memory_requirements.size == expected_size);
    if (fabsf(info->vk_memory_priority - expected_priority) > 1e-7f)
    {
        fprintf(stderr, "%s priority %f, expected %f\n", allocations ? "staging" : "image",
                info->vk_memory_priority, expected_priority);
        exit(1);
    }
    allocations++;
    return S_OK;
}

#undef VK_CALL
#define VK_CALL(f) f
#define vkGetImageMemoryRequirements2(dev, image, req) ((req)->memoryRequirements = (VkMemoryRequirements){expected_size, 65536, 1})
#define vkBindImageMemory2(...) VK_SUCCESS
#undef VKD3D_CONFIG_FLAG_IS_SET
#define VKD3D_CONFIG_FLAG_IS_SET(x) false
#undef ERR
#undef FIXME
#define ERR(...) ((void)0)
#define FIXME(...) ((void)0)
#define d3d12_resource_validate_create_info(...) S_OK
#define vkd3d_malloc(size) (&texture)
#define vkd3d_free(obj) ((void)0)
#define vkd3d_view_map_init(...) S_OK
#define vkd3d_private_store_init(...) S_OK
#define vkd3d_view_map_destroy(...) ((void)0)
#define d3d_destruction_notifier_init(...) ((void)0)
#define d3d12_resource_determine_alignment(...) D3D12_DEFAULT_RESOURCE_PLACEMENT_ALIGNMENT
#define vkd3d_format_from_d3d12_resource_desc(...) (&test_format)
#define vkd3d_allocate_cookie() ((struct vkd3d_cookie){0})
#define d3d12_resource_init_subresource_layouts(...) ((void)0)
#undef FIXME_ONCE
#define FIXME_ONCE(...) ((void)0)
#define d3d12_device_add_ref(...) ((void)0)
#define vkd3d_descriptor_debug_register_resource_cookie(...) ((void)0)
#define d3d12_resource_create_vk_resource(...) S_OK
#define vkd3d_allocate_image_memory_prefers_dedicated(...) true
#define d3d12_device_allow_committed_texture_suballocation(...) false
#define vkd3d_allocate_memory(dev, allocator, info, allocation) check_allocation(info, allocation)
#define d3d12_resource_open_export_kmt(...) ((void)0)
#define vkd3d_resource_can_be_vrs(...) false
#define vkd3d_resource_make_vrs_view(...) E_FAIL
#define d3d12_resource_desc_get_sub_resource_count(dev, desc) d3d12_resource_desc_get_sub_resource_count_per_plane(desc)
#define vkd3d_format_footprint_for_plane(fmt, plane) ((struct vkd3d_format_footprint){ \
        .block_width = 1, .block_height = 1, .block_byte_count = test_format.byte_count })
#define d3d12_resource_desc_get_subresource_extent(desc, fmt, index) \
        d3d12_resource_desc_get_vk_subresource_extent(desc, NULL, \
                &(VkImageSubresourceLayers){.mipLevel = (index) % (desc)->MipLevels})
#define d3d12_resource_tag_debug_name(...) ((void)0)
#define vkd3d_allocate_heap_memory(...) E_FAIL
#define vkd3d_queue_timeline_trace_register_instantaneous(...) ((void)0)
#define d3d12_resource_destroy_and_release_device(...) ((void)0)
'''

CHECKS = r'''
int main(void)
{
    static struct d3d12_device device;
    const D3D12_HEAP_PROPERTIES properties = {D3D12_HEAP_TYPE_GPU_UPLOAD};
    const D3D12_HEAP_PROPERTIES default_heap = {D3D12_HEAP_TYPE_DEFAULT};
    static const struct
    {
        D3D12_RESOURCE_FLAGS resource_flags;
        D3D12_HEAP_FLAGS heap_flags;
        float priority;
    } cases[] =
    {
        {D3D12_RESOURCE_FLAG_NONE, D3D12_HEAP_FLAG_NONE, 0.5f},
        {D3D12_RESOURCE_FLAG_ALLOW_UNORDERED_ACCESS, D3D12_HEAP_FLAG_NONE, 0.7f},
        {D3D12_RESOURCE_FLAG_NONE, D3D12_HEAP_FLAG_CREATE_NOT_RESIDENT, 0.0f},
    };
    D3D12_RESOURCE_DESC1 desc = {.Dimension = D3D12_RESOURCE_DIMENSION_TEXTURE2D,
            .Width = 1024, .Height = 1024, .DepthOrArraySize = 1, .MipLevels = 1,
            .Format = DXGI_FORMAT_R8G8B8A8_UNORM, .SampleDesc = {1, 0}};
    struct d3d12_resource *result;
    bool correct_sizes = true;
    HRESULT hr;

    test_format.byte_count = 4;
    device.device_info.memory_priority_features.memoryPriority = VK_TRUE;
    for (unsigned int i = 0; i < ARRAY_SIZE(cases); i++)
    {
        device.device_info.pageable_device_memory_features.pageableDeviceLocalMemory =
                !!(cases[i].heap_flags & D3D12_HEAP_FLAG_CREATE_NOT_RESIDENT);
        desc.Flags = cases[i].resource_flags;
        expected_priority = cases[i].priority;
        allocations = 0;
        hr = d3d12_resource_create_committed(&device, &desc, &properties,
                cases[i].heap_flags, D3D12_RESOURCE_STATE_COMMON, NULL, 0, NULL, NULL, &result);
        assert(hr == S_OK && result == &texture && allocations == 2);
    }
    puts("Committed texture image and staging allocation priorities passed.");
    /* Both forms describe the same 2048x2048 RGBA8 texture down to its 1x1 mip. */
    device.device_info.pageable_device_memory_features.pageableDeviceLocalMemory = VK_FALSE;
    desc.Width = desc.Height = 2048;
    desc.Flags = D3D12_RESOURCE_FLAG_ALLOW_UNORDERED_ACCESS;
    expected_size = 22369620;
    expected_priority = vkd3d_convert_to_vk_prio(D3D12_RESIDENCY_PRIORITY_HIGH | 2);
    for (unsigned int i = 0; i < 2; i++)
    {
        desc.MipLevels = i ? 0 : 12;
        allocations = 0;
        hr = d3d12_resource_create_committed(&device, &desc, &properties,
                D3D12_HEAP_FLAG_NONE, D3D12_RESOURCE_STATE_COMMON, NULL, 0, NULL, NULL, &result);
        assert(hr == S_OK && result->desc.MipLevels == 12 && allocations == 2);
        assert(result->priority.d3d12priority == (D3D12_RESIDENCY_PRIORITY_HIGH | 2));
    }
    puts("Explicit and implicit full mip chain priorities and staging sizes passed.");

    /* A 16k RGBA32F subresource is 4 GiB. Two layers also exercise accumulation. */
    desc.MipLevels = 1;
    desc.Width = desc.Height = 16384;
    desc.DepthOrArraySize = 2;
    desc.Format = DXGI_FORMAT_R32G32B32A32_FLOAT;
    desc.Flags = D3D12_RESOURCE_FLAG_ALLOW_UNORDERED_ACCESS;
    test_format.byte_count = 16;
    if (sizeof(size_t) < sizeof(VkDeviceSize))
    {
        for (unsigned int i = 0; i < 2; i++)
        {
            result = NULL;
            hr = d3d12_resource_create(&device, i ? VKD3D_RESOURCE_PLACED : VKD3D_RESOURCE_COMMITTED,
                    &desc, &properties, D3D12_HEAP_FLAG_NONE, D3D12_RESOURCE_STATE_COMMON,
                    NULL, 0, NULL, &result);
            assert(hr == E_OUTOFMEMORY && !result);
        }
    }
    for (unsigned int i = 0; i < 2; i++)
    {
        if (i)
        {
            desc.Dimension = D3D12_RESOURCE_DIMENSION_BUFFER;
            desc.Width = UINT64_C(8) << 30;
            desc.Height = desc.DepthOrArraySize = 1;
            desc.Format = DXGI_FORMAT_UNKNOWN;
            desc.Layout = D3D12_TEXTURE_LAYOUT_ROW_MAJOR;
        }
        hr = d3d12_resource_create(&device, VKD3D_RESOURCE_COMMITTED, &desc, &default_heap,
                D3D12_HEAP_FLAG_NONE, D3D12_RESOURCE_STATE_COMMON, NULL, 0, NULL, &result);
        assert(hr == S_OK && result == &texture);
        if (result->priority.d3d12priority != (D3D12_RESIDENCY_PRIORITY_HIGH | 819u))
        {
            fprintf(stderr, "8 GiB %s priority %#x, expected HIGH + 819\n",
                    i ? "buffer" : "texture", result->priority.d3d12priority);
            correct_sizes = false;
        }
    }
    if (!correct_sizes)
        return 1;
    puts("Large committed texture and buffer priorities passed.");
    return 0;
}
'''


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--build-dir", type=Path, required=True, help="configured vkd3d-proton build with generated headers")
    parser.add_argument("--revision", help="read the production functions from this Git revision")
    args = parser.parse_args()
    root = Path(__file__).resolve().parents[1]
    build = args.build_dir.resolve()

    def source(name):
        path = f"libs/vkd3d/{name}.c"
        if args.revision:
            return subprocess.check_output(["git", "show", f"{args.revision}:{path}"], cwd=root, text=True)
        return (root / path).read_text()

    def function(source_text, name):
        match = re.search(r"^\w[^\n]*?\b" + re.escape(name) + r"\([^;{}]*\)\n\{", source_text, re.M)
        if not match:
            raise ValueError(f"Cannot find definition of {name}")
        return source_text[match.start():source_text.index("\n}", match.end()) + 2]

    utils, resource = source("utils"), source("resource")
    functions = [function(utils, "vkd3d_get_priority_adjust"), function(utils, "vkd3d_lerp_u32_to_float"), function(utils, "vkd3d_convert_to_vk_prio"),
                 function(resource, "max_miplevel_count"), function(resource, "vkd3d_compute_resource_layouts_from_desc"),
                 function(resource, "d3d12_resource_create"), function(resource, "d3d12_resource_create_committed")]
    with tempfile.TemporaryDirectory() as tmp:
        harness, binary = Path(tmp) / "priority.c", Path(tmp) / "priority"
        harness.write_text("\n".join([PREAMBLE, *functions, CHECKS]))
        includes = [root / p for p in ("libs/vkd3d", "include/private", "include", "khronos/Vulkan-Headers/include",
                                       "khronos/SPIRV-Headers/include")]
        includes.extend([build, build / "include", build / "libs/vkd3d-common/libvkd3d_common.a.p"])
        command = shlex.split(os.environ.get("CC", "cc"))
        subprocess.run([*command, "-std=gnu11", "-D_GNU_SOURCE", "-O2", *[f"-I{p}" for p in includes],
                        str(harness), "-o", str(binary)], check=True)
        subprocess.run([str(binary)], check=True)


if __name__ == "__main__":
    main()
