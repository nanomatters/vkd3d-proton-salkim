/*
 * Copyright 2026 Erhan Bilgili
 *
 * This library is free software; you can redistribute it and/or
 * modify it under the terms of the GNU Lesser General Public
 * License as published by the Free Software Foundation; either
 * version 2.1 of the License, or (at your option) any later version.
 *
 * This library is distributed in the hope that it will be useful,
 * but WITHOUT ANY WARRANTY; without even the implied warranty of
 * MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE. See the GNU
 * Lesser General Public License for more details.
 */

#define VKD3D_DBG_CHANNEL VKD3D_DBG_CHANNEL_API
#include "d3d12_crosstest.h"

struct dgc_state_test
{
    struct test_context context;
    ID3D12CommandSignature *signature;
    ID3D12Resource *upload, *arguments;
    ID3D12QueryHeap *queries;
};

static bool init_dgc_state_test(struct dgc_state_test *test, unsigned int instance_count)
{
    D3D12_INDIRECT_ARGUMENT_DESC args[2] = {{0}};
    D3D12_ROOT_PARAMETER parameter = {0};
    D3D12_ROOT_SIGNATURE_DESC root_desc = {0};
    D3D12_COMMAND_SIGNATURE_DESC signature_desc = {0};
    D3D12_QUERY_HEAP_DESC query_desc = {0};
    struct test_context_desc desc = {0};
    struct
    {
        float depth, slope;
        D3D12_DRAW_ARGUMENTS draw;
    } data = {0.5f, 0.0f, {3, instance_count, 0, 0}};
    HRESULT hr;

    memset(test, 0, sizeof(*test));
    desc.no_pipeline = true;
    desc.no_root_signature = true;
    desc.rt_width = desc.rt_height = 16;
    if (!init_test_context(&test->context, &desc))
        return false;

    if (is_vkd3d_proton_device(test->context.device) &&
            !is_vk_device_extension_supported(test->context.device, "VK_EXT_device_generated_commands"))
    {
        skip("Graphics DGC is not supported.\n");
        destroy_test_context(&test->context);
        return false;
    }

    parameter.ParameterType = D3D12_ROOT_PARAMETER_TYPE_32BIT_CONSTANTS;
    parameter.Constants.Num32BitValues = 2;
    root_desc.NumParameters = 1;
    root_desc.pParameters = &parameter;
    hr = create_root_signature(test->context.device, &root_desc, &test->context.root_signature);
    ok(hr == S_OK, "Failed to create root signature, hr %#x.\n", (int)hr);

    args[0].Type = D3D12_INDIRECT_ARGUMENT_TYPE_CONSTANT;
    args[0].Constant.Num32BitValuesToSet = 2;
    args[1].Type = D3D12_INDIRECT_ARGUMENT_TYPE_DRAW;
    signature_desc.ByteStride = sizeof(data);
    signature_desc.NumArgumentDescs = ARRAY_SIZE(args);
    signature_desc.pArgumentDescs = args;
    hr = ID3D12Device_CreateCommandSignature(test->context.device, &signature_desc,
            test->context.root_signature, &IID_ID3D12CommandSignature, (void **)&test->signature);
    ok(hr == S_OK, "Failed to create command signature, hr %#x.\n", (int)hr);

    test->upload = create_upload_buffer(test->context.device, sizeof(data), &data);
    test->arguments = create_default_buffer(test->context.device, sizeof(data),
            D3D12_RESOURCE_FLAG_NONE, D3D12_RESOURCE_STATE_COPY_DEST);
    query_desc.Type = D3D12_QUERY_HEAP_TYPE_PIPELINE_STATISTICS;
    query_desc.Count = 1;
    hr = ID3D12Device_CreateQueryHeap(test->context.device, &query_desc,
            &IID_ID3D12QueryHeap, (void **)&test->queries);
    ok(hr == S_OK, "Failed to create query heap, hr %#x.\n", (int)hr);
    return true;
}

