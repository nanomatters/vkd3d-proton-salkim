#!/usr/bin/env python3
# Copyright 2026 Erhan Bilgili
# SPDX-License-Identifier: LGPL-2.1-or-later
"""Exercise the production WBI batch recorder without an AMD Vulkan device."""

import argparse
import os
from pathlib import Path
import re
import shlex
import subprocess


def extract_function(source, name, prefix="static void "):
    start = source.index(prefix + name + "(")
    while source.index(";", start) < source.index("{", start):
        start = source.index(prefix + name + "(", start + 1)
    body = source.index("{", start)
    depth = 1
    end = body + 1
    while depth:
        depth += (source[end] == "{") - (source[end] == "}")
        end += 1
    return source[start:end]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path,
                        default=Path(__file__).resolve().parents[1] / "libs/vkd3d/command.c")
    parser.add_argument("--build-dir", type=Path)
    parser.add_argument("--revision", help="read command.c from a git revision for a negative control")
    parser.add_argument("--generate", type=Path, help="write the C harness without compiling or running it")
    parser.add_argument("--ordering-only", action="store_true", help="isolate the deferred-draw ordering regression")
    args = parser.parse_args()
    if not args.generate and not args.build_dir:
        parser.error("either --generate or --build-dir is required")

    harness = r'''
/* SPDX-License-Identifier: LGPL-2.1-or-later */
#include <assert.h>
#include <stdbool.h>
#include <stdint.h>
#include <stdio.h>
#include <string.h>

#define VK_PIPELINE_STAGE_TRANSFER_BIT 1
#define VK_PIPELINE_STAGE_BOTTOM_OF_PIPE_BIT 2
#define VK_PIPELINE_STAGE_TOP_OF_PIPE_BIT 4
#define VK_PIPELINE_STAGE_2_COPY_BIT 8
#define VK_PIPELINE_STAGE_2_CLEAR_BIT 16
#define VK_PIPELINE_STAGE_2_RESOLVE_BIT 32
#define VK_PIPELINE_STAGE_2_COLOR_ATTACHMENT_OUTPUT_BIT 64
#define VK_PIPELINE_STAGE_2_COMPUTE_SHADER_BIT 128
#define VK_ACCESS_2_TRANSFER_WRITE_BIT 1
#define VK_ACCESS_2_COLOR_ATTACHMENT_WRITE_BIT 2
#define VK_ACCESS_2_COLOR_ATTACHMENT_READ_BIT 4
#define VK_ACCESS_2_SHADER_READ_BIT 8
#define VK_ACCESS_2_SHADER_WRITE_BIT 16
#define VK_ACCESS_2_SHADER_STORAGE_WRITE_BIT 32
#define VK_ACCESS_2_NONE 0
#define D3D12_RESOURCE_STATE_COPY_DEST 1
#define D3D12_RESOURCE_STATE_COPY_SOURCE 2
#define D3D12_RESOURCE_STATE_RESOLVE_DEST 4
#define D3D12_BARRIER_SYNC_ALL 1
#define D3D12_BARRIER_SYNC_COPY 2
#define D3D12_BARRIER_SYNC_RESOLVE 4
#define D3D12_BARRIER_ACCESS_COMMON 0
#define D3D12_BARRIER_ACCESS_COPY_DEST 1
#define D3D12_BARRIER_ACCESS_COPY_SOURCE 2
#define D3D12_BARRIER_ACCESS_RESOLVE_DEST 4
#define VK_STRUCTURE_TYPE_MEMORY_BARRIER_2 1
#define VK_STRUCTURE_TYPE_DEPENDENCY_INFO 2
#define VK_NULL_HANDLE UINT32_MAX
#define VK_WHOLE_SIZE UINT64_MAX
#define ARRAY_SIZE(a) (sizeof(a) / sizeof((a)[0]))
#define min(a, b) ((a) < (b) ? (a) : (b))
#define max(a, b) ((a) > (b) ? (a) : (b))
#define VKD3D_RENDERING_ACTIVE 1
#define VKD3D_MAX_WBI_BATCH_SIZE 3
#define VK_CALL(call) call
#define TRACE(...) ((void)0)
#define VKD3D_BREADCRUMB_COMMAND(...) ((void)0)
#define STDMETHODCALLTYPE

typedef uint32_t UINT;
typedef uint64_t VkDeviceSize;
typedef unsigned int VkPipelineStageFlagBits;
typedef unsigned int VkPipelineStageFlags2;
typedef unsigned int VkBuffer;
typedef unsigned int VkAccessFlags2;
typedef unsigned int D3D12_BARRIER_SYNC;
typedef unsigned int D3D12_BARRIER_ACCESS;
typedef struct
{
    D3D12_BARRIER_SYNC SyncBefore, SyncAfter;
    D3D12_BARRIER_ACCESS AccessBefore, AccessAfter;
} D3D12_GLOBAL_BARRIER;
typedef struct
{
    unsigned int sType, srcStageMask, dstStageMask, srcAccessMask, dstAccessMask;
} VkMemoryBarrier2;
typedef struct
{
    unsigned int sType, memoryBarrierCount;
    VkMemoryBarrier2 *pMemoryBarriers;
} VkDependencyInfo;
struct d3d12_resource { struct { unsigned int vk_image; } res; };
typedef struct
{
    struct d3d12_resource *pResource;
    unsigned int StateBefore, StateAfter, Subresource;
} D3D12_RESOURCE_TRANSITION_BARRIER;
struct d3d12_tracked_texture_copy { unsigned int vk_image, subresource_index; };
struct d3d12_command_list_barrier_batch { VkMemoryBarrier2 vk_memory_barrier; };
typedef enum
{
    D3D12_WRITEBUFFERIMMEDIATE_MODE_DEFAULT,
    D3D12_WRITEBUFFERIMMEDIATE_MODE_MARKER_IN,
    D3D12_WRITEBUFFERIMMEDIATE_MODE_MARKER_OUT,
} D3D12_WRITEBUFFERIMMEDIATE_MODE;
typedef struct
{
    uint64_t Dest;
    uint32_t Value;
} D3D12_WRITEBUFFERIMMEDIATE_PARAMETER;

struct vkd3d_vk_device_procs { int unused; };
struct test_device
{
    struct vkd3d_vk_device_procs vk_procs;
    struct { bool AMD_buffer_marker; } vk_info;
    struct { int va_map; } memory_allocator;
};
struct d3d12_tracked_buffer_copy
{
    VkBuffer vk_buffer;
    VkDeviceSize hazard_begin, hazard_end;
};
struct d3d12_command_list
{
    struct test_device *device;
    struct { unsigned int state_flags; } rendering_info;
    struct { unsigned int build_info_count; } rtas_batch;
    unsigned int query_resolve_count;
    unsigned int pending_draws;
    struct
    {
        struct d3d12_tracked_buffer_copy tracked_copy_buffers[3];
        struct d3d12_tracked_texture_copy tracked_copy_textures[1];
        unsigned int tracked_copy_buffer_count, tracked_copy_texture_count;
        VkPipelineStageFlags2 vk_stages;
        VkPipelineStageFlags2 read_after_write_hazard_stages, write_after_read_hazard_stages;
        VkPipelineStageFlags2 shader_resource_execution_stages_are_idle;
    } transfer_batch;
    struct
    {
        int vk_command_buffer;
        struct { bool block_resume; } suspend_resume;
    } cmd;
    struct
    {
        unsigned int buffers[3];
        size_t offsets[3];
        unsigned int stages[3];
        uint32_t values[3];
        size_t batch_len;
    } wbi_batch;
};
typedef struct d3d12_command_list d3d12_command_list_iface;
struct vkd3d_unique_resource { uint64_t va; unsigned int vk_buffer; };

static uint32_t memory[3][4];
static const struct vkd3d_unique_resource resource = {0x1000, 0};
static unsigned int drain_calls, transfer_calls;
static unsigned int event_serial, draw_serial, write_serial;
static unsigned int pending_stages[3][4], available_stages[3][4];
static unsigned int barriers, hazards;
static bool check_overlap;

static struct d3d12_resource *impl_from_ID3D12Resource(struct d3d12_resource *resource)
{
    return resource;
}

static bool d3d12_resource_is_texture(struct d3d12_resource *resource)
{
    (void)resource;
    return false;
}

static bool d3d12_device_prefers_render_pass_resolves(struct test_device *device)
{
    (void)device;
    return false;
}

static void d3d12_command_list_debug_mark_label(struct d3d12_command_list *list,
        const char *label, float r, float g, float b, float a)
{
    (void)list; (void)label; (void)r; (void)g; (void)b; (void)a;
}

static void vkCmdPipelineBarrier2(int command_buffer, const VkDependencyInfo *dependency)
{
    const VkMemoryBarrier2 *barrier = dependency->pMemoryBarriers;
    unsigned int i, j;
    (void)command_buffer;
    barriers++;
    assert(dependency->memoryBarrierCount == 1);
    assert(barrier->srcAccessMask & VK_ACCESS_2_TRANSFER_WRITE_BIT);
    assert(barrier->dstAccessMask & VK_ACCESS_2_TRANSFER_WRITE_BIT);
    for (i = 0; i < 3; i++)
    for (j = 0; j < 4; j++)
        if (pending_stages[i][j] & barrier->srcStageMask)
            available_stages[i][j] |= barrier->dstStageMask;
}

static void record_write(unsigned int buffer, size_t offset, size_t size,
        const void *data, unsigned int stage)
{
    size_t i;
    assert(buffer < 3 && offset + size <= sizeof(memory[buffer]));
    for (i = offset / 4; i < (offset + size) / 4; i++)
    {
        if (check_overlap && pending_stages[buffer][i] && !(available_stages[buffer][i] & stage))
            hazards++;
        pending_stages[buffer][i] = stage;
        available_stages[buffer][i] = 0;
    }
    memcpy((char *)memory[buffer] + offset, data, size);
}

static struct d3d12_command_list *impl_from_ID3D12GraphicsCommandList(d3d12_command_list_iface *iface)
{
    return iface;
}

static const struct vkd3d_unique_resource *vkd3d_va_map_deref(const void *map, uint64_t address)
{
    (void)map;
    return address >= resource.va && address < resource.va + sizeof(memory[0]) ? &resource : NULL;
}

static void d3d12_command_list_mark_as_invalid(struct d3d12_command_list *list, const char *format, ...)
{
    (void)list;
    (void)format;
    assert(!"Unexpected invalid WBI call");
}

static void d3d12_command_list_check_end_of_command_list_cleanup(struct d3d12_command_list *list)
{
    (void)list;
}

static void vkCmdUpdateBuffer(int command_buffer, unsigned int buffer, size_t offset,
        size_t size, const void *data)
{
    (void)command_buffer;
    record_write(buffer, offset, size, data, VK_PIPELINE_STAGE_2_CLEAR_BIT);
    write_serial = ++event_serial;
}

static void vkCmdWriteBufferMarkerAMD(int command_buffer, unsigned int stage,
        unsigned int buffer, size_t offset, uint32_t value)
{
    (void)command_buffer;
    (void)stage;
    assert(buffer < 3 && offset + sizeof(value) <= sizeof(memory[buffer]));
    memcpy((char *)memory[buffer] + offset, &value, sizeof(value));
    write_serial = ++event_serial;
}
'''
    if args.revision:
        source_text = subprocess.run(["git", "show", args.revision + ":libs/vkd3d/command.c"],
                                     cwd=args.source.resolve().parents[2], check=True,
                                     capture_output=True, text=True).stdout
    else:
        source_text = args.source.read_text()
    for name in ("d3d12_command_list_reset_transfer_waw_tracking",
                 "d3d12_command_list_resolve_transfer_waw",
                 "d3d12_command_list_mark_copy_buffer_write",
                 "d3d12_command_list_barrier_batch_add_global_transition",
                 "d3d12_command_list_merge_copy_tracking",
                 "d3d12_command_list_merge_copy_tracking_transition"):
        harness += extract_function(source_text, name)
    for name in ("d3d12_barrier_accesses_copy_dest", "d3d12_barrier_accesses_resolve_dest"):
        harness += extract_function(source_text, name, "static bool ")
    harness += extract_function(source_text, "d3d12_command_list_merge_copy_tracking_global_barrier")
    stage_argument = ", VK_PIPELINE_STAGE_2_COPY_BIT" if "bool sparse, VkPipelineStageFlags2 stage)" in source_text else ""
    harness += r'''
static void test_copy(struct d3d12_command_list *list, unsigned int buffer, size_t offset, uint32_t value)
{
    d3d12_command_list_mark_copy_buffer_write(list, buffer, offset, sizeof(value), false''' + stage_argument + r''');
    record_write(buffer, offset, sizeof(value), &value, VK_PIPELINE_STAGE_2_COPY_BIT);
}
'''
    harness += extract_function(source_text, "d3d12_command_list_end_wbi_batch")
    harness += r'''
static void d3d12_command_list_end_current_render_pass(struct d3d12_command_list *list, bool suspend)
{
    (void)suspend;
    list->rendering_info.state_flags = 0;
    d3d12_command_list_end_wbi_batch(list);
}

static void d3d12_command_list_flush_dgc_batch(struct d3d12_command_list *list)
{
    drain_calls++;
    if (!list->pending_draws)
        return;
    /* Multi-draw preprocessing ends rendering before replaying the captured
     * draws. Ending rendering emits any already queued immediate writes. */
    if (list->pending_draws > 1)
        d3d12_command_list_end_current_render_pass(list, true);
    draw_serial = ++event_serial;
    list->pending_draws = 0;
    list->rendering_info.state_flags = VKD3D_RENDERING_ACTIVE;
}

static void d3d12_command_list_begin_transfer(struct d3d12_command_list *list)
{
    /* Deliberately no deferred WAR hazard: isolate command-recording order
     * from the separately tested transfer synchronization fixes. */
    (void)list;
    transfer_calls++;
}

static void d3d12_command_list_end_transfer_batch(struct d3d12_command_list *list, bool resolve)
{
    (void)list;
    (void)resolve;
}

static void d3d12_command_list_flush_rtas_batch(struct d3d12_command_list *list)
{
    (void)list;
}
'''
    harness += extract_function(source_text, "vk_pipeline_stage_from_wbi_mode", "static bool ")
    harness += extract_function(source_text, "d3d12_command_list_WriteBufferImmediate", "static void STDMETHODCALLTYPE ")
    # These transfer paths consume the WAW tracker themselves. Compile their
    # actual barrier assignments to check both producer and consumer scopes.
    harness += r'''
static void test_transfer_consumers(struct d3d12_command_list *list)
{
    VkMemoryBarrier2 barrier = {0}, vk_barrier = {0}, vk_global_barrier = {0};
    unsigned int global_transfer_access, global_dst_access, dst_stages, global_dst_stages, i;
    struct { bool needs_conversion; } info_value, *info = &info_value;
'''
    for name, variable in (("d3d12_command_list_copy_image_to_buffer_compute", "barrier"),
                           ("d3d12_command_list_resolve_binary_occlusion_queries", "vk_barrier")):
        function = extract_function(source_text, name)
        scopes = re.findall(rf"{variable}\.srcStageMask = .*?{variable}\.dstAccessMask = .*?;",
                            function, re.DOTALL)
        assert len(scopes) == 2
        harness += scopes[0] + f"\nassert({variable}.srcStageMask & VK_PIPELINE_STAGE_2_CLEAR_BIT);\n"
        harness += scopes[1] + f"\nassert({variable}.dstStageMask & VK_PIPELINE_STAGE_2_CLEAR_BIT);\n"
    function = extract_function(source_text, "d3d12_command_list_CopyTiles", "static void STDMETHODCALLTYPE ")
    scopes = re.findall(r"vk_global_barrier\.srcStageMask = VK_PIPELINE_STAGE_2_COPY_BIT;.*?"
                        r"vk_global_barrier\.dstAccessMask = .*?;", function, re.DOTALL)
    assert len(scopes) == 1
    harness += scopes[0] + "\nassert(vk_global_barrier.dstStageMask & VK_PIPELINE_STAGE_2_CLEAR_BIT);\n"
    function = extract_function(source_text, "d3d12_command_list_before_copy_texture_region")
    start = function.index("info->src_layout, global_transfer_access,") + len("info->src_layout, global_transfer_access,")
    expression = function[start:function.index(");", start)].strip()
    start = function.index("src_resource->common_layout,") + len("src_resource->common_layout,")
    stage_expression = function[start:function.index(", dst_access,", start)].strip()
    harness += r'''
    for (i = 0; i < 4; i++)
    {
        info->needs_conversion = !!(i & 1);
        global_transfer_access = i & 2 ? VK_ACCESS_2_TRANSFER_WRITE_BIT : VK_ACCESS_2_NONE;
        dst_stages = info->needs_conversion ? VK_PIPELINE_STAGE_2_COMPUTE_SHADER_BIT : VK_PIPELINE_STAGE_2_COPY_BIT;
        global_dst_stages = ''' + stage_expression + r''';
        global_dst_access = ''' + expression + r''';
        assert(global_dst_access == (global_transfer_access | (global_transfer_access && info->needs_conversion ?
                VK_ACCESS_2_SHADER_STORAGE_WRITE_BIT : 0)));
        assert(global_dst_stages & dst_stages);
        if (global_transfer_access)
        {
            assert(global_dst_stages & VK_PIPELINE_STAGE_2_COPY_BIT);
            assert(global_dst_stages & VK_PIPELINE_STAGE_2_CLEAR_BIT);
        }
    }
}
'''
    harness += r'''
int main(int argc, char **argv)
{
    struct test_device device = {0};
    struct d3d12_command_list list = {0};
    D3D12_WRITEBUFFERIMMEDIATE_PARAMETER parameter = {0x1004, 22};
    D3D12_WRITEBUFFERIMMEDIATE_MODE mode = D3D12_WRITEBUFFERIMMEDIATE_MODE_MARKER_OUT;
    unsigned int marker, enabled, i, j;
    bool ordering_only = argc > 1 && !strcmp(argv[1], "--ordering-only");

    list.device = &device;
    for (enabled = ordering_only ? 2 : 0; enabled < 2; enabled++)
    for (marker = 0; marker < 3; marker++)
    {
        memset(memory, 0, sizeof(memory));
        device.vk_info.AMD_buffer_marker = enabled;
        for (i = 0; i < 3; i++)
        {
            list.wbi_batch.buffers[i] = i;
            list.wbi_batch.offsets[i] = sizeof(uint32_t);
            list.wbi_batch.stages[i] = i == marker ?
                    VK_PIPELINE_STAGE_BOTTOM_OF_PIPE_BIT : VK_PIPELINE_STAGE_TRANSFER_BIT;
            list.wbi_batch.values[i] = 0x12340000u + i;
        }
        list.wbi_batch.batch_len = 3;
        d3d12_command_list_end_wbi_batch(&list);
        assert(!list.wbi_batch.batch_len);
        for (i = 0; i < 3; i++)
        for (j = 0; j < 4; j++)
        {
            uint32_t expected = j == 1 ? list.wbi_batch.values[i] : 0;
            if (memory[i][j] != expected)
            {
                fprintf(stderr, "AMD marker %u, marker index %u, buffer %u word %u: "
                        "got %#x, expected %#x\n", enabled, marker, i, j, memory[i][j], expected);
                return 1;
            }
        }
    }
    /* A zero-count call must neither drain draws nor emit dependencies. */
    drain_calls = transfer_calls = 0;
    d3d12_command_list_WriteBufferImmediate(&list, 0, NULL, NULL);
    assert(!drain_calls && !transfer_calls);

    memset(memory, 0, sizeof(memory));
    event_serial = draw_serial = write_serial = 0;
    device.vk_info.AMD_buffer_marker = true;
    list.pending_draws = 2;
    list.rendering_info.state_flags = VKD3D_RENDERING_ACTIVE;
    /* MARKER_OUT must follow earlier draws even when its destination has no
     * shader-read dependency. The draw does not access the marker buffer. */
    d3d12_command_list_WriteBufferImmediate(&list, 1, &parameter, &mode);
    d3d12_command_list_flush_dgc_batch(&list);
    d3d12_command_list_end_current_render_pass(&list, false);
    if (!draw_serial || draw_serial >= write_serial || memory[0][1] != 22)
    {
        fprintf(stderr, "Deferred draw order %u must precede MARKER_OUT order %u, "
                "final WBI value %u (expected 22)\n", draw_serial, write_serial, memory[0][1]);
        return 1;
    }

    if (!ordering_only)
    {
        /* Observe actual production tracker and barrier scopes. A serial CPU
         * memcpy alone would conceal the missing Vulkan WAW dependency. */
        for (enabled = 0; enabled < 2; enabled++)
        for (i = 0; i < 10; i++)
        {
            D3D12_WRITEBUFFERIMMEDIATE_PARAMETER writes[2] = {{0x1000, 10}, {0x1000, 20}};
            memset(&list, 0, sizeof(list));
            memset(memory, 0, sizeof(memory));
            memset(pending_stages, 0, sizeof(pending_stages));
            memset(available_stages, 0, sizeof(available_stages));
            list.device = &device;
            device.vk_info.AMD_buffer_marker = enabled;
            barriers = hazards = 0;
            check_overlap = true;
            switch (i)
            {
                case 0: /* COPY -> CLEAR. */
                    test_copy(&list, 0, 0, 10);
                    d3d12_command_list_WriteBufferImmediate(&list, 1, &writes[1], NULL);
                    break;
                case 1: /* CLEAR -> COPY. */
                    d3d12_command_list_WriteBufferImmediate(&list, 1, writes, NULL);
                    test_copy(&list, 0, 0, 20);
                    break;
                case 2: /* Two runs in one WBI call. */
                    d3d12_command_list_WriteBufferImmediate(&list, 2, writes, NULL);
                    break;
                case 3: /* Separate WBI calls. */
                    d3d12_command_list_WriteBufferImmediate(&list, 1, writes, NULL);
                    d3d12_command_list_WriteBufferImmediate(&list, 1, &writes[1], NULL);
                    break;
                case 4: /* Adjacent writes must not introduce a barrier. */
                    writes[1].Dest += 4;
                    d3d12_command_list_WriteBufferImmediate(&list, 2, writes, NULL);
                    test_copy(&list, 0, 8, 30);
                    break;
                case 5: /* Both stages must survive a non-overlapping append. */
                    test_copy(&list, 0, 8, 30);
                    d3d12_command_list_WriteBufferImmediate(&list, 1, writes, NULL);
                    d3d12_command_list_WriteBufferImmediate(&list, 1, &writes[1], NULL);
                    break;
                case 6: /* List close releases COPY writes to a later list's CLEAR. */
                    test_copy(&list, 0, 0, 10);
                    d3d12_command_list_resolve_transfer_waw(&list);
                    assert(!list.transfer_batch.tracked_copy_buffer_count);
                    d3d12_command_list_WriteBufferImmediate(&list, 1, &writes[1], NULL);
                    break;
                case 7: /* List close releases CLEAR writes to a later list's COPY. */
                    d3d12_command_list_WriteBufferImmediate(&list, 1, writes, NULL);
                    d3d12_command_list_resolve_transfer_waw(&list);
                    assert(!list.transfer_batch.tracked_copy_buffer_count);
                    test_copy(&list, 0, 0, 20);
                    break;
                case 8:
                case 9:
                {
                    /* A barrier for some other buffer may drain the entire
                     * tracker. Its release must still cover both writer types. */
                    struct d3d12_command_list_barrier_batch batch = {0};
                    VkDependencyInfo dependency = {VK_STRUCTURE_TYPE_DEPENDENCY_INFO, 1, &batch.vk_memory_barrier};
                    if (i == 8)
                        d3d12_command_list_WriteBufferImmediate(&list, 1, writes, NULL);
                    else
                        test_copy(&list, 0, 0, 10);
                    d3d12_command_list_merge_copy_tracking(&list, &batch);
                    vkCmdPipelineBarrier2(list.cmd.vk_command_buffer, &dependency);
                    assert(!list.transfer_batch.tracked_copy_buffer_count);
                    if (i == 8)
                        test_copy(&list, 0, 0, 20);
                    else
                        d3d12_command_list_WriteBufferImmediate(&list, 1, &writes[1], NULL);
                    break;
                }
            }
            if (hazards || barriers != (i == 4 ? 0 : 1))
            {
                fprintf(stderr, "AMD marker %u, overlap case %u: %u hazards, %u barriers\n",
                        enabled, i, hazards, barriers);
                return 1;
            }
        }
        check_overlap = false;
        test_transfer_consumers(&list);
        /* Legacy and enhanced COPY_DEST barriers must retire WBI-only tracking
         * and merge deferred shader dependencies without an ordinary copy. */
        for (i = 0; i < 2; i++)
        {
            struct d3d12_resource buffer = {0};
            struct d3d12_command_list_barrier_batch batch = {0};
            D3D12_RESOURCE_TRANSITION_BARRIER transition =
                    {&buffer, D3D12_RESOURCE_STATE_COPY_DEST, D3D12_RESOURCE_STATE_COPY_SOURCE, 0};
            D3D12_GLOBAL_BARRIER global = {D3D12_BARRIER_SYNC_COPY, D3D12_BARRIER_SYNC_COPY,
                    D3D12_BARRIER_ACCESS_COPY_DEST, D3D12_BARRIER_ACCESS_COPY_SOURCE};
            memset(&list, 0, sizeof(list));
            list.device = &device;
            d3d12_command_list_WriteBufferImmediate(&list, 1, &parameter, NULL);
            list.transfer_batch.read_after_write_hazard_stages = VK_PIPELINE_STAGE_2_COMPUTE_SHADER_BIT;
            list.transfer_batch.write_after_read_hazard_stages = VK_PIPELINE_STAGE_2_COMPUTE_SHADER_BIT;
            if (i)
                d3d12_command_list_merge_copy_tracking_global_barrier(&list, &global, &batch);
            else
                d3d12_command_list_merge_copy_tracking_transition(&list, &transition, &batch);
            assert(!list.transfer_batch.tracked_copy_buffer_count);
            assert(!list.transfer_batch.vk_stages);
            assert(!list.transfer_batch.read_after_write_hazard_stages);
            assert(!list.transfer_batch.write_after_read_hazard_stages);
            assert(batch.vk_memory_barrier.srcStageMask & VK_PIPELINE_STAGE_2_CLEAR_BIT);
            assert(batch.vk_memory_barrier.dstStageMask & VK_PIPELINE_STAGE_2_CLEAR_BIT);
            assert(batch.vk_memory_barrier.dstStageMask & VK_PIPELINE_STAGE_2_COMPUTE_SHADER_BIT);
            assert(batch.vk_memory_barrier.dstAccessMask & VK_ACCESS_2_SHADER_READ_BIT);
        }
    }

    puts(ordering_only ? "Zero-count call and deferred-draw ordering passed." :
            "6 mixed-mode WBI cases, 20 overlap cases, transfer consumers, zero-count call and deferred-draw ordering passed.");
    return 0;
}
'''
    if args.generate:
        args.generate.parent.mkdir(parents=True, exist_ok=True)
        args.generate.write_text(harness)
        return

    args.build_dir.mkdir(parents=True, exist_ok=True)
    source = args.build_dir / "wbi_recording.c"
    binary = args.build_dir / "wbi_recording"
    source.write_text(harness)
    compiler = shlex.split(os.environ.get("CC", "cc"))
    subprocess.run(compiler + ["-std=c11", "-O0", "-g", "-UNDEBUG", str(source), "-o", str(binary)], check=True)
    subprocess.run([str(binary)] + (["--ordering-only"] if args.ordering_only else []), check=True)


if __name__ == "__main__":
    main()
