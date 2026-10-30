#!/usr/bin/env python3
# Copyright 2026 Erhan Bilgili
# SPDX-License-Identifier: LGPL-2.1-or-later
"""Exercise production queue placement without a Vulkan device."""

import argparse
import os
from pathlib import Path
import re
import shlex
import subprocess
import tempfile

ROOT = Path(__file__).resolve().parents[1]


def function(source, name):
    match = re.search(r"^(?:static )?(?:bool|void|unsigned int|uint32_t|HRESULT|struct vkd3d_queue \*)\s*" + name
                      + r"\([^;]+?\)\s*\{", source, re.M)
    if not match:
        raise ValueError("Missing source function: " + name)
    end, depth = match.end(), 1
    while depth:
        depth += (source[end] == "{") - (source[end] == "}")
        end += 1
    return source[match.start():end]


def generate(source_path):
    source = source_path.read_text()
    memory = (ROOT / "libs/vkd3d-common/memory.c").read_text()
    functions = function(memory, "vkd3d_array_reserve") + "\n\n" + "\n\n".join(
        function(source, name) for name in (
            "d3d12_device_allocate_vkd3d_queue",
            "d3d12_device_unmap_vkd3d_queue"))
    return HARNESS.replace("@FUNCTIONS@", functions)


HARNESS = r'''
#include <assert.h>
#include <pthread.h>
#include <stdbool.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

#define max(a, b) ((a) > (b) ? (a) : (b))
#define VK_QUEUE_GRAPHICS_BIT 1
#define vkd3d_realloc realloc

struct d3d12_command_queue;
struct vkd3d_queue
{
    pthread_mutex_t command_queue_mutex;
    struct d3d12_command_queue **command_queues;
    size_t command_queue_count, command_queue_size;
    unsigned int virtual_queue_count, vk_queue_index, vk_family_index;
    unsigned int vk_queue_flags;
};
struct vkd3d_queue_family_info
{
    struct vkd3d_queue **queues;
    unsigned int queue_count;
};
struct d3d12_device { struct { bool tiler_suspend_resume; } workarounds; };
struct d3d12_command_queue
{
    struct d3d12_device *device;
    struct vkd3d_queue *vkd3d_queue;
    struct { unsigned int Type; int32_t Priority; } desc;
};

static unsigned int lock_calls, unlock_calls;

static int record_lock(pthread_mutex_t *lock)
{
    lock_calls++;
    return pthread_mutex_lock(lock);
}

static int record_unlock(pthread_mutex_t *lock)
{
    unlock_calls++;
    return pthread_mutex_unlock(lock);
}

#define pthread_mutex_lock record_lock
#define pthread_mutex_unlock record_unlock
@FUNCTIONS@
#undef pthread_mutex_lock
#undef pthread_mutex_unlock

static struct d3d12_device device;
static struct vkd3d_queue graphics[7];
static struct vkd3d_queue *graphics_ptrs[7];
static struct vkd3d_queue_family_info family;
static struct d3d12_command_queue clients[16];
static unsigned int client_count;

static void placement_init(unsigned int queue_count)
{
    memset(&device, 0, sizeof(device));
    lock_calls = unlock_calls = 0;
    memset(graphics, 0, sizeof(graphics));
    memset(clients, 0, sizeof(clients));
    for (unsigned int i = 0; i < sizeof(graphics) / sizeof(graphics[0]); i++)
    {
        assert(!pthread_mutex_init(&graphics[i].command_queue_mutex, NULL));
        graphics_ptrs[i] = &graphics[i];
        graphics[i].vk_queue_index = i;
        graphics[i].vk_family_index = 7;
        graphics[i].vk_queue_flags = VK_QUEUE_GRAPHICS_BIT;
    }
    family.queues = graphics_ptrs;
    family.queue_count = queue_count;
    client_count = 0;
}

static struct d3d12_command_queue *allocate(unsigned int expected_index)
{
    unsigned int locks = lock_calls;
    struct d3d12_command_queue *q = &clients[client_count++];
    assert(client_count <= 16);
    q->device = &device;
    q->desc.Priority = 100;
    q->vkd3d_queue = d3d12_device_allocate_vkd3d_queue(&family, q);
    assert(q->vkd3d_queue == &graphics[expected_index]);
    assert(lock_calls - locks == family.queue_count && unlock_calls == lock_calls);
    assert(q->vkd3d_queue->command_queues[q->vkd3d_queue->command_queue_count - 1] == q);
    return q;
}

static void placement_finish(void)
{
    for (unsigned int i = 0; i < client_count; i++)
        if (clients[i].vkd3d_queue)
            d3d12_device_unmap_vkd3d_queue(clients[i].vkd3d_queue, &clients[i]);
    for (unsigned int i = 0; i < sizeof(graphics) / sizeof(graphics[0]); i++)
    {
        assert(!graphics[i].virtual_queue_count && !graphics[i].command_queue_count);
        free(graphics[i].command_queues);
        assert(!pthread_mutex_destroy(&graphics[i].command_queue_mutex));
    }
}

static void populate(void)
{
    for (unsigned int i = 0; i < 6; i++)
        allocate(i % 3);
}

static void test_default_placement(void)
{
    placement_init(3);
    populate();
    allocate(0);
    placement_finish();
}

static void test_single_queue(void)
{
    placement_init(1);
    allocate(0);
    allocate(0);
    placement_finish();
}

static void test_fifth_direct_queue(void)
{
    struct d3d12_command_queue *present;
    placement_init(7);
    for (unsigned int i = 0; i < 4; i++)
        allocate(i)->desc.Priority = 0;
    present = allocate(4);
    assert(present->vkd3d_queue != clients[0].vkd3d_queue);
    assert(present->desc.Priority == 100);
    placement_finish();
}

int main(void)
{
    test_default_placement();
    test_single_queue();
    test_fifth_direct_queue();
    puts("Queue placement: 3 cases passed.");
    return 0;
}
'''


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, default=ROOT / "libs/vkd3d/command.c")
    parser.add_argument("--generate", type=Path)
    parser.add_argument("--output-dir", type=Path)
    parser.add_argument("--sanitize", action="store_true")
    args = parser.parse_args()
    source = generate(args.source)
    if args.generate:
        args.generate.write_text(source)
        return
    output = args.output_dir or Path(tempfile.mkdtemp(prefix="vkd3d-queue-placement-"))
    output.mkdir(parents=True, exist_ok=True)
    generated, binary = output / "queue_placement.c", output / "queue_placement"
    generated.write_text(source)
    cc = shlex.split(os.environ.get("CC", "cc"))
    flags = ["-std=c11", "-O2", "-g", "-UNDEBUG", "-Wall", "-Wextra", "-Werror", "-pthread"]
    if args.sanitize:
        flags += ["-fsanitize=address,undefined", "-fno-omit-frame-pointer", "-fno-pie", "-no-pie"]
    subprocess.run(cc + flags + ["-I" + str(ROOT / "include/private"),
                               str(generated), "-o", str(binary)], check=True)
    subprocess.run([str(binary)], check=True, timeout=15)


if __name__ == "__main__":
    main()
