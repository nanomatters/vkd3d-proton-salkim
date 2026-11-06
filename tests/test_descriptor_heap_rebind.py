#!/usr/bin/env python3
# Copyright 2026 Erhan Bilgili
# SPDX-License-Identifier: LGPL-2.1-or-later
"""Exercise descriptor heap rebinding through the production heap backends.

GPU commands are replaced with recorders. Offset reset, heap identity checks,
dirty-set handling and the application override table are extracted unchanged.
"""

import argparse
import os
from pathlib import Path
import shlex
import subprocess
import tempfile


ROOT = Path(__file__).resolve().parents[1]


def block(source, marker, terminated=False):
    start = source.index(marker)
    end = source.index("{", start) + 1
    depth = 1
    while depth:
        depth += (source[end] == "{") - (source[end] == "}")
        end += 1
    return source[start:end] + (";" if terminated else "")


def generate(source):
    command = source.read_text()
    config = (ROOT / "include/private/config_flags.h").read_text().replace(
        '#include "config_flag_decl.h"', (ROOT / "include/private/config_flag_decl.h").read_text())
    workarounds = (ROOT / "libs/vkd3d/device_workarounds.c").read_text()
    strings = (ROOT / "include/private/vkd3d_string.h").read_text()
    pieces = [PREFIX, config, "union vkd3d_config_flags vkd3d_config_flags;", TYPES]
    for name in ("vkd3d_pipeline_bindings_set_dirty_sets", "d3d12_command_list_clear_descriptor_table_offsets",
                 "d3d12_command_list_set_descriptor_heaps", "d3d12_command_list_set_descriptor_heaps_buffers",
                 "d3d12_command_list_set_descriptor_heaps_sets"):
        pieces.append(block(command, "static void " + name + "("))
    pieces.append(block(strings, "enum vkd3d_string_compare_mode", True))
    pieces.append(block(workarounds, "enum vkd3d_application_feature_override", True))
    pieces.append("typedef uint32_t vkd3d_application_feature_override_flags;")
    pieces.append(block(workarounds, "struct vkd3d_instance_application_meta\n", True))
    pieces.append(block(workarounds, "static const struct vkd3d_instance_application_meta application_override[]", True))
    pieces.append(TESTS)
    return "\n".join(pieces)


PREFIX = r'''
#ifdef NDEBUG
#undef NDEBUG
#endif
#include <assert.h>
#include <stdbool.h>
#include <stdint.h>
#include <stdio.h>
#include <string.h>
#define ARRAY_SIZE(a) (sizeof(a) / sizeof((a)[0]))
#define STATIC_ASSERT(expr) _Static_assert(expr, #expr)
static unsigned int vkd3d_popcount(uint32_t value) { return __builtin_popcount(value); }
'''

