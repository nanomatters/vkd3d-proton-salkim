#!/usr/bin/env python3
# Copyright 2026 Erhan Bilgili
# SPDX-License-Identifier: LGPL-2.1-or-later
"""Record production discard barriers and batch overlap handling without a GPU."""

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
    return source[start:source.index(";", end) + 1 if declaration else end]


def generate(source):
    header = (ROOT / "libs/vkd3d/vkd3d_private.h").read_text()
    defines = [next(line for line in text.splitlines() if line.startswith("#define " + name + " "))
               for text, name in ((header, "VKD3D_COPY_TEXTURE_REGION_MAX_BATCH_SIZE"),
                                  (source, "MAX_BATCHED_IMAGE_BARRIERS"))]
    pieces = [PREFIX, *defines, block(source, "struct d3d12_command_list_barrier_batch\n", True), MOCKS]
    for prefix, name in (
        ("static void ", "d3d12_command_list_barrier_batch_init"),
        ("static void ", "d3d12_command_list_barrier_batch_end"),
        ("static bool ", "vk_subresource_range_overlaps"),
        ("static bool ", "vk_image_barrier_overlaps_subresource"),
        ("static void ", "d3d12_command_list_barrier_batch_add_layout_transition"),
        ("static void ", "d3d12_command_list_discard_attachment_barrier"),
    ):
        pieces.append(block(source, prefix + name + "("))
    handler = block(source, "static void STDMETHODCALLTYPE d3d12_command_list_DiscardResource(")
    branch = block(handler, "    else\n    {\n        struct d3d12_command_list_barrier_batch batch;")
    pieces.append(PARTIAL_WRAPPER + branch[branch.index("{"):] + "\n}\n")
    return "\n".join(pieces + [TESTS])


PREFIX = r'''
#include <assert.h>
#include <stdbool.h>
#include <stdint.h>
#include <stdio.h>
#include <string.h>
#define VK_NO_PROTOTYPES
#include <vulkan/vulkan.h>
#define ARRAY_SIZE(a) (sizeof(a) / sizeof((a)[0]))
#define D3D12_COMMAND_LIST_TYPE_DIRECT 0
#define D3D12_RESOURCE_FLAG_ALLOW_RENDER_TARGET 1
#define D3D12_RESOURCE_FLAG_ALLOW_DEPTH_STENCIL 2
#define D3D12_RESOURCE_FLAG_ALLOW_UNORDERED_ACCESS 4
#define VKD3D_RESOURCE_RESERVED 8
#define VK_CALL(call) call
#define VKD3D_CONFIG_FLAG_IS_SET(flag) breadcrumbs
#define VKD3D_BREADCRUMB_AUX32(...) ((void)0)
#define VKD3D_BREADCRUMB_COMMAND(...) record_breadcrumb()
#define ERR(...) ((void)0)
#define d3d12_command_list_check_end_of_command_list_cleanup(list) ((void)0)
#define vk_queue_shader_stages(...) (VK_PIPELINE_STAGE_2_COMPUTE_SHADER_BIT | VK_PIPELINE_STAGE_2_FRAGMENT_SHADER_BIT)
#define d3d12_resource_pick_layout(resource, layout) VK_IMAGE_LAYOUT_GENERAL
#define d3d12_command_list_get_depth_stencil_resource_layout(...) VK_IMAGE_LAYOUT_DEPTH_STENCIL_ATTACHMENT_OPTIMAL
struct vkd3d_vk_device_procs { int unused; };
struct d3d12_device { struct vkd3d_vk_device_procs vk_procs; };
struct d3d12_resource { unsigned int flags; struct { unsigned int Flags; } desc; struct { VkImage vk_image; } res; };
struct d3d12_command_list
{
    struct d3d12_device *device;
    unsigned int type, vk_queue_flags, deferred_clear_count;
    VkImageLayout dsv_layout;
    struct { VkCommandBuffer vk_command_buffer; } cmd;
};
'''

MOCKS = r'''
static bool breadcrumbs;
static unsigned int calls, clear_calls, expected_before_clear, breadcrumb_count, counts[8];
static VkImageMemoryBarrier2 recorded[8][MAX_BATCHED_IMAGE_BARRIERS];
static void vkCmdPipelineBarrier2(VkCommandBuffer cmd, const VkDependencyInfo *info)
{
    assert(calls < ARRAY_SIZE(counts) && !info->memoryBarrierCount && !info->bufferMemoryBarrierCount);
    assert(info->imageMemoryBarrierCount <= MAX_BATCHED_IMAGE_BARRIERS);
    counts[calls] = info->imageMemoryBarrierCount;
    memcpy(recorded[calls++], info->pImageMemoryBarriers,
            info->imageMemoryBarrierCount * sizeof(VkImageMemoryBarrier2));
}
static void d3d12_command_list_discard_attachment(struct d3d12_command_list *list,
        struct d3d12_resource *resource, const VkImageSubresourceRange *range)
{
    if (list->deferred_clear_count)
    {
        /* This mock emits pending work. It must observe earlier barriers already recorded. */
        assert(calls == expected_before_clear);
        clear_calls++;
        list->deferred_clear_count = 0;
    }
}
static void reset(void)
{
    calls = clear_calls = expected_before_clear = breadcrumb_count = 0;
    breadcrumbs = false;
    memset(counts, 0, sizeof(counts));
    memset(recorded, 0, sizeof(recorded));
}
static void record_breadcrumb(void)
{
    if (breadcrumbs)
        assert(calls == ++breadcrumb_count);
}
static VkImageSubresource d3d12_resource_get_vk_subresource(struct d3d12_resource *resource,
        unsigned int index, bool all_aspects)
{
    return (VkImageSubresource){VK_IMAGE_ASPECT_COLOR_BIT, index, 0};
}
static VkImageSubresourceRange vk_subresource_range_from_subresource(const VkImageSubresource *subresource)
{
    return (VkImageSubresourceRange){subresource->aspectMask, subresource->mipLevel, 1, subresource->arrayLayer, 1};
}
'''

