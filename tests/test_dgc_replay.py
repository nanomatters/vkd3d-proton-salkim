#!/usr/bin/env python3
# Copyright 2026 Erhan Bilgili
# SPDX-License-Identifier: LGPL-2.1-or-later
"""Exercise production DGC replay with deterministic recording and failure injection.

The batch drain and scissor clamp are extracted unchanged from command.c. Vulkan
calls and allocation are recorded so failures do not require exhausting a GPU.
Use --revision e9c64a8e as a negative control against the unpatched source.
Use --revision 53bf7426 as a negative control for sticky command-list validity.
Artifacts remain in the explicitly selected --output-dir.
"""

import argparse
import os
from pathlib import Path
import re
import shlex
import subprocess


PREFIX = r'''
#include <assert.h>
#include <stdbool.h>
#include <stddef.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <vulkan/vulkan.h>

#define VK_CALL(call) (vk_procs->call)
#define FAILED(hr) ((hr) < 0)
#define S_OK 0
#define S_FALSE 1
#define E_FAIL ((HRESULT)0x80004005)
#define WARN(...) ((void)0)
#define max(a,b) ((a) > (b) ? (a) : (b))
#define min(a,b) ((a) < (b) ? (a) : (b))
#define D3D12_VIEWPORT_AND_SCISSORRECT_OBJECT_COUNT_PER_PIPELINE 16
typedef int32_t HRESULT;

struct vkd3d_pipeline_bindings { unsigned int cookie; };
struct vkd3d_index_buffer { unsigned int cookie; };
struct vkd3d_dynamic_state
{
    unsigned int view_mask, strip_cut, viewport_count;
    float depth_bias;
    VkViewport viewports[16];
    VkRect2D scissors[16];
};
struct d3d12_pipeline_state
{
    int ID3D12PipelineState_iface;
    struct { struct { bool dynamic_mask; } multiview; } graphics;
};
struct d3d12_command_signature { unsigned int pipeline_type; };
struct d3d12_resource { unsigned int id; };
struct vkd3d_scratch_allocation { VkDeviceAddress va; };
struct vkd3d_dgc_batch_draw
{
    struct d3d12_command_signature *signature;
    uint32_t max_command_count;
    struct d3d12_resource *arg_buffer, *count_buffer;
    uint64_t arg_buffer_offset, count_buffer_offset;
    struct d3d12_pipeline_state *state;
    struct vkd3d_index_buffer index_buffer;
    struct vkd3d_dynamic_state dynamic_state;
    struct vkd3d_pipeline_bindings graphics_bindings;
    VkDeviceAddress preprocess_va;
    VkDeviceSize preprocess_size;
};
struct vkd3d_vk_device_procs
{
    PFN_vkCmdPipelineBarrier2 vkCmdPipelineBarrier2;
    PFN_vkCmdSetScissorWithCount vkCmdSetScissorWithCount;
};
struct d3d12_device { struct vkd3d_vk_device_procs vk_procs; };
struct d3d12_command_list
{
    int ID3D12GraphicsCommandList_iface;
    struct d3d12_device *device;
    struct vkd3d_pipeline_bindings graphics_bindings;
    struct vkd3d_dynamic_state dynamic_state;
    struct vkd3d_index_buffer index_buffer;
    struct d3d12_pipeline_state *state;
    struct { struct vkd3d_dgc_batch_draw *draws; size_t draws_count; } dgc_batch;
    struct { VkCommandBuffer vk_command_buffer; } cmd;
    struct { struct { VkRect2D renderArea; } info; } rendering_info;
    unsigned int fb_width, fb_height;
    uint64_t current_pipeline;
    bool is_valid;
};
enum vkd3d_dgc_mode
{
    VKD3D_DGC_MODE_APPLICATION_CALL,
    VKD3D_DGC_MODE_PREPROCESS_ONLY,
    VKD3D_DGC_MODE_EXECUTE_ONLY,
    VKD3D_DGC_MODE_PREPROCESS_AND_EXECUTE
};

static unsigned int failures, cases;
static unsigned int prepared[4], executed[4], allocation_calls, pipeline_calls;
static unsigned int fail_allocation, fail_pipeline, fail_recording, skip_recording, recording_calls;
static unsigned int barrier_calls, region_calls;
static bool initially_valid;
static const char *case_name;
static int debug_depth;
static VkRect2D recorded_scissor;

static void check(bool condition, const char *message)
{
    if (!condition)
    {
        fprintf(stderr, "Case %u (%s, initially %s): %s\n", cases, case_name,
                initially_valid ? "valid" : "invalid", message);
        failures++;
    }
}

static void d3d12_command_list_SetPipelineState(int *iface, int *pipeline)
{
    struct d3d12_command_list *list = (void *)iface;
    list->state = (void *)pipeline;
    /* D3D12 PSO binding overwrites these dynamic overrides. */
    list->dynamic_state.depth_bias = 0;
    list->dynamic_state.strip_cut = 0;
}

static void d3d12_command_list_invalidate_all_state(struct d3d12_command_list *list) {}
static void d3d12_command_list_promote_dsv_layout(struct d3d12_command_list *list) {}
static void d3d12_command_list_end_current_render_pass(struct d3d12_command_list *list, bool suspend) {}
static void d3d12_command_list_debug_mark_begin_region(struct d3d12_command_list *list, const char *name)
{
    check(initially_valid || !list->is_valid, "sticky invalid flag was reset");
    region_calls++;
    debug_depth++;
}
static void d3d12_command_list_debug_mark_end_region(struct d3d12_command_list *list)
{
    debug_depth--;
    check(debug_depth >= 0, "debug region underflow");
}
static void d3d12_command_list_mark_as_invalid(struct d3d12_command_list *list, const char *fmt, ...)
{
    list->is_valid = false;
}
static bool d3d12_command_list_update_graphics_pipeline(struct d3d12_command_list *list, unsigned int type)
{
    check(initially_valid || !list->is_valid, "sticky invalid flag was reset");
    pipeline_calls++;
    if (list->state->graphics.multiview.dynamic_mask && !list->dynamic_state.view_mask)
        return false;
    list->current_pipeline = pipeline_calls;
    return pipeline_calls != fail_pipeline;
}
static HRESULT d3d12_command_signature_allocate_preprocess_memory_for_list(
        struct d3d12_command_list *list, struct d3d12_command_signature *signature,
        uint64_t pipeline, bool explicit_preprocess, uint32_t count,
        struct vkd3d_scratch_allocation *allocation, VkDeviceSize *size)
{
    allocation_calls++;
    if (allocation_calls == fail_allocation)
        return -1;
    allocation->va = 0x1000 * allocation_calls;
    *size = 0x1000;
    return 0;
}
static HRESULT d3d12_command_list_execute_indirect_state_template_dgc(struct d3d12_command_list *list,
        struct d3d12_command_signature *signature, uint32_t count, struct d3d12_resource *arguments,
        uint64_t offset, struct d3d12_resource *count_buffer, uint64_t count_offset,
        enum vkd3d_dgc_mode mode, VkDeviceAddress address, VkDeviceSize size)
{
    unsigned int id = arguments->id;
    check(initially_valid || !list->is_valid, "sticky invalid flag was reset");
    /* The immediate path also skips a zero-mask draw when it updates its PSO. */
    if (list->state->graphics.multiview.dynamic_mask && !list->dynamic_state.view_mask)
        return S_FALSE;
    if (mode == VKD3D_DGC_MODE_PREPROCESS_AND_EXECUTE &&
            !d3d12_command_list_update_graphics_pipeline(list, signature->pipeline_type))
        return S_FALSE;
    if (++recording_calls == fail_recording)
    {
        list->is_valid = false;
        return E_FAIL;
    }
    /* A nested pipeline/state check can skip a draw after outer preparation. */
    if (recording_calls == skip_recording)
        return S_FALSE;
    check(list->graphics_bindings.cookie == id + 10, "captured root bindings not restored");
    check(list->index_buffer.cookie == id + 20, "captured index binding not restored");
    check(list->dynamic_state.depth_bias == (float)id + 30, "captured depth bias not restored");
    check(list->dynamic_state.strip_cut == id + 40, "captured strip cut not restored");
    if (mode == VKD3D_DGC_MODE_PREPROCESS_ONLY)
        prepared[id]++;
    else
    {
        if (mode == VKD3D_DGC_MODE_EXECUTE_ONLY)
            check(address && size && prepared[id] == 1, "executing unprepared commands");
        executed[id]++;
    }
    return S_OK;
}
static void VKAPI_CALL record_barrier(VkCommandBuffer command_buffer, const VkDependencyInfo *info)
{
    barrier_calls++;
    check(info->memoryBarrierCount == 1, "missing preprocessing barrier");
    check(info->pMemoryBarriers[0].srcStageMask == VK_PIPELINE_STAGE_2_COMMAND_PREPROCESS_BIT_EXT,
            "incorrect preprocessing source stage");
    check(info->pMemoryBarriers[0].dstStageMask == VK_PIPELINE_STAGE_2_DRAW_INDIRECT_BIT,
            "incorrect execution destination stage");
}
static void VKAPI_CALL record_scissor(VkCommandBuffer command_buffer, uint32_t count, const VkRect2D *scissors)
{
    check(count == 1, "unexpected scissor count");
    recorded_scissor = scissors[0];
}
'''