TYPES = r'''
typedef uint64_t VkDeviceAddress;
typedef uint64_t VkDescriptorSet;
#define D3D12_DESCRIPTOR_HEAP_TYPE_CBV_SRV_UAV 0
#define D3D12_DESCRIPTOR_HEAP_TYPE_SAMPLER 1
#define VKD3D_DESCRIPTOR_HEAP_DIRTY_RESOURCE 1
#define VKD3D_DESCRIPTOR_HEAP_DIRTY_SAMPLER 2
#define VKD3D_DESCRIPTOR_HEAP_DIRTY_ALL 3
#define VKD3D_PIPELINE_DIRTY_DESCRIPTOR_TABLE_OFFSETS 1
#define VKD3D_PIPELINE_DIRTY_HOISTED_DESCRIPTORS 2
#define OTHER_DIRTY 8
struct d3d12_descriptor_heap
{
    struct { unsigned int Type; } desc;
    struct { VkDescriptorSet vk_descriptor_set; } sets[2];
    struct { uintptr_t ptr; } cpu_va;
    struct { uint64_t va, size, reserved_offset, vk_buffer, offsets[2]; } descriptor_buffer;
};
typedef struct d3d12_descriptor_heap ID3D12DescriptorHeap;
struct vkd3d_bindless_state
{
    struct { unsigned int set_count; struct { unsigned int heap_type; } set_info[3]; } legacy;
};
struct d3d12_device { struct vkd3d_bindless_state bindless_state; };
struct d3d12_root_signature
{
    unsigned int descriptor_table_count;
    struct { unsigned int num_desc; } hoist_info;
};
struct vkd3d_pipeline_bindings
{
    const struct d3d12_root_signature *root_signature;
    uint32_t descriptor_tables[8];
    uint64_t legacy_descriptor_set_dirty_mask;
    unsigned int dirty_flags;
};
struct d3d12_command_list
{
    struct d3d12_device *device;
    struct vkd3d_pipeline_bindings graphics_bindings, compute_bindings;
    struct
    {
        struct { VkDescriptorSet vk_sets[3]; } sets;
        struct
        {
            struct { uint64_t va, size, reserved_offset; struct d3d12_descriptor_heap *heap; } resource, sampler;
            struct { uint64_t heap_va_resource, heap_va_sampler, vk_buffer_resource, vk_offsets[3]; } db;
            unsigned int dirty_mask;
        } buffers;
    } descriptor_heap;
    void *cbv_srv_uav_descriptors_view;
};
struct d3d12_desc_split { void *view; };
static unsigned int invalidations, global_binds, cases;
static struct d3d12_descriptor_heap *impl_from_ID3D12DescriptorHeap(ID3D12DescriptorHeap *heap) { return heap; }
static struct d3d12_desc_split d3d12_desc_decode_va(uintptr_t va)
{
    return (struct d3d12_desc_split) { (void *)va };
}
static bool d3d12_device_use_embedded_mutable_descriptors(struct d3d12_device *device)
{
    (void)device;
    return false;
}
static void d3d12_command_list_update_global_descriptor_heap(struct d3d12_command_list *list)
{
    (void)list;
    global_binds++;
}
static void d3d12_command_list_invalidate_root_parameters(struct d3d12_command_list *list,
        struct vkd3d_pipeline_bindings *bindings, bool sets, struct vkd3d_pipeline_bindings *sibling)
{
    (void)list; (void)bindings; (void)sets; (void)sibling;
    invalidations++;
}
'''

