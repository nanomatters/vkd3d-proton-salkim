#!/usr/bin/env python3
# Copyright 2026 Erhan Bilgili
# SPDX-License-Identifier: LGPL-2.1-or-later
"""Exercise upload-barrier classification and bookkeeping without a GPU.

The helpers, full ResourceBarrier handler and D3D12 declarations are extracted
from production. Backend mocks record synchronization and resource tracking.
"""

import argparse
import os
from pathlib import Path
import shlex
import subprocess

ROOT = Path(__file__).resolve().parents[1]


def block(source, marker, declaration=False):
    start = source.index(marker)
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
    pieces = [PREFIX]
    for kind, names in (("enum", ("D3D12_HEAP_TYPE", "D3D12_RESOURCE_DIMENSION",
                                 "D3D12_COMMAND_LIST_TYPE", "D3D12_RESOURCE_STATES",
                                 "D3D12_RESOURCE_BARRIER_TYPE", "D3D12_RESOURCE_BARRIER_FLAGS")),
                        ("struct", ("D3D12_RESOURCE_TRANSITION_BARRIER", "D3D12_RESOURCE_ALIASING_BARRIER",
                                   "D3D12_RESOURCE_UAV_BARRIER", "D3D12_RESOURCE_BARRIER"))):
        pieces.extend(block(idl, "typedef " + kind + " " + name + "\n", True) for name in names)
    pieces += [MOCKS, block(source,
        "static bool d3d12_resource_barrier_is_read_only_upload("), block(source,
        "static bool d3d12_command_list_check_resource_barrier_read_only_upload("), HANDLER_MOCKS,
        block(source, "static void STDMETHODCALLTYPE d3d12_command_list_ResourceBarrier("), TESTS]
    return "\n".join(pieces)


PREFIX = r'''
#include <assert.h>
#include <stdbool.h>
#include <stdint.h>
#include <stdio.h>
#include <string.h>
typedef unsigned int UINT;
typedef struct d3d12_resource ID3D12Resource;
#define D3D12_RESOURCE_BARRIER_ALL_SUBRESOURCES UINT32_MAX
#define ARRAY_SIZE(a) (sizeof(a) / sizeof((a)[0]))
#define TRACE(...) ((void)0)
'''

MOCKS = r'''
struct d3d12_resource
{
    struct { D3D12_HEAP_TYPE Type; } heap_properties;
    struct { D3D12_RESOURCE_DIMENSION Dimension; unsigned int Flags, MipLevels; } desc;
    unsigned int flags;
    struct { unsigned int vk_aspect_mask; } *format;
};
struct d3d12_command_list
{
    D3D12_COMMAND_LIST_TYPE type;
    struct
    {
        uintptr_t vk_command_buffer, vk_post_indirect_barrier_commands;
        bool observes_indirect_argument_barrier;
        struct { bool has_visible_clear_stage; } clear_uav;
    } cmd;
    struct d3d12_resource *tracked[8];
    unsigned int tracked_count, vk_queue_flags, validated, breadcrumbs;
    struct { bool pending_rtas_work; } rtas_batch;
    struct
    {
        uint64_t masks[4];
        uint64_t maps, merges, globals, images, uav, rtas, clears, aliases, batches;
    } sync;
};
#define impl_from_ID3D12Resource(r) (r)
#define d3d12_resource_is_buffer(r) ((r)->desc.Dimension == D3D12_RESOURCE_DIMENSION_BUFFER)

static void d3d12_command_list_track_resource_usage(struct d3d12_command_list *list,
        struct d3d12_resource *resource, bool initial_transition)
{
    assert(initial_transition);
    assert(list->tracked_count < ARRAY_SIZE(list->tracked));
    list->tracked[list->tracked_count++] = resource;
}
'''


