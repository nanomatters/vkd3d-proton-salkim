#!/usr/bin/env python3
# Copyright 2026 Erhan Bilgili
# SPDX-License-Identifier: LGPL-2.1-or-later

"""Compile the actual Reflex functions against rendezvous-controlled Vulkan mocks.

Run normally with python3 tests/test_reflex_lifetime.py. Set
REFLEX_TEST_SANITIZE=1 to enable ASan and UBSan. REFLEX_TEST_SOURCE can point
at an older swapchain.c for a negative control. For the Windows lock path,
use CC='x86_64-w64-mingw32-gcc -static' and REFLEX_TEST_RUNNER=wine.
Meson generates the same harness and compiles it for the configured target.
"""

import argparse
import os
from pathlib import Path
import re
import shlex
import subprocess
import sys
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[1]


def function(source, name):
    match = re.search(r"^(?:static )?(?:inline )?(?:HRESULT|void|int) " + name + r"\(", source, re.M)
    if not match:
        raise AssertionError("Missing source function: " + name)
    start = source.index("{", match.start())
    depth = 1
    end = start + 1
    while depth:
        depth += (source[end] == "{") - (source[end] == "}")
        end += 1
    return source[match.start():end]


HARNESS = r"""
#ifndef _GNU_SOURCE
#define _GNU_SOURCE
#endif
#include <assert.h>
#include <errno.h>
#include <inttypes.h>
#include <stdbool.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <time.h>

#ifdef _WIN32
#define WIN32_LEAN_AND_MEAN
#define NOMINMAX
#include <windows.h>
#define TRACE(...) ((void)0)
#define vkd3d_calloc calloc
#define vkd3d_free free
@WINTHREADS@
#define PTHREAD_COND_INITIALIZER {CONDITION_VARIABLE_INIT}

static int pthread_mutex_trylock(pthread_mutex_t *mutex)
{
    return !TryAcquireSRWLockExclusive(&mutex->lock);
}
static int try_rwlock_write(rwlock_t *lock)
{
    return !TryAcquireSRWLockExclusive(&lock->rwlock);
}
#else
#include <pthread.h>
@POSIXRWLOCK@

typedef int HRESULT;
static int try_rwlock_write(rwlock_t *lock)
{
    return pthread_rwlock_trywrlock(&lock->rwlock);
}
#endif

typedef int VkResult;
typedef uint64_t VkSemaphore;
typedef uint64_t VkSwapchainKHR;
typedef int VkLatencyMarkerNV;
#define VK_SUCCESS 0
#define VK_ERROR_DEVICE_LOST -4
#define VK_STRUCTURE_TYPE_LATENCY_SLEEP_INFO_NV 1
#define VK_STRUCTURE_TYPE_SEMAPHORE_WAIT_INFO 2
#define VK_STRUCTURE_TYPE_SET_LATENCY_MARKER_INFO_NV 3
#define VK_STRUCTURE_TYPE_GET_LATENCY_MARKER_INFO_NV 4
#define VK_STRUCTURE_TYPE_LATENCY_TIMINGS_FRAME_REPORT_NV 5
#define VK_LATENCY_MARKER_OUT_OF_BAND_PRESENT_START_NV 1
#define VK_LATENCY_MARKER_OUT_OF_BAND_PRESENT_END_NV 2
#define VK_LATENCY_MARKER_PRESENT_START_NV 3
#define VK_CALL(call) (call)
#define ARRAY_SIZE(a) (sizeof(a) / sizeof((a)[0]))
#define min(a, b) ((a) < (b) ? (a) : (b))
#define ERR(...) ((void)0)
#define INFO(...) ((void)0)
#define vkd3d_memory_order_release 0
#define vkd3d_atomic_uint64_store_explicit(p, v, order) (*(p) = (v))

typedef struct
{
    int sType;
    const void *pNext;
    VkSemaphore signalSemaphore;
    uint64_t value;
} VkLatencySleepInfoNV;
typedef struct
{
    int sType;
    const void *pNext;
    unsigned int flags, semaphoreCount;
    const VkSemaphore *pSemaphores;
    const uint64_t *pValues;
} VkSemaphoreWaitInfo;
typedef struct
{
    int sType;
    const void *pNext;
    uint64_t presentID;
    VkLatencyMarkerNV marker;
} VkSetLatencyMarkerInfoNV;
#define REPORT_FIELDS(X) \
    X(inputSampleTime) X(simStartTime) X(simEndTime) \
    X(renderSubmitStartTime) X(renderSubmitEndTime) \
    X(presentStartTime) X(presentEndTime) X(driverStartTime) X(driverEndTime) \
    X(osRenderQueueStartTime) X(osRenderQueueEndTime) \
    X(gpuRenderStartTime) X(gpuRenderEndTime)
#define VK_FIELD(name) uint64_t name##Us;
typedef struct
{
    int sType;
    uint64_t presentID;
    REPORT_FIELDS(VK_FIELD)
} VkLatencyTimingsFrameReportNV;
typedef struct
{
    int sType;
    unsigned int timingCount;
    VkLatencyTimingsFrameReportNV *pTimings;
} VkGetLatencyMarkerInfoNV;
#define D3D_FIELD(name) uint64_t name;
typedef struct
{
    uint64_t frameID;
    REPORT_FIELDS(D3D_FIELD)
    uint64_t gpuActiveRenderTimeUs, gpuFrameTimeUs;
} D3D12_FRAME_REPORT;
typedef struct { D3D12_FRAME_REPORT frame_reports[64]; } D3D12_LATENCY_RESULTS;
struct vkd3d_vk_device_procs { int unused; };
struct vkd3d_queue_timeline_trace { bool active; };
struct vkd3d_queue_timeline_trace_cookie { int unused; };
struct mock_device
{
    struct vkd3d_vk_device_procs vk_procs;
    struct vkd3d_queue_timeline_trace queue_timeline_trace;
    int vk_device;
    struct { uint64_t out_of_band_present; } frame_markers;
};
struct mock_queue { struct mock_device *device; };
struct dxgi_vk_swap_chain
{
    struct mock_queue *queue;
    bool debug_latency;
    struct
    {
        @LOCKTYPE@ low_latency_swapchain_lock;
        pthread_mutex_t low_latency_sleep_lock;
        VkSwapchainKHR vk_swapchain;
        VkSemaphore low_latency_sem;
        uint64_t low_latency_sem_value;
    } present;
};

static pthread_mutex_t state_lock = PTHREAD_MUTEX_INITIALIZER;
static pthread_cond_t state_cond = PTHREAD_COND_INITIALIZER;
static struct dxgi_vk_swap_chain chain;
static unsigned int native_calls, wait_calls, marker_calls, timing_calls, second_attempts;
static uint64_t request_values[4], wait_values[4];
static bool block_native, block_wait, block_marker, block_timing;
static bool release_native, release_wait, release_marker, release_timing;
static VkResult request_result;
#ifdef _MSC_VER
static __declspec(thread) bool second_sleep;
#else
static _Thread_local bool second_sleep;
#endif

static void signal_state(void)
{
    assert(!pthread_cond_broadcast(&state_cond));
}

static void wait_until(const unsigned int *counter, unsigned int target)
{
#ifdef _WIN32
    ULONGLONG deadline = GetTickCount64() + 2000;
    ULONGLONG now;
    assert(!pthread_mutex_lock(&state_lock));
    while (*counter < target)
    {
        now = GetTickCount64();
        assert(now < deadline);
        assert(SleepConditionVariableSRW(&state_cond.cond, &state_lock.lock, deadline - now, 0));
    }
#else
    struct timespec deadline;
    assert(!clock_gettime(CLOCK_REALTIME, &deadline));
    deadline.tv_sec += 2;
    assert(!pthread_mutex_lock(&state_lock));
    while (*counter < target)
        assert(!pthread_cond_timedwait(&state_cond, &state_lock, &deadline));
#endif
    assert(!pthread_mutex_unlock(&state_lock));
}

static void release_gate(bool *gate)
{
    assert(!pthread_mutex_lock(&state_lock));
    *gate = true;
    signal_state();
    assert(!pthread_mutex_unlock(&state_lock));
}

static int instrument_mutex_lock(pthread_mutex_t *lock)
{
    if (second_sleep && (void *)lock == (void *)&chain.present.@ATTEMPTLOCK@)
    {
        assert(!pthread_mutex_lock(&state_lock));
        second_attempts++;
        signal_state();
        assert(!pthread_mutex_unlock(&state_lock));
    }
    return pthread_mutex_lock(lock);
}

static VkResult vkLatencySleepNV(int device, VkSwapchainKHR swapchain, const VkLatencySleepInfoNV *info)
{
    (void)device;
    assert(swapchain == 42);
    assert(!pthread_mutex_lock(&state_lock));
    assert(native_calls < ARRAY_SIZE(request_values));
    request_values[native_calls++] = info->value;
    signal_state();
    while (block_native && !release_native)
        assert(!pthread_cond_wait(&state_cond, &state_lock));
    assert(!pthread_mutex_unlock(&state_lock));
    return request_result;
}

static VkResult vkWaitSemaphores(int device, const VkSemaphoreWaitInfo *info, uint64_t timeout)
{
    uint64_t expected;
    (void)device;
    assert(timeout == UINT64_MAX && info->semaphoreCount == 1);
    assert(*info->pSemaphores == 9);
    assert(!pthread_mutex_lock(&state_lock));
    assert(wait_calls < ARRAY_SIZE(wait_values));
    expected = *info->pValues;
    wait_values[wait_calls++] = expected;
    signal_state();
    while (block_wait && !release_wait)
        assert(!pthread_cond_wait(&state_cond, &state_lock));
    assert(*info->pValues == expected);
    assert(!pthread_mutex_unlock(&state_lock));
    return VK_SUCCESS;
}

static void vkSetLatencyMarkerNV(int device, VkSwapchainKHR swapchain, const VkSetLatencyMarkerInfoNV *info)
{
    (void)device;
    assert(swapchain == 42 && info->presentID == 77);
    assert(!pthread_mutex_lock(&state_lock));
    marker_calls++;
    signal_state();
    while (block_marker && !release_marker)
        assert(!pthread_cond_wait(&state_cond, &state_lock));
    assert(!pthread_mutex_unlock(&state_lock));
}

static void vkGetLatencyTimingsNV(int device, VkSwapchainKHR swapchain, VkGetLatencyMarkerInfoNV *info)
{
    (void)device;
    assert(swapchain == 42);
    assert(!pthread_mutex_lock(&state_lock));
    timing_calls++;
    info->timingCount = 0;
    signal_state();
    while (block_timing && !release_timing)
        assert(!pthread_cond_wait(&state_cond, &state_lock));
    assert(!pthread_mutex_unlock(&state_lock));
}

static HRESULT hresult_from_vk_result(VkResult result) { return result; }
static uint64_t vkd3d_get_current_time_ns(void) { return 1; }
static struct vkd3d_queue_timeline_trace_cookie vkd3d_queue_timeline_trace_register_low_latency_sleep(
        struct vkd3d_queue_timeline_trace *trace, uint64_t value)
{
    (void)trace;
    (void)value;
    return (struct vkd3d_queue_timeline_trace_cookie){0};
}
static void vkd3d_queue_timeline_trace_complete_low_latency_sleep(
        struct vkd3d_queue_timeline_trace *trace, struct vkd3d_queue_timeline_trace_cookie cookie)
{
    (void)trace;
    (void)cookie;
}
static void vkd3d_queue_timeline_trace_present_region(struct vkd3d_queue_timeline_trace *trace,
        const char *name, struct dxgi_vk_swap_chain *swapchain, uint64_t value, uint64_t start, uint64_t end)
{
    (void)trace;
    (void)name;
    (void)swapchain;
    (void)value;
    (void)start;
    (void)end;
}

#define pthread_mutex_lock instrument_mutex_lock
@FUNCTIONS@
#undef pthread_mutex_lock

static void *sleep_thread(void *arg)
{
    second_sleep = arg != NULL;
    assert(dxgi_vk_swap_chain_latency_sleep(&chain) == VK_SUCCESS);
    return NULL;
}
static void *marker_thread(void *arg)
{
    dxgi_vk_swap_chain_set_latency_marker(&chain, 77, VK_LATENCY_MARKER_PRESENT_START_NV, arg == NULL);
    return NULL;
}
static void *timing_thread(void *arg)
{
    D3D12_LATENCY_RESULTS results = {0};
    (void)arg;
    dxgi_vk_swap_chain_get_latency_info(&chain, &results);
    return NULL;
}
static bool try_writer(void) { return @TRYWRITE@ == 0; }
static void unlock_writer(void) { assert(!@UNLOCKWRITE@); }

int main(int argc, char **argv)
{
    struct mock_device device = {0};
    struct mock_queue queue = {&device};
    pthread_t sleep, reader, second;
    assert(argc == 2);
    chain.queue = &queue;
    chain.present.vk_swapchain = 42;
    chain.present.low_latency_sem = 9;
    assert(!@INITLOCK@);
    assert(!pthread_mutex_init(&chain.present.low_latency_sleep_lock, NULL));

    if (!strcmp(argv[1], "request-marker") || !strcmp(argv[1], "request-timing"))
    {
        block_native = true;
        assert(!pthread_create(&sleep, NULL, sleep_thread, NULL));
        wait_until(&native_calls, 1);
        if (!strcmp(argv[1], "request-marker"))
        {
            assert(!pthread_create(&reader, NULL, marker_thread, NULL));
            wait_until(&marker_calls, 1);
        }
        else
        {
            assert(!pthread_create(&reader, NULL, timing_thread, NULL));
            wait_until(&timing_calls, 1);
        }
        assert(!pthread_join(reader, NULL));
        assert(!try_writer());
        release_gate(&release_native);
        assert(!pthread_join(sleep, NULL));
    }
    else if (!strcmp(argv[1], "marker-lifetime") || !strcmp(argv[1], "timing-lifetime"))
    {
        if (!strcmp(argv[1], "marker-lifetime"))
        {
            block_marker = true;
            assert(!pthread_create(&reader, NULL, marker_thread, NULL));
            wait_until(&marker_calls, 1);
        }
        else
        {
            block_timing = true;
            assert(!pthread_create(&reader, NULL, timing_thread, NULL));
            wait_until(&timing_calls, 1);
        }
        assert(!try_writer());
        release_gate(&release_marker);
        release_gate(&release_timing);
        assert(!pthread_join(reader, NULL));
    }
    else if (!strcmp(argv[1], "semaphore-readers"))
    {
        block_wait = true;
        assert(!pthread_create(&sleep, NULL, sleep_thread, NULL));
        wait_until(&wait_calls, 1);
        dxgi_vk_swap_chain_set_latency_marker(&chain, 77, VK_LATENCY_MARKER_PRESENT_START_NV, true);
        assert(marker_calls == 1);
        assert(try_writer());
        chain.present.vk_swapchain = 43;
        unlock_writer();
        release_gate(&release_wait);
        assert(!pthread_join(sleep, NULL));
    }
    else if (!strcmp(argv[1], "serialized-sleeps"))
    {
        block_native = true;
        assert(!pthread_create(&sleep, NULL, sleep_thread, NULL));
        wait_until(&native_calls, 1);
        assert(!pthread_create(&second, NULL, sleep_thread, (void *)1));
        wait_until(&second_attempts, 1);
        assert(chain.present.low_latency_sem_value == 1);
        assert(native_calls == 1 && wait_calls == 0);
        release_gate(&release_native);
        assert(!pthread_join(sleep, NULL));
        assert(!pthread_join(second, NULL));
        assert(native_calls == 2 && wait_calls == 2);
        assert(request_values[0] == 1 && request_values[1] == 2);
        assert(wait_values[0] + wait_values[1] == 3 && wait_values[0] != wait_values[1]);
    }
    else if (!strcmp(argv[1], "overlapping-waits"))
    {
        block_wait = true;
        assert(!pthread_create(&sleep, NULL, sleep_thread, NULL));
        wait_until(&wait_calls, 1);
        assert(!pthread_create(&second, NULL, sleep_thread, (void *)1));
        wait_until(&wait_calls, 2);
        assert(chain.present.low_latency_sem_value == 2);
        assert(native_calls == 2);
        assert(request_values[0] == 1 && request_values[1] == 2);
        assert(wait_values[0] == 1 && wait_values[1] == 2);
        release_gate(&release_wait);
        assert(!pthread_join(sleep, NULL));
        assert(!pthread_join(second, NULL));
    }
    else if (!strcmp(argv[1], "absent") || !strcmp(argv[1], "failed"))
    {
        if (!strcmp(argv[1], "absent"))
            chain.present.vk_swapchain = 0;
        else
            request_result = VK_ERROR_DEVICE_LOST;
        assert(dxgi_vk_swap_chain_latency_sleep(&chain) == request_result);
        assert(native_calls == (request_result != VK_SUCCESS));
        assert(wait_calls == 0);
        assert(!pthread_mutex_trylock(&chain.present.low_latency_sleep_lock));
        assert(!pthread_mutex_unlock(&chain.present.low_latency_sleep_lock));
    }
    else if (!strcmp(argv[1], "out-of-band"))
    {
        assert(try_writer());
        assert(!pthread_create(&reader, NULL, marker_thread, (void *)1));
        wait_until(&marker_calls, 1);
        assert(!pthread_join(reader, NULL));
        unlock_writer();
        dxgi_vk_swap_chain_set_latency_marker(&chain, 77,
                VK_LATENCY_MARKER_OUT_OF_BAND_PRESENT_START_NV, true);
        assert(device.frame_markers.out_of_band_present == 77);
        assert(marker_calls == 1);
    }
    else
        assert(!"Unknown test");

    assert(try_writer());
    unlock_writer();
    assert(!@DESTROYLOCK@);
    assert(!pthread_mutex_destroy(&chain.present.low_latency_sleep_lock));
    return 0;
}
"""


