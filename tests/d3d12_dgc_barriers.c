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

void test_execute_indirect_barrier_states(void)
{
    static const D3D12_RESOURCE_STATES states[] =
    {
        D3D12_RESOURCE_STATE_INDIRECT_ARGUMENT,
        D3D12_RESOURCE_STATE_INDIRECT_ARGUMENT | D3D12_RESOURCE_STATE_NON_PIXEL_SHADER_RESOURCE,
        D3D12_RESOURCE_STATE_COMMON,
        D3D12_RESOURCE_STATE_COMMON,
    };
    static const struct indirect_args
    {
        struct vec4 color;
        D3D12_DRAW_ARGUMENTS draw;
        UINT count;
    } data[] =
    {
        {{1.0f, 0.0f, 0.0f, 1.0f}, {3, 0, 0, 0}, 0},
        {{0.0f, 1.0f, 0.0f, 1.0f}, {3, 1, 0, 0}, 1},
    };
    static const float clear[] = {0.0f, 0.0f, 0.0f, 0.0f};
    D3D12_INDIRECT_ARGUMENT_DESC argument_descs[2];
    D3D12_COMMAND_SIGNATURE_DESC signature_desc;
    ID3D12GraphicsCommandList *writer, *producer;
    ID3D12CommandSignature *signatures[2] = {0};
    ID3D12CommandAllocator *writer_allocator;
    ID3D12CommandList *submit_lists[2];
    struct test_context_desc desc;
    ID3D12Resource *upload, *args;
    struct test_context context;
    unsigned int mode, state, split;
    HRESULT hr;

#include "shaders/render_target/headers/ps_color.h"

    memset(&desc, 0, sizeof(desc));
    desc.no_root_signature = true;
    desc.no_pipeline = true;
    desc.rt_width = 16;
    desc.rt_height = 16;
    if (!init_test_context(&context, &desc))
        return;

    context.root_signature = create_32bit_constants_root_signature(context.device, 0, 4,
            D3D12_SHADER_VISIBILITY_PIXEL);
    context.pipeline_state = create_pipeline_state(context.device, context.root_signature,
            context.render_target_desc.Format, NULL, &ps_color_dxbc, NULL);

    memset(argument_descs, 0, sizeof(argument_descs));
    argument_descs[0].Type = D3D12_INDIRECT_ARGUMENT_TYPE_CONSTANT;
    argument_descs[0].Constant.Num32BitValuesToSet = 4;
    argument_descs[1].Type = D3D12_INDIRECT_ARGUMENT_TYPE_DRAW;
    memset(&signature_desc, 0, sizeof(signature_desc));
    signature_desc.ByteStride = sizeof(D3D12_DRAW_ARGUMENTS);
    signature_desc.NumArgumentDescs = 1;
    signature_desc.pArgumentDescs = &argument_descs[1];
    hr = ID3D12Device_CreateCommandSignature(context.device, &signature_desc, NULL,
            &IID_ID3D12CommandSignature, (void **)&signatures[0]);
    ok(hr == S_OK, "Failed to create draw signature, hr %#x.\n", (int)hr);
    signature_desc.ByteStride = offsetof(struct indirect_args, count);
    signature_desc.NumArgumentDescs = 2;
    signature_desc.pArgumentDescs = argument_descs;
    if (is_vkd3d_proton_device(context.device) &&
            !is_vk_device_extension_supported(context.device, "VK_EXT_device_generated_commands"))
    {
        skip("DGC unsupported, testing plain indirect draws only.\n");
    }
    else
    {
        hr = ID3D12Device_CreateCommandSignature(context.device, &signature_desc, context.root_signature,
                &IID_ID3D12CommandSignature, (void **)&signatures[1]);
        if (FAILED(hr))
            skip("Indirect root constants unsupported, testing plain indirect draws only.\n");
    }

    hr = ID3D12Device_CreateCommandAllocator(context.device, D3D12_COMMAND_LIST_TYPE_DIRECT,
            &IID_ID3D12CommandAllocator, (void **)&writer_allocator);
    ok(hr == S_OK, "Failed to create writer allocator, hr %#x.\n", (int)hr);
    hr = ID3D12Device_CreateCommandList(context.device, 0, D3D12_COMMAND_LIST_TYPE_DIRECT,
            writer_allocator, NULL, &IID_ID3D12GraphicsCommandList, (void **)&writer);
    ok(hr == S_OK, "Failed to create writer list, hr %#x.\n", (int)hr);
    hr = ID3D12GraphicsCommandList_Close(writer);
    ok(hr == S_OK, "Failed to close writer list, hr %#x.\n", (int)hr);
    upload = create_upload_buffer(context.device, sizeof(data), data);

    for (mode = 0; mode < ARRAY_SIZE(signatures); mode++)
    {
        if (!signatures[mode])
            continue;

        for (state = 0; state < ARRAY_SIZE(states); state++)
        {
            for (split = 0; split < 2; split++)
            {
                vkd3d_test_set_context("Constants %u, state %u, split %u", mode, state, split);

                /* Complete initialization separately. Premature preprocessing must see a
                 * valid old record with zero draws, not uninitialized indirect arguments. */
                args = create_default_buffer(context.device, sizeof(data[0]),
                        D3D12_RESOURCE_FLAG_NONE, D3D12_RESOURCE_STATE_COPY_DEST);
                ID3D12GraphicsCommandList_CopyBufferRegion(context.list, args, 0, upload, 0, sizeof(data[0]));
                transition_resource_state(context.list, args,
                        D3D12_RESOURCE_STATE_COPY_DEST, D3D12_RESOURCE_STATE_COMMON);
                hr = ID3D12GraphicsCommandList_Close(context.list);
                ok(hr == S_OK, "Failed to close initialization list, hr %#x.\n", (int)hr);
                exec_command_list(context.queue, context.list);
                wait_queue_idle(context.device, context.queue);
                reset_command_list(context.list, context.allocator);

                if (split)
                    reset_command_list(writer, writer_allocator);
                producer = split ? writer : context.list;
                /* COPY_DEST is implicitly promoted from COMMON. The final COMMON
                 * cases are then implicitly promoted to INDIRECT_ARGUMENT. */
                ID3D12GraphicsCommandList_CopyBufferRegion(producer, args, 0, upload,
                        sizeof(data[0]), sizeof(data[0]));
                if (state == 3)
                {
                    transition_resource_state(producer, args, D3D12_RESOURCE_STATE_COPY_DEST,
                            D3D12_RESOURCE_STATE_NON_PIXEL_SHADER_RESOURCE);
                    transition_resource_state(producer, args, D3D12_RESOURCE_STATE_NON_PIXEL_SHADER_RESOURCE,
                            D3D12_RESOURCE_STATE_COMMON);
                }
                else
                {
                    transition_resource_state(producer, args, D3D12_RESOURCE_STATE_COPY_DEST, states[state]);
                }

                ID3D12GraphicsCommandList_ClearRenderTargetView(context.list, context.rtv, clear, 0, NULL);
                ID3D12GraphicsCommandList_OMSetRenderTargets(context.list, 1, &context.rtv, false, NULL);
                ID3D12GraphicsCommandList_SetGraphicsRootSignature(context.list, context.root_signature);
                ID3D12GraphicsCommandList_SetGraphicsRoot32BitConstants(context.list, 0, 4, &data[1].color, 0);
                ID3D12GraphicsCommandList_SetPipelineState(context.list, context.pipeline_state);
                ID3D12GraphicsCommandList_IASetPrimitiveTopology(context.list, D3D_PRIMITIVE_TOPOLOGY_TRIANGLELIST);
                ID3D12GraphicsCommandList_RSSetViewports(context.list, 1, &context.viewport);
                ID3D12GraphicsCommandList_RSSetScissorRects(context.list, 1, &context.scissor_rect);
                ID3D12GraphicsCommandList_ExecuteIndirect(context.list, signatures[mode], 1, args,
                        mode ? 0 : offsetof(struct indirect_args, draw), args, offsetof(struct indirect_args, count));
                transition_resource_state(context.list, context.render_target,
                        D3D12_RESOURCE_STATE_RENDER_TARGET, D3D12_RESOURCE_STATE_COPY_SOURCE);
                hr = ID3D12GraphicsCommandList_Close(context.list);
                ok(hr == S_OK, "Failed to close draw list, hr %#x.\n", (int)hr);
                if (split)
                {
                    hr = ID3D12GraphicsCommandList_Close(writer);
                    ok(hr == S_OK, "Failed to close writer list, hr %#x.\n", (int)hr);
                    submit_lists[0] = (ID3D12CommandList *)writer;
                    submit_lists[1] = (ID3D12CommandList *)context.list;
                    ID3D12CommandQueue_ExecuteCommandLists(context.queue, 2, submit_lists);
                }
                else
                {
                    exec_command_list(context.queue, context.list);
                }
                wait_queue_idle(context.device, context.queue);
                reset_command_list(context.list, context.allocator);
                check_sub_resource_uint(context.render_target, 0, context.queue, context.list, 0xff00ff00, 0);
                reset_command_list(context.list, context.allocator);
                transition_resource_state(context.list, context.render_target,
                        D3D12_RESOURCE_STATE_COPY_SOURCE, D3D12_RESOURCE_STATE_RENDER_TARGET);
                ID3D12Resource_Release(args);
            }
        }
    }
    vkd3d_test_set_context(NULL);

    ID3D12Resource_Release(upload);
    ID3D12GraphicsCommandList_Release(writer);
    ID3D12CommandAllocator_Release(writer_allocator);
    for (mode = 0; mode < ARRAY_SIZE(signatures); mode++)
        if (signatures[mode])
            ID3D12CommandSignature_Release(signatures[mode]);
    destroy_test_context(&context);
}
