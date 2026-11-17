#!/usr/bin/env python3
# Copyright 2026 Erhan Bilgili
# SPDX-License-Identifier: LGPL-2.1-or-later
"""Exercise upload-barrier classification and bookkeeping without a GPU.

The helper and D3D12 declarations are extracted from the production sources.
Resource tracking records calls. Rejected batches must leave the list untouched.
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
        "static bool d3d12_command_list_check_resource_barrier_read_only_upload("), TESTS]
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
    struct { D3D12_RESOURCE_DIMENSION Dimension; } desc;
};
struct d3d12_command_list
{
    D3D12_COMMAND_LIST_TYPE type;
    struct
    {
        uintptr_t vk_command_buffer, vk_post_indirect_barrier_commands;
        bool observes_indirect_argument_barrier;
    } cmd;
    struct d3d12_resource *tracked[8];
    unsigned int tracked_count;
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
    struct d3d12_resource upload = {{D3D12_HEAP_TYPE_UPLOAD}, {D3D12_RESOURCE_DIMENSION_BUFFER}};
    D3D12_RESOURCE_BARRIER barriers[] = {
        transition(&upload, D3D12_RESOURCE_STATE_VERTEX_AND_CONSTANT_BUFFER, D3D12_RESOURCE_STATE_GENERIC_READ),
        invalid,
    };
    reject(D3D12_COMMAND_LIST_TYPE_DIRECT, 1, &invalid);
    /* The valid prefix would update BOTH tracking and indirect metadata if applied too soon. */
    reject(D3D12_COMMAND_LIST_TYPE_DIRECT, ARRAY_SIZE(barriers), barriers);
}

int main(void)
{
    struct d3d12_resource upload = {{D3D12_HEAP_TYPE_UPLOAD}, {D3D12_RESOURCE_DIMENSION_BUFFER}};
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
