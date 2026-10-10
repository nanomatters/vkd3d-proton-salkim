#!/usr/bin/env python3
# Copyright 2026 Erhan Bilgili
# SPDX-License-Identifier: LGPL-2.1-or-later
"""Exercise production surface-extent gates and dummy present completion.

The recreation prefix is extracted unchanged through format selection. The
remaining Vulkan image creation is replaced by a recorder. The actual present
callback and sticky-error helpers run against synchronous mocks, without a GPU.
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
            .replace("@RECREATE_IF_REQUIRED@", function(source,
                     "dxgi_vk_swap_chain_present_recreate_swapchain_if_required"))
            .replace("@CALLBACKS@", callbacks))


HARNESS = r"""
#include <assert.h>
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
#define ARRAY_SIZE(a) (sizeof(a) / sizeof((a)[0]))
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

@ERROR_HELPERS@

static void pthread_mutex_lock(bool *locked) { assert(!*locked); *locked = true; }
static void pthread_mutex_unlock(bool *locked) { assert(*locked); *locked = false; }

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
}

static void dxgi_vk_swap_chain_set_hdr_metadata(struct dxgi_vk_swap_chain *swapchain) { (void)swapchain; }
static void dxgi_vk_swap_chain_low_latency_state_update(struct dxgi_vk_swap_chain *swapchain) { (void)swapchain; }
static void dxgi_vk_swap_chain_update_present_timing(struct dxgi_vk_swap_chain *swapchain) { (void)swapchain; }

@CALLBACKS@

static void reset(void)
{
    memset(&chain, 0, sizeof(chain));
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
}

static void check_no_creation(void)
{
    assert(!chain.present.vk_swapchain && !eligible_recreations);
    assert(!format_queries && !timing_queries && !format_selections);
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

        /* Format/HDR support can change while minimized. Restore must force a fresh query. */
        format_generation = 2;
        queried_caps.maxImageExtent = (VkExtent2D){1920, 1080};
        dxgi_vk_swap_chain_present_callback(&chain);
        assert(caps_queries == 3 && format_queries == 1 && timing_queries == 1 && format_selections == 1);
        assert(eligible_recreations == 1 && chain.present.vk_swapchain);
        assert(selected_generation == 2 && completions == 3 && waiter_signals == 3);
        assert(dxgi_vk_swap_chain_get_error(&chain) == S_OK);
    }
}

static void test_success(void)
{
    reset();
    dxgi_vk_swap_chain_present_callback(&chain);
    assert(caps_queries == 1 && format_queries == 1 && timing_queries == 1 && format_selections == 1);
    assert(eligible_recreations == 1 && observed_extent.width == 800 && observed_extent.height == 600);
    dxgi_vk_swap_chain_present_callback(&chain);
    assert(caps_queries == 1 && completions == 2 && waiter_signals == 2);

    /* A genuine later recreation still forces formats, with no extent caching. */
    chain.present.force_swapchain_recreation = true;
    format_generation = 3;
    queried_caps.currentExtent = (VkExtent2D){UINT32_MAX, UINT32_MAX};
    dxgi_vk_swap_chain_present_callback(&chain);
    assert(caps_queries == 2 && format_queries == 2 && timing_queries == 2 && selected_generation == 3);
    assert(eligible_recreations == 2 && observed_extent.width == 1280 && observed_extent.height == 720);
}

static void test_caps_errors(void)
{
    static const VkResult retryable[] = {VK_ERROR_OUT_OF_DATE_KHR, VK_ERROR_INITIALIZATION_FAILED,
            VK_INCOMPLETE, VK_TIMEOUT};
    static const VkResult terminal[] = {VK_ERROR_DEVICE_LOST, VK_ERROR_OUT_OF_HOST_MEMORY,
            VK_ERROR_OUT_OF_DEVICE_MEMORY};
    unsigned int i;

    for (i = 0; i < ARRAY_SIZE(retryable); i++)
    {
        reset();
        caps_result = retryable[i];
        dxgi_vk_swap_chain_present_callback(&chain);
        check_no_creation();
        assert(caps_queries == 1 && !chain.present.is_surface_lost);
        assert(dxgi_vk_swap_chain_get_error(&chain) == S_OK);
        assert(completions == 1 && waiter_signals == 1 && waiter_blit_count == UINT64_MAX);
        caps_result = VK_SUCCESS;
        dxgi_vk_swap_chain_present_callback(&chain);
        assert(caps_queries == 2 && eligible_recreations == 1 && format_queries == 1 && timing_queries == 1);
        assert(completions == 2 && waiter_signals == 2);
    }

    for (i = 0; i < ARRAY_SIZE(terminal); i++)
    {
        reset();
        caps_result = terminal[i];
        dxgi_vk_swap_chain_present_callback(&chain);
        check_no_creation();
        assert(dxgi_vk_swap_chain_get_error(&chain) == terminal[i] && !chain.present.is_surface_lost);
        assert(!completions && waiter_signals == 1 && waiter_blit_count == 7);
        caps_result = VK_SUCCESS;
        dxgi_vk_swap_chain_present_callback(&chain);
        assert(caps_queries == 1 && !completions && waiter_signals == 2);
        dxgi_vk_swap_chain_set_error(&chain, VK_ERROR_INITIALIZATION_FAILED);
        assert(dxgi_vk_swap_chain_get_error(&chain) == terminal[i]);
    }

    reset();
    caps_result = VK_ERROR_SURFACE_LOST_KHR;
    dxgi_vk_swap_chain_present_callback(&chain);
    caps_result = VK_SUCCESS;
    dxgi_vk_swap_chain_present_callback(&chain);
    check_no_creation();
    assert(caps_queries == 1 && chain.present.is_surface_lost);
    assert(dxgi_vk_swap_chain_get_error(&chain) == S_OK && completions == 2 && waiter_signals == 2);
}

static void test_format_errors(void)
{
    reset();
    formats_ok = false;
    dxgi_vk_swap_chain_present_callback(&chain);
    assert(format_queries == 1 && !timing_queries && !format_selections && !eligible_recreations);
    assert(chain.present.is_surface_lost && !chain.properties.lock);
    assert(dxgi_vk_swap_chain_get_error(&chain) == S_OK && completions == 1 && waiter_signals == 1);

    reset();
    selected_format_ok = false;
    dxgi_vk_swap_chain_present_callback(&chain);
    assert(format_queries == 1 && timing_queries == 1 && format_selections == 1 && !eligible_recreations);
    assert(!chain.present.is_surface_lost && !chain.properties.lock);
    selected_format_ok = true;
    dxgi_vk_swap_chain_present_callback(&chain);
    assert(format_queries == 2 && timing_queries == 2 && format_selections == 2 && eligible_recreations == 1);
    assert(dxgi_vk_swap_chain_get_error(&chain) == S_OK && completions == 2 && waiter_signals == 2);
}

int main(void)
{
    test_zero_extent();
    test_success();
    test_caps_errors();
    test_format_errors();
    puts("Swapchain extent tests passed (zero extent, restore, query failures, completion).");
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
        command += ["-std=c11", "-O1", "-g", "-UNDEBUG", "-Wall", "-Wextra", "-Werror",
                    "-Wno-unused-variable", "-Wno-unused-function"]
        if os.environ.get("SWAPCHAIN_EXTENT_TEST_SANITIZE") == "1":
            command += ["-fsanitize=address,undefined", "-fno-omit-frame-pointer", "-no-pie"]
        subprocess.run(command + [str(source), "-o", str(binary)], check=True)
        subprocess.run([str(binary)], check=True, timeout=10)
        print("Retained harness:", build)
