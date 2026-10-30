#!/usr/bin/env python3
# Copyright 2026 Erhan Bilgili
# SPDX-License-Identifier: LGPL-2.1-or-later
"""Exercise production Vulkan queue selection with synthetic device properties."""

import argparse
import os
from pathlib import Path
import re
import shlex
import subprocess
import tempfile

from test_queue_activity import function

ROOT = Path(__file__).resolve().parents[1]


def generate(source_path):
    source = source_path.read_text()
    header = (ROOT / "libs/vkd3d/vkd3d_private.h").read_text()
    families = re.search(r"enum vkd3d_queue_family\n\{.*?\n\};", header, re.S).group()
    info = re.search(r"struct vkd3d_device_queue_info\n\{.*?\n\};", source, re.S).group()
    priorities = source[source.index("#define VKD3D_MAX_QUEUE_COUNT_PER_FAMILY"):
                        source.index("static uint32_t vkd3d_find_queue(")]
    functions = "\n\n".join(function(source, name) for name in (
        "vkd3d_queue_family_needs_out_of_band_queue", "vkd3d_find_queue", "vkd3d_select_queues"))
    return "\n\n".join((PREFIX, families, info, priorities, functions, TESTS))


PREFIX = r'''
#include <assert.h>
#include <stdbool.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <vulkan/vulkan_core.h>

#define ARRAY_SIZE(a) (sizeof(a) / sizeof((a)[0]))
#define STATIC_ASSERT(e) _Static_assert(e, #e)
#define min(a, b) ((a) < (b) ? (a) : (b))
#define S_OK 0
#define E_FAIL (-1)
#define E_OUTOFMEMORY (-2)
#define SINGLE_QUEUE 1
#define VKD3D_CONFIG_FLAG_IS_SET(flag) (config_flags & (flag))
#define VK_CALL(f) vk_procs->p_##f
#define ERR(...) ((void)0)
#define vkd3d_calloc calloc
#define vkd3d_free free

typedef int32_t HRESULT;
struct vkd3d_vk_instance_procs
{
    PFN_vkGetPhysicalDeviceQueueFamilyProperties p_vkGetPhysicalDeviceQueueFamilyProperties;
};
struct vkd3d_instance { struct vkd3d_vk_instance_procs vk_procs; };
struct d3d12_device
{
    struct vkd3d_instance *vkd3d_instance;
    struct { bool NV_low_latency2, NV_optical_flow; } vk_info;
};

static unsigned int config_flags, property_count;
static VkQueueFamilyProperties properties[5];

static void VKAPI_CALL query_properties(VkPhysicalDevice device, uint32_t *count,
        VkQueueFamilyProperties *out)
{
    (void)device;
    if (out)
    {
        assert(*count >= property_count);
        memcpy(out, properties, property_count * sizeof(*out));
    }
    *count = property_count;
}
'''

