#!/usr/bin/env python3
# Copyright 2026 Erhan Bilgili
# SPDX-License-Identifier: LGPL-2.1-or-later
"""Inject command-buffer allocation/begin failures into production recording code.

Both allocation helpers and all post-indirect allocation call sites are extracted
from command.c unchanged. Only Vulkan and the surrounding draw work are mocked.
Use --source for a negative control, or --generate for a Meson native test.
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


def generate(path):
    source = path.read_text()
    helpers = "\n".join(block(source, source.index("static HRESULT " + name + "(")) for name in (
        "d3d12_command_allocator_allocate_fixup_command_buffer",
        "d3d12_command_allocator_allocate_init_post_indirect_command_buffer"))
    cleanup_function = block(source, source.index("static void d3d12_command_list_check_end_of_command_list_cleanup("))
    names = ("d3d12_command_list_emit_multi_dispatch_indirect_count",
             "d3d12_command_list_emit_predicated_command", "d3d12_command_list_SetPredication",
             "d3d12_command_list_execute_indirect_state_template_dgc")
    wrappers = []
    for name in names:
        function = block(source, source.index(name + "(", source.index(helpers.splitlines()[0])))
        offset = 0
        while True:
            start = function.find("d3d12_command_allocator_allocate_init_post_indirect_command_buffer(", offset)
            if start < 0:
                break
            start = function.rfind("\n", 0, start) + 1
            end = function.index(";", start) + 1
            if function[start:end].lstrip().startswith("if ("):
                end = start + len(block(function, start))
            call = function[start:end]
            # The wrapper return type matches the original call site. Every
            # failure return/goto remains exactly as written in production.
            result = "bool" if name in names[:2] else "void"
            if name == names[-1]:
                result = re.search(r"static (void|HRESULT) " + name + r"\(", source).group(1)
            success = {"bool": "return true;", "void": "return;", "HRESULT": "return S_OK;"}[result]
            failure = {"bool": "return false;", "void": "return;", "HRESULT": "return result;"}[result]
            declaration = "HRESULT result = S_FALSE;" if result == "HRESULT" else ""
            cleanup = "restore_predication:\n    " + failure if "goto restore_predication" in call else ""
            wrappers.append(f"""
static {result} caller_{len(wrappers)}(struct d3d12_command_list *list)
{{
    HRESULT hr;
    {declaration}
    {call}
    assert(list->cmd.vk_post_indirect_barrier_commands != VK_NULL_HANDLE);
    recorded++;
    {success}
    {cleanup}
}}
""")
            offset = end
    assert len(wrappers) == 5, "Update the recorder for new allocation callers."
    calls = "\n".join(f"case {i}: caller_{i}(&list); break;" for i in range(len(wrappers)))
    return (HARNESS.replace("@HELPERS@", helpers).replace("@CLEANUP@", cleanup_function)
            .replace("@WRAPPERS@", "\n".join(wrappers)).replace("@CALLS@", calls))


HARNESS = r"""
#include <assert.h>
#include <stdbool.h>
#include <stdint.h>
#include <stdio.h>
#include <string.h>

