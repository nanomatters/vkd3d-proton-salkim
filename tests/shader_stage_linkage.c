/* Copyright 2026 Erhan Bilgili
 * SPDX-License-Identifier: LGPL-2.1-or-later */

#ifdef NDEBUG
#undef NDEBUG
#endif
#define VKD3D_DBG_CHANNEL VKD3D_DBG_CHANNEL_SHADER
#include "vkd3d_shader.h"
#include <spirv/unified1/spirv.h>
#include <assert.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

/* Record device-dependent settings while using the production PSO initializer. */
#undef VKD3D_CONFIG_FLAG_IS_SET
#define VKD3D_CONFIG_FLAG_IS_SET(flag) false
struct vkd3d_nv_shader_extn { uint32_t uav_slot, uav_space; };
struct d3d12_device
{
    struct { unsigned int shader_extension_count; const enum vkd3d_shader_target_extension *shader_extensions; } vk_info;
    struct { struct { uint32_t WaveLaneCountMin, WaveLaneCountMax; } options1; } d3d12_caps;
    struct { struct vkd3d_shader_quirk_info quirks; } workarounds;
    struct { VkPhysicalDeviceVulkan12Properties vulkan_1_2_properties; VkPhysicalDeviceProperties2 properties2; } device_info;
};
struct d3d12_pipeline_state
{
    struct
    {
        struct
        {
            unsigned int shader_parameters_count;
            const struct vkd3d_shader_parameter *shader_parameters;
            bool is_dual_source_blending;
            unsigned int ps_output_swizzle[8];
        } cached_desc;
        unsigned int rt_count;
        VkShaderStageFlags stage_flags;
        struct { uint32_t view_mask; } multiview;
    } graphics;
};
static struct vkd3d_nv_shader_extn d3d12_device_get_nv_shader_extn(struct d3d12_device *device)
{
    return (struct vkd3d_nv_shader_extn) { UINT32_MAX, UINT32_MAX };
}
static bool d3d12_device_supports_required_subgroup_size_for_stage(struct d3d12_device *device, VkShaderStageFlagBits stage)
{
    return false;
}
#include "shader_stage_compile_arguments.h"
#include "shaders/stage_linkage/headers/layer_viewport.h"
#include "shaders/render_target/headers/vs_no_attachments.h"

struct io_variable
{
    unsigned int semantic, storage, builtin, location, component;
};
struct stage_io
{
    struct io_variable input[2], output[2];
};

static struct stage_io read_interface(const struct vkd3d_shader_code *spirv)
{
    const uint32_t *words = spirv->code;
    struct io_variable *variables;
    struct stage_io result;
    unsigned int i, count, opcode, id;

    assert(spirv->size >= 5 * sizeof(*words));
    assert(words[0] == SpvMagicNumber);
    variables = calloc(words[3], sizeof(*variables));
    assert(variables);
    memset(&result, 0, sizeof(result));
    for (i = 0; i < words[3]; i++)
    {
        variables[i].storage = UINT32_MAX;
        variables[i].builtin = UINT32_MAX;
        variables[i].location = UINT32_MAX;
    }

    for (i = 5; i < spirv->size / sizeof(*words); i += count)
    {
        opcode = words[i] & 0xffff;
        count = words[i] >> 16;
        assert(count && i + count <= spirv->size / sizeof(*words));
        if (opcode == SpvOpName)
        {
            id = words[i + 1];
            assert(id < words[3]);
            if (!strcmp((const char *)&words[i + 2], "SV_RenderTargetArrayIndex"))
                variables[id].semantic = 1;
            else if (!strcmp((const char *)&words[i + 2], "SV_ViewportArrayIndex"))
                variables[id].semantic = 2;
        }
        else if (opcode == SpvOpDecorate)
        {
            id = words[i + 1];
            assert(id < words[3]);
            if (words[i + 2] == SpvDecorationBuiltIn)
                variables[id].builtin = words[i + 3];
            else if (words[i + 2] == SpvDecorationLocation)
                variables[id].location = words[i + 3];
            else if (words[i + 2] == SpvDecorationComponent)
                variables[id].component = words[i + 3];
        }
        else if (opcode == SpvOpVariable)
        {
            id = words[i + 2];
            assert(id < words[3]);
            variables[id].storage = words[i + 3];
        }
    }

    for (i = 0; i < words[3]; i++)
    {
        if (!variables[i].semantic)
            continue;
        id = variables[i].semantic - 1;
        if (variables[i].storage == SpvStorageClassInput)
            result.input[id] = variables[i];
        else if (variables[i].storage == SpvStorageClassOutput)
            result.output[id] = variables[i];
    }
    free(variables);
    return result;
}

