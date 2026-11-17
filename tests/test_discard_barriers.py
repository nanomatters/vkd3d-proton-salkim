#!/usr/bin/env python3
# Copyright 2026 Erhan Bilgili
# SPDX-License-Identifier: LGPL-2.1-or-later
"""Record production discard calls, alias handling and flush boundaries without a GPU."""

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
    # Action entry points must publish discards before internal meta work.
    methods = """Close DrawInstanced DrawIndexedInstanced Dispatch CopyBufferRegion
        CopyTextureRegion CopyResource CopyTiles ResolveSubresource ClearDepthStencilView
        ClearRenderTargetView ClearUnorderedAccessViewUint ClearUnorderedAccessViewFloat
        BeginQuery EndQuery ResolveQueryData SetPredication ExecuteIndirect ResolveSubresourceRegion
        WriteBufferImmediate BeginRenderPass EndRenderPass InitializeMetaCommand ExecuteMetaCommand
        BuildRaytracingAccelerationStructure EmitRaytracingAccelerationStructurePostbuildInfo
        CopyRaytracingAccelerationStructure DispatchRays DispatchMesh Barrier SetProgram DispatchGraph""".split()
    for name in methods:
        prefix = "static HRESULT STDMETHODCALLTYPE " if name == "Close" else "static void STDMETHODCALLTYPE "
        fn = block(source, prefix + "d3d12_command_list_" + name + "(")
        after_trace = fn[fn.index(";", fn.index("TRACE(")) + 1:].lstrip()
        assert after_trace.startswith("d3d12_command_list_flush_discard_barriers(list);"), name
    barrier = block(source, "static void STDMETHODCALLTYPE d3d12_command_list_ResourceBarrier(")
    assert barrier.index("d3d12_command_list_flush_discard_barriers(list);") < barrier.index("d3d12_command_list_flush_dgc_batch(list);")
    reset = block(source, "static void d3d12_command_list_reset_internal_state(")
    assert "list->discard_barriers.count = 0;" in reset
    assert "discard_barriers.external_commands = false" not in reset
    pieces = [PREFIX, block(header, "struct vkd3d_discard_barrier_batch\n", True), MOCKS]
    for prefix, name in (
        ("void ", "d3d12_command_list_flush_discard_barriers"),
        ("static bool ", "vk_subresource_range_overlaps"),
        ("static bool ", "vkd3d_check_subresource_overlap"),
        ("static void ", "d3d12_command_list_queue_discard_barrier"),
        ("static void ", "d3d12_command_list_discard_attachment_barrier"),
        ("static void ", "d3d12_command_list_execute_deferred_clear"),
        ("static void STDMETHODCALLTYPE ", "d3d12_command_list_DiscardResource"),
        ("static void STDMETHODCALLTYPE ", "d3d12_command_list_OMSetBlendFactor"),
    ):
        pieces.append(block(source, prefix + name + "("))
    ext = (ROOT / "libs/vkd3d/command_list_vkd3d_ext.c").read_text()
    pieces.append(block(ext, "static HRESULT STDMETHODCALLTYPE d3d12_command_list_vkd3d_ext_GetVulkanHandle("))
    device_ext = (ROOT / "libs/vkd3d/device_vkd3d_ext.c").read_text()
    pieces.append(block(device_ext, "static HRESULT STDMETHODCALLTYPE d3d12_dxvk_interop_device_BeginVkCommandBufferInterop("))
    launch = block(ext, "static HRESULT STDMETHODCALLTYPE d3d12_command_list_vkd3d_ext_LaunchCubinShaderEx(")
    assert launch.index("d3d12_command_list_flush_discard_barriers(command_list);") < launch.index("vkCmdCuLaunchKernelNVX")
    for name, before in (("emit_render_pass_transition", "memset(&dep_info"),
                         ("begin_new_sequence", "vkEndCommandBuffer"),
                         ("flush_dgc_batch", "vk_procs =")):
        prefix = "void " if name == "flush_dgc_batch" else "static void "
        fn = block(source, prefix + "d3d12_command_list_" + name + "(")
        assert fn.index("d3d12_command_list_flush_discard_barriers(list);") < fn.index(before), name
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
#define D3D12_COMMAND_LIST_TYPE_COMPUTE 2
#define D3D12_RESOURCE_FLAG_ALLOW_RENDER_TARGET 1
#define D3D12_RESOURCE_FLAG_ALLOW_DEPTH_STENCIL 2
#define D3D12_RESOURCE_FLAG_ALLOW_UNORDERED_ACCESS 4
#define VKD3D_RESOURCE_RESERVED 8
#define VKD3D_RESOURCE_COMMITTED 16
#define VKD3D_RESOURCE_PLACED 32
#define VKD3D_RESOURCE_EXTERNAL 64
#define VK_CALL(call) call
#define VKD3D_CONFIG_FLAG_IS_SET(flag) breadcrumbs
#define VKD3D_BREADCRUMB_AUX32(...) ((void)0)
#define VKD3D_BREADCRUMB_COMMAND(...) record_breadcrumb()
#define VKD3D_BREADCRUMB_RESOURCE(...) ((void)0)
#define VKD3D_BREADCRUMB_TAG(...) ((void)0)
#define TRACE(...) ((void)0)
#define ERR(...) ((void)0)
#define WARN(...) ((void)0)
#define FIXME(...) ((void)0)
#define STDMETHODCALLTYPE
#define S_OK 0
#define E_INVALIDARG (-1)
#define VKD3D_DYNAMIC_STATE_BLEND_CONSTANTS 1
#define vk_queue_shader_stages(...) (VK_PIPELINE_STAGE_2_COMPUTE_SHADER_BIT | VK_PIPELINE_STAGE_2_FRAGMENT_SHADER_BIT)
#define d3d12_resource_pick_layout(resource, layout) VK_IMAGE_LAYOUT_GENERAL
#define d3d12_command_list_get_depth_stencil_resource_layout(...) VK_IMAGE_LAYOUT_DEPTH_STENCIL_ATTACHMENT_OPTIMAL
#define impl_from_ID3D12GraphicsCommandList(list) (list)
#define d3d12_command_list_from_ID3D12GraphicsCommandListExt(list) (list)
#define impl_from_ID3D12Resource(resource) (resource)
#define d3d12_command_list_from_iface(list) (list)
#define d3d12_command_list_decay_tracked_state(...) ((void)0)
#define d3d12_command_list_invalidate_all_state(...) ((void)0)
#define d3d12_command_list_check_render_pass_validation(...) ((void)0)
#define d3d12_command_list_flush_dgc_batch(...) ((void)0)
#define d3d12_command_list_notify_dsv_discard(...) ((void)0)
#define d3d12_command_list_end_current_render_pass(...) ((void)0)
#define d3d12_resource_is_texture(...) true
#define d3d12_resource_get_sub_resource_count(resource) ((resource)->desc.MipLevels * (resource)->desc.DepthOrArraySize)
#define d3d12_command_list_is_subresource_bound_as_rtv_dsv(...) false
#define d3d12_command_list_track_resource_usage(...) ((void)0)
#define d3d12_get_image_rect(...) ((D3D12_RECT){0, 0, 4, 4})
#define d3d12_rect_fully_covers_region(a, b) false
struct vkd3d_vk_device_procs { int unused; };
struct d3d12_device { struct vkd3d_vk_device_procs vk_procs; };
struct vkd3d_format { unsigned int vk_aspect_mask; };
struct d3d12_resource
{
    unsigned int flags;
    struct { unsigned int Flags, MipLevels, DepthOrArraySize; } desc;
    struct { VkImage vk_image; } res;
    struct { struct { VkDeviceMemory vk_memory; } device_allocation;
        uint64_t offset; struct { uint64_t size; } resource; } mem;
    const struct vkd3d_format *format;
};
typedef int HRESULT;
typedef float FLOAT;
typedef struct { int left, top, right, bottom; } D3D12_RECT;
typedef struct { unsigned int NumRects; const D3D12_RECT *pRects;
    unsigned int FirstSubresource, NumSubresources; } D3D12_DISCARD_REGION;
