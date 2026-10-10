#!/usr/bin/env python3
# Copyright 2026 Erhan Bilgili
# SPDX-License-Identifier: LGPL-2.1-or-later
"""Check temporary DGC predication cleanup using production failure branches.

The fallback-predication block, failure exits and final restoration block come
directly from command.c. Surrounding GPU work is mocked. Run --source against
an earlier command.c as a negative control. Standalone artifacts stay in ~/tmp.
"""

import argparse
import os
from pathlib import Path
import re
import shlex
import subprocess
import tempfile


ROOT = Path(__file__).resolve().parents[1]


def block(source, start):
    opening = source.index("{", start)
    depth, end = 1, opening + 1
    while depth:
        depth += (source[end] == "{") - (source[end] == "}")
        end += 1
    return source[start:end]


def statement(source, condition):
    start = source.index(condition)
    end = source.index(";", start) + 1
    opening = source.find("{", start, end)
    return block(source, start) if opening >= 0 else source[start:end]


def generate(path):
    source = path.read_text()
    start = re.search(r"static (void|HRESULT) d3d12_command_list_execute_indirect_state_template_dgc\(", source)
    returns_status = start.group(1) == "HRESULT"
    source = block(source, start.start())
    predicate = block(source, source.index("if (list->predication.va)"))
    cleanup = block(source, source.rindex("if (restart_predication)"))
    conditions = (
        "if (!d3d12_command_list_update_compute_pipeline(list))",
        "if (!d3d12_command_list_update_graphics_pipeline(list, signature->pipeline_type))",
        "if (FAILED(hr = d3d12_command_signature_allocate_stream_memory_for_list(",
        "if (!d3d12_command_allocator_allocate_scratch_memory(list->allocator,",
        "if (!d3d12_command_list_begin_render_pass(list, signature->pipeline_type))",
        "if (signature->pipeline_type == VKD3D_PIPELINE_TYPE_COMPUTE &&\n            !d3d12_command_list_update_compute_state(list))",
        "if (!require_ibo_update &&",
        "if (!preprocess_va)",
    )
    cases = [f"case {i + 1}:\n{statement(source, condition)}\nbreak;" for i, condition in enumerate(conditions)]
    return (HARNESS.replace("@PREDICATE@", predicate).replace("@CLEANUP@", cleanup)
            .replace("@CASES@", "\n".join(cases))
            .replace("@DGC_RETURN_TYPE@", start.group(1))
            .replace("@DGC_RESULT@", "HRESULT result = S_FALSE;" if returns_status else "")
            .replace("@DGC_SUCCESS@", "result = S_OK;" if returns_status else "")
            .replace("@DGC_RETURN@", "return result;" if returns_status else "return;")
            .replace("@CHECK_RESULT@", "assert((call) == (expected))" if returns_status else "(call)"))


