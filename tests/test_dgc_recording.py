#!/usr/bin/env python3
# SPDX-License-Identifier: LGPL-2.1-or-later

"""Exercise production DGC capture eligibility for root-UBO descriptors.

The generated wrapper contains the actual capture block from command.c.
Only its surrounding work and allocation dependencies are mocked. GPU output
and ordering are tested separately in d3d12_dgc_*.c. Use --source with an older
command.c for a negative control, or --generate to let Meson compile the harness.
Standalone build artifacts are retained below ~/tmp, or in --build-dir.
"""

import argparse
from pathlib import Path
import os
import shlex
import subprocess
import tempfile


ROOT = Path(__file__).resolve().parents[1]
CASES = ("capture-success", "capture-eligibility")


def block_at(source, start):
    """Return an unchanged source block, including its condition."""
    opening = source.index("{", start)
    depth = 1
    end = opening + 1
    while depth:
        depth += (source[end] == "{") - (source[end] == "}")
        end += 1
    return source[start:end]


def generate(source_path):
    source = source_path.read_text()
    start = source.index("static void d3d12_command_list_execute_indirect_state_template_dgc(")
    source = block_at(source, start)
    capture = block_at(source, source.index("if (!list->predication.va && dgc_mode =="))
    return HARNESS.replace("@CAPTURE@", capture)


