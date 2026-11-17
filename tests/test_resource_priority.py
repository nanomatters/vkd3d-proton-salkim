#!/usr/bin/env python3
# SPDX-License-Identifier: LGPL-2.1-or-later
"""Run resource priority checks using the generated headers from a Meson build."""

import argparse
import os
from pathlib import Path
import shlex
import subprocess
import tempfile


PREAMBLE = r'''
#define VKD3D_DBG_CHANNEL VKD3D_DBG_CHANNEL_API
#include "vkd3d_private.h"
#include <stdio.h>

static struct d3d12_resource texture;
static unsigned int allocations;
static float expected_priority;

static HRESULT check_allocation(const struct vkd3d_allocate_memory_info *info,
        const struct vkd3d_memory_allocation *allocation)
{
    assert(allocations < 2);
    assert(allocation == (allocations ? &texture.mem : &texture.private_mem));
    assert(info->heap_properties.Type == (allocations ? D3D12_HEAP_TYPE_GPU_UPLOAD : D3D12_HEAP_TYPE_DEFAULT));
    assert(info->memory_requirements.size == 4 * 1024 * 1024);
    if (info->vk_memory_priority != expected_priority)
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
#define vkGetImageMemoryRequirements2(dev, image, req) ((req)->memoryRequirements = (VkMemoryRequirements){4 * 1024 * 1024, 65536, 1})
#define vkBindImageMemory2(...) VK_SUCCESS
#undef VKD3D_CONFIG_FLAG_IS_SET
#define VKD3D_CONFIG_FLAG_IS_SET(x) false
#undef ERR
#undef FIXME
#define ERR(...) ((void)0)
#define FIXME(...) ((void)0)
#define d3d12_resource_create(dev, flags, desc, props, heap_flags, state, clear, count, formats, out) (*(out) = &texture, S_OK)
#define d3d12_resource_create_vk_resource(...) S_OK
#define vkd3d_allocate_image_memory_prefers_dedicated(...) true
#define d3d12_device_allow_committed_texture_suballocation(...) false
#define vkd3d_allocate_memory(dev, allocator, info, allocation) check_allocation(info, allocation)
#define d3d12_resource_open_export_kmt(...) ((void)0)
#define vkd3d_resource_can_be_vrs(...) false
#define vkd3d_resource_make_vrs_view(...) E_FAIL
#define vkd3d_compute_resource_layouts_from_desc(...) (4 * 1024 * 1024)
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
    struct d3d12_resource *result;
    HRESULT hr;

    texture.flags = VKD3D_RESOURCE_LINEAR_STAGING_COPY;
    texture.desc.Dimension = D3D12_RESOURCE_DIMENSION_TEXTURE2D;
    texture.desc.Width = texture.desc.Height = 1024;
    texture.desc.Alignment = D3D12_DEFAULT_RESOURCE_PLACEMENT_ALIGNMENT;

    for (unsigned int i = 0; i < 3; i++)
    {
        texture.priority.residency_count = i != 2;
        texture.priority.d3d12priority = i ? D3D12_RESIDENCY_PRIORITY_HIGH : D3D12_RESIDENCY_PRIORITY_NORMAL;
        expected_priority = i == 2 ? 0.f : i == 1 ? 0.7f : 0.5f;
        allocations = 0;
        hr = d3d12_resource_create_committed(&device, &texture.desc, &properties,
                D3D12_HEAP_FLAG_NONE, D3D12_RESOURCE_STATE_COMMON, NULL, 0, NULL, NULL, &result);
        assert(hr == S_OK && result == &texture && allocations == 2);
    }
    puts("Committed texture image and staging allocation priorities passed.");
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
        start = source_text.index(name + "(")
        start = source_text.rfind("\n", 0, start) + 1
        return source_text[start:source_text.index("\n}", start) + 2]

    utils, resource = source("utils"), source("resource")
    functions = [function(utils, "vkd3d_lerp_u32_to_float"), function(utils, "vkd3d_convert_to_vk_prio"),
                 function(resource, "d3d12_resource_create_committed")]
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
