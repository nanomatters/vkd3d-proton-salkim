#!/usr/bin/env python3
# Copyright 2026 Erhan Bilgili
# SPDX-License-Identifier: LGPL-2.1-or-later
"""Exercise production allocation policy and mutex ownership without a GPU."""

import argparse
import os
from pathlib import Path
import shlex
import subprocess


def extract_function(source, name):
    start = source.index(name + "(")
    start = source.rfind("\n", 0, start) + 1
    body = source.index("{", start)
    end, depth = body + 1, 1
    while depth:
        depth += (source[end] == "{") - (source[end] == "}")
        end += 1
    return source[start:end]


PREAMBLE = r'''
#define _POSIX_C_SOURCE 200809L
#include <errno.h>
#include <inttypes.h>
#include <pthread.h>
#include <stdbool.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

typedef uint64_t VkDeviceSize;
typedef uint32_t VkMemoryPropertyFlags;
typedef int HRESULT;
typedef int VkResult;
#define S_OK 0
#define E_OUTOFMEMORY -1
#define FAILED(hr) ((hr) < 0)
#define VK_SUCCESS 0
#define VK_ERROR_OUT_OF_DEVICE_MEMORY -2
#define VK_MEMORY_PROPERTY_DEVICE_LOCAL_BIT 1
#define VK_MEMORY_PROPERTY_HOST_VISIBLE_BIT 2
#define VK_MEMORY_PROPERTY_HOST_COHERENT_BIT 4
#define VK_MEMORY_PROPERTY_HOST_CACHED_BIT 8
#define MEMORY_DEVICE VK_MEMORY_PROPERTY_DEVICE_LOCAL_BIT
#define MEMORY_HOST (VK_MEMORY_PROPERTY_HOST_VISIBLE_BIT | VK_MEMORY_PROPERTY_HOST_COHERENT_BIT)
#define MEMORY_SYSTEM (MEMORY_HOST | VK_MEMORY_PROPERTY_HOST_CACHED_BIT)
#define MEMORY_REBAR (MEMORY_DEVICE | MEMORY_HOST)
#define MEMORY_REBAR_CACHED (MEMORY_REBAR | VK_MEMORY_PROPERTY_HOST_CACHED_BIT)
#define TYPE_DEVICE (1u << 0)
#define TYPE_SYSTEM (1u << 1)
#define TYPE_REBAR (1u << 2)
#define TYPE_REBAR_CACHED (1u << 3)
#define TYPES_REBAR (TYPE_REBAR | TYPE_REBAR_CACHED)
#define TYPES_ALL (TYPE_DEVICE | TYPE_SYSTEM | TYPES_REBAR)
#define VK_STRUCTURE_TYPE_MEMORY_ALLOCATE_INFO 1
#define VK_OBJECT_TYPE_DEVICE_MEMORY 1
#define VKD3D_QUEUE_TIMELINE_TRACE_STATE_TYPE_VK_ALLOCATE_MEMORY 1
#define LOG_MEMORY_BUDGET 1
#define VKD3D_CONFIG_FLAG_IS_SET(x) logging
#define VK_CALL(x) (x)
#define INFO(...) ((void)0)
#define WARN(...) ((void)0)
#define FIXME(...) ((void)0)
#define ERR(...) ((void)0)

static const char *current_case;
static unsigned int checks, cases;
#define CHECK(expr) do { checks++; if (!(expr)) { \
    fprintf(stderr, "%s:%d: %s: %s\n", __FILE__, __LINE__, current_case, #expr); exit(1); \
} } while (0)

struct VkPhysicalDeviceMemoryProperties
{
    uint32_t memoryTypeCount;
    struct { uint32_t propertyFlags, heapIndex; } memoryTypes[32];
    struct { uint64_t size; } memoryHeaps[16];
};
typedef struct VkPhysicalDeviceMemoryProperties VkPhysicalDeviceMemoryProperties;
typedef struct
{
    int sType;
    void *pNext;
    uint64_t allocationSize;
    uint32_t memoryTypeIndex;
} VkMemoryAllocateInfo;
struct vkd3d_vk_device_procs { int unused; };
struct vkd3d_memory_info
{
    pthread_mutex_t budget_lock;
    uint32_t rebar_budget_mask;
    uint64_t rebar_current, rebar_budget, type_current[32];
    struct { uint32_t rt_ds_type_mask, sampled_type_mask, buffer_type_mask; } fallback_domain;
};
struct d3d12_device
{
    struct VkPhysicalDeviceMemoryProperties memory_properties;
    struct vkd3d_vk_device_procs vk_procs;
    struct vkd3d_memory_info memory_info;
    struct
    {
        struct { uint64_t maxMemoryAllocationSize; } vulkan_1_1_properties;
        struct { bool pageableDeviceLocalMemory; } pageable_device_memory_features;
    } device_info;
    int vk_device, queue_timeline_trace, address_binding_tracker;
};
struct vkd3d_device_memory_allocation
{
    uint64_t vk_memory, size;
    uint32_t vk_memory_type;
};
union vkd3d_address_binding_report_resource_info
{
    struct { uint32_t memory_type_index; } memory;
};

static struct d3d12_device *active_device;
static bool logging, respect_budget, mutex_owned;
static unsigned int locks, unlocks, allocation_count;
static uint32_t failed_types, attempted_types[32];
static VkDeviceSize requested_size;
static const int next_cookie;

static int checked_lock(pthread_mutex_t *lock)
{
    CHECK(!mutex_owned);
    CHECK(!pthread_mutex_lock(lock));
    mutex_owned = true;
    locks++;
    return 0;
}

static int checked_unlock(pthread_mutex_t *lock)
{
    /* ERRORCHECK also checks ownership independently of our accounting. */
    CHECK(!pthread_mutex_unlock(lock));
    CHECK(mutex_owned);
    mutex_owned = false;
    unlocks++;
    return 0;
}
#define pthread_mutex_lock checked_lock
#define pthread_mutex_unlock checked_unlock

static uint32_t vkd3d_bitmask_iter32(uint32_t *mask)
{
    uint32_t bit = __builtin_ctz(*mask);
    *mask &= *mask - 1;
    return bit;
}
static uint32_t vkd3d_bitmask_tzcnt32(uint32_t mask) { return mask ? __builtin_ctz(mask) : 32; }
static void vkd3d_address_binding_tracker_mark_user_thread(void) {}
static void vkd3d_queue_timeline_trace_register_instantaneous(void *p, int a, uint64_t b) {}
static bool vkd3d_address_binding_tracker_active(void *p) { return false; }
static void vkd3d_address_binding_tracker_assign_info(void *p, int a, uint64_t b, void *c) {}
static void vkd3d_report_memory_budget(void *p) { CHECK(!mutex_owned); }

static VkResult vkAllocateMemory(int dev, const VkMemoryAllocateInfo *info, void *p, uint64_t *memory)
{
    bool budgeted = (active_device->memory_info.rebar_budget_mask & (1u << info->memoryTypeIndex)) != 0;
    CHECK(info->sType == VK_STRUCTURE_TYPE_MEMORY_ALLOCATE_INFO);
    CHECK(info->pNext == &next_cookie);
    CHECK(info->allocationSize == requested_size);
    CHECK(info->memoryTypeIndex < active_device->memory_properties.memoryTypeCount);
    CHECK(mutex_owned == (budgeted && respect_budget));
    CHECK(allocation_count < 32);
    attempted_types[allocation_count++] = info->memoryTypeIndex;
    if (failed_types & (1u << info->memoryTypeIndex))
        return VK_ERROR_OUT_OF_DEVICE_MEMORY;
    *memory = 0x100 + info->memoryTypeIndex;
    return VK_SUCCESS;
}
'''

