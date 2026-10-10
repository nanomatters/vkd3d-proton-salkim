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

void test_write_buffer_immediate_dgc_ordering(void)
{
    static const D3D12_WRITEBUFFERIMMEDIATE_MODE modes[] =
    {
        D3D12_WRITEBUFFERIMMEDIATE_MODE_DEFAULT,
        D3D12_WRITEBUFFERIMMEDIATE_MODE_MARKER_IN,
        D3D12_WRITEBUFFERIMMEDIATE_MODE_MARKER_OUT,
    };
    static const struct
    {
        UINT location;
        D3D12_DRAW_ARGUMENTS draw;
    } indirect_data = {0, {3, 1, 0, 0}};
    static const D3D12_RESOURCE_STATES read_states[] =
    {
        D3D12_RESOURCE_STATE_PIXEL_SHADER_RESOURCE,
        D3D12_RESOURCE_STATE_PIXEL_SHADER_RESOURCE | D3D12_RESOURCE_STATE_INDIRECT_ARGUMENT,
    };
    static const float red[] = {1.0f, 0.0f, 0.0f, 1.0f};
    static const UINT initial_value = 1;
    D3D12_WRITEBUFFERIMMEDIATE_PARAMETER parameter;
    D3D12_INDIRECT_ARGUMENT_DESC arguments[2];
    D3D12_COMMAND_SIGNATURE_DESC signature_desc;
    D3D12_ROOT_SIGNATURE_DESC root_signature_desc;
    ID3D12GraphicsCommandList2 *command_list2;
    ID3D12CommandSignature *signature = NULL;
    ID3D12Resource *input, *input_upload;
    ID3D12Resource *indirect, *indirect_upload;
    D3D12_ROOT_PARAMETER root_parameters[2];
    ID3D12GraphicsCommandList *command_list;
    struct test_context_desc desc;
    struct test_context context;
    struct resource_readback rb;
    unsigned int m, n, s, x;
    bool has_dgc;
    RECT rect;
    HRESULT hr;

#include "shaders/descriptors/headers/null_srv_buffer.h"

    memset(&desc, 0, sizeof(desc));
    desc.no_root_signature = true;
    desc.no_pipeline = true;
    if (!init_test_context(&context, &desc))
        return;

    command_list = context.list;
    hr = ID3D12GraphicsCommandList_QueryInterface(command_list,
            &IID_ID3D12GraphicsCommandList2, (void **)&command_list2);
    if (FAILED(hr))
    {
        skip("ID3D12GraphicsCommandList2 is not supported.\n");
        destroy_test_context(&context);
        return;
    }

    has_dgc = !is_vkd3d_proton_device(context.device) ||
            is_vk_device_extension_supported(context.device, "VK_EXT_device_generated_commands");

    memset(root_parameters, 0, sizeof(root_parameters));
    root_parameters[0].ParameterType = D3D12_ROOT_PARAMETER_TYPE_32BIT_CONSTANTS;
    root_parameters[0].Constants.Num32BitValues = 1;
    root_parameters[0].ShaderVisibility = D3D12_SHADER_VISIBILITY_PIXEL;
    root_parameters[1].ParameterType = D3D12_ROOT_PARAMETER_TYPE_SRV;
    root_parameters[1].ShaderVisibility = D3D12_SHADER_VISIBILITY_PIXEL;
    memset(&root_signature_desc, 0, sizeof(root_signature_desc));
    root_signature_desc.NumParameters = ARRAY_SIZE(root_parameters);
    root_signature_desc.pParameters = root_parameters;
    hr = create_root_signature(context.device, &root_signature_desc, &context.root_signature);
    ok(hr == S_OK, "Failed to create root signature, hr %#x.\n", (int)hr);
    context.pipeline_state = create_pipeline_state(context.device, context.root_signature,
            context.render_target_desc.Format, NULL, &null_srv_buffer_dxbc, NULL);

    memset(arguments, 0, sizeof(arguments));
    arguments[0].Type = D3D12_INDIRECT_ARGUMENT_TYPE_CONSTANT;
    arguments[0].Constant.Num32BitValuesToSet = 1;
    arguments[1].Type = D3D12_INDIRECT_ARGUMENT_TYPE_DRAW;
    memset(&signature_desc, 0, sizeof(signature_desc));
    signature_desc.ByteStride = sizeof(indirect_data);
    signature_desc.NumArgumentDescs = ARRAY_SIZE(arguments);
    signature_desc.pArgumentDescs = arguments;
    if (has_dgc)
    {
        hr = ID3D12Device_CreateCommandSignature(context.device, &signature_desc,
                context.root_signature, &IID_ID3D12CommandSignature, (void **)&signature);
        ok(hr == S_OK, "Failed to create command signature, hr %#x.\n", (int)hr);
    }

    input = create_default_buffer(context.device, 4,
            D3D12_RESOURCE_FLAG_NONE, D3D12_RESOURCE_STATE_COPY_DEST);
    input_upload = create_upload_buffer(context.device, sizeof(initial_value), &initial_value);
    indirect = create_default_buffer(context.device, sizeof(indirect_data),
            D3D12_RESOURCE_FLAG_NONE, D3D12_RESOURCE_STATE_COPY_DEST);
    indirect_upload = create_upload_buffer(context.device, sizeof(indirect_data), &indirect_data);
    parameter.Dest = ID3D12Resource_GetGPUVirtualAddress(input);
    parameter.Value = 0;

    for (s = 0; s < ARRAY_SIZE(read_states); s++)
    for (m = 0; m < ARRAY_SIZE(modes); m++)
    for (n = 0; n < (has_dgc ? 3 : 1); n++)
    {
        vkd3d_test_set_context("states %#x, mode %u, indirect draws %u", read_states[s], modes[m], n);

        /* Rewrite the argument buffer in this command list so preprocessing
         * cannot be hoisted ahead of the render pass. */
        ID3D12GraphicsCommandList_CopyBufferRegion(command_list, input, 0, input_upload, 0, 4);
        ID3D12GraphicsCommandList_CopyBufferRegion(command_list, indirect, 0,
                indirect_upload, 0, sizeof(indirect_data));
        transition_resource_state(command_list, input, D3D12_RESOURCE_STATE_COPY_DEST, read_states[s]);
        transition_resource_state(command_list, indirect,
                D3D12_RESOURCE_STATE_COPY_DEST, D3D12_RESOURCE_STATE_INDIRECT_ARGUMENT);

        ID3D12GraphicsCommandList_ClearRenderTargetView(command_list, context.rtv, red, 0, NULL);
        ID3D12GraphicsCommandList_OMSetRenderTargets(command_list, 1, &context.rtv, false, NULL);
        ID3D12GraphicsCommandList_SetPipelineState(command_list, context.pipeline_state);
        ID3D12GraphicsCommandList_SetGraphicsRootSignature(command_list, context.root_signature);
        ID3D12GraphicsCommandList_SetGraphicsRoot32BitConstant(command_list, 0, 0, 0);
        ID3D12GraphicsCommandList_SetGraphicsRootShaderResourceView(command_list, 1, parameter.Dest);
        ID3D12GraphicsCommandList_IASetPrimitiveTopology(command_list, D3D_PRIMITIVE_TOPOLOGY_TRIANGLELIST);
        ID3D12GraphicsCommandList_RSSetViewports(command_list, 1, &context.viewport);

        /* Start a real render pass before the indirect draws are captured. */
        set_rect(&rect, 20, 0, 32, 32);
        ID3D12GraphicsCommandList_RSSetScissorRects(command_list, 1, &rect);
        ID3D12GraphicsCommandList_DrawInstanced(command_list, 3, 1, 0, 0);

        for (x = 0; x < 2; x++)
        {
            set_rect(&rect, x * 10, 0, (x + 1) * 10, 32);
            ID3D12GraphicsCommandList_RSSetScissorRects(command_list, 1, &rect);
            if (x < n)
                ID3D12GraphicsCommandList_ExecuteIndirect(command_list, signature, 1, indirect, 0, NULL, 0);
            else
                ID3D12GraphicsCommandList_DrawInstanced(command_list, 3, 1, 0, 0);
        }

        transition_resource_state(command_list, input, read_states[s], D3D12_RESOURCE_STATE_COPY_DEST);
        ID3D12GraphicsCommandList2_WriteBufferImmediate(command_list2, 1, &parameter, &modes[m]);
        transition_resource_state(command_list, input, D3D12_RESOURCE_STATE_COPY_DEST, read_states[s]);

        set_rect(&rect, 20, 0, 32, 32);
        ID3D12GraphicsCommandList_RSSetScissorRects(command_list, 1, &rect);
        ID3D12GraphicsCommandList_DrawInstanced(command_list, 3, 1, 0, 0);
        transition_resource_state(command_list, input, read_states[s], D3D12_RESOURCE_STATE_COPY_DEST);
        transition_resource_state(command_list, indirect,
                D3D12_RESOURCE_STATE_INDIRECT_ARGUMENT, D3D12_RESOURCE_STATE_COPY_DEST);
        transition_resource_state(command_list, context.render_target,
                D3D12_RESOURCE_STATE_RENDER_TARGET, D3D12_RESOURCE_STATE_COPY_SOURCE);
        get_texture_readback_with_command_list(context.render_target, 0, &rb, context.queue, command_list);
        for (x = 0; x < 32; x++)
        {
            UINT value = get_readback_uint(&rb, x, 16, 0);
            UINT expected = x < 20 ? 0xffffffff : 0;
            ok(value == expected, "Pixel %u: got %#x, expected %#x.\n", x, value, expected);
        }
        release_resource_readback(&rb);
        reset_command_list(command_list, context.allocator);
        transition_resource_state(command_list, context.render_target,
                D3D12_RESOURCE_STATE_COPY_SOURCE, D3D12_RESOURCE_STATE_RENDER_TARGET);
    }

    vkd3d_test_set_context(NULL);
    if (signature)
        ID3D12CommandSignature_Release(signature);
    ID3D12Resource_Release(indirect_upload);
    ID3D12Resource_Release(indirect);
    ID3D12Resource_Release(input_upload);
    ID3D12Resource_Release(input);
    ID3D12GraphicsCommandList2_Release(command_list2);
    destroy_test_context(&context);
}

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