static void begin_dgc_state_test(struct dgc_state_test *test)
{
    ID3D12GraphicsCommandList *list = test->context.list;
    const float clear[] = {0, 0, 0, 0};

    /* A live query prevents a new command-buffer sequence from moving
     * preprocessing ahead of this explicit argument-buffer dependency. */
    ID3D12GraphicsCommandList_BeginQuery(list, test->queries, D3D12_QUERY_TYPE_PIPELINE_STATISTICS, 0);
    ID3D12GraphicsCommandList_CopyBufferRegion(list, test->arguments, 0, test->upload, 0,
            ID3D12Resource_GetDesc(test->arguments).Width);
    transition_resource_state(list, test->arguments,
            D3D12_RESOURCE_STATE_COPY_DEST, D3D12_RESOURCE_STATE_INDIRECT_ARGUMENT);
    ID3D12GraphicsCommandList_OMSetRenderTargets(list, 1, &test->context.rtv, true, NULL);
    ID3D12GraphicsCommandList_ClearRenderTargetView(list, test->context.rtv, clear, 0, NULL);
    ID3D12GraphicsCommandList_SetGraphicsRootSignature(list, test->context.root_signature);
    ID3D12GraphicsCommandList_SetPipelineState(list, test->context.pipeline_state);
    ID3D12GraphicsCommandList_IASetPrimitiveTopology(list, D3D_PRIMITIVE_TOPOLOGY_TRIANGLELIST);
    ID3D12GraphicsCommandList_RSSetViewports(list, 1, &test->context.viewport);
    ID3D12GraphicsCommandList_RSSetScissorRects(list, 1, &test->context.scissor_rect);
}

static void destroy_dgc_state_test(struct dgc_state_test *test)
{
    ID3D12QueryHeap_Release(test->queries);
    ID3D12Resource_Release(test->arguments);
    ID3D12Resource_Release(test->upload);
    ID3D12CommandSignature_Release(test->signature);
    destroy_test_context(&test->context);
}

void test_execute_indirect_dynamic_depth_bias(void)
{
    D3D12_FEATURE_DATA_D3D12_OPTIONS16 options = {0};
    D3D12_GRAPHICS_PIPELINE_STATE_DESC pipeline_desc;
    ID3D12GraphicsCommandList9 *list9;
    struct depth_stencil_resource depth;
    struct dgc_state_test test;
    const float constants[] = {0.5f, 0.0f};
    unsigned int count, i;
    HRESULT hr;

#include "shaders/pso/headers/vs_dynamic_depth_bias.h"

    if (!init_dgc_state_test(&test, 0))
        return;
    ID3D12Device_CheckFeatureSupport(test.context.device, D3D12_FEATURE_D3D12_OPTIONS16,
            &options, sizeof(options));
    if (!options.DynamicDepthBiasSupported)
    {
        skip("Dynamic depth bias is not supported.\n");
        destroy_dgc_state_test(&test);
        return;
    }
    hr = ID3D12GraphicsCommandList_QueryInterface(test.context.list,
            &IID_ID3D12GraphicsCommandList9, (void **)&list9);
    ok(hr == S_OK, "Failed to get command list interface, hr %#x.\n", (int)hr);
    init_pipeline_state_desc(&pipeline_desc, test.context.root_signature,
            DXGI_FORMAT_R8G8B8A8_UNORM, &vs_dynamic_depth_bias_dxbc, NULL, NULL);
    pipeline_desc.Flags = D3D12_PIPELINE_STATE_FLAG_DYNAMIC_DEPTH_BIAS;
    pipeline_desc.DSVFormat = DXGI_FORMAT_D32_FLOAT;
    pipeline_desc.DepthStencilState.DepthEnable = true;
    pipeline_desc.DepthStencilState.DepthWriteMask = D3D12_DEPTH_WRITE_MASK_ZERO;
    pipeline_desc.DepthStencilState.DepthFunc = D3D12_COMPARISON_FUNC_GREATER;
    hr = ID3D12Device_CreateGraphicsPipelineState(test.context.device, &pipeline_desc,
            &IID_ID3D12PipelineState, (void **)&test.context.pipeline_state);
    ok(hr == S_OK, "Failed to create pipeline, hr %#x.\n", (int)hr);
    init_depth_stencil(&depth, test.context.device, 16, 16, 1, 1,
            DXGI_FORMAT_D32_FLOAT, DXGI_FORMAT_UNKNOWN, NULL);

    for (count = 1; count <= 2; count++)
    {
        vkd3d_test_set_context("%u deferred draws", count);
        begin_dgc_state_test(&test);
        ID3D12GraphicsCommandList_OMSetRenderTargets(test.context.list, 1,
                &test.context.rtv, true, &depth.dsv_handle);
        ID3D12GraphicsCommandList_ClearDepthStencilView(test.context.list, depth.dsv_handle,
                D3D12_CLEAR_FLAG_DEPTH, 0.5f, 0, 0, NULL);
        ID3D12GraphicsCommandList_SetGraphicsRoot32BitConstants(test.context.list, 0, 2, constants, 0);
        ID3D12GraphicsCommandList_DrawInstanced(test.context.list, 3, 1, 0, 0);
        for (i = 0; i < count; i++)
            ID3D12GraphicsCommandList_ExecuteIndirect(test.context.list, test.signature, 1,
                    test.arguments, 0, NULL, 0);

        /* The indirect records contain zero instances. Only this direct draw
         * should write, and it requires the dynamic override to survive replay. */
        ID3D12GraphicsCommandList9_RSSetDepthBias(list9, 16.0f, 0.0f, 0.0f);
        ID3D12GraphicsCommandList_SetGraphicsRoot32BitConstants(test.context.list, 0, 2, constants, 0);
        ID3D12GraphicsCommandList_DrawInstanced(test.context.list, 3, 1, 0, 0);
        ID3D12GraphicsCommandList_EndQuery(test.context.list, test.queries, D3D12_QUERY_TYPE_PIPELINE_STATISTICS, 0);
        transition_resource_state(test.context.list, test.context.render_target,
                D3D12_RESOURCE_STATE_RENDER_TARGET, D3D12_RESOURCE_STATE_COPY_SOURCE);
        check_sub_resource_uint(test.context.render_target, 0, test.context.queue, test.context.list, 0xff00ff00, 0);
        reset_command_list(test.context.list, test.context.allocator);
        transition_resource_state(test.context.list, test.context.render_target,
                D3D12_RESOURCE_STATE_COPY_SOURCE, D3D12_RESOURCE_STATE_RENDER_TARGET);
        transition_resource_state(test.context.list, test.arguments,
                D3D12_RESOURCE_STATE_INDIRECT_ARGUMENT, D3D12_RESOURCE_STATE_COPY_DEST);
    }

    vkd3d_test_set_context(NULL);
    ID3D12GraphicsCommandList9_Release(list9);
    destroy_depth_stencil(&depth);
    destroy_dgc_state_test(&test);
}