HARNESS = r"""
#include <assert.h>
#include <stdbool.h>
#include <stdint.h>
#include <stdio.h>
#include <string.h>

typedef uint64_t VkDeviceAddress;
typedef int32_t HRESULT;
#define S_OK 0
#define S_FALSE 1
#define E_OUTOFMEMORY (-1)
#define CHECK_RESULT(call, expected) @CHECK_RESULT@
#define FAILED(hr) ((HRESULT)(hr) < 0)
#define WARN(...) ((void)0)
#define VKD3D_PIPELINE_TYPE_COMPUTE 1
#define VKD3D_SCRATCH_POOL_KIND_DEVICE_STORAGE 1
#define D3D12_INDIRECT_ARGUMENT_TYPE_DRAW_INDEXED 1
enum vkd3d_predicate_command_type
{
    VKD3D_PREDICATE_COMMAND_DRAW_INDIRECT,
    VKD3D_PREDICATE_COMMAND_DRAW_INDIRECT_COUNT,
};
union vkd3d_predicate_command_direct_args { unsigned int draw_count; };
struct vkd3d_scratch_allocation { uint64_t va; };
struct d3d12_resource { struct { uint64_t va; } res; };
struct d3d12_command_signature
{
    unsigned int pipeline_type;
    struct { unsigned int NumArgumentDescs; struct { unsigned int Type; } pArgumentDescs[1]; } desc;
};
struct mock_device { struct { bool tiler_suspend_resume; } workarounds; };
struct d3d12_command_list
{
    struct mock_device *device;
    void *allocator;
    struct { uint64_t va; bool enabled_on_command_buffer; } predication;
    struct { struct { bool block_resume; } suspend_resume; } cmd;
    bool invalid;
};
static bool operation_result, predicate_result;
static unsigned int predicate_calls, suspended, resumed, executed, operation_calls;

static void d3d12_command_list_update_conditional_rendering_state(struct d3d12_command_list *list, bool suspend)
{
    assert(list->predication.enabled_on_command_buffer);
    if (suspend) suspended++;
    else resumed++;
}
static bool d3d12_command_list_emit_predicated_command(struct d3d12_command_list *list,
        enum vkd3d_predicate_command_type type, VkDeviceAddress count_va,
        const union vkd3d_predicate_command_direct_args *args, struct vkd3d_scratch_allocation *allocation)
{
    assert(!list->predication.enabled_on_command_buffer);
    if (type == VKD3D_PREDICATE_COMMAND_DRAW_INDIRECT_COUNT)
        assert(count_va == 0x2008);
    else
        assert(!count_va && args->draw_count == 3);
    predicate_calls++;
    if (predicate_result)
        allocation->va = 0x3000;
    return predicate_result;
}
static void d3d12_command_list_mark_as_invalid(struct d3d12_command_list *list, const char *message, ...)
{
    (void)message;
    list->invalid = true;
}
static bool d3d12_command_list_update_compute_pipeline(struct d3d12_command_list *list)
{ (void)list; operation_calls++; return operation_result; }
static bool d3d12_command_list_update_graphics_pipeline(struct d3d12_command_list *list, unsigned int type)
{ (void)type; return d3d12_command_list_update_compute_pipeline(list); }
static bool d3d12_command_list_begin_render_pass(struct d3d12_command_list *list, unsigned int type)
{ return d3d12_command_list_update_graphics_pipeline(list, type); }
static bool d3d12_command_list_update_compute_state(struct d3d12_command_list *list)
{ return d3d12_command_list_update_compute_pipeline(list); }
static bool d3d12_command_list_update_index_buffer(struct d3d12_command_list *list)
{ return d3d12_command_list_update_compute_pipeline(list); }
static HRESULT d3d12_command_signature_allocate_stream_memory_for_list(struct d3d12_command_list *list,
        struct d3d12_command_signature *signature, uint32_t count, struct vkd3d_scratch_allocation *allocation)
{
    (void)list; (void)signature; (void)allocation;
    assert(count == 3);
    operation_calls++;
    return operation_result ? 0 : -1;
}
static bool d3d12_command_allocator_allocate_scratch_memory(void *allocator, unsigned int pool,
        size_t size, size_t alignment, unsigned int memory_types, struct vkd3d_scratch_allocation *allocation)
{
    (void)allocator; (void)pool; (void)allocation;
    assert(size == 4 && alignment == 4 && memory_types == ~0u);
    operation_calls++;
    return operation_result;
}
static HRESULT d3d12_command_signature_allocate_preprocess_memory_for_list(struct d3d12_command_list *list,
        struct d3d12_command_signature *signature, uint64_t pipeline, bool explicit_preprocess,
        uint32_t count, struct vkd3d_scratch_allocation *allocation, uint64_t *size)
{
    (void)list; (void)signature; (void)allocation; (void)size;
    assert(pipeline == 7 && !explicit_preprocess && count == 3);
    operation_calls++;
    return operation_result ? 0 : -1;
}

static @DGC_RETURN_TYPE@ record(struct d3d12_command_list *list, struct d3d12_resource *count_buffer, unsigned int exit_point)
{
    struct d3d12_command_signature storage = {VKD3D_PIPELINE_TYPE_COMPUTE,
            {1, {{D3D12_INDIRECT_ARGUMENT_TYPE_DRAW_INDEXED}}}};
    struct d3d12_command_signature *signature = &storage;
    struct vkd3d_scratch_allocation predication_allocation = {0xdeadbeef};
    struct vkd3d_scratch_allocation stream_allocation, count_allocation, preprocess_allocation;
    uint64_t count_buffer_offset = 8, preprocess_va = 0, preprocess_size = 0, current_pipeline = 7;
    uint32_t max_command_count = 3;
    bool require_custom_predication = false, restart_predication = false;
    bool old_predication_enabled_on_command_buffer = false;
    bool require_ibo_update = false, explicit_preprocess = false;
    HRESULT hr;
    @DGC_RESULT@

    @PREDICATE@
    switch (exit_point)
    {
        case 0: break;
        @CASES@
        default: assert(0);
    }
    if (require_custom_predication)
        assert(predication_allocation.va == 0x3000);
    executed++;
    @DGC_SUCCESS@
restore_predication:
    @CLEANUP@
    @DGC_RETURN@
}

int main(int argc, char **argv)
{
    struct mock_device device = {{true}};
    struct d3d12_resource count = {{0x2000}};
    struct d3d12_command_list list;
    unsigned int exit_point, enabled, have_count, failure, tests = 0;
    bool exits_only = argc > 1 && !strcmp(argv[1], "exits");

    for (exit_point = 0; exit_point <= 8; exit_point++)
    for (enabled = 0; enabled < 2; enabled++)
    for (have_count = 0; have_count < 2; have_count++)
    for (failure = 0; failure < 3; failure++)
    {
        if (exits_only && failure == 1)
            continue;
        memset(&list, 0, sizeof(list));
        list.device = &device;
        list.predication.va = 0x1000;
        list.predication.enabled_on_command_buffer = enabled;
        operation_result = failure != 2;
        predicate_result = failure != 1;
        predicate_calls = suspended = resumed = executed = operation_calls = 0;
        CHECK_RESULT(record(&list, have_count ? &count : NULL, exit_point),
                failure == 1 ? E_OUTOFMEMORY : failure != 2 || exit_point == 0 ? S_OK :
                exit_point == 3 || exit_point == 4 || exit_point == 8 ? E_OUTOFMEMORY : S_FALSE);
        assert(predicate_calls == 1 && suspended == enabled && resumed == enabled);
        assert(list.predication.enabled_on_command_buffer == enabled);
        assert(executed == (failure == 0 || (failure == 2 && exit_point == 0)));
        assert(list.invalid == (failure == 1 || (failure == 2 &&
                (exit_point == 3 || exit_point == 4 || exit_point == 8))));
        assert(operation_calls == (failure != 1 && exit_point != 0));
        tests++;
    }

    /* No predicate or no tiler fallback must not touch conditional rendering. */
    for (enabled = 0; enabled < 2; enabled++)
    for (exit_point = 0; exit_point < 3; exit_point++)
    {
        memset(&list, 0, sizeof(list));
        list.device = &device;
        list.predication.va = exit_point == 0 ? 0 : 0x1000;
        list.predication.enabled_on_command_buffer = enabled;
        device.workarounds.tiler_suspend_resume = exit_point != 1;
        list.cmd.suspend_resume.block_resume = exit_point == 2;
        operation_result = predicate_result = true;
        predicate_calls = suspended = resumed = executed = 0;
        CHECK_RESULT(record(&list, NULL, 0), S_OK);
        assert(!predicate_calls && !suspended && !resumed && executed == 1 && !list.invalid);
        assert(list.predication.enabled_on_command_buffer == enabled);
        tests++;
    }
    printf("DGC predication tests passed (%u cases).\n", tests);
    return 0;
}
"""


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, default=ROOT / "libs/vkd3d/command.c")
    parser.add_argument("--generate", type=Path)
    parser.add_argument("--build-dir", type=Path)
    parser.add_argument("--exits-only", action="store_true")
    args = parser.parse_args()
    harness = generate(args.source)
    if args.generate:
        args.generate.write_text(harness)
    else:
        base = Path.home() / "tmp"
        base.mkdir(parents=True, exist_ok=True)
        build = args.build_dir or Path(tempfile.mkdtemp(prefix="dgc-predication-", dir=base))
        build.mkdir(parents=True, exist_ok=True)
        source, binary = build / "dgc_predication.c", build / "dgc_predication"
        source.write_text(harness)
        command = shlex.split(os.environ.get("CC", "cc"))
        command += ["-std=c11", "-O1", "-g", "-UNDEBUG", "-Wall", "-Wextra", "-Werror",
                    "-Wno-unused-variable", "-Wno-unused-label"]
        if os.environ.get("DGC_TEST_SANITIZE") == "1":
            command += ["-fsanitize=address,undefined", "-fno-omit-frame-pointer", "-no-pie"]
        subprocess.run(command + [str(source), "-o", str(binary)], check=True)
        subprocess.run([str(binary)] + (["exits"] if args.exits_only else []), check=True, timeout=10)
        print("Retained harness:", build)
