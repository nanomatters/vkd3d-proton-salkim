#!/usr/bin/env python3
# Copyright 2026 Erhan Bilgili
# SPDX-License-Identifier: LGPL-2.1-or-later
"""Exercise production swapchain retirement with allocation faults and real pthreads.

The queue, worker, initialization and teardown functions are extracted unchanged.
Only Vulkan completion and OS latency handles are mocked. --generate emits the
same C harness for Meson. Use --revision f59d367c for an unfixed negative control.
Standalone artifacts are retained under ~/tmp unless --output-dir is supplied.
"""

import argparse
import os
from pathlib import Path
import re
import shlex
import subprocess
import tempfile


ROOT = Path(__file__).resolve().parents[1]
CASES = ("init-oom", "thread-oom", "growth", "growth-oom", "values", "present-wait2", "shutdown-full")


def function(source, name):
    match = re.search(r"^(?:static )?(?:bool|void|HRESULT) \*?" + name + r"\([^;]+?\)\s*\{", source, re.M)
    if not match:
        raise ValueError("Missing source function: " + name)
    end, depth = match.end(), 1
    while depth:
        depth += (source[end] == "{") - (source[end] == "}")
        end += 1
    return source[match.start():end]


HARNESS = r'''
#define _POSIX_C_SOURCE 200809L
#include <assert.h>
#include <errno.h>
#include <inttypes.h>
#include <pthread.h>
#include <stdatomic.h>
#include <stdbool.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <time.h>

#define DXGI_MAX_SWAP_CHAIN_BUFFERS 16
#define S_OK 0
#define E_OUTOFMEMORY ((int)0x8007000e)
#define WARN(...) ((void)0)
#define INFO(...) ((void)0)
#define TRACE(...) ((void)0)
#define VK_CALL(call) (call)
#define VK_STRUCTURE_TYPE_PRESENT_WAIT_2_INFO_KHR 1
#define vkd3d_memory_order_release memory_order_release
#define vkd3d_memory_order_acquire memory_order_acquire
#define vkd3d_atomic_uint64_store_explicit atomic_store_explicit
#define vkd3d_atomic_uint64_load_explicit atomic_load_explicit
#define max(a, b) ((a) > (b) ? (a) : (b))
typedef int HRESULT;
typedef struct { int sType; uint64_t presentId, timeout; } VkPresentWait2InfoKHR;
struct vkd3d_vk_device_procs { int unused; };
struct vkd3d_queue_timeline_trace { int unused; };
struct vkd3d_queue_timeline_trace_cookie { int unused; };
struct mock_device
{
    struct vkd3d_vk_device_procs vk_procs;
    struct vkd3d_queue_timeline_trace queue_timeline_trace;
    int vk_device;
};
struct mock_queue { struct mock_device *device; };
@ENTRY@
struct dxgi_vk_swap_chain
{
    struct mock_queue *queue;
    bool debug_latency;
    int frame_latency_event, frame_latency_event_internal;
    unsigned int frame_latency;
    struct { int lock; } frame_statistics;
    struct { uint64_t begin_frame_time_ns; } request;
    struct
    {
        _Atomic uint64_t present_count;
        uint64_t present_id, internal_blit_count;
        int vk_swapchain;
        bool wait2, present_id_valid, present_target_enabled;
    } present;
    struct
    {
        pthread_t thread;
        struct present_wait_entry *wait_queue;
        size_t wait_queue_size, wait_queue_count;
        pthread_cond_t cond, present_cond;
        pthread_mutex_t lock;
        bool skip_waits;
    } wait_thread;
};

static struct mock_device device;
static struct mock_queue queue = { &device };
static struct dxgi_vk_swap_chain chain;
static pthread_mutex_t state_lock = PTHREAD_MUTEX_INITIALIZER;
static pthread_cond_t state_cond = PTHREAD_COND_INITIALIZER;
static _Atomic bool fail_allocations;
static bool fail_thread_create;
static unsigned int allocations, live_allocations, creates, joins, mutex_inits, mutex_destroys;
static unsigned int cond_inits, cond_destroys, space_waits, cpu_waits, permits, gpu_waits;
static unsigned int releases[3], retired_count, delays;
static uint64_t retired[64], wait_values[64];
static unsigned int wait_kinds[64];
static _Thread_local unsigned int wait_role;

/* State rendezvous are separate from the production queue lock. */
static void await_counter(const unsigned int *counter, unsigned int value)
{
    struct timespec deadline;
    assert(!clock_gettime(CLOCK_REALTIME, &deadline));
    deadline.tv_sec += 3;
    assert(!pthread_mutex_lock(&state_lock));
    while (*counter < value)
        assert(!pthread_cond_timedwait(&state_cond, &state_lock, &deadline));
    assert(!pthread_mutex_unlock(&state_lock));
}

static void allow_completions(unsigned int value)
{
    assert(!pthread_mutex_lock(&state_lock));
    permits = value;
    assert(!pthread_cond_broadcast(&state_cond));
    assert(!pthread_mutex_unlock(&state_lock));
}

static void mock_completion(unsigned int kind, uint64_t value)
{
    unsigned int ticket;
    assert(!pthread_mutex_lock(&state_lock));
    assert(gpu_waits < 64);
    wait_values[gpu_waits] = value;
    wait_kinds[gpu_waits] = kind;
    ticket = ++gpu_waits;
    assert(!pthread_cond_broadcast(&state_cond));
    while (permits < ticket)
        assert(!pthread_cond_wait(&state_cond, &state_lock));
    assert(!pthread_mutex_unlock(&state_lock));
}

static void *mock_realloc(void *ptr, size_t size)
{
    void *result;
    allocations++;
    if (atomic_load(&fail_allocations))
        return NULL;
    result = realloc(ptr, size);
    assert(result);
    if (!ptr)
        live_allocations++;
    return result;
}

static void mock_free(void *ptr)
{
    if (ptr)
    {
        assert(live_allocations);
        live_allocations--;
    }
    free(ptr);
}

static int mock_create(pthread_t *thread, const pthread_attr_t *attr, void *(*entry)(void *), void *arg)
{
    creates++;
    return fail_thread_create ? EAGAIN : pthread_create(thread, attr, entry, arg);
}

static int mock_join(pthread_t thread, void **result)
{
    assert(!pthread_mutex_lock(&state_lock));
    joins++;
    assert(!pthread_cond_broadcast(&state_cond));
    assert(!pthread_mutex_unlock(&state_lock));
    return pthread_join(thread, result);
}

static int mock_cond_wait(pthread_cond_t *cond, pthread_mutex_t *lock)
{
    if (wait_role)
    {
        assert(!pthread_mutex_lock(&state_lock));
        if (wait_role == 1)
            space_waits++;
        else
            cpu_waits++;
        assert(!pthread_cond_broadcast(&state_cond));
        assert(!pthread_mutex_unlock(&state_lock));
    }
    return pthread_cond_wait(cond, lock);
}

static void spinlock_init(int *lock) { *lock = 0; }
static void vkd3d_set_thread_name(const char *name) {}
static bool vkd3d_get_env_var(const char *name, char *value, size_t size) { return false; }
static uint64_t vkd3d_get_current_time_ns(void) { return 1234; }
static void dxgi_vk_swap_chain_drain_complete_semaphore(struct dxgi_vk_swap_chain *c, uint64_t value)
{ mock_completion(1, value); }
static void dxgi_vk_swap_chain_drain_internal_blit_semaphore(struct dxgi_vk_swap_chain *c, uint64_t value)
{ mock_completion(2, value); }
static void vkWaitForPresentKHR(int device, int swapchain, uint64_t id, uint64_t timeout)
{ assert(timeout == UINT64_MAX); mock_completion(3, id); }
static void vkWaitForPresent2KHR(int device, int swapchain, const VkPresentWait2InfoKHR *info)
{ assert(info->timeout == UINT64_MAX); mock_completion(4, info->presentId); }
static struct vkd3d_queue_timeline_trace_cookie vkd3d_queue_timeline_trace_register_present_wait(
        struct vkd3d_queue_timeline_trace *trace, uint64_t id)
{ return (struct vkd3d_queue_timeline_trace_cookie){0}; }
static void vkd3d_queue_timeline_trace_complete_present_wait(struct vkd3d_queue_timeline_trace *trace,
        struct vkd3d_queue_timeline_trace_cookie cookie) {}
static void dxgi_vk_swap_chain_delay_next_frame(struct dxgi_vk_swap_chain *c, uint64_t now) { delays++; }
static void dxgi_vk_swap_chain_update_frame_statistics(struct dxgi_vk_swap_chain *c, uint64_t count, uint64_t id)
{ assert(retired_count < 64); retired[retired_count++] = count; }
static bool vkd3d_native_sync_handle_is_valid(int handle) { return handle != 0; }
static int vkd3d_native_sync_handle_release(int handle, unsigned int value)
{ assert(value == 1); releases[handle]++; return 0; }

#define vkd3d_realloc mock_realloc
#define vkd3d_free mock_free
#define pthread_create mock_create
#define pthread_join mock_join
#define pthread_cond_wait mock_cond_wait
#define pthread_mutex_init(p, a) (mutex_inits++, pthread_mutex_init(p, a))
#define pthread_mutex_destroy(p) (mutex_destroys++, pthread_mutex_destroy(p))
#define pthread_cond_init(p, a) (cond_inits++, pthread_cond_init(p, a))
#define pthread_cond_destroy(p) (cond_destroys++, pthread_cond_destroy(p))
@FUNCTIONS@
#undef pthread_create
#undef pthread_join
#undef pthread_cond_wait
#undef pthread_mutex_init
#undef pthread_mutex_destroy
#undef pthread_cond_init
#undef pthread_cond_destroy

struct job { uint64_t count; bool cleanup, cpu; unsigned int done; };

static void *run_job(void *arg)
{
    struct job *job = arg;
    wait_role = job->cpu ? 2 : 1;
    if (job->cleanup)
        dxgi_vk_swap_chain_cleanup_waiter_thread(&chain);
    else if (job->cpu)
        dxgi_vk_swap_chain_wait_for_present_count(&chain, job->count);
    else
        dxgi_vk_swap_chain_push_present_id(&chain, job->count, 0, 0, true, UINT64_MAX);
    assert(!pthread_mutex_lock(&state_lock));
    job->done = 1;
    assert(!pthread_cond_broadcast(&state_cond));
    assert(!pthread_mutex_unlock(&state_lock));
    return NULL;
}

static void initialize(void)
{
    chain.queue = &queue;
    chain.frame_latency_event = 1;
    chain.frame_latency_event_internal = 2;
    assert(dxgi_vk_swap_chain_init_waiter_thread(&chain) == S_OK);
    assert(chain.wait_thread.wait_queue_size >= DXGI_MAX_SWAP_CHAIN_BUFFERS);
}

static void fill_queue(void)
{
    unsigned int i;
    initialize();
    for (i = 1; i <= DXGI_MAX_SWAP_CHAIN_BUFFERS; i++)
        dxgi_vk_swap_chain_push_present_id(&chain, i, 0, i * 100, true, UINT64_MAX);
    await_counter(&gpu_waits, 1);
    assert(!pthread_mutex_lock(&chain.wait_thread.lock));
    assert(chain.wait_thread.wait_queue_count == DXGI_MAX_SWAP_CHAIN_BUFFERS);
    assert(chain.wait_thread.wait_queue[0].present_count == 1);
    assert(!pthread_mutex_unlock(&chain.wait_thread.lock));
}

static void finish(unsigned int count)
{
    unsigned int i;
    allow_completions(64);
    dxgi_vk_swap_chain_cleanup_waiter_thread(&chain);
    assert(retired_count == count);
    for (i = 0; i < count; i++)
        assert(retired[i] == i + 1);
    assert(releases[1] == count && releases[2] == count);
    assert(!live_allocations);
}

int main(int argc, char **argv)
{
    struct job producer = {17, false, false, 0}, cpu = {17, false, true, 0};
    struct job cleanup = {0, true, false, 0};
    pthread_t producer_thread, cpu_thread;
    struct present_wait_entry *storage;
    unsigned int i;

    assert(argc == 2);
    if (!strcmp(argv[1], "init-oom"))
    {
        atomic_store(&fail_allocations, true);
        assert(dxgi_vk_swap_chain_init_waiter_thread(&chain) == E_OUTOFMEMORY);
        assert(!creates && !mutex_inits && !cond_inits && !live_allocations);
    }
    else if (!strcmp(argv[1], "thread-oom"))
    {
        fail_thread_create = true;
        assert(dxgi_vk_swap_chain_init_waiter_thread(&chain) == E_OUTOFMEMORY);
        assert(creates == 1 && !live_allocations);
        assert(mutex_inits == 1 && mutex_destroys == 1 && cond_inits == 2 && cond_destroys == 2);
    }
    else if (!strcmp(argv[1], "growth"))
    {
        fill_queue();
        dxgi_vk_swap_chain_push_present_id(&chain, 17, 0, 1700, true, UINT64_MAX);
        assert(!pthread_mutex_lock(&chain.wait_thread.lock));
        assert(chain.wait_thread.wait_queue_size > DXGI_MAX_SWAP_CHAIN_BUFFERS);
        assert(chain.wait_thread.wait_queue_count == 17 && allocations == 2);
        for (i = 0; i < 17; i++)
        {
            assert(chain.wait_thread.wait_queue[i].present_count == i + 1);
            assert(chain.wait_thread.wait_queue[i].begin_frame_time_ns == (i + 1) * 100);
        }
        assert(!pthread_mutex_unlock(&chain.wait_thread.lock));
        finish(17);
    }
    else if (!strcmp(argv[1], "growth-oom"))
    {
        fill_queue();
        storage = chain.wait_thread.wait_queue;
        atomic_store(&fail_allocations, true);
        assert(!pthread_create(&producer_thread, NULL, run_job, &producer));
        await_counter(&space_waits, 1);
        assert(!pthread_create(&cpu_thread, NULL, run_job, &cpu));
        await_counter(&cpu_waits, 1);
        assert(!pthread_mutex_lock(&chain.wait_thread.lock));
        assert(chain.wait_thread.wait_queue_count == 16);
        assert(chain.wait_thread.wait_queue[0].present_count == 1);
        assert(atomic_load(&chain.present.present_count) == 16);
        /* Wake without freeing space: the producer must recheck its predicate. */
        assert(!pthread_cond_broadcast(&chain.wait_thread.cond));
        assert(!pthread_mutex_unlock(&chain.wait_thread.lock));
        await_counter(&space_waits, 2);
        allow_completions(1);
        await_counter(&producer.done, 1);
        await_counter(&cpu.done, 1);
        assert(!pthread_join(producer_thread, NULL));
        assert(!pthread_join(cpu_thread, NULL));
        await_counter(&gpu_waits, 2);
        assert(!pthread_mutex_lock(&chain.wait_thread.lock));
        assert(chain.wait_thread.wait_queue == storage && chain.wait_thread.wait_queue_size == 16);
        assert(chain.wait_thread.wait_queue_count == 16 && allocations == 2);
        assert(chain.wait_thread.wait_queue[0].present_count == 2);
        assert(chain.wait_thread.wait_queue[15].present_count == 17);
        assert(atomic_load(&chain.present.present_count) == 17);
        assert(!pthread_mutex_unlock(&chain.wait_thread.lock));
        finish(17);
    }
    else if (!strcmp(argv[1], "values") || !strcmp(argv[1], "present-wait2"))
    {
        initialize();
        chain.present.wait2 = !strcmp(argv[1], "present-wait2");
        chain.request.begin_frame_time_ns = 99;
        dxgi_vk_swap_chain_signal_waitable_handle(&chain, 1, true);
        await_counter(&gpu_waits, 1);
        chain.present.present_id_valid = true;
        chain.present.present_id = 303;
        chain.present.internal_blit_count = 41;
        dxgi_vk_swap_chain_signal_waitable_handle(&chain, 2, false);
        chain.present.present_target_enabled = true;
        dxgi_vk_swap_chain_signal_waitable_handle(&chain, 3, true);
        assert(!pthread_mutex_lock(&chain.wait_thread.lock));
        assert(chain.wait_thread.wait_queue_count == 3);
        assert(chain.wait_thread.wait_queue[1].id == 0 && chain.wait_thread.wait_queue[1].blit_count == 41);
        assert(chain.wait_thread.wait_queue[2].id == 303 && chain.wait_thread.wait_queue[2].blit_count == UINT64_MAX);
        assert(chain.wait_thread.wait_queue[2].present_timing_enabled);
        assert(chain.wait_thread.wait_queue[2].begin_frame_time_ns == 99);
        assert(!pthread_mutex_unlock(&chain.wait_thread.lock));
        finish(3);
        assert(gpu_waits == 3 && delays == 1);
        assert(wait_kinds[0] == 1 && wait_values[0] == 1);
        assert(wait_kinds[1] == 2 && wait_values[1] == 41);
        assert(wait_kinds[2] == (chain.present.wait2 ? 4 : 3) && wait_values[2] == 303);
    }
    else if (!strcmp(argv[1], "shutdown-full"))
    {
        fill_queue();
        atomic_store(&fail_allocations, true);
        assert(!pthread_create(&producer_thread, NULL, run_job, &cleanup));
        await_counter(&space_waits, 1);
        allow_completions(1);
        await_counter(&joins, 1);
        await_counter(&gpu_waits, 2);
        assert(!pthread_mutex_lock(&chain.wait_thread.lock));
        assert(chain.wait_thread.wait_queue_count == 16);
        assert(chain.wait_thread.wait_queue[15].present_count == 0);
        assert(atomic_load(&chain.present.present_count) == 16);
        assert(!pthread_mutex_unlock(&chain.wait_thread.lock));
        assert(!pthread_mutex_lock(&state_lock));
        assert(!cleanup.done);
        assert(!pthread_mutex_unlock(&state_lock));
        allow_completions(64);
        assert(!pthread_join(producer_thread, NULL));
        assert(retired_count == 16 && releases[1] == 16 && releases[2] == 16);
        for (i = 0; i < 16; i++)
            assert(retired[i] == i + 1);
        assert(!live_allocations);
    }
    else
        abort();
    printf("PASS %s\n", argv[1]);
    return 0;
}
'''