void test_execute_indirect_scissor_after_target_change(void)
{
    struct dgc_state_test test;
    ID3D12Resource *small_target;
    ID3D12DescriptorHeap *heap;
    D3D12_CPU_DESCRIPTOR_HANDLE rtv;
    const float constants[] = {0, 0};
    unsigned int i;

    if (!init_dgc_state_test(&test, 1))
        return;
    test.context.pipeline_state = create_pipeline_state(test.context.device, test.context.root_signature,
            DXGI_FORMAT_R8G8B8A8_UNORM, NULL, NULL, NULL);
    small_target = create_default_texture2d(test.context.device, 4, 4, 1, 1, DXGI_FORMAT_R8G8B8A8_UNORM,
            D3D12_RESOURCE_FLAG_ALLOW_RENDER_TARGET, D3D12_RESOURCE_STATE_RENDER_TARGET);
    heap = create_cpu_descriptor_heap(test.context.device, D3D12_DESCRIPTOR_HEAP_TYPE_RTV, 1);
    rtv = ID3D12DescriptorHeap_GetCPUDescriptorHandleForHeapStart(heap);
    ID3D12Device_CreateRenderTargetView(test.context.device, small_target, NULL, rtv);

    begin_dgc_state_test(&test);
    ID3D12GraphicsCommandList_OMSetRenderTargets(test.context.list, 1, &rtv, true, NULL);
    ID3D12GraphicsCommandList_SetGraphicsRoot32BitConstants(test.context.list, 0, 2, constants, 0);
    ID3D12GraphicsCommandList_DrawInstanced(test.context.list, 3, 1, 0, 0);
    ID3D12GraphicsCommandList_OMSetRenderTargets(test.context.list, 1, &test.context.rtv, true, NULL);
    for (i = 0; i < 2; i++)
        ID3D12GraphicsCommandList_ExecuteIndirect(test.context.list, test.signature, 1, test.arguments, 0, NULL, 0);
    ID3D12GraphicsCommandList_EndQuery(test.context.list, test.queries, D3D12_QUERY_TYPE_PIPELINE_STATISTICS, 0);
    transition_resource_state(test.context.list, test.context.render_target,
            D3D12_RESOURCE_STATE_RENDER_TARGET, D3D12_RESOURCE_STATE_COPY_SOURCE);
    check_sub_resource_uint(test.context.render_target, 0, test.context.queue, test.context.list, 0xff00ff00, 0);

    ID3D12Resource_Release(small_target);
    ID3D12DescriptorHeap_Release(heap);
    destroy_dgc_state_test(&test);
}

