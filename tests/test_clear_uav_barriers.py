#!/usr/bin/env python3
# Copyright 2026 Erhan Bilgili
# SPDX-License-Identifier: LGPL-2.1-or-later
"""Record enhanced ClearUAV barriers using production handlers and tracking.

No GPU is needed. Resource/layout helpers are stubbed; synchronization conversion,
clear registration, consumption and all three enhanced barrier handlers are real.
"""

import argparse
import os
from pathlib import Path
import shlex
import subprocess

ROOT = Path(__file__).resolve().parents[1]


def block(source, marker, declaration=False):
    start = source.index(marker)
    while not declaration and source.index(";", start) < source.index("{", start):
        start = source.index(marker, start + 1)
    end = source.index("{", start) + 1
    depth = 1
    while depth:
        depth += (source[end] == "{") - (source[end] == "}")
        end += 1
    if declaration:
        end = source.index(";", end) + 1
    return source[start:end]


def generate(source):
    idl = (ROOT / "include/vkd3d_d3d12.idl").read_text()
    header = (ROOT / "libs/vkd3d/vkd3d_private.h").read_text()
    pieces = [PREFIX]
    for kind, names in (("enum", ("D3D12_BARRIER_LAYOUT", "D3D12_BARRIER_SYNC", "D3D12_BARRIER_ACCESS",
                                 "D3D12_TEXTURE_BARRIER_FLAGS")),
                        ("struct", ("D3D12_BARRIER_SUBRESOURCE_RANGE", "D3D12_GLOBAL_BARRIER",
                                   "D3D12_TEXTURE_BARRIER", "D3D12_BUFFER_BARRIER"))):
        pieces.extend(block(idl, "typedef " + kind + " " + name + "\n", True) for name in names)
    pieces.append(block(header, "struct d3d12_command_list_clear_uav_tracking\n", True))
    pieces.append(MOCKS)
    for prefix, name in (
        ("void ", "d3d12_command_list_register_clear_uav_pending"),
        ("void ", "d3d12_command_list_consume_clear_uav_global_barrier"),
        ("void ", "d3d12_command_list_consume_clear_uav_barrier_batch"),
        ("static VkPipelineStageFlags2 ", "vk_stage_flags_from_d3d12_barrier"),
        ("static VkAccessFlags2 ", "vk_access_flags_from_d3d12_barrier"),
        ("static VkPipelineStageFlags2 ", "vk_sanitize_stage_flags_for_access"),
        ("static void ", "d3d12_command_list_barrier_batch_add_global_transition"),
        ("static void ", "d3d12_command_list_process_enhanced_barrier_global"),
        ("static void ", "d3d12_command_list_process_enhanced_barrier_buffer"),
        ("static void ", "d3d12_command_list_process_enhanced_barrier_texture"),
    ):
        pieces.append(block(source, prefix + name + "("))
    return "\n".join(pieces + [TESTS])


PREFIX = r'''
#include <assert.h>
#include <stdbool.h>
#include <stdint.h>
#include <stdio.h>
#include <string.h>
#include <vulkan/vulkan.h>
typedef uint32_t UINT;
typedef uint64_t UINT64;
typedef struct d3d12_resource ID3D12Resource;
#define ARRAY_SIZE(a) (sizeof(a) / sizeof((a)[0]))
#define VKD3D_CLEAR_UAV_TRACKING_MAX_CLEARS 8
#define VKD3D_CONFIG_FLAG_IS_SET(...) false
#define VKD3D_BREADCRUMB_AUX32(...) ((void)0)
#define VKD3D_BREADCRUMB_TAG(...) ((void)0)
#define WARN(...) ((void)0)
#define FIXME(...) ((void)0)
#define FIXME_ONCE(...) ((void)0)
#define d3d12_command_list_debug_mark_label_printf(...) ((void)0)
#define d3d12_command_list_debug_mark_label(...) ((void)0)
'''