static struct stage_io compile_stage(const D3D12_SHADER_BYTECODE *bytecode, VkShaderStageFlagBits stage,
        VkShaderStageFlags stages, bool standalone)
{
    struct vkd3d_shader_compile_arguments arguments;
    struct vkd3d_shader_interface_info interface_info;
    struct vkd3d_shader_code input = { bytecode->pShaderBytecode, bytecode->BytecodeLength }, output;
    struct d3d12_pipeline_state state;
    struct d3d12_device device;
    struct stage_io result;
    int rc;

    memset(&device, 0, sizeof(device));
    memset(&state, 0, sizeof(state));
    memset(&arguments, 0, sizeof(arguments));
    memset(&interface_info, 0, sizeof(interface_info));
    memset(&output, 0, sizeof(output));
    device.d3d12_caps.options1.WaveLaneCountMin = 32;
    device.d3d12_caps.options1.WaveLaneCountMax = 64;
    state.graphics.stage_flags = stages;
    if (!standalone)
        d3d12_pipeline_state_init_compile_arguments(&state, &device, stage, &arguments);
    else
    {
        arguments.target = VKD3D_SHADER_TARGET_SPIRV_VULKAN_1_0;
        arguments.nv_shader_extn_uav_slot = UINT32_MAX;
        arguments.last_pre_rasterization = true;
    }
    interface_info.stage = stage;
    interface_info.patch_location_offset = 16;
    rc = vkd3d_shader_compile_dxbc(&input, &output, NULL, 0, &interface_info, &arguments);
    if (rc)
        fprintf(stderr, "Stage %#x compilation failed: %d.\n", stage, rc);
    assert(rc == 0);
    result = read_interface(&output);
    vkd3d_shader_free_shader_code(&output);
    return result;
}

static void check_builtins(const struct io_variable variables[2])
{
    assert(variables[0].semantic == 1 && variables[1].semantic == 2);
    assert(variables[0].builtin == SpvBuiltInLayer);
    assert(variables[1].builtin == SpvBuiltInViewportIndex);
    assert(variables[0].location == UINT32_MAX && variables[1].location == UINT32_MAX);
}

static void check_link(const struct stage_io *producer, const struct stage_io *consumer)
{
    unsigned int i;
    for (i = 0; i < 2; i++)
    {
        assert(producer->output[i].semantic == i + 1 && consumer->input[i].semantic == i + 1);
        assert(producer->output[i].builtin == UINT32_MAX && consumer->input[i].builtin == UINT32_MAX);
        assert(producer->output[i].location != UINT32_MAX);
        assert(producer->output[i].location == consumer->input[i].location);
        assert(producer->output[i].component == consumer->input[i].component);
    }
}

static void test_dxbc_output(void)
{
    const struct vkd3d_shader_code input = { vs_no_attachments_dxbc.pShaderBytecode,
            vs_no_attachments_dxbc.BytecodeLength };
    struct vkd3d_shader_interface_info interface_info = {0};
    struct vkd3d_shader_compile_arguments arguments = {0};
    struct vkd3d_shader_code output[2] = {{0}};
    unsigned int i;
    int rc;

    interface_info.stage = VK_SHADER_STAGE_VERTEX_BIT;
    arguments.target = VKD3D_SHADER_TARGET_SPIRV_VULKAN_1_0;
    arguments.nv_shader_extn_uav_slot = UINT32_MAX;
    for (i = 0; i < 2; i++)
    {
        arguments.last_pre_rasterization = i == 0;
        rc = vkd3d_shader_compile_dxbc(&input, &output[i], NULL, 0, &interface_info, &arguments);
        assert(rc == 0);
    }
    assert(output[0].size == output[1].size);
    assert(!memcmp(output[0].code, output[1].code, output[0].size));
    for (i = 0; i < 2; i++)
        vkd3d_shader_free_shader_code(&output[i]);
}