void test_execute_indirect_dynamic_strip_cut(void)
{
    static const uint16_t indices[] = {0, 1, 0xffff, 2};
    const D3D12_RECT empty = {0, 0, 0, 0};
    D3D12_FEATURE_DATA_D3D12_OPTIONS15 options = {0};
    D3D12_GRAPHICS_PIPELINE_STATE_DESC pipeline_desc;
    ID3D12GraphicsCommandList9 *list9;
    D3D12_INDEX_BUFFER_VIEW view;
    ID3D12Resource *index_buffer;
    struct dgc_state_test test;
    HRESULT hr;

    if (!init_dgc_state_test(&test, 0))
        return;
    ID3D12Device_CheckFeatureSupport(test.context.device, D3D12_FEATURE_D3D12_OPTIONS15,
            &options, sizeof(options));
    if (!options.DynamicIndexBufferStripCutSupported)
    {
        skip("Dynamic index buffer strip cut is not supported.\n");
        destroy_dgc_state_test(&test);
        return;
    }
    hr = ID3D12GraphicsCommandList_QueryInterface(test.context.list,
            &IID_ID3D12GraphicsCommandList9, (void **)&list9);
    ok(hr == S_OK, "Failed to get command list interface, hr %#x.\n", (int)hr);
    init_pipeline_state_desc(&pipeline_desc, test.context.root_signature,
            DXGI_FORMAT_R8G8B8A8_UNORM, NULL, NULL, NULL);
    pipeline_desc.Flags = D3D12_PIPELINE_STATE_FLAG_DYNAMIC_INDEX_BUFFER_STRIP_CUT;
    pipeline_desc.RasterizerState.CullMode = D3D12_CULL_MODE_NONE;
    hr = ID3D12Device_CreateGraphicsPipelineState(test.context.device, &pipeline_desc,
            &IID_ID3D12PipelineState, (void **)&test.context.pipeline_state);
    ok(hr == S_OK, "Failed to create pipeline, hr %#x.\n", (int)hr);
    index_buffer = create_upload_buffer(test.context.device, sizeof(indices), indices);
    view.BufferLocation = ID3D12Resource_GetGPUVirtualAddress(index_buffer);
    view.SizeInBytes = sizeof(indices);
    view.Format = DXGI_FORMAT_R16_UINT;

    begin_dgc_state_test(&test);
    ID3D12GraphicsCommandList_IASetPrimitiveTopology(test.context.list, D3D_PRIMITIVE_TOPOLOGY_TRIANGLESTRIP);
    ID3D12GraphicsCommandList_IASetIndexBuffer(test.context.list, &view);
    ID3D12GraphicsCommandList_RSSetScissorRects(test.context.list, 1, &empty);
    ID3D12GraphicsCommandList_DrawInstanced(test.context.list, 3, 1, 0, 0);
    ID3D12GraphicsCommandList_ExecuteIndirect(test.context.list, test.signature, 1, test.arguments, 0, NULL, 0);

    /* The restart splits the index buffer into strips too short to produce
     * triangles. Losing this override during replay would produce pixels. */
    ID3D12GraphicsCommandList9_IASetIndexBufferStripCutValue(list9, D3D12_INDEX_BUFFER_STRIP_CUT_VALUE_0xFFFF);
    ID3D12GraphicsCommandList_RSSetScissorRects(test.context.list, 1, &test.context.scissor_rect);
    ID3D12GraphicsCommandList_DrawIndexedInstanced(test.context.list, ARRAY_SIZE(indices), 1, 0, 0, 0);
    ID3D12GraphicsCommandList_EndQuery(test.context.list, test.queries, D3D12_QUERY_TYPE_PIPELINE_STATISTICS, 0);
    transition_resource_state(test.context.list, test.context.render_target,
            D3D12_RESOURCE_STATE_RENDER_TARGET, D3D12_RESOURCE_STATE_COPY_SOURCE);
    check_sub_resource_uint(test.context.render_target, 0, test.context.queue, test.context.list, 0, 0);

    ID3D12Resource_Release(index_buffer);
    ID3D12GraphicsCommandList9_Release(list9);
    destroy_dgc_state_test(&test);
}