# Compile the complete production dispatcher. Only backend calls and the unrelated
# deferred-copy optimization are mocked; no barrier loop/filtering is reimplemented.
HANDLER_MOCKS = r'''
typedef struct d3d12_command_list d3d12_command_list_iface;
typedef uint64_t VkAccessFlags2, VkPipelineStageFlags2;
typedef unsigned int VkImageLayout;
typedef struct { unsigned int aspectMask, baseArrayLayer, layerCount, mipLevel; } VkImageSubresourceLayers;
typedef VkImageSubresourceLayers VkImageSubresource;
typedef struct { unsigned int unused; } VkImageMemoryBarrier2;
struct d3d12_command_list_barrier_batch
{
    struct { uint64_t srcStageMask, srcAccessMask, dstStageMask, dstAccessMask; } vk_memory_barrier;
};
#define STDMETHODCALLTYPE
#define impl_from_ID3D12GraphicsCommandList(iface) (iface)
#define d3d12_resource_is_texture(r) (!d3d12_resource_is_buffer(r))
#define d3d12_resource_is_acceleration_structure(r) false
#define d3d12_resource_may_alias_other_resources(r) true
#define VK_IMAGE_LAYOUT_UNDEFINED 0
#define VKD3D_RESOURCE_COPY_QUEUE_COMPATIBLE 1
#define VKD3D_RESOURCE_LINEAR_STAGING_COPY 2
#define D3D12_RESOURCE_FLAG_ALLOW_DEPTH_STENCIL 1
#define VK_ACCESS_2_MEMORY_READ_BIT (UINT64_C(1) << 60)
#define VK_ACCESS_2_MEMORY_WRITE_BIT (UINT64_C(1) << 61)
#define VK_PIPELINE_STAGE_2_ALL_COMMANDS_BIT (UINT64_C(1) << 62)
#define WARN(...) ((void)0)
#define FIXME_ONCE(...) ((void)0)
#define VKD3D_CONFIG_FLAG_IS_SET(...) false
#define VKD3D_BREADCRUMB_COOKIE(...) ((void)0)
#define VKD3D_BREADCRUMB_AUX32(...) ((void)0)
#define VKD3D_BREADCRUMB_TAG(...) (++list->breadcrumbs)
#define VKD3D_BREADCRUMB_COMMAND(...) ((void)0)
#define is_valid_resource_state(state) (++list->validated, true)
#define d3d12_command_list_mark_as_invalid(...) assert(false)
#define d3d12_command_list_check_resource_barrier_trivial_copy_resource(...) false
#define d3d12_command_list_flush_dgc_batch(...) ((void)0)
#define d3d12_command_list_flush_discard_barriers(...) ((void)0)
#define d3d12_command_list_end_current_render_pass(...) ((void)0)
#define d3d12_command_list_end_transfer_batch(...) ((void)0)
#define d3d12_command_list_debug_mark_begin_region(...) ((void)0)
#define d3d12_command_list_debug_mark_end_region(...) ((void)0)
#define d3d12_command_list_check_render_pass_validation(...) ((void)0)
#define d3d12_command_list_debug_mark_label(...) ((void)0)
#define d3d12_command_list_debug_mark_label_printf(...) ((void)0)
#define d3d12_command_list_copy_queue_fallback(...) assert(false)
#define d3d12_command_list_flush_rtas_batch(l) (++(l)->sync.rtas)
#define d3d12_command_list_flush_clears(l, ...) (++(l)->sync.clears)
#define d3d12_resource_desc_get_layer_count(...) 1
#define d3d12_resource_get_vk_subresource(...) ((VkImageSubresource){0})
#define vk_subresource_layers_from_subresource(s) (*(s))
#define d3d12_command_list_update_subresource_data(l, r, s) ((void)(s), assert(false))
#define d3d12_command_list_notify_dsv_state(...) 0
#define vk_image_layout_from_d3d12_resource_state(l, r, state) (state)
#define d3d12_command_list_merge_copy_tracking_transition(l, ...) (++(l)->sync.merges)
#define d3d12_command_list_consume_clear_uav_barrier_batch(...) (++list->sync.uav)
#define d3d12_command_list_consume_clear_uav_global_barrier(...) (++list->sync.uav)
#define d3d12_command_list_flush_subresource_updates(l) (++(l)->sync.aliases)
#define d3d12_command_list_barrier_batch_init(b) memset((b), 0, sizeof(*(b)))
#define d3d12_command_list_barrier_batch_end(l, b) \
    (memcpy((l)->sync.masks, &(b)->vk_memory_barrier, sizeof((l)->sync.masks)), ++(l)->sync.batches)
#define vk_image_memory_barrier_for_transition(b, r, s, old, new, ss, sa, ds, da, decay) \
    ((void)(b), (void)(old), (void)(new), (void)(decay))
#define d3d12_command_list_barrier_batch_add_layout_transition(l, b, t) (++(l)->sync.images)

static void vk_access_and_stage_flags_from_d3d12_resource_state(struct d3d12_command_list *list,
        struct d3d12_resource *resource, D3D12_RESOURCE_STATES state, unsigned int queue_flags,
        VkPipelineStageFlags2 *stages, VkAccessFlags2 *access)
{
    (void)queue_flags;
    assert(resource && resource->heap_properties.Type != D3D12_HEAP_TYPE_UPLOAD);
    *stages |= UINT64_C(1) << resource->desc.Dimension;
    *access |= state;
    list->sync.maps++;
}

static void d3d12_command_list_barrier_batch_add_global_transition(struct d3d12_command_list *list,
        struct d3d12_command_list_barrier_batch *batch, uint64_t src_stage, uint64_t src_access,
        uint64_t dst_stage, uint64_t dst_access)
{
    batch->vk_memory_barrier.srcStageMask |= src_stage;
    batch->vk_memory_barrier.srcAccessMask |= src_access;
    batch->vk_memory_barrier.dstStageMask |= dst_stage;
    batch->vk_memory_barrier.dstAccessMask |= dst_access;
    list->sync.globals++;
}
'''