TESTS = r'''
typedef void (*bind_fn)(struct d3d12_command_list *, unsigned int, ID3D12DescriptorHeap *const *);
static const bind_fn backends[] =
{
    d3d12_command_list_set_descriptor_heaps,
    d3d12_command_list_set_descriptor_heaps_buffers,
    d3d12_command_list_set_descriptor_heaps_sets,
};
static struct d3d12_device device = { .bindless_state.legacy =
    { .set_count = 3, .set_info = { { 0 }, { 1 }, { 0 } } } };
static const struct d3d12_root_signature root = { .descriptor_table_count = 8, .hoist_info.num_desc = 1 };

static struct d3d12_descriptor_heap make_heap(unsigned int type, uint64_t id)
{
    struct d3d12_descriptor_heap heap = { .desc.Type = type, .cpu_va.ptr = id * 0x1000,
        .descriptor_buffer = { .va = id * 0x10000, .size = 4096, .reserved_offset = 4000,
            .vk_buffer = id, .offsets = { 16, 32 } } };
    heap.sets[0].vk_descriptor_set = id * 2;
    heap.sets[1].vk_descriptor_set = id * 2 + 1;
    return heap;
}

static void seed_bindings(struct d3d12_command_list *list)
{
    unsigned int i;
    for (i = 0; i < ARRAY_SIZE(list->graphics_bindings.descriptor_tables); i++)
    {
        list->graphics_bindings.descriptor_tables[i] = 100 + i;
        list->compute_bindings.descriptor_tables[i] = 200 + i;
    }
    list->graphics_bindings.dirty_flags = list->compute_bindings.dirty_flags = OTHER_DIRTY;
    list->graphics_bindings.legacy_descriptor_set_dirty_mask = 7;
    list->compute_bindings.legacy_descriptor_set_dirty_mask = 7;
}

static void check_tables(const struct d3d12_command_list *list, bool cleared)
{
    unsigned int i;
    for (i = 0; i < ARRAY_SIZE(list->graphics_bindings.descriptor_tables); i++)
    {
        assert(list->graphics_bindings.descriptor_tables[i] == (cleared ? 0 : 100 + i));
        assert(list->compute_bindings.descriptor_tables[i] == (cleared ? 0 : 200 + i));
    }
}

static void run_rebinds(unsigned int backend, bool enabled)
{
    struct d3d12_descriptor_heap resource[2] = { make_heap(0, 1), make_heap(0, 2) };
    struct d3d12_descriptor_heap sampler[2] = { make_heap(1, 3), make_heap(1, 4) };
    unsigned int change, reverse, repeat;
    ID3D12DescriptorHeap *heaps[2];
    struct d3d12_command_list list;
    bind_fn bind = backends[backend];

    vkd3d_config_flags = VKD3D_CONFIG_FLAG_INIT(.CLEAR_TABLE_OFFSETS_ON_REBIND = enabled);
    for (change = 0; change < 4; change++)
    for (reverse = 0; reverse < 2; reverse++)
    {
        memset(&list, 0, sizeof(list));
        list.device = &device;
        list.graphics_bindings.root_signature = list.compute_bindings.root_signature = &root;
        heaps[0] = &resource[0];
        heaps[1] = &sampler[0];
        bind(&list, 2, heaps);
        seed_bindings(&list);
        heaps[reverse] = &resource[change & 1];
        heaps[1 - reverse] = &sampler[!!(change & 2)];
        invalidations = global_binds = 0;
        bind(&list, 2, heaps);
        check_tables(&list, enabled && change);
        assert(list.graphics_bindings.dirty_flags & OTHER_DIRTY);
        assert(list.compute_bindings.dirty_flags & OTHER_DIRTY);
        if (enabled && change)
        {
            assert(list.graphics_bindings.dirty_flags & VKD3D_PIPELINE_DIRTY_DESCRIPTOR_TABLE_OFFSETS);
            assert(list.compute_bindings.dirty_flags & VKD3D_PIPELINE_DIRTY_DESCRIPTOR_TABLE_OFFSETS);
            assert(list.graphics_bindings.dirty_flags & VKD3D_PIPELINE_DIRTY_HOISTED_DESCRIPTORS);
            assert(list.compute_bindings.dirty_flags & VKD3D_PIPELINE_DIRTY_HOISTED_DESCRIPTORS);
        }
        if (!change)
            assert(!invalidations && !global_binds);
        if (backend == 2)
        {
            assert(list.graphics_bindings.legacy_descriptor_set_dirty_mask == 7);
            assert(list.compute_bindings.legacy_descriptor_set_dirty_mask == 7);
            assert(list.descriptor_heap.sets.vk_sets[0] == resource[change & 1].sets[0].vk_descriptor_set);
            assert(list.descriptor_heap.sets.vk_sets[2] == resource[change & 1].sets[1].vk_descriptor_set);
        }
        cases++;

        /* Rebinds before the next draw must retain all newly written offsets. */
        seed_bindings(&list);
        for (repeat = 0; repeat < 3; repeat++)
        {
            bind(&list, 2, heaps);
            check_tables(&list, false);
            if (backend == 2)
                assert(list.graphics_bindings.legacy_descriptor_set_dirty_mask == 7);
            cases++;
        }

        /* A different heap and then the original heap retain offsets by default. */
        heaps[0] = &resource[0];
        heaps[1] = &sampler[0];
        bind(&list, 2, heaps);
        check_tables(&list, enabled && change);
        cases++;
    }
}

static void run_root_cases(unsigned int backend)
{
    struct d3d12_descriptor_heap resource = make_heap(0, 1);
    struct d3d12_descriptor_heap samplers[2] = { make_heap(1, 2), make_heap(1, 3) };
    ID3D12DescriptorHeap *heaps[] = { &resource, &samplers[0] };
    struct d3d12_command_list list;
    struct d3d12_root_signature signature;
    unsigned int root_case;

    vkd3d_config_flags = VKD3D_CONFIG_FLAG(CLEAR_TABLE_OFFSETS_ON_REBIND);
    for (root_case = 0; root_case < 4; root_case++)
    {
        memset(&list, 0, sizeof(list));
        list.device = &device;
        signature.descriptor_table_count = root_case == 1 || root_case == 3 ? 8 : 0;
        signature.hoist_info.num_desc = root_case >= 2 ? 1 : 0;
        list.graphics_bindings.root_signature = root_case ? &signature : NULL;
        list.compute_bindings.root_signature = root_case ? &signature : NULL;
        heaps[1] = &samplers[0];
        backends[backend](&list, 2, heaps);
        seed_bindings(&list);
        heaps[1] = &samplers[1];
        backends[backend](&list, 2, heaps);
        check_tables(&list, true);
        assert(!!(list.graphics_bindings.dirty_flags & VKD3D_PIPELINE_DIRTY_DESCRIPTOR_TABLE_OFFSETS)
                == !!signature.descriptor_table_count);
        assert(!!(list.compute_bindings.dirty_flags & VKD3D_PIPELINE_DIRTY_DESCRIPTOR_TABLE_OFFSETS)
                == !!signature.descriptor_table_count);
        if (signature.hoist_info.num_desc)
        {
            assert(list.graphics_bindings.dirty_flags & VKD3D_PIPELINE_DIRTY_HOISTED_DESCRIPTORS);
            assert(list.compute_bindings.dirty_flags & VKD3D_PIPELINE_DIRTY_HOISTED_DESCRIPTORS);
        }
        assert(list.graphics_bindings.dirty_flags & OTHER_DIRTY);
        assert(list.compute_bindings.dirty_flags & OTHER_DIRTY);
        cases++;
    }
}

static void run_second_set_change(void)
{
    struct d3d12_descriptor_heap resource = make_heap(0, 1), sampler = make_heap(1, 2);
    ID3D12DescriptorHeap *heaps[] = { &resource, &sampler };
    struct d3d12_command_list list = { .device = &device };
    vkd3d_config_flags = VKD3D_CONFIG_FLAG(CLEAR_TABLE_OFFSETS_ON_REBIND);
    d3d12_command_list_set_descriptor_heaps_sets(&list, 2, heaps);
    seed_bindings(&list);
    resource.sets[1].vk_descriptor_set++;
    d3d12_command_list_set_descriptor_heaps_sets(&list, 2, heaps);
    check_tables(&list, true);
    cases++;
}

static void check_application_override(void)
{
    unsigned int i, matched = 0;
    vkd3d_config_flags = VKD3D_CONFIG_FLAG(CLEAR_TABLE_OFFSETS_ON_REBIND);
    for (i = 0; i < ARRAY_SIZE(application_override); i++)
    {
        if (!vkd3d_config_flag_overlaps(application_override[i].global_flags_add, vkd3d_config_flags))
            continue;
        assert(application_override[i].mode == VKD3D_STRING_COMPARE_EXACT);
        assert(!strcmp(application_override[i].name, "Resonance of Fate.exe"));
        matched++;
    }
    assert(matched == 1);
    cases++;
}

int main(void)
{
    unsigned int backend;
    for (backend = 0; backend < ARRAY_SIZE(backends); backend++)
    {
        run_rebinds(backend, false);
        run_rebinds(backend, true);
        run_root_cases(backend);
    }
    run_second_set_change();
    check_application_override();
    printf("Descriptor heap rebind tests passed (%u cases).\n", cases);
    return 0;
}
'''


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
        build = args.build_dir or Path(tempfile.mkdtemp(prefix="descriptor-heap-rebind-"))
        build.mkdir(parents=True, exist_ok=True)
        source, binary = build / "descriptor_heap_rebind.c", build / "descriptor_heap_rebind"
        source.write_text(harness)
        command = shlex.split(os.environ.get("CC", "cc"))
        command += ["-std=c11", "-O1", "-g", "-UNDEBUG", "-Wall", "-Wextra", "-Werror",
                    "-Wno-missing-field-initializers"]
        if os.environ.get("DESCRIPTOR_TEST_SANITIZE") == "1":
            command += ["-fsanitize=address,undefined", "-fno-omit-frame-pointer", "-no-pie"]
        subprocess.run(command + [str(source), "-o", str(binary)], check=True)
        subprocess.run([str(binary)], check=True, timeout=10)
        print("Retained harness:", build)