PARTIAL_WRAPPER = r'''
static void partial_discard(struct d3d12_command_list *list, struct d3d12_resource *texture,
        unsigned int first_subresource, unsigned int subresource_count, bool has_unbound_subresource)
{
    unsigned int i;
    VkImageSubresource vk_subresource;
    VkImageSubresourceRange vk_subresource_range;
'''

TESTS = r'''
static void check_barrier(const VkImageMemoryBarrier2 *barrier, const struct d3d12_resource *resource,
        const VkImageSubresourceRange *range, VkPipelineStageFlags2 stages, VkAccessFlags2 access, VkImageLayout layout)
{
    assert(barrier->sType == VK_STRUCTURE_TYPE_IMAGE_MEMORY_BARRIER_2 && !barrier->pNext);
    assert(barrier->srcStageMask == stages && barrier->dstStageMask == stages);
    assert(barrier->srcAccessMask == access && barrier->dstAccessMask == access);
    assert(barrier->oldLayout == VK_IMAGE_LAYOUT_UNDEFINED && barrier->newLayout == layout);
    assert(barrier->srcQueueFamilyIndex == VK_QUEUE_FAMILY_IGNORED);
    assert(barrier->dstQueueFamilyIndex == VK_QUEUE_FAMILY_IGNORED && barrier->image == resource->res.vk_image);
    assert(!memcmp(&barrier->subresourceRange, range, sizeof(*range)));
}

int main(void)
{
    struct d3d12_device device = {0};
    struct d3d12_command_list list = {.device = &device, .type = D3D12_COMMAND_LIST_TYPE_DIRECT};
    struct d3d12_resource resource = {.desc.Flags = D3D12_RESOURCE_FLAG_ALLOW_RENDER_TARGET,
        .res.vk_image = (VkImage)(uintptr_t)1};
    struct d3d12_command_list_barrier_batch batch;
    VkImageSubresourceRange range = {VK_IMAGE_ASPECT_COLOR_BIT, 0, 1, 0, 1};
    VkImageMemoryBarrier2 immediate;
    VkPipelineStageFlags2 stages;
    VkAccessFlags2 access;
    VkImageLayout layout;

    /* Adjacent mips accumulate without changing their individual transition scopes. */
    reset();
    d3d12_command_list_barrier_batch_init(&batch);
    partial_discard(&list, &resource, 0, 3, true);
    assert(calls == 1 && counts[0] == 3);
    for (unsigned int i = 0; i < 3; i++)
    {
        range.baseMipLevel = i;
        check_barrier(&recorded[0][i], &resource, &range, VK_PIPELINE_STAGE_2_COLOR_ATTACHMENT_OUTPUT_BIT,
                VK_ACCESS_2_COLOR_ATTACHMENT_READ_BIT | VK_ACCESS_2_COLOR_ATTACHMENT_WRITE_BIT, VK_IMAGE_LAYOUT_GENERAL);
    }
    d3d12_command_list_barrier_batch_end(&list, &batch);
    assert(calls == 1); /* Empty finalization emits nothing. */

    /* Sparse subresources may alias physical tiles and retain individual publication. */
    reset(); resource.flags = VKD3D_RESOURCE_RESERVED;
    partial_discard(&list, &resource, 0, 3, true);
    assert(calls == 3 && counts[0] == 1 && counts[1] == 1 && counts[2] == 1);
    resource.flags = 0;

    /* Exercise the production capacity and overlap flushes. */
    reset();
    partial_discard(&list, &resource, 0, MAX_BATCHED_IMAGE_BARRIERS + 1, true);
    assert(counts[0] == MAX_BATCHED_IMAGE_BARRIERS);
    assert(calls == 2 && counts[1] == 1 && recorded[1][0].subresourceRange.baseMipLevel == MAX_BATCHED_IMAGE_BARRIERS);
    reset();
    range.baseMipLevel = 0; range.levelCount = 2;
    d3d12_command_list_discard_attachment_barrier(&list, &resource, &range, false, &batch);
    range.baseMipLevel = 1; range.levelCount = 1;
    d3d12_command_list_discard_attachment_barrier(&list, &resource, &range, false, &batch);
    assert(calls == 1 && counts[0] == 1 && batch.image_barrier_count == 1);
    d3d12_command_list_barrier_batch_end(&list, &batch);
    assert(calls == 2);

    /* Pending clear work on another mip cannot overtake a queued discard barrier. */
    reset();
    range.baseMipLevel = 0;
    d3d12_command_list_discard_attachment_barrier(&list, &resource, &range, false, &batch);
    list.deferred_clear_count = 1; expected_before_clear = 1; range.baseMipLevel = 1;
    d3d12_command_list_discard_attachment_barrier(&list, &resource, &range, false, &batch);
    assert(clear_calls == 1 && calls == 1 && counts[0] == 1 && batch.image_barrier_count == 1);
    d3d12_command_list_barrier_batch_end(&list, &batch);
    assert(calls == 2 && recorded[0][0].subresourceRange.baseMipLevel == 0);

    /* Batched scopes match immediate RTV, bound/unbound DSV and compute UAV callers. */
    for (unsigned int kind = 0; kind < 4; kind++)
    {
        reset();
        resource.desc.Flags = kind == 0 ? D3D12_RESOURCE_FLAG_ALLOW_RENDER_TARGET :
                kind == 3 ? D3D12_RESOURCE_FLAG_ALLOW_UNORDERED_ACCESS : D3D12_RESOURCE_FLAG_ALLOW_DEPTH_STENCIL;
        range = (VkImageSubresourceRange){kind == 1 || kind == 2 ?
            VK_IMAGE_ASPECT_DEPTH_BIT | VK_IMAGE_ASPECT_STENCIL_BIT : VK_IMAGE_ASPECT_COLOR_BIT, 2, 1, 3, 1};
        list.type = kind == 3 ? 2 : D3D12_COMMAND_LIST_TYPE_DIRECT;
        list.dsv_layout = VK_IMAGE_LAYOUT_DEPTH_ATTACHMENT_STENCIL_READ_ONLY_OPTIMAL;
        stages = kind == 0 ? VK_PIPELINE_STAGE_2_COLOR_ATTACHMENT_OUTPUT_BIT : kind == 3 ?
            vk_queue_shader_stages() : VK_PIPELINE_STAGE_2_EARLY_FRAGMENT_TESTS_BIT | VK_PIPELINE_STAGE_2_LATE_FRAGMENT_TESTS_BIT;
        access = kind == 0 ? VK_ACCESS_2_COLOR_ATTACHMENT_READ_BIT | VK_ACCESS_2_COLOR_ATTACHMENT_WRITE_BIT : kind == 3 ?
            VK_ACCESS_2_SHADER_READ_BIT | VK_ACCESS_2_SHADER_WRITE_BIT : VK_ACCESS_2_DEPTH_STENCIL_ATTACHMENT_READ_BIT | VK_ACCESS_2_DEPTH_STENCIL_ATTACHMENT_WRITE_BIT;
        layout = kind == 1 ? VK_IMAGE_LAYOUT_DEPTH_STENCIL_ATTACHMENT_OPTIMAL : kind == 2 ? list.dsv_layout : VK_IMAGE_LAYOUT_GENERAL;
        d3d12_command_list_discard_attachment_barrier(&list, &resource, &range, kind == 2, NULL);
        assert(calls == 1 && counts[0] == 1);
        immediate = recorded[0][0];
        check_barrier(&immediate, &resource, &range, stages, access, layout);
        d3d12_command_list_discard_attachment_barrier(&list, &resource, &range, kind == 2, &batch);
        assert(calls == 1);
        d3d12_command_list_barrier_batch_end(&list, &batch);
        assert(calls == 2 && counts[1] == 1);
        check_barrier(&recorded[1][0], &resource, &range, stages, access, layout);
    }

    /* Breadcrumb callers require publication before each following diagnostic command. */
    reset(); breadcrumbs = true;
    partial_discard(&list, &resource, 0, 3, true);
    assert(calls == 3 && breadcrumb_count == 3 && counts[0] == 1 && counts[1] == 1 && counts[2] == 1);
    puts("Discard barriers: batching, capacity, overlap, clear ordering, scopes and breadcrumbs passed");
    return 0;
}
'''


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, default=ROOT / "libs/vkd3d/command.c")
    parser.add_argument("--generate", type=Path)
    parser.add_argument("--build-dir", type=Path)
    args = parser.parse_args()
    if not args.generate and not args.build_dir:
        parser.error("either --generate or --build-dir is required")
    source = generate(args.source.read_text())
    if args.generate:
        args.generate.write_text(source)
        return
    args.build_dir.mkdir(parents=True, exist_ok=True)
    output, executable = args.build_dir / "discard_barriers.c", args.build_dir / "discard_barriers"
    output.write_text(source)
    subprocess.run(shlex.split(os.environ.get("CC", "cc")) + ["-std=c11", "-O0", "-g", "-UNDEBUG",
        "-I" + str(ROOT / "khronos/Vulkan-Headers/include")] +
        shlex.split(os.environ.get("CFLAGS", "")) + [str(output), "-o", str(executable)], check=True)
    subprocess.run([str(executable)], check=True)


if __name__ == "__main__":
    main()
