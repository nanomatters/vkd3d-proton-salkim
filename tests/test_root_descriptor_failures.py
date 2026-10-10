#!/usr/bin/env python3
# SPDX-License-Identifier: LGPL-2.1-or-later
# Copyright 2026 Erhan Bilgili

"""Check root-upload failures through production descriptor-update callers.

The descriptor-update function and the root allocation block are extracted
unchanged. Graphics, compute, raygen and DGC preprocessing use their production
setup prefixes, stopping before the remaining Vulkan work. A counter stands in
for that work so a missed failure check is observable without a Vulkan device.
"""

import argparse
import os
from pathlib import Path
import re
import shlex
import subprocess
import sys
import tempfile

sys.dont_write_bytecode = True
from test_dgc_recording import block_at


ROOT = Path(__file__).resolve().parents[1]


def function(source, name):
    start = re.search(r"static (?:void|bool|HRESULT) " + name + r"\(", source).start()
    return block_at(source, start)


def generate(path):
    source = path.read_text()
    root = function(source, "d3d12_command_list_update_root_descriptors")
    allocation = block_at(root, root.index("if (root_signature_flags &"))
    descriptors = function(source, "d3d12_command_list_update_descriptors")
    # Older revisions return void. Adapt only the wrapper return type so they
    # compile and fail the behavioural assertions instead of the compiler.
    allocation = allocation.replace("return;", "return false;")
    if descriptors.startswith("static void"):
        descriptors = descriptors.replace("static void", "static bool", 1).replace("return;", "return true;")
        descriptors = descriptors[:-1] + "    return true;\n}"
    compute = function(source, "d3d12_command_list_update_compute_state")
    raygen = function(source, "d3d12_command_list_update_raygen_state")
    raygen = raygen[:raygen.index("    /* If we have a static sampler set")] + "    return true;\n}"
    graphics = function(source, "d3d12_command_list_begin_render_pass")
    graphics = graphics[:graphics.index("    if (list->rendering_info.state_flags &")] + "    return true;\n}"
    dgc = function(source, "d3d12_command_list_execute_indirect_state_template_dgc")
    returns_status = dgc.startswith("static HRESULT")
    preprocess = dgc.index("        /* Do the minimal flushing necessary to make state consistent. */")
    preprocess = block_at(dgc, dgc.rfind("else", 0, preprocess)).removeprefix("else")
    return (HARNESS.replace("@ROOT_ALLOCATION@", allocation)
            .replace("@DESCRIPTORS@", descriptors)
            .replace("@COMPUTE@", compute).replace("@RAYGEN@", raygen)
            .replace("@GRAPHICS@", graphics).replace("@PREPROCESS@", preprocess)
            .replace("@DGC_RETURN_TYPE@", "HRESULT" if returns_status else "void")
            .replace("@DGC_RESULT@", "HRESULT result = S_FALSE;" if returns_status else "")
            .replace("@DGC_SUCCESS@", "result = S_OK;" if returns_status else "")
            .replace("@DGC_RETURN@", "return result;" if returns_status else "return;")
            .replace("@PREPROCESS_CLEANUP@", "restore_predication:" if "goto restore_predication;" in preprocess else ""))