SUFFIX = r'''
struct replay_case
{
    const char *name;
    unsigned int count, disabled_mask;
    unsigned int allocation_failure, pipeline_failure, recording_failure, recording_skip;
    unsigned int prepared_mask, executed_mask, recording_calls, barriers;
};

static void test_batch(const struct replay_case *test, bool valid)
{
    struct d3d12_device device = {{record_barrier, record_scissor}};
    struct d3d12_pipeline_state states[5] = {{0}};
    struct d3d12_command_signature signature = {0};
    struct vkd3d_dgc_batch_draw draws[4] = {{0}};
    struct d3d12_resource arguments[4] = {{0}};
    struct d3d12_command_list list = {0};
    unsigned int i;

    cases++;
    case_name = test->name;
    initially_valid = valid;
    memset(prepared, 0, sizeof(prepared));
    memset(executed, 0, sizeof(executed));
    allocation_calls = pipeline_calls = recording_calls = 0;
    barrier_calls = region_calls = 0;
    fail_allocation = test->allocation_failure;
    fail_pipeline = test->pipeline_failure;
    fail_recording = test->recording_failure;
    skip_recording = test->recording_skip;
    debug_depth = 0;
    list.device = &device;
    list.state = &states[4];
    list.graphics_bindings.cookie = 101;
    list.index_buffer.cookie = 102;
    list.dynamic_state.depth_bias = 103;
    list.dynamic_state.strip_cut = 104;
    list.dynamic_state.view_mask = 105;
    list.dgc_batch.draws = draws;
    list.dgc_batch.draws_count = test->count;
    list.is_valid = valid;
    for (i = 0; i < test->count; i++)
    {
        states[i].graphics.multiview.dynamic_mask = true;
        arguments[i].id = i;
        draws[i].signature = &signature;
        draws[i].state = &states[i];
        draws[i].arg_buffer = &arguments[i];
        draws[i].dynamic_state.view_mask = test->disabled_mask & (1u << i) ? 0 : 1;
        draws[i].dynamic_state.depth_bias = i + 30;
        draws[i].dynamic_state.strip_cut = i + 40;
        draws[i].graphics_bindings.cookie = i + 10;
        draws[i].index_buffer.cookie = i + 20;
    }

    d3d12_command_list_flush_dgc_batch(&list);
    check(list.dgc_batch.draws_count == 0, "batch not drained");
    check(debug_depth == 0, "unbalanced debug region after replay");
    check(list.state == &states[4], "application PSO not restored");
    check(list.graphics_bindings.cookie == 101, "application root bindings not restored");
    check(list.index_buffer.cookie == 102, "application index binding not restored");
    check(list.dynamic_state.depth_bias == 103, "application depth bias overwritten");
    check(list.dynamic_state.strip_cut == 104, "application strip cut overwritten");
    check(list.dynamic_state.view_mask == 105, "application view mask not restored");
    check(list.is_valid == (valid && !test->allocation_failure && !test->recording_failure),
            "wrong command-list validity");
    check(recording_calls == test->recording_calls, "wrong number of nested recording calls");
    check(barrier_calls == test->barriers, "wrong number of preprocessing barriers");
    for (i = 0; i < test->count; i++)
    {
        check(prepared[i] == !!(test->prepared_mask & (1u << i)),
                "wrong set of prepared draws");
        check(executed[i] == !!(test->executed_mask & (1u << i)),
                "draw dropped or skipped draw executed");
    }
    /* A failed drain must not retry partial work on the next flush. */
    {
        unsigned int old_allocation_calls = allocation_calls, old_pipeline_calls = pipeline_calls;
        unsigned int old_recording_calls = recording_calls, old_barrier_calls = barrier_calls;
        unsigned int old_region_calls = region_calls;

        d3d12_command_list_flush_dgc_batch(&list);
        check(!debug_depth && region_calls == old_region_calls &&
                allocation_calls == old_allocation_calls && pipeline_calls == old_pipeline_calls &&
                recording_calls == old_recording_calls && barrier_calls == old_barrier_calls,
                "empty flush emitted commands or retried work");
    }
}

static void test_scissors(void)
{
    struct d3d12_device device = {{record_barrier, record_scissor}};
    struct d3d12_command_list list = {0};
    const unsigned int extents[] = {4, 16};
    unsigned int i, j;

    case_name = "scissor clamp";
    list.device = &device;
    list.dynamic_state.viewport_count = 1;
    list.dynamic_state.viewports[0].width = 16;
    list.dynamic_state.viewports[0].height = 16;
    list.dynamic_state.scissors[0].extent.width = 16;
    list.dynamic_state.scissors[0].extent.height = 16;
    for (i = 0; i < 2; i++)
    {
        for (j = 0; j < 2; j++)
        {
            VkRect2D preprocessed;
            cases++;
            list.fb_width = list.fb_height = extents[i];
            list.rendering_info.info.renderArea.extent.width = extents[j];
            list.rendering_info.info.renderArea.extent.height = extents[j];
            update_scissors(&list);
            preprocessed = recorded_scissor;
            list.rendering_info.info.renderArea.extent.width = list.fb_width;
            list.rendering_info.info.renderArea.extent.height = list.fb_height;
            update_scissors(&list);
            check(!memcmp(&preprocessed, &recorded_scissor, sizeof(preprocessed)),
                    "preprocessing and execution use different scissors");
            check(recorded_scissor.extent.width == extents[i] && recorded_scissor.extent.height == extents[i],
                    "scissor not clamped to the current attachments");
        }
    }
}

int main(void)
{
    static const struct replay_case tests[] =
    {
        {.name = "empty"},
        {.name = "single healthy", .count = 1, .executed_mask = 1, .recording_calls = 1},
        {.name = "batch healthy", .count = 3, .prepared_mask = 7, .executed_mask = 7,
            .recording_calls = 6, .barriers = 1},
        {.name = "single disabled", .count = 1, .disabled_mask = 1},
        {.name = "single pipeline skip", .count = 1, .pipeline_failure = 1},
        {.name = "single nested failure", .count = 1, .recording_failure = 1, .recording_calls = 1},
        {.name = "single nested skip", .count = 1, .recording_skip = 1, .recording_calls = 1},
        {.name = "first disabled", .count = 3, .disabled_mask = 1,
            .prepared_mask = 6, .executed_mask = 6, .recording_calls = 4, .barriers = 1},
        {.name = "middle disabled", .count = 3, .disabled_mask = 2,
            .prepared_mask = 5, .executed_mask = 5, .recording_calls = 4, .barriers = 1},
        {.name = "last disabled", .count = 3, .disabled_mask = 4,
            .prepared_mask = 3, .executed_mask = 3, .recording_calls = 4, .barriers = 1},
        {.name = "first allocation failure", .count = 3, .allocation_failure = 1},
        {.name = "middle allocation failure", .count = 3, .allocation_failure = 2,
            .prepared_mask = 1, .recording_calls = 1},
        {.name = "last allocation failure", .count = 3, .allocation_failure = 3,
            .prepared_mask = 3, .recording_calls = 2},
        {.name = "first outer pipeline skip", .count = 3, .pipeline_failure = 1,
            .prepared_mask = 6, .executed_mask = 6, .recording_calls = 4, .barriers = 1},
        {.name = "middle outer pipeline skip", .count = 3, .pipeline_failure = 2,
            .prepared_mask = 5, .executed_mask = 5, .recording_calls = 4, .barriers = 1},
        {.name = "last outer pipeline skip", .count = 3, .pipeline_failure = 3,
            .prepared_mask = 3, .executed_mask = 3, .recording_calls = 4, .barriers = 1},
        {.name = "first nested preprocess failure", .count = 3, .recording_failure = 1,
            .recording_calls = 1},
        {.name = "middle nested preprocess failure", .count = 3, .recording_failure = 2,
            .prepared_mask = 1, .recording_calls = 2},
        {.name = "last nested preprocess failure", .count = 3, .recording_failure = 3,
            .prepared_mask = 3, .recording_calls = 3},
        {.name = "first nested execution failure", .count = 3, .recording_failure = 4,
            .prepared_mask = 7, .recording_calls = 4, .barriers = 1},
        {.name = "middle nested execution failure", .count = 3, .recording_failure = 5,
            .prepared_mask = 7, .executed_mask = 1, .recording_calls = 5, .barriers = 1},
        {.name = "last nested execution failure", .count = 3, .recording_failure = 6,
            .prepared_mask = 7, .executed_mask = 3, .recording_calls = 6, .barriers = 1},
        {.name = "first nested preprocess skip", .count = 3, .recording_skip = 1,
            .prepared_mask = 6, .executed_mask = 6, .recording_calls = 5, .barriers = 1},
        {.name = "middle nested preprocess skip", .count = 3, .recording_skip = 2,
            .prepared_mask = 5, .executed_mask = 5, .recording_calls = 5, .barriers = 1},
        {.name = "last nested preprocess skip", .count = 3, .recording_skip = 3,
            .prepared_mask = 3, .executed_mask = 3, .recording_calls = 5, .barriers = 1},
        {.name = "first nested execution skip", .count = 3, .recording_skip = 4,
            .prepared_mask = 7, .executed_mask = 6, .recording_calls = 6, .barriers = 1},
        {.name = "middle nested execution skip", .count = 3, .recording_skip = 5,
            .prepared_mask = 7, .executed_mask = 5, .recording_calls = 6, .barriers = 1},
        {.name = "last nested execution skip", .count = 3, .recording_skip = 6,
            .prepared_mask = 7, .executed_mask = 3, .recording_calls = 6, .barriers = 1},
        {.name = "allocation failure after disabled draw", .count = 3, .disabled_mask = 2,
            .allocation_failure = 2, .prepared_mask = 1, .recording_calls = 1},
        {.name = "nested execution failure after disabled draw", .count = 3, .disabled_mask = 2,
            .recording_failure = 3, .prepared_mask = 5, .recording_calls = 3, .barriers = 1},
        {.name = "nested preprocess skip after disabled draw", .count = 3, .disabled_mask = 2,
            .recording_skip = 2, .prepared_mask = 1, .executed_mask = 1, .recording_calls = 3, .barriers = 1},
    };
    unsigned int i;

    for (i = 0; i < sizeof(tests) / sizeof(tests[0]); i++)
    {
        test_batch(&tests[i], true);
        test_batch(&tests[i], false);
    }
    test_scissors();
    printf("%u DGC replay/scissor cases, %u failures\n", cases, failures);
    return failures ? EXIT_FAILURE : EXIT_SUCCESS;
}
'''


