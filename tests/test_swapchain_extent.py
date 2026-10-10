#!/usr/bin/env python3
# Copyright 2026 Erhan Bilgili
# SPDX-License-Identifier: LGPL-2.1-or-later
"""Exercise production surface-extent gates and dummy present completion.

The recreation prefix is extracted unchanged through format selection. The
remaining Vulkan image creation is replaced by a recorder. The actual present
callback, frame-statistics update, and sticky-error helpers run against mocks,
without a GPU. A real pthread exercises restore while a dummy completion is
pending, using the production waiter drain to order the two threads.
Use --source for a negative control or --generate for a Meson native test.
Standalone artifacts are retained under ~/tmp unless --build-dir is supplied.
"""

import argparse
import os
from pathlib import Path
import re
import shlex
import subprocess
import tempfile


ROOT = Path(__file__).resolve().parents[1]


def function(source, name):
    match = re.search(r"^static (?:HRESULT|void|bool) " + name + r"\([^;]+?\)\n\{", source, re.M)
    if not match:
        raise AssertionError("Missing source function: " + name)
    opening = source.index("{", match.start())
    depth, end = 1, opening + 1
    while depth:
        depth += (source[end] == "{") - (source[end] == "}")
        end += 1
    return source[match.start():end]


def generate(path):
    source = path.read_text()
    recreate = function(source, "dxgi_vk_swap_chain_recreate_swapchain_in_present_task")
    boundary = "    memset(&swapchain_create_info, 0, sizeof(swapchain_create_info));"
    recreate = recreate[:recreate.index(boundary)] + """
    /* Record reaching swapchain creation, whose unchanged implementation is outside this test. */
    eligible_recreations++;
    observed_extent = surface_caps.currentExtent;
    chain->present.vk_swapchain = 1;
}
"""
    helpers = "\n".join(function(source, name) for name in (
        "dxgi_vk_swap_chain_get_error", "dxgi_vk_swap_chain_set_error"))
    callbacks = "\n".join(function(source, name) for name in (
        "dxgi_vk_swap_chain_request_needs_swapchain_recreation",
        "dxgi_vk_swap_chain_signal_waitable_handle", "dxgi_vk_swap_chain_present_callback"))
    return (HARNESS.replace("@ERROR_HELPERS@", helpers).replace("@RECREATE@", recreate)
            .replace("@DRAIN_WAITER@", function(source, "dxgi_vk_swap_chain_drain_waiter"))
            .replace("@RECREATE_IF_REQUIRED@", function(source,
                     "dxgi_vk_swap_chain_present_recreate_swapchain_if_required"))
            .replace("@FRAME_STATISTICS@", function(source,
                     "dxgi_vk_swap_chain_update_frame_statistics"))
            .replace("@CALLBACKS@", callbacks))


