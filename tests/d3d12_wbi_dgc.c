/*
 * Copyright 2026 Erhan Bilgili
 *
 * This library is free software; you can redistribute it and/or
 * modify it under the terms of the GNU Lesser General Public
 * License as published by the Free Software Foundation; either
 * version 2.1 of the License, or (at your option) any later version.
 */

#define VKD3D_DBG_CHANNEL VKD3D_DBG_CHANNEL_API
#include "d3d12_crosstest.h"

void test_write_buffer_immediate_mixed_modes(void)
{
    static const D3D12_WRITEBUFFERIMMEDIATE_MODE modes[] =
    {
        D3D12_WRITEBUFFERIMMEDIATE_MODE_DEFAULT,
        D3D12_WRITEBUFFERIMMEDIATE_MODE_MARKER_OUT,
        D3D12_WRITEBUFFERIMMEDIATE_MODE_DEFAULT,
    };
    static const UINT initial_values[] = {0, 0, 0, 0};
    D3D12_WRITEBUFFERIMMEDIATE_PARAMETER parameters[3];
    ID3D12GraphicsCommandList2 *command_list2;
    struct test_context_desc desc;
    struct test_context context;
    struct resource_readback rb;
    ID3D12Resource *buffers[3];
    unsigned int i, j;
    HRESULT hr;

    memset(&desc, 0, sizeof(desc));
    desc.no_render_target = true;
    desc.no_root_signature = true;
    desc.no_pipeline = true;
    if (!init_test_context(&context, &desc))
        return;
    hr = ID3D12GraphicsCommandList_QueryInterface(context.list,
            &IID_ID3D12GraphicsCommandList2, (void **)&command_list2);
    if (FAILED(hr))
    {
        skip("ID3D12GraphicsCommandList2 is not supported.\n");
        destroy_test_context(&context);
        return;
    }

    for (i = 0; i < ARRAY_SIZE(buffers); i++)
    {
        buffers[i] = create_default_buffer(context.device, sizeof(initial_values),
                D3D12_RESOURCE_FLAG_NONE, D3D12_RESOURCE_STATE_COPY_DEST);
        upload_buffer_data(buffers[i], 0, sizeof(initial_values), initial_values, context.queue, context.list);
        reset_command_list(context.list, context.allocator);
        parameters[i].Dest = ID3D12Resource_GetGPUVirtualAddress(buffers[i]) + sizeof(UINT);
        parameters[i].Value = 0x12340000u + i;
    }

    ID3D12GraphicsCommandList2_WriteBufferImmediate(command_list2,
            ARRAY_SIZE(parameters), parameters, modes);
    for (i = 0; i < ARRAY_SIZE(buffers); i++)
        transition_resource_state(context.list, buffers[i],
                D3D12_RESOURCE_STATE_COPY_DEST, D3D12_RESOURCE_STATE_COPY_SOURCE);
    for (i = 0; i < ARRAY_SIZE(buffers); i++)
    {
        get_buffer_readback_with_command_list(buffers[i], DXGI_FORMAT_R32_UINT,
                &rb, context.queue, context.list);
        for (j = 0; j < ARRAY_SIZE(initial_values); j++)
        {
            UINT value = get_readback_uint(&rb, j, 0, 0);
            UINT expected = j == 1 ? parameters[i].Value : 0;
            ok(value == expected, "Buffer %u, word %u: got %#x, expected %#x.\n", i, j, value, expected);
        }
        release_resource_readback(&rb);
        reset_command_list(context.list, context.allocator);
        ID3D12Resource_Release(buffers[i]);
    }

    ID3D12GraphicsCommandList2_Release(command_list2);
    destroy_test_context(&context);
}

