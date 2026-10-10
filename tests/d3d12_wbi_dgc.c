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