TESTS = r'''
struct test_case
{
    const char *name;
    uint32_t flags, type_mask, failures;
    VkDeviceSize size, budget;
    bool public_allocator, pageable;
    int expected_type;
    unsigned int expected_count;
    uint32_t expected_attempts[4];
};

static void run_case(const struct test_case *test)
{
    struct d3d12_device dev = {0};
    struct vkd3d_device_memory_allocation allocation = {0};
    pthread_mutexattr_t attr;
    HRESULT hr;
    unsigned int i;
    bool success, rebar;

    current_case = test->name;
    active_device = &dev;
    CHECK(!pthread_mutexattr_init(&attr));
    CHECK(!pthread_mutexattr_settype(&attr, PTHREAD_MUTEX_ERRORCHECK));
    CHECK(!pthread_mutex_init(&dev.memory_info.budget_lock, &attr));
    CHECK(!pthread_mutexattr_destroy(&attr));
    dev.memory_properties.memoryTypeCount = 4;
    dev.memory_properties.memoryTypes[0].propertyFlags = MEMORY_DEVICE;
    dev.memory_properties.memoryTypes[1].propertyFlags = MEMORY_SYSTEM;
    dev.memory_properties.memoryTypes[1].heapIndex = 1;
    dev.memory_properties.memoryTypes[2].propertyFlags = MEMORY_REBAR;
    dev.memory_properties.memoryTypes[3].propertyFlags = MEMORY_REBAR_CACHED;
    dev.memory_properties.memoryHeaps[0].size = 1024;
    dev.memory_properties.memoryHeaps[1].size = 4096;
    dev.device_info.vulkan_1_1_properties.maxMemoryAllocationSize = UINT64_MAX;
    dev.device_info.pageable_device_memory_features.pageableDeviceLocalMemory = test->pageable;
    dev.memory_info.rebar_budget_mask = TYPES_REBAR;
    dev.memory_info.rebar_budget = test->budget;
    dev.memory_info.rebar_current = 8;
    for (i = 0; i < 32; i++)
        dev.memory_info.type_current[i] = 8;
    dev.memory_info.fallback_domain.rt_ds_type_mask = TYPES_ALL;
    dev.memory_info.fallback_domain.sampled_type_mask = TYPES_ALL;
    dev.memory_info.fallback_domain.buffer_type_mask = TYPES_ALL;
    locks = unlocks = allocation_count = 0;
    mutex_owned = false;
    requested_size = test->size;
    failed_types = test->failures;

    if (test->public_allocator)
        hr = vkd3d_allocate_device_memory(&dev, test->size, test->flags, test->type_mask,
                (void *)&next_cookie, respect_budget, &allocation);
    else
        hr = vkd3d_try_allocate_device_memory(&dev, test->size, test->flags, test->type_mask,
                (void *)&next_cookie, respect_budget, &allocation);

    CHECK(hr == (test->expected_type < 0 ? E_OUTOFMEMORY : S_OK));
    CHECK(allocation_count == test->expected_count);
    for (i = 0; i < allocation_count; i++)
        CHECK(attempted_types[i] == test->expected_attempts[i]);
    CHECK(!mutex_owned);
    CHECK(locks == unlocks);
    CHECK(!pthread_mutex_trylock(&dev.memory_info.budget_lock));
    mutex_owned = true;
    locks++;
    CHECK(!pthread_mutex_unlock(&dev.memory_info.budget_lock));
    CHECK(!pthread_mutex_destroy(&dev.memory_info.budget_lock));

    success = test->expected_type >= 0;
    rebar = success && (dev.memory_info.rebar_budget_mask & (1u << test->expected_type));
    CHECK(dev.memory_info.rebar_current == 8 + (rebar ? test->size : 0));
    for (i = 0; i < 32; i++)
    {
        VkDeviceSize expected = 8;
        if (success && i == (uint32_t)test->expected_type && (logging || rebar))
            expected += test->size;
        CHECK(dev.memory_info.type_current[i] == expected);
    }
    if (success)
    {
        CHECK(allocation.vk_memory_type == (uint32_t)test->expected_type);
        CHECK(allocation.vk_memory == 0x100u + test->expected_type);
        CHECK(allocation.size == test->size);
    }
    else
    {
        CHECK(!allocation.vk_memory);
        CHECK(!allocation.size);
    }
    cases++;
}

static void run_locking(void)
{
    const struct test_case common[] = {
        {"ReBAR success", MEMORY_REBAR, TYPES_ALL, 0, 32, 64, false, false, 2, 1, {2}},
        {"ReBAR failed allocation", MEMORY_REBAR, TYPES_ALL, TYPES_REBAR, 32, 64, false, false, -1, 2, {2, 3}},
        {"ReBAR candidate retry", MEMORY_REBAR, TYPES_ALL, TYPE_REBAR, 32, 64, false, false, 3, 2, {2, 3}},
        {"ReBAR fallback succeeds", MEMORY_REBAR, TYPES_ALL, TYPES_REBAR, 32, 64, true, false, 1, 3, {2, 3, 1}},
        {"System allocation failure", MEMORY_HOST, TYPES_ALL, TYPE_SYSTEM, 32, 64, false, false, -1, 1, {1}},
        {"Heap-size rejection", MEMORY_REBAR, TYPES_ALL, 0, 950, 2048, false, false, -1, 0, {0}},
    };
    const struct test_case budgeted[] = {
        {"ReBAR budget rejection", MEMORY_REBAR, TYPES_ALL, 0, 32, 8, false, false, -1, 0, {0}},
        {"ReBAR budget fallback", MEMORY_REBAR, TYPES_ALL, 0, 32, 8, true, false, 1, 1, {1}},
        {"ReBAR budget boundary", MEMORY_REBAR, TYPES_ALL, 0, 56, 64, false, false, 2, 1, {2}},
    };
    const struct test_case unbudgeted =
        {"ReBAR budget bypass", MEMORY_REBAR, TYPES_ALL, 0, 32, 8, false, false, 2, 1, {2}};
    for (unsigned int log = 0; log < 2; log++)
    for (unsigned int respect = 0; respect < 2; respect++)
    {
        logging = log;
        respect_budget = respect;
        for (unsigned int i = 0; i < sizeof(common) / sizeof(*common); i++)
            run_case(&common[i]);
        if (respect)
            for (unsigned int i = 0; i < sizeof(budgeted) / sizeof(*budgeted); i++)
                run_case(&budgeted[i]);
        else
            run_case(&unbudgeted);
    }
}

static void run_candidates(void)
{
    const struct test_case tests[] = {
        {"Device-local preference", MEMORY_DEVICE, TYPES_ALL, 0, 32, 64, true, false, 0, 1, {0}},
        {"System-memory preference", 0, TYPES_ALL, 0, 32, 64, false, false, 1, 1, {1}},
        {"System fallback after VRAM failure", MEMORY_DEVICE, TYPES_ALL, TYPE_DEVICE, 32, 64, true, false, 1, 2, {0, 1}},
        {"Pageable memory avoids fallback", MEMORY_DEVICE, TYPES_ALL, TYPE_DEVICE, 32, 64, true, true, -1, 1, {0}},
        {"UMA keeps host-visible device memory", MEMORY_DEVICE, TYPE_REBAR, 0, 32, 64, true, false, 2, 1, {2}},
        {"Device-only memory remains valid", 0, TYPE_DEVICE, 0, 32, 64, false, false, 0, 1, {0}},
        {"Host-only memory remains valid", 0, TYPE_SYSTEM, 0, 32, 64, false, false, 1, 1, {1}},
        {"Missing optional device-local flag", MEMORY_REBAR, TYPE_SYSTEM, 0, 32, 64, false, false, 1, 1, {1}},
        {"Missing optional cached flag", MEMORY_SYSTEM, TYPE_REBAR, 0, 32, 64, false, false, 2, 1, {2}},
        {"No host-visible memory", MEMORY_HOST, TYPE_DEVICE, 0, 32, 64, false, false, -1, 0, {0}},
        {"Empty type mask", MEMORY_DEVICE, 0, 0, 32, 64, true, false, -1, 0, {0}},
    };
    respect_budget = true;
    for (unsigned int log = 0; log < 2; log++)
    {
        logging = log;
        for (unsigned int i = 0; i < sizeof(tests) / sizeof(*tests); i++)
            run_case(&tests[i]);
    }
}

int main(int argc, char **argv)
{
    if (argc != 2) return 2;
    if (strcmp(argv[1], "all") && strcmp(argv[1], "locking") && strcmp(argv[1], "candidates"))
    {
        fprintf(stderr, "Unknown test group: %s\n", argv[1]);
        return 2;
    }
    if (!strcmp(argv[1], "all") || !strcmp(argv[1], "locking")) run_locking();
    if (!strcmp(argv[1], "all") || !strcmp(argv[1], "candidates")) run_candidates();
    printf("Memory allocation: %u cases and %u checks passed (%s).\n", cases, checks, argv[1]);
    return 0;
}
'''


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path,
                        default=Path(__file__).resolve().parents[1] / "libs/vkd3d/memory.c")
    parser.add_argument("--revision", help="read memory.c from a Git revision for a negative control")
    parser.add_argument("--build-dir", type=Path)
    parser.add_argument("--generate", type=Path)
    parser.add_argument("--group", choices=("all", "locking", "candidates"), default="all")
    parser.add_argument("--sanitize", action="store_true")
    args = parser.parse_args()
    if not args.generate and not args.build_dir:
        parser.error("either --generate or --build-dir is required")
    source_path = args.source.resolve()
    if args.revision:
        source = subprocess.check_output(["git", "show", args.revision + ":libs/vkd3d/memory.c"],
                                         cwd=source_path.parents[2], text=True)
    else:
        source = source_path.read_text()
    harness = PREAMBLE + "\n".join(extract_function(source, name) for name in (
        "vkd3d_memory_info_type_mask_covers_multiple_memory_heaps",
        "vkd3d_try_allocate_device_memory", "vkd3d_allocate_device_memory")) + TESTS
    if args.generate:
        args.generate.write_text(harness)
        return
    args.build_dir.mkdir(parents=True, exist_ok=True)
    c = args.build_dir / "memory_allocation.c"
    binary = args.build_dir / "memory_allocation"
    c.write_text(harness)
    command = shlex.split(os.environ.get("CC", "cc")) + ["-std=gnu11", "-O1", "-g", "-pthread"]
    if args.sanitize:
        command += ["-fsanitize=address,undefined", "-fno-omit-frame-pointer"]
    subprocess.run(command + [str(c), "-o", str(binary)], check=True)
    subprocess.run([str(binary), args.group], check=True, timeout=10)


if __name__ == "__main__":
    main()