MOCKS = r'''
#define D3D12_COMMAND_LIST_TYPE_COPY 3
#define D3D12_RESOURCE_FLAG_ALLOW_DEPTH_STENCIL 2
#define VKD3D_RESOURCE_COPY_QUEUE_COMPATIBLE 1
#define VKD3D_RESOURCE_LINEAR_STAGING_COPY 2
struct d3d12_resource
{
    bool texture;
    unsigned int flags;
    struct { unsigned int Flags; } desc;
    struct { VkImage vk_image; } res;
};
struct d3d12_device
{
    struct
    {
        struct { bool attachmentFragmentShadingRate; } fragment_shading_rate_features;
        struct { bool deviceGeneratedCommands; } device_generated_commands_features;
    } device_info;
};
struct d3d12_command_list
{
    struct d3d12_device *device;
    VkQueueFlags vk_queue_flags;
    unsigned int type;
    struct
    {
        VkCommandBuffer vk_command_buffer, vk_post_indirect_barrier_commands;
        bool observes_indirect_argument_barrier;
        struct d3d12_command_list_clear_uav_tracking clear_uav;
    } cmd;
    struct { bool pending_rtas_work; } rtas_batch;
};
struct d3d12_command_list_barrier_batch
{
    VkMemoryBarrier2 vk_memory_barrier;
    VkImageMemoryBarrier2 images[VKD3D_CLEAR_UAV_TRACKING_MAX_CLEARS + 1];
    unsigned int image_count;
};
#define d3d12_resource_is_buffer(r) (!(r)->texture)
#define d3d12_resource_is_texture(r) ((r)->texture)
#define impl_from_ID3D12Resource(r) (r)
#define d3d12_device_supports_unified_layouts(d) true
#define d3d12_device_supports_ray_tracing_tier_1_0(d) false
#define d3d12_device_prefers_render_pass_resolves(d) false
#define d3d12_barrier_invalidates_indirect_arguments(...) false
#define d3d12_resource_may_alias_other_resources(...) false
#define d3d12_barrier_subresource_range_covers_resource(...) true
#define d3d12_command_list_copy_queue_fallback(...) ((void)0)
#define d3d12_command_list_flush_clears(...) ((void)0)
#define d3d12_command_list_flush_rtas_batch(...) ((void)0)
#define d3d12_command_list_merge_copy_tracking_global_barrier(...) ((void)0)
#define d3d12_command_list_notify_dsv_state_enhanced(...) 0
#define d3d12_command_list_update_subresource_data(...) ((void)0)
#define d3d12_command_list_flush_subresource_updates(...) ((void)0)
#define d3d12_command_list_track_resource_usage(...) ((void)0)
#define vk_queue_shader_stages(...) (VK_PIPELINE_STAGE_2_COMPUTE_SHADER_BIT | VK_PIPELINE_STAGE_2_FRAGMENT_SHADER_BIT)
#define vk_image_layout_from_d3d12_barrier(...) VK_IMAGE_LAYOUT_GENERAL

static void vk_image_memory_barrier_subresources_from_d3d12_texture_barrier(
        struct d3d12_command_list *list, struct d3d12_resource *resource,
        const D3D12_BARRIER_SUBRESOURCE_RANGE *range, uint32_t decay, VkImageSubresourceRange *out)
{
    *out = (VkImageSubresourceRange){VK_IMAGE_ASPECT_COLOR_BIT, 0, 1, 0, 1};
}

static void d3d12_command_list_barrier_batch_add_layout_transition(struct d3d12_command_list *list,
        struct d3d12_command_list_barrier_batch *batch, const VkImageMemoryBarrier2 *barrier)
{
    assert(batch->image_count < ARRAY_SIZE(batch->images));
    batch->images[batch->image_count++] = *barrier;
}
'''