typedef int32_t HRESULT;
typedef int32_t VkResult;
typedef uintptr_t VkCommandBuffer;
typedef struct { int sType; const void *pNext; unsigned int flags; const void *pInheritanceInfo; } VkCommandBufferBeginInfo;
#define S_OK 0
#define S_FALSE 1
#define VK_SUCCESS 0
#define E_OUTOFMEMORY (-1)
#define FAILED(hr) ((HRESULT)(hr) < 0)
#define VK_NULL_HANDLE 0
#define VK_STRUCTURE_TYPE_COMMAND_BUFFER_BEGIN_INFO 1
#define VK_COMMAND_BUFFER_USAGE_ONE_TIME_SUBMIT_BIT 1
#define VK_CALL(call) call
#define VKD3D_CONFIG_FLAG_IS_SET(flag) one_time_submit
#define WARN(...) ((void)0)
#define ERR(...) ((void)0)
#define TRACE(...) ((void)0)
struct vkd3d_vk_device_procs { int unused; };
struct d3d12_device { struct vkd3d_vk_device_procs vk_procs; uintptr_t vk_device; };
struct d3d12_command_allocator_command_pool { uintptr_t vk_command_pool; };
struct d3d12_command_allocator { struct d3d12_device *device; struct d3d12_command_allocator_command_pool primary_pool; };
struct d3d12_command_list_iteration { VkCommandBuffer vk_post_indirect_barrier_commands; };
struct d3d12_command_list
{
    struct d3d12_command_allocator *allocator;
    struct d3d12_device *device;
    struct
    {
        VkCommandBuffer vk_post_indirect_barrier_commands;
        unsigned int iteration_count;
        struct d3d12_command_list_iteration iterations[1];
        bool has_post_indirect_barrier_work;
        VkCommandBuffer vk_command_buffer, vk_cleanup_commands;
        struct { struct { VkCommandBuffer vk_fixup_cmd_buffer; } suspend; } suspend_resume;
    } cmd;
    bool invalid;
};
static bool fail_allocate, fail_begin, one_time_submit;
static unsigned int allocations, begins, ends, frees, debug_begins, recorded;
static VkCommandBuffer d3d12_command_allocator_allocate_vk_command_buffer(
        struct d3d12_command_allocator_command_pool *pool, struct d3d12_device *device)
{
    (void)pool; (void)device;
    allocations++;
    return fail_allocate ? VK_NULL_HANDLE : 0x1234;
}
static VkResult vkBeginCommandBuffer(VkCommandBuffer command_buffer, const VkCommandBufferBeginInfo *info)
{
    assert(command_buffer == 0x1234);
    assert(info->sType == VK_STRUCTURE_TYPE_COMMAND_BUFFER_BEGIN_INFO);
    assert(!info->pNext && !info->pInheritanceInfo);
    assert(info->flags == (one_time_submit ? VK_COMMAND_BUFFER_USAGE_ONE_TIME_SUBMIT_BIT : 0));
    begins++;
    return fail_begin ? -2 : 0;
}
static VkResult vkEndCommandBuffer(VkCommandBuffer command_buffer)
{
    assert(command_buffer == 0x7654);
    ends++;
    return VK_SUCCESS;
}
static void vkFreeCommandBuffers(uintptr_t device, uintptr_t pool, unsigned int count, const VkCommandBuffer *buffers)
{
    (void)device; (void)pool;
    assert(count == 1 && *buffers == 0x1234);
    frees++;
}
static HRESULT hresult_from_vk_result(VkResult result) { return result; }
static void d3d12_command_list_debug_mark_begin_region_cmd(
        struct d3d12_command_list *list, VkCommandBuffer buffer, const char *tag)
{
    (void)list; (void)tag;
    assert(buffer == 0x1234);
    debug_begins++;
}
static void d3d12_command_list_mark_as_invalid(struct d3d12_command_list *list, const char *message, ...)
{
    (void)message;
    list->invalid = true;
}
@HELPERS@
@CLEANUP@
@WRAPPERS@

