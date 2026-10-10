#!/usr/bin/env python3
# Copyright 2026 Erhan Bilgili
# SPDX-License-Identifier: LGPL-2.1-or-later

"""Exercise production DGC recording blocks with allocation failures injected.

The generated wrappers contain the actual capture and scratch-allocation blocks
from command.c. Only their surrounding Vulkan work and allocation dependencies
are mocked. GPU output and ordering are tested separately in d3d12_dgc_*.c.
Use --source with an older command.c for a negative control, or --generate to
let Meson compile the harness. Standalone build artifacts are retained below
~/tmp, or in --build-dir when supplied.
"""

import argparse
from pathlib import Path
import os
import re
import shlex
import subprocess
import tempfile


ROOT = Path(__file__).resolve().parents[1]
CASES = ("capture-success", "capture-failure", "capture-eligibility",
         "count-success", "count-failure", "count-absent", "stream-failure",
         "preprocess-success", "preprocess-failure", "preprocess-existing",
         "root-ubo-success", "root-ubo-failure")


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
    root_start = re.search(r"static (void|bool) d3d12_command_list_update_root_descriptors\(", source)
    root_descriptors = block_at(source, root_start.start())
    root_ubo = block_at(root_descriptors, root_descriptors.index("if (root_signature_flags &"))
    start = re.search(r"static (void|HRESULT) d3d12_command_list_execute_indirect_state_template_dgc\(", source)
    returns_status = start.group(1) == "HRESULT"
    source = block_at(source, start.start())
    capture = block_at(source, source.index("if (!list->predication.va && dgc_mode =="))
    stream = source.index("if (FAILED(hr = d3d12_command_signature_allocate_stream_memory_for_list(")
    count_start = source.index("if (count_buffer)", stream)
    count_end = source.index("if (patch_args.debug_tag != 0)", count_start)
    preprocess = block_at(source, source.index("if (!preprocess_va)"))
    address = source.index("generated_ext.preprocessAddress =")
    address_end = source.index("generated_ext.maxSequenceCount =", address)
    return (HARNESS.replace("@CAPTURE@", capture)
            .replace("@COUNT@", source[stream:count_end])
            .replace("@PREPROCESS@", preprocess + "\n" + source[address:address_end])
            .replace("@ROOT_UBO@", root_ubo)
            .replace("@DGC_RETURN_TYPE@", start.group(1))
            .replace("@DGC_RESULT@", "HRESULT result = S_FALSE;" if returns_status else "")
            .replace("@DGC_SUCCESS@", "result = S_OK;" if returns_status else "")
            .replace("@DGC_RETURN@", "return result;" if returns_status else "return;")
            .replace("@DGC_SUCCESS_RETURN@", "return S_OK;" if returns_status else "return;")
            .replace("@ROOT_RETURN_TYPE@", root_start.group(1))
            .replace("@ROOT_SUCCESS_RETURN@", "return true;" if root_start.group(1) == "bool" else "return;"))