typedef struct d3d12_resource ID3D12Resource;
typedef struct d3d12_command_list ID3D12CommandList;
typedef void d3d12_dxvk_interop_device_iface;
typedef struct d3d12_command_list d3d12_command_list_iface;
typedef struct d3d12_command_list d3d12_command_list_vkd3d_ext_iface;
'''

MOCKS = r'''
struct vkd3d_dynamic_state { float blend_constants[4]; unsigned int dirty_flags; };
struct d3d12_command_list
{
    struct d3d12_device *device;
    unsigned int type, vk_queue_flags, deferred_clear_count;
    VkImageLayout dsv_layout;
    struct { VkCommandBuffer vk_command_buffer; struct { bool block_resume; } suspend_resume; } cmd;
    bool is_inside_render_pass;
    struct { bool enabled_on_command_buffer; } predication;
    struct vkd3d_discard_barrier_batch discard_barriers;
    struct vkd3d_dynamic_state dynamic_state;
};
struct vkd3d_deferred_clear
{
    struct d3d12_resource *resource;
    void *view;
    unsigned int clear_aspects;
    VkClearValue clear_value;
};
static bool breadcrumbs;
static unsigned int calls, clear_calls, expected_before_clear, breadcrumb_count, counts[32];
static VkImageMemoryBarrier2 recorded[32][16];
static void vkCmdPipelineBarrier2(VkCommandBuffer cmd, const VkDependencyInfo *info)
{
    assert(calls < ARRAY_SIZE(counts) && !info->memoryBarrierCount && !info->bufferMemoryBarrierCount);
    assert(info->imageMemoryBarrierCount <= ARRAY_SIZE(recorded[0]));
    counts[calls] = info->imageMemoryBarrierCount;
    memcpy(recorded[calls++], info->pImageMemoryBarriers,
            info->imageMemoryBarrierCount * sizeof(VkImageMemoryBarrier2));
}
static void d3d12_command_list_execute_deferred_clear(struct d3d12_command_list *list,
        const struct vkd3d_deferred_clear *clear);