void test_write_buffer_immediate_enhanced_barriers(void)
{
    D3D12_WRITEBUFFERIMMEDIATE_PARAMETER parameter;
    D3D12_FEATURE_DATA_D3D12_OPTIONS12 features;
    ID3D12GraphicsCommandList2 *command_list2;
    ID3D12GraphicsCommandList7 *command_list7;
    D3D12_GLOBAL_BARRIER global_barrier;
    D3D12_BUFFER_BARRIER buffer_barrier;
    D3D12_BARRIER_GROUP barrier_group;
    struct test_context_desc desc;
    struct test_context context;
    struct resource_readback rb;
    ID3D12Resource *buffer;
    unsigned int i;
    HRESULT hr;

    memset(&desc, 0, sizeof(desc));
    desc.no_render_target = true;
    desc.no_root_signature = true;
    desc.no_pipeline = true;
    if (!init_test_context(&context, &desc))
        return;
    if (FAILED(ID3D12Device_CheckFeatureSupport(context.device,
            D3D12_FEATURE_D3D12_OPTIONS12, &features, sizeof(features))) ||
            !features.EnhancedBarriersSupported)
    {
        skip("Enhanced barriers are not supported.\n");
        destroy_test_context(&context);
        return;
    }

    hr = ID3D12GraphicsCommandList_QueryInterface(context.list,
            &IID_ID3D12GraphicsCommandList2, (void **)&command_list2);
    ok(hr == S_OK, "Failed to query command list2, hr %#x.\n", (int)hr);
    hr = ID3D12GraphicsCommandList_QueryInterface(context.list,
            &IID_ID3D12GraphicsCommandList7, (void **)&command_list7);
    ok(hr == S_OK, "Failed to query command list7, hr %#x.\n", (int)hr);
    buffer = create_default_buffer2(context.device, 4, D3D12_RESOURCE_FLAG_NONE);
    parameter.Dest = ID3D12Resource_GetGPUVirtualAddress(buffer);

    memset(&global_barrier, 0, sizeof(global_barrier));
    global_barrier.SyncBefore = D3D12_BARRIER_SYNC_COPY;
    global_barrier.SyncAfter = D3D12_BARRIER_SYNC_COPY;
    global_barrier.AccessBefore = D3D12_BARRIER_ACCESS_COPY_DEST;
    global_barrier.AccessAfter = D3D12_BARRIER_ACCESS_COPY_SOURCE;
    memset(&buffer_barrier, 0, sizeof(buffer_barrier));
    buffer_barrier.SyncBefore = global_barrier.SyncBefore;
    buffer_barrier.SyncAfter = global_barrier.SyncAfter;
    buffer_barrier.AccessBefore = global_barrier.AccessBefore;
    buffer_barrier.AccessAfter = global_barrier.AccessAfter;
    buffer_barrier.pResource = buffer;
    buffer_barrier.Size = UINT64_MAX;
    memset(&barrier_group, 0, sizeof(barrier_group));
    barrier_group.NumBarriers = 1;

    for (i = 0; i < 2; i++)
    {
        parameter.Value = 0xfeed0000u + i;
        ID3D12GraphicsCommandList2_WriteBufferImmediate(command_list2, 1, &parameter, NULL);
        if (i)
        {
            barrier_group.Type = D3D12_BARRIER_TYPE_BUFFER;
            barrier_group.pBufferBarriers = &buffer_barrier;
        }
        else
        {
            barrier_group.Type = D3D12_BARRIER_TYPE_GLOBAL;
            barrier_group.pGlobalBarriers = &global_barrier;
        }
        ID3D12GraphicsCommandList7_Barrier(command_list7, 1, &barrier_group);
        get_buffer_readback_with_command_list(buffer, DXGI_FORMAT_R32_UINT, &rb, context.queue, context.list);
        ok(get_readback_uint(&rb, 0, 0, 0) == parameter.Value,
                "Barrier type %u: got %#x, expected %#x.\n",
                barrier_group.Type, get_readback_uint(&rb, 0, 0, 0), parameter.Value);
        release_resource_readback(&rb);
        reset_command_list(context.list, context.allocator);
    }

    ID3D12Resource_Release(buffer);
    ID3D12GraphicsCommandList7_Release(command_list7);
    ID3D12GraphicsCommandList2_Release(command_list2);
    destroy_test_context(&context);
}
