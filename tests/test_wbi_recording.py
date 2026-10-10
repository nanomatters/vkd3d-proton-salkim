#!/usr/bin/env python3
# Copyright 2026 Erhan Bilgili
# SPDX-License-Identifier: LGPL-2.1-or-later
"""Exercise the production WBI batch recorder without an AMD Vulkan device."""

import argparse
import os
from pathlib import Path
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
#define VKD3D_RENDERING_ACTIVE 1
#define VKD3D_MAX_WBI_BATCH_SIZE 3
#define VK_CALL(call) call
#define TRACE(...) ((void)0)
#define VKD3D_BREADCRUMB_COMMAND(...) ((void)0)
#define STDMETHODCALLTYPE

typedef uint32_t UINT;
typedef uint64_t VkDeviceSize;
typedef unsigned int VkPipelineStageFlagBits;
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
struct d3d12_command_list
{
    struct test_device *device;
    struct { unsigned int state_flags; } rendering_info;
    struct { unsigned int build_info_count; } rtas_batch;
    unsigned int query_resolve_count;
    unsigned int pending_draws;
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
    assert(buffer < 3 && offset + size <= sizeof(memory[buffer]));
    memcpy((char *)memory[buffer] + offset, data, size);
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
    source_text = args.source.read_text()
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

    puts(ordering_only ? "Zero-count call and deferred-draw ordering passed." :
            "6 mixed-mode WBI cases, zero-count call and deferred-draw ordering passed.");
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
