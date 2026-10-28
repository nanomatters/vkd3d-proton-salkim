#!/usr/bin/env python3
# Copyright 2026 Erhan Bilgili
# SPDX-License-Identifier: LGPL-2.1-or-later
"""Exercise production presentation and timing polling with mocked Vulkan.

The present iteration, tracking ring and frame-statistics update are extracted
unchanged. Vulkan submission and timing retrieval are recorded without a GPU.
The threaded worker and outer callback are covered by the other swapchain tests.
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
    match = re.search(r"^static (?:inline )?(?:bool|void|struct present_timing_entry) "
                      + name + r"\([^;]+?\)\s*\{", source, re.M)
    if not match:
        raise ValueError("Missing source function: " + name)
    end, depth = match.end(), 1
    while depth:
        depth += (source[end] == "{") - (source[end] == "}")
        end += 1
    return source[match.start():end]


def generate(path):
    source = path.read_text()
    entry = re.search(r"struct present_timing_entry\s*\{.*?\n\};", source, re.S).group()
    ring = "\n".join(function(source, name) for name in (
        "dxgi_vk_swap_chain_track_present_timing", "dxgi_vk_swap_chain_take_present_timing"))
    return (HARNESS.replace("@ENTRY@", entry).replace("@RING@", ring)
            .replace("@PACING@", function(source, "present_mode_pacing_should_wait"))
            .replace("@PRESENT@", function(source, "dxgi_vk_swap_chain_present_iteration"))
            .replace("@STATISTICS@", function(source, "dxgi_vk_swap_chain_update_frame_statistics")))


HARNESS = r'''
#define VK_NO_PROTOTYPES
#include <vulkan/vulkan.h>
#include <assert.h>
#include <stdbool.h>
#include <stdint.h>
#include <stdio.h>
#include <string.h>

#define ARRAY_SIZE(a) (sizeof(a) / sizeof((a)[0]))
#define max(a, b) ((a) > (b) ? (a) : (b))
#define VK_CALL(call) (call)
#define FIXME_ONCE(...) (++warnings)
#define VKD3D_REGION_DECL(...)
#define VKD3D_REGION_BEGIN(...)
#define VKD3D_REGION_END(...)
#define VKD3D_DEVICE_REPORT_FAULT_AND_BREADCRUMB_IF(...)
#define vkd3d_atomic_uint32_load_explicit(p, order) (*(p))
#define vkd3d_atomic_uint32_store_explicit(p, value, order) (*(p) = (value))
#define vkd3d_queue_timeline_trace_register_instantaneous(...)
#define dxgi_vk_swap_chain_set_latency_marker(...)

@ENTRY@
struct vkd3d_vk_device_procs { int unused; };
struct mock_device
{
    struct vkd3d_vk_device_procs vk_procs;
    struct { bool NV_low_latency2; } vk_info;
};
struct mock_queue { struct mock_device *device; void *vkd3d_queue; };
struct dxgi_vk_swap_chain
{
    struct mock_queue *queue;
    bool swapchain_maintenance1;
    uint32_t last_present_mode;
    struct { uint64_t low_latency_frame_id; uint32_t swap_interval; } request;
    struct { unsigned int time_domains_count; } timing;
    struct
    {
        VkSwapchainKHR vk_swapchain;
        VkSemaphore vk_release_semaphores[2];
        VkFence vk_swapchain_fences[2];
        bool vk_swapchain_fences_signalled[2];
        uint32_t current_backbuffer_index, swapchain_fence_index;
        VkPresentModeKHR selected_present_mode, unlocked_present_mode;
        uint64_t present_id, internal_blit_count;
        bool wait, wait2, timing, present_id_valid, timing_id_committed;
        bool present_target_enabled, force_swapchain_recreation, is_surface_lost;
        bool compatible_unlocked_present_mode;
    } present;
    struct
    {
        bool lock;
        uint32_t enabled;
        struct present_timing_entry pending[16];
    } present_telemetry;
    struct { bool lock; uint64_t count, time; } frame_statistics;
};

static struct mock_device device;
static struct mock_queue queue = {.device = &device};
static struct dxgi_vk_swap_chain chain;
static VkResult queue_result, present_result, acquire_result, fence_result;
static bool blit_success;
static uint64_t clock_ns, submitted_id, complete_through;
static VkPresentStageFlagsEXT requested_stages;
static unsigned int submissions, polls, reports, warnings;

static uint64_t vkd3d_get_current_time_ns(void) { return ++clock_ns; }
static void spinlock_acquire(bool *lock) { assert(!*lock); *lock = true; }
static void spinlock_release(bool *lock) { assert(*lock); *lock = false; }

@RING@
@PACING@

static void vk_prepend_struct(void *head, void *next)
{
    VkBaseOutStructure *h = head, *n = next;
    n->pNext = h->pNext;
    h->pNext = n;
}

static void dxgi_vk_swap_chain_present_recreate_swapchain_if_required(struct dxgi_vk_swap_chain *c) {}
static VkResult dxgi_vk_swap_chain_try_acquire_next_image(struct dxgi_vk_swap_chain *c)
{ return acquire_result; }
static VkResult dxgi_vk_swap_chain_ensure_unsignaled_swapchain_fence(struct dxgi_vk_swap_chain *c, uint32_t index)
{ return fence_result; }
static bool dxgi_vk_swap_chain_submit_blit(struct dxgi_vk_swap_chain *c, uint32_t index)
{ return blit_success; }
static void dxgi_vk_swap_chain_set_error(struct dxgi_vk_swap_chain *c, VkResult result) {}
static void dxgi_vk_swap_chain_drain_internal_blit_semaphore(struct dxgi_vk_swap_chain *c, uint64_t count) {}
static VkQueue vkd3d_queue_acquire(void *queue) { return (VkQueue)(uintptr_t)1; }
static void vkd3d_queue_release(void *queue) {}

static void dxgi_vk_swap_chain_destroy_swapchain_in_present_task(struct dxgi_vk_swap_chain *c)
{
    c->present.vk_swapchain = VK_NULL_HANDLE;
    memset(c->present_telemetry.pending, 0, sizeof(c->present_telemetry.pending));
}

static bool dxgi_vk_swap_chain_setup_present_timing_request(struct dxgi_vk_swap_chain *c,
        uint64_t count, bool telemetry, VkPresentTimingInfoEXT *info)
{
    info->presentStageQueries = VK_PRESENT_STAGE_QUEUE_OPERATIONS_END_BIT_EXT;
    return false;
}

static VkResult vkQueuePresentKHR(VkQueue queue, const VkPresentInfoKHR *info)
{
    const VkBaseInStructure *next = info->pNext;
    unsigned int i;
    submitted_id = 0;
    requested_stages = 0;
    submissions++;
    assert(info->swapchainCount == 1 && *info->pSwapchains == chain.present.vk_swapchain);
    while (next)
    {
        if (next->sType == VK_STRUCTURE_TYPE_PRESENT_ID_2_KHR)
            submitted_id = *((const VkPresentId2KHR *)next)->pPresentIds;
        else if (next->sType == VK_STRUCTURE_TYPE_PRESENT_ID_KHR)
            submitted_id = *((const VkPresentIdKHR *)next)->pPresentIds;
        else if (next->sType == VK_STRUCTURE_TYPE_PRESENT_TIMINGS_INFO_EXT)
            requested_stages = ((const VkPresentTimingsInfoEXT *)next)->pTimingInfos->presentStageQueries;
        next = next->pNext;
    }
    if (requested_stages)
    {
        for (i = 0; i < ARRAY_SIZE(chain.present_telemetry.pending); i++)
            if (chain.present_telemetry.pending[i].present_id == submitted_id)
                break;
        assert(submitted_id && i < ARRAY_SIZE(chain.present_telemetry.pending));
    }
    *info->pResults = present_result;
    return queue_result;
}

@PRESENT@

static void dxgi_vk_swap_chain_poll_past_presentation(struct dxgi_vk_swap_chain *c)
{
    unsigned int i;
    assert(c->present.vk_swapchain);
    polls++;
    for (i = 0; i < ARRAY_SIZE(c->present_telemetry.pending); i++)
    {
        uint64_t id = c->present_telemetry.pending[i].present_id;
        struct present_timing_entry entry;
        if (!id || id > complete_through)
            continue;
        entry = dxgi_vk_swap_chain_take_present_timing(c, id);
        reports++;
        if (entry.frame_statistics)
        {
            c->frame_statistics.count = entry.present_count;
            c->frame_statistics.time = ++clock_ns;
        }
    }
}

@STATISTICS@

static void reset(VkPresentModeKHR mode)
{
    memset(&chain, 0, sizeof(chain));
    chain.queue = &queue;
    chain.present.vk_swapchain = (VkSwapchainKHR)(uintptr_t)1;
    chain.present.wait = chain.present.wait2 = chain.present.timing = true;
    chain.present.selected_present_mode = mode;
    chain.present_telemetry.enabled = chain.timing.time_domains_count = 1;
    queue_result = present_result = acquire_result = fence_result = VK_SUCCESS;
    blit_success = true;
    clock_ns = 1000;
    submissions = polls = reports = warnings = 0;
    complete_through = UINT64_MAX;
}

static void present(uint64_t count)
{
    /* Initialize each request as the outer callback does. Its reset and the
     * wait-entry publication have separate callback and threaded tests. */
    chain.present.present_id_valid = false;
    chain.present.timing_id_committed = false;
    chain.present.present_target_enabled = false;
    chain.present.current_backbuffer_index = 0;
    dxgi_vk_swap_chain_present_iteration(&chain, count, 0);
}

static void retire(uint64_t count)
{
    dxgi_vk_swap_chain_update_frame_statistics(&chain, count,
            chain.present.present_id_valid ? chain.present.present_id : 0,
            chain.present.timing_id_committed);
}

static unsigned int pending(void)
{
    unsigned int i, count = 0;
    for (i = 0; i < ARRAY_SIZE(chain.present_telemetry.pending); i++)
        count += chain.present_telemetry.pending[i].present_id != 0;
    return count;
}

static void test_modes(void)
{
    static const VkPresentModeKHR modes[] = {VK_PRESENT_MODE_IMMEDIATE_KHR,
        VK_PRESENT_MODE_MAILBOX_KHR, VK_PRESENT_MODE_FIFO_KHR,
        VK_PRESENT_MODE_FIFO_RELAXED_KHR, VK_PRESENT_MODE_FIFO_LATEST_READY_KHR};
    unsigned int i;
    for (i = 0; i < ARRAY_SIZE(modes); i++)
    {
        bool wait = modes[i] == VK_PRESENT_MODE_FIFO_KHR || modes[i] == VK_PRESENT_MODE_FIFO_RELAXED_KHR;
        reset(modes[i]);
        present(1);
        assert(submitted_id == 1 && requested_stages && pending() == 1);
        assert(chain.present.timing_id_committed && chain.present.present_id_valid == wait);
        retire(1);
        assert(polls == 1 && reports == 1 && !pending() && !warnings);
        assert(chain.frame_statistics.count == 1);
    }
}

static void test_full_ring(void)
{
    unsigned int i;
    reset(VK_PRESENT_MODE_IMMEDIATE_KHR);
    complete_through = 0;
    for (i = 1; i <= 17; i++)
    {
        present(i);
        assert(chain.present.timing_id_committed && !chain.present.present_id_valid);
        assert(submitted_id == i && !!requested_stages == (i <= 16));
        retire(i);
        assert(polls == i && !reports && !warnings);
    }
    assert(pending() == 16);
    complete_through = 16;
    present(18);
    assert(!requested_stages && chain.present.timing_id_committed);
    retire(18);
    assert(polls == 18 && reports == 16 && !pending());
    complete_through = UINT64_MAX;
    present(19);
    assert(requested_stages);
    retire(19);
    assert(polls == 19 && reports == 17 && !pending() && !warnings);
}

static void test_failures(void)
{
    unsigned int i;
    for (i = 0; i < 6; i++)
    {
        reset(VK_PRESENT_MODE_IMMEDIATE_KHR);
        switch (i)
        {
            case 0: queue_result = VK_ERROR_OUT_OF_HOST_MEMORY; break;
            case 1: present_result = VK_ERROR_SURFACE_LOST_KHR; break;
            case 2: acquire_result = VK_ERROR_DEVICE_LOST; break;
            case 3: chain.present.vk_swapchain = VK_NULL_HANDLE; break;
            case 4: blit_success = false; break;
            case 5:
                chain.swapchain_maintenance1 = true;
                fence_result = VK_ERROR_OUT_OF_HOST_MEMORY;
                break;
        }
        present(1);
        assert(!chain.present.timing_id_committed && !chain.present.present_id_valid && !pending());
        retire(1);
        assert(!polls && !reports && !warnings && chain.frame_statistics.count == 1);
    }
    reset(VK_PRESENT_MODE_IMMEDIATE_KHR);
    present_result = VK_SUBOPTIMAL_KHR;
    present(1);
    assert(chain.present.timing_id_committed && chain.present.force_swapchain_recreation);
    retire(1);
    assert(polls == 1 && reports == 1 && !pending() && !warnings);
}

static void test_disabled(void)
{
    unsigned int i;
    for (i = 0; i < 3; i++)
    {
        reset(VK_PRESENT_MODE_IMMEDIATE_KHR);
        switch (i)
        {
            case 0: chain.present_telemetry.enabled = false; break;
            case 1: chain.present.timing = false; break;
            case 2: chain.timing.time_domains_count = 0; break;
        }
        present(1);
        assert(!submitted_id && !requested_stages && !chain.present.timing_id_committed);
        retire(1);
        assert(!polls && !reports && !pending() && !warnings);
    }
    reset(VK_PRESENT_MODE_FIFO_KHR);
    chain.present_telemetry.enabled = false;
    present(1);
    assert(chain.present.present_id_valid && submitted_id == 1);
    retire(1);
    assert(polls == 1 && reports == 1 && !warnings);
}

int main(void)
{
    test_modes();
    test_full_ring();
    test_failures();
    test_disabled();
    puts("Presentation timing tests passed (five modes, delayed full ring, failures, disabled telemetry).");
    return 0;
}
'''


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, default=ROOT / "libs/vkd3d/swapchain.c")
    parser.add_argument("--generate", type=Path)
    parser.add_argument("--build-dir", type=Path)
    parser.add_argument("--sanitize", action="store_true")
    args = parser.parse_args()
    harness = generate(args.source)
    if args.generate:
        args.generate.write_text(harness)
    else:
        build = args.build_dir or Path(tempfile.mkdtemp(prefix="swapchain-timing-"))
        build.mkdir(parents=True, exist_ok=True)
        source, binary = build / "swapchain_timing.c", build / "swapchain_timing"
        source.write_text(harness)
        command = shlex.split(os.environ.get("CC", "cc")) + [
            "-std=c11", "-O1", "-g", "-UNDEBUG", "-Wall", "-Wextra", "-Werror",
            "-Wno-unused-variable", "-Wno-unused-parameter", "-Wno-unused-function",
            "-I" + str(ROOT / "khronos/Vulkan-Headers/include")]
        if args.sanitize:
            command += ["-fsanitize=address,undefined", "-fno-omit-frame-pointer", "-no-pie"]
        subprocess.run(command + [str(source), "-o", str(binary)], check=True, timeout=30)
        subprocess.run([str(binary)], check=True, timeout=10)
        print("Retained harness:", build)
