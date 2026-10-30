#!/usr/bin/env python3
# Copyright 2026 Erhan Bilgili
# SPDX-License-Identifier: LGPL-2.1-or-later
"""Exercise production queue activity tracking and placement without a Vulkan device."""

import argparse
import os
from pathlib import Path
import re
import shlex
import subprocess
import tempfile

ROOT = Path(__file__).resolve().parents[1]


def function(source, name):
    match = re.search(r"^(?:static )?(?:bool|void|unsigned int|struct vkd3d_queue \*)\s*" + name
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
    header = (ROOT / "libs/vkd3d/vkd3d_private.h").read_text()
    threshold = re.search(r"^#define VKD3D_QUEUE_INACTIVE_THRESHOLD_NS .*", header, re.M).group()
    memory = (ROOT / "libs/vkd3d-common/memory.c").read_text()
    functions = function(memory, "vkd3d_array_reserve") + "\n\n" + "\n\n".join(
        function(source, name) for name in (
            "d3d12_command_queue_needs_staggered_submissions",
            "vkd3d_queue_get_active_count",
            "d3d12_device_allocate_vkd3d_queue",
            "d3d12_device_unmap_vkd3d_queue"))
    return HARNESS.replace("@THRESHOLD@", threshold).replace("@FUNCTIONS@", functions)


HARNESS = r'''
#include <assert.h>
#include <pthread.h>
#include <stdbool.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
typedef uint64_t UINT64;
#include "vkd3d_atomic.h"

#define max(a, b) ((a) > (b) ? (a) : (b))
#define VK_QUEUE_GRAPHICS_BIT 1
#define D3D12_COMMAND_LIST_TYPE_DIRECT 0
#define D3D12_COMMAND_LIST_TYPE_COMPUTE 2
#define NO_STAGGERED_SUBMIT 1
#define QUEUE_ACTIVITY 2
#define vkd3d_realloc realloc
#define VKD3D_CONFIG_FLAG_IS_SET(flag) (config_flags & (flag))
@THRESHOLD@

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
    uint64_t last_submission_time_ns;
};

static unsigned int config_flags, lock_calls, unlock_calls, clock_calls;
static uint64_t now;
static unsigned int log_calls;
static char log_output[256];
#define INFO(...) do { assert(lock_calls == unlock_calls); log_calls++; \
        snprintf(log_output, sizeof(log_output), __VA_ARGS__); } while (0)

static uint64_t vkd3d_get_current_time_ns(void)
{
    clock_calls++;
    return now;
}

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
static struct vkd3d_queue native;
static struct d3d12_command_queue commands[2];
static struct d3d12_command_queue *members[] = { &commands[0], &commands[1] };

static void reset(void)
{
    memset(&device, 0, sizeof(device));
    memset(commands, 0, sizeof(commands));
    native.command_queues = members;
    native.command_queue_count = 2;
    native.vk_queue_flags = VK_QUEUE_GRAPHICS_BIT;
    for (unsigned int i = 0; i < 2; i++)
    {
        commands[i].device = &device;
        commands[i].vkd3d_queue = &native;
    }
    now = 10 * VKD3D_QUEUE_INACTIVE_THRESHOLD_NS;
    config_flags = lock_calls = unlock_calls = clock_calls = 0;
}

static void check_activity(bool stagger, unsigned int locks)
{
    assert(d3d12_command_queue_needs_staggered_submissions(&commands[0]) == stagger);
    assert(vkd3d_atomic_uint64_load_explicit(&commands[0].last_submission_time_ns,
            vkd3d_memory_order_relaxed) == now);
    assert(clock_calls == 1);
    assert(lock_calls == locks && unlock_calls == locks);
}

static struct vkd3d_queue graphics[3];
static struct vkd3d_queue *graphics_ptrs[] = { &graphics[0], &graphics[1], &graphics[2] };
static struct vkd3d_queue_family_info family;
static struct d3d12_command_queue clients[16];
static unsigned int client_count;

static void placement_init(unsigned int queue_count, unsigned int flags)
{
    reset();
    memset(graphics, 0, sizeof(graphics));
    memset(clients, 0, sizeof(clients));
    for (unsigned int i = 0; i < 3; i++)
    {
        assert(!pthread_mutex_init(&graphics[i].command_queue_mutex, NULL));
        graphics[i].vk_queue_index = i;
        graphics[i].vk_family_index = 7;
        graphics[i].vk_queue_flags = VK_QUEUE_GRAPHICS_BIT;
    }
    family.queues = graphics_ptrs;
    family.queue_count = queue_count;
    client_count = log_calls = 0;
    config_flags = flags;
}

static struct d3d12_command_queue *allocate(unsigned int expected_index)
{
    unsigned int locks = lock_calls, clocks = clock_calls, logs = log_calls;
    struct d3d12_command_queue *q = &clients[client_count++];
    assert(client_count <= 16);
    q->device = &device;
    q->desc.Priority = 100;
    q->vkd3d_queue = d3d12_device_allocate_vkd3d_queue(&family, q);
    assert(q->vkd3d_queue == &graphics[expected_index]);
    assert(lock_calls - locks == family.queue_count && unlock_calls == lock_calls);
    assert(clock_calls - clocks == !!((config_flags & QUEUE_ACTIVITY) && family.queue_count > 1));
    assert(log_calls - logs == !!(config_flags & QUEUE_ACTIVITY));
    assert(q->vkd3d_queue->command_queues[q->vkd3d_queue->command_queue_count - 1] == q);
    return q;
}

static void placement_finish(void)
{
    for (unsigned int i = 0; i < client_count; i++)
        if (clients[i].vkd3d_queue)
            d3d12_device_unmap_vkd3d_queue(clients[i].vkd3d_queue, &clients[i]);
    for (unsigned int i = 0; i < 3; i++)
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

static void test_disabled(void)
{
    placement_init(3, 0);
    populate();
    assert(!d3d12_command_queue_needs_staggered_submissions(&clients[0]));
    allocate(0);
    placement_finish();
}

static void test_startup(void)
{
    placement_init(3, QUEUE_ACTIVITY);
    now = 1;
    populate();
    assert(vkd3d_queue_get_active_count(&graphics[0], now) == 0);
    allocate(0);
    placement_finish();
}

static void test_busy_and_idle(void)
{
    placement_init(3, QUEUE_ACTIVITY);
    populate();
    assert(!d3d12_command_queue_needs_staggered_submissions(&clients[0]));
    allocate(1);
    assert(strstr(log_output, "type 0, priority 100 to Vulkan queue 1 in family 7"));
    assert(!d3d12_command_queue_needs_staggered_submissions(&clients[6]));
    allocate(2);
    assert(clients[0].vkd3d_queue == &graphics[0]);
    assert(clients[6].vkd3d_queue == &graphics[1]);
    placement_finish();
}

static void test_active_count_precedes_total(void)
{
    placement_init(3, QUEUE_ACTIVITY);
    populate();
    for (unsigned int i = 0; i < 6; i++)
        if (i != 4)
            clients[i].last_submission_time_ns = now;
    allocate(1);
    allocate(1);
    assert(graphics[1].virtual_queue_count == 4 && graphics[0].virtual_queue_count == 2);
    placement_finish();
}

static void test_activity_boundaries(void)
{
    placement_init(3, QUEUE_ACTIVITY);
    populate();
    clients[0].last_submission_time_ns = now - VKD3D_QUEUE_INACTIVE_THRESHOLD_NS;
    clients[1].last_submission_time_ns = now;
    clients[2].last_submission_time_ns = now;
    allocate(0);
    clients[0].last_submission_time_ns++;
    assert(vkd3d_queue_get_active_count(&graphics[0], now) == 1);
    allocate(1);
    placement_finish();
}

static void test_newer_snapshot(void)
{
    placement_init(3, QUEUE_ACTIVITY);
    populate();
    clients[0].last_submission_time_ns = now + 1;
    allocate(1);
    now = UINT64_MAX - 1;
    clients[0].last_submission_time_ns = UINT64_MAX;
    allocate(2);
    placement_finish();
}

static void test_unmap_and_internal(void)
{
    struct vkd3d_queue *reserved;
    unsigned int clocks, logs;
    placement_init(3, QUEUE_ACTIVITY);
    populate();
    clients[0].last_submission_time_ns = now;
    clocks = clock_calls;
    logs = log_calls;
    reserved = d3d12_device_allocate_vkd3d_queue(&family, NULL);
    assert(reserved == &graphics[0]);
    assert(clock_calls == clocks && log_calls == logs);
    assert(vkd3d_queue_get_active_count(reserved, now) == 2);
    d3d12_device_unmap_vkd3d_queue(reserved, &clients[0]);
    clients[0].vkd3d_queue = NULL;
    assert(vkd3d_queue_get_active_count(reserved, now) == 1);
    allocate(1);
    d3d12_device_unmap_vkd3d_queue(reserved, NULL);
    assert(vkd3d_queue_get_active_count(reserved, now) == 0);
    allocate(0);
    placement_finish();
}

static void test_single_queue(void)
{
    placement_init(1, QUEUE_ACTIVITY);
    allocate(0);
    clients[0].last_submission_time_ns = now;
    allocate(0);
    placement_finish();
}

static void test_exclusive_without_staggering(void)
{
    placement_init(3, QUEUE_ACTIVITY | NO_STAGGERED_SUBMIT);
    for (unsigned int i = 0; i < 3; i++)
        allocate(i);
    assert(!d3d12_command_queue_needs_staggered_submissions(&clients[0]));
    allocate(1);
    placement_finish();
}

int main(void)
{
    assert(!pthread_mutex_init(&native.command_queue_mutex, NULL));

    reset();
    native.command_queue_count = 1;
    check_activity(false, 1);

    reset();
    config_flags = NO_STAGGERED_SUBMIT;
    commands[1].desc.Priority = 100;
    commands[1].last_submission_time_ns = now;
    check_activity(false, 0);

    reset();
    device.workarounds.tiler_suspend_resume = true;
    check_activity(false, 0);

    reset();
    commands[1].desc.Priority = 100;
    commands[1].last_submission_time_ns = now;
    check_activity(true, 1);

    reset();
    commands[1].desc.Priority = 100;
    commands[1].last_submission_time_ns = now - VKD3D_QUEUE_INACTIVE_THRESHOLD_NS;
    check_activity(false, 1);

    reset();
    commands[1].desc.Type = D3D12_COMMAND_LIST_TYPE_COMPUTE;
    commands[1].last_submission_time_ns = now;
    check_activity(true, 1);

    assert(!pthread_mutex_destroy(&native.command_queue_mutex));
    test_disabled();
    test_startup();
    test_busy_and_idle();
    test_active_count_precedes_total();
    test_activity_boundaries();
    test_newer_snapshot();
    test_unmap_and_internal();
    test_single_queue();
    test_exclusive_without_staggering();
    puts("Queue activity and placement: 15 cases passed.");
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
    output = args.output_dir or Path(tempfile.mkdtemp(prefix="vkd3d-queue-activity-"))
    output.mkdir(parents=True, exist_ok=True)
    generated, binary = output / "queue_activity.c", output / "queue_activity"
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
