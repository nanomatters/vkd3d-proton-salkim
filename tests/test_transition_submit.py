#!/usr/bin/env python3
# Copyright 2026 Erhan Bilgili
# SPDX-License-Identifier: LGPL-2.1-or-later
"""Record production transition building and submission without a Vulkan device.

The recorder keeps submissions pending until a semaphore wait retires them.
Resetting a pending transition buffer or waiting for an absent signal fails.
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
    match = re.search(r"^static (?:bool|void|VkResult) " + name + r"\([^;]+?\)\s*\{", source, re.M)
    if not match:
        raise ValueError("Missing source function: " + name)
    end, depth = match.end(), 1
    while depth:
        depth += (source[end] == "{") - (source[end] == "}")
        end += 1
    return source[match.start():end]


def generate(source_path):
    source = source_path.read_text()
    header = source_path.with_name("vkd3d_private.h").read_text()
    structures = "\n".join(re.search(r"struct " + name + r"\s*\{.*?\n\};", text, re.S).group()
            for name, text in (("d3d12_command_queue_submission_execute", header),
                               ("d3d12_command_queue_transition_pool", source)))
    functions = "\n\n".join(function(source, name) for name in (
        "d3d12_command_queue_allocate_command_buffers",
        "d3d12_command_queue_transition_pool_wait",
        "d3d12_command_queue_transition_pool_add_barrier",
        "d3d12_command_queue_transition_pool_add_query_heap",
        "d3d12_command_queue_init_query_heap",
        "d3d12_command_queue_transition_pool_build",
        "d3d12_command_queue_submit_split_locked",
        "d3d12_command_queue_wait_staggered_submission",
        "d3d12_command_queue_can_merge_transition",
        "d3d12_command_queue_execute",
        "d3d12_command_queue_exec_submit_needs_fallback_queue"))
    count = re.search(r"^#define VKD3D_COMMAND_QUEUE_NUM_TRANSITION_BUFFERS \d+$", source, re.M).group()
    return HARNESS.replace("@STRUCTURES@", count + "\n" + structures).replace("@FUNCTIONS@", functions)


HARNESS = r'''
#define VK_NO_PROTOTYPES
#include <vulkan/vulkan.h>
#include <assert.h>
#include <stdbool.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

#define HANDLE(type, n) ((type)(uintptr_t)(n))
#define ARRAY_SIZE(a) (sizeof(a) / sizeof((a)[0]))
#define VK_CALL(call) (call)
#define VKD3D_UNUSED
#define TRACE(...) ((void)0)
#define INFO(...) ((void)0)
#define WARN(...) ((void)0)
#define ERR(...) ((void)0)
#define VKD3D_DEVICE_REPORT_FAULT_AND_BREADCRUMB_IF(...)
#define VKD3D_CONFIG_FLAG_IS_SET(name) (config_flags & name)
#define INSTRUCTION_QA_CHECKS 1
#define DESCRIPTOR_QA_CHECKS 2
#define BREADCRUMBS_TRACE 4
#define VKD3D_COMMAND_BUFFER_SUBMIT_INFO_DEVICE_MASK_DEFAULT 0
#define VKD3D_COMMAND_BUFFER_SUBMIT_INFO_DEVICE_MASK_FALLBACK_QUEUE 2
#define VKD3D_COMMAND_COST_MERGE_THRESHOLD 10
#define VKD3D_WAIT_SEMAPHORES_EXTERNAL 1
#define VKD3D_WAIT_SEMAPHORES_SERIALIZING 2
#define FAILED(hr) ((hr) < 0)
#define vkd3d_free free
#define vkd3d_queue_timeline_trace_complete_execute(...)
#define vkd3d_queue_timeline_trace_begin_execute_overhead(...)
#define vkd3d_queue_timeline_trace_end_execute_overhead(...)

typedef unsigned int UINT;
typedef int HRESULT;
struct vkd3d_queue_timeline_trace_cookie { int unused; };
struct vkd3d_vk_device_procs { int unused; };
struct d3d12_command_allocator { int unused; };
struct dxgi_vk_swap_chain { int unused; };
struct vkd3d_queue
{
    VkQueue vk_queue;
    VkSemaphore submission_timeline;
    uint64_t submission_timeline_count;
    unsigned int vk_family_index;
    bool locked;
};
struct d3d12_device
{
    struct vkd3d_vk_device_procs vk_procs;
    VkDevice vk_device;
    struct { bool NV_low_latency2; } vk_info;
    struct { struct vkd3d_queue *vkd3d_queue; } memory_transfers;
    struct { struct dxgi_vk_swap_chain *low_latency_swapchain; } swapchain_info;
    bool low_latency_swapchain_spinlock, concurrent_transfer_queue;
    void *descriptor_qa_global_info;
};
struct d3d12_command_queue
{
    struct d3d12_device *device;
    struct vkd3d_queue *vkd3d_queue;
    VkSemaphore serializing_semaphore;
    uint64_t last_submission_timeline_value;
    bool stagger_submissions, serializing_semaphore_signaled;
    int fence_worker;
};
struct d3d12_resource { uint32_t initial_layout_transition; };
enum { D3D12_QUERY_HEAP_TYPE_OCCLUSION, D3D12_QUERY_HEAP_TYPE_SO_STATISTICS,
       D3D12_QUERY_HEAP_TYPE_PIPELINE_STATISTICS, D3D12_QUERY_HEAP_TYPE_TIMESTAMP,
       D3D12_QUERY_HEAP_TYPE_COPY_QUEUE_TIMESTAMP };
struct d3d12_query_heap
{
    uint32_t initialized;
    VkQueryPool vk_query_pool;
    struct { unsigned int Count, Type; } desc;
};
enum { VKD3D_INITIAL_TRANSITION_TYPE_RESOURCE, VKD3D_INITIAL_TRANSITION_TYPE_QUERY_HEAP };
struct vkd3d_initial_transition
{
    unsigned int type;
    struct { struct d3d12_resource *resource; bool perform_initial_transition; } resource;
    struct d3d12_query_heap *query_heap;
};
@STRUCTURES@
struct vkd3d_fence_wait_info { VkSemaphore vk_semaphore; uint64_t vk_semaphore_value; };
struct vkd3d_waiting_fence_submission_info
{
    struct d3d12_command_allocator **command_allocators;
    unsigned int num_command_allocators;
};
struct recorded_submit
{
    VkQueue queue;
    VkCommandBufferSubmitInfo cmd[8];
    VkSemaphoreSubmitInfo waits[8], signals[3];
    unsigned int cmd_count, wait_count, signal_count;
    uint64_t present_id;
};
static struct recorded_submit records[256];
static unsigned int record_count, submit_calls, retired, pending[512], reset_count, wait_count;
static unsigned int config_flags, image_barriers, memory_barriers, query_ops, qa_ops, callbacks, finalized;
static uint64_t callback_value;
static bool stagger, fail_allocation, merged_barriers, expect_post_barrier;
static VkResult submit_result;
static VkCommandBuffer recording;
static struct vkd3d_queue normal_queue, fallback_queue;
static struct d3d12_device device;
static struct d3d12_command_queue queue;
static struct dxgi_vk_swap_chain swapchain;
static struct d3d12_command_queue_transition_pool pool;
static VkSemaphoreSubmitInfo gathered_waits[8];

static void *vkd3d_calloc(size_t n, size_t size) { return fail_allocation ? NULL : calloc(n, size); }
static bool vkd3d_array_reserve(void **data, size_t *size, size_t count, size_t stride)
{
    if (*size >= count) return true;
    *data = realloc(*data, count * stride);
    assert(*data);
    *size = count;
    return true;
}
static uint32_t exchange(uint32_t *p, uint32_t value) { uint32_t old = *p; *p = value; return old; }
#define vkd3d_atomic_uint32_exchange_explicit(p, v, order) exchange(p, v)
static bool vk_image_memory_barrier_for_initial_transition(const struct d3d12_resource *r, VkImageMemoryBarrier2 *b)
{
    memset(b, 0, sizeof(*b));
    b->sType = VK_STRUCTURE_TYPE_IMAGE_MEMORY_BARRIER_2;
    b->oldLayout = VK_IMAGE_LAYOUT_UNDEFINED;
    b->newLayout = VK_IMAGE_LAYOUT_GENERAL;
    return true;
}
static VkResult vkWaitSemaphores(VkDevice dev, const VkSemaphoreWaitInfo *info, uint64_t timeout)
{
    unsigned int i, j;
    assert(info->semaphoreCount == 1 && timeout == UINT64_MAX);
    wait_count++;
    if (!info->pValues[0]) return VK_SUCCESS;
    for (i = 0; i < record_count; i++)
        for (j = 0; j < records[i].signal_count; j++)
            if (records[i].signals[j].semaphore == info->pSemaphores[0] &&
                    records[i].signals[j].value >= info->pValues[0])
            {
                if (retired < i + 1) retired = i + 1;
                return VK_SUCCESS;
            }
    assert(!"Waiting for an absent completion signal");
    return VK_ERROR_DEVICE_LOST;
}
static VkResult vkResetCommandBuffer(VkCommandBuffer cmd, VkCommandBufferResetFlags flags)
{
    assert(pending[(uintptr_t)cmd] <= retired);
    reset_count++;
    return VK_SUCCESS;
}
static VkResult vkBeginCommandBuffer(VkCommandBuffer cmd, const VkCommandBufferBeginInfo *info)
{
    assert(!recording && info->flags == VK_COMMAND_BUFFER_USAGE_ONE_TIME_SUBMIT_BIT);
    recording = cmd;
    return VK_SUCCESS;
}
static void vkCmdPipelineBarrier2(VkCommandBuffer cmd, const VkDependencyInfo *info)
{
    unsigned int i;
    assert(recording == cmd);
    for (i = 0; i < info->imageMemoryBarrierCount; i++)
    {
        const VkImageMemoryBarrier2 *b = &info->pImageMemoryBarriers[i];
        assert(b->srcStageMask == (merged_barriers ? VK_PIPELINE_STAGE_2_ALL_COMMANDS_BIT : 0));
        assert(b->dstStageMask == b->srcStageMask);
        assert(b->srcAccessMask == (merged_barriers ? VK_ACCESS_2_MEMORY_WRITE_BIT : 0));
        assert(b->dstAccessMask == (merged_barriers ? VK_ACCESS_2_MEMORY_READ_BIT | VK_ACCESS_2_MEMORY_WRITE_BIT : 0));
        image_barriers++;
    }
    for (i = 0; i < info->memoryBarrierCount; i++)
    {
        const VkMemoryBarrier2 *b = &info->pMemoryBarriers[i];
        assert(merged_barriers && expect_post_barrier && (query_ops || qa_ops));
        assert(b->srcStageMask == VK_PIPELINE_STAGE_2_ALL_COMMANDS_BIT && b->dstStageMask == b->srcStageMask);
        assert(b->srcAccessMask == VK_ACCESS_2_MEMORY_WRITE_BIT);
        assert(b->dstAccessMask == (VK_ACCESS_2_MEMORY_READ_BIT | VK_ACCESS_2_MEMORY_WRITE_BIT));
        expect_post_barrier = false;
        memory_barriers++;
    }
}
static VkResult vkEndCommandBuffer(VkCommandBuffer cmd)
{
    assert(recording == cmd && (!merged_barriers || !expect_post_barrier));
    recording = VK_NULL_HANDLE;
    return VK_SUCCESS;
}
#define QUERY_OP() do { assert(recording == cmd); query_ops++; expect_post_barrier = true; } while (0)
static void vkCmdResetQueryPool(VkCommandBuffer cmd, VkQueryPool qp, uint32_t first, uint32_t count) { QUERY_OP(); }
static void vkCmdBeginQuery(VkCommandBuffer cmd, VkQueryPool qp, uint32_t idx, VkQueryControlFlags flags) { QUERY_OP(); }
static void vkCmdEndQuery(VkCommandBuffer cmd, VkQueryPool qp, uint32_t idx) { QUERY_OP(); }
static void vkCmdWriteTimestamp2(VkCommandBuffer cmd, VkPipelineStageFlags2 stage, VkQueryPool qp, uint32_t idx) { QUERY_OP(); }
static uint32_t vkd3d_descriptor_debug_clear_bloom_filter(void *qa, struct d3d12_device *dev, VkCommandBuffer cmd)
{ assert(recording == cmd); qa_ops++; expect_post_barrier = true; return 0; }
#define vkd3d_descriptor_debug_update_va_timestamp vkd3d_descriptor_debug_clear_bloom_filter

static VkQueue vkd3d_queue_acquire(struct vkd3d_queue *q) { assert(!q->locked); q->locked = true; return q->vk_queue; }
static void vkd3d_queue_release(struct vkd3d_queue *q) { assert(q->locked); q->locked = false; }
static bool d3d12_command_queue_needs_staggered_submissions(struct d3d12_command_queue *q) { return stagger; }
static void d3d12_command_allocator_dec_ref(struct d3d12_command_allocator *a) {}
static void spinlock_acquire(bool *lock) { assert(!*lock); *lock = true; }
static void spinlock_release(bool *lock) { assert(*lock); *lock = false; }
static void dxgi_vk_swap_chain_incref(struct dxgi_vk_swap_chain *s) {}
static void dxgi_vk_swap_chain_decref(struct dxgi_vk_swap_chain *s) {}
static void d3d12_command_queue_gather_wait_semaphores_locked(struct d3d12_command_queue *q, VkSubmitInfo2 *s, unsigned int flags)
{
    unsigned int n = s->waitSemaphoreInfoCount;
    assert(flags == (VKD3D_WAIT_SEMAPHORES_EXTERNAL | VKD3D_WAIT_SEMAPHORES_SERIALIZING));
    if (n) memcpy(gathered_waits, s->pWaitSemaphoreInfos, n * sizeof(*gathered_waits));
    gathered_waits[n++] = (VkSemaphoreSubmitInfo){.sType = VK_STRUCTURE_TYPE_SEMAPHORE_SUBMIT_INFO,
        .semaphore = HANDLE(VkSemaphore, 99), .stageMask = VK_PIPELINE_STAGE_2_ALL_COMMANDS_BIT};
    if (q->serializing_semaphore_signaled)
    {
        gathered_waits[n++] = (VkSemaphoreSubmitInfo){.sType = VK_STRUCTURE_TYPE_SEMAPHORE_SUBMIT_INFO,
            .semaphore = q->serializing_semaphore, .stageMask = VK_PIPELINE_STAGE_2_ALL_COMMANDS_BIT};
        q->serializing_semaphore_signaled = false;
    }
    s->waitSemaphoreInfoCount = n;
    s->pWaitSemaphoreInfos = gathered_waits;
}
static void d3d12_command_queue_finalize_waits(struct d3d12_command_queue *q, VkResult result) { finalized++; }
static VkResult vkQueueSubmit2(VkQueue q, uint32_t count, const VkSubmitInfo2 *submits, VkFence fence)
{
    unsigned int i, j;
    submit_calls++;
    if (submit_result != VK_SUCCESS) return submit_result;
    for (i = 0; i < count; i++)
    {
        const VkSubmitInfo2 *s = &submits[i];
        struct recorded_submit *r = &records[record_count++];
        assert(record_count <= ARRAY_SIZE(records));
        assert(s->commandBufferInfoCount <= ARRAY_SIZE(r->cmd));
        assert(s->signalSemaphoreInfoCount <= ARRAY_SIZE(r->signals));
        assert(s->waitSemaphoreInfoCount <= ARRAY_SIZE(r->waits));
        r->queue = q;
        r->cmd_count = s->commandBufferInfoCount;
        r->wait_count = s->waitSemaphoreInfoCount;
        r->signal_count = s->signalSemaphoreInfoCount;
        if (r->cmd_count) memcpy(r->cmd, s->pCommandBufferInfos, r->cmd_count * sizeof(*r->cmd));
        if (r->wait_count) memcpy(r->waits, s->pWaitSemaphoreInfos, r->wait_count * sizeof(*r->waits));
        if (r->signal_count) memcpy(r->signals, s->pSignalSemaphoreInfos, r->signal_count * sizeof(*r->signals));
        if (s->pNext) r->present_id = ((const VkLatencySubmissionPresentIdNV *)s->pNext)->presentID;
        for (j = 0; j < r->cmd_count; j++) pending[(uintptr_t)r->cmd[j].commandBuffer] = record_count;
    }
    return VK_SUCCESS;
}
static void vkd3d_waiting_fence_release_submission(void) {}
static void *vkd3d_waiting_fence_set_callback(struct vkd3d_fence_wait_info *f, void (*cb)(void), size_t size)
{ static struct vkd3d_waiting_fence_submission_info info; assert(size == sizeof(info)); return &info; }
static HRESULT vkd3d_enqueue_timeline_semaphore(void *worker, struct vkd3d_fence_wait_info *f, const void *cookie)
{ callbacks++; callback_value = f->vk_semaphore_value; return 0; }

/* The extracted execute path assumes a nonempty batch, which the test enforces. */
#if defined(__GNUC__) && !defined(__clang__)
#pragma GCC diagnostic push
#pragma GCC diagnostic ignored "-Wmaybe-uninitialized"
#endif
@FUNCTIONS@
#if defined(__GNUC__) && !defined(__clang__)
#pragma GCC diagnostic pop
#endif

static void reset(void)
{
    unsigned int i;
    free(pool.barriers);
    free(pool.query_heaps);
    memset(&pool, 0, sizeof(pool));
    memset(records, 0, sizeof(records));
    memset(pending, 0, sizeof(pending));
    record_count = submit_calls = retired = reset_count = wait_count = 0;
    config_flags = image_barriers = memory_barriers = query_ops = qa_ops = callbacks = finalized = 0;
    callback_value = 0;
    stagger = fail_allocation = merged_barriers = expect_post_barrier = false;
    submit_result = VK_SUCCESS;
    normal_queue = (struct vkd3d_queue){.vk_queue = HANDLE(VkQueue, 1), .submission_timeline = HANDLE(VkSemaphore, 1)};
    fallback_queue = (struct vkd3d_queue){.vk_queue = HANDLE(VkQueue, 2)};
    device = (struct d3d12_device){.memory_transfers.vkd3d_queue = &fallback_queue};
    queue = (struct d3d12_command_queue){.device = &device, .vkd3d_queue = &normal_queue,
        .serializing_semaphore = HANDLE(VkSemaphore, 2), .serializing_semaphore_signaled = true};
    pool.timeline = HANDLE(VkSemaphore, 3);
    for (i = 0; i < ARRAY_SIZE(pool.cmd); i++)
    {
        pool.cmd[i] = HANDLE(VkCommandBuffer, i + 1);
        pool.fallback_cmd[i] = HANDLE(VkCommandBuffer, i + 33);
    }
}
static struct d3d12_command_queue_submission_execute make_execute(unsigned int count)
{
    struct d3d12_command_queue_submission_execute exec = {0};
    unsigned int i;
    assert(d3d12_command_queue_allocate_command_buffers(&exec, count));
    exec.cmd_count = count;
    for (i = 0; i < count; i++)
    {
        exec.cmd[i] = (VkCommandBufferSubmitInfo){.sType = VK_STRUCTURE_TYPE_COMMAND_BUFFER_SUBMIT_INFO,
            .commandBuffer = HANDLE(VkCommandBuffer, 100 + i)};
        exec.cmd_cost[i] = VKD3D_COMMAND_COST_MERGE_THRESHOLD;
    }
    return exec;
}
static void run_execute(struct d3d12_command_queue_submission_execute *exec, bool legacy)
{
    VkCommandBufferSubmitInfo cmd = {.sType = VK_STRUCTURE_TYPE_COMMAND_BUFFER_SUBMIT_INFO};
    VkSemaphoreSubmitInfo sem = {.sType = VK_STRUCTURE_TYPE_SEMAPHORE_SUBMIT_INFO,
        .semaphore = pool.timeline, .stageMask = VK_PIPELINE_STAGE_2_ALL_COMMANDS_BIT};
    bool fallback = d3d12_command_queue_exec_submit_needs_fallback_queue(&queue, exec);
    assert(exec->cmd_count);
    if (fallback) cmd.deviceMask = VKD3D_COMMAND_BUFFER_SUBMIT_INFO_DEVICE_MASK_FALLBACK_QUEUE;
    merged_barriers = !legacy && d3d12_command_queue_can_merge_transition(exec, &cmd);
    d3d12_command_queue_transition_pool_build(&pool, &device, exec->transitions, exec->transition_count,
            fallback, merged_barriers, &cmd.commandBuffer, &sem.value);
    d3d12_command_queue_execute(&queue, exec, &cmd, &sem, merged_barriers);
    assert(!normal_queue.locked && !fallback_queue.locked);
}
static bool has_signal(unsigned int index, VkSemaphore sem, uint64_t value)
{
    unsigned int i;
    for (i = 0; i < records[index].signal_count; i++)
        if (records[index].signals[i].semaphore == sem && records[index].signals[i].value == value) return true;
    return false;
}
static void test_submit(void)
{
    unsigned int mode;
    for (mode = 0; mode < 7; mode++)
    {
        struct d3d12_resource resource = {.initial_layout_transition = 1};
        struct vkd3d_initial_transition transition = {.resource = {&resource, true}};
        struct d3d12_command_queue_submission_execute exec;
        reset();
        exec = make_execute(2);
        exec.transitions = &transition;
        exec.transition_count = mode == 2 ? 0 : 1;
        exec.split_submission = mode == 3;
        stagger = mode == 4;
        if (mode == 5)
        {
            queue.serializing_semaphore = VK_NULL_HANDLE;
            queue.serializing_semaphore_signaled = false;
        }
        if (mode == 6) submit_result = VK_ERROR_DEVICE_LOST;
        device.vk_info.NV_low_latency2 = true;
        device.swapchain_info.low_latency_swapchain = &swapchain;
        exec.low_latency_frame_id = 123;
        exec.num_command_allocators = 1;
        run_execute(&exec, mode == 1);
        assert(finalized == 1);
        if (mode == 6)
            assert(!record_count && !callbacks && !queue.last_submission_timeline_value);
        else
        {
            assert(callbacks == 1 && callback_value == queue.last_submission_timeline_value);
            assert(records[0].wait_count == (mode == 5 ? 1 : 2));
            assert(records[0].waits[0].semaphore == HANDLE(VkSemaphore, 99));
            if (mode != 5) assert(records[0].waits[1].semaphore == HANDLE(VkSemaphore, 2));
            if (mode != 3) assert(records[0].present_id == 123);
            if (mode == 0 || mode == 5)
            {
                assert(record_count == 1 && submit_calls == 1 && records[0].cmd_count == 3);
                assert(records[0].cmd[0].commandBuffer == pool.cmd[1]);
                assert(records[0].cmd[1].commandBuffer == exec.cmd[0].commandBuffer);
                assert(has_signal(0, pool.timeline, 1));
                assert(records[0].signal_count == (mode == 5 ? 2 : 3));
            }
            else if (mode == 1)
            {
                assert(record_count == 2 && submit_calls == 1);
                assert(records[0].cmd_count == 1 && records[1].cmd_count == 2);
                assert(records[1].wait_count == 1 && records[1].waits[0].semaphore == pool.timeline);
            }
            else if (mode == 2)
                assert(record_count == 1 && records[0].cmd_count == 2 && !pool.timeline_value);
            else if (mode == 3)
                assert(record_count == 3 && submit_calls == 3 && records[0].cmd_count == 1 && records[2].cmd_count == 1);
            else if (mode == 4)
            {
                assert(record_count == 2 && submit_calls == 2 && records[0].cmd_count == 2 && records[1].cmd_count == 1);
                assert(has_signal(0, pool.timeline, 1) && !has_signal(1, pool.timeline, 1));
                assert(!records[1].wait_count && records[1].signals[1].semaphore == queue.serializing_semaphore);
            }
        }
        free(exec.cmd_storage);
    }
}
static void test_fallback(void)
{
    unsigned int concurrent;
    for (concurrent = 0; concurrent < 2; concurrent++)
    {
        struct d3d12_resource resource = {.initial_layout_transition = 1};
        struct vkd3d_initial_transition transition = {.resource = {&resource, true}};
        struct d3d12_command_queue_submission_execute exec;
        reset();
        exec = make_execute(3);
        exec.transitions = &transition;
        exec.transition_count = 1;
        exec.cmd[1].deviceMask = VKD3D_COMMAND_BUFFER_SUBMIT_INFO_DEVICE_MASK_FALLBACK_QUEUE;
        normal_queue.submission_timeline_count = 7;
        device.concurrent_transfer_queue = concurrent;
        run_execute(&exec, false);
        if (concurrent)
        {
            assert(merged_barriers && record_count == 3);
            assert(records[0].queue == normal_queue.vk_queue && records[0].cmd_count == 2);
            assert(has_signal(0, pool.timeline, 1));
            assert(records[1].queue == fallback_queue.vk_queue && records[1].waits[0].value == 8);
            assert(records[2].queue == normal_queue.vk_queue && records[2].waits[0].value == 9);
            assert(queue.last_submission_timeline_value == 10);
        }
        else
        {
            assert(!merged_barriers && record_count == 5);
            assert(records[0].queue == fallback_queue.vk_queue && records[0].cmd[0].commandBuffer == pool.fallback_cmd[1]);
            assert(records[0].waits[0].semaphore == normal_queue.submission_timeline && records[0].waits[0].value == 7);
            assert(records[1].queue == fallback_queue.vk_queue && !records[1].cmd_count);
            assert(records[1].waits[0].semaphore == pool.timeline && records[1].waits[0].value == 1);
            assert(records[2].queue == normal_queue.vk_queue && records[2].waits[0].value == 8);
            assert(records[3].queue == fallback_queue.vk_queue && records[3].waits[0].value == 9);
            assert(records[4].queue == normal_queue.vk_queue && records[4].waits[0].value == 10);
            assert(queue.last_submission_timeline_value == 11);
        }
        free(exec.cmd_storage);
    }
}
static void test_initializers(void)
{
    unsigned int mode;
    for (mode = 0; mode < 7; mode++)
    {
        struct d3d12_query_heap heap = {.desc = {.Count = 2, .Type = mode % 5}};
        struct vkd3d_initial_transition transition = {.type = VKD3D_INITIAL_TRANSITION_TYPE_QUERY_HEAP, .query_heap = &heap};
        struct d3d12_command_queue_submission_execute exec;
        reset();
        exec = make_execute(1);
        if (mode < 5)
        {
            exec.transitions = &transition;
            exec.transition_count = 1;
        }
        else config_flags = mode == 5 ? INSTRUCTION_QA_CHECKS : DESCRIPTOR_QA_CHECKS;
        run_execute(&exec, false);
        assert(memory_barriers == 1 && record_count == 1 && records[0].cmd_count == 2);
        if (mode < 5)
        {
            assert(heap.initialized && query_ops && !qa_ops);
            run_execute(&exec, false);
            assert(pool.timeline_value == 1 && records[1].cmd_count == 1);
        }
        else assert(qa_ops == 1 && !query_ops);
        free(exec.cmd_storage);
    }
}
static void test_reuse(void)
{
    struct d3d12_resource resource = {0};
    struct vkd3d_initial_transition transition = {.resource = {&resource, true}};
    struct d3d12_command_queue_submission_execute exec;
    unsigned int i;
    reset();
    exec = make_execute(1);
    exec.transitions = &transition;
    exec.transition_count = 1;
    for (i = 1; i <= 2 * VKD3D_COMMAND_QUEUE_NUM_TRANSITION_BUFFERS + 1; i++)
    {
        resource.initial_layout_transition = 1;
        run_execute(&exec, false);
        assert(pool.timeline_value == i && record_count == i);
        assert(has_signal(i - 1, pool.timeline, i));
    }
    assert(wait_count == VKD3D_COMMAND_QUEUE_NUM_TRANSITION_BUFFERS + 1);
    d3d12_command_queue_transition_pool_wait(&pool, &device, pool.timeline_value);
    assert(retired == record_count);
    free(exec.cmd_storage);
}
static void test_allocation(void)
{
    struct d3d12_command_queue_submission_execute exec = {0};
    VkCommandBufferSubmitInfo cmd = {0};
    unsigned int i;
    reset();
    fail_allocation = true;
    assert(!d3d12_command_queue_allocate_command_buffers(&exec, 4));
    assert(!exec.cmd_storage);
    fail_allocation = false;
    exec = make_execute(64);
    exec.cmd_storage[0].commandBuffer = HANDLE(VkCommandBuffer, 400);
    for (i = 0; i < 64; i++)
    {
        assert(exec.cmd[i].commandBuffer == HANDLE(VkCommandBuffer, 100 + i));
        assert(exec.cmd_cost[i] == VKD3D_COMMAND_COST_MERGE_THRESHOLD);
    }
    assert(d3d12_command_queue_can_merge_transition(&exec, &cmd));
    exec.split_submission = true;
    assert(!d3d12_command_queue_can_merge_transition(&exec, &cmd));
    exec.split_submission = false;
    cmd.deviceMask = VKD3D_COMMAND_BUFFER_SUBMIT_INFO_DEVICE_MASK_FALLBACK_QUEUE;
    assert(!d3d12_command_queue_can_merge_transition(&exec, &cmd));
    cmd.deviceMask = 0;
    exec.cmd_count = 0;
    assert(!d3d12_command_queue_can_merge_transition(&exec, &cmd));
    free(exec.cmd_storage);
}
int main(void)
{
    test_allocation();
    test_submit();
    test_fallback();
    test_initializers();
    test_reuse();
    reset();
    puts("Transition submission tests passed.");
    return 0;
}
'''

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, default=ROOT / "libs/vkd3d/command.c")
    parser.add_argument("--generate", type=Path)
    parser.add_argument("--build-dir", type=Path)
    parser.add_argument("--sanitize", action="store_true")
    args = parser.parse_args()
    harness = generate(args.source)
    if args.generate:
        args.generate.write_text(harness)
    else:
        build = args.build_dir or Path(tempfile.mkdtemp(prefix="transition-submit-"))
        build.mkdir(parents=True, exist_ok=True)
        source, binary = build / "transition_submit.c", build / "transition_submit"
        source.write_text(harness)
        command = shlex.split(os.environ.get("CC", "cc")) + [
            "-std=c11", "-O1", "-g", "-UNDEBUG", "-Wall", "-Wextra", "-Werror",
            "-Wno-unused-variable", "-Wno-unused-parameter", "-Wno-unused-function",
            "-Wno-unused-but-set-variable",
            "-I" + str(ROOT / "khronos/Vulkan-Headers/include")]
        if args.sanitize:
            command += ["-fsanitize=address,undefined", "-fno-omit-frame-pointer", "-no-pie"]
        subprocess.run(command + [str(source), "-o", str(binary)], check=True, timeout=60)
        subprocess.run([str(binary)], check=True, timeout=10)
        print("Retained harness:", build)