def body(source, start):
    opening = source.index("{", start)
    end, depth = opening + 1, 1
    while depth:
        depth += (source[end] == "{") - (source[end] == "}")
        end += 1
    return opening, end


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-dir", type=Path)
    parser.add_argument("--generate", type=Path, help="Only generate the C harness for the configured build")
    parser.add_argument("--revision")
    parser.add_argument("--cc", default=os.environ.get("CC", "cc"))
    args = parser.parse_args()
    if not args.generate and not args.output_dir:
        parser.error("--output-dir is required unless --generate is used")
    root = Path(__file__).resolve().parents[1]
    if args.revision:
        source = subprocess.check_output(["git", "show", f"{args.revision}:libs/vkd3d/command.c"],
                cwd=root, text=True)
    else:
        source = (root / "libs/vkd3d/command.c").read_text()
    match = re.search(r"^void d3d12_command_list_flush_dgc_batch\(", source, re.M)
    if not match:
        raise RuntimeError("Missing production DGC batch drain")
    _, end = body(source, match.start())
    replay = source[match.start():end]
    dynamic = source.index("static void d3d12_command_list_update_dynamic_state(")
    scissor = source.index("if (dyn_state->dirty_flags & VKD3D_DYNAMIC_STATE_SCISSOR)", dynamic)
    start, end = body(source, scissor)
    clamp = """
static void update_scissors(struct d3d12_command_list *list)
{
    const struct vkd3d_vk_device_procs *vk_procs = &list->device->vk_procs;
    struct vkd3d_dynamic_state *dyn_state = &list->dynamic_state;
    unsigned int i;
""" + source[start:end] + "\n}\n"
    harness = PREFIX + replay + clamp + SUFFIX
    if args.generate:
        args.generate.write_text(harness)
        return 0
    args.output_dir.mkdir(parents=True, exist_ok=True)
    binary = args.output_dir.resolve() / "dgc-replay-test"
    subprocess.run(shlex.split(args.cc) + ["-std=c11", "-Wall", "-Wextra", "-Werror",
            "-Wno-unused-parameter", "-Wno-unused-function",
            "-I", str(root / "khronos/Vulkan-Headers/include"), "-x", "c", "-", "-o", str(binary)],
            input=harness, text=True, check=True)
    return subprocess.run([str(binary)]).returncode


if __name__ == "__main__":
    raise SystemExit(main())