TESTS = r'''
static struct vkd3d_device_queue_info select_queues(unsigned int graphics_count,
        bool single, bool reflex, bool shared)
{
    struct vkd3d_instance instance = {{query_properties}};
    struct d3d12_device device = {&instance, {reflex, !shared}};
    struct vkd3d_device_queue_info info;
    const VkQueueFlags flags[] = {
        VK_QUEUE_GRAPHICS_BIT | VK_QUEUE_COMPUTE_BIT | VK_QUEUE_TRANSFER_BIT | VK_QUEUE_SPARSE_BINDING_BIT,
        VK_QUEUE_COMPUTE_BIT | VK_QUEUE_TRANSFER_BIT | VK_QUEUE_SPARSE_BINDING_BIT,
        VK_QUEUE_TRANSFER_BIT,
        VK_QUEUE_SPARSE_BINDING_BIT,
        VK_QUEUE_OPTICAL_FLOW_BIT_NV,
    };

    memset(properties, 0, sizeof(properties));
    property_count = shared ? 1 : ARRAY_SIZE(properties);
    config_flags = single ? SINGLE_QUEUE : 0;
    for (unsigned int i = 0; i < property_count; i++)
    {
        properties[i].queueFlags = flags[i];
        properties[i].queueCount = i ? 16 : graphics_count;
        properties[i].minImageTransferGranularity = (VkExtent3D){1, 1, 1};
    }
    assert(vkd3d_select_queues(&device, VK_NULL_HANDLE, &info) == S_OK);
    for (unsigned int i = 0; i < info.vk_family_count; i++)
    {
        const VkDeviceQueueCreateInfo *q = &info.vk_queue_create_info[i];
        assert(q->queueCount && q->queueCount <= properties[q->queueFamilyIndex].queueCount);
        assert(q->queueCount <= ARRAY_SIZE(queue_priorities));
        for (unsigned int j = 0; j < q->queueCount; j++)
            assert(q->pQueuePriorities[j] == 1.0f);
    }
    return info;
}

int main(void)
{
    const struct { unsigned int reported, normal, reflex; } cases[] = {
        {1, 1, 1}, {2, 2, 2}, {4, 4, 4}, {5, 5, 5},
        {6, 6, 6}, {7, 7, 7}, {8, 7, 8}, {16, 7, 8},
    };
    unsigned int count = 0;

    for (unsigned int i = 0; i < ARRAY_SIZE(cases); i++)
    {
        for (unsigned int reflex = 0; reflex < 2; reflex++)
        {
            struct vkd3d_device_queue_info info = select_queues(cases[i].reported, false, reflex, false);
            assert(info.vk_family_count == 5);
            for (unsigned int j = 0; j < info.vk_family_count; j++)
            {
                const VkDeviceQueueCreateInfo *q = &info.vk_queue_create_info[j];
                unsigned int expected = q->queueFamilyIndex == 0 ?
                        (reflex ? cases[i].reflex : cases[i].normal) : 4;
                if (reflex && q->queueFamilyIndex == 1)
                    expected++;
                assert(q->queueCount == expected);
            }
            count++;
        }
    }
    for (unsigned int reflex = 0; reflex < 2; reflex++)
    {
        struct vkd3d_device_queue_info info = select_queues(16, true, reflex, false);
        for (unsigned int i = 0; i < info.vk_family_count; i++)
        {
            const VkDeviceQueueCreateInfo *q = &info.vk_queue_create_info[i];
            assert(q->queueCount == (q->queueFamilyIndex == 0 && reflex ? 2u : 1u));
        }
        count++;
        info = select_queues(16, false, reflex, true);
        assert(info.vk_family_count == 1);
        assert(info.vk_queue_create_info[0].queueCount == (reflex ? 8u : 7u));
        count++;
    }
    printf("Queue selection: %u cases passed.\n", count);
    return 0;
}
'''


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, default=ROOT / "libs/vkd3d/device.c")
    parser.add_argument("--generate", type=Path)
    parser.add_argument("--output-dir", type=Path)
    parser.add_argument("--sanitize", action="store_true")
    args = parser.parse_args()
    source = generate(args.source)
    if args.generate:
        args.generate.write_text(source)
        return
    output = args.output_dir or Path(tempfile.mkdtemp(prefix="vkd3d-queue-selection-"))
    output.mkdir(parents=True, exist_ok=True)
    generated, binary = output / "queue_selection.c", output / "queue_selection"
    generated.write_text(source)
    cc = shlex.split(os.environ.get("CC", "cc"))
    flags = ["-std=c11", "-O2", "-g", "-UNDEBUG", "-Wall", "-Wextra", "-Werror"]
    if args.sanitize:
        flags += ["-fsanitize=address,undefined", "-fno-omit-frame-pointer", "-fno-pie", "-no-pie"]
    subprocess.run(cc + flags + ["-I" + str(ROOT / "khronos/Vulkan-Headers/include"),
                               str(generated), "-o", str(binary)], check=True)
    subprocess.run([str(binary)], check=True, timeout=15)


if __name__ == "__main__":
    main()