HARNESS = r"""
#include <assert.h>
#include <pthread.h>
#include <stdbool.h>
#include <stdint.h>
#include <stdio.h>
#include <string.h>

typedef int32_t HRESULT;
typedef int VkResult, VkPhysicalDevice, VkDevice, VkSurfaceFormatKHR;
typedef int VkSwapchainLatencyCreateInfoNV, VkSwapchainPresentModesCreateInfoKHR;
typedef int VkCommandPoolCreateInfo, VkSwapchainCreateInfoKHR, VkPresentModeKHR, VkImageViewCreateInfo;
typedef struct { uint32_t width, height; } VkExtent2D;
typedef struct { VkExtent2D maxImageExtent, currentExtent; } VkSurfaceCapabilitiesKHR;
#define S_OK 0
#define SUCCEEDED(hr) ((HRESULT)(hr) >= 0)
#define VK_SUCCESS 0
#define VK_TIMEOUT 2
#define VK_INCOMPLETE 5
#define VK_ERROR_OUT_OF_HOST_MEMORY -1
#define VK_ERROR_OUT_OF_DEVICE_MEMORY -2
#define VK_ERROR_INITIALIZATION_FAILED -3
#define VK_ERROR_DEVICE_LOST -4
#define VK_ERROR_SURFACE_LOST_KHR -1000000000
#define VK_ERROR_OUT_OF_DATE_KHR -1000001004
#define VKD3D_PATH_MAX 4096
#define VK_CALL(call) (call)
#define WARN(...) ((void)0)
#define FIXME_ONCE(...) ((void)0)
#define ARRAY_SIZE(a) (sizeof(a) / sizeof((a)[0]))
#define max(a, b) ((a) > (b) ? (a) : (b))
#define vkd3d_memory_order_acquire 0
#define vkd3d_memory_order_release 0
#define vkd3d_memory_order_relaxed 0
#define vkd3d_atomic_uint32_load_explicit(p, order) (*(p))

static void vkd3d_atomic_uint32_compare_exchange(uint32_t *p, uint32_t expected,
        uint32_t desired, int success_order, int failure_order)
{
    (void)success_order;
    (void)failure_order;
    if (*p == expected)
        *p = desired;
}

/* Preserve distinct errors so the real first-error helper can be checked. */
static HRESULT hresult_from_vk_result(VkResult vr) { return vr; }

struct vkd3d_vk_device_procs { int unused; };
struct mock_device
{
    struct vkd3d_vk_device_procs vk_procs;
    VkPhysicalDevice vk_physical_device;
    VkDevice vk_device;
    struct { bool NV_low_latency2; } vk_info;
};
struct mock_queue { struct mock_device *device; };
struct dxgi_vk_swap_chain_present_request
{
    unsigned int dxgi_color_space_type, dxgi_format, swap_interval;
    bool modifies_hdr_metadata;
    uint64_t begin_frame_time_ns;
};
struct dxgi_vk_swap_chain
{
    struct mock_queue *queue;
    int vk_surface;
    uint32_t present_error;
    struct { uint32_t Width, Height; } desc;
    struct { bool lock; } properties;
    struct { bool lock; uint64_t count, time; } frame_statistics;
    struct
    {
        pthread_mutex_t lock;
        pthread_cond_t cond;
        unsigned int wait_queue_count;
        bool skip_waits;
    } wait_thread;
    struct
    {
        int vk_swapchain;
        bool is_surface_lost, force_swapchain_recreation, timing, wait2;
        bool compatible_unlocked_present_mode, override_present_mode;
        bool present_id_valid, present_target_enabled;
        uint64_t present_count, present_id, internal_blit_count;
    } present;
    struct dxgi_vk_swap_chain_present_request request, request_ring[4];
};

static struct mock_device device;
static struct mock_queue queue = { &device };
static struct dxgi_vk_swap_chain chain;
static VkSurfaceCapabilitiesKHR queried_caps;
static VkExtent2D observed_extent;
static VkResult caps_result;
static bool formats_ok, selected_format_ok;
static unsigned int caps_queries, format_queries, timing_queries, format_selections;
static unsigned int eligible_recreations, destroys, demotions, completions, waiter_signals;
static unsigned int format_generation, selected_generation;
static uint64_t waiter_blit_count;
static unsigned int timing_polls, cpu_samples;
static uint64_t cpu_time, report_count, report_time;
static unsigned int drains;
static bool wait_thread_initialized, watch_drain_wait, drain_wait_entered;

@ERROR_HELPERS@

/* Observe the production drain reaching its wait, while still holding the
 * real queue lock. This handshake needs no scheduling sleeps or timeouts. */
static int observed_cond_wait(pthread_cond_t *cond, pthread_mutex_t *lock)
{
    if (watch_drain_wait)
    {
        drain_wait_entered = true;
        assert(!pthread_cond_broadcast(cond));
    }
    return pthread_cond_wait(cond, lock);
}

#define pthread_cond_wait observed_cond_wait
#define dxgi_vk_swap_chain_drain_waiter production_drain_waiter
@DRAIN_WAITER@
#undef dxgi_vk_swap_chain_drain_waiter
#undef pthread_cond_wait

static void dxgi_vk_swap_chain_drain_waiter(struct dxgi_vk_swap_chain *swapchain)
{
    drains++;
    production_drain_waiter(swapchain);
}

static void mock_mutex_lock(bool *locked) { assert(!*locked); *locked = true; }
static void mock_mutex_unlock(bool *locked) { assert(*locked); *locked = false; }
static void spinlock_acquire(bool *locked) { assert(!*locked); *locked = true; }
static void spinlock_release(bool *locked) { assert(*locked); *locked = false; }

/* Property locks are unrelated to the waiter hand-off exercised above. */
#define pthread_mutex_lock mock_mutex_lock
#define pthread_mutex_unlock mock_mutex_unlock

static uint64_t vkd3d_get_current_time_ns(void)
{
    cpu_samples++;
    return cpu_time++;
}

#ifdef _WIN32
typedef struct { int64_t QuadPart; } LARGE_INTEGER;
static void QueryPerformanceCounter(LARGE_INTEGER *counter)
{
    counter->QuadPart = vkd3d_get_current_time_ns();
}
#endif

static void dxgi_vk_swap_chain_poll_past_presentation(struct dxgi_vk_swap_chain *swapchain)
{
    /* Vulkan timing queries require a live swapchain even after a dummy present. */
    assert(swapchain->present.vk_swapchain);
    timing_polls++;
    if (report_count > swapchain->frame_statistics.count)
    {
        swapchain->frame_statistics.count = report_count;
        swapchain->frame_statistics.time = report_time;
    }
}

@FRAME_STATISTICS@

static void dxgi_vk_swap_chain_destroy_swapchain_in_present_task(struct dxgi_vk_swap_chain *swapchain)
{
    destroys++;
    swapchain->present.vk_swapchain = 0;
    swapchain->present.force_swapchain_recreation = false;
}

static void d3d12_device_notify_vk_swapchain_creation(struct mock_device *dev,
        struct dxgi_vk_swap_chain *swapchain)
{
    assert(dev == &device && swapchain == &chain);
    demotions++;
}

static VkResult vkGetPhysicalDeviceSurfaceCapabilitiesKHR(VkPhysicalDevice physical_device,
        int surface, VkSurfaceCapabilitiesKHR *caps)
{
    (void)physical_device;
    (void)surface;
    assert(!chain.present.vk_swapchain && !chain.properties.lock);
    caps_queries++;
    /* Failed queries intentionally leave the output untouched. */
    if (caps_result == VK_SUCCESS)
        *caps = queried_caps;
    return caps_result;
}

static bool dxgi_vk_swap_chain_update_formats_locked(struct dxgi_vk_swap_chain *swapchain, bool force)
{
    assert(swapchain->properties.lock && force);
    format_queries++;
    selected_generation = format_generation;
    return formats_ok;
}

static void dxgi_vk_swap_chain_update_wait_timing_capabilities(struct dxgi_vk_swap_chain *swapchain)
{
    assert(!swapchain->properties.lock && format_queries);
    /* A pending dummy completion still reads timing and vk_swapchain. */
    assert(!swapchain->wait_thread.wait_queue_count);
    timing_queries++;
}

static bool dxgi_vk_swap_chain_select_format(struct dxgi_vk_swap_chain *swapchain, VkSurfaceFormatKHR *format)
{
    assert(!swapchain->properties.lock && timing_queries);
    (void)format;
    format_selections++;
    return selected_format_ok;
}

@RECREATE@
@RECREATE_IF_REQUIRED@

static void dxgi_vk_swap_chain_present_iteration(struct dxgi_vk_swap_chain *swapchain,
        uint64_t present_count, unsigned int retry_counter)
{
    assert(present_count && !retry_counter);
    dxgi_vk_swap_chain_present_recreate_swapchain_if_required(swapchain);
}

static VkResult dxgi_vk_swap_chain_present_signal_blit_semaphore(struct dxgi_vk_swap_chain *swapchain,
        uint64_t present_count)
{
    assert(present_count == swapchain->present.present_count + 1);
    completions++;
    return VK_SUCCESS;
}

static void dxgi_vk_swap_chain_push_present_id(struct dxgi_vk_swap_chain *swapchain,
        uint64_t present_count, uint64_t present_id, uint64_t begin_frame_time_ns,
        bool present_timing_enabled, uint64_t blit_count)
{
    (void)begin_frame_time_ns;
    assert(!present_id && !present_timing_enabled);
    assert(present_count == swapchain->present.present_count + 1);
    swapchain->present.present_count = present_count;
    waiter_blit_count = blit_count;
    waiter_signals++;
    /* Synchronously retire the entry as the wait worker would after its GPU wait. */
    dxgi_vk_swap_chain_update_frame_statistics(swapchain, present_count, present_id);
}

static void dxgi_vk_swap_chain_set_hdr_metadata(struct dxgi_vk_swap_chain *swapchain) { (void)swapchain; }
static void dxgi_vk_swap_chain_low_latency_state_update(struct dxgi_vk_swap_chain *swapchain) { (void)swapchain; }
static void dxgi_vk_swap_chain_update_present_timing(struct dxgi_vk_swap_chain *swapchain) { (void)swapchain; }

@CALLBACKS@

#undef pthread_mutex_lock
#undef pthread_mutex_unlock

static void reset(void)
{
    if (wait_thread_initialized)
    {
        assert(!pthread_mutex_destroy(&chain.wait_thread.lock));
        assert(!pthread_cond_destroy(&chain.wait_thread.cond));
    }
    memset(&chain, 0, sizeof(chain));
    assert(!pthread_mutex_init(&chain.wait_thread.lock, NULL));
    assert(!pthread_cond_init(&chain.wait_thread.cond, NULL));
    wait_thread_initialized = true;
    drains = 0;
    watch_drain_wait = drain_wait_entered = false;
    chain.queue = &queue;
    chain.desc.Width = 1280;
    chain.desc.Height = 720;
    chain.present.internal_blit_count = 7;
    queried_caps.maxImageExtent = (VkExtent2D){1920, 1080};
    queried_caps.currentExtent = (VkExtent2D){800, 600};
    caps_result = VK_SUCCESS;
    formats_ok = selected_format_ok = true;
    caps_queries = format_queries = timing_queries = format_selections = 0;
    eligible_recreations = destroys = demotions = completions = waiter_signals = 0;
    format_generation = 1;
    selected_generation = 0;
    waiter_blit_count = 0;
    timing_polls = cpu_samples = 0;
    cpu_time = 1000;
    report_count = report_time = 0;
}

static void check_fallback_statistics(uint64_t present_count)
{
    assert(!timing_polls && cpu_samples == present_count);
    assert(chain.frame_statistics.count == present_count);
    assert(chain.frame_statistics.time == 999 + present_count && !chain.frame_statistics.lock);
}

static void check_no_creation(void)
{
    assert(!chain.present.vk_swapchain && !eligible_recreations);
    assert(!format_queries && !timing_queries && !format_selections);
    assert(!drains);
    assert(!chain.properties.lock);
}

static void test_zero_extent(void)
{
    unsigned int zero_axes, timing;

    for (zero_axes = 1; zero_axes < 4; zero_axes++)
    for (timing = 0; timing < 2; timing++)
    {
        reset();
        chain.present.timing = chain.present.wait2 = timing;
        if (zero_axes & 1)
            queried_caps.maxImageExtent.width = 0;
        if (zero_axes & 2)
            queried_caps.maxImageExtent.height = 0;
        dxgi_vk_swap_chain_present_callback(&chain);
        dxgi_vk_swap_chain_present_callback(&chain);
        check_no_creation();
        assert(caps_queries == 2 && destroys == 2 && demotions == 2);
        assert(chain.present.timing == (bool)timing && chain.present.wait2 == (bool)timing);
        assert(dxgi_vk_swap_chain_get_error(&chain) == S_OK && !chain.present.is_surface_lost);
        assert(completions == 2 && waiter_signals == 2 && waiter_blit_count == UINT64_MAX);
        check_fallback_statistics(2);

        /* Format/HDR support can change while minimized. Restore must force a fresh query. */
        format_generation = 2;
        queried_caps.maxImageExtent = (VkExtent2D){1920, 1080};
        dxgi_vk_swap_chain_present_callback(&chain);
        assert(caps_queries == 3 && format_queries == 1 && timing_queries == 1 && format_selections == 1);
        assert(drains == 1);
        assert(eligible_recreations == 1 && chain.present.vk_swapchain);
        assert(selected_generation == 2 && completions == 3 && waiter_signals == 3);
        assert(dxgi_vk_swap_chain_get_error(&chain) == S_OK);
        assert(timing_polls == timing && cpu_samples == 3 && chain.frame_statistics.count == 3);
    }
}

static void test_success(void)
{
    reset();
    dxgi_vk_swap_chain_present_callback(&chain);
    assert(caps_queries == 1 && format_queries == 1 && timing_queries == 1 && format_selections == 1);
    assert(drains == 1);
    assert(eligible_recreations == 1 && observed_extent.width == 800 && observed_extent.height == 600);
    dxgi_vk_swap_chain_present_callback(&chain);
    assert(caps_queries == 1 && completions == 2 && waiter_signals == 2);
    assert(drains == 1);

    /* A genuine later recreation still forces formats, with no extent caching. */
    chain.present.force_swapchain_recreation = true;
    format_generation = 3;
    queried_caps.currentExtent = (VkExtent2D){UINT32_MAX, UINT32_MAX};
    dxgi_vk_swap_chain_present_callback(&chain);
    assert(caps_queries == 2 && format_queries == 2 && timing_queries == 2 && selected_generation == 3);
    assert(drains == 2);
    assert(eligible_recreations == 2 && observed_extent.width == 1280 && observed_extent.height == 720);
}

static void test_caps_errors(bool timing)
{
    static const VkResult retryable[] = {VK_ERROR_OUT_OF_DATE_KHR, VK_ERROR_INITIALIZATION_FAILED,
            VK_INCOMPLETE, VK_TIMEOUT};
    static const VkResult terminal[] = {VK_ERROR_DEVICE_LOST, VK_ERROR_OUT_OF_HOST_MEMORY,
            VK_ERROR_OUT_OF_DEVICE_MEMORY};
    unsigned int i;

    for (i = 0; i < ARRAY_SIZE(retryable); i++)
    {
        reset();
        chain.present.timing = timing;
        caps_result = retryable[i];
        dxgi_vk_swap_chain_present_callback(&chain);
        check_no_creation();
        assert(caps_queries == 1 && !chain.present.is_surface_lost);
        assert(dxgi_vk_swap_chain_get_error(&chain) == S_OK);
        assert(completions == 1 && waiter_signals == 1 && waiter_blit_count == UINT64_MAX);
        check_fallback_statistics(1);
        caps_result = VK_SUCCESS;
        dxgi_vk_swap_chain_present_callback(&chain);
        assert(caps_queries == 2 && eligible_recreations == 1 && format_queries == 1 && timing_queries == 1);
        assert(drains == 1);
        assert(completions == 2 && waiter_signals == 2);
        assert(timing_polls == (unsigned int)timing && chain.frame_statistics.count == 2);
    }

    for (i = 0; i < ARRAY_SIZE(terminal); i++)
    {
        reset();
        chain.present.timing = timing;
        caps_result = terminal[i];
        dxgi_vk_swap_chain_present_callback(&chain);
        check_no_creation();
        assert(dxgi_vk_swap_chain_get_error(&chain) == terminal[i] && !chain.present.is_surface_lost);
        assert(!completions && waiter_signals == 1 && waiter_blit_count == 7);
        check_fallback_statistics(1);
        caps_result = VK_SUCCESS;
        dxgi_vk_swap_chain_present_callback(&chain);
        assert(caps_queries == 1 && !completions && waiter_signals == 2);
        dxgi_vk_swap_chain_set_error(&chain, VK_ERROR_INITIALIZATION_FAILED);
        assert(dxgi_vk_swap_chain_get_error(&chain) == terminal[i]);
        check_fallback_statistics(2);
    }

    reset();
    chain.present.timing = timing;
    caps_result = VK_ERROR_SURFACE_LOST_KHR;
    dxgi_vk_swap_chain_present_callback(&chain);
    caps_result = VK_SUCCESS;
    dxgi_vk_swap_chain_present_callback(&chain);
    check_no_creation();
    assert(caps_queries == 1 && chain.present.is_surface_lost);
    assert(dxgi_vk_swap_chain_get_error(&chain) == S_OK && completions == 2 && waiter_signals == 2);
    check_fallback_statistics(2);
}

static void test_format_errors(bool timing)
{
    reset();
    chain.present.timing = timing;
    formats_ok = false;
    dxgi_vk_swap_chain_present_callback(&chain);
    assert(format_queries == 1 && !timing_queries && !format_selections && !eligible_recreations);
    assert(!drains);
    assert(chain.present.is_surface_lost && !chain.properties.lock);
    assert(dxgi_vk_swap_chain_get_error(&chain) == S_OK && completions == 1 && waiter_signals == 1);
    check_fallback_statistics(1);

    reset();
    chain.present.timing = timing;
    selected_format_ok = false;
    dxgi_vk_swap_chain_present_callback(&chain);
    assert(format_queries == 1 && timing_queries == 1 && format_selections == 1 && !eligible_recreations);
    assert(drains == 1);
    assert(!chain.present.is_surface_lost && !chain.properties.lock);
    check_fallback_statistics(1);
    selected_format_ok = true;
    dxgi_vk_swap_chain_present_callback(&chain);
    assert(format_queries == 2 && timing_queries == 2 && format_selections == 2 && eligible_recreations == 1);
    assert(drains == 2);
    assert(dxgi_vk_swap_chain_get_error(&chain) == S_OK && completions == 2 && waiter_signals == 2);
    assert(timing_polls == (unsigned int)timing && chain.frame_statistics.count == 2);
}

static void test_live_frame_statistics(void)
{
    reset();
    chain.present.vk_swapchain = 1;
    chain.present.timing = true;
    report_count = 3;
    report_time = 5000;
    dxgi_vk_swap_chain_update_frame_statistics(&chain, 3, 42);
    assert(timing_polls == 1 && !cpu_samples);
    assert(chain.frame_statistics.count == 3 && chain.frame_statistics.time == 5000);

    /* A live swapchain still polls when its report is delayed, then uses the CPU fallback. */
    cpu_time = 6000;
    dxgi_vk_swap_chain_update_frame_statistics(&chain, 4, 43);
    assert(timing_polls == 2 && cpu_samples == 1);
    assert(chain.frame_statistics.count == 4 && chain.frame_statistics.time == 6000);

    /* Fallback sampling may not move a previously reported timestamp backwards. */
    chain.present.timing = false;
    cpu_time = 5500;
    dxgi_vk_swap_chain_update_frame_statistics(&chain, 5, 0);
    assert(timing_polls == 2 && cpu_samples == 2);
    assert(chain.frame_statistics.count == 5 && chain.frame_statistics.time == 6000);
    assert(!chain.frame_statistics.lock);
}

static void *recreate_thread(void *context)
{
    dxgi_vk_swap_chain_recreate_swapchain_in_present_task(context);
    return NULL;
}

static void test_restore_drains_dummy(bool timing)
{
    pthread_t thread;

    reset();
    chain.present.timing = timing;
    chain.wait_thread.wait_queue_count = 1;
    watch_drain_wait = true;
    assert(!pthread_mutex_lock(&chain.wait_thread.lock));
    assert(!pthread_create(&thread, NULL, recreate_thread, &chain));
    while (!drain_wait_entered)
        assert(!pthread_cond_wait(&chain.wait_thread.cond, &chain.wait_thread.lock));

    /* The recreating thread cannot reconfigure timing or publish a handle
     * while the completion from the minimized period remains in flight. */
    assert(drains == 1 && chain.wait_thread.skip_waits);
    assert(!chain.present.vk_swapchain && !timing_queries && !eligible_recreations);
    dxgi_vk_swap_chain_update_frame_statistics(&chain, 1, 0);
    check_fallback_statistics(1);
    chain.wait_thread.wait_queue_count = 0;
    assert(!pthread_cond_broadcast(&chain.wait_thread.cond));
    assert(!pthread_mutex_unlock(&chain.wait_thread.lock));
    assert(!pthread_join(thread, NULL));

    assert(!chain.wait_thread.skip_waits && drains == 1);
    assert(chain.present.vk_swapchain && eligible_recreations == 1 && timing_queries == 1);
    assert(!timing_polls && chain.frame_statistics.count == 1);
}

int main(void)
{
    test_restore_drains_dummy(false);
    test_restore_drains_dummy(true);
    test_zero_extent();
    test_success();
    test_caps_errors(false);
    test_caps_errors(true);
    test_format_errors(false);
    test_format_errors(true);
    test_live_frame_statistics();
    assert(!pthread_mutex_destroy(&chain.wait_thread.lock));
    assert(!pthread_cond_destroy(&chain.wait_thread.cond));
    puts("Swapchain extent tests passed (zero extent, threaded restore, query failures, completion, frame statistics).");
    return 0;
}
"""


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, default=ROOT / "libs/vkd3d/swapchain.c")
    parser.add_argument("--generate", type=Path)
    parser.add_argument("--build-dir", type=Path)
    args = parser.parse_args()
    harness = generate(args.source)
    if args.generate:
        args.generate.write_text(harness)
    else:
        base = Path.home() / "tmp"
        base.mkdir(parents=True, exist_ok=True)
        build = args.build_dir or Path(tempfile.mkdtemp(prefix="swapchain-extent-", dir=base))
        build.mkdir(parents=True, exist_ok=True)
        source, binary = build / "swapchain_extent.c", build / "swapchain_extent"
        source.write_text(harness)
        command = shlex.split(os.environ.get("CC", "cc"))
        command += ["-std=c11", "-O1", "-g", "-pthread", "-UNDEBUG", "-Wall", "-Wextra", "-Werror",
                    "-Wno-unused-variable", "-Wno-unused-function"]
        if os.environ.get("SWAPCHAIN_EXTENT_TEST_SANITIZE") == "1":
            command += ["-fsanitize=address,undefined", "-fno-omit-frame-pointer", "-no-pie"]
        subprocess.run(command + [str(source), "-o", str(binary)], check=True)
        subprocess.run([str(binary)], check=True, timeout=10)
        print("Retained harness:", build)