HARNESS = r"""
#include <assert.h>
#include <stdbool.h>
#include <stdint.h>
#include <stdio.h>
#include <string.h>

typedef uint64_t UINT64;
#define VKD3D_PIPELINE_TYPE_COMPUTE 1
#define VKD3D_PIPELINE_TYPE_GRAPHICS 2
#define VKD3D_PIPELINE_TYPE_MESH_GRAPHICS 3
#define VKD3D_ROOT_SIGNATURE_USE_PUSH_CONSTANT_UNIFORM_BLOCK 1u
enum vkd3d_dgc_mode { VKD3D_DGC_MODE_APPLICATION_CALL, VKD3D_DGC_MODE_EXECUTE_ONLY };
struct d3d12_pipeline_state { unsigned int pipeline_type; };
struct d3d12_resource { struct { uint64_t va; } res; };
struct d3d12_root_signature { unsigned int flags; };
struct mock_bindings
{
    struct d3d12_root_signature *root_signature;
    unsigned int value;
};
struct d3d12_command_signature
{
    struct { unsigned int ByteStride; } desc;
    struct { struct { unsigned int stride; uint64_t buffer_va; } dgc; } state_template;
};
struct vkd3d_dgc_batch_draw
{
    struct d3d12_pipeline_state *state;
    struct d3d12_command_signature *signature;
    struct d3d12_resource *arg_buffer, *count_buffer;
    UINT64 arg_buffer_offset, count_buffer_offset;
    uint32_t max_command_count;
    unsigned int dynamic_state, index_buffer;
    struct mock_bindings graphics_bindings;
    uint64_t preprocess_va, preprocess_size;
};
struct mock_device { struct { bool tiler_suspend_resume; } workarounds; };
struct d3d12_command_list
{
    struct { uint64_t va; } predication;
    struct mock_device *device;
    struct d3d12_pipeline_state *state;
    unsigned int dynamic_state, index_buffer;
    struct mock_bindings graphics_bindings;
    struct { struct vkd3d_dgc_batch_draw *draws; size_t draws_count, draws_size; } dgc_batch;
    bool invalid;
};

static unsigned int reserve_calls, clear_calls, immediate_calls;

static bool vkd3d_array_reserve(void **storage, size_t *capacity, size_t count, size_t size)
{
    (void)storage;
    assert(size == sizeof(struct vkd3d_dgc_batch_draw));
    reserve_calls++;
    *capacity = count;
    return true;
}
static void d3d12_command_list_mark_as_invalid(struct d3d12_command_list *list, const char *message, ...)
{
    (void)message;
    list->invalid = true;
}
static void d3d12_command_list_clear_signature_state(struct d3d12_command_list *list,
        struct d3d12_command_signature *signature)
{
    (void)signature;
    clear_calls++;
    list->graphics_bindings.value = 0;
}
static const struct d3d12_root_signature *d3d12_root_signature_get_layout(
        const struct d3d12_root_signature *root_signature, unsigned int pipeline_type)
{
    assert(pipeline_type != VKD3D_PIPELINE_TYPE_COMPUTE);
    return root_signature;
}

static void capture(struct d3d12_command_list *list, struct d3d12_command_signature *signature,
        uint32_t max_command_count, struct d3d12_resource *arg_buffer, UINT64 arg_buffer_offset,
        struct d3d12_resource *count_buffer, UINT64 count_buffer_offset,
        enum vkd3d_dgc_mode dgc_mode, bool explicit_preprocess)
{
    @CAPTURE@
    immediate_calls++;
}

int main(int argc, char **argv)
{
    struct vkd3d_dgc_batch_draw storage[4] = {{0}};
    struct d3d12_root_signature root_signature = {0};
    struct d3d12_pipeline_state state = {VKD3D_PIPELINE_TYPE_GRAPHICS};
    struct d3d12_command_signature signature = {{32}, {{32, 0x100000}}};
    struct d3d12_resource args = {{0x200000}}, count = {{0x300000}};
    struct mock_device device = {{false}};
    struct d3d12_command_list list = {0};
    unsigned int i;

    assert(argc == 2);
    list.device = &device;
    list.state = &state;
    list.dynamic_state = 19;
    list.index_buffer = 23;
    list.graphics_bindings.root_signature = &root_signature;
    list.graphics_bindings.value = 29;
    list.dgc_batch.draws = storage;

    if (!strcmp(argv[1], "capture-success"))
    {
        capture(&list, &signature, 3, &args, 4, &count, 8, VKD3D_DGC_MODE_APPLICATION_CALL, false);
        assert(reserve_calls == 1 && clear_calls == 1 && !immediate_calls && !list.invalid);
        assert(list.dgc_batch.draws_count == 1);
        assert(storage[0].state == &state && storage[0].signature == &signature);
        assert(storage[0].arg_buffer == &args && storage[0].count_buffer == &count);
        assert(storage[0].arg_buffer_offset == 4 && storage[0].count_buffer_offset == 8);
        assert(storage[0].max_command_count == 3 && storage[0].dynamic_state == 19);
        assert(storage[0].index_buffer == 23 && storage[0].graphics_bindings.value == 29);
        assert(!storage[0].preprocess_va && !storage[0].preprocess_size);
        assert(!list.graphics_bindings.value);
    }
    else if (!strcmp(argv[1], "capture-eligibility"))
    {
        for (i = 0; i < 10; i++)
        {
            list.dgc_batch.draws_count = 0;
            list.graphics_bindings.root_signature = i == 4 ? NULL : &root_signature;
            root_signature.flags = i == 1 ? VKD3D_ROOT_SIGNATURE_USE_PUSH_CONSTANT_UNIFORM_BLOCK : i == 2 ? 2 : 0;
            state.pipeline_type = i == 3 ? VKD3D_PIPELINE_TYPE_COMPUTE :
                    i == 9 ? VKD3D_PIPELINE_TYPE_MESH_GRAPHICS : VKD3D_PIPELINE_TYPE_GRAPHICS;
            device.workarounds.tiler_suspend_resume = i == 5;
            list.predication.va = i == 6 ? 1 : 0;
            reserve_calls = clear_calls = immediate_calls = 0;
            capture(&list, &signature, 3, &args, 4, &count, 8,
                    i == 7 ? VKD3D_DGC_MODE_EXECUTE_ONLY : VKD3D_DGC_MODE_APPLICATION_CALL, i == 8);
            if (i == 0 || i == 2 || i == 9)
                assert(list.dgc_batch.draws_count == 1 && reserve_calls == 1 && !immediate_calls);
            else
                assert(!list.dgc_batch.draws_count && !reserve_calls && !clear_calls && immediate_calls == 1);
        }
    }
    else
        return 2;

    puts(argv[1]);
    return 0;
}
"""


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, default=ROOT / "libs/vkd3d/command.c")
    parser.add_argument("--generate", type=Path)
    parser.add_argument("--build-dir", type=Path)
    parser.add_argument("--case", choices=CASES)
    args = parser.parse_args()
    harness = generate(args.source)
    if args.generate:
        args.generate.write_text(harness)
    else:
        if args.build_dir:
            build = args.build_dir
            build.mkdir(parents=True, exist_ok=True)
        else:
            base = Path.home() / "tmp"
            base.mkdir(parents=True, exist_ok=True)
            build = Path(tempfile.mkdtemp(prefix="dgc-recording-", dir=base))
        source = build / "dgc_recording.c"
        binary = build / "dgc_recording.exe"
        source.write_text(harness)
        command = shlex.split(os.environ.get("CC", "cc"))
        command += ["-std=c11", "-O1", "-g", "-UNDEBUG", "-Wall", "-Wextra", "-Werror",
                    "-Wno-unused-variable", "-Wno-unused-function", "-Wno-unused-but-set-variable"]
        if os.environ.get("DGC_TEST_SANITIZE") == "1":
            command += ["-fsanitize=address,undefined", "-fno-omit-frame-pointer", "-no-pie"]
        subprocess.run(command + [str(source), "-o", str(binary)], check=True)
        runner = shlex.split(os.environ.get("DGC_TEST_RUNNER", ""))
        for case in (args.case,) if args.case else CASES:
            subprocess.run(runner + [str(binary), case], check=True, timeout=10)
        print("Retained harness:", build)