static void test_stage_selection(void)
{
    static const VkShaderStageFlags pipelines[] =
    {
        VK_SHADER_STAGE_VERTEX_BIT | VK_SHADER_STAGE_FRAGMENT_BIT,
        VK_SHADER_STAGE_VERTEX_BIT | VK_SHADER_STAGE_GEOMETRY_BIT | VK_SHADER_STAGE_FRAGMENT_BIT,
        VK_SHADER_STAGE_VERTEX_BIT | VK_SHADER_STAGE_TESSELLATION_CONTROL_BIT |
                VK_SHADER_STAGE_TESSELLATION_EVALUATION_BIT | VK_SHADER_STAGE_FRAGMENT_BIT,
        VK_SHADER_STAGE_VERTEX_BIT | VK_SHADER_STAGE_TESSELLATION_CONTROL_BIT |
                VK_SHADER_STAGE_TESSELLATION_EVALUATION_BIT | VK_SHADER_STAGE_GEOMETRY_BIT | VK_SHADER_STAGE_FRAGMENT_BIT,
        VK_SHADER_STAGE_TASK_BIT_EXT | VK_SHADER_STAGE_MESH_BIT_EXT | VK_SHADER_STAGE_FRAGMENT_BIT,
        VK_SHADER_STAGE_MESH_BIT_EXT | VK_SHADER_STAGE_FRAGMENT_BIT,
        VK_SHADER_STAGE_COMPUTE_BIT,
        VK_SHADER_STAGE_VERTEX_BIT,
    };
    static const VkShaderStageFlagBits last[] =
    {
        VK_SHADER_STAGE_VERTEX_BIT, VK_SHADER_STAGE_GEOMETRY_BIT,
        VK_SHADER_STAGE_TESSELLATION_EVALUATION_BIT, VK_SHADER_STAGE_GEOMETRY_BIT,
        VK_SHADER_STAGE_MESH_BIT_EXT, VK_SHADER_STAGE_MESH_BIT_EXT, VK_SHADER_STAGE_COMPUTE_BIT,
        VK_SHADER_STAGE_VERTEX_BIT,
    };
    struct d3d12_pipeline_state state = {0};
    struct d3d12_device device = {0};
    struct vkd3d_shader_compile_arguments args;
    unsigned int i, view, stage;
    bool expected;

    for (i = 0; i < sizeof(pipelines) / sizeof(*pipelines); i++)
    for (view = 0; view < 2; view++)
    for (stage = 1; stage <= VK_SHADER_STAGE_MESH_BIT_EXT; stage <<= 1)
    {
        if (!(pipelines[i] & stage))
            continue;
        state.graphics.stage_flags = pipelines[i];
        state.graphics.multiview.view_mask = view;
        d3d12_pipeline_state_init_compile_arguments(&state, &device, stage, &args);
        expected = stage == last[i] && stage != VK_SHADER_STAGE_COMPUTE_BIT;
        if (stage != VK_SHADER_STAGE_FRAGMENT_BIT)
            assert(args.last_pre_rasterization == expected);
        assert(args.multiview.enable == (view && stage != VK_SHADER_STAGE_COMPUTE_BIT));
    }
}

int main(void)
{
    const VkShaderStageFlags vs_ps = VK_SHADER_STAGE_VERTEX_BIT | VK_SHADER_STAGE_FRAGMENT_BIT;
    const VkShaderStageFlags vs_gs_ps = vs_ps | VK_SHADER_STAGE_GEOMETRY_BIT;
    const VkShaderStageFlags tess = vs_ps | VK_SHADER_STAGE_TESSELLATION_CONTROL_BIT | VK_SHADER_STAGE_TESSELLATION_EVALUATION_BIT;
    struct stage_io vs, gs, hs, ds, ps;

    test_stage_selection();
    test_dxbc_output();
    vs = compile_stage(&layer_viewport_vs_dxil, VK_SHADER_STAGE_VERTEX_BIT, vs_ps, false);
    ps = compile_stage(&layer_viewport_ps_dxil, VK_SHADER_STAGE_FRAGMENT_BIT, vs_ps, false);
    check_builtins(vs.output);
    check_builtins(ps.input);
    vs = compile_stage(&layer_viewport_vs_dxil, VK_SHADER_STAGE_VERTEX_BIT, vs_gs_ps, true);
    check_builtins(vs.output);

    vs = compile_stage(&layer_viewport_vs_dxil, VK_SHADER_STAGE_VERTEX_BIT, vs_gs_ps, false);
    gs = compile_stage(&layer_viewport_gs_dxil, VK_SHADER_STAGE_GEOMETRY_BIT, vs_gs_ps, false);
    check_link(&vs, &gs);
    check_builtins(gs.output);

    vs = compile_stage(&layer_viewport_vs_dxil, VK_SHADER_STAGE_VERTEX_BIT, tess, false);
    hs = compile_stage(&layer_viewport_hs_dxil, VK_SHADER_STAGE_TESSELLATION_CONTROL_BIT, tess, false);
    ds = compile_stage(&layer_viewport_ds_dxil, VK_SHADER_STAGE_TESSELLATION_EVALUATION_BIT, tess, false);
    check_link(&vs, &hs);
    check_link(&hs, &ds);
    check_builtins(ds.output);

    ds = compile_stage(&layer_viewport_ds_dxil, VK_SHADER_STAGE_TESSELLATION_EVALUATION_BIT,
            tess | VK_SHADER_STAGE_GEOMETRY_BIT, false);
    check_link(&ds, &gs);
    puts("Shader stage selection, 9 DXIL interfaces and unchanged DXBC output passed.");
    return 0;
}