def generate_harness(revision=None):
    def read(path):
        if revision:
            return subprocess.check_output(["git", "show", f"{revision}:{path}"], cwd=ROOT, text=True)
        return (ROOT / path).read_text()

    source = read("libs/vkd3d/swapchain.c")
    entry = re.search(r"struct present_wait_entry\s*\{.*?\n\};", source, re.S)
    if not entry:
        raise ValueError("Missing present_wait_entry")
    functions = [function(read("libs/vkd3d-common/memory.c"), "vkd3d_array_reserve")]
    functions += [function(source, name) for name in (
        "dxgi_vk_swap_chain_push_present_id", "dxgi_vk_swap_chain_wait_for_present_count",
        "dxgi_vk_swap_chain_signal_waitable_handle", "dxgi_vk_swap_chain_wait_worker",
        "dxgi_vk_swap_chain_init_waiter_thread", "dxgi_vk_swap_chain_cleanup_waiter_thread")]
    return HARNESS.replace("@ENTRY@", entry.group()).replace("@FUNCTIONS@", "\n\n".join(functions))


def run(options, directory):
    source = directory / "swapchain_wait_queue.c"
    binary = directory / "swapchain_wait_queue"
    source.write_text(generate_harness(options.revision))
    command = shlex.split(os.environ.get("CC", "cc")) + [
        "-std=c11", "-O1", "-g", "-pthread", "-UNDEBUG", "-Wall", "-Wextra", "-Werror",
        "-Wno-unused-variable", "-Wno-unused-parameter", "-Wno-unused-function"]
    if options.sanitize:
        command += ["-fsanitize=address,undefined", "-fno-omit-frame-pointer", "-no-pie"]
    subprocess.run(command + [str(source), "-o", str(binary)], check=True, timeout=30)
    failed = []
    for case in options.cases or CASES:
        try:
            result = subprocess.run([str(binary), case], timeout=10)
            if result.returncode:
                failed.append(case)
        except subprocess.TimeoutExpired:
            failed.append(case)
    if failed:
        raise SystemExit("Failed cases: " + ", ".join(failed))


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--generate", type=Path)
    parser.add_argument("--revision")
    parser.add_argument("--output-dir", type=Path)
    parser.add_argument("--sanitize", action="store_true")
    parser.add_argument("cases", nargs="*", choices=CASES)
    args = parser.parse_args()
    if args.generate:
        args.generate.write_text(generate_harness(args.revision))
    elif args.output_dir:
        args.output_dir.mkdir(parents=True, exist_ok=True)
        run(args, args.output_dir)
    else:
        base = Path.home() / "tmp"
        base.mkdir(parents=True, exist_ok=True)
        directory = Path(tempfile.mkdtemp(prefix="swapchain-wait-queue-", dir=base))
        print("Retained harness:", directory, flush=True)
        run(args, directory)