HARNESS = r"""
#include <assert.h>
#include <stdbool.h>
#include <stdint.h>
#include <stdio.h>
#include <string.h>

typedef uint64_t UINT64;
typedef int32_t HRESULT;
#define S_OK 0
#define S_FALSE 1
#define E_OUTOFMEMORY (-1)
#define FAILED(hr) ((HRESULT)(hr) < 0)
#define WARN(...) ((void)0)
#define VKD3D_PIPELINE_TYPE_COMPUTE 1
#define VKD3D_PIPELINE_TYPE_GRAPHICS 2
#define VKD3D_PIPELINE_TYPE_MESH_GRAPHICS 3
#define VKD3D_ROOT_SIGNATURE_USE_PUSH_CONSTANT_UNIFORM_BLOCK 1u
#define VKD3D_SCRATCH_POOL_KIND_DEVICE_STORAGE 1
#define VKD3D_SCRATCH_POOL_KIND_UNIFORM_UPLOAD 2
#define D3D12_CONSTANT_BUFFER_DATA_PLACEMENT_ALIGNMENT 256
enum vkd3d_dgc_mode { VKD3D_DGC_MODE_APPLICATION_CALL, VKD3D_DGC_MODE_EXECUTE_ONLY };
struct d3d12_pipeline_state { unsigned int pipeline_type; };
struct d3d12_resource { struct { uint64_t va; } res; };
struct d3d12_root_signature { unsigned int flags; uint64_t root_descriptor_raw_va_mask; };
struct mock_bindings
{
    struct d3d12_root_signature *root_signature;
    unsigned int value;
    uint64_t root_descriptor_dirty_mask;
};
union vkd3d_root_parameter_data { uint32_t words[64]; };
struct d3d12_command_signature
{
    struct { unsigned int ByteStride; } desc;
    struct { struct { unsigned int stride; uint64_t buffer_va; } dgc; } state_template;
};
struct vkd3d_scratch_allocation { uint64_t va; void *host_ptr; };
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
    void *allocator;
    bool invalid;
};

static bool reserve_result, scratch_result, stream_result, preprocess_result;
static unsigned int reserve_calls, scratch_calls, clear_calls, immediate_calls, patch_calls;
static unsigned int stream_calls, preprocess_calls, execute_calls;
static uint64_t patched_count_address, execute_address, execute_size;
static union vkd3d_root_parameter_data root_upload, root_failure_canary;
static unsigned int root_upload_calls;

static bool vkd3d_array_reserve(void **storage, size_t *capacity, size_t count, size_t size)
{
    (void)storage;
    assert(size == sizeof(struct vkd3d_dgc_batch_draw));
    reserve_calls++;
    if (reserve_result)
        *capacity = count;
    return reserve_result;
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
static bool d3d12_command_allocator_allocate_scratch_memory(void *allocator, unsigned int kind,
        size_t size, size_t alignment, unsigned int memory_types, struct vkd3d_scratch_allocation *allocation)
{
    (void)allocator;
    assert(memory_types == ~0u);
    if (kind == VKD3D_SCRATCH_POOL_KIND_DEVICE_STORAGE)
        assert(size == sizeof(uint32_t) && alignment == sizeof(uint32_t));
    else
    {
        assert(kind == VKD3D_SCRATCH_POOL_KIND_UNIFORM_UPLOAD);
        assert(size == sizeof(root_upload) && alignment == D3D12_CONSTANT_BUFFER_DATA_PLACEMENT_ALIGNMENT);
    }
    scratch_calls++;
    if (scratch_result)
    {
        allocation->va = 0x123456780;
        allocation->host_ptr = &root_upload;
    }
    return scratch_result;
}
static HRESULT d3d12_command_signature_allocate_stream_memory_for_list(
        struct d3d12_command_list *list, struct d3d12_command_signature *signature,
        uint32_t max_command_count, struct vkd3d_scratch_allocation *allocation)
{
    (void)list;
    (void)signature;
    assert(max_command_count == 3);
    stream_calls++;
    if (!stream_result)
        return -1;
    allocation->va = 0x400000;
    return 0;
}
static HRESULT d3d12_command_signature_allocate_preprocess_memory_for_list(
        struct d3d12_command_list *list, struct d3d12_command_signature *signature,
        uint64_t pipeline, bool explicit_preprocess, uint32_t max_command_count,
        struct vkd3d_scratch_allocation *allocation, uint64_t *size)
{
    (void)list;
    (void)signature;
    assert(pipeline == 7 && !explicit_preprocess && max_command_count == 3);
    preprocess_calls++;
    if (!preprocess_result)
        return -1;
    allocation->va = 0x500000;
    *size = 256;
    return 0;
}

static @DGC_RETURN_TYPE@ capture(struct d3d12_command_list *list, struct d3d12_command_signature *signature,
        uint32_t max_command_count, struct d3d12_resource *arg_buffer, UINT64 arg_buffer_offset,
        struct d3d12_resource *count_buffer, UINT64 count_buffer_offset,
        enum vkd3d_dgc_mode dgc_mode, bool explicit_preprocess)
{
    @CAPTURE@
    immediate_calls++;
    @DGC_SUCCESS_RETURN@
}

static @DGC_RETURN_TYPE@ patch_count(struct d3d12_command_list *list, struct d3d12_command_signature *signature,
        struct d3d12_resource *arg_buffer, UINT64 arg_buffer_offset,
        struct d3d12_resource *count_buffer, UINT64 count_buffer_offset)
{
    struct vkd3d_scratch_allocation count_allocation = {.va = 0xdeadbeef};
    struct vkd3d_scratch_allocation stream_allocation = {.va = 0xdeadbeef};
    uint32_t max_command_count = 3;
    struct
    {
        uint64_t template_va, api_buffer_va, device_generated_commands_va;
        uint64_t indirect_count_va, dst_indirect_count_va;
        unsigned int api_buffer_word_stride, device_generated_commands_word_stride;
    } patch_args = {0};
    HRESULT hr;
    @DGC_RESULT@
    @COUNT@
    patch_calls++;
    patched_count_address = patch_args.dst_indirect_count_va;
    @DGC_SUCCESS@
    @DGC_RETURN@
}

static @DGC_RETURN_TYPE@ prepare_execute(struct d3d12_command_list *list, struct d3d12_command_signature *signature,
        uint64_t preprocess_va, uint64_t preprocess_size)
{
    struct vkd3d_scratch_allocation preprocess_allocation = {.va = 0xdeadbeef};
    struct { uint64_t preprocessAddress, preprocessSize; } generated_ext;
    uint64_t current_pipeline = 7;
    bool explicit_preprocess = false;
    uint32_t max_command_count = 3;
    HRESULT hr;
    @DGC_RESULT@
    @PREPROCESS@
    execute_calls++;
    execute_address = generated_ext.preprocessAddress;
    execute_size = generated_ext.preprocessSize;
    @DGC_SUCCESS@
    @DGC_RETURN@
}

static @ROOT_RETURN_TYPE@ upload_root_ubo(struct d3d12_command_list *list)
{
    struct mock_bindings *bindings = &list->graphics_bindings;
    const struct d3d12_root_signature *root_signature = bindings->root_signature;
    uint32_t root_signature_flags = VKD3D_ROOT_SIGNATURE_USE_PUSH_CONSTANT_UNIFORM_BLOCK;
    struct vkd3d_scratch_allocation alloc = {.host_ptr = &root_failure_canary};
    union vkd3d_root_parameter_data root_parameter_data, *ptr_root_parameter_data = NULL;
    @ROOT_UBO@
    root_upload_calls++;
    ptr_root_parameter_data->words[0] = 0x12345678;
    @ROOT_SUCCESS_RETURN@
}

int main(int argc, char **argv)
{
    struct vkd3d_dgc_batch_draw storage[4] = {{0}}, before[4];
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
    reserve_result = scratch_result = stream_result = preprocess_result = true;

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
    else if (!strcmp(argv[1], "capture-failure"))
    {
        /* Leave a real allocation behind so the negative control fails an
         * assertion rather than depending on a null-pointer crash. */
        list.dgc_batch.draws_count = list.dgc_batch.draws_size = 1;
        memset(storage, 0x5a, sizeof(storage));
        memcpy(before, storage, sizeof(before));
        reserve_result = false;
        capture(&list, &signature, 3, &args, 4, &count, 8, VKD3D_DGC_MODE_APPLICATION_CALL, false);
        assert(reserve_calls == 1 && list.invalid && !clear_calls && !immediate_calls);
        assert(list.dgc_batch.draws_count == 1 && list.dgc_batch.draws_size == 1);
        assert(!memcmp(storage, before, sizeof(storage)) && list.graphics_bindings.value == 29);
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
    else if (!strcmp(argv[1], "count-success"))
    {
        patch_count(&list, &signature, &args, 4, &count, 8);
        assert(scratch_calls == 1 && patch_calls == 1 && !list.invalid);
        assert(patched_count_address == 0x123456780);
    }
    else if (!strcmp(argv[1], "count-failure"))
    {
        scratch_result = false;
        patch_count(&list, &signature, &args, 4, &count, 8);
        assert(scratch_calls == 1 && !patch_calls && list.invalid);
    }
    else if (!strcmp(argv[1], "count-absent"))
    {
        scratch_result = false;
        patch_count(&list, &signature, &args, 4, NULL, 0);
        assert(!scratch_calls && patch_calls == 1 && !patched_count_address && !list.invalid);
    }
    else if (!strcmp(argv[1], "stream-failure"))
    {
        stream_result = false;
        patch_count(&list, &signature, &args, 4, &count, 8);
        assert(stream_calls == 1 && !scratch_calls && !patch_calls && list.invalid);
    }
    else if (!strcmp(argv[1], "preprocess-success"))
    {
        prepare_execute(&list, &signature, 0, 0);
        assert(preprocess_calls == 1 && execute_calls == 1 && !list.invalid);
        assert(execute_address == 0x500000 && execute_size == 256);
    }
    else if (!strcmp(argv[1], "preprocess-failure"))
    {
        preprocess_result = false;
        prepare_execute(&list, &signature, 0, 0);
        assert(preprocess_calls == 1 && !execute_calls && list.invalid);
    }
    else if (!strcmp(argv[1], "preprocess-existing"))
    {
        preprocess_result = false;
        prepare_execute(&list, &signature, 0x600000, 512);
        assert(!preprocess_calls && execute_calls == 1 && !list.invalid);
        assert(execute_address == 0x600000 && execute_size == 512);
    }
    else if (!strcmp(argv[1], "root-ubo-success"))
    {
        root_signature.root_descriptor_raw_va_mask = 1;
        list.graphics_bindings.root_descriptor_dirty_mask = 4;
        upload_root_ubo(&list);
        assert(scratch_calls == 1 && root_upload_calls == 1 && !list.invalid);
        assert(list.graphics_bindings.root_descriptor_dirty_mask == 5);
        assert(root_upload.words[0] == 0x12345678 && !root_failure_canary.words[0]);
    }
    else if (!strcmp(argv[1], "root-ubo-failure"))
    {
        root_signature.root_descriptor_raw_va_mask = 1;
        list.graphics_bindings.root_descriptor_dirty_mask = 4;
        scratch_result = false;
        upload_root_ubo(&list);
        assert(scratch_calls == 1 && !root_upload_calls && list.invalid);
        assert(list.graphics_bindings.root_descriptor_dirty_mask == 4);
        assert(!root_upload.words[0] && !root_failure_canary.words[0]);
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