TESTS = r'''
static unsigned int cases;

static struct d3d12_command_list new_list(D3D12_COMMAND_LIST_TYPE type)
{
    struct d3d12_command_list list = {0};
    list.type = type;
    list.cmd.vk_command_buffer = 0x1234;
    list.cmd.vk_post_indirect_barrier_commands = 0x5678;
    return list;
}

static D3D12_RESOURCE_BARRIER transition(struct d3d12_resource *resource,
        D3D12_RESOURCE_STATES before, D3D12_RESOURCE_STATES after)
{
    D3D12_RESOURCE_BARRIER barrier = {0};
    barrier.Transition.pResource = resource;
    barrier.Transition.StateBefore = before;
    barrier.Transition.StateAfter = after;
    return barrier;
}

static void reject(D3D12_COMMAND_LIST_TYPE type, unsigned int count,
        const D3D12_RESOURCE_BARRIER *barriers)
{
    struct d3d12_command_list list = new_list(type), before;
    memcpy(&before, &list, sizeof(list));
    assert(!d3d12_command_list_check_resource_barrier_read_only_upload(&list, count, barriers));
    assert(!memcmp(&before, &list, sizeof(list)));
    cases++;
}

static void reject_alone_and_mixed(D3D12_RESOURCE_BARRIER invalid)
{
    struct d3d12_resource upload = {.heap_properties.Type = D3D12_HEAP_TYPE_UPLOAD,
        .desc.Dimension = D3D12_RESOURCE_DIMENSION_BUFFER};
    D3D12_RESOURCE_BARRIER barriers[] = {
        transition(&upload, D3D12_RESOURCE_STATE_VERTEX_AND_CONSTANT_BUFFER, D3D12_RESOURCE_STATE_GENERIC_READ),
        invalid,
    };
    reject(D3D12_COMMAND_LIST_TYPE_DIRECT, 1, &invalid);
    /* The valid prefix would update BOTH tracking and indirect metadata if applied too soon. */
    reject(D3D12_COMMAND_LIST_TYPE_DIRECT, ARRAY_SIZE(barriers), barriers);
}

static void test_mixed_batches(void)
{
    struct d3d12_resource upload = {.heap_properties.Type = D3D12_HEAP_TYPE_UPLOAD,
        .desc.Dimension = D3D12_RESOURCE_DIMENSION_BUFFER};
    struct d3d12_resource buffer = upload, image = upload;
    D3D12_RESOURCE_BARRIER base[4], mixed[6];
    D3D12_RESOURCE_BARRIER read = transition(&upload, D3D12_RESOURCE_STATE_NON_PIXEL_SHADER_RESOURCE,
        D3D12_RESOURCE_STATE_GENERIC_READ);
    D3D12_RESOURCE_BARRIER tail = transition(&upload, D3D12_RESOURCE_STATE_GENERIC_READ,
        D3D12_RESOURCE_STATE_VERTEX_AND_CONSTANT_BUFFER);
    buffer.heap_properties.Type = image.heap_properties.Type = D3D12_HEAP_TYPE_DEFAULT;
    image.desc.Dimension = D3D12_RESOURCE_DIMENSION_TEXTURE2D;
    base[0] = transition(&buffer, D3D12_RESOURCE_STATE_NON_PIXEL_SHADER_RESOURCE,
        D3D12_RESOURCE_STATE_VERTEX_AND_CONSTANT_BUFFER);
    base[1] = (D3D12_RESOURCE_BARRIER){.Type = D3D12_RESOURCE_BARRIER_TYPE_UAV, .UAV.pResource = &buffer};
    base[2] = (D3D12_RESOURCE_BARRIER){.Type = D3D12_RESOURCE_BARRIER_TYPE_ALIASING,
        .Aliasing = {.pResourceBefore = &buffer, .pResourceAfter = &buffer}};
    base[3] = transition(&image, D3D12_RESOURCE_STATE_RENDER_TARGET, D3D12_RESOURCE_STATE_PIXEL_SHADER_RESOURCE);

    for (unsigned int type = D3D12_COMMAND_LIST_TYPE_DIRECT; type <= D3D12_COMMAND_LIST_TYPE_COMPUTE; type += 2)
    for (unsigned int group = 0; group <= ARRAY_SIZE(base); group++)
    {
        unsigned int n = group == ARRAY_SIZE(base) ? ARRAY_SIZE(base) : 1;
        const D3D12_RESOURCE_BARRIER *selected = n == 1 ? &base[group] : base;
        struct d3d12_command_list baseline = new_list(type);
        d3d12_command_list_ResourceBarrier(&baseline, n, selected);
        assert(baseline.sync.batches == 1);
        assert(baseline.sync.maps == (n == 1 ? (group == 2 ? 0 : 2) : 6));
        assert(baseline.sync.globals == (n > 1 || group == 0));
        assert(baseline.sync.images == (n > 1 || group == 3));
        assert(baseline.sync.uav == (n == 1 ? (group == 1 || group == 2) : 2));
        assert(!baseline.cmd.observes_indirect_argument_barrier);
        for (unsigned int reverse = 0; reverse < 2; reverse++)
        for (unsigned int insert = 0; insert <= n; insert++)
        {
            struct d3d12_command_list list = new_list(type);
            unsigned int count = 0, tracked = 0, transitions = 0;
            for (unsigned int j = 0; j <= n; j++)
            {
                if (j == insert)
                    mixed[count++] = read;
                if (j < n)
                    mixed[count++] = selected[reverse ? n - 1 - j : j];
            }
            mixed[count++] = tail; /* Later non-indirect reads must retain indirect bookkeeping. */
            d3d12_command_list_ResourceBarrier(&list, count, mixed);
            assert(!memcmp(&baseline.sync, &list.sync, sizeof(list.sync)));
            assert(list.cmd.observes_indirect_argument_barrier);
            assert(list.cmd.vk_post_indirect_barrier_commands == list.cmd.vk_command_buffer);
            assert(list.breadcrumbs == count);
            for (unsigned int j = 0; j < count; j++)
            {
                if (mixed[j].Type == D3D12_RESOURCE_BARRIER_TYPE_TRANSITION)
                {
                    transitions++;
                    assert(list.tracked[tracked++] == mixed[j].Transition.pResource);
                }
                else if (mixed[j].Type == D3D12_RESOURCE_BARRIER_TYPE_UAV)
                    assert(list.tracked[tracked++] == mixed[j].UAV.pResource);
            }
            assert(list.tracked_count == tracked && list.validated == 2 * transitions);
            cases++;
        }
    }
}

int main(void)
{
    struct d3d12_resource upload = {.heap_properties.Type = D3D12_HEAP_TYPE_UPLOAD,
        .desc.Dimension = D3D12_RESOURCE_DIMENSION_BUFFER};
    struct d3d12_resource other = upload;
    D3D12_RESOURCE_BARRIER valid = transition(&upload,
        D3D12_RESOURCE_STATE_GENERIC_READ, D3D12_RESOURCE_STATE_VERTEX_AND_CONSTANT_BUFFER), bad;
    static const D3D12_RESOURCE_STATES invalid_states[] = {
        D3D12_RESOURCE_STATE_COMMON, D3D12_RESOURCE_STATE_COPY_DEST,
        D3D12_RESOURCE_STATE_UNORDERED_ACCESS, D3D12_RESOURCE_STATE_STREAM_OUT,
        D3D12_RESOURCE_STATE_RENDER_TARGET, D3D12_RESOURCE_STATE_DEPTH_READ,
        D3D12_RESOURCE_STATE_RESOLVE_SOURCE, D3D12_RESOURCE_STATE_RAYTRACING_ACCELERATION_STRUCTURE,
        D3D12_RESOURCE_STATE_GENERIC_READ | D3D12_RESOURCE_STATE_COPY_DEST, 0x80000000u,
    };

    reject(D3D12_COMMAND_LIST_TYPE_DIRECT, 0, NULL);
    reject(D3D12_COMMAND_LIST_TYPE_COPY, 1, &valid);
    for (unsigned int type = D3D12_HEAP_TYPE_DEFAULT; type <= D3D12_HEAP_TYPE_GPU_UPLOAD; type++)
    {
        if (type == D3D12_HEAP_TYPE_UPLOAD)
            continue;
        other.heap_properties.Type = type;
        bad = valid;
        bad.Transition.pResource = &other;
        reject_alone_and_mixed(bad);
    }
    other = upload;
    for (unsigned int dimension = D3D12_RESOURCE_DIMENSION_TEXTURE1D;
            dimension <= D3D12_RESOURCE_DIMENSION_TEXTURE3D; dimension++)
    {
        other.desc.Dimension = dimension;
        bad = valid;
        bad.Transition.pResource = &other;
        reject_alone_and_mixed(bad);
    }
    bad = valid;
    bad.Transition.pResource = NULL;
    reject_alone_and_mixed(bad);
    bad = valid;
    bad.Transition.Subresource = 1;
    reject_alone_and_mixed(bad);
    for (unsigned int flags = 1; flags <= 4; flags++)
    {
        bad = valid;
        bad.Flags = flags;
        reject_alone_and_mixed(bad);
    }
    for (unsigned int type = D3D12_RESOURCE_BARRIER_TYPE_ALIASING; type <= 3; type++)
    {
        bad = valid;
        bad.Type = type;
        reject_alone_and_mixed(bad);
    }
    for (unsigned int i = 0; i < ARRAY_SIZE(invalid_states); i++)
    {
        bad = valid;
        bad.Transition.StateBefore = invalid_states[i];
        reject_alone_and_mixed(bad);
        bad = valid;
        bad.Transition.StateAfter = invalid_states[i];
        reject_alone_and_mixed(bad);
    }

    /* Every nonempty read subset is allowed, on direct and compute lists. */
    for (unsigned int type = D3D12_COMMAND_LIST_TYPE_DIRECT; type <= D3D12_COMMAND_LIST_TYPE_COMPUTE; type += 2)
    {
        for (unsigned int state = D3D12_RESOURCE_STATE_GENERIC_READ; state;
                state = (state - 1) & D3D12_RESOURCE_STATE_GENERIC_READ)
        {
            struct d3d12_command_list list = new_list(type);
            D3D12_RESOURCE_BARRIER barrier = transition(&upload, D3D12_RESOURCE_STATE_GENERIC_READ, state);
            bool indirect = !!(state & D3D12_RESOURCE_STATE_INDIRECT_ARGUMENT);
            assert(d3d12_command_list_check_resource_barrier_read_only_upload(&list, 1, &barrier));
            assert(list.tracked_count == 1 && list.tracked[0] == &upload);
            assert(list.cmd.observes_indirect_argument_barrier == indirect);
            assert(list.cmd.vk_post_indirect_barrier_commands == (indirect ? 0x1234 : 0x5678));
            cases++;
        }
    }
    {
        struct d3d12_command_list list = new_list(D3D12_COMMAND_LIST_TYPE_DIRECT);
        D3D12_RESOURCE_BARRIER barriers[] = {
            valid, transition(&upload, D3D12_RESOURCE_STATE_VERTEX_AND_CONSTANT_BUFFER, D3D12_RESOURCE_STATE_GENERIC_READ),
            transition(&other, D3D12_RESOURCE_STATE_INDEX_BUFFER, D3D12_RESOURCE_STATE_COPY_SOURCE),
        };
        other = upload;
        barriers[1].Transition.Subresource = D3D12_RESOURCE_BARRIER_ALL_SUBRESOURCES;
        assert(d3d12_command_list_check_resource_barrier_read_only_upload(&list, ARRAY_SIZE(barriers), barriers));
        assert(list.tracked_count == 3 && list.tracked[0] == &upload &&
                list.tracked[1] == &upload && list.tracked[2] == &other);
        /* The final non-indirect transition must not undo an earlier indirect transition. */
        assert(list.cmd.observes_indirect_argument_barrier);
        assert(list.cmd.vk_post_indirect_barrier_commands == list.cmd.vk_command_buffer);
        cases++;
    }
    test_mixed_batches();
    printf("Upload barriers: %u cases passed\n", cases);
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
    output = args.build_dir / "upload_barriers.c"
    executable = args.build_dir / "upload_barriers"
    output.write_text(source)
    subprocess.run(shlex.split(os.environ.get("CC", "cc")) + ["-std=c11", "-O0", "-g", "-UNDEBUG"] +
        shlex.split(os.environ.get("CFLAGS", "")) + [str(output), "-o", str(executable)], check=True)
    subprocess.run([str(executable)], check=True)


if __name__ == "__main__":
    main()