static void d3d12_command_list_load_attachment(struct d3d12_command_list *list,
        struct d3d12_resource *resource, void *view, unsigned int aspect, const VkClearValue *value,
        unsigned int rect_count, const void *rects, VkAttachmentLoadOp load)
{
    assert(calls == expected_before_clear && list->discard_barriers.count == 0);
    clear_calls++;
}
static void d3d12_command_list_discard_attachment(struct d3d12_command_list *list,
        struct d3d12_resource *resource, const VkImageSubresourceRange *range)
{
    if (list->deferred_clear_count)
    {
        struct vkd3d_deferred_clear clear = {.resource = resource};
        d3d12_command_list_execute_deferred_clear(list, &clear);
        list->deferred_clear_count = 0;
    }
}
static void reset(struct d3d12_command_list *list)
{
    assert(!list->discard_barriers.count);
    calls = clear_calls = expected_before_clear = breadcrumb_count = 0;
    breadcrumbs = false;
    memset(counts, 0, sizeof(counts));
}
static void record_breadcrumb(void)
{
    if (breadcrumbs)
        assert(calls == ++breadcrumb_count);
}
static VkImageSubresource d3d12_resource_get_vk_subresource(struct d3d12_resource *resource,
        unsigned int index, bool all_aspects)
{
    return (VkImageSubresource){resource->format->vk_aspect_mask,
        index % resource->desc.MipLevels, index / resource->desc.MipLevels};
}
static VkImageSubresourceRange vk_subresource_range_from_subresource(const VkImageSubresource *subresource)
{
    return (VkImageSubresourceRange){subresource->aspectMask, subresource->mipLevel, 1, subresource->arrayLayer, 1};
}
static VkImageSubresourceLayers vk_subresource_layers_from_subresource(const VkImageSubresource *subresource)
{
    return (VkImageSubresourceLayers){subresource->aspectMask, subresource->mipLevel, subresource->arrayLayer, 1};
}
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
    const struct vkd3d_format format = {VK_IMAGE_ASPECT_COLOR_BIT};
    struct d3d12_device device = {0};
    struct d3d12_command_list list = {.device = &device, .type = D3D12_COMMAND_LIST_TYPE_DIRECT};
    struct d3d12_resource resource = {.flags = VKD3D_RESOURCE_COMMITTED,
        .desc = {D3D12_RESOURCE_FLAG_ALLOW_RENDER_TARGET, 8, 3}, .res.vk_image = (VkImage)(uintptr_t)1, .format = &format};
    struct d3d12_resource other = resource;
    D3D12_DISCARD_REGION region = {.FirstSubresource = 1, .NumSubresources = 3};
    VkImageSubresourceRange range = {VK_IMAGE_ASPECT_COLOR_BIT, 1, 1, 0, 1};
    const float blend[] = {1, 2, 3, 4};
    VkCommandBuffer handle;
    other.res.vk_image = (VkImage)(uintptr_t)2;

    /* Distinct whole-resource calls remain pending across a real state setter. */
    d3d12_command_list_DiscardResource(&list, &resource, NULL);
    d3d12_command_list_OMSetBlendFactor(&list, blend);
    d3d12_command_list_DiscardResource(&list, &other, NULL);
    assert(!calls && list.discard_barriers.count == 2 && list.dynamic_state.blend_constants[3] == 4);
    d3d12_command_list_flush_discard_barriers(&list);
    assert(calls == 1 && counts[0] == 2);

    /* Partial calls on distinct mips and array layers share the same batch. */
    reset(&list);
    d3d12_command_list_DiscardResource(&list, &resource, &region);
    region.FirstSubresource = 9;
    d3d12_command_list_DiscardResource(&list, &resource, &region);
    assert(!calls && list.discard_barriers.count == 6);
    d3d12_command_list_flush_discard_barriers(&list);
    assert(calls == 1 && counts[0] == 6);
    for (unsigned int i = 0; i < 6; i++)
    {
        range.baseMipLevel = 1 + i % 3; range.baseArrayLayer = i / 3;
        check_barrier(&recorded[0][i], &resource, &range, VK_PIPELINE_STAGE_2_COLOR_ATTACHMENT_OUTPUT_BIT,
                VK_ACCESS_2_COLOR_ATTACHMENT_READ_BIT | VK_ACCESS_2_COLOR_ATTACHMENT_WRITE_BIT, VK_IMAGE_LAYOUT_GENERAL);
    }

    /* Whole-resource sentinels overlap a later mip, even when its base is nonzero. */
    reset(&list);
    d3d12_command_list_DiscardResource(&list, &resource, NULL);
    d3d12_command_list_DiscardResource(&list, &resource, &region);
    assert(calls == 1 && counts[0] == 1 && list.discard_barriers.count == 3);
    d3d12_command_list_flush_discard_barriers(&list);

    /* A partial region crossing array layers also exercises capacity rollover. */
    reset(&list); region.FirstSubresource = 1; region.NumSubresources = 23;
    d3d12_command_list_DiscardResource(&list, &resource, &region);
    assert(calls == 1 && counts[0] == 16 && list.discard_barriers.count == 7);
    d3d12_command_list_flush_discard_barriers(&list);
    assert(calls == 2 && counts[1] == 7);

    /* Aliased placed resources flush even when their VkImages differ. */
    resource.flags = other.flags = VKD3D_RESOURCE_PLACED;
    resource.mem.device_allocation.vk_memory = other.mem.device_allocation.vk_memory = (VkDeviceMemory)(uintptr_t)7;
    resource.mem.resource.size = other.mem.resource.size = 1024;
    for (unsigned int alias = 0; alias < 2; alias++)
    {
        reset(&list); other.mem.offset = alias ? 512 : 1024;
        d3d12_command_list_DiscardResource(&list, &resource, NULL);
        d3d12_command_list_DiscardResource(&list, &other, NULL);
        assert(calls == alias && list.discard_barriers.count == 2 - alias);
        d3d12_command_list_flush_discard_barriers(&list);
    }
    resource.flags = other.flags = VKD3D_RESOURCE_COMMITTED;

    /* A deferred clear must follow earlier discards and precede the next one. */
    reset(&list);
    d3d12_command_list_DiscardResource(&list, &resource, NULL);
    list.deferred_clear_count = 1; expected_before_clear = 1;
    d3d12_command_list_DiscardResource(&list, &other, NULL);
    assert(calls == 1 && clear_calls == 1 && list.discard_barriers.count == 1);
    d3d12_command_list_flush_discard_barriers(&list);
    assert(calls == 2);

    /* Sparse and borrowed images cannot prove disjoint backing memory. */
    for (unsigned int borrowed = 0; borrowed < 2; borrowed++)
    {
        reset(&list);
        other.flags = borrowed ? VKD3D_RESOURCE_PLACED | VKD3D_RESOURCE_EXTERNAL : VKD3D_RESOURCE_RESERVED;
        d3d12_command_list_DiscardResource(&list, &resource, NULL);
        d3d12_command_list_DiscardResource(&list, &other, NULL);
        assert(calls == 2 && counts[0] == 1 && counts[1] == 1 && !list.discard_barriers.count);
    }
    other.flags = VKD3D_RESOURCE_COMMITTED;
    reset(&list); breadcrumbs = true;
    d3d12_command_list_DiscardResource(&list, &resource, NULL);
    d3d12_command_list_DiscardResource(&list, &other, NULL);
    assert(calls == 2 && breadcrumb_count == 2 && !list.discard_barriers.count);

    /* Depth/stencil masks and the captured layout survive later state changes. */
    reset(&list);
    resource.desc.Flags = D3D12_RESOURCE_FLAG_ALLOW_DEPTH_STENCIL;
    range = (VkImageSubresourceRange){VK_IMAGE_ASPECT_DEPTH_BIT | VK_IMAGE_ASPECT_STENCIL_BIT, 2, 1, 1, 1};
    list.dsv_layout = VK_IMAGE_LAYOUT_DEPTH_ATTACHMENT_STENCIL_READ_ONLY_OPTIMAL;
    d3d12_command_list_discard_attachment_barrier(&list, &resource, &range, true, true);
    list.dsv_layout = VK_IMAGE_LAYOUT_GENERAL;
    d3d12_command_list_flush_discard_barriers(&list);
    check_barrier(&recorded[0][0], &resource, &range,
            VK_PIPELINE_STAGE_2_EARLY_FRAGMENT_TESTS_BIT | VK_PIPELINE_STAGE_2_LATE_FRAGMENT_TESTS_BIT,
            VK_ACCESS_2_DEPTH_STENCIL_ATTACHMENT_READ_BIT | VK_ACCESS_2_DEPTH_STENCIL_ATTACHMENT_WRITE_BIT,
            VK_IMAGE_LAYOUT_DEPTH_ATTACHMENT_STENCIL_READ_ONLY_OPTIMAL);

    reset(&list); resource.desc.Flags = D3D12_RESOURCE_FLAG_ALLOW_UNORDERED_ACCESS;
    list.type = D3D12_COMMAND_LIST_TYPE_COMPUTE; range.aspectMask = VK_IMAGE_ASPECT_COLOR_BIT;
    d3d12_command_list_discard_attachment_barrier(&list, &resource, &range, false, true);
    d3d12_command_list_flush_discard_barriers(&list);
    check_barrier(&recorded[0][0], &resource, &range, vk_queue_shader_stages(),
            VK_ACCESS_2_SHADER_READ_BIT | VK_ACCESS_2_SHADER_WRITE_BIT, VK_IMAGE_LAYOUT_GENERAL);
    reset(&list); list.type = D3D12_COMMAND_LIST_TYPE_DIRECT;
    d3d12_command_list_discard_attachment_barrier(&list, &resource, &range, false, true);
    d3d12_command_list_discard_attachment_barrier(&list, &other, &range, false, false);
    assert(calls == 2 && !list.discard_barriers.count); /* Render-pass load caller stays immediate. */

    /* Raw Vulkan access drains the batch and makes later discards immediate. */
    reset(&list);
    d3d12_command_list_discard_attachment_barrier(&list, &resource, &range, false, true);
    assert(d3d12_command_list_vkd3d_ext_GetVulkanHandle(&list, &handle) == S_OK);
    assert(calls == 1 && !list.discard_barriers.count && list.discard_barriers.external_commands);
    d3d12_command_list_discard_attachment_barrier(&list, &resource, &range, false, true);
    assert(calls == 2 && !list.discard_barriers.count);
    reset(&list); list.discard_barriers.external_commands = false;
    d3d12_command_list_discard_attachment_barrier(&list, &resource, &range, false, true);
    assert(d3d12_dxvk_interop_device_BeginVkCommandBufferInterop(NULL, &list, &handle) == S_OK);
    assert(calls == 1 && !list.discard_barriers.count && list.discard_barriers.external_commands);
    d3d12_command_list_discard_attachment_barrier(&list, &resource, &range, false, true);
    assert(calls == 2 && !list.discard_barriers.count);
    puts("Discard barriers: cross-call batching, state setters, ranges, aliases, clears, scopes and interop passed");
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