HARNESS = r"""
#include <assert.h>
#include <stdbool.h>
#include <stdint.h>
#include <stdio.h>
#include <string.h>

typedef unsigned int VkPipelineBindPoint, VkShaderStageFlags, VkPipelineLayout;
typedef int32_t HRESULT;
#define S_OK 0
#define S_FALSE 1
#define E_OUTOFMEMORY (-1)
enum vkd3d_pipeline_type { VKD3D_PIPELINE_TYPE_NONE, VKD3D_PIPELINE_TYPE_GRAPHICS,
    VKD3D_PIPELINE_TYPE_COMPUTE, VKD3D_PIPELINE_TYPE_RAY_TRACING };
#define VKD3D_ROOT_SIGNATURE_USE_PUSH_CONSTANT_UNIFORM_BLOCK 1u
#define VKD3D_PIPELINE_DIRTY_STATIC_SAMPLER_SET 1u
#define VKD3D_PIPELINE_DIRTY_HOISTED_DESCRIPTORS 2u
#define VKD3D_PIPELINE_DIRTY_DESCRIPTOR_TABLE_OFFSETS 4u
#define VKD3D_PIPELINE_DIRTY_INLINE_REDZONE 8u
#define VKD3D_SCRATCH_POOL_KIND_UNIFORM_UPLOAD 1u
#define VKD3D_RENDERING_ACTIVE 1u
#define VK_PIPELINE_STAGE_2_COMPUTE_SHADER_BIT 1u
#define VK_PIPELINE_STAGE_2_RAY_TRACING_SHADER_BIT_KHR 2u
#define D3D12_CONSTANT_BUFFER_DATA_PLACEMENT_ALIGNMENT 256u

struct d3d12_bind_point_layout { unsigned int flags, vk_pipeline_layout, vk_push_stages; };
struct d3d12_root_signature
{
    struct d3d12_bind_point_layout layout;
    uint64_t root_descriptor_raw_va_mask;
};
struct vkd3d_pipeline_bindings
{
    struct d3d12_root_signature *root_signature;
    uint64_t root_descriptor_dirty_mask, root_constant_dirty_mask;
    unsigned int dirty_flags;
};
struct vkd3d_vk_device_procs { unsigned int unused; };
struct mock_device { struct vkd3d_vk_device_procs vk_procs; };
struct d3d12_command_list
{
    struct mock_device *device;
    struct vkd3d_pipeline_bindings bindings;
    enum vkd3d_pipeline_type active_pipeline_type;
    struct { bool pending_rtas_work; } rtas_batch;
    struct { unsigned int state_flags; } rendering_info;
    struct { unsigned int dirty_flags; } dynamic_state;
    void *allocator;
    bool invalid;
};
struct d3d12_command_signature { enum vkd3d_pipeline_type pipeline_type; };
union vkd3d_root_parameter_data { uint32_t words[64]; };
struct vkd3d_scratch_allocation { void *host_ptr; };
static union vkd3d_root_parameter_data upload;
static bool allocation_success;
static unsigned int allocation_calls, upload_calls, issued_commands;

static bool d3d12_command_allocator_allocate_scratch_memory(void *allocator, unsigned int pool,
        size_t size, size_t align, unsigned int mask, struct vkd3d_scratch_allocation *allocation)
{
    (void)allocator;
    assert(pool == VKD3D_SCRATCH_POOL_KIND_UNIFORM_UPLOAD);
    assert(size == sizeof(upload) && align == 256 && mask == ~0u);
    allocation_calls++;
    if (allocation_success)
        allocation->host_ptr = &upload;
    return allocation_success;
}
static void d3d12_command_list_mark_as_invalid(struct d3d12_command_list *list, const char *message)
{
    (void)message;
    list->invalid = true;
}
static bool d3d12_command_list_update_root_descriptors(struct d3d12_command_list *list,
        struct vkd3d_pipeline_bindings *bindings, VkPipelineBindPoint vk_bind_point,
        VkPipelineLayout layout, VkShaderStageFlags push_stages, uint32_t root_signature_flags)
{
    const struct d3d12_root_signature *root_signature = bindings->root_signature;
    union vkd3d_root_parameter_data root_parameter_data, *ptr_root_parameter_data;
    struct vkd3d_scratch_allocation alloc;
    (void)vk_bind_point;
    (void)layout;
    (void)push_stages;
    @ROOT_ALLOCATION@
    upload_calls++;
    bindings->root_descriptor_dirty_mask = 0;
    bindings->root_constant_dirty_mask = 0;
    bindings->dirty_flags &= ~VKD3D_PIPELINE_DIRTY_DESCRIPTOR_TABLE_OFFSETS;
    return true;
}
static struct vkd3d_pipeline_bindings *d3d12_command_list_get_bindings(
        struct d3d12_command_list *list, unsigned int pipeline_type)
{
    assert(pipeline_type);
    return &list->bindings;
}
static const struct d3d12_bind_point_layout *d3d12_root_signature_get_layout(
        const struct d3d12_root_signature *rs, unsigned int pipeline_type)
{
    assert(pipeline_type);
    return &rs->layout;
}
#define vk_bind_point_from_pipeline_type(type) (type)
#define d3d12_command_list_update_descriptor_heaps(...) ((void)0)
#define d3d12_command_list_update_static_samplers(...) ((void)0)
#define d3d12_command_list_update_hoisted_descriptors(...) ((void)0)
#define d3d12_command_list_update_root_constants(...) ((void)0)
#define d3d12_command_list_update_descriptor_table_offsets(...) ((void)0)
#define d3d12_command_list_update_inline_redzone(...) ((void)0)
#define d3d12_command_list_end_current_render_pass(...) ((void)0)
#define d3d12_command_list_update_compute_pipeline(...) (true)
#define d3d12_command_list_update_raygen_pipeline(...) (true)
#define d3d12_command_list_check_pre_compute_barrier(...) ((void)0)
#define d3d12_command_list_flush_rtas_batch(...) ((void)0)
#define d3d12_command_list_flush_rtas_barrier(...) ((void)0)
#define d3d12_command_list_end_transfer_batch(...) ((void)0)
#define d3d12_command_list_promote_dsv_layout(...) ((void)0)
static bool d3d12_command_list_update_graphics_pipeline(struct d3d12_command_list *list,
        enum vkd3d_pipeline_type pipeline_type)
{
    (void)list;
    (void)pipeline_type;
    return true;
}
#define d3d12_command_list_update_rendering_info(...) (true)
#define d3d12_command_list_update_dynamic_state(...) ((void)0)

@DESCRIPTORS@
@COMPUTE@
@RAYGEN@
@GRAPHICS@

static @DGC_RETURN_TYPE@ preprocess(struct d3d12_command_list *list)
{
    struct d3d12_command_signature storage = {VKD3D_PIPELINE_TYPE_GRAPHICS};
    struct d3d12_command_signature *signature = &storage;
    @DGC_RESULT@
    @PREPROCESS@
    issued_commands++;
    @DGC_SUCCESS@
@PREPROCESS_CLEANUP@
    @DGC_RETURN@
}

int main(void)
{
    struct d3d12_root_signature root = {{VKD3D_ROOT_SIGNATURE_USE_PUSH_CONSTANT_UNIFORM_BLOCK, 1, 1}, 1};
    struct mock_device device = {0};
    struct d3d12_command_list list;
    unsigned int caller, mode;
    bool success;

    for (caller = 0; caller < 4; caller++)
    {
        for (mode = 0; mode < 7; mode++)
        {
            memset(&list, 0, sizeof(list));
            list.device = &device;
            list.active_pipeline_type = caller == 1 ? VKD3D_PIPELINE_TYPE_COMPUTE :
                    caller == 2 ? VKD3D_PIPELINE_TYPE_RAY_TRACING : VKD3D_PIPELINE_TYPE_GRAPHICS;
            list.bindings.root_signature = mode == 5 ? NULL : &root;
            root.layout.flags = mode == 6 ? 0 : VKD3D_ROOT_SIGNATURE_USE_PUSH_CONSTANT_UNIFORM_BLOCK;
            list.bindings.root_descriptor_dirty_mask = mode < 2 || mode == 6 ? 4 : 0;
            list.bindings.root_constant_dirty_mask = mode == 2 ? 8 : 0;
            list.bindings.dirty_flags = mode == 3 ? VKD3D_PIPELINE_DIRTY_DESCRIPTOR_TABLE_OFFSETS : 0;
            allocation_success = mode != 1 && mode != 2 && mode != 3;
            allocation_calls = upload_calls = issued_commands = 0;

            success = false;
            switch (caller)
            {
                case 0: success = d3d12_command_list_begin_render_pass(&list, VKD3D_PIPELINE_TYPE_GRAPHICS); break;
                case 1: success = d3d12_command_list_update_compute_state(&list); break;
                case 2: success = d3d12_command_list_update_raygen_state(&list); break;
                case 3: preprocess(&list); break;
            }
            if (caller != 3 && success)
                issued_commands++;

            assert(issued_commands == (mode == 0 || mode >= 4));
            assert(list.invalid == (mode >= 1 && mode <= 3));
            assert(allocation_calls == (mode <= 3));
            assert(upload_calls == (mode == 0 || mode == 6));
            if (list.invalid)
            {
                assert(list.bindings.root_descriptor_dirty_mask == (mode == 1 ? 4 : 0));
                assert(list.bindings.root_constant_dirty_mask == (mode == 2 ? 8 : 0));
                assert(list.bindings.dirty_flags == (mode == 3 ? VKD3D_PIPELINE_DIRTY_DESCRIPTOR_TABLE_OFFSETS : 0));
            }
        }
    }
    puts("Root upload failure propagation: 28 cases passed.");
    return 0;
}
"""


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, default=ROOT / "libs/vkd3d/command.c")
    parser.add_argument("--generate", type=Path)
    parser.add_argument("--build-dir", type=Path)
    args = parser.parse_args()
    harness = generate(args.source)
    if args.generate:
        args.generate.write_text(harness)
    else:
        base = Path.home() / "tmp"
        base.mkdir(parents=True, exist_ok=True)
        build = args.build_dir or Path(tempfile.mkdtemp(prefix="root-descriptor-failures-", dir=base))
        build.mkdir(parents=True, exist_ok=True)
        source, binary = build / "root_descriptor_failures.c", build / "root_descriptor_failures"
        source.write_text(harness)
        command = shlex.split(os.environ.get("CC", "cc"))
        command += ["-std=c11", "-O1", "-g", "-UNDEBUG", "-Wall", "-Wextra", "-Werror",
                    "-Wno-unused-variable", "-Wno-unused-function", "-Wno-unused-but-set-variable"]
        if os.environ.get("DGC_TEST_SANITIZE") == "1":
            command += ["-fsanitize=address,undefined", "-fno-omit-frame-pointer", "-no-pie"]
        subprocess.run(command + [str(source), "-o", str(binary)], check=True)
        subprocess.run([str(binary)], check=True, timeout=10)
        print("Retained harness:", build)