def generate_harness():
    source = Path(os.environ.get("REFLEX_TEST_SOURCE", ROOT / "libs/vkd3d/swapchain.c")).read_text()
    header = Path(os.environ.get("REFLEX_TEST_THREADS",
            ROOT / "include/private/vkd3d_threads.h")).read_text()
    windows, native = header.split("#else\n#include <pthread.h>", 1)
    win_end = function(windows, "pthread_cond_wait")
    win_threads = windows[windows.index("struct pthread\n"):windows.index(win_end) + len(win_end)]
    rwlock = native[native.index("typedef struct rwlock"):native.index("typedef struct condvar_reltime")]
    functions = "\n\n".join(function(source, name) for name in (
        "dxgi_vk_swap_chain_latency_sleep", "dxgi_vk_swap_chain_set_latency_marker",
        "dxgi_vk_swap_chain_get_latency_info"))
    shared = bool(re.search(r"rwlock_t\s+low_latency_swapchain_lock", source))
    lock = "chain.present.low_latency_swapchain_lock"
    values = {
        "WINTHREADS": win_threads,
        "POSIXRWLOCK": rwlock,
        "FUNCTIONS": functions,
        "LOCKTYPE": "rwlock_t" if shared else "pthread_mutex_t",
        "ATTEMPTLOCK": "low_latency_sleep_lock" if shared else "low_latency_swapchain_lock",
        "TRYWRITE": f"try_rwlock_write(&{lock})" if shared else f"pthread_mutex_trylock(&{lock})",
        "UNLOCKWRITE": f"rwlock_unlock_write(&{lock})" if shared else f"pthread_mutex_unlock(&{lock})",
        "INITLOCK": f"rwlock_init(&{lock})" if shared else f"pthread_mutex_init(&{lock}, NULL)",
        "DESTROYLOCK": f"rwlock_destroy(&{lock})" if shared else f"pthread_mutex_destroy(&{lock})",
    }
    harness = HARNESS
    for key, value in values.items():
        harness = harness.replace("@" + key + "@", value)
    return harness


class ReflexLifetimeTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.build = tempfile.TemporaryDirectory(prefix="reflex-lifetime-test-")
        cls.addClassCleanup(cls.build.cleanup)
        cls.binary = Path(cls.build.name) / "test.exe"
        command = shlex.split(os.environ.get("CC", "cc")) + ["-std=c11", "-O1", "-g", "-pthread",
                   "-Werror", "-Wall", "-Wextra", "-Wno-unused-variable", "-Wno-unused-function",
                   "-Wno-unused-parameter"]
        if os.environ.get("REFLEX_TEST_SANITIZE") == "1":
            command += ["-fsanitize=address,undefined", "-fno-omit-frame-pointer", "-no-pie"]
        result = subprocess.run(command + ["-x", "c", "-", "-o", str(cls.binary)],
                input=generate_harness(), capture_output=True, text=True, timeout=30)
        if result.returncode:
            raise RuntimeError(result.stderr)

    def run_case(self, name):
        runner = shlex.split(os.environ.get("REFLEX_TEST_RUNNER", ""))
        result = subprocess.run(runner + [str(self.binary), name], capture_output=True, text=True, timeout=10)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_marker_proceeds_during_native_request(self):
        self.run_case("request-marker")

    def test_timing_query_proceeds_during_native_request(self):
        self.run_case("request-timing")

    def test_marker_retains_swapchain_lifetime(self):
        self.run_case("marker-lifetime")

    def test_timing_query_retains_swapchain_lifetime(self):
        self.run_case("timing-lifetime")

    def test_semaphore_wait_does_not_block_marker_or_replacement(self):
        self.run_case("semaphore-readers")

    def test_concurrent_sleeps_serialize_with_distinct_wait_values(self):
        self.run_case("serialized-sleeps")

    def test_concurrent_semaphore_waits_retain_captured_values(self):
        self.run_case("overlapping-waits")

    def test_absent_swapchain_releases_locks(self):
        self.run_case("absent")

    def test_failed_request_releases_locks(self):
        self.run_case("failed")

    def test_out_of_band_markers_remain_lock_free(self):
        self.run_case("out-of-band")

if __name__ == "__main__":
    parser = argparse.ArgumentParser(add_help=False)
    parser.add_argument("--source")
    parser.add_argument("--generate")
    options, unittest_args = parser.parse_known_args()
    if options.source:
        os.environ["REFLEX_TEST_SOURCE"] = options.source
    if options.generate:
        Path(options.generate).write_text(generate_harness())
    else:
        unittest.main(argv=[sys.argv[0], *unittest_args])