void test_write_buffer_immediate_copy_overlap(void)
{
    D3D12_WRITEBUFFERIMMEDIATE_PARAMETER parameters[2];
    ID3D12GraphicsCommandList2 *command_list2;
    D3D12_TEXTURE_COPY_LOCATION dst, src;
    D3D12_SUBRESOURCE_DATA texture_data;
    unsigned int data[64], expected[64];
    struct test_context_desc desc;
    struct test_context context;
    struct resource_readback rb;
    ID3D12Resource *buffer, *upload, *texture;
    unsigned int mode, i;
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

    for (i = 0; i < ARRAY_SIZE(data); i++)
        data[i] = 0xabcd0000u + i;
    upload = create_upload_buffer(context.device, sizeof(data), data);
    texture = create_default_texture2d(context.device, ARRAY_SIZE(data), 1, 1, 1,
            DXGI_FORMAT_R32_UINT, D3D12_RESOURCE_FLAG_NONE, D3D12_RESOURCE_STATE_COPY_DEST);
    texture_data.pData = data;
    texture_data.RowPitch = sizeof(data);
    texture_data.SlicePitch = sizeof(data);
    upload_texture_data(texture, &texture_data, 1, context.queue, context.list);
    reset_command_list(context.list, context.allocator);
    transition_resource_state(context.list, texture,
            D3D12_RESOURCE_STATE_COPY_DEST, D3D12_RESOURCE_STATE_COPY_SOURCE);
    buffer = create_default_buffer(context.device, sizeof(data),
            D3D12_RESOURCE_FLAG_NONE, D3D12_RESOURCE_STATE_COPY_DEST);
    memset(&src, 0, sizeof(src));
    src.pResource = texture;
    src.Type = D3D12_TEXTURE_COPY_TYPE_SUBRESOURCE_INDEX;
    memset(&dst, 0, sizeof(dst));
    dst.pResource = buffer;
    dst.Type = D3D12_TEXTURE_COPY_TYPE_PLACED_FOOTPRINT;
    dst.PlacedFootprint.Footprint.Format = DXGI_FORMAT_R32_UINT;
    dst.PlacedFootprint.Footprint.Width = ARRAY_SIZE(data);
    dst.PlacedFootprint.Footprint.Height = 1;
    dst.PlacedFootprint.Footprint.Depth = 1;
    dst.PlacedFootprint.Footprint.RowPitch = sizeof(data);
    parameters[0].Dest = ID3D12Resource_GetGPUVirtualAddress(buffer) + 5 * sizeof(UINT);
    parameters[0].Value = 0xdead1234;
    parameters[1] = parameters[0];
    parameters[1].Value = 0xbeef5678;

    for (mode = 0; mode < 7; mode++)
    {
        vkd3d_test_set_context("Overlap mode %u", mode);
        memcpy(expected, data, sizeof(data));
        ID3D12GraphicsCommandList_CopyBufferRegion(context.list, buffer, 0, upload, 0, sizeof(data));
        if (mode == 6)
            ID3D12GraphicsCommandList_CopyTextureRegion(context.list, &dst, 0, 0, 0, &src, NULL);

        /* All operations keep the legacy destination in COPY_DEST. DEFAULT
         * immediate writes must order overlapping writes like ordinary copies,
         * without an application barrier between them. */
        if (mode == 3)
        {
            ID3D12GraphicsCommandList2_WriteBufferImmediate(command_list2, 2, parameters, NULL);
            expected[5] = parameters[1].Value;
        }
        else
        {
            ID3D12GraphicsCommandList2_WriteBufferImmediate(command_list2, 1, parameters, NULL);
            expected[5] = parameters[0].Value;
            if (mode == 1)
            {
                ID3D12GraphicsCommandList_CopyBufferRegion(context.list, buffer, 5 * sizeof(UINT),
                        upload, 9 * sizeof(UINT), sizeof(UINT));
                expected[5] = data[9];
            }
            else if (mode == 2)
            {
                ID3D12GraphicsCommandList2_WriteBufferImmediate(command_list2, 1, &parameters[1], NULL);
                expected[5] = parameters[1].Value;
            }
            else if (mode == 4)
            {
                ID3D12GraphicsCommandList_CopyBufferRegion(context.list, buffer, 7 * sizeof(UINT),
                        upload, 11 * sizeof(UINT), sizeof(UINT));
                expected[7] = data[11];
            }
            else if (mode == 5)
            {
                ID3D12GraphicsCommandList_CopyTextureRegion(context.list, &dst, 0, 0, 0, &src, NULL);
                memcpy(expected, data, sizeof(data));
            }
        }

        transition_resource_state(context.list, buffer,
                D3D12_RESOURCE_STATE_COPY_DEST, D3D12_RESOURCE_STATE_COPY_SOURCE);
        get_buffer_readback_with_command_list(buffer, DXGI_FORMAT_R32_UINT, &rb, context.queue, context.list);
        for (i = 0; i < ARRAY_SIZE(expected); i++)
            ok(get_readback_uint(&rb, i, 0, 0) == expected[i], "Word %u: got %#x, expected %#x.\n",
                    i, get_readback_uint(&rb, i, 0, 0), expected[i]);
        release_resource_readback(&rb);
        reset_command_list(context.list, context.allocator);
        /* Buffers decay to COMMON after the completed execution. The next
         * copy promotes to COPY_DEST without requiring another transition. */
    }
    vkd3d_test_set_context(NULL);
    ID3D12Resource_Release(buffer);
    ID3D12Resource_Release(texture);
    ID3D12Resource_Release(upload);
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