TESTS = r'''
static unsigned int failures, cases;
#define CHECK(condition) do { if (!(condition)) { \
    fprintf(stderr, "FAIL route=%u scope=%u destination=%u: %s\n", route, scope, destination, #condition); \
    failures++; } } while (0)

static void run_case(unsigned int route, unsigned int scope, bool destination)
{
    static const D3D12_BARRIER_SYNC scopes[] = {
        D3D12_BARRIER_SYNC_CLEAR_UNORDERED_ACCESS_VIEW,
        D3D12_BARRIER_SYNC_CLEAR_UNORDERED_ACCESS_VIEW | D3D12_BARRIER_SYNC_COMPUTE_SHADING,
        D3D12_BARRIER_SYNC_ALL,
        D3D12_BARRIER_SYNC_ALL | D3D12_BARRIER_SYNC_COMPUTE_SHADING,
        D3D12_BARRIER_SYNC_COMPUTE_SHADING,
    };
    struct d3d12_device device = {0};
    struct d3d12_command_list list = {.device = &device, .vk_queue_flags = VK_QUEUE_GRAPHICS_BIT | VK_QUEUE_COMPUTE_BIT};
    struct d3d12_command_list_barrier_batch batch = {0};
    struct d3d12_resource resource = {.texture = route == 2, .res.vk_image = (VkImage)(uintptr_t)1};
    D3D12_GLOBAL_BARRIER global = {scopes[scope], D3D12_BARRIER_SYNC_COPY,
        D3D12_BARRIER_ACCESS_UNORDERED_ACCESS, D3D12_BARRIER_ACCESS_COPY_SOURCE};
    bool covers_clear = scope != 4;
    VkPipelineStageFlags2 stages;
    VkAccessFlags2 access;

    if (destination)
    {
        global.SyncBefore = D3D12_BARRIER_SYNC_COMPUTE_SHADING;
        global.SyncAfter = scopes[scope];
        global.AccessAfter = D3D12_BARRIER_ACCESS_UNORDERED_ACCESS;
        list.cmd.clear_uav.has_visible_clear_stage = true;
    }
    else
    {
        /* Use the same registration as native vkCmdFillBuffer/vkCmdClearColorImage. */
        d3d12_command_list_register_clear_uav_pending(&list.cmd.clear_uav, &resource, true);
    }

    if (route == 0)
        d3d12_command_list_process_enhanced_barrier_global(&list, &batch, &global);
    else if (route == 1)
    {
        D3D12_BUFFER_BARRIER buffer = {global.SyncBefore, global.SyncAfter,
            global.AccessBefore, global.AccessAfter, &resource, 0, UINT64_MAX};
        d3d12_command_list_process_enhanced_barrier_buffer(&list, &batch, &buffer);
    }
    else
    {
        D3D12_TEXTURE_BARRIER texture = {
            .SyncBefore = global.SyncBefore, .SyncAfter = global.SyncAfter,
            .AccessBefore = global.AccessBefore, .AccessAfter = global.AccessAfter,
            .LayoutBefore = D3D12_BARRIER_LAYOUT_UNORDERED_ACCESS,
            .LayoutAfter = destination ? D3D12_BARRIER_LAYOUT_UNORDERED_ACCESS : D3D12_BARRIER_LAYOUT_COPY_SOURCE,
            .pResource = &resource,
            .Subresources = {.NumMipLevels = 1, .NumArraySlices = 1, .NumPlanes = 1},
        };
        d3d12_command_list_process_enhanced_barrier_texture(&list, &batch, &texture);
        CHECK(batch.image_count == 1);
        CHECK(batch.images[0].image == resource.res.vk_image);
    }

    if (destination)
        CHECK(list.cmd.clear_uav.has_visible_clear_stage == !covers_clear);
    else
    {
        stages = route == 2 ? batch.images[0].srcStageMask : batch.vk_memory_barrier.srcStageMask;
        access = route == 2 ? batch.images[0].srcAccessMask : batch.vk_memory_barrier.srcAccessMask;
        CHECK(!!(stages & VK_PIPELINE_STAGE_2_CLEAR_BIT) == covers_clear);
        CHECK(!!(access & VK_ACCESS_2_TRANSFER_WRITE_BIT) == covers_clear);
        CHECK(list.cmd.clear_uav.has_clear_command == !covers_clear);
        CHECK(!!(list.cmd.clear_uav.has_global_barrier || list.cmd.clear_uav.num_pending_image_clears) == !covers_clear);
        access = route == 2 ? batch.images[0].dstAccessMask : batch.vk_memory_barrier.dstAccessMask;
        CHECK(access & VK_ACCESS_2_TRANSFER_READ_BIT);
    }
    cases++;
}

int main(void)
{
    for (unsigned int route = 0; route < 3; route++)
        for (unsigned int scope = 0; scope < 5; scope++)
            for (unsigned int destination = 0; destination < 2; destination++)
                run_case(route, scope, destination);
    printf("ClearUAV enhanced barriers: %u cases, %u failures\n", cases, failures);
    return failures ? 1 : 0;
}
'''


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, default=ROOT / "libs/vkd3d/command.c")
    parser.add_argument("--revision", help="read command.c from a git revision for a negative control")
    parser.add_argument("--generate", type=Path)
    parser.add_argument("--build-dir", type=Path)
    args = parser.parse_args()
    if not args.generate and not args.build_dir:
        parser.error("either --generate or --build-dir is required")
    source = subprocess.check_output(["git", "show", args.revision + ":libs/vkd3d/command.c"], cwd=ROOT,
                                     text=True) if args.revision else args.source.read_text()
    harness = generate(source)
    if args.generate:
        args.generate.write_text(harness)
        return
    args.build_dir.mkdir(parents=True, exist_ok=True)
    output = args.build_dir / "clear_uav_barriers.c"
    executable = args.build_dir / "clear_uav_barriers"
    output.write_text(harness)
    subprocess.run(shlex.split(os.environ.get("CC", "cc")) + ["-std=c11", "-O0", "-g", "-UNDEBUG",
        "-I" + str(ROOT / "khronos/Vulkan-Headers/include")] + shlex.split(os.environ.get("CFLAGS", "")) +
        [str(output), "-o", str(executable)], check=True)
    subprocess.run([str(executable)], check=True)


if __name__ == "__main__":
    main()