int main(int argc, char **argv)
{
    struct d3d12_device device = {0};
    struct d3d12_command_allocator allocator = {.device = &device};
    struct d3d12_command_list list;
    VkCommandBuffer fixup;
    unsigned int helper, fault, caller, one_time;
    HRESULT hr;
    bool callers_only = argc > 1 && !strcmp(argv[1], "callers");

    for (one_time = 0; one_time < 2; one_time++)
    for (helper = 0; helper < 2; helper++)
    for (fault = 0; fault < 4; fault++)
    {
        if (callers_only)
            continue;
        memset(&list, 0, sizeof(list));
        list.allocator = &allocator;
        list.cmd.iteration_count = 1;
        fixup = fault == 3 ? 0x4321 : VK_NULL_HANDLE;
        list.cmd.vk_post_indirect_barrier_commands = fixup;
        fail_allocate = fault == 1;
        fail_begin = fault == 2;
        one_time_submit = one_time;
        allocations = begins = frees = debug_begins = 0;
        hr = helper ? d3d12_command_allocator_allocate_init_post_indirect_command_buffer(&allocator, &list) :
                d3d12_command_allocator_allocate_fixup_command_buffer(&allocator, &list, &fixup, "test");
        if (fault == 1 || fault == 2)
        {
            assert(FAILED(hr));
            assert(!fixup && !list.cmd.vk_post_indirect_barrier_commands);
            assert(!list.cmd.iterations[0].vk_post_indirect_barrier_commands);
            assert(!list.cmd.has_post_indirect_barrier_work && !debug_begins);
            assert(allocations == 1 && begins == (fault == 2) && frees == (fault == 2));
            fail_allocate = fail_begin = false;
            hr = helper ? d3d12_command_allocator_allocate_init_post_indirect_command_buffer(&allocator, &list) :
                    d3d12_command_allocator_allocate_fixup_command_buffer(&allocator, &list, &fixup, "retry");
            assert(hr == S_OK && allocations == 2);
        }
        else
        {
            assert(hr == S_OK);
            assert(allocations == (fault == 0) && begins == (fault == 0) && !frees);
        }
    }

    for (caller = 0; caller < 5; caller++)
    for (fault = 0; fault < 4; fault++)
    {
        memset(&list, 0, sizeof(list));
        list.allocator = &allocator;
        list.cmd.iteration_count = 1;
        list.cmd.vk_post_indirect_barrier_commands = fault == 3 ? 0x4321 : VK_NULL_HANDLE;
        allocations = begins = frees = recorded = 0;
        fail_allocate = fault == 1;
        fail_begin = fault == 2;
        switch (caller) { @CALLS@ }
        assert(recorded == (fault == 0 || fault == 3));
        assert(list.invalid == (fault == 1 || fault == 2));
    }
    for (fault = 0; fault < 4; fault++)
    {
        memset(&list, 0, sizeof(list));
        list.allocator = &allocator;
        list.device = &device;
        list.cmd.vk_command_buffer = 0x7654;
        list.cmd.vk_cleanup_commands = fault == 3 ? 0x4321 : VK_NULL_HANDLE;
        list.cmd.suspend_resume.suspend.vk_fixup_cmd_buffer = 0x5678;
        allocations = begins = ends = frees = 0;
        fail_allocate = fault == 1;
        fail_begin = fault == 2;
        d3d12_command_list_check_end_of_command_list_cleanup(&list);
        assert(ends == (fault == 0));
        assert(list.cmd.vk_command_buffer == (fault == 0 ? 0x1234 : 0x7654));
        assert(list.invalid == (fault == 1 || fault == 2));
        if (fault == 1 || fault == 2)
            assert(!list.cmd.vk_cleanup_commands);
    }
    puts("Command-buffer allocation tests passed (40 cases).");
    return 0;
}
"""


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, default=ROOT / "libs/vkd3d/command.c")
    parser.add_argument("--generate", type=Path)
    parser.add_argument("--build-dir", type=Path)
    parser.add_argument("--callers-only", action="store_true")
    args = parser.parse_args()
    harness = generate(args.source)
    if args.generate:
        args.generate.write_text(harness)
    else:
        base = Path.home() / "tmp"
        base.mkdir(parents=True, exist_ok=True)
        build = args.build_dir or Path(tempfile.mkdtemp(prefix="indirect-allocation-", dir=base))
        build.mkdir(parents=True, exist_ok=True)
        source, binary = build / "indirect_allocation.c", build / "indirect_allocation"
        source.write_text(harness)
        command = shlex.split(os.environ.get("CC", "cc"))
        command += ["-std=c11", "-O1", "-g", "-UNDEBUG", "-Wall", "-Wextra", "-Werror",
                    "-Wno-unused-variable", "-Wno-unused-function"]
        if os.environ.get("DGC_TEST_SANITIZE") == "1":
            command += ["-fsanitize=address,undefined", "-fno-omit-frame-pointer", "-no-pie"]
        subprocess.run(command + [str(source), "-o", str(binary)], check=True)
        subprocess.run([str(binary)] + (["callers"] if args.callers_only else []), check=True, timeout=10)
        print("Retained harness:", build)
