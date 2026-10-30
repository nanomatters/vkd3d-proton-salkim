#!/usr/bin/env python3
# Copyright 2026 Erhan Bilgili
# SPDX-License-Identifier: LGPL-2.1-or-later
"""Exercise production queue activity tracking without a Vulkan device."""

import argparse
import os
from pathlib import Path
import re
import shlex
import subprocess
import tempfile

ROOT = Path(__file__).resolve().parents[1]


def function(source, name):
    match = re.search(r"^(?:static )?(?:bool|void|struct vkd3d_queue \*)\s*" + name
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
    functions = function(source, "d3d12_command_queue_needs_staggered_submissions")
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
#define VKD3D_CONFIG_FLAG_IS_SET(flag) (config_flags & (flag))
@THRESHOLD@

struct d3d12_command_queue;
struct vkd3d_queue
{
    pthread_mutex_t command_queue_mutex;
    struct d3d12_command_queue **command_queues;
    size_t command_queue_count;
    unsigned int vk_queue_flags;
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
    puts("Queue activity: 6 cases passed.");
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
